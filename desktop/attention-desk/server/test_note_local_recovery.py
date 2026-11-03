"""Process interruption may resume local work, never an unknown paid request."""
import asyncio
import copy
from uuid import uuid4

import pytest

from server.learning.auto_notes import AutoNotes
from server.learning.service import LearningService
from server.test_course_school import english_course,intake
from server.test_school import rig
from server.test_school import finish
from server.test_school_general import GeneralProvider
from server.school.models import BookMetadataInput,SectionInput
from server.test_documents import text_pdf


@pytest.mark.parametrize('entry',['direct','startup_schedule'])
def test_interrupted_local_note_continues_after_restart_without_manual_repair(tmp_path,monkeypatch,entry):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            raw,_=intake(r.service,tmp_path,'restart-note','Original wording saved before the local parser was interrupted.',course)
            context={'note_share':True,'course_id':course,'course_basis':'user_confirmation','confirmed_note_date':'2026-10-28'}
            persisted=copy.deepcopy(context)
            first=LearningService(r.store,r.service,r.extract)
            with r.store.connection() as c:sid=c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=?',(raw,)).fetchone()[0]
            capture=first.store.record_capture(raw,sid,'Original wording.',persisted)
            notes=AutoNotes(first)
            async def process_stopped(job,**options):
                r.service.jobs.recover()
                raise asyncio.CancelledError()
            with monkeypatch.context() as stopped:
                stopped.setattr(r.service,'_schedule_local',lambda *args:None)
                stopped.setattr(notes,'wait',process_stopped)
                with pytest.raises(asyncio.CancelledError):await notes.run(raw,'课堂笔记','Original wording.',context)
            await first.close()
            # These are the same recovery owners invoked by Windows startup.
            restarted=LearningService(r.store,r.service,r.extract)
            try:
                if entry=='direct':
                    await AutoNotes(restarted).run(raw,'课堂笔记','Original wording.',persisted)
                    assert persisted['note_automation']['status']=='local_ready',persisted
                    assert persisted['lecture_id']==context['lecture_id']
                else:
                    provider=GeneralProvider();r.service.provider=provider
                    restarted.analysis_status=lambda:{'enabled':True}
                    restarted.resume_shared_notes()
                    assert restarted.tasks
                    await asyncio.gather(*list(restarted.tasks.values()))
                    await finish(r.service)
                    restored=restarted.capture_detail(capture['id'])
                    assert restored['status']=='completed',restored
                    assert restored['context']['lecture_id']==context['lecture_id']
                    assert provider.calls==['evidence','composition']
                with r.store.connection() as c:
                    assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==1
                    assert c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0]==(0 if entry=='direct' else 2)
                assert r.provider.calls==[]
            finally:await restarted.close()
    asyncio.run(run())


@pytest.mark.parametrize('outcome',['saved','unknown'])
def test_restart_harvests_saved_review_but_never_replays_an_unknown_paid_request(tmp_path,monkeypatch,outcome):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            raw,_=intake(r.service,tmp_path,'review-restart','Original note wording for a safely resumable review.',course)
            provider=GeneralProvider()
            if outcome=='unknown':
                async def interrupted(context,config,secret,gate):
                    gate();provider.calls.append('evidence');raise asyncio.CancelledError()
                provider.extract=interrupted
            r.service.provider=provider
            context={'note_share':True,'course_id':course,'course_basis':'user_confirmation','confirmed_note_date':'2026-10-28'}
            first=LearningService(r.store,r.service,r.extract);first.analysis_status=lambda:{'enabled':True}
            notes=AutoNotes(first);await notes.run(raw,'课堂笔记','Original wording.',context)
            with r.store.connection() as c:sid=c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=?',(raw,)).fetchone()[0]
            capture=first.store.record_capture(raw,sid,'Original wording.',context)
            async def stopped_before_parent_receipt(job,**options):
                if r.service.tasks:await asyncio.gather(*list(r.service.tasks.values()),return_exceptions=True)
                raise asyncio.CancelledError()
            with monkeypatch.context() as stopped:
                stopped.setattr(notes,'wait',stopped_before_parent_receipt)
                with pytest.raises(asyncio.CancelledError):await notes.review(context)
            await first.close()
            prior_calls=list(provider.calls)
            with r.store.connection() as c:prior_attempts=[dict(row) for row in c.execute('SELECT * FROM school_ai_attempt')]
            restarted=LearningService(r.store,r.service,r.extract);restarted.analysis_status=lambda:{'enabled':True}
            try:
                restarted.resume_shared_notes();await asyncio.gather(*list(restarted.tasks.values()))
                restored=restarted.capture_detail(capture['id'])
                assert restored['status']==('completed' if outcome=='saved' else 'needs_context'),restored
                assert restored['context']['note_automation']['status']==('completed' if outcome=='saved' else 'needs_review')
                assert provider.calls==prior_calls
                with r.store.connection() as c:assert [dict(row) for row in c.execute('SELECT * FROM school_ai_attempt')]==prior_attempts
                if outcome=='unknown':assert any(row['status']=='unknown' for row in prior_attempts)
                else:assert restored['context']['note_automation']['review_id']
            finally:await restarted.close()
    asyncio.run(run())


def test_startup_recovers_book_catalog_after_the_file_command_already_completed(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            book=r.service.store.register_book(BookMetadataInput(course_id=course,title='Interrupted catalogue'),str(uuid4()))
            file=text_pdf(tmp_path/'catalogue.pdf',['Original textbook before catalogue processing.'])
            saved=r.docs.files.collect([(file.name,'application/pdf',file)],'','Book original',submission_id=str(uuid4()))
            raw=saved['snapshot']['raw_item_id']
            linked=r.service.store.link_book(raw,1,'full_textbook',textbook_id=book['id'])
            assert linked['document']['page_count'] is None
            with monkeypatch.context() as stopped:
                stopped.setattr(r.service,'_schedule_local',lambda *args:None)
                job=r.service.start_catalog(linked['document_id'],str(uuid4()))
            r.service.jobs.recover()
            r.service.start_local_recovery()
            async def completed():
                while r.service.jobs.get(job['id'])['status'] in {'recovery_needed','waiting','running'}:await asyncio.sleep(.05)
            await asyncio.wait_for(completed(),30)
            assert r.service.jobs.get(job['id'])['status']=='completed'
            current=r.service.store.textbook(book['id'])
            assert current['document_id']==linked['document_id'] and current['document']['page_count']==1
            assert current['real_acceptance']=='pending_evaluation' and r.provider.calls==[]
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM attachment_job').fetchone()[0]==1
                assert c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0]==0
    asyncio.run(run())


def test_local_recovery_does_not_index_a_replaced_books_old_chapter(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            book=r.service.store.register_book(BookMetadataInput(course_id=course,title='Changing source'),str(uuid4()))
            raw,doc=intake(r.service,tmp_path,'old-scope','Original chapter file.',course)
            linked=r.service.store.link_book(raw,1,'chapter_scan',textbook_id=book['id'])
            section=r.service.store.add_section(SectionInput(textbook_id=book['id'],expected_version=linked['version'],
                title='Original chapter',start_page=1,end_page=1))
            with monkeypatch.context() as stopped:
                stopped.setattr(r.service,'_schedule_local',lambda *args:None)
                job=r.service.start_index(section['id'],str(uuid4()))
            r.service.jobs.recover()
            raw,new=intake(r.service,tmp_path,'new-scope','The later source selected by the owner.',course)
            r.service.store.link_book(raw,linked['version'],'chapter_scan',textbook_id=book['id'])
            r.service.recover_local_once()
            result=r.service.jobs.get(job['id'])
            assert result['status']=='needs_review' and result['errors'][0]['code']=='source_scope_changed'
            assert r.service.store.textbook(book['id'])['document_id']==new['id']
            assert r.service.learning.get(doc['id'])['sha256']==doc['sha256'] and r.provider.calls==[]
    asyncio.run(run())
