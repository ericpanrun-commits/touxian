"""Actual local note continuation, isolated originals, and cross-course provenance."""
import asyncio
import json
from uuid import uuid4

import pytest

from server.cloud_sync.projection import CloudProjection
from server.courses import CourseStore
from server.inbox_store import InboxStore
from server.learning.auto_notes import AutoNotes,resolve_note_day
from server.learning.service import LearningService
from server.school.models import LectureInput,SourceCorrectionInput
from server.school.source_correction import save_correction
from server.test_course_school import english_course,intake
from server.test_school import rig,generate_input,finish,prepared
from server.test_school_general import GeneralProvider


def test_general_notes_without_book_finish_the_existing_review_chain_once(tmp_path):
    async def run():
        async with rig(tmp_path,fixture_mode=False) as r:
            course=english_course(r.service);provider=GeneralProvider();r.service.provider=provider
            raw,doc=intake(r.service,tmp_path,'general-auto','Original course wording with an example sentence.',course)
            learning=LearningService(r.store,r.service,r.extract);learning.analysis_status=lambda:{'enabled':True}
            try:
                notes=AutoNotes(learning)
                for _ in range(2):
                    context={'note_share':True,'course_id':course,'course_basis':'user_confirmation','confirmed_note_date':'2026-10-28'}
                    await notes.run(raw,'课堂笔记','Original course wording.',context)
                    await notes.review(context)
                    assert context['note_automation']['status']=='completed',context
                    lecture=r.service.store.lecture(context['lecture_id'])
                    assert lecture['course_id']==course and lecture['learning_unit_id'] is None and lecture['textbook'] is None
                    review=r.service.artifacts.get(context['note_automation']['review_id'])
                    assert review['content']['source_scope']=='notes_only' and not review['adopted']
                assert provider.calls==['evidence','composition']
                with r.store.connection() as c:
                    assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==1
                    assert c.execute('SELECT count(*) FROM lecture_note').fetchone()[0]==1
                    assert c.execute('SELECT count(*) FROM task').fetchone()[0]==0
            finally:await learning.close()
    asyncio.run(run())


def test_general_upload_day_alone_does_not_manufacture_a_lecture_date(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            raw,doc=intake(r.service,tmp_path,'old-undated','Original notes without an actual classroom date.',course)
            learning=LearningService(r.store,r.service,r.extract)
            try:
                context={'course_id':course,'course_basis':'existing_link','source_time':'2026-10-28T09:00:00+08:00'}
                await AutoNotes(learning).run(raw,'课堂笔记','Original notes.',context)
                assert context['note_automation']['status']=='needs_context' and not context.get('lecture_id')
                with r.store.connection() as c:assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==0
                assert r.service.learning.get(doc['id'])['sha256']==doc['sha256'] and r.provider.calls==[]
                assert resolve_note_day('笔记.pdf','',context['source_time'],'2026-10-29',allow_share_default=False)==(None,'note_date_missing')
                assert resolve_note_day('2026-10-26.pdf','',None,None,allow_share_default=False)==('2026-10-26','note_header')
            finally:await learning.close()
    asyncio.run(run())


def test_shared_document_in_another_course_never_hijacks_the_confirmed_course(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            old=prepared(r.service,tmp_path,book=False);course=english_course(r.service)
            source=r.docs.files.path(r.docs.files.get(old.note['attachment_id']))
            saved=r.docs.files.collect([('shared-notes.pdf','application/pdf',source)],'',
                'Separate course submission',submission_id=str(uuid4()))
            raw=saved['snapshot']['raw_item_id'];InboxStore(r.store).link(raw,1,course,'')
            assert r.service.learning.register(saved['snapshot']['attachments'][0]['id'])['id']==old.note['id']
            learning=LearningService(r.store,r.service,r.extract)
            try:
                context={'note_share':True,'course_id':course,'course_basis':'user_confirmation','confirmed_note_date':'2026-10-28'}
                await AutoNotes(learning).run(raw,'共享原件','Original shared wording.',context)
                assert context['lecture_id']!=old.lecture['id'] and context['note_automation']['status']=='local_ready',context
                current=r.service.store.lecture(context['lecture_id'])
                assert current['course_id']==course and current['notes'][0]['id']==old.note['id']
                assert current['notes'][0]['raw_item_id']==raw
                assert r.service.store.lecture(old.lecture['id'])['notes'][0]['raw_item_id']==old.raw_id
                assert r.service.store.note_attachment(old.note['id'],raw,course)['raw_item_id']==raw
                with r.store.connection() as c:assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==2
                assert not r.provider.calls
            finally:await learning.close()
    asyncio.run(run())


@pytest.mark.parametrize('proof',['matching_copy','different_bytes','changed_course','unrelated_root'])
def test_reused_document_capture_needs_exact_snapshot_course_and_failed_review_lineage(tmp_path,proof):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            first,doc=intake(r.service,tmp_path,'first-source','Original sentence retained as the shared immutable document.',course)
            original=r.docs.files.path(r.docs.files.get(doc['attachment_id']))
            # A new genuine Raw/snapshot shares the same original bytes. Its
            # document keeps the first attachment identity, as designed.
            saved=r.docs.files.collect([('second-source.pdf','application/pdf',original)],'',
                'Second independent submission',submission_id=str(uuid4()))
            raw=saved['snapshot']['raw_item_id'];sid=saved['snapshot']['id']
            InboxStore(r.store).link(raw,1,course,'')
            lecture=r.service.store.create_lecture(LectureInput(course_id=course,title='原文核对',notes_raw_item_id=raw),str(uuid4()))
            provider=GeneralProvider();provider.bad=True;provider.repair_passes=False;r.service.provider=provider
            queued=await r.service.generate(lecture['id'],generate_input(lecture,use_vision=False));await finish(r.service)
            old=r.service.artifacts.get(r.service.jobs.get(queued['job']['id'])['result']['artifact_id'])
            descriptor=old['source_correction'];assert descriptor
            assert r.service.jobs.get(old['job_id'])['raw_item_id']==raw
            learning=LearningService(r.store,r.service,r.extract)
            try:
                capture_raw,capture_sid=raw,sid
                if proof=='different_bytes':
                    capture_raw,_=intake(r.service,tmp_path,'different-original','Different immutable note bytes.',course)
                    with r.store.connection() as c:capture_sid=c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=?',(capture_raw,)).fetchone()[0]
                context={'note_share':True,'course_id':course,'lecture_id':lecture['id'],
                    'note_automation':{'status':'needs_review','document_id':doc['id'],
                        'review_id':old['id'] if proof!='unrelated_root' else 'unrelated-review','message':'Awaiting source check'}}
                capture=learning.store.record_capture(capture_raw,capture_sid,'Independent source capture',context)
                learning.store.capture_status(capture['id'],'needs_context',error={'message':'Review needs source check'})
                body=SourceCorrectionInput.model_validate({'request_id':str(uuid4()),'expected_version':lecture['version'],
                    'draft_hash':descriptor['draft_hash'],
                    'changes':[{'key':f['key'],'segments':['请依据原文核对这段说明。']+f['segments'][1:]} for f in descriptor['fields']]})
                corrected=save_correction(r.store,old['id'],body)
                if proof=='changed_course':
                    other=CourseStore(r.store).create('另一门课')['id']
                    with r.store.connection(write=True) as c:c.execute('UPDATE raw_item SET inbox_course_id=? WHERE id=?',(other,raw))
                local=learning.capture_detail(capture['id'])
                assert local['status']==('completed' if proof=='matching_copy' else 'needs_context')
                if proof=='matching_copy':assert local['context']['note_automation']['review_id']==corrected['artifact_id'] and local['error'] is None
                records=CloudProjection(r.store,tmp_path/'cloud').records()
                projected=next(record['payload'] for record in records if record['kind']=='learning_capture' and record['id']==capture['id'])
                assert projected['status']==local['status'] and provider.calls==['evidence','composition','repair']
            finally:await learning.close()
    asyncio.run(run())
