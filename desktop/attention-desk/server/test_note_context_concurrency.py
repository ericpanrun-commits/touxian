"""Owner confirmation must win when a background note resumes after an await."""
import asyncio
import json
from uuid import uuid4

import pytest

from server.learning.auto_notes import AutoNotes
from server.learning.service import LearningService
from server.learning.note_work import NoteWork
from server.inbox_store import InboxStore
from server.personal_store import PersonalError,encode
from server.school.models import LectureInput
from server.test_course_school import english_course,intake
from server.test_photo_notes import collect_photo,photo
from server.test_school import rig,prepared
from server.test_documents import text_pdf
from server.test_school_general import GeneralProvider


@pytest.mark.parametrize('entry',['ingest','resume'])
@pytest.mark.parametrize('pause_at',['routing','review'])
def test_owner_confirmation_is_not_overwritten_by_an_older_background_note(tmp_path,monkeypatch,entry,pause_at):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            raw,sid,_=collect_photo(r,photo(tmp_path/'race.png'),linked=False)
            with r.store.connection(write=True) as c:
                c.execute("UPDATE raw_item SET kind='inbox_lecture_note' WHERE id=?",(raw,))
            learning=LearningService(r.store,r.service,r.extract)
            with r.store.connection() as c:
                source=c.execute('SELECT original_text FROM raw_snapshot WHERE id=?',(sid,)).fetchone()[0]
            capture=learning.store.record_capture(raw,sid,source,{'note_share':True,'course_id':None})
            paused,release=asyncio.Event(),asyncio.Event()
            calls=[]

            async def route(self,raw_id,title,text,context,**options):
                if context.get('confirmed_note_date'):
                    context['note_automation']={'status':'completed','message':'New owner choice retained.'}
                    return
                if pause_at=='routing':
                    paused.set();await release.wait()
                context.update(course_id='course-math-ii',note_automation={'status':'local_ready','message':'Old background routing.'})

            async def review(self,context,**options):
                if context.get('confirmed_note_date'):return
                calls.append('old-review')
                if pause_at=='review':
                    paused.set();await release.wait()
                context['note_automation']={'status':'completed','message':'Old review finished.'}

            monkeypatch.setattr(AutoNotes,'run',route)
            monkeypatch.setattr(AutoNotes,'review',review)
            old=asyncio.create_task(learning.ingest(raw,sid) if entry=='ingest' else learning.resume_note(capture['id']))
            try:
                await asyncio.wait_for(paused.wait(),5)
                await learning.confirm_capture(capture['id'],course,lecture_date='2026-10-28')
                with r.store.connection() as c:
                    confirmed=dict(c.execute('SELECT * FROM learning_capture WHERE id=?',(capture['id'],)).fetchone())
                release.set();await asyncio.wait_for(old,5)
                with r.store.connection() as c:
                    current=dict(c.execute('SELECT * FROM learning_capture WHERE id=?',(capture['id'],)).fetchone())
                    assert c.execute('SELECT inbox_course_id FROM raw_item WHERE id=?',(raw,)).fetchone()[0]==course
                    assert c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0]==0
                assert current==confirmed
                assert json.loads(current['context_json'])['confirmed_note_date']=='2026-10-28'
                assert calls==([] if pause_at=='routing' else ['old-review'])
                assert not r.provider.calls
            finally:
                release.set()
                await asyncio.gather(old,return_exceptions=True)
                await learning.close()
    asyncio.run(run())


@pytest.mark.parametrize('operation',['create','move','sections'])
def test_domain_writes_recheck_the_note_source_inside_their_write_transaction(tmp_path,monkeypatch,operation):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            learning=LearningService(r.store,r.service,r.extract)
            with r.store.connection() as c:
                sid=c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=?',(p.raw_id,)).fetchone()[0]
            capture=learning.store.record_capture(p.raw_id,sid,'Original note',{'note_share':True,'course_id':'course-math-ii'})
            work=NoteWork(learning,capture['id'])
            def changed():
                with r.store.connection(write=True) as c:
                    c.execute('UPDATE learning_capture SET context_json=? WHERE id=?',
                        (encode({'note_share':True,'course_id':'course-math-ii','confirmed_note_date':'2026-10-28'}),capture['id']))
            # Simulate a newer choice after the slow original-file lookup, but
            # before the domain transaction. The guard must be checked there.
            original=r.service.store._note_document
            def lookup(*args,**kwargs):
                document=original(*args,**kwargs);changed();return document
            monkeypatch.setattr(r.service.store,'_note_document',lookup)
            with r.store.connection() as c:
                before={table:[tuple(row) for row in c.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                    for table in ('lecture','lecture_note','lecture_section','school_request')}
            with pytest.raises(PersonalError) as rejected:
                if operation=='create':
                    r.service.store.create_lecture(LectureInput(title='New class must not be created',lecture_date='2026-10-29',notes_raw_item_id=p.raw_id),str(uuid4()),source_guard=work.current)
                elif operation=='move':
                    r.service.store.confirm_note_lecture(p.raw_id,p.lecture['id'],source_guard=work.current)
                else:
                    changed()
                    r.service.store.link_sections(p.lecture['id'],p.lecture['version'],[],source_guard=work.current)
            assert rejected.value.code=='note_context_changed'
            with r.store.connection() as c:
                for table,rows in before.items():
                    assert [tuple(row) for row in c.execute('SELECT * FROM '+table+' ORDER BY rowid')]==rows
                assert c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0]==0
            assert not r.provider.calls
            await learning.close()
    asyncio.run(run())


@pytest.mark.parametrize('checkpoint',['catalog','note_pages'])
def test_new_choice_during_local_parsing_stops_the_old_association_before_review(tmp_path,monkeypatch,checkpoint):
    async def run():
        async with rig(tmp_path) as r:
            if checkpoint=='catalog':
                path=text_pdf(tmp_path/'new-note.pdf',['Original local material.'])
                saved=r.docs.files.collect([(path.name,'application/pdf',path)],'',path.stem,str(uuid4()))
                raw,sid=saved['snapshot']['raw_item_id'],saved['snapshot']['id']
            else:
                p=prepared(r.service,tmp_path);raw=p.raw_id
                with r.store.connection() as c:sid=c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=?',(raw,)).fetchone()[0]
            with r.store.connection(write=True) as c:
                c.execute("UPDATE raw_item SET kind='inbox_lecture_note',inbox_course_id='course-math-ii' WHERE id=?",(raw,))
            learning=LearningService(r.store,r.service,r.extract)
            context={'note_share':True,'course_id':'course-math-ii','course_basis':'user_confirmation','confirmed_note_date':'2026-10-26'}
            with r.store.connection() as c:text=c.execute('SELECT original_text FROM raw_snapshot WHERE id=?',(sid,)).fetchone()[0]
            capture=learning.store.record_capture(raw,sid,text,context)
            marker={'note_share':True,'course_id':'course-math-ii','confirmed_note_date':'2026-10-28','owner_choice':'newer'}
            seen=[]
            async def parsed(self,job,**options):
                seen.append(job['id'])
                with r.store.connection(write=True) as c:
                    c.execute('UPDATE learning_capture SET context_json=? WHERE id=?',(encode(marker),capture['id']))
                return {'status':'completed'}
            monkeypatch.setattr(AutoNotes,'wait',parsed)
            if checkpoint=='catalog':monkeypatch.setattr(r.service,'start_catalog',lambda *args:{'id':'local-catalog'})
            else:monkeypatch.setattr(r.service,'start_note_index',lambda *args,**kwargs:{'id':'local-pages'})
            await learning.resume_note(capture['id'])
            with r.store.connection() as c:
                current=c.execute('SELECT context_json FROM learning_capture WHERE id=?',(capture['id'],)).fetchone()[0]
                assert json.loads(current)==marker
                assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==(0 if checkpoint=='catalog' else 1)
                assert c.execute("SELECT count(*) FROM learning_artifact WHERE status='running'").fetchone()[0]==0
                assert c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0]==0
            assert seen==['local-catalog' if checkpoint=='catalog' else 'local-pages']
            assert not r.provider.calls
            await learning.close()
    asyncio.run(run())


def test_an_already_started_review_is_retained_without_overwriting_a_new_owner_choice(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path,fixture_mode=False) as r:
            course=english_course(r.service)
            raw,doc=intake(r.service,tmp_path,'review-race','A conditional sentence from the original class.',course)
            learning=LearningService(r.store,r.service,r.extract);learning.analysis_status=lambda:{'enabled':True}
            with r.store.connection() as c:
                sid=c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=?',(raw,)).fetchone()[0]
                text=c.execute('SELECT original_text FROM raw_snapshot WHERE id=?',(sid,)).fetchone()[0]
            capture=learning.store.record_capture(raw,sid,text,{'note_share':True,'course_id':course,
                'course_basis':'user_confirmation','confirmed_note_date':'2026-10-26'})
            provider=GeneralProvider();r.service.provider=provider
            compose=provider.compose;started,release=asyncio.Event(),asyncio.Event()
            async def composition(*args,**kwargs):
                result=await compose(*args,**kwargs)
                started.set();await release.wait()
                return result
            monkeypatch.setattr(provider,'compose',composition)
            old=asyncio.create_task(learning.resume_note(capture['id']))
            try:
                await asyncio.wait_for(started.wait(),10)
                chosen=await learning.confirm_capture(capture['id'],course,lecture_date='2026-10-28')
                assert chosen['context']['note_automation']['status']=='needs_context'
                release.set();await asyncio.wait_for(old,10)
                current=learning.capture_detail(capture['id'])
                assert current==chosen and current['status']=='needs_context'
                with r.store.connection() as c:
                    review=c.execute('SELECT * FROM review_artifact').fetchone()
                    assert review['content_json'] and review['error_json'] is None
                    assert c.execute('SELECT lecture_date FROM lecture WHERE id=?',(review['lecture_id'],)).fetchone()[0]=='2026-10-26'
                    assert c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0]==2
                    assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==1
                    assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==0
                assert not r.service.artifacts.get(review['id'])['adopted']
                assert provider.calls==['evidence','composition']
            finally:
                release.set()
                await asyncio.gather(old,return_exceptions=True)
                await learning.close()
    asyncio.run(run())


def test_inbox_course_change_also_invalidates_the_background_note_commit(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            raw,sid,_=collect_photo(r,photo(tmp_path/'inbox-course.png'))
            target=english_course(r.service)
            learning=LearningService(r.store,r.service,r.extract)
            capture=learning.store.record_capture(raw,sid,'Original source.',{'note_share':True,'course_id':'course-math-ii'})
            work=NoteWork(learning,capture['id'])
            with r.store.connection() as c:version=c.execute('SELECT inbox_version FROM raw_item WHERE id=?',(raw,)).fetchone()[0]
            InboxStore(r.store).link(raw,version,target,'')
            with pytest.raises(PersonalError) as rejected:work.current()
            assert rejected.value.code=='note_context_changed'
            context={**capture['context'],'note_automation':{'status':'completed'}}
            assert work.save(context) is False
            with r.store.connection() as c:
                assert c.execute('SELECT inbox_course_id FROM raw_item WHERE id=?',(raw,)).fetchone()[0]==target
                assert c.execute('SELECT status FROM learning_capture WHERE id=?',(capture['id'],)).fetchone()[0]=='received'
            assert not r.provider.calls
            await learning.close()
    asyncio.run(run())
