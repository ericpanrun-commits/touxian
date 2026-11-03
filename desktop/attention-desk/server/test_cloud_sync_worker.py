"""Real files and HTTP boundary failures; no network, learner database or model calls."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from server.cloud_sync.service import CloudSync,SyncError,PART_BYTES,batches,ENDPOINT,capture_states,preview_targets


def worker(tmp_path,handler):
    client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service=CloudSync(SimpleNamespace(path=tmp_path/'source.sqlite3'),None,None,None,None,cache=tmp_path/'cache',client=client)
    service.config={'worker_key':'ework_'+'s'*43}
    return service,client


def test_interrupted_upload_resumes_verified_parts_and_never_repeats_a_saved_part(tmp_path):
    async def run():
        body=b'synthetic archive part'*120;path=tmp_path/'original.txt';path.write_bytes(body)
        digest=hashlib.sha256(body).hexdigest();calls=[]
        def handler(request):
            assert request.headers['X-Eric-Worker-Key']=='ework_'+'s'*43
            assert str(request.url).startswith(ENDPOINT)
            route=request.url.path.split('/eric-app-api',1)[1];calls.append(route)
            if route=='/worker/resources':return httpx.Response(200,json={'complete':False,'parts':[{'part_index':0,'sha256':digest,'bytes':len(body)}]})
            if route.endswith('/finish'):return httpx.Response(200,json={'complete':True})
            raise AssertionError('Already saved bytes must not be uploaded again')
        service,client=worker(tmp_path,handler)
        try:
            await service.upload_resource({'id':'attachment:synthetic:original','filename':path.name,'mime':'text/plain','sha256':digest,'bytes':len(body),'path':path})
            assert len(calls)==2
            await service.upload_resource({'id':'attachment:synthetic:original','sha256':digest,'bytes':len(body)})
            assert len(calls)==2
        finally:await client.aclose()
    asyncio.run(run())


def test_catalogue_skips_verified_bytes_but_never_hides_a_changed_original(tmp_path):
    async def run():
        def handler(request):
            assert request.method=='GET' and request.url.params['offset']=='0'
            return httpx.Response(200,json={'items':[{'id':'book','sha256':'a'*64,'bytes':500}],'limit':500})
        service,client=worker(tmp_path,handler)
        try:
            await service.resource_catalogue()
            await service.upload_resource({'id':'book','sha256':'a'*64,'bytes':500})
            with pytest.raises(SyncError,match='source_file_changed'):
                await service.upload_resource({'id':'book','sha256':'b'*64,'bytes':500})
        finally:await client.aclose()
    asyncio.run(run())


def test_storage_failure_never_marks_resource_complete_and_retry_uses_same_identity(tmp_path):
    async def run():
        body=b'Original synthetic bytes';path=tmp_path/'original.txt';path.write_bytes(body);digest=hashlib.sha256(body).hexdigest()
        attempts=[];fail=[True]
        def handler(request):
            route=request.url.path.split('/eric-app-api',1)[1]
            if route=='/worker/resources':return httpx.Response(200,json={'complete':False,'parts':[]})
            if '/parts/' in route:
                attempts.append((route,request.content))
                if fail[0]:return httpx.Response(503,json={'detail':{'code':'storage_unavailable'}})
                return httpx.Response(200,json={'saved':True})
            if route.endswith('/finish'):return httpx.Response(200,json={'complete':True})
            raise AssertionError(route)
        service,client=worker(tmp_path,handler);item={'id':'attachment:synthetic:original','filename':path.name,'mime':'text/plain','sha256':digest,'bytes':len(body),'path':path}
        try:
            with pytest.raises(SyncError):await service.upload_resource(item)
            assert not service.resources
            fail[0]=False;await service.upload_resource(item)
            assert attempts[0]==attempts[1]
            assert item['id'] in service.resources
        finally:await client.aclose()
    asyncio.run(run())


def test_wrong_original_hash_prevents_cloud_finish(tmp_path):
    async def run():
        path=tmp_path/'changed.txt';path.write_bytes(b'Changed original');finished=[]
        def handler(request):
            if request.url.path.endswith('/resources'):return httpx.Response(200,json={'complete':False,'parts':[]})
            if '/parts/' in request.url.path:return httpx.Response(200,json={'saved':True})
            finished.append(str(request.url));return httpx.Response(200,json={})
        service,client=worker(tmp_path,handler)
        try:
            with pytest.raises(SyncError,match='source_file_changed'):await service.upload_resource({'id':'synthetic','filename':'changed.txt','mime':'text/plain','sha256':'0'*64,'bytes':path.stat().st_size,'path':path})
            assert not finished and not service.resources
        finally:await client.aclose()
    asyncio.run(run())


def test_download_truncation_never_enters_local_intake(tmp_path):
    async def run():
        def handler(request):return httpx.Response(200,content=b'truncated')
        service,client=worker(tmp_path,handler)
        capture={'id':str(uuid4()),'files':[{'resource_id':'capture:synthetic:hash','sha256':'0'*64,'bytes':100,'filename':'note.pdf','mime':'application/pdf'}]}
        try:
            with pytest.raises(SyncError,match='download_hash_mismatch'):await service.download_capture(capture)
        finally:await client.aclose()
    asyncio.run(run())


def test_projection_batches_are_bounded_by_encoded_utf8_bytes_and_count():
    items=[{'kind':'synthetic','text':'课'*100,'index':i} for i in range(15)]
    result=list(batches(items,max_bytes=1000,max_items=5))
    assert [x for batch in result for x in batch]==items
    assert all(len(json.dumps(batch,ensure_ascii=False,separators=(',',':')).encode())<=1000 and len(batch)<=5 for batch in result)


def test_capture_progress_uses_real_learning_context_and_preserves_lecture_identity():
    inbox={'kind':'inbox_item','payload':{'id':'raw-synthetic','processing_status':'completed','needs_manual':False,'version':3}}
    learning={'kind':'learning_capture','payload':{'id':'learning-synthetic','raw_item_id':'raw-synthetic','created_at':'2026-10-29T00:00:00Z','status':'completed',
        'context':{'note_share':True,'course_id':'course-synthetic','lecture_id':'lecture-synthetic','note_automation':{'status':'needs_source','message':'请核对当前教材小节。'}}}}
    value=capture_states([inbox,learning])[0]
    assert value['state']=='needs_confirmation' and value['lecture_id']=='lecture-synthetic' and value['course_id']=='course-synthetic'
    assert value['learning_capture_id']=='learning-synthetic' and value['error']['message']=='请核对当前教材小节。'
    learning['payload']['context']['note_automation']={'status':'completed'}
    value=capture_states([inbox,learning])[0]
    assert value['state']=='completed' and value['error'] is None


def test_preview_targets_are_actual_classroom_evidence_not_the_entire_book():
    records=[{'kind':'document','id':'book','payload':{'page_count':300}},
        {'kind':'facade','id':'/school/lectures/lesson/context','payload':{'sources':[
            {'document_id':'book','page':271},{'document_id':'notes','page':1},{'document_id':'book','page':271},
            {'document_id':'book','page':-1},{'document_id':'book','page':True},{'attachment_id':'photo'}]}},
        {'kind':'facade','id':'/school/textbook','payload':{'sources':[{'document_id':'book','page':300}]}}]
    assert preview_targets(records)=={('book',271),('notes',1)}


def test_restart_catalogue_skips_identical_read_models_but_publishes_actual_source_edits(tmp_path):
    async def run():
        sent=[]
        def handler(request):
            if request.url.path.endswith('/worker/records'):
                return httpx.Response(200,json={'items':[{'kind':'course','id':'preserved','source_hash':'a'*64}], 'limit':500})
            assert request.url.path.endswith('/worker/publish')
            sent.extend(json.loads(request.content)['records']);return httpx.Response(200,json={'revision':2})
        service,client=worker(tmp_path,handler)
        service.projection=SimpleNamespace(resources=lambda:([],[]),records=lambda:[
            {'kind':'course','id':'preserved','payload':{'name':'Unchanged'},'source_hash':'a'*64},
            {'kind':'course','id':'changed','payload':{'name':'Edited'},'source_hash':'b'*64}])
        async def no_capture_progress(records):pass
        service.publish_capture_states=no_capture_progress
        try:
            await service.record_catalogue();await service.publish()
            assert [row['id'] for row in sent]==['changed']
            assert service.hashes[('course','preserved')]=='a'*64
            sent.clear();await service.publish();assert not sent
        finally:await client.aclose()
    asyncio.run(run())
