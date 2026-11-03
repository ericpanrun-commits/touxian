"""Outbound-only Windows processor, backed by the existing originals and domain owners."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote
from uuid import UUID,uuid4

import httpx
from pydantic import ValidationError

from ..cloud_store import ProcessLock
from ..personal_store import PersonalError
from ..notes_anywhere.settings import BASE
from ..collector.safe_logs import record
from .commands import CommandExecutor
from .projection import CloudProjection

ENDPOINT=BASE+'/functions/v1/eric-app-api'
PART_BYTES=4_000_000


class SyncError(Exception):
    def __init__(self,code,status=503):self.code,self.status=code,status;super().__init__(code)


def retryable(error):
    """Only known temporary failures may replay a durable business operation."""
    if isinstance(error,httpx.TransportError):return True
    if isinstance(error,SyncError):
        if error.code in {'schema_mismatch','data_access_denied','record_too_large','source_file_changed','source_part_changed','unsupported_file_protocol','connection_not_configured'}:return False
        return error.status==429 or 500<=error.status<600
    if isinstance(error,sqlite3.OperationalError):
        return getattr(error,'sqlite_errorcode',None) in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED)
    return False


def batches(items,max_bytes=4_000_000,max_items=100):
    batch=[];size=2
    for item in items:
        length=len(json.dumps(item,ensure_ascii=False,separators=(',',':')).encode())+1
        if length>max_bytes:raise SyncError('record_too_large')
        if batch and (len(batch)>=max_items or size+length>max_bytes):yield batch;batch=[];size=2
        batch.append(item);size+=length
    if batch:yield batch


def capture_states(records):
    learning={}
    for record in records:
        if record['kind']=='learning_capture':
            value=record['payload'];old=learning.get(value['raw_item_id'])
            if not old or value.get('created_at','')>=old.get('created_at',''):learning[value['raw_item_id']]=value
    result=[]
    for record in records:
        if record['kind']!='inbox_item':continue
        row=record['payload'];status=row['processing_status'];capture=learning.get(row['id']) or {};context=capture.get('context') or {};automation=context.get('note_automation') or {}
        state='completed' if status=='completed' else 'needs_confirmation' if row['needs_manual'] else 'failed' if status in ('failed','recovery_needed') else 'processing'
        error=row.get('error')
        if context.get('note_share'):
            if automation.get('status')=='completed':state='completed';error=None
            elif capture.get('status')=='needs_context' or automation.get('status') in ('needs_context','needs_review','needs_source','needs_setup','needs_vision_permission'):
                state='needs_confirmation'
                if automation.get('message'):error={'code':'note_review_required' if automation.get('review_job_id') or automation.get('review_id') else 'note_context_required','message':automation['message']}
            elif automation.get('status') in ('local_ready','reviewing','processing','generating'):state='processing'
        result.append({'raw_item_id':row['id'],'local_version':row['version'],'state':state,'course_id':(row.get('course') or {}).get('id') or context.get('course_id'),
            'lecture_id':context.get('lecture_id') or automation.get('lecture_id'),'learning_capture_id':capture.get('id'),'error':error})
    return result


def preview_targets(records):
    """Only classroom evidence needs a separate rendered copy; books load by range."""
    targets=set()
    for record in records:
        if record['kind']!='facade' or not record['id'].startswith('/school/lectures/') or not record['id'].endswith('/context'):continue
        for source in record['payload'].get('sources',[]):
            doc,page=source.get('document_id'),source.get('page')
            if doc and isinstance(page,int) and not isinstance(page,bool) and page>0:targets.add((doc,page))
    return targets


class CloudSync:
    def __init__(self,personal,intake,school,extraction,settings,learning=None,*,cache=None,client=None,poll_seconds=15):
        self.personal,self.intake,self.school,self.settings=personal,intake,school,settings
        self.learning=learning
        self.cache=Path(cache or personal.path.with_name('cloud-sync'));self.cache.mkdir(parents=True,exist_ok=True)
        self.projection=CloudProjection(personal,self.cache)
        self.executor=CommandExecutor(personal,school,extraction,intake,learning)
        self.client=client or self._new_client();self.owns_client=client is None
        self.poll_seconds=poll_seconds;self.lock=asyncio.Lock();self.process_lock=ProcessLock(self.cache)
        self.task=None;self.heartbeat=None;self.epoch=str(uuid4());self.generation=0;self.workspace=None;self.config=None
        self.hashes={};self.resources=set();self.resource_signatures={};self.pending_commands={};self.preview_targets=set();self.state={'connected':False,'phase':'waiting','last_error':None}

    @staticmethod
    def _new_client():return httpx.AsyncClient(timeout=httpx.Timeout(90,connect=10),follow_redirects=False)

    async def request(self,method,path,body=None,content=None):
        if not self.config:raise SyncError('connection_not_configured')
        headers={'X-Eric-Worker-Key':self.config['worker_key']}
        if content is not None:headers['Content-Type']='application/octet-stream'
        response=await self.client.request(method,ENDPOINT+path,headers=headers,
            **({'json':body} if content is None else {'content':content}))
        if response.status_code not in (200,201,202):
            try:value=response.json()
            except ValueError:value={}
            detail=value.get('detail') if isinstance(value,dict) else None
            candidate=detail.get('code') if isinstance(detail,dict) else None
            code=candidate if isinstance(candidate,str) else 'cloud_unavailable'
            raise SyncError(code,response.status_code)
        try:return response.json()
        except ValueError:raise SyncError('invalid_cloud_response') from None

    async def start(self):
        if self.task and not self.task.done():return
        self.process_lock.acquire()
        self.task=asyncio.create_task(self._run())

    async def close(self):
        for task in (self.task,self.heartbeat):
            if task:task.cancel()
        await asyncio.gather(*(x for x in (self.task,self.heartbeat) if x),return_exceptions=True)
        self.task=self.heartbeat=None
        if self.owns_client:await self.client.aclose()
        self.process_lock.close()

    async def _heartbeats(self):
        while True:
            await asyncio.sleep(20)
            try:await self.request('POST','/worker/heartbeat',{'epoch':self.epoch,'phase':self.state['phase'] if self.state['phase'] in ('preparing','processing') else 'connected'})
            except asyncio.CancelledError:raise
            except Exception as error:
                self.state['last_error']='heartbeat_unavailable'
                record(self.personal,'cloud_heartbeat',error,job_id=self.epoch,entity='workspace')

    async def _run(self):
        while True:
            try:await self.sync_once()
            except asyncio.CancelledError:raise
            except Exception as error:
                self.state.update(connected=False,last_error=error.code if isinstance(error,SyncError) else 'connection_unavailable')
                record(self.personal,'cloud_sync',error,job_id=self.epoch,entity='workspace')
                if isinstance(error,httpx.TransportError) and self.owns_client:
                    old=self.client;self.client=self._new_client();await old.aclose()
            await asyncio.sleep(self.poll_seconds)

    async def sync_once(self):
        if self.lock.locked():return
        async with self.lock:
            # Local handoffs continue even while the Internet request below
            # fails. Their durable marker also covers commit-before-shutdown.
            if self.learning is not None:self.learning.resume_pending_note_links()
            config=self.settings.read()
            if not config or not config.get('enabled'):return
            self.config=config
            if self.workspace is None:
                hello=await self.request('POST','/worker/hello',{'epoch':self.epoch})
                workspace=str(UUID(hello['workspace_id']))
                if not PART_BYTES<=hello.get('part_bytes',0)<=16_000_000:raise SyncError('unsupported_file_protocol')
                if not self.heartbeat or self.heartbeat.done():self.heartbeat=asyncio.create_task(self._heartbeats())
                await self.resource_catalogue()
                if hello.get('record_catalogue'):await self.record_catalogue()
                self.workspace=workspace
            self.state.update(connected=True,phase='preparing',last_error=None)
            await self.publish()
            self.state['phase']='processing'
            await self.mirror_tasks()
            await self.intake.resume_notes()
            for kind in ('captures','commands'):
                claimed=await self.request('POST','/worker/claim',{'kind':kind,'limit':3})
                for item in claimed['items']:
                    await self.process(kind,item)
            # New material and edits always take priority over optional book previews.
            await self.publish_previews(limit=4)
            self.state['phase']='connected'
            await self.request('POST','/worker/heartbeat',{'epoch':self.epoch,'phase':'connected','published':True})

    async def upload_resource(self,item):
        identity=item['id']
        signature=(item['sha256'],item['bytes'])
        if identity in self.resources:
            if self.resource_signatures.get(identity,signature)!=signature:raise SyncError('source_file_changed')
            return
        path=Path(item['path']);before=path.stat()
        manifest={key:item[key] for key in ('id','filename','mime','sha256','bytes')}
        manifest['part_count']=math.ceil(item['bytes']/PART_BYTES)
        resource=await self.request('POST','/worker/resources',manifest)
        if resource.get('complete'):self.resources.add(identity);self.resource_signatures[identity]=signature;return
        received={p['part_index']:p for p in resource.get('parts',[])}
        total=0;whole=hashlib.sha256()
        with path.open('rb') as stream:
            for index in range(manifest['part_count']):
                data=await asyncio.to_thread(stream.read,PART_BYTES)
                if not data:raise SyncError('source_file_changed')
                total+=len(data);whole.update(data)
                part=received.get(index);digest=hashlib.sha256(data).hexdigest()
                if part and (part['sha256']!=digest or part['bytes']!=len(data)):raise SyncError('source_part_changed')
                if not part:await self.request('PUT','/worker/files/'+quote(identity,safe='')+'/parts/'+str(index),content=data)
        after=path.stat()
        if total!=item['bytes'] or whole.hexdigest()!=item['sha256'] or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise SyncError('source_file_changed')
        await self.request('POST','/worker/files/'+quote(identity,safe='')+'/finish',{})
        self.resources.add(identity)
        self.resource_signatures[identity]=signature

    async def resource_catalogue(self):
        offset=0
        while True:
            page=await self.request('GET','/worker/resources?offset='+str(offset))
            for row in page['items']:
                self.resources.add(row['id']);self.resource_signatures[row['id']]=(row['sha256'],row['bytes'])
            if len(page['items'])<page['limit']:break
            offset+=len(page['items'])

    async def mirror_tasks(self):
        from .progress import apply_task_progress
        page=await self.request('GET','/worker/task-progress')
        for row in page['items']:
            try:
                await asyncio.to_thread(apply_task_progress,self.personal,row)
                state,message='applied',''
            except PersonalError as error:
                state,message='needs_confirmation',str(error)
            await self.publish()
            await self.request('POST','/worker/task-progress/ack',{'id':row['id'],'revision':row['revision'],'state':state,'message':message,
                'epoch':self.epoch,'generation':self.generation})

    async def record_catalogue(self):
        offset=0
        while True:
            page=await self.request('GET','/worker/records?offset='+str(offset))
            for row in page['items']:self.hashes[(row['kind'],row['id'])]=row['source_hash']
            if len(page['items'])<page['limit']:break
            offset+=len(page['items'])

    def render_page(self,doc,index):
        target=self.cache/(doc['sha256']+'-'+str(index)+'.webp')
        if not target.exists():
            pending=target.with_suffix('.pending.png')
            self.school.documents.worker(doc['path'],'school-render:'+str(index),pending,45)
            from PIL import Image
            encoded=target.with_suffix('.pending.webp')
            with Image.open(pending) as image:image.save(encoded,format='WEBP',lossless=True,method=4,exact=True)
            with Image.open(encoded) as image:image.verify()
            encoded.replace(target);pending.unlink(missing_ok=True)
        filename=''.join(c if c not in '/\\\x00' else '-' for c in doc['title'][:160])+'-'+str(index)+'.webp'
        return {'id':'page:'+doc['id']+':'+str(index)+':image','filename':filename,'mime':'image/webp',
                'bytes':target.stat().st_size,'sha256':self.projection._hash(target),'path':target}

    async def publish(self):
        resources,documents=await asyncio.to_thread(self.projection.resources)
        for resource in resources:await self.upload_resource(resource)
        records=await asyncio.to_thread(self.projection.records)
        self.preview_targets=preview_targets(records)
        self.generation+=1
        changed=[r for r in records if self.hashes.get((r['kind'],r['id']))!=r['source_hash']]
        for batch in batches(changed):
            await self.request('POST','/worker/publish',{'epoch':self.epoch,'generation':self.generation,'records':batch})
            self.hashes.update({(r['kind'],r['id']):r['source_hash'] for r in batch})
        await self.publish_capture_states(records)

    async def publish_previews(self,limit=4):
        _,documents=await asyncio.to_thread(self.projection.resources)
        count=0
        for doc in documents:
            for index in sorted(page for identity,page in self.preview_targets if identity==doc['id'] and page<=(doc.get('page_count') or 0)):
                if 'page:'+doc['id']+':'+str(index)+':image' in self.resources:continue
                await self.upload_resource(await asyncio.to_thread(self.render_page,doc,index))
                count+=1
                if count>=limit:return

    async def publish_capture_states(self,records):
        owner='anywhere:'+self.config['notes_installation_id']
        with self.personal.connection() as c:
            receipts=[{'id':r['request_id'],'raw_item_id':r['raw_item_id'],'local_receipt_id':r['id']} for r in c.execute('SELECT * FROM inbox_receipt WHERE owner=?',(owner,))]
        for batch in batches(receipts):await self.request('POST','/worker/receipts',{'items':batch})
        states=capture_states(records)
        for batch in batches(states):await self.request('POST','/worker/capture-results',{'epoch':self.epoch,'generation':self.generation,'items':batch})

    async def download_capture(self,capture):
        files=[]
        for file in capture['files']:
            target=self.cache/(capture['id']+'-'+file['sha256']+'.download')
            digest=hashlib.sha256();size=0
            async with self.client.stream('GET',ENDPOINT+'/worker/files/'+quote(file['resource_id'],safe='')+'/content',headers={'X-Eric-Worker-Key':self.config['worker_key']}) as response:
                if response.status_code!=200:raise SyncError('download_unavailable')
                with target.open('wb') as stream:
                    async for data in response.aiter_bytes(65536):
                        size+=len(data)
                        if size>file['bytes']:raise SyncError('download_size_mismatch')
                        stream.write(data);digest.update(data)
                    stream.flush();os.fsync(stream.fileno())
            if size!=file['bytes'] or digest.hexdigest()!=file['sha256']:raise SyncError('download_hash_mismatch')
            files.append((file['filename'],file['mime'],target))
        return files

    async def process(self,kind,item):
        lease=asyncio.create_task(self.renew(kind,item))
        work=asyncio.create_task(self._process(kind,item))
        try:
            done,_=await asyncio.wait((lease,work),return_when=asyncio.FIRST_COMPLETED)
            if lease in done:
                # Lease failure must stop this owner before any further publish/ack.
                await lease
                raise SyncError('stale_processing_lease',409)
            await work
        finally:
            for task in (lease,work):task.cancel()
            await asyncio.gather(lease,work,return_exceptions=True)

    async def _process(self,kind,item):
        paths=[]
        try:
            if kind=='captures':
                paths=[self.cache/(item['id']+'-'+f['sha256']+'.download') for f in item['files']]
                files=await self.download_capture(item)
                choices=[entry['course_id'] for entry in item.get('course_candidates',[]) if entry.get('basis')=='owner_selected']
                if len(choices)>1:raise PersonalError('这次上传选择了多个课程，请核对一次关联。','ambiguous_course',409)
                saved=await self.intake.bridge(self.intake.submit('cloud:'+self.workspace,'file' if files else 'text',{
                    'submission_id':item['id'],'text':item['text_content'],'source_name':item['title'],'source_time':item['shared_at'],'purpose':item['purpose'],'confirm_large':True},files,
                    **({'confirmed_course_id':choices[0]} if choices else {})))
                if not saved.get('saved'):raise SyncError('local_receive_unconfirmed')
                result={'raw_item_id':saved['submission_id'],'local_receipt_id':saved['id']};state='processing'
            else:
                result=await self.executor.execute(item);state='completed'
            # Do not report a completed command before its actual data are readable in the cloud.
            await self.publish()
        except asyncio.CancelledError:raise
        except (PersonalError,ValidationError) as error:
            result={'code':getattr(error,'code','invalid_input'),'message':str(error) if isinstance(error,PersonalError) else '内容需要核对，原有资料保留。'}
            state='failed' if kind=='captures' else 'needs_confirmation'
            record(self.personal,'cloud_process',error,job_id=item['id'],entity=kind,status=state)
        except Exception as error:
            temporary=retryable(error)
            result={'code':'processing_interrupted' if temporary else 'processing_failed',
                'message':'处理暂时中断，原件保留，会自动继续。' if temporary else '处理失败，原件保留；请核对错误后再重试。'}
            state=('received' if kind=='captures' else 'pending') if temporary else 'failed'
            record(self.personal,'cloud_process',error,job_id=item['id'],entity=kind,status=state)
        finally:
            for path in paths:path.unlink(missing_ok=True)
        from .projection import public_value
        await self.request('POST','/worker/finish',{'kind':kind,'id':item['id'],'token':item['lease_token'],'state':state,'result':public_value(result),'records':[],
            'epoch':self.epoch,'generation':self.generation})

    async def renew(self,kind,item):
        while True:
            await asyncio.sleep(60)
            result=await self.request('POST','/worker/renew',{'kind':kind,'id':item['id'],'token':item['lease_token']})
            if result.get('renewed') is not True:raise SyncError('stale_processing_lease',409)
