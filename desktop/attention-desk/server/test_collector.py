"""Phase 5: isolated LAN contracts; no live network/key/firewall mutations."""
import asyncio
import copy
import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from docx import Document
from server.collector.app import create_app, COOKIE, CollectorBoundary, mobile_assets
from server.collector.controller import CollectorController
from server.collector.control_api import router as control_router
from server.collector.network import lan_address, select_interface, discover
from server.collector.public_url import canonical_public_url, public_ip, resolve_public, PublicURLFetcher, PinnedConnection, bounded_resolve
from server.collector.security import CollectorError, PairingAuthority, RateLimiter, PAIRING_SECONDS, SESSION_SECONDS
from server.collector.submissions import SubmissionService
from server.documents.service import DocumentService
from server.extraction_service import ExtractionService
from server.extraction_errors import ExtractionFailure
from server.personal_store import PersonalStore
from server.review_store import ReviewStore
from server.test_extraction_review import FixtureProvider, TestSettings, candidate, confirmation
from server.test_documents import text_pdf, screenshot, review_payload

ORIGIN = 'http://192.168.40.2:8473'
TEXT = '通知：请于2026年10月12日前提交申请。'

def assets(root):
    (root/'dist/mobile').mkdir(parents=True)
    (root/'dist/assets').mkdir()
    (root/'dist/.vite').mkdir()
    (root/'dist/mobile/index.html').write_text('<html>手机收集</html>', encoding='utf-8')
    (root/'dist/assets/mobile.js').write_text('/* mobile only */')
    (root/'dist/assets/desktop.js').write_text('/* desktop secret settings */')
    (root/'dist/.vite/manifest.json').write_text(json.dumps({'mobile/index.html': {'file':'assets/mobile.js'}, 'index.html': {'file':'assets/desktop.js'}}))
    return root

@asynccontextmanager
async def rig(tmp_path, *, failure=None, fetcher=None):
    store=PersonalStore(tmp_path/'personal.sqlite3'); store.initialize()
    docs=DocumentService(store)
    provider=FixtureProvider(output=candidate(TEXT), failure=failure)
    extraction=ExtractionService(store, TestSettings(), provider)
    service=SubmissionService(store, docs, extraction, fetcher=fetcher)
    authority=PairingAuthority()
    application=create_app(service, authority, ORIGIN, assets(tmp_path/'app'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=ORIGIN) as client:
        token=authority.regenerate()
        paired=await client.post('/api/pair/confirm',json={'token':token})
        assert paired.status_code==200
        client.headers['X-Collector-CSRF']=paired.json()['csrf']
        yield SimpleNamespace(store=store,docs=docs,extraction=extraction,provider=provider,service=service,authority=authority,client=client,app=application,token=token)
    await service.close(); await extraction.close(); await asyncio.to_thread(docs.close)

async def done(r, receipt):
    deadline=time.monotonic()+25
    while time.monotonic()<deadline:
        result=await r.service.status(r.service.receipts[receipt['id']].owner,receipt['id'])
        if result['state'] in {'needs_review','failed'}: return result
        await asyncio.sleep(.05)
    raise AssertionError('processing did not finish')

def payload(**extra): return dict(submission_id=str(uuid4()),text=TEXT,**extra)
def count(store,table):
    with store.connection() as c: return c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

def test_pairing_hash_expiry_rotate_stop_and_single_use():
    tick=[100.]; authority=PairingAuthority(clock=lambda:datetime(2026, 10, 8, tzinfo=timezone.utc) + timedelta(seconds=tick[0]))
    token=authority.regenerate(); assert len(token)>=43 and token not in repr(vars(authority))
    with pytest.raises(CollectorError): authority.pair('wrong'*9)
    assert authority.validate(token)['pairing_valid']
    assert authority.validate(token)['expires_in']==PAIRING_SECONDS
    cookie,session=authority.pair(token)
    assert cookie not in repr(vars(authority)) and authority.session(cookie)==session
    with pytest.raises(CollectorError): authority.pair(token)
    assert not authority.public()['pairing_available']
    authority.regenerate()
    assert authority.session(cookie)==session
    token=authority.regenerate();tick[0]+=PAIRING_SECONDS
    with pytest.raises(CollectorError): authority.pair(token)
    token=authority.regenerate();cookie,_=authority.pair(token);tick[0]+=SESSION_SECONDS
    with pytest.raises(CollectorError): authority.session(cookie)
    token=authority.regenerate();authority.revoke()
    with pytest.raises(CollectorError): authority.pair(token)


def test_pairing_confirmation_is_atomic_between_independent_browsers():
    from concurrent.futures import ThreadPoolExecutor
    authority=PairingAuthority();token=authority.regenerate()
    gate=threading.Barrier(8)
    def confirm():
        gate.wait(timeout=5)
        try:
            authority.pair(token)
            return 200
        except CollectorError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:confirm(),range(8)))
    assert results.count(200)==1 and results.count(401)==7
    assert authority.public()['paired_devices']==1
    assert not authority.public()['pairing_available']


def test_code_scanner_get_and_validation_leave_invitation_for_safari(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            token=r.authority.regenerate()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=r.app),base_url=ORIGIN) as scanner, \
                       httpx.AsyncClient(transport=httpx.ASGITransport(app=r.app),base_url=ORIGIN) as safari:
                before=r.authority.public()['paired_devices']
                for browser in (scanner,scanner,safari):
                    page=await browser.get('/collect',params={'token':token})
                    assert page.status_code==200 and 'set-cookie' not in page.headers
                    validated=await browser.post('/api/pair/validate',json={'token':token})
                    assert validated.status_code==200 and validated.json()['pairing_valid']
                    assert 'set-cookie' not in validated.headers
                    assert (await browser.get('/api/session')).status_code==401
                assert r.authority.public()['paired_devices']==before
                assert r.authority.public()['pairing_available']
                confirmed=await safari.post('/api/pair/confirm',json={'token':token})
                assert confirmed.status_code==200
                assert (await safari.get('/api/session')).status_code==200
                assert (await scanner.get('/api/session')).status_code==401
                assert (await scanner.post('/api/pair/confirm',json={'token':token})).status_code==401
                # A persistent cookie survives a new browser client, while the
                # invitation remains consumed. No token is needed to reopen.
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=r.app),base_url=ORIGIN,cookies=safari.cookies) as reopened:
                    assert (await reopened.get('/collect')).status_code==200
                    assert (await reopened.get('/api/session')).status_code==200
                    r.authority.regenerate()
                    assert (await reopened.get('/api/session')).status_code==200
    asyncio.run(run())


@pytest.mark.parametrize('method,path,status',[
    ('GET','/api/pair/confirm',405),('GET','/api/pair/validate',405),('POST','/api/pair',401),
])
def test_get_and_legacy_auto_pair_cannot_consume_invitation(tmp_path,method,path,status):
    async def run():
        async with rig(tmp_path) as r:
            r.client.cookies.clear();token=r.authority.regenerate()
            response=await r.client.request(method,path,json={'token':token})
            assert response.status_code==status
            assert r.authority.validate(token)['pairing_valid']
            assert (await r.client.post('/api/pair/confirm',json={'token':token})).status_code==200
    asyncio.run(run())


@pytest.mark.parametrize('path',['/api/pair/validate','/api/pair/confirm'])
@pytest.mark.parametrize('headers,status',[
    ({'Origin':'https://evil.example'},403),({'Host':'evil.example'},403),({'Content-Type':'text/plain'},415),
])
def test_pairing_confirmation_and_validation_keep_request_boundaries(tmp_path,path,headers,status):
    async def run():
        async with rig(tmp_path) as r:
            token=r.authority.regenerate();r.client.cookies.clear()
            response=await r.client.post(path,json={'token':token},headers=headers)
            assert response.status_code==status and r.authority.validate(token)['pairing_valid']
            assert count(r.store,'raw_item')==0
    asyncio.run(run())


def test_unused_invitation_rotation_and_five_minute_validation_expiry():
    tick=[0.];authority=PairingAuthority(clock=lambda:datetime(2026, 10, 8, tzinfo=timezone.utc) + timedelta(seconds=tick[0]))
    old=authority.regenerate();current=authority.regenerate()
    with pytest.raises(CollectorError):authority.validate(old)
    tick[0]=PAIRING_SECONDS-1
    assert authority.validate(current)['pairing_valid']
    tick[0]=PAIRING_SECONDS
    with pytest.raises(CollectorError):authority.validate(current)
    with pytest.raises(CollectorError):authority.pair(current)
    assert not authority.sessions


def test_device_capacity_does_not_consume_invitation_and_expiry_frees_capacity():
    from server.collector.security import MAX_DEVICES
    tick=[0.];authority=PairingAuthority(clock=lambda:datetime(2026, 10, 8, tzinfo=timezone.utc) + timedelta(seconds=tick[0]))
    for _ in range(MAX_DEVICES):authority.pair(authority.regenerate())
    token=authority.regenerate()
    with pytest.raises(CollectorError) as error:authority.pair(token)
    assert error.value.code=='device_limit' and authority.validate(token)['pairing_valid']
    tick[0]=SESSION_SECONDS
    cookie,_=authority.pair(authority.regenerate())
    assert authority.session(cookie) and authority.public()['paired_devices']==1

@pytest.mark.parametrize('path',['/api/text','/api/url','/api/file','/api/session','/api/submissions/other'])
def test_without_session_rejected_before_reading_body(tmp_path,path):
    async def run():
        async with rig(tmp_path) as r:
            r.client.cookies.clear()
            result=await (r.client.get(path) if path in ['/api/session','/api/submissions/other'] else r.client.post(path,content=b'not even valid',headers={'Content-Type':'application/json'}))
            assert result.status_code==401 and count(r.store,'raw_item')==0
    asyncio.run(run())

@pytest.mark.parametrize('headers,status',[({'Origin':'https://evil.example'},403),({'Host':'evil.example'},403),({'X-Collector-CSRF':'wrong'},403),({'Content-Type':'text/plain'},415)])
def test_request_boundaries(tmp_path,headers,status):
    async def run():
        async with rig(tmp_path) as r:
            result=await r.client.post('/api/text',json=payload(),headers=headers)
            assert result.status_code==status and count(r.store,'raw_item')==0
    asyncio.run(run())

@pytest.mark.parametrize('path',['/api/personal/ai-settings','/api/personal/events','/api/personal/collector/qr','/api/personal/collector/revoke-devices','/api/attachments','/docs','/openapi.json','/assets/desktop.js','/personal.sqlite3','/server/main.py','/assets/../../server/main.py'])
def test_desktop_api_and_arbitrary_files_never_exposed(tmp_path,path):
    async def run():
        async with rig(tmp_path) as r:
            result=await r.client.get(path)
            assert result.status_code==404
            assert not any(value in result.text for value in ['traceback','test-key-for','sqlite3','C:\\Users','desktop secret'])
            assert result.headers['cache-control']=='no-store'
    asyncio.run(run())

def test_static_whitelist_and_cookie_security(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            assert (await r.client.get('/collect')).status_code==200
            assert (await r.client.get('/assets/mobile.js')).status_code==200
            assert (await r.client.delete('/api/text')).status_code==405
            assert (await r.client.get('/collect')).headers['referrer-policy']=='no-referrer'
            r.authority.regenerate();token=r.authority.regenerate()
            result=await r.client.post('/api/pair/confirm',json={'token':token})
            cookie=result.headers['set-cookie'].lower()
            assert 'httponly' in cookie and 'samesite=strict' in cookie
            assert f'max-age={SESSION_SECONDS}' in cookie and 'expires=' in cookie
            assert 'token' not in result.json() and 'key' not in result.json()
    asyncio.run(run())

def test_text_shared_extraction_human_confirmation_restart_and_dedup(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=payload(); result=(await r.client.post('/api/text',json=p)).json()
            assert result['saved']; assert (await done(r,result))['state']=='needs_review'
            assert count(r.store,'raw_item')==1 and count(r.store,'event')==0
            assert (await r.client.post('/api/text',json=p)).json()['duplicate']
            p['submission_id']=str(uuid4()); assert (await r.client.post('/api/text',json=p)).json()['id']==result['id']
            raw=ReviewStore(r.store).list(source_id='mobile')['items'][0]
            assert raw['source_name']=='来自手机'
            bundle=ReviewStore(r.store).get(raw['raw_snapshot_id'])
            event=ReviewStore(r.store).confirm(raw['raw_snapshot_id'],confirmation(bundle,candidate(TEXT)))['events'][0]
            assert len(event['tasks'])==len(event['deadlines'])==1 and event['evidence']
            reopened=PersonalStore(r.store.path);reopened.initialize()
            assert reopened.get_event(event['id'])['evidence']==event['evidence']
            p.update(submission_id=str(uuid4()),again=True)
            new=(await r.client.post('/api/text',json=p)).json();assert new['id']!=result['id']
            await done(r,new);assert count(r.store,'raw_item')==2
            p['text']='changed'; assert (await r.client.post('/api/text',json=p)).status_code==409
    asyncio.run(run())

def test_concurrent_duplicates_create_one_raw_item(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=payload()
            values=await asyncio.gather(*(r.service.submit(next(iter(r.authority.sessions)),'text',p) for _ in range(6)))
            assert len({x['id'] for x in values})==1 and count(r.store,'raw_item')==1
            await done(r,values[0]);assert r.provider.calls==1
    asyncio.run(run())

def test_lan_thread_bridge_uses_desktop_loop_and_job_survives_disconnect(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            token=r.authority.regenerate()
            def phone():
                with TestClient(r.app,base_url=ORIGIN) as phone_client:
                    result=phone_client.post('/api/pair/confirm',json={'token':token});assert result.status_code==200
                    headers={'X-Collector-CSRF':result.json()['csrf']}
                    received=phone_client.post('/api/text',json=payload(),headers=headers)
                    assert received.status_code==202
                    return received.json()
            result=await asyncio.to_thread(phone)
            assert (await done(r,result))['state']=='needs_review' and r.provider.calls==1
            assert count(r.store,'raw_item')==1 and count(r.store,'event')==0
    asyncio.run(run())

def test_private_receipts_and_rotation(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            result=(await r.client.post('/api/text',json=payload())).json(); await done(r,result)
            assert set(result)=={'id','state','phase','saved','duplicate','warning'}
            token=r.authority.regenerate()
            assert (await r.client.get('/api/session')).status_code==200
            assert (await r.client.get('/api/submissions/'+result['id'])).status_code==200
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=r.app),base_url=ORIGIN) as other:
                assert (await other.post('/api/pair/confirm',json={'token':token})).status_code==200
                assert (await other.get('/api/submissions/'+result['id'])).status_code==404
            r.authority.revoke()
            assert (await r.client.get('/api/session')).status_code==401
            assert count(r.store,'raw_item')==1
    asyncio.run(run())

@pytest.mark.parametrize('kind',['text','file'])
def test_provider_failure_retains_original(tmp_path,kind):
    async def run():
        async with rig(tmp_path,failure=ExtractionFailure('provider_unavailable','测试错误，不应传到手机')) as r:
            if kind=='text': response=await r.client.post('/api/text',json=payload())
            else: response=await r.client.post('/api/file',data={'submission_id':str(uuid4())},files=[('files',('notice.txt',TEXT.encode(),'text/plain'))])
            result=await done(r,response.json());assert result['state']=='failed' and result['saved']
            assert '测试错误' not in json.dumps(result,ensure_ascii=False)
            assert count(r.store,'raw_item')==1 and count(r.store,'event')==0
            if kind=='file':
                with r.store.connection() as c: attachment=dict(c.execute('SELECT * FROM attachment').fetchone())
                assert Path(attachment['local_path']).read_bytes()==TEXT.encode()
    asyncio.run(run())

def test_duplicate_alias_map_and_receipt_lifetime_are_bounded(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            owner=next(iter(r.authority.sessions))
            result=await r.service.submit(owner,'text',payload());await done(r,result)
            r.service.requests={(owner,str(uuid4())):result['id'] for _ in range(2048)}
            with pytest.raises(CollectorError) as error:await r.service.submit(owner,'text',payload())
            assert error.value.status==429 and len(r.service.requests)==2048 and count(r.store,'raw_item')==1
            stamp=r.service.receipts[result['id']].created
            r.service.clock=lambda:stamp+3601
            with pytest.raises(CollectorError):await r.service.status(owner,result['id'])
            assert not r.service.requests and not r.service.receipts and count(r.store,'raw_item')==1
    asyncio.run(run())

@pytest.mark.parametrize('kind',['text','image'])
def test_mobile_formal_confirmation_evidence_and_service_restart(tmp_path,kind):
    async def run():
        async with rig(tmp_path) as r:
            if kind=='text': response=await r.client.post('/api/text',json=payload())
            else:
                path=screenshot(tmp_path/'school.png',TEXT)
                response=await r.client.post('/api/file',data={'submission_id':str(uuid4())},files=[('files',(path.name,path.read_bytes(),'image/png'))])
            await done(r,response.json())
            sid=ReviewStore(r.store).list(source_id='mobile')['items'][0]['raw_snapshot_id']
            raw=ReviewStore(r.store).get(sid)
            if kind=='text': check=confirmation(raw,candidate(TEXT))
            else:
                block=next(b for b in raw['snapshot']['blocks'] if b['source_type']=='image_ocr')
                check=review_payload(raw,block,block['text'],value='2026-10-12')
                check=check.model_copy(update={'extraction_id':raw['extraction_id']})
            event=ReviewStore(r.store).confirm(sid,check)['events'][0]
            assert event['source']['id']=='mobile' and len(event['deadlines'])==len(event['tasks'])==1
            assert event['evidence'] and not ReviewStore(r.store).list(source_id='mobile')['items']
            if kind=='image':assert event['evidence'][0]['bounding_box'] and event['evidence'][0]['attachment_id']
            await r.service.close();await r.extraction.close();await asyncio.to_thread(r.docs.close)
            reopened=PersonalStore(r.store.path);reopened.initialize()
            assert reopened.get_event(event['id'])==event
    asyncio.run(run())

@pytest.mark.parametrize('ext',['txt','pdf','docx','png'])
def test_supported_files_use_existing_parsers_blocks_and_latest_mobile_queue(tmp_path,ext):
    async def run():
        async with rig(tmp_path) as r:
            path=tmp_path/('notice.'+ext)
            if ext=='txt': path.write_text(TEXT,encoding='utf-8')
            elif ext=='pdf': text_pdf(path,['Submit application before 2026-10-18.'])
            elif ext=='docx': doc=Document();doc.add_paragraph(TEXT);doc.save(path)
            else: screenshot(path,TEXT)
            response=await r.client.post('/api/file',data={'submission_id':str(uuid4()),'text':'补充通知正文'},files=[('files',(path.name,path.read_bytes(),'application/octet-stream'))])
            assert response.status_code==202,response.text
            result=await done(r,response.json());assert result['saved']
            queue=ReviewStore(r.store).list(source_id='mobile'); assert len(queue['items'])==1
            raw=ReviewStore(r.store).get(queue['items'][0]['raw_snapshot_id'])
            assert len(raw['snapshot']['blocks'])>=2
            assert count(r.store,'raw_item')==1 and count(r.store,'raw_snapshot')>=2
            attachment=raw['snapshot']['attachments'][0]
            assert r.docs.files.path(r.docs.files.get(attachment['id'])).read_bytes()==path.read_bytes()
            assert attachment['parse_status']=='parsed' and count(r.store,'event')==0
            if ext=='png': assert any(b['source_type']=='image_ocr' and b['bounding_box'] for b in raw['snapshot']['blocks'])
    asyncio.run(run())

@pytest.mark.parametrize('name,mime,body',[('a.exe','application/octet-stream',b'MZ00'),('a.zip','application/zip',b'PK00'),('a.heic','image/heic',b'foo'),('a.jpg','image/jpeg',b'not an image'),('a.txt','application/pdf',b'hello'),('a.pdf','application/pdf',b'not pdf'),('a.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document',b'PK00')])
def test_bad_file_type_or_content_never_saved(tmp_path,name,mime,body):
    async def run():
        async with rig(tmp_path) as r:
            response=await r.client.post('/api/file',data={'submission_id':str(uuid4())},files=[('files',(name,body,mime))])
            assert response.status_code in {400,422} and count(r.store,'raw_item')==0
            assert not list(r.docs.files.work.glob('*.mobile-upload'))
    asyncio.run(run())

def test_corrupt_pdf_after_safe_signature_retains_original(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            body=b'%PDF-1.7\nmalformed'
            response=await r.client.post('/api/file',data={'submission_id':str(uuid4())},files=[('files',('broken.pdf',body,'application/pdf'))])
            assert response.status_code==202,response.text
            result=await done(r,response.json());assert result['state']=='failed' and result['saved']
            with r.store.connection() as c: attachment=dict(c.execute('SELECT * FROM attachment').fetchone())
            assert Path(attachment['local_path']).read_bytes()==body and count(r.store,'event')==0
    asyncio.run(run())

def test_limits_declared_chunked_soft_hard_count_and_rate(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            result=await r.client.post('/api/file',content=b'x',headers={'Content-Type':'multipart/form-data; boundary=xx','Content-Length':'60000001'})
            assert result.status_code==413
            async def chunks(): yield b'x'*450001
            result=await r.client.post('/api/text',content=chunks(),headers={'Content-Type':'application/json'})
            assert result.status_code==413
            result=await r.client.post('/api/file',data={'submission_id':str(uuid4())},files=[('files',(f'n{i}.txt',b'hello','text/plain')) for i in range(11)])
            assert result.status_code==400
            monkeypatch.setattr('server.collector.app.SOFT_BYTES',8)
            result=await r.client.post('/api/file',data={'submission_id':str(uuid4())},files=[('files',('n.txt',b'hello world','text/plain'))])
            assert result.status_code==413
            monkeypatch.setattr('server.collector.app.MAX_BYTES',4)
            result=await r.client.post('/api/file',data={'submission_id':str(uuid4()),'confirm_large':'true'},files=[('files',('n.txt',b'hello','text/plain'))])
            assert result.status_code==413 and count(r.store,'raw_item')==0
            outcomes=[(await r.client.post('/api/text',json=payload())).status_code for _ in range(12)]
            assert 429 in outcomes
    asyncio.run(run())

@pytest.mark.parametrize('url',['file:///C:/Windows','ftp://example.com/a','http://localhost/a','http://127.0.0.1','http://10.1.1.1','http://192.168.1.2','http://172.20.5.2','http://169.254.169.254','http://[::1]','http://[fc00::1]','http://[fe80::1]','http://100.64.0.1','http://224.0.0.1','http://0.0.0.0','http://2130706433','http://127.1','http://0x7f000001','http://user:pass@example.com','https://example.com:8765','http://computer.local','http://example.com\n/'])
def test_unsafe_url_shapes(url):
    with pytest.raises(CollectorError): canonical_public_url(url)

@pytest.mark.parametrize('ip',['127.0.0.1','10.0.0.1','192.168.1.1','172.20.5.2','169.254.169.254','::1','::ffff:127.0.0.1','2002:7f00:1::1','64:ff9b::7f00:1','100.64.1.1','224.0.0.1','0.0.0.0'])
def test_nonpublic_dns_addresses(ip): assert not public_ip(ip)

def address(ip='93.184.216.34'): return (socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))
def test_dns_mixed_answers_and_rebinding_pin(monkeypatch):
    with pytest.raises(CollectorError): resolve_public('safe.example',443,lambda *a,**k:[address(),address('127.0.0.1')])
    calls=[]
    class Sock:
        def settimeout(self,t): pass
        def connect(self,endpoint): calls.append(endpoint)
        def close(self): pass
    class TLS:
        def wrap_socket(self,sock,server_hostname): calls.append(server_hostname);return sock
    monkeypatch.setattr(socket,'socket',lambda *a:Sock())
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k: (_ for _ in ()).throw(AssertionError('second DNS lookup')))
    monkeypatch.setattr('server.collector.public_url.ssl.create_default_context',lambda:TLS())
    connection=PinnedConnection('safe.example',443,[address()],https=True,timeout=1);connection.connect()
    assert calls==[('93.184.216.34',443),'safe.example']

def test_dns_timeout_is_bounded_and_recovers():
    gate=threading.Event()
    def slow(*a,**k): gate.wait(1);return [address()]
    start=time.monotonic()
    try:
        with pytest.raises(CollectorError): bounded_resolve('safe.example',443,slow,.03)
        assert time.monotonic()-start<.3
    finally: gate.set()

class Response:
    def __init__(self,status=200,headers=None,body=b''):
        self.status,self.headers,self.body=status,headers or {'Content-Type':'text/html'},body
    def getheader(self,key): return self.headers.get(key)
    def read1(self,n): value,self.body=self.body[:n],self.body[n:];return value

def fetcher(responses,resolver=None):
    calls=[]
    class Connection:
        sock=None
        def __init__(self,host,*a,**kw):calls.append(host)
        def request(self,*a,**kw):calls.append(kw['headers'])
        def getresponse(self):return responses.pop(0)
        def close(self):pass
    return PublicURLFetcher(resolver=resolver or (lambda *a,**k:[address()]),connection_factory=Connection),calls

@pytest.mark.parametrize('mode',['private_literal','private_dns','login','too_large','encoding','redirect_loop','unsupported'])
def test_url_fetch_failures_bounded_safe_and_no_cookies(mode):
    if mode.startswith('private'):
        responses=[Response(302,{'Location':'http://127.0.0.1/secret' if mode=='private_literal' else 'https://private.example/x'})]
    elif mode=='login':responses=[Response(body=b'<body><input type="password"/></body>')]
    elif mode=='too_large': responses=[Response(headers={'Content-Type':'text/html','Content-Length':'2000001'})]
    elif mode=='encoding': responses=[Response(headers={'Content-Type':'text/html','Content-Encoding':'gzip'})]
    elif mode=='redirect_loop':responses=[Response(302,{'Location':'https://safe.example/again'}) for _ in range(4)]
    else:responses=[Response(headers={'Content-Type':'application/octet-stream'})]
    resolver=lambda host,*a,**k:[address('127.0.0.1' if host=='private.example' else '93.184.216.34')]
    provider,calls=fetcher(responses,resolver)
    with pytest.raises(CollectorError):provider.fetch('https://safe.example/notice')
    assert all('Cookie' not in c and 'Authorization' not in c for c in calls if isinstance(c,dict))

def test_url_success_source_provenance_and_failure_keeps_link(tmp_path):
    async def run():
        provider,_=fetcher([Response(body=('<html><title>通知</title><article><p>'+TEXT+'</p></article></html>').encode())])
        async with rig(tmp_path,fetcher=provider) as r:
            p={'submission_id':str(uuid4()),'url':'https://safe.example/notice#part'}
            response=await r.client.post('/api/url',json=p);assert response.status_code==202
            result=await done(r,response.json());assert result['state']=='needs_review'
            queue=ReviewStore(r.store).list(source_id='mobile');assert len(queue['items'])==1
            bundle=ReviewStore(r.store).get(queue['items'][0]['raw_snapshot_id'])
            assert bundle['snapshot']['web']['canonical_url']=='https://safe.example/notice'
            assert TEXT in bundle['snapshot']['original_text'] and count(r.store,'event')==0
            r.service.fetcher=SimpleNamespace(fetch=lambda *a: (_ for _ in ()).throw(CollectorError('url_unavailable','not for phone')))
            p.update(submission_id=str(uuid4()),url='https://safe.example/login')
            response=await r.client.post('/api/url',json=p);result=await done(r,response.json())
            assert result['state']=='failed' and result['saved'] and '正文无法自动获取' in result['phase']
            with r.store.connection() as c: assert c.execute('SELECT original_text FROM raw_snapshot ORDER BY created_at DESC LIMIT 1').fetchone()[0]==p['url']
    asyncio.run(run())

@pytest.mark.parametrize('address_value,expected',[('192.168.2.3',True),('10.2.3.4',True),('172.16.1.2',True),('127.0.0.1',False),('169.254.1.1',False),('8.8.8.8',False),('::1',False)])
def test_lan_address(address_value,expected):assert lan_address(address_value)==expected

def network(ip='192.168.40.2',profile='Private',id='3'):
    return {'interfaces':[dict(id=id,name='WLAN',address=ip,profile=profile)],'firewall':{'checked':True}}
def test_network_choice_private_only():
    for data,chosen in [({'interfaces':[]},None),(network(profile='Public'),None),(network(), 'nope'),({'interfaces':network()['interfaces']*2},None)]:
        with pytest.raises(CollectorError):select_interface(data,chosen)
    assert select_interface(network(),'3')['address']=='192.168.40.2'

def test_control_routes_local_only(tmp_path):
    application=FastAPI();application.include_router(control_router)
    calls=[]
    application.state.collector=SimpleNamespace(status=lambda:{'enabled':False},revoke_devices=lambda:calls.append('revoke') or {'paired_devices':0})
    with TestClient(application) as client:
        assert client.get('/api/personal/collector').status_code==200
        assert client.get('/api/personal/collector',headers={'Origin':ORIGIN}).status_code==403
        assert client.get('/api/personal/collector',headers={'Host':'evil.example'}).status_code==403
        assert client.post('/api/personal/collector/revoke-devices',headers={'Origin':ORIGIN}).status_code==403
        assert client.post('/api/personal/collector/revoke-devices',headers={'Host':'evil.example'}).status_code==403
        assert not calls
        assert client.post('/api/personal/collector/revoke-devices').status_code==200
        assert calls==['revoke']

class FakeServer:
    def __init__(self,config):self.config=config;self.started=False;self.should_exit=False
    def run(self,sockets):
        self.started=True
        while not self.should_exit:time.sleep(.01)

def test_controller_dynamic_address_no_persisted_ip_token_enabled_and_restart(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            current=network()
            class Listener:
                def setsockopt(self,*a):pass
                def bind(self,value):bindings.append(value)
                def listen(self,*a):pass
                def close(self):pass
            bindings=[]
            monkeypatch.setattr('server.collector.controller.socket',SimpleNamespace(socket=lambda *a:Listener(),AF_INET=socket.AF_INET,SOCK_STREAM=socket.SOCK_STREAM,SOL_SOCKET=socket.SOL_SOCKET,SO_EXCLUSIVEADDRUSE=getattr(socket,'SO_EXCLUSIVEADDRUSE',4)))
            controller=CollectorController(r.store,r.docs,r.extraction,tmp_path/'app',detector=lambda port:copy.deepcopy(current),server_factory=FakeServer)
            assert not controller.status()['enabled']
            first=await asyncio.to_thread(controller.enable,'3',8473)
            assert bindings[-1]==('192.168.40.2',8473) and first['enabled']
            assert not first['pairing_available']
            generated=controller.regenerate()
            assert b'<svg' in controller.qr(generated['pairing_id'])
            prefs=json.loads(controller.settings_path.read_text());assert set(prefs)=={'interface_id','port'} and '192.168.' not in str(prefs)
            assert not any(k in first for k in ['token','session','csrf'])
            await asyncio.to_thread(controller.stop)
            current['interfaces'][0]['address']='192.168.50.7'
            second=await asyncio.to_thread(controller.enable,'3',8473)
            assert bindings[-1]==('192.168.50.7',8473) and second['address']!=first['address']
            token=controller.authority.regenerate();cookie,session=controller.authority.pair(token)
            rotated=controller.regenerate()
            assert rotated['paired_devices']==1 and controller.authority.session(cookie)==session
            revoked=controller.revoke_devices()
            assert revoked['paired_devices']==0 and not revoked['pairing_available']
            with pytest.raises(CollectorError):controller.authority.session(cookie)
            await controller.close()
            reopened=CollectorController(r.store,r.docs,r.extraction,tmp_path/'app',detector=lambda port:network(),server_factory=FakeServer)
            assert reopened.port==8473 and reopened.interface_id=='3' and not reopened.status()['enabled']
            await reopened.close()
    asyncio.run(run())

def test_network_watcher_revokes_changed_interface(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            controller=CollectorController(r.store,r.docs,r.extraction,tmp_path/'app',detector=lambda port:network(ip='192.168.50.7'),server_factory=FakeServer)
            controller.bound=network()['interfaces'][0];token=controller.authority.regenerate();cookie,_=controller.authority.pair(token)
            class Trigger:
                def wait(self,seconds):return False
                def is_set(self):return False
                def set(self):pass
            controller.stop_watch=Trigger()
            await asyncio.to_thread(controller._watch_network,controller.stop_watch)
            with pytest.raises(CollectorError):controller.authority.session(cookie)
            assert not controller.status()['enabled'] and '网络已变化' in controller.message
            await controller.close()
    asyncio.run(run())

@pytest.mark.parametrize('failure',['public','reserved','occupied','startup'])
def test_collector_enable_failures_release_resources_and_leave_raw_pipeline_available(tmp_path,monkeypatch,failure):
    async def run():
        async with rig(tmp_path) as r:
            closed=[]
            class Listener:
                def setsockopt(self,*a):pass
                def bind(self,value):
                    if failure=='occupied':raise OSError('port fixture')
                def listen(self,*a):pass
                def close(self):closed.append(True)
            monkeypatch.setattr('server.collector.controller.socket',SimpleNamespace(socket=lambda *a:Listener(),AF_INET=socket.AF_INET,SOCK_STREAM=socket.SOCK_STREAM,SOL_SOCKET=socket.SOL_SOCKET,SO_EXCLUSIVEADDRUSE=getattr(socket,'SO_EXCLUSIVEADDRUSE',4)))
            def factory(config):
                if failure=='startup':raise RuntimeError('fixture internal path key must not leak')
                return FakeServer(config)
            controller=CollectorController(r.store,r.docs,r.extraction,tmp_path/'app',detector=lambda port:network(profile='Public' if failure=='public' else 'Private'),server_factory=factory)
            with pytest.raises(CollectorError) as error:await asyncio.to_thread(controller.enable,'3',8765 if failure=='reserved' else 8473)
            assert error.value.code=={'public':'private_network_required','reserved':'invalid_port','occupied':'port_unavailable','startup':'start_failed'}[failure]
            assert not controller.status()['enabled'] and not controller.authority.public()['pairing_available']
            if failure in {'occupied','startup'}:assert closed
            response=await r.client.post('/api/text',json=payload());assert response.status_code==202
            assert (await done(r,response.json()))['saved']
            await controller.close()
    asyncio.run(run())

def test_network_script_read_only_and_no_hardcoded_address():
    folder=Path(__file__).parent/'collector'
    ps=(folder/'network.ps1').read_text(encoding='utf-8')
    assert not any(x in ps for x in ['Set-Net','New-Net','Remove-Net','netsh','172.20.10.8'])
    assert 'Get-NetAdapter -Physical' in ps and 'VPN|Tailscale|Hyper-V' in ps
    for path in folder.glob('*.py'):assert '172.20.10.8' not in path.read_text(encoding='utf-8')

def test_safe_logs_never_store_exception_message_or_full_paths(tmp_path):
    from server.collector.safe_logs import record
    store=PersonalStore(tmp_path/'personal.sqlite3')
    try: raise ValueError('raw phone notice token key C:\\Users\\private')
    except Exception as error:record(store,'test',error)
    log=(tmp_path/'logs/mobile-collector.log').read_text(encoding='utf-8')
    assert 'ValueError' in log and 'raw phone notice' not in log and 'C:\\Users' not in log
