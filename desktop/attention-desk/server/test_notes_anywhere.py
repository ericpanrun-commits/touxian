"""Isolated transport contracts. These files are never real learner evidence."""
import asyncio,base64,hashlib,json
from uuid import uuid4
import httpx
import pytest
from server.collector.submissions import SubmissionService
from server.notes_anywhere.service import AnywhereNotes,NotesSyncError
from server.notes_anywhere.settings import NotesSettings,BASE,ENDPOINT
from server.personal_store import PersonalError
from server.test_collector import rig
from server.test_documents import text_pdf

INSTALL='11111111-1111-4111-8111-111111111111'
TOKEN='esync_'+'s'*43


def settings(root):
    def protect(value,*,decrypt=False):
        return base64.b64decode(value).decode() if decrypt else base64.b64encode(value.encode()).decode()
    value=NotesSettings(root/'notes-settings.json',protector=protect)
    value.save(INSTALL,TOKEN)
    assert TOKEN not in value.path.read_text()
    return value


def remote(data,*,corrupt=False,ack_fails=False,download_url=None):
    digest=hashlib.sha256(data).hexdigest();sid=str(uuid4())
    row={'id':sid,'sha256':digest,'bytes':len(data),'filename':'无穷小的比较.pdf','shared_at':'2026-10-28T10:00:00+08:00',
         'received_at':'2026-10-28T02:01:00Z',
         'download_url':download_url or BASE+'/storage/v1/object/sign/eric-notes-transit/'+INSTALL+'/'+digest+'.pdf?token=fixture'}
    state={'pending':True,'acks':0,'downloads':0,'ack_fails':ack_fails,'before_ack':lambda:None}
    def handler(request):
        if request.url.path.endswith('/pending'):
            assert request.headers.get('X-Eric-Sync-Key')==TOKEN
            return httpx.Response(200,json={'submissions':[row] if state['pending'] else []})
        if request.url.path.endswith('/ack'):
            state['before_ack']();state['acks']+=1
            body=json.loads(request.content)
            assert body['sha256']==digest and body['bytes']==len(data) and body['id']==sid
            if state['ack_fails']:return httpx.Response(503,json={'acked':False})
            state['pending']=False
            return httpx.Response(200,json={'acked':True})
        assert request.url.host==BASE.removeprefix('https://')
        assert request.headers.get('X-Eric-Sync-Key') is None
        state['downloads']+=1
        return httpx.Response(200,content=(b'x'+data[1:]) if corrupt else data)
    return row,state,httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_cloud_note_commits_original_inbox_and_share_time_before_ack(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            data=text_pdf(tmp_path/'fixture.pdf',['Synthetic math notes, never learning evidence.']).read_bytes()
            row,state,client=remote(data)
            monkeypatch.setattr(r.service,'_schedule',lambda raw:None)
            def before_ack():
                with r.store.connection() as c:
                    saved=dict(c.execute("SELECT * FROM raw_item WHERE kind='inbox_lecture_note'").fetchone())
                    attachment=dict(c.execute('SELECT * FROM attachment WHERE raw_item_id=?',(saved['id'],)).fetchone())
                    receipt=c.execute('SELECT * FROM inbox_receipt WHERE request_id=?',(row['id'],)).fetchone()
                    snap=c.execute('SELECT source_time FROM raw_snapshot WHERE id=?',(saved['inbox_original_snapshot_id'],)).fetchone()
                    contexts=c.execute("SELECT payload_json FROM learning_artifact WHERE kind='note_transport_context'").fetchall()
                    assert len(contexts)==1
                    source=json.loads(contexts[0][0])
                    assert source['shared_at']==row['received_at'] and source['pending_created_at']==row['shared_at']
                    assert source['creation_origin']=='pending_pdf_proxy'
                path=r.docs.files.path(attachment)
                assert path.read_bytes()==data and receipt
                assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
                assert snap[0]==row['shared_at']
            state['before_ack']=before_ack
            service=AnywhereNotes(r.store,r.service,settings(tmp_path),client=client)
            try:
                await service.sync_once()
                assert state['acks']==1 and state['downloads']==1 and not state['pending']
                assert TOKEN not in json.dumps(service.public())
                with r.store.connection() as c:assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==0
            finally:await service.close();await client.aclose()
    asyncio.run(run())


def test_lost_ack_restarts_from_durable_receipt_without_redownload_or_new_intake(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            data=text_pdf(tmp_path/'fixture.pdf',['Synthetic retry case.']).read_bytes()
            row,state,client=remote(data,ack_fails=True)
            monkeypatch.setattr(r.service,'_schedule',lambda raw:None)
            service=AnywhereNotes(r.store,r.service,settings(tmp_path),client=client)
            await service.sync_once();assert state['pending'] and state['acks']==1
            await service.close()
            reopened=SubmissionService(r.store,r.docs,r.extraction)
            monkeypatch.setattr(reopened,'_schedule',lambda raw:None)
            state['ack_fails']=False
            second=AnywhereNotes(r.store,reopened,settings(tmp_path),client=client)
            try:
                await second.sync_once()
                assert state['acks']==2 and state['downloads']==1 and not state['pending']
                with r.store.connection() as c:
                    assert c.execute('SELECT count(*) FROM inbox_receipt WHERE request_id=?',(row['id'],)).fetchone()[0]==1
                    assert c.execute("SELECT count(*) FROM raw_item WHERE kind='inbox_lecture_note'").fetchone()[0]==1
            finally:await second.close();await reopened.close();await client.aclose()
    asyncio.run(run())


def test_corrupt_download_never_saves_or_acknowledges_and_cloud_pending_is_retained(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            data=text_pdf(tmp_path/'fixture.pdf',['Synthetic corruption case.']).read_bytes()
            row,state,client=remote(data,corrupt=True)
            service=AnywhereNotes(r.store,r.service,settings(tmp_path),client=client)
            try:
                await service.sync_once()
                assert state['pending'] and state['acks']==0
                assert service.public()['last_error']=='download_hash_mismatch'
                with r.store.connection() as c:assert c.execute('SELECT count(*) FROM inbox_receipt').fetchone()[0]==0
                assert not list(r.docs.files.work.glob('*.anywhere-note'))
            finally:await service.close();await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('url',['http://127.0.0.1/private','https://attacker.example/file','https://mantuehuzptactymjpqg.supabase.co/storage/v1/object/sign/other-bucket/file?token=x'])
def test_download_target_cannot_redirect_sync_credentials_or_fetch_other_storage(tmp_path,url):
    async def run():
        async with rig(tmp_path) as r:
            data=text_pdf(tmp_path/'fixture.pdf',['Synthetic unsafe target.']).read_bytes()
            row,state,client=remote(data,download_url=url)
            service=AnywhereNotes(r.store,r.service,settings(tmp_path),client=client)
            try:
                await service.sync_once()
                assert state['acks']==0 and state['downloads']==0 and state['pending']
                assert service.public()['last_error']=='invalid_cloud_metadata'
            finally:await service.close();await client.aclose()
    asyncio.run(run())


def test_local_recovery_reuses_existing_note_pipeline_only(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            path=text_pdf(tmp_path/'fixture.pdf',['Synthetic local restart case.'])
            scheduled=[];monkeypatch.setattr(r.service,'_schedule',scheduled.append)
            await r.service.submit('anywhere:'+INSTALL,'file',{'submission_id':str(uuid4()),'purpose':'lecture_note','source_name':'fixture'},[('fixture.pdf','application/pdf',path)])
            scheduled.clear()
            with r.store.connection(write=True) as c:c.execute("UPDATE raw_item SET inbox_status='recovery_needed' WHERE kind='inbox_lecture_note'")
            await r.service.resume_notes()
            assert len(scheduled)==1
    asyncio.run(run())


def test_consumer_settings_pin_endpoint_and_hide_plaintext(tmp_path):
    value=settings(tmp_path)
    assert value.read()['sync_key']==TOKEN
    config=json.loads(value.path.read_text());config['endpoint']='https://attacker.example'
    value.path.write_text(json.dumps(config))
    with pytest.raises(PersonalError):value.read()


def test_running_consumer_rebuilds_failed_transport_then_receives_without_repairing_device(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            data=text_pdf(tmp_path/'fixture.pdf',['Network changed; transport recovery fixture.']).read_bytes()
            row,state,healthy=remote(data)
            failures=[]
            async def broken(request):
                failures.append(request.url.path)
                raise httpx.ConnectError('Never expose transport details or credentials.',request=request)
            stale=httpx.AsyncClient(transport=httpx.MockTransport(broken))
            monkeypatch.setattr(r.service,'_schedule',lambda raw:None)
            connection=settings(tmp_path)
            before=connection.path.read_bytes()
            service=AnywhereNotes(r.store,r.service,connection,client=stale,poll_seconds=.01)
            # Simulate the owned production connection without opening real sockets.
            service.owns_client=True
            replacements=[]
            def fresh():replacements.append(True);return healthy
            monkeypatch.setattr(service,'_new_client',fresh)
            try:
                await service.start()
                async def received():
                    while state['pending']:await asyncio.sleep(.01)
                await asyncio.wait_for(received(),3)
                assert len(failures)==1 and len(replacements)==1 and stale.is_closed
                assert state['downloads']==1 and state['acks']==1
                assert connection.path.read_bytes()==before
                with r.store.connection() as c:
                    assert c.execute('SELECT count(*) FROM inbox_receipt WHERE request_id=?',(row['id'],)).fetchone()[0]==1
                    assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==0
            finally:await service.close();await stale.aclose();await healthy.aclose()
    asyncio.run(run())


def test_reconnect_does_not_replace_or_close_an_injected_client(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200))) as client:
                service=AnywhereNotes(r.store,r.service,settings(tmp_path),client=client)
                await service._reconnect()
                await service.close()
                assert service.client is client and not client.is_closed
    asyncio.run(run())


@pytest.mark.parametrize('failed_stage',['download','ack'])
def test_network_change_mid_receive_retains_cloud_pending_and_reuses_committed_receipt(tmp_path,monkeypatch,failed_stage):
    async def run():
        async with rig(tmp_path) as r:
            data=text_pdf(tmp_path/'fixture.pdf',['Interrupted receive; never a real classroom note.']).read_bytes()
            row,state,healthy=remote(data)
            failed=[]
            async def broken(request):
                target=request.url.path
                if (failed_stage=='ack' and target.endswith('/ack') or
                        failed_stage=='download' and '/storage/v1/object/sign/' in target):
                    failed.append(True)
                    raise httpx.ConnectError('Changed network',request=request)
                return await healthy._transport.handle_async_request(request)
            stale=httpx.AsyncClient(transport=httpx.MockTransport(broken))
            monkeypatch.setattr(r.service,'_schedule',lambda raw:None)
            service=AnywhereNotes(r.store,r.service,settings(tmp_path),client=stale)
            service.owns_client=True
            monkeypatch.setattr(service,'_new_client',lambda:healthy)
            try:
                await service.sync_once()
                assert failed==[True] and stale.is_closed and state['pending']
                with r.store.connection() as c:
                    assert c.execute('SELECT count(*) FROM inbox_receipt').fetchone()[0]==(1 if failed_stage=='ack' else 0)
                await service.sync_once()
                assert not state['pending'] and state['acks']==1 and state['downloads']==1
                with r.store.connection() as c:
                    assert c.execute('SELECT count(*) FROM inbox_receipt').fetchone()[0]==1
                    assert c.execute("SELECT count(*) FROM raw_item WHERE kind='inbox_lecture_note'").fetchone()[0]==1
            finally:await service.close();await stale.aclose();await healthy.aclose()
    asyncio.run(run())


def test_cloud_controls_remain_loopback_only_and_do_not_expose_consumer_secret():
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.collector.control_api import router
    calls=[]
    class Service:
        setup=None
        def active_setup(self):return self.setup
        def public(self):return {'configured':True,'enabled':True,'message':'Ready'}
        async def device_setup(self,origin):
            calls.append(origin);self.setup={'id':'fixture-setup','svg':b'<svg xmlns="http://www.w3.org/2000/svg"/>','device_id':str(uuid4())}
        async def devices(self):return {'devices':[]}
        async def revoke(self,id):calls.append(id);return {'revoked':True}
    service=Service();app=FastAPI();app.include_router(router)
    app.state.notes_anywhere=service
    app.state.collector=SimpleNamespace(status=lambda:{'public_base_url':'http://192.168.40.2:8473','anywhere_setup_id':service.setup['id'] if service.setup else None})
    with TestClient(app) as client:
        path='/api/personal/collector/anywhere'
        assert client.get(path,headers={'Origin':'http://192.168.40.2:8473'}).status_code==403
        assert client.post(path+'/setup',headers={'Host':'attacker.example'}).status_code==403
        assert not calls
        status=client.get(path);assert status.status_code==200 and TOKEN not in status.text
        assert client.post(path+'/setup').status_code==200 and len(calls)==1
        assert client.get(path+'/qr?setup_id=wrong').status_code==409
        assert client.get(path+'/qr?setup_id=fixture-setup').status_code==200
        assert client.post(path+'/revoke',json={'device_id':'bad'}).status_code==422


def test_short_lived_setup_survives_browser_handoff_but_rejects_wrong_expired_and_replaced_codes(tmp_path,monkeypatch):
    from urllib.parse import urlsplit,parse_qs
    import qrcode
    from server.collector.app import create_app
    from server.collector.security import CollectorError
    from server.test_collector import ORIGIN
    async def run():
        urls=[];calls=[]
        original=qrcode.QRCode.add_data
        def capture(qr,data,*args,**kwargs):urls.append(data);return original(qr,data,*args,**kwargs)
        monkeypatch.setattr(qrcode.QRCode,'add_data',capture)
        async def cloud(request):
            assert request.headers['X-Eric-Sync-Key']==TOKEN
            calls.append(request.url.path)
            if request.url.path.endswith('/devices/revoke'):return httpx.Response(200,json={'revoked':True})
            return httpx.Response(201,json={'upload_key':'enote_'+chr(97+len(calls))*43,'device_id':str(uuid4())})
        async with rig(tmp_path) as r,httpx.AsyncClient(transport=httpx.MockTransport(cloud)) as remote_client:
            service=AnywhereNotes(r.store,r.service,settings(tmp_path),client=remote_client)
            try:
                with pytest.raises(CollectorError):await service.device_setup('http://localhost:8473')
                assert calls==[]
                await service.device_setup(ORIGIN)
                url=urlsplit(urls[-1]);nonce=parse_qs(url.query)['setup'][0]
                assert url.netloc=='192.168.40.2:8473' and not url.fragment
                assert 'enote_' not in urls[-1] and 'key=' not in urls[-1]
                assert service.setup['upload_key'] not in service.settings.path.read_text()
                app=create_app(r.service,r.authority,ORIGIN,tmp_path/'app',notes_setup=service.setup_connection)
                for _ in range(3):
                    # Separate cookie jars model Code Scanner -> Safari and a fresh reload.
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as phone:
                        response=await phone.get('/notes-connection',params={'setup':nonce})
                        assert response.status_code==200 and response.json()['upload_key']==service.setup['upload_key']
                        assert response.json()['endpoint']==ENDPOINT+'/upload'
                        assert response.headers['cache-control']=='no-store' and response.headers['referrer-policy']=='no-referrer'
                        assert 'set-cookie' not in response.headers
                        assert (await phone.get('/notes-connection',params={'setup':'x'*43})).status_code==403
                        assert (await phone.get('/notes-connection',params={'setup':nonce},headers={'Origin':'https://attacker.example'})).status_code==403
                        assert (await phone.get('/notes-connection',params={'setup':nonce},headers={'Host':'127.0.0.1:8473'})).status_code==403
                        assert (await phone.post('/notes-connection',json={'setup':nonce})).status_code==405
                await service.device_setup(ORIGIN)
                with pytest.raises(CollectorError):service.setup_connection(nonce)
                nonce=parse_qs(urlsplit(urls[-1]).query)['setup'][0]
                service.setup['expires']=0
                with pytest.raises(CollectorError):service.setup_connection(nonce)
                assert service.setup is None
                await service.device_setup(ORIGIN)
                nonce=parse_qs(urlsplit(urls[-1]).query)['setup'][0]
                await service.revoke(service.setup['device_id'])
                with pytest.raises(CollectorError):service.setup_connection(nonce)
                with r.store.connection() as c:
                    assert c.execute('SELECT count(*) FROM inbox_receipt').fetchone()[0]==0
                    assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==0
            finally:await service.close()
    asyncio.run(run())


def test_reviewed_shortcut_import_and_upload_use_the_same_device_key_and_raw_pdf_contract():
    import plistlib,re
    from pathlib import Path
    root=Path(__file__).resolve().parent.parent/'supabase/functions/eric-notes-ingest'
    raw=(root/'shortcut-source.plist').read_bytes();workflow=plistlib.loads(raw)
    source_digest=re.search(r'SHORTCUT_SOURCE_SHA256 = "([a-f0-9]{64})"',(root/'shortcut.ts').read_text())[1]
    assert hashlib.sha256(raw).hexdigest()==source_digest
    actions=workflow['WFWorkflowActions'];params=lambda i:actions[i]['WFWorkflowActionParameters']
    question=workflow['WFWorkflowImportQuestions'][0]
    assert len(workflow['WFWorkflowImportQuestions'])==1 and question['ActionIndex']==1
    assert question['ParameterKey']=='WFTextActionText' and question['DefaultValue']==''
    assert params(1)['WFTextActionText']=='' and params(0)['WFTextActionText']==ENDPOINT+'/upload'
    assert not re.search(rb'(?:enote_|esync_)[A-Za-z0-9_-]{43}',raw)
    by_id=lambda name:[a['WFWorkflowActionParameters'] for a in actions if a['WFWorkflowActionIdentifier']=='is.workflow.actions.'+name]
    request=by_id('downloadurl')[0]
    assert request['WFHTTPMethod']=='POST' and request['WFHTTPBodyType']=='File'
    assert request['WFRequestVariable']['Value']=={'Type':'Variable','VariableName':'note'}
    headers=request['WFHTTPHeaders']['Value']['WFDictionaryFieldValueItems']
    assert all(item['WFItemType']==0 for item in headers)
    by_name={item['WFKey']['Value']['string']:item['WFValue'] for item in headers}
    assert set(by_name)=={'Content-Type','X-Eric-Notes-Key','X-Eric-Filename','X-Eric-Shared-At'}
    assert by_name['Content-Type']['Value']['string']=='application/pdf'
    key_ref=next(iter(by_name['X-Eric-Notes-Key']['Value']['attachmentsByRange'].values()))
    assert key_ref['OutputUUID']==params(question['ActionIndex'])['UUID']
    formatted=by_id('format.date')[0]
    assert formatted['WFISO8601IncludeTime'] is True
    assert formatted['WFDate']['WFSerializationType']=='WFTextTokenAttachment'
    save=by_id('documentpicker.save')[0]
    assert save['WFAskWhereToSave'] is False and save['WFFolder']['Value']['OutputUUID']==params(2)['UUID']
    assert params(2)['WFFolder']['fileLocation']['WFFileLocationType']=='LocalStorage'
    assert any(p.get('WFDictionaryKey')=='saved' for p in by_id('getvalueforkey'))
    assert any(p.get('WFDictionaryKey')=='receipt_id' for p in by_id('getvalueforkey'))
    assert not by_id('file.delete')
