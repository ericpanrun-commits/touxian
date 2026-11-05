"""Regression coverage for preserving unqueued pending originals across startup."""
import asyncio
import json

from server.tests_wechat.test_pipeline import setup_service, scan
from server.wechat_collect.models import Observation, ObservedMessage
from server.wechat_collect.quality import Attention, persist, reclassify_saved
from server.wechat_collect.service import WeChatService


def capture_pending(personal):
    message = ObservedMessage('请于周五前提交完整实验报告并查看老师的安排。', sender='合成教师')
    service, _, calls = setup_service(personal, [Observation('test', 'unverified', (message,), True, True)])
    asyncio.run(scan(service))
    with personal.connection() as connection:
        row = connection.execute("SELECT * FROM wechat_candidate WHERE model_item_id='_pending'").fetchone()
        batch = connection.execute('SELECT * FROM wechat_batch WHERE id=?', (row['batch_id'],)).fetchone()
        assert connection.execute('SELECT count(*) FROM wechat_analysis_job WHERE batch_id=?', (row['batch_id'],)).fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM wechat_attention_state WHERE candidate_id=?', (row['id'],)).fetchone()[0] == 0
        assert json.loads(row['data_json'])['analysis_origin'] == 'local_pending'
        return service, calls, dict(row), dict(batch)


def rows(personal, table, column, value):
    with personal.connection() as connection:
        return [dict(row) for row in connection.execute(f'SELECT * FROM {table} WHERE {column}=?', (value,))]


def test_unqueued_pending_and_review_are_unchanged_across_repeated_startup_upgrade(personal):
    service, calls, candidate, batch = capture_pending(personal)
    before_candidate = rows(personal, 'wechat_candidate', 'id', candidate['id'])
    before_review = rows(personal, 'raw_review', 'raw_snapshot_id', batch['snapshot_id'])
    before_snapshot = rows(personal, 'raw_snapshot', 'id', batch['snapshot_id'])
    for _ in range(2):
        assert reclassify_saved(personal, service.context.read().model_dump()) == []
        assert rows(personal, 'wechat_candidate', 'id', candidate['id']) == before_candidate
        assert rows(personal, 'raw_review', 'raw_snapshot_id', batch['snapshot_id']) == before_review
        assert rows(personal, 'raw_snapshot', 'id', batch['snapshot_id']) == before_snapshot
        assert rows(personal, 'wechat_attention_state', 'candidate_id', candidate['id']) == []
    assert calls == []


def test_queued_pending_still_uses_the_existing_quality_flow(personal):
    service, calls, candidate, batch = capture_pending(personal)
    service.analysis.enqueue(batch['id'], service.context.read())
    decisions = reclassify_saved(personal, service.context.read().model_dump())
    assert [item['id'] for item in decisions] == [candidate['id']]
    assert len(rows(personal, 'wechat_attention_state', 'candidate_id', candidate['id'])) == 1
    assert calls == []


def test_existing_classification_still_revisits_a_changed_policy(personal):
    service, calls, candidate, batch = capture_pending(personal)
    service.analysis.enqueue(batch['id'], service.context.read())
    assert reclassify_saved(personal, service.context.read().model_dump())
    with personal.connection(write=True) as connection:
        connection.execute("UPDATE wechat_attention_state SET policy_version='older-fixture-policy' WHERE candidate_id=?", (candidate['id'],))
    decisions = reclassify_saved(personal, service.context.read().model_dump())
    assert [item['id'] for item in decisions] == [candidate['id']]
    assert len(rows(personal, 'wechat_attention_state', 'candidate_id', candidate['id'])) == 1
    assert calls == []


def test_previously_classified_pending_without_a_job_is_still_reclassified(personal):
    service, calls, candidate, _ = capture_pending(personal)
    original = json.loads(candidate['data_json'])
    with personal.connection(write=True) as connection:
        persist(connection, candidate['id'], original,
                Attention('unresolved', 'ignore', False, True, False, None, 'fixture'),
                original=original, previous_status='unresolved', fingerprint='old-policy')
    decisions = reclassify_saved(personal, service.context.read().model_dump())
    assert [item['id'] for item in decisions] == [candidate['id']]
    assert len(rows(personal, 'wechat_attention_state', 'candidate_id', candidate['id'])) == 1
    assert calls == []


def test_existing_extraction_without_a_job_is_not_mistaken_for_a_placeholder(personal):
    service, calls, candidate, batch = capture_pending(personal)
    job = service.analysis.enqueue(batch['id'], service.context.read())
    with personal.connection(write=True) as connection:
        connection.execute('DELETE FROM wechat_analysis_job WHERE id=?', (job,))
        assert connection.execute('SELECT extraction_id FROM raw_review WHERE raw_snapshot_id=?', (batch['snapshot_id'],)).fetchone()[0]
    decisions = reclassify_saved(personal, service.context.read().model_dump())
    assert [item['id'] for item in decisions] == [candidate['id']]
    assert calls == []


def test_actual_service_initialization_preserves_unqueued_pending(personal):
    service, calls, candidate, batch = capture_pending(personal)
    before_candidate = rows(personal, 'wechat_candidate', 'id', candidate['id'])
    before_review = rows(personal, 'raw_review', 'raw_snapshot_id', batch['snapshot_id'])
    WeChatService(personal, service.analysis.settings, adapter=service.adapter,
                  clock=service.clock, transport=service.analysis.transport)
    assert rows(personal, 'wechat_candidate', 'id', candidate['id']) == before_candidate
    assert rows(personal, 'raw_review', 'raw_snapshot_id', batch['snapshot_id']) == before_review
    assert rows(personal, 'wechat_attention_state', 'candidate_id', candidate['id']) == []
    assert calls == []
