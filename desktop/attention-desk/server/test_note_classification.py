"""Isolated one-call filing and original-file contracts; no real paid requests."""
import asyncio
import json
import hashlib
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from server.ai_transport import DeepSeekTransport
from server.documents.api import router
from server.inbox_store import InboxStore
from server.learning.store import LearningStore
from server.personal_store import encode
from server.notes_anywhere.classification import MODE, classify_note
from server.test_collector import rig, payload, count
from server.test_documents import text_pdf
from server.test_learning_analysis import consent, response, calls


def controls(path, monkeypatch, paid=True):
    target = path / 'recovery-controls.json'
    target.write_text(json.dumps({'version':1,'hold_cloud_uploads':True,'pause_paid_ai':paid}))
    monkeypatch.setenv('ATTENTION_RECOVERY_CONTROL_PATH',str(target))


async def received(r, path):
    path = text_pdf(path / 'note.pdf',['Implicit differentiation. Calculus derivatives.'])
    data = payload(purpose='lecture_note',text='')
    receipt = await r.service.submit('desktop','file',data,[('note.pdf','application/pdf',path)])
    await asyncio.gather(*list(r.service.tasks.values()))
    return path, data, receipt, InboxStore(r.store).detail(receipt['submission_id'])


def result(**changes):
    return dict(course_id='course-math-ii',knowledge_point='导数',tags=['微分','求导'],summary='介绍隐函数求导。',uncertain=False,**changes)


def transport(sent, output=None, status=200):
    def handle(request):
        sent.append(json.loads(request.content))
        return response(json.dumps(result() if output is None else output)) if status == 200 else httpx.Response(status)
    return DeepSeekTransport(httpx.MockTransport(handle))


def test_receive_classify_dedup_and_original_view_are_one_closed_loop(tmp_path,monkeypatch):
    controls(tmp_path,monkeypatch)
    async def run():
        async with rig(tmp_path) as r:
            path,data,receipt,d = await received(r,tmp_path)
            assert d['note_processing']['status'] == 'paused'
            assert count(r.store,'learning_capture') == 0
            store=LearningStore(r.store);consent(store);controls(tmp_path,monkeypatch,False)
            sent=[];tx=transport(sent)
            await classify_note(r.service,d['id'],d['review']['snapshot']['id'],transport=tx)
            final=InboxStore(r.store).detail(d['id'])
            assert final['course']['id']=='course-math-ii'
            assert final['note_processing']['status']=='completed'
            assert final['classification']['note_classification']['tags']==['微分','求导']
            assert final['processing_status']=='completed' and not final['needs_manual']
            assert final['preview']=='介绍隐函数求导。'
            assert len(sent)==1 and len(calls(store))==1
            assert calls(store)[0]['status']=='completed'
            assert count(r.store,'lecture')==count(r.store,'review_artifact')==count(r.store,'school_ai_attempt')==0
            await classify_note(r.service,d['id'],d['review']['snapshot']['id'],transport=tx)
            duplicate=await r.service.submit('desktop','file',{**data,'submission_id':str(uuid4())},[('copy.pdf','application/pdf',path)])
            assert duplicate['duplicate'] and duplicate['submission_id']==d['id']
            assert len(sent)==1 and count(r.store,'raw_item')==count(r.store,'attachment')==1
            app=FastAPI();app.state.document_service=r.docs;app.include_router(router)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                url=f"/api/personal/attachments/{d['attachments'][0]['id']}/original"
                inline=await client.get(url+'?inline=true');download=await client.get(url)
                assert inline.status_code==200 and inline.content==download.content==path.read_bytes()
                assert inline.headers['content-type']=='application/pdf'
                assert inline.headers['content-disposition'].startswith('inline;')
                assert inline.headers['x-content-type-options']=='nosniff'
                assert download.headers['content-disposition'].startswith('attachment;')
    asyncio.run(run())


@pytest.mark.parametrize('case',['uncertain','invalid_course','too_many_tags','provider_failure','unreadable_output'])
def test_uncertainty_and_errors_never_retry_or_hide_original(tmp_path,monkeypatch,case):
    controls(tmp_path,monkeypatch)
    async def run():
        async with rig(tmp_path) as r:
            path,_,_,d=await received(r,tmp_path)
            store=LearningStore(r.store);consent(store);controls(tmp_path,monkeypatch,False)
            value=result()
            if case=='uncertain':value.update(course_id=None,knowledge_point='',uncertain=True)
            if case=='invalid_course':value['course_id']='invented-course'
            if case=='too_many_tags':value['tags']=['一','二','三','四']
            if case=='unreadable_output':value={'unexpected':'data'}
            sent=[];tx=transport(sent,value,503 if case=='provider_failure' else 200)
            for _ in range(2):await classify_note(r.service,d['id'],d['review']['snapshot']['id'],transport=tx)
            final=InboxStore(r.store).detail(d['id'])
            assert final['note_processing']['status']=='needs_context' and final['needs_manual']
            assert final['course'] is None and '等待 AI 分析' not in final['preview']
            assert r.docs.files.path(r.docs.files.get(d['attachments'][0]['id'])).read_bytes()==path.read_bytes()
            assert len(sent)==1 and len(calls(store))==1
    asyncio.run(run())


@pytest.mark.parametrize('case',['paid_pause','no_consent','legacy_note','owner_course_changed','owner_cleared_course','concurrent','ocr_empty'])
def test_boundaries_preserve_owner_history_and_one_request(tmp_path,monkeypatch,case):
    controls(tmp_path,monkeypatch)
    async def run():
        async with rig(tmp_path) as r:
            _,_,_,d=await received(r,tmp_path)
            store=LearningStore(r.store)
            if case!='no_consent':consent(store)
            if case!='paid_pause':controls(tmp_path,monkeypatch,False)
            if case=='legacy_note':
                with r.store.connection(write=True) as c:c.execute("UPDATE raw_item SET inbox_classification_json='{}' WHERE id=?",(d['id'],))
            if case=='ocr_empty':
                auth=store.reserve_artifact('note_vision_authorization','test')
                store.finish_artifact(auth['id'],{'enabled':True,'scope_version':'notes-rendered-pages-v1'})
                # Simulate an unavailable OCR read; parsed source versions stay immutable.
                get_bundle=r.service.reviews.get;get_file=r.docs.files.get
                def unreadable(sid):
                    bundle=get_bundle(sid);bundle['snapshot']['original_text']='';return bundle
                monkeypatch.setattr(r.service.reviews,'get',unreadable)
                monkeypatch.setattr(r.docs.files,'get',lambda aid:{**get_file(aid),'needs_review':1})
            sent=[];entered=asyncio.Event();release=asyncio.Event()
            async def handle(request):
                sent.append(json.loads(request.content));entered.set()
                if case in {'concurrent','owner_course_changed','owner_cleared_course'}:await release.wait()
                return response(json.dumps(result()))
            tx=DeepSeekTransport(httpx.MockTransport(handle))
            first=asyncio.create_task(classify_note(r.service,d['id'],d['review']['snapshot']['id'],transport=tx))
            if case in {'concurrent','owner_course_changed','owner_cleared_course'}:
                await entered.wait()
                if case=='concurrent':await classify_note(r.service,d['id'],d['review']['snapshot']['id'],transport=tx)
                else:
                    current=InboxStore(r.store).detail(d['id'])
                    InboxStore(r.store).link(d['id'],current['version'],'course-math-ii','')
                    if case=='owner_cleared_course':
                        current=InboxStore(r.store).detail(d['id'])
                        InboxStore(r.store).link(d['id'],current['version'],None,'')
                release.set()
            await first
            final=InboxStore(r.store).detail(d['id'])
            if case in {'paid_pause','no_consent','legacy_note'}:
                assert not sent and not calls(store)
            else:
                assert len(sent)==1 and len(calls(store))==1
                if case=='owner_cleared_course':assert final['course'] is None
                else:assert final['course']['id']=='course-math-ii'
            if case=='ocr_empty':
                parts=sent[0]['messages'][1]['content']
                assert len([p for p in parts if p['type']=='image_url'])==1
                assert json.loads(parts[0]['text'])['text']==''
                assert json.loads(calls(store)[0]['cost_json'])['cloud_images']==1
                assert final['classification']['note_classification']['cost']['cloud_images']==1
    asyncio.run(run())


def test_fresh_pdf_calls_filing_from_intake_without_complex_review(tmp_path,monkeypatch):
    controls(tmp_path,monkeypatch,False)
    async def run():
        async with rig(tmp_path) as r:
            consent(LearningStore(r.store));sent=[]
            monkeypatch.setattr('server.notes_anywhere.classification.DeepSeekTransport',lambda:transport(sent))
            _,_,_,d=await received(r,tmp_path)
            assert len(sent)==1 and d['note_processing']['status']=='completed'
            assert count(r.store,'learning_capture')==count(r.store,'school_ai_attempt')==0
    asyncio.run(run())


def test_restart_with_unknown_paid_outcome_never_claims_waiting_or_resends(tmp_path,monkeypatch):
    controls(tmp_path,monkeypatch)
    async def run():
        async with rig(tmp_path) as r:
            _,_,_,d=await received(r,tmp_path)
            store=LearningStore(r.store);consent(store);controls(tmp_path,monkeypatch,False)
            fingerprint=hashlib.sha256(encode([d['id'],d['review']['snapshot']['id'],MODE]).encode()).hexdigest()
            parent=store.reserve_artifact('note_classification',fingerprint)
            pending=store.reserve_artifact('learning_ai_call','unknown-request')
            with r.store.connection(write=True) as c:
                c.execute('UPDATE learning_artifact SET payload_json=?,model=? WHERE id=?',
                          (encode({'parent_id':parent['id'],'phase':'dispatched'}),'deepseek-flash',pending['id']))
            sent=[]
            await classify_note(r.service,d['id'],d['review']['snapshot']['id'],transport=transport(sent))
            final=InboxStore(r.store).detail(d['id'])
            assert not sent and final['note_processing']['status']=='needs_context'
            assert '结果尚未确认' in final['preview']
            assert len(calls(store))==1
    asyncio.run(run())


def test_saved_classification_is_harvested_with_paid_work_paused(tmp_path,monkeypatch):
    controls(tmp_path,monkeypatch)
    async def run():
        async with rig(tmp_path) as r:
            _,_,_,d=await received(r,tmp_path)
            store=LearningStore(r.store)
            fingerprint=hashlib.sha256(encode([d['id'],d['review']['snapshot']['id'],MODE]).encode()).hexdigest()
            parent=store.reserve_artifact('note_classification',fingerprint)
            store.finish_artifact(parent['id'],result(),model='deepseek-flash')
            sent=[]
            await classify_note(r.service,d['id'],d['review']['snapshot']['id'],transport=transport(sent))
            final=InboxStore(r.store).detail(d['id'])
            assert not sent and final['note_processing']['status']=='completed'
            assert final['course']['id']=='course-math-ii'
    asyncio.run(run())


def test_legacy_pdf_retry_still_uses_its_existing_note_owner(tmp_path,monkeypatch):
    controls(tmp_path,monkeypatch)
    async def run():
        async with rig(tmp_path) as r:
            _,_,_,d=await received(r,tmp_path)
            with r.store.connection(write=True) as c:
                c.execute("UPDATE raw_item SET inbox_classification_json='{}' WHERE id=?",(d['id'],))
            ingested=[]
            class ExistingLearning:
                async def ingest(self,raw,snapshot):ingested.append((raw,snapshot))
            r.service.learning=ExistingLearning()
            await r.service._finish(d['id'])
            assert len(ingested)==1 and ingested[0][0]==d['id']
            assert not calls(LearningStore(r.store))
    asyncio.run(run())
