"""The existing Inbox must describe WeChat work without inventing completion."""
import asyncio
import json

import pytest

from server.extraction_service import ExtractionService
from server.inbox_store import InboxStore
from server.tests_wechat.test_pipeline import setup_service, scan
from server.wechat_collect.models import Observation, ObservedMessage
from server.wechat_collect.store import WeChatStore


@pytest.fixture
def captured(personal):
    notice = ObservedMessage('请于本周提交课程实验报告。', sender='老师')
    service, _, calls = setup_service(personal, [Observation('test', 'unverified', (notice,), True, True)])
    asyncio.run(scan(service))
    with personal.connection() as c:
        batch = dict(c.execute('SELECT * FROM wechat_batch').fetchone())
    return service, calls, batch


def enqueue(personal, captured):
    service, _, batch = captured
    job_id = service.analysis.enqueue(batch['id'], service.context.read())
    with personal.connection() as c:
        job = dict(c.execute('SELECT * FROM wechat_analysis_job WHERE id=?', (job_id,)).fetchone())
    return job


def test_captured_unanalysed_text_is_received_and_keeps_original(personal, captured):
    _, calls, batch = captured
    inbox = InboxStore(personal)
    item = inbox.detail(batch['raw_item_id'])
    assert (item['processing_status'], item['processing_stage']) == ('received', 'analysis_pending')
    assert item['original']['original_text'] == '请于本周提交课程实验报告。'
    assert batch['raw_item_id'] in {row['id'] for row in inbox.list('processing')['items']}
    assert batch['raw_item_id'] not in {row['id'] for row in inbox.list('completed')['items']}
    assert calls == []


@pytest.mark.parametrize(('job_state', 'code', 'expected', 'stage'), [
    ('pending', 'interrupted', 'received', 'analysis_pending'),
    ('retry', 'rate_limit', 'received', 'analysis_pending'),
    ('budget_deferred', 'budget_deferred', 'received', 'analysis_budget_deferred'),
    ('error', 'invalid_evidence', 'failed', 'analysis'),
    ('running', 'interrupted', 'analyzing', 'analysis'),
])
def test_queue_state_distinguishes_waiting_budget_failure_and_running(personal, captured, job_state, code, expected, stage):
    _, calls, batch = captured
    job = enqueue(personal, captured)
    with personal.connection(write=True) as c:
        c.execute('UPDATE wechat_analysis_job SET status=?,error_code=? WHERE id=?', (job_state, code, job['id']))
        c.execute("UPDATE ai_extraction SET status='failed',error_json=? WHERE id=?", (json.dumps({'code': code, 'message': 'fixture'}), job['extraction_id']))
    item = InboxStore(personal).detail(batch['raw_item_id'])
    assert (item['processing_status'], item['processing_stage']) == (expected, stage)
    if job_state == 'pending':
        assert item['error'] is None  # A generic restart falsely marked an unstarted job.
    if job_state == 'budget_deferred':
        assert item['error']['code'] == 'budget_deferred' and item['error']['retryable'] is False
    assert calls == []


def test_unknown_outcome_is_not_relabelled_as_an_ordinary_pending_job(personal, captured):
    _, calls, batch = captured
    job = enqueue(personal, captured)
    with personal.connection(write=True) as c:
        c.execute("INSERT INTO wechat_llm_attempt(id,job_id,attempt_number,kind,started_at,status,reserved_tokens,outcome_unknown) VALUES ('attempt',?,1,'text','2026-10-22T00:00:00+00:00','outcome_unknown',100,1)", (job['id'],))
    item = InboxStore(personal).detail(batch['raw_item_id'])
    assert (item['processing_status'], item['processing_stage']) == ('recovery_needed', 'analysis_result_unknown')
    assert item['error']['code'] == 'outcome_unknown' and item['error']['retryable'] is False
    assert calls == []


def test_pending_analysis_preserves_attachment_review_and_confirmed_review(personal, captured):
    _, calls, batch = captured
    with personal.connection(write=True) as c:
        c.execute("INSERT INTO attachment(id,raw_item_id,origin_snapshot_id,filename,parse_status,needs_review,created_at) VALUES ('fixture-file',?,?, 'fixture.pdf','downloaded',1,'2026-10-22T00:00:00+00:00')", (batch['raw_item_id'], batch['snapshot_id']))
        c.execute('INSERT INTO snapshot_attachment(raw_snapshot_id,attachment_id) VALUES (?,?)', (batch['snapshot_id'], 'fixture-file'))
    item = InboxStore(personal).detail(batch['raw_item_id'])
    assert (item['processing_status'], item['processing_stage']) == ('needs_review', 'analysis_pending')
    with personal.connection(write=True) as c:
        c.execute("UPDATE raw_review SET status='confirmed' WHERE raw_snapshot_id=?", (batch['snapshot_id'],))
    item = InboxStore(personal).detail(batch['raw_item_id'])
    assert (item['processing_status'], item['processing_stage']) == ('completed', 'confirmed')
    assert calls == []


def test_generic_restart_preserves_only_unsent_wechat_waiting_work(personal, captured):
    service, calls, _ = captured
    unsent = enqueue(personal, captured)
    running_id = service.analysis.enqueue(unsent['batch_id'], service.context.read(), force=True)
    with personal.connection(write=True) as c:
        running = dict(c.execute('SELECT * FROM wechat_analysis_job WHERE id=?', (running_id,)).fetchone())
        c.execute("UPDATE wechat_analysis_job SET status='running' WHERE id=?", (running_id,))
        c.execute("UPDATE ai_extraction SET status='running' WHERE id=?", (running['extraction_id'],))
        c.execute("INSERT INTO wechat_llm_attempt(id,job_id,attempt_number,kind,started_at,status,reserved_tokens) VALUES ('running-attempt',?,1,'text','2026-10-22T00:00:00+00:00','running',100)", (running_id,))
    ExtractionService(personal, service.analysis.settings).recover_interrupted()
    WeChatStore(personal).recover()
    with personal.connection() as c:
        states = {r['id']: r['status'] for r in c.execute('SELECT id,status FROM ai_extraction')}
        attempt = c.execute("SELECT status,outcome_unknown FROM wechat_llm_attempt WHERE id='running-attempt'").fetchone()
        running_state = c.execute('SELECT status FROM wechat_analysis_job WHERE id=?', (running_id,)).fetchone()[0]
    assert states[unsent['extraction_id']] == 'waiting'
    assert states[running['extraction_id']] == 'failed'
    assert running_state == 'outcome_unknown' and tuple(attempt) == ('outcome_unknown', 1)
    assert calls == []
