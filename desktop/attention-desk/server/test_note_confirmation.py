"""Real domain owners in isolated stores; no fixture reaches owner data or AI."""
import asyncio
import hashlib
import json
from uuid import uuid4
import pytest

from server.cloud_sync.projection import CloudProjection
from server.cloud_sync.service import capture_states
from server.inbox_store import InboxStore
from server.learning.auto_notes import AutoNotes
from server.learning.service import LearningService
from server.learning.store import NOTE_REVIEW_REQUIRED
from server.courses import CourseStore
from server.test_course_school import english_course,intake as course_intake
from server.test_photo_notes import photo, collect_photo
from server.test_school import rig, prepared
from server.test_school import intake
from server.test_documents import text_pdf
from server.school.models import LectureInput


def test_unresolved_note_exposes_existing_lecture_identically_locally_and_in_cloud(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            lesson = prepared(r.service, tmp_path)
            raw, sid, _ = collect_photo(r, photo(tmp_path / 'unconfirmed.png'), linked=False)
            learning = LearningService(r.store, r.service, r.extract)
            capture = learning.store.record_capture(raw, sid, 'Synthetic unconfirmed note', {'note_share': True, 'course_id': None})
            detail = learning.capture_detail(capture['id'])
            assert lesson.lecture['id'] in {item['id'] for item in detail['lecture_choices']}
            assert detail['context']['course_id'] is None
            projection = CloudProjection(r.store, tmp_path / 'cloud-cache')
            before = next(item for item in projection.records() if item['kind'] == 'learning_capture')
            assert before['payload'] == {key: value for key, value in detail.items() if key != 'lecture_choices'}
            # Picker metadata changing is not an edit of this capture. An
            # already-saved offline confirmation must retain its source version.
            with r.store.connection(write=True) as c:
                c.execute('UPDATE lecture SET version=version+1,title=? WHERE id=?', ('Updated picker title', lesson.lecture['id']))
            after = next(item for item in projection.records() if item['kind'] == 'learning_capture')
            assert after['source_hash'] == before['source_hash']
            inbox = InboxStore(r.store).detail(raw)
            assert inbox['learning_capture'] == {'id': capture['id'], 'status': 'received'}
            with r.store.connection() as c:
                assert c.execute('SELECT inbox_course_id FROM raw_item WHERE id=?', (raw,)).fetchone()[0] is None
            assert not r.provider.calls
            await learning.close()
    asyncio.run(run())


def test_course_and_existing_lecture_confirmation_reuses_original_and_has_no_duplicate_lesson(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p = prepared(r.service, tmp_path)
            path = photo(tmp_path / 'unconfirmed.png')
            raw, sid, attachment = collect_photo(r, path, linked=False)
            original = path.read_bytes()
            doc = r.service.learning.register(attachment['id'])
            r.service.learning.store_page(doc['id'], 1, {'extracted_text': 'Isolated OCR remains uncertain.', 'metadata': {}}, method='windows.media.ocr', quality='ocr_unverified')
            learning = LearningService(r.store, r.service, r.extract)
            learning.analysis_status = lambda: {'enabled': False}
            capture = learning.store.record_capture(raw, sid, 'Isolated note', {'note_share': True, 'course_id': None})
            for _ in range(2):
                result = await learning.confirm_capture(capture['id'], 'course-math-ii', lecture_id=p.lecture['id'])
                assert result['context']['lecture_id'] == p.lecture['id']
                assert result['context']['confirmed_note_date'] == '2026-10-26'
                assert result['context']['note_automation']['status'] == 'needs_setup'
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM lecture').fetchone()[0] == 1
                assert c.execute('SELECT count(*) FROM lecture_note WHERE document_id=?', (doc['id'],)).fetchone()[0] == 1
                assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0] == 0
                assert c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0] == 0
            assert r.docs.files.path(r.docs.files.get(attachment['id'])).read_bytes() == original
            await learning.close()
    asyncio.run(run())


def test_legacy_failed_review_status_is_repaired_from_existing_job_without_paid_replay(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p = prepared(r.service, tmp_path)
            learning = LearningService(r.store, r.service, r.extract)
            context = {'note_share': True, 'course_id': 'course-math-ii', 'lecture_id': p.lecture['id'],
                       'note_automation': {'status': 'needs_review', 'message': '笔记处理没有完成，原件和已有结果都已保留。'}}
            with r.store.connection() as c:
                sid = c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=? ORDER BY created_at LIMIT 1', (p.raw_id,)).fetchone()[0]
            capture = learning.store.record_capture(p.raw_id, sid, 'Isolated old status', context)
            attachment = r.docs.files.get(p.note['attachment_id'])
            job, _ = r.service.jobs.create('school_review', {'lecture_id': p.lecture['id'], 'automatic_note': True}, attachment,
                                           hashlib.sha256(b'isolated-validation-failure').hexdigest(), str(uuid4()))
            r.service.jobs.update(job['id'], 'needs_review', 'Source review needed', result={'artifact_id': 'isolated-failed-review'})
            local = learning.capture_detail(capture['id'])
            assert local['context']['note_automation']['message'] == NOTE_REVIEW_REQUIRED
            assert local['context']['note_automation']['review_job_id'] == job['id']
            with r.store.connection() as c:
                stored = json.loads(c.execute('SELECT context_json FROM learning_capture WHERE id=?', (capture['id'],)).fetchone()[0])
                assert stored == context  # read-only migration of the display, not the source
            records = CloudProjection(r.store, tmp_path / 'cloud-cache').records()
            exported = next(row['payload'] for row in records if row['kind'] == 'learning_capture')
            assert exported == {key: value for key, value in local.items() if key != 'lecture_choices'}
            inbox = {'kind': 'inbox_item', 'payload': {'id': p.raw_id, 'processing_status': 'completed', 'needs_manual': False, 'version': 1}}
            state = capture_states([inbox, {'kind': 'learning_capture', 'payload': local}])[0]
            assert state['state'] == 'needs_confirmation' and state['error']['code'] == 'note_review_required'
            assert state['lecture_id'] == p.lecture['id'] and not r.provider.calls
            await learning.close()
    asyncio.run(run())


def test_automatic_review_keeps_validation_failure_as_review_needed_with_job_reference(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p = prepared(r.service, tmp_path)
            learning = LearningService(r.store, r.service, r.extract)
            learning.analysis_status = lambda: {'enabled': True}
            async def generate(*args, **kwargs): return {'job': {'id': 'isolated-review-job'}}
            r.service.generate = generate
            r.service.jobs.get = lambda _: {'id': 'isolated-review-job', 'status': 'needs_review', 'result': {'artifact_id': 'isolated-review'}}
            context = {'lecture_id': p.lecture['id'], 'note_automation': {'status': 'local_ready'}}
            await AutoNotes(learning).review(context)
            value = context['note_automation']
            assert value['status'] == 'needs_review' and value['review_id'] == 'isolated-review'
            assert value['review_job_id'] == 'isolated-review-job' and value['message'] == NOTE_REVIEW_REQUIRED
            with r.store.connection() as c:
                row = c.execute("SELECT status,payload_json FROM learning_artifact WHERE kind='auto_note_review'").fetchone()
                assert row['status'] == 'needs_review' and json.loads(row['payload_json'])['review_id'] == 'isolated-review'
            assert not r.provider.calls
            await learning.close()
    asyncio.run(run())


def test_explicit_lecture_correction_moves_only_this_note_and_preserves_identity(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p = prepared(r.service, tmp_path)
            raw, _ = intake(r.service, text_pdf(tmp_path / 'target-note.pdf', ['Different isolated lesson source.']))
            target = r.service.store.create_lecture(LectureInput(title='Target lesson', lecture_date='2026-10-28', notes_raw_item_id=raw), str(uuid4()))
            learning = LearningService(r.store, r.service, r.extract);learning.analysis_status = lambda: {'enabled': False}
            with r.store.connection() as c:
                sid = c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=? ORDER BY created_at LIMIT 1', (p.raw_id,)).fetchone()[0]
                original_ids = {table: {row[0] for row in c.execute('SELECT id FROM ' + table)} for table in ('raw_item', 'attachment', 'document', 'lecture')}
            capture = learning.store.record_capture(p.raw_id, sid, 'Explicit correction fixture', {'note_share': True, 'course_id': 'course-math-ii', 'lecture_id': p.lecture['id']})
            result = await learning.confirm_capture(capture['id'], 'course-math-ii', lecture_id=target['id'])
            assert result['context']['lecture_id'] == target['id']
            assert result['context']['confirmed_note_date'] == '2026-10-28'
            assert not r.service.store.lecture(p.lecture['id'])['notes']
            assert len(r.service.store.lecture(target['id'])['notes']) == 2
            version = r.service.store.lecture(target['id'])['version']
            await learning.confirm_capture(capture['id'], 'course-math-ii', lecture_id=target['id'])
            assert r.service.store.lecture(target['id'])['version'] == version
            with r.store.connection() as c:
                for table, identities in original_ids.items():
                    assert {row[0] for row in c.execute('SELECT id FROM ' + table)} == identities
                assert not c.execute('PRAGMA foreign_key_check').fetchall()
                assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0] == 0
            assert not r.provider.calls
            await learning.close()
    asyncio.run(run())


def test_date_only_correction_cannot_silently_reuse_a_different_existing_lecture(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p = prepared(r.service, tmp_path)
            learning = LearningService(r.store, r.service, r.extract);learning.analysis_status = lambda: {'enabled': False}
            with r.store.connection() as c:
                sid = c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=? ORDER BY created_at LIMIT 1', (p.raw_id,)).fetchone()[0]
            capture = learning.store.record_capture(p.raw_id, sid, 'Date correction fixture', {'note_share': True, 'course_id': 'course-math-ii', 'lecture_id': p.lecture['id']})
            result = await learning.confirm_capture(capture['id'], 'course-math-ii', lecture_date='2026-10-28')
            assert result['context']['note_automation']['status'] == 'needs_context'
            assert '明确选择目标课次' in result['context']['note_automation']['message']
            assert len(r.service.store.lecture(p.lecture['id'])['notes']) == 1
            with r.store.connection() as c: assert c.execute('SELECT count(*) FROM lecture').fetchone()[0] == 1
            await learning.close()
    asyncio.run(run())


def test_same_document_from_another_input_does_not_erase_the_previous_inputs_link(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p = prepared(r.service, tmp_path)
            duplicate_raw, same_doc = intake(r.service, tmp_path / 'notes.pdf')
            assert same_doc['id'] == p.note['id']
            raw, _ = intake(r.service, text_pdf(tmp_path / 'other.pdf', ['Different isolated classroom source.']))
            target = r.service.store.create_lecture(LectureInput(title='Other lesson', lecture_date='2026-10-28', notes_raw_item_id=raw), str(uuid4()))
            r.service.store.confirm_note_lecture(duplicate_raw, target['id'])
            assert len(r.service.store.lecture(p.lecture['id'])['notes']) == 1
            with r.store.connection() as c:
                old = c.execute('SELECT raw_item_id FROM lecture_note WHERE lecture_id=? AND document_id=?', (p.lecture['id'], p.note['id'])).fetchone()
                new = c.execute('SELECT raw_item_id FROM lecture_note WHERE lecture_id=? AND document_id=?', (target['id'], p.note['id'])).fetchone()
                assert old[0] == p.raw_id and new[0] == duplicate_raw
                assert not c.execute('PRAGMA foreign_key_check').fetchall()
    asyncio.run(run())


@pytest.mark.parametrize('existing_target',[False,True])
def test_correcting_a_previously_confirmed_course_keeps_the_new_date_and_one_note_link(tmp_path,existing_target):
    async def run():
        async with rig(tmp_path,fixture_mode=False) as r:
            course=english_course(r.service)
            target=CourseStore(r.store).create('隔离测试 · 大学物理')['id']
            if existing_target:
                target_raw,_=course_intake(r.service,tmp_path,'target-class','A separate original input.',target)
                r.service.store.create_lecture(LectureInput(course_id=target,title='Existing target class',lecture_date='2026-10-28',notes_raw_item_id=target_raw),str(uuid4()))
            raw,doc=course_intake(r.service,tmp_path,'correct-course','Original source remains immutable.',course)
            learning=LearningService(r.store,r.service,r.extract);learning.analysis_status=lambda:{'enabled':False}
            with r.store.connection() as c:sid=c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=?',(raw,)).fetchone()[0]
            capture=learning.store.record_capture(raw,sid,'Original source.',{'note_share':True,'course_id':course,'course_basis':'user_confirmation'})
            first=await learning.confirm_capture(capture['id'],course,lecture_date='2026-10-26')
            old_id=first['context']['lecture_id']
            await learning.confirm_capture(capture['id'],course,lecture_id=old_id)
            original=r.docs.files.path(r.docs.files.get(doc['attachment_id'])).read_bytes()
            for _ in range(2):
                result=await learning.confirm_capture(capture['id'],target,lecture_date='2026-10-28')
                assert result['context']['course_id']==target
                assert result['context']['confirmed_note_date']=='2026-10-28'
                current=r.service.store.lecture(result['context']['lecture_id'])
                assert current['course_id']==target and current['lecture_date']=='2026-10-28'
                assert result['context']['note_automation']['status']=='needs_setup'
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==2
                links=c.execute('SELECT lecture_id FROM lecture_note WHERE raw_item_id=?',(raw,)).fetchall()
                assert [row[0] for row in links]==[current['id']]
                assert c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0]==0
                assert not c.execute('PRAGMA foreign_key_check').fetchall()
            assert r.service.store.lecture(old_id)['course_id']==course
            assert r.docs.files.path(r.docs.files.get(doc['attachment_id'])).read_bytes()==original
            assert not r.provider.calls
            await learning.close()
    asyncio.run(run())


def test_a_new_date_does_not_silently_reuse_the_previous_explicit_lecture(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            learning=LearningService(r.store,r.service,r.extract);learning.analysis_status=lambda:{'enabled':False}
            with r.store.connection() as c:sid=c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=?',(p.raw_id,)).fetchone()[0]
            capture=learning.store.record_capture(p.raw_id,sid,'Original source.',{'note_share':True,'course_id':'course-math-ii'})
            await learning.confirm_capture(capture['id'],'course-math-ii',lecture_id=p.lecture['id'])
            result=await learning.confirm_capture(capture['id'],'course-math-ii',lecture_date='2026-10-28')
            assert result['context']['confirmed_note_date']=='2026-10-28'
            assert not result['context'].get('confirmed_note_lecture_id')
            assert result['context']['note_automation']['status']=='needs_context'
            assert '明确选择目标课次' in result['context']['note_automation']['message']
            assert r.service.store.lecture(p.lecture['id'])['lecture_date']=='2026-10-26'
            with r.store.connection() as c:assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==1
            assert not r.provider.calls
            await learning.close()
    asyncio.run(run())
