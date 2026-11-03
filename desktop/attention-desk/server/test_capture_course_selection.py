"""Explicit authenticated course choice is separate from a device suggestion."""
import asyncio
from uuid import uuid4

import pytest

from server.collector.security import CollectorError
from server.collector.submissions import SubmissionService
from server.learning.auto_notes import AutoNotes
from server.learning.service import LearningService
from server.personal_store import PersonalError
from server.test_course_school import english_course
from server.test_documents import text_pdf
from server.test_school import rig


def test_legacy_submission_fingerprints_match_the_published_v15_golden(tmp_path):
    path=tmp_path/'fixture.pdf';path.write_bytes(b'original fingerprint fixture')
    payload={'submission_id':'11111111-1111-4111-8111-111111111111','text':'class date: 2026-10-28',
        'source_name':'Synthetic note','source_time':'2026-10-28T06:45:00Z','purpose':'lecture_note','confirm_large':True}
    # Recorded from f0545a0's SubmissionService, not calculated from the new code.
    assert SubmissionService.fingerprints('file',payload,[('fixture.pdf','application/pdf',path)])==(
        '2eeb20ac079b04d5d8cea05a3eb64ca66b25021c277f7a61f860c32e9b4e3157','7c3de6d160658c9a7c693676d309367e35324399a658d9683fd9d23b45a6c34f')
    assert SubmissionService.fingerprints('text',payload,[])==(
        '342b455484eff537a792a7272e9c2a93f19274102d23562c5b15108e2eccf5b4','3b2d515a191b66a62bf68263388bc7bc1ef6750f1e279ffc525803f4458199ca')


def test_selected_course_is_saved_before_processing_and_same_bytes_keep_distinct_course_contexts(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service);intake=SubmissionService(r.store,r.docs,r.extract)
            scheduled=[];monkeypatch.setattr(intake,'_schedule',scheduled.append)
            file=text_pdf(tmp_path/'selected-notes.pdf',['Original note sentence, supplied for the selected course.'])
            files=[(file.name,'application/pdf',file)];owner='cloud:'+str(uuid4())
            payload={'submission_id':str(uuid4()),'text':'课堂日期：2026-10-28','purpose':'lecture_note',
                'source_name':'Original course note','source_time':'2026-10-29T09:00:00+08:00'}
            try:
                first=await intake.submit(owner,'file',payload,files,confirmed_course_id=course)
                again=await intake.submit(owner,'file',payload,files,confirmed_course_id=course)
                second=await intake.submit(owner,'file',{**payload,'submission_id':str(uuid4())},files,confirmed_course_id='course-math-ii')
                same_course=await intake.submit(owner,'file',{**payload,'submission_id':str(uuid4())},files,confirmed_course_id=course)
                assert first['saved'] and again['id']==first['id'] and same_course['submission_id']==first['submission_id']
                assert second['submission_id']!=first['submission_id'] and len(scheduled)==2
                with r.store.connection() as c:
                    raw=c.execute('SELECT inbox_course_id,inbox_original_snapshot_id FROM raw_item WHERE id=?',(first['submission_id'],)).fetchone()
                    attachment=c.execute('SELECT id FROM attachment WHERE raw_item_id=?',(first['submission_id'],)).fetchone()[0]
                    other_attachment=c.execute('SELECT id FROM attachment WHERE raw_item_id=?',(second['submission_id'],)).fetchone()[0]
                assert raw['inbox_course_id']==course
                learning=LearningService(r.store,r.service,r.extract)
                try:
                    context=learning.context(first['submission_id'],raw['inbox_original_snapshot_id'],payload['text'])
                    assert context['course_id']==course and context['course_basis']=='existing_link'
                    await AutoNotes(learning).run(first['submission_id'],payload['source_name'],payload['text'],context)
                    assert context['note_automation']['status']=='local_ready',context
                    lecture=r.service.store.lecture(context['lecture_id'])
                    assert lecture['course_id']==course and lecture['lecture_date']=='2026-10-28'
                    assert r.service.learning.register(attachment)['id']==r.service.learning.register(other_attachment)['id']
                finally:await learning.close()
                with pytest.raises(CollectorError) as changed:await intake.submit(owner,'file',payload,files,confirmed_course_id='course-math-ii')
                assert changed.value.code=='submission_changed' and r.provider.calls==[]
            finally:await intake.close()
    asyncio.run(run())


def test_notes_capability_cannot_set_owner_course_and_unknown_course_cannot_save_a_receipt(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service);intake=SubmissionService(r.store,r.docs,r.extract)
            monkeypatch.setattr(intake,'_schedule',lambda *args:None)
            payload={'submission_id':str(uuid4()),'text':'Original note text','purpose':'lecture_note'}
            try:
                with pytest.raises(CollectorError) as denied:await intake.submit('notes-capability','text',payload,confirmed_course_id=course)
                assert denied.value.code=='scope_denied'
                with pytest.raises(PersonalError) as unknown:await intake.submit('cloud:'+str(uuid4()),'text',payload,confirmed_course_id='missing-course')
                assert unknown.value.code=='invalid_course'
                with r.store.connection() as c:assert c.execute('SELECT count(*) FROM inbox_receipt').fetchone()[0]==0
                assert r.provider.calls==[]
            finally:await intake.close()
    asyncio.run(run())
