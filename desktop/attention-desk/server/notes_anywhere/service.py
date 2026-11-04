"""Outbound-only sync; originals commit through the existing SubmissionService."""
import asyncio
import hashlib
import os
import re
import secrets
import time
from datetime import datetime,timezone
from urllib.parse import urlsplit
from uuid import UUID,uuid4

import httpx

from ..documents.policy import filename,validate_file
from ..personal_store import PersonalError
from .settings import BASE,ENDPOINT,MAX_BYTES
from ..recovery_controls import cloud_uploads_held


class NotesSyncError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)


class AnywhereNotes:
    def __init__(self,personal,intake,settings,*,client=None,poll_seconds=30):
        self.personal,self.intake,self.settings=personal,intake,settings
        self.client=client or self._new_client()
        self.owns_client=client is None
        self.poll_seconds=poll_seconds;self.lock=asyncio.Lock();self.task=None
        self.state={'configured':False,'enabled':False,'message':'笔记云端连接尚未配置。','last_received_at':None,'received':0,'last_error':None}
        self.setup=None

    @staticmethod
    def _new_client():
        return httpx.AsyncClient(timeout=httpx.Timeout(90,connect=10),follow_redirects=False)

    async def _reconnect(self):
        # A long-lived client's proxy configuration and connection pool were
        # captured before a network/sleep/proxy change. Re-read them on retry.
        # Caller-owned clients are never closed or replaced by this service.
        if not self.owns_client:return
        previous=self.client
        self.client=self._new_client()
        await previous.aclose()

    def public(self):
        try:config=self.settings.read()
        except PersonalError:return {**self.state,'configured':True,'enabled':False,'message':'笔记连接暂时无法读取，云端待接收文件保留。'}
        return {**self.state,'configured':bool(config),'enabled':bool(config and config.get('enabled'))}

    async def start(self):
        if self.task and not self.task.done():return
        self.task=asyncio.create_task(self._run())

    async def _run(self):
        while True:
            try:await self.sync_once()
            except asyncio.CancelledError:raise
            except Exception as error:
                if isinstance(error,httpx.TransportError):await self._reconnect()
                code=error.code if isinstance(error,NotesSyncError) else 'connection_unavailable'
                self.state.update(last_error=code,message='云端笔记暂未取回，连接恢复后会自动重试。')
            await asyncio.sleep(self.poll_seconds)

    async def close(self):
        if self.task:
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True);self.task=None
        if self.owns_client:await self.client.aclose()
        self.setup=None

    async def _request(self,config,method,path,body=None):
        if cloud_uploads_held(self.personal.path) and (method.upper() not in {'GET','HEAD'} or body is not None):
            raise NotesSyncError('cloud_uploads_held')
        response=await self.client.request(method,ENDPOINT+path,headers={'X-Eric-Sync-Key':config['sync_key']},json=body)
        if response.status_code not in {200,201,202}:raise NotesSyncError('cloud_unavailable')
        try:return response.json()
        except ValueError:raise NotesSyncError('invalid_cloud_response') from None

    @staticmethod
    def _metadata(row,config):
        try:
            if str(UUID(row['id']))!=row['id'] or not re.fullmatch('[a-f0-9]{64}',row['sha256']):raise ValueError()
            if type(row['bytes']) is not int or not 6<=row['bytes']<=MAX_BYTES:raise ValueError()
            url=urlsplit(row['download_url'])
            expected='/storage/v1/object/sign/eric-notes-transit/'+config['installation_id']+'/'+row['sha256']+'.pdf'
            if url.scheme!='https' or url.netloc!=urlsplit(BASE).netloc or url.username or url.password or url.fragment or url.path!=expected or not url.query:raise ValueError()
            name=filename(row.get('filename') or 'Apple Notes 课堂笔记.pdf')
            if not name.lower().endswith('.pdf'):name+='.pdf'
            shared=row.get('shared_at')
            for value in (shared,row.get('received_at')):
                if value:
                    stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
                    if stamp.tzinfo is None:raise ValueError()
            return {**row,'filename':name,'shared_at':shared}
        except (ValueError,KeyError,TypeError,AttributeError):raise NotesSyncError('invalid_cloud_metadata') from None

    def _receipt(self,owner,row):
        with self.personal.connection() as c:
            receipt=c.execute('SELECT * FROM inbox_receipt WHERE owner=? AND request_id=?',(owner,row['id'])).fetchone()
            if not receipt:return None
            attachments=[dict(r) for r in c.execute('SELECT * FROM attachment WHERE raw_item_id=? AND sha256=? AND size=?',(receipt['raw_item_id'],row['sha256'],row['bytes']))]
        for att in attachments:
            try:
                path=self.intake.documents.files.path(att)
                if path.stat().st_size!=row['bytes']:continue
                with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
                if actual==row['sha256']:return dict(receipt)
            except (OSError,PersonalError):continue
        return None

    async def _download(self,row,target):
        digest=hashlib.sha256();size=0
        async with self.client.stream('GET',row['download_url']) as response:
            if response.status_code!=200:raise NotesSyncError('download_unavailable')
            with target.open('xb') as stream:
                async for chunk in response.aiter_bytes(65536):
                    size+=len(chunk)
                    if size>row['bytes'] or size>MAX_BYTES:raise NotesSyncError('download_size_mismatch')
                    stream.write(chunk);digest.update(chunk)
                stream.flush();os.fsync(stream.fileno())
        if size!=row['bytes'] or digest.hexdigest()!=row['sha256']:raise NotesSyncError('download_hash_mismatch')
        validate_file(target,'pdf','application/pdf')

    async def sync_once(self):
        if cloud_uploads_held(self.personal.path):
            self.state.update(uploads_held=True,last_error=None,message='本地恢复期间云端收发暂停；原件与队列保留。')
            return self.public()
        self.state['uploads_held']=False
        config=self.settings.read()
        self.state.update(configured=bool(config),enabled=bool(config and config.get('enabled')))
        if not config or not config.get('enabled'):return self.public()
        if self.lock.locked():return self.public()
        async with self.lock:
            await self.intake.resume_notes()
            result=await self._request(config,'GET','/pending')
            rows=result.get('submissions')
            if not isinstance(rows,list) or len(rows)>5:raise NotesSyncError('invalid_cloud_response')
            self.state['last_error']=None
            owner='anywhere:'+config['installation_id']
            for value in rows:
                try:
                    row=self._metadata(value,config)
                    await asyncio.to_thread(self._source_context,row)
                    receipt=await asyncio.to_thread(self._receipt,owner,row)
                    if not receipt:
                        target=self.intake.documents.files.work/(str(uuid4())+'.anywhere-note')
                        try:
                            await self._download(row,target)
                            saved=await self.intake.bridge(self.intake.submit(owner,'file',{
                                'submission_id':row['id'],'purpose':'lecture_note','source_name':row['filename'],
                                'source_time':row['shared_at'],'confirm_large':True},[(row['filename'],'application/pdf',target)]))
                            if not saved.get('saved'):raise NotesSyncError('local_save_unconfirmed')
                            receipt=await asyncio.to_thread(self._receipt,owner,row)
                            if not receipt:raise NotesSyncError('local_original_unverified')
                        finally:target.unlink(missing_ok=True)
                    ack=await self._request(config,'POST','/ack',{
                        'id':row['id'],'sha256':row['sha256'],'bytes':row['bytes'],'local_receipt_id':receipt['id']})
                    if ack.get('acked') is not True:raise NotesSyncError('cloud_ack_unconfirmed')
                    self.state.update(last_received_at=datetime.now(timezone.utc).isoformat(),received=self.state['received']+1,last_error=None)
                except asyncio.CancelledError:raise
                except Exception as error:
                    if isinstance(error,httpx.TransportError):await self._reconnect()
                    self.state.update(last_error=error.code if isinstance(error,NotesSyncError) else 'local_receive_failed',message='有一份云端笔记尚未确认保存，原件保留，稍后自动重试。')
                    continue
            if not self.state.get('last_error'):self.state['message']='已连接；笔记会自动取回并沿用现有整理流程。'
            return self.public()

    def _source_context(self,row):
        # Reuse the existing local provenance ledger, not a second note database.
        # Never mistake a PDF cache's creation timestamp for an original Notes date.
        import json
        from ..personal_store import now
        payload={'submission_id':row['id'],'shared_at':row.get('received_at'),
                 'pending_created_at':row.get('shared_at'),'creation_origin':'pending_pdf_proxy'}
        fingerprint=hashlib.sha256(('note-transport:'+row['id']).encode()).hexdigest()
        with self.personal.connection(write=True) as c:
            c.execute('''INSERT OR IGNORE INTO learning_artifact
                (id,kind,input_hash,status,payload_json,created_at,completed_at)
                VALUES (?,'note_transport_context',?,'completed',?,?,?)''',
                ('note-source-'+fingerprint[:24],fingerprint,json.dumps(payload),now(),now()))

    async def device_setup(self,origin):
        from ..collector.endpoint import public_base_url
        origin=public_base_url(origin)
        config=self.settings.read()
        if not config:raise PersonalError('笔记云端连接尚未配置。','notes_not_configured',409)
        result=await self._request(config,'POST','/devices',{})
        if not re.fullmatch(r'enote_[A-Za-z0-9_-]{43}',result.get('upload_key','')):raise NotesSyncError('invalid_device_response')
        import hashlib,io,qrcode,qrcode.image.svg
        from urllib.parse import urlencode
        nonce=secrets.token_urlsafe(32)
        query=urlencode({'setup':nonce})
        qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=6,border=4)
        qr.add_data(origin+'/notes-setup?'+query);qr.make(fit=True)
        out=io.BytesIO();qr.make_image(image_factory=qrcode.image.svg.SvgPathFillImage).save(out)
        setup_id=hashlib.sha256(result['upload_key'].encode()).hexdigest()[:16]
        self.setup={'id':setup_id,'svg':out.getvalue(),'device_id':result['device_id'],
                    'nonce':nonce,'upload_key':result['upload_key'],'expires':time.monotonic()+900}
        return {'setup_id':setup_id,'device_id':result['device_id']}

    def active_setup(self):
        if self.setup and time.monotonic()>=self.setup['expires']:self.setup=None
        return self.setup

    def setup_connection(self,nonce):
        from ..collector.security import CollectorError
        value=self.active_setup()
        if not value or not isinstance(nonce,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',nonce) or not secrets.compare_digest(value['nonce'],nonce):
            raise CollectorError('notes_setup_expired','配置码已过期或无效，请在电脑重新点击“配置笔记分享”。',403)
        return {'upload_key':value['upload_key'],'endpoint':ENDPOINT+'/upload'}

    async def devices(self):
        config=self.settings.read()
        if not config:return {'devices':[]}
        return await self._request(config,'GET','/devices')

    async def revoke(self,device_id):
        config=self.settings.read()
        if not config:raise PersonalError('笔记云端连接尚未配置。','notes_not_configured',409)
        result=await self._request(config,'POST','/devices/revoke',{'id':str(UUID(device_id))})
        if self.setup and self.setup['device_id']==device_id:self.setup=None
        return result
