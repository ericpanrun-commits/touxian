"""Synthetic note intake and routing. No real learner DB or external model calls."""
import asyncio,json
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from types import SimpleNamespace
import pytest
from server.collector.security import PairingAuthority,CollectorError
from server.test_collector import rig as collector_rig
from server.test_school import rig,prepared,intake
from server.test_documents import text_pdf
from server.learning.auto_notes import AutoNotes,resolve_note_day,vision_authorization
from server.learning.service import LearningService
from server.learning.models import NotesVisionAuthorizationInput
from server.learning.api import authorize_notes_vision
from server.personal_store import PersonalError

def test_notes_connection_is_memory_only_append_capability_and_revoked(tmp_path):
    clock=[datetime(2026,10,28,tzinfo=timezone.utc)]
    authority=PairingAuthority(clock=lambda:clock[0],diagnostics_path=tmp_path/'pairing.json')
    value=authority.issue_notes();assert authority.notes_session(value).owner.startswith('notes-')
    with pytest.raises(CollectorError):authority.session(value)
    assert not PairingAuthority(diagnostics_path=tmp_path/'pairing.json').notes_ready()
    assert not any(value in p.read_text() for p in tmp_path.glob('*.json'))
    clock[0]+=timedelta(days=8)
    with pytest.raises(CollectorError):authority.notes_session(value)
    authority.issue_notes();authority.revoke('network_changed');assert not authority.notes_ready()

def test_notes_key_cannot_read_inbox_or_use_general_intake_and_upload_deduplicates(tmp_path):
    async def run():
        async with collector_rig(tmp_path) as r:
            value=r.authority.issue_notes();r.client.cookies.clear();r.client.headers.pop('X-Collector-CSRF',None)
            headers={'X-Eric-Notes-Key':value}
            assert (await r.client.get('/api/session',headers=headers)).status_code==401
            assert (await r.client.post('/api/text',headers=headers,json={'submission_id':str(uuid4()),'text':'not allowed'})).status_code==401
            path=text_pdf(tmp_path/'课堂笔记.pdf',['Synthetic classroom notes, no student evidence.'])
            for _ in range(2):
                response=await r.client.post('/api/notes',headers=headers,files={'file':('课堂笔记.pdf',path.read_bytes(),'application/pdf')})
                assert response.status_code==202 and response.json()['saved'] is True
            with r.store.connection() as c:
                assert c.execute("SELECT count(*) FROM raw_item WHERE kind='inbox_lecture_note'").fetchone()[0]==1
                assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==0
            rejected=await r.client.post('/api/notes',headers=headers,files={'file':('x.pdf',path.read_bytes(),'application/pdf')},data={'course_id':'forged'})
            assert rejected.status_code==400
    asyncio.run(run())

@pytest.mark.parametrize('title,body,expected',[('2026-10-26 notes.pdf','无穷小比较',('2026-10-26','note_header')),('notes.pdf','2026-10-25 与 2026-10-26 两天笔记',(None,'conflicting_note_dates')),('notes.pdf','无穷小比较',('2026-10-29','share_day_default'))])
def test_note_day_uses_explicit_header_or_declared_share_default(title,body,expected):
    assert resolve_note_day(title,body,'2026-10-28T17:00:00+00:00','2026-10-28T17:01:00+00:00')==expected


def test_parallel_new_note_uploads_share_one_course_day_lecture(tmp_path):
    async def run():
        from concurrent.futures import ThreadPoolExecutor
        from server.school.models import LectureInput
        async with rig(tmp_path) as r:
            inputs=[]
            for i in range(2):
                raw,_=intake(r.service,text_pdf(tmp_path/f'note-{i}.pdf',[f'Classroom source {i}, no learner attempts.']))
                inputs.append((LectureInput(title=f'课堂笔记 {i}',lecture_date='2026-10-26',notes_raw_item_id=raw),str(uuid4())))
            with ThreadPoolExecutor(max_workers=2) as pool:
                lessons=list(pool.map(lambda item:r.service.store.create_lecture(*item,automatic_note=True),inputs))
            assert lessons[0]['id']==lessons[1]['id']
            lesson=r.service.store.lecture(lessons[0]['id'])
            assert len(lesson['notes'])==2
            previous_version=lesson['version']
            for payload,key in inputs:r.service.store.create_lecture(payload,key,automatic_note=True)
            assert r.service.store.lecture(lesson['id'])['version']==previous_version
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==1
                assert c.execute('SELECT count(*) FROM review_artifact').fetchone()[0]==0
                assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==0
    asyncio.run(run())

def test_existing_document_reuses_original_lecture_and_repeated_intake_has_no_new_attempt_or_review(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            learning=LearningService(r.store,r.service,r.extract);notes=AutoNotes(learning)
            for _ in range(2):
                ctx={'course_id':'course-math-ii','course_basis':'existing_link','source_time':'2026-10-28T10:00:00+08:00'}
                await notes.run(p.raw_id,'Apple Notes 课堂笔记','Squeeze theorem notes.',ctx)
                assert ctx['lecture_id']==p.lecture['id'] and ctx['note_automation']['status']=='local_ready'
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==1
                assert c.execute('SELECT count(*) FROM lecture_note').fetchone()[0]==1
                assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==0
                assert c.execute('SELECT count(*) FROM review_artifact').fetchone()[0]==0
            await learning.close()
    asyncio.run(run())

def test_unknown_course_does_not_route_all_shared_notes_into_the_only_registered_course(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            path=text_pdf(tmp_path/'unrelated.pdf',['Unrelated meeting, not mathematics.'])
            raw,doc=intake(r.service,path)
            with r.store.connection(write=True) as c:c.execute('UPDATE raw_item SET inbox_course_id=NULL WHERE id=?',(raw,))
            learning=LearningService(r.store,r.service,r.extract);ctx={'course_id':None}
            await AutoNotes(learning).run(raw,'Apple Notes 课堂笔记','A meeting agenda.',ctx)
            assert ctx['note_automation']['status']=='needs_context'
            with r.store.connection() as c:assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==0
            await learning.close()
    asyncio.run(run())

def test_empty_local_ocr_can_continue_to_the_existing_original_page_fallback():
    class Jobs:
        def get(self,key):return {'status':'failed','result':{'failed':[{'page':1,'code':'empty_text'}]}}
    async def run():
        from types import SimpleNamespace
        service=SimpleNamespace(school=SimpleNamespace(jobs=Jobs()),personal=None)
        value=await AutoNotes(service).wait({'id':'isolated-job'},allow_empty_text=True)
        assert value['status']=='failed'  # failure retained; never fabricated text
    asyncio.run(run())

def test_vision_is_off_without_separate_image_consent_and_no_external_job_is_started(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path,notes_quality='ocr_unverified')
            learning=LearningService(r.store,r.service,r.extract)
            learning.analysis_status=lambda:{'enabled':True}
            ctx={'lecture_id':p.lecture['id'],'note_automation':{'status':'local_ready'}}
            assert vision_authorization(r.store)['enabled'] is False
            await AutoNotes(learning).review(ctx)
            assert ctx['note_automation']['status']=='needs_vision_permission'
            with r.store.connection() as c:assert c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0]==0
            await learning.close()
    asyncio.run(run())

def test_note_image_scope_requires_the_explicit_new_confirmation(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            learning=LearningService(r.store,r.service,r.extract);calls=[]
            learning.resume_shared_notes=lambda:calls.append('resume')
            common={'scope_version':'notes-rendered-pages-v1','enabled':True,'confirmation_source':'explicit_user_chat'}
            with pytest.raises(PersonalError):await authorize_notes_vision(NotesVisionAuthorizationInput(**common,confirmation_text='允许上述范围自动处理'),learning)
            result=await authorize_notes_vision(NotesVisionAuthorizationInput(**common,confirmation_text='允许自动处理最多2页笔记图像'),learning)
            assert result=={'enabled':True,'scope_version':'notes-rendered-pages-v1','maximum_pages':2} and calls==['resume']
            with r.store.connection() as c:
                value=json.loads(c.execute("SELECT payload_json FROM learning_artifact WHERE kind='note_vision_authorization'").fetchone()[0])
                assert 'original_pdf_files' in value['excluded'] and value['maximum_pages']==2
            await learning.close()
    asyncio.run(run())


def test_rerendered_old_note_without_date_never_silently_creates_todays_duplicate_lecture(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            with r.store.connection(write=True) as c:
                c.execute("UPDATE textbook SET is_fixture=0")  # isolated routing fixture only
                c.execute("UPDATE document_section SET title='第一章 第六节 极限存在准则与两个重要极限'")
                c.execute("UPDATE lecture SET title='极限存在准则与两个重要极限' WHERE id=?",(p.lecture['id'],))
            path=text_pdf(tmp_path/'极限存在准则与两个重要极限.pdf',['Isolated re-export of an older note.'])
            raw,doc=intake(r.service,path)
            learning=LearningService(r.store,r.service,r.extract)
            ctx={'course_id':'course-math-ii','source_time':'2026-10-29T00:00:00+08:00'}
            await AutoNotes(learning).run(raw,'Apple Notes 课堂笔记','Isolated older note.',ctx)
            assert ctx['note_automation']['status']=='needs_context'
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==1
                assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==0
            await learning.close()
    asyncio.run(run())


def test_automatic_note_vision_rejects_textbook_images_before_rendering_or_external_calls(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            learning=LearningService(r.store,r.service,r.extract)
            authorization=learning.store.reserve_artifact('note_vision_authorization','isolated-image-scope')
            learning.store.finish_artifact(authorization['id'],{'enabled':True,'scope_version':'notes-rendered-pages-v1'})
            r.service.jobs.get=lambda _: {'payload':{'automatic_note':True}}
            with pytest.raises(PersonalError) as error:
                await r.service._vision_batch('isolated-auto-job',p.lecture['id'],[{'document_id':r.service.store.book()['document_id'],'page':1}])
            assert error.value.code=='note_vision_scope_exceeded'
            assert not r.provider.calls
            await learning.close()
    asyncio.run(run())


def test_vision_can_identify_missing_section_with_two_note_pages_and_no_second_image_request(tmp_path):
    async def run():
        from server.school.models import LectureInput
        from server.extraction_provider import ProviderResult
        from server.personal_store import encode
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            with r.store.connection(write=True) as c:
                c.execute('UPDATE textbook SET is_fixture=0')  # isolated routing fixture only
                c.execute("UPDATE document_section SET title='第一章 第八节 函数的连续性'")
            path=text_pdf(tmp_path/'new-isolated-note.pdf',['Isolated note page one.','Isolated note page two.','Isolated note page three.'])
            raw,doc=intake(r.service,path)
            lecture=r.service.store.create_lecture(LectureInput(lecture_date='2026-10-29',title='2026-10-29 高等数学 II ·',notes_raw_item_id=raw),str(uuid4()))
            for index in [1,2,3]:r.service.learning.store_page(doc['id'],index,{'extracted_text':'Unclear isolated OCR.','metadata':{}},method='windows.media.ocr',quality='ocr_unverified')
            learning=LearningService(r.store,r.service,r.extract);learning.analysis_status=lambda:{'enabled':True}
            policy=learning.store.reserve_artifact('note_vision_authorization','isolated-recovery-consent')
            learning.store.finish_artifact(policy['id'],{'enabled':True,'scope_version':'notes-rendered-pages-v1'})
            async def vision(pages,config,key,gate):
                gate();r.provider.calls.append(('vision',len(pages)))
                assert {item['document_id'] for item in pages}=={doc['id']} and len(pages)==2
                return ProviderResult(encode({'pages':[{'source_id':item['source_id'],'transcript':'函数的连续性：函数在某点有定义，函数极限存在，并且极限等于该点函数值。','clarity':'clear','uncertainties':[]} for item in pages]}),{'prompt_tokens':100,'completion_tokens':100},'deepseek-flash')
            r.provider.vision=vision
            actual_get=r.service.jobs.get
            def get(job_id):return {'status':'completed','result':{'artifact_id':'isolated-review'}} if job_id=='isolated-compose-job' else actual_get(job_id)
            r.service.jobs.get=get
            generated=[]
            async def generate(lecture_id,payload,*,automatic_note=False):
                generated.append(payload.use_vision)
                assert automatic_note and not payload.use_vision
                assert any(s['link_status']=='confirmed' for s in r.service.store.lecture(lecture_id)['sections'])
                return {'job':{'id':'isolated-compose-job'}}
            r.service.generate=generate
            ctx={'lecture_id':lecture['id'],'automatic_lecture_title':lecture['title'],'note_automation':{'status':'needs_source'}}
            await AutoNotes(learning).review(ctx)
            assert ctx['note_automation']['status']=='completed'
            assert r.provider.calls==[('vision',2)] and generated==[False]
            assert '函数的连续性' in r.service.store.lecture(lecture['id'])['title']
            with r.store.connection() as c:
                assert c.execute('SELECT sum(image_count) FROM school_ai_attempt').fetchone()[0]==2
                assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==0
                assert c.execute('SELECT count(*) FROM document_page WHERE document_id=? AND vision_attempt_id IS NOT NULL',(r.service.store.book()['document_id'],)).fetchone()[0]==0
            await AutoNotes(learning).review(ctx)
            assert r.provider.calls==[('vision',2)]
            await learning.close()
    asyncio.run(run())


def test_shared_lecture_note_examples_never_become_student_attempts(tmp_path,monkeypatch):
    async def run():
        from server.review_store import ReviewStore
        from server.extraction_models import RawInput
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            text='高数课堂笔记中的例题\n题目：函数极限的条件\nA. 条件一\nB. 条件二\n我的答案：B\n正确答案：A\n回答错误'
            bundle=ReviewStore(r.store).collect(RawInput(original_text=text,title_hint='课堂笔记'),str(uuid4()))
            raw=bundle['snapshot']['raw_item_id'];sid=bundle['snapshot']['id']
            with r.store.connection(write=True) as c:c.execute("UPDATE raw_item SET kind='inbox_lecture_note' WHERE id=?",(raw,))
            async def ready(self,raw_id,title,text,context,**options):context.update(course_id='course-math-ii',lecture_id=p.lecture['id'],note_automation={'status':'completed'})
            async def reviewed(self,context,**options):pass
            monkeypatch.setattr(AutoNotes,'run',ready);monkeypatch.setattr(AutoNotes,'review',reviewed)
            learning=LearningService(r.store,r.service,r.extract);await learning.ingest(raw,sid)
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==0
                assert c.execute('SELECT status FROM learning_capture WHERE raw_item_id=?',(raw,)).fetchone()[0]=='completed'
            await learning.close()
    asyncio.run(run())
