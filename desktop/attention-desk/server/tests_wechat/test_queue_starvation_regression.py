"""Synthetic regressions for batch identity, job dedupe and worker starvation."""
import asyncio
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest

from server.personal_store import encode
from server.review_store import ReviewStore
from server.tests_wechat.test_deepseek_content_gate import grant, write_grant
from server.tests_wechat.test_pipeline import setup_service
from server.user_context import UserContextStore
from server.wechat_collect.analysis import PROMPT_VERSION
from server.wechat_collect.batching import BatchPlan
from server.wechat_collect.deepseek_worker import CONTROLS, TextAnalysisWorker
from server.wechat_collect.ingestion import prepare_batch
from server.wechat_collect.models import Observation, ObservedMessage


@pytest.fixture
def rig(personal, monkeypatch):
    service, clock, calls = setup_service(personal, [], time='2026-11-12T12:00:00+08:00')
    service.synthetic_content_grant = False
    service.document.wechat.allowed_chats = ['合成测试群']
    service.document.wechat.context_followup.enabled = False
    asyncio.run(service.save_settings(service.document, service.document.revision))
    write_grant(personal, grant(not_before=(clock[0] - timedelta(days=4)).isoformat()))
    directory = personal.path.parent
    (directory / 'recovery-controls.json').write_text(encode({
        'version': 1, 'hold_cloud_uploads': True, 'pause_paid_ai': True}), encoding='utf8')
    (directory / CONTROLS).write_text(encode({
        'version': 1, 'hold_cloud_uploads': True, 'pause_paid_ai': False}), encoding='utf8')
    monkeypatch.setenv('ATTENTION_RECOVERY_CONTROL_PATH', str((directory / CONTROLS).resolve()))
    worker = TextAnalysisWorker(directory, clock=service.clock, settings=service.analysis.settings,
                                transport=service.analysis.transport, unlimited_test_tokens=True)
    observed = []

    def add(text, *, kind='text', attachment='', context=(), captured_at=None, created_at=None):
        stamp = captured_at or clock[0] - timedelta(minutes=10) + timedelta(seconds=len(observed))
        observed.append(ObservedMessage(text, source_time=stamp.isoformat(),
            time_reliability='reliable', message_type=kind, attachment_identity=attachment))
        scan, _ = service.repository.begin_scan('synthetic-regression', service.document.revision)
        chat = service.repository.chat('test')
        with monkeypatch.context() as capture_clock:
            capture_clock.setattr('server.wechat_collect.store.now', lambda: stamp.isoformat())
            service.repository.capture(scan['id'], chat,
                Observation('test', 'unverified', tuple(observed), True, True))
        service.repository.finish_scan(scan['id'], 'changed', {})
        with personal.connection() as c:
            message_id = c.execute('SELECT id FROM wechat_message ORDER BY ingest_seq DESC LIMIT 1').fetchone()[0]
        batch_id = prepare_batch(personal, chat, scan['id'],
            BatchPlan((message_id,), tuple(context), False), at=clock[0])
        with personal.connection(write=True) as c:
            c.execute('UPDATE wechat_batch SET created_at=? WHERE id=?',
                      ((created_at or stamp).isoformat(), batch_id))
            batch = dict(c.execute('SELECT * FROM wechat_batch WHERE id=?', (batch_id,)).fetchone())
        return {**batch, 'message_id': message_id}

    return SimpleNamespace(personal=personal, service=service, worker=worker,
                           clock=clock, calls=calls, add=add)


def enqueue(rig, batch):
    return rig.worker.queue.enqueue(batch['id'], UserContextStore(rig.personal.path.parent).read())


def row(personal, table, item_id):
    with personal.connection() as c:
        return dict(c.execute('SELECT * FROM ' + table + ' WHERE id=?', (item_id,)).fetchone())


def preserve(personal):
    with personal.connection() as c:
        return {name: [tuple(r) for r in c.execute('SELECT * FROM ' + name + ' ORDER BY id')]
                for name in ('wechat_message', 'raw_snapshot', 'event', 'task', 'user_decision')}


def seed_existing_event(rig, batch):
    stamp = rig.clock[0].isoformat()
    with rig.personal.connection(write=True) as c:
        c.execute('INSERT INTO event(id,raw_item_id,raw_snapshot_id,title,status,created_at,updated_at) '
            'VALUES (?,?,?,?,?,?,?)', ('saved-event', batch['raw_item_id'], batch['snapshot_id'],
                                     '保留的既有事项', 'needs_action', stamp, stamp))
        c.execute('INSERT INTO task(id,event_id,title,sort_order,origin,created_at,updated_at) '
            'VALUES (?,?,?,?,?,?,?)', ('saved-task', 'saved-event', '保留既有任务', 0, 'manual', stamp, stamp))
        c.execute('INSERT INTO user_decision(id,event_id,action,entity_id,after_json,created_at) '
            'VALUES (?,?,?,?,?,?)', ('saved-decision', 'saved-event', 'synthetic-history',
                                   'saved-task', '{}', stamp))


@pytest.mark.parametrize('hash_field', ['evidence_sha256', 'encrypted_evidence_sha256'])
def test_batch_fingerprint_is_not_replaced_by_shared_attachment(rig, hash_field):
    path = rig.personal.path.parent / 'files' / 'shared.bin'
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b'synthetic attachment only')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    reference = encode({'kind': 'file', 'title': '合成附件.bin', 'evidence_path': str(path), hash_field: digest})
    media = rig.add('合成附件.bin', kind='file', attachment=reference)
    first = rig.add('请全体同学提交合成实验报告甲。', context=(media['message_id'],))
    second = rig.add('请全体同学提交合成实验报告乙。', context=(media['message_id'],))
    saved = preserve(rig.personal)
    for batch in (first, second):
        with rig.personal.connection() as c:
            original = c.execute('SELECT submission_hash FROM raw_item WHERE id=?', (batch['raw_item_id'],)).fetchone()[0]
            attachment = c.execute('SELECT sha256 FROM attachment WHERE raw_item_id=?', (batch['raw_item_id'],)).fetchone()[0]
        assert batch['input_hash'] == original
        assert attachment == digest and batch['input_hash'] != attachment
    assert first['input_hash'] != second['input_hash']
    assert path.read_bytes() == b'synthetic attachment only'
    assert preserve(rig.personal) == saved


@pytest.mark.parametrize('legacy_key', [False, True])
def test_different_batches_cannot_reuse_completed_job_even_with_colliding_hash(rig, legacy_key):
    first = rig.add('请全体同学提交合成实验报告甲。')
    old_job = enqueue(rig, first)
    second = rig.add('请全体同学提交合成实验报告乙。')
    with rig.personal.connection(write=True) as c:
        c.execute("UPDATE wechat_analysis_job SET status='parsed' WHERE id=?", (old_job,))
        c.execute('UPDATE wechat_batch SET input_hash=? WHERE id=?', (first['input_hash'], second['id']))
        if legacy_key:
            job = dict(c.execute('SELECT * FROM wechat_analysis_job WHERE id=?', (old_job,)).fetchone())
            context = rig.service.context.read()
            key = hashlib.sha256(encode([first['input_hash'], context.fingerprint(),
                json.loads(job['config_json']), PROMPT_VERSION, 'wechat.v1', None]).encode()).hexdigest()
            c.execute('UPDATE wechat_analysis_job SET input_key=? WHERE id=?', (key, old_job))
    old_record = row(rig.personal, 'wechat_analysis_job', old_job)
    new_job = enqueue(rig, second)
    assert new_job != old_job
    assert row(rig.personal, 'wechat_analysis_job', new_job)['batch_id'] == second['id']
    assert row(rig.personal, 'wechat_analysis_job', new_job)['snapshot_id'] == second['snapshot_id']
    assert enqueue(rig, second) == new_job
    assert row(rig.personal, 'wechat_analysis_job', old_job) == old_record
    assert rig.calls == []


@pytest.mark.parametrize('status', ['pending', 'parsed', 'error', 'outcome_unknown'])
@pytest.mark.parametrize('legacy_key', [False, True])
def test_same_batch_and_snapshot_keep_job_deduplication(rig, status, legacy_key):
    batch = rig.add('请全体同学提交合成实验报告。')
    job_id = enqueue(rig, batch)
    with rig.personal.connection(write=True) as c:
        c.execute('UPDATE wechat_analysis_job SET status=? WHERE id=?', (status, job_id))
        if legacy_key:
            job = dict(c.execute('SELECT * FROM wechat_analysis_job WHERE id=?', (job_id,)).fetchone())
            key = hashlib.sha256(encode([batch['input_hash'], rig.service.context.read().fingerprint(),
                json.loads(job['config_json']), PROMPT_VERSION, 'wechat.v1', None]).encode()).hexdigest()
            c.execute('UPDATE wechat_analysis_job SET input_key=? WHERE id=?', (key, job_id))
        before = c.execute('SELECT count(*) FROM ai_extraction').fetchone()[0]
    saved = row(rig.personal, 'wechat_analysis_job', job_id)
    assert enqueue(rig, batch) == job_id
    assert row(rig.personal, 'wechat_analysis_job', job_id) == saved
    with rig.personal.connection() as c:
        assert c.execute('SELECT count(*) FROM ai_extraction').fetchone()[0] == before
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0] == 1
    assert rig.calls == []


def test_completed_job_from_old_snapshot_is_not_current_snapshot_result(rig):
    batch = rig.add('请全体同学提交合成实验报告。')
    job_id = enqueue(rig, batch)
    with rig.personal.connection(write=True) as c:
        c.execute("UPDATE wechat_analysis_job SET status='parsed' WHERE id=?", (job_id,))
        current = ReviewStore._new_snapshot(c, batch['raw_item_id'], '合成通知补充正文',
                                          rig.clock[0].isoformat(), None, '合成补充')
        c.execute('UPDATE wechat_batch SET snapshot_id=? WHERE id=?', (current, batch['id']))
    saved = row(rig.personal, 'wechat_analysis_job', job_id)
    new_job = enqueue(rig, batch)
    assert new_job != job_id
    assert row(rig.personal, 'wechat_analysis_job', new_job)['snapshot_id'] == current
    assert enqueue(rig, batch) == new_job
    assert row(rig.personal, 'wechat_analysis_job', job_id) == saved
    assert rig.calls == []


@pytest.mark.parametrize('hold', ['historical_source', 'historical_job', 'catch_up',
                                 'future_retry', 'inactive_review', 'outcome_unknown'])
def test_four_unrunnable_old_jobs_leave_four_slots_for_fresh_notices(rig, hold):
    old_jobs = []
    for index in range(4):
        old_time = rig.clock[0] - timedelta(days=2) + timedelta(seconds=index)
        batch = rig.add('请全体同学提交旧合成报告' + '甲乙丙丁'[index] + '。',
            captured_at=old_time if hold == 'historical_source' else None, created_at=old_time)
        job_id = enqueue(rig, batch)
        with rig.personal.connection(write=True) as c:
            if hold == 'historical_job':
                c.execute('UPDATE wechat_analysis_job SET created_at=? WHERE id=?', (old_time.isoformat(), job_id))
            elif hold == 'catch_up':
                c.execute('UPDATE wechat_scan SET stats_json=? WHERE id=?',
                          (encode({'recovery_requires_approval': 1}), batch['scan_id']))
            elif hold == 'future_retry':
                c.execute("UPDATE wechat_analysis_job SET status='retry',next_attempt_at=? WHERE id=?",
                          ((rig.clock[0] + timedelta(hours=1)).isoformat(), job_id))
            elif hold == 'inactive_review':
                c.execute("UPDATE raw_review SET status='cancelled' WHERE raw_snapshot_id=?", (batch['snapshot_id'],))
            elif hold == 'outcome_unknown':
                c.execute("UPDATE wechat_analysis_job SET status='outcome_unknown' WHERE id=?", (job_id,))
        old_jobs.append(job_id)
    seed_existing_event(rig, batch)
    fresh = [rig.add('请全体同学提交新合成报告' + label + '。') for label in '甲乙丙丁戊']
    saved = preserve(rig.personal)
    old_rows = [row(rig.personal, 'wechat_analysis_job', job_id) for job_id in old_jobs]
    controls = {name: (rig.personal.path.parent / name).read_bytes()
                for name in ('recovery-controls.json', CONTROLS)}
    result = asyncio.run(rig.worker.cycle())
    assert result['calls'] == result['parsed'] == result['enqueued'] == 4
    assert len(rig.calls) == 4
    with rig.personal.connection() as c:
        for job_id in old_jobs:
            assert c.execute('SELECT count(*) FROM wechat_llm_attempt WHERE job_id=?', (job_id,)).fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM wechat_analysis_job WHERE batch_id=?', (fresh[-1]['id'],)).fetchone()[0] == 0
    assert [row(rig.personal, 'wechat_analysis_job', j) for j in old_jobs] == old_rows
    assert preserve(rig.personal) == saved
    assert all((rig.personal.path.parent / name).read_bytes() == value for name, value in controls.items())
    assert asyncio.run(rig.worker.cycle())['calls'] == 1
    assert asyncio.run(rig.worker.cycle())['calls'] == 0 and len(rig.calls) == 5


def test_unstarted_historical_batches_remain_unqueued_while_new_notice_runs(rig):
    old = [rig.add('请全体同学提交历史合成报告' + label + '。',
        captured_at=rig.clock[0] - timedelta(days=2) + timedelta(seconds=index))
        for index, label in enumerate('甲乙丙丁')]
    rig.add('请全体同学提交新合成报告。')
    saved = preserve(rig.personal)
    result = asyncio.run(rig.worker.cycle())
    assert result['enqueued'] == result['calls'] == result['parsed'] == 1
    with rig.personal.connection() as c:
        for batch in old:
            assert c.execute('SELECT count(*) FROM wechat_analysis_job WHERE batch_id=?', (batch['id'],)).fetchone()[0] == 0
    assert preserve(rig.personal) == saved


def test_no_dispatch_race_does_not_use_four_processing_slots(rig, monkeypatch):
    skipped = [rig.add('请全体同学提交暂缓合成报告' + label + '。') for label in '甲乙丙丁']
    skipped_ids = {b['id'] for b in skipped}
    for label in '甲乙丙丁戊':
        rig.add('请全体同学提交新合成报告' + label + '。')
    original = rig.worker.queue.run_one

    async def race(job_id):
        if row(rig.personal, 'wechat_analysis_job', job_id)['batch_id'] in skipped_ids:
            return False
        return await original(job_id)

    monkeypatch.setattr(rig.worker.queue, 'run_one', race)
    result = asyncio.run(rig.worker.cycle())
    assert result['calls'] == result['parsed'] == len(rig.calls) == 4
    with rig.personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0] == 4


def test_foreign_enqueue_result_is_skipped_without_false_enqueued_or_dispatch(rig, monkeypatch):
    donor = rig.add('请全体同学提交已处理的合成报告。')
    donor_job = enqueue(rig, donor)
    with rig.personal.connection(write=True) as c:
        c.execute("UPDATE wechat_analysis_job SET status='parsed' WHERE id=?", (donor_job,))
    skipped = [rig.add('请全体同学提交需核对的合成报告' + label + '。') for label in '甲乙丙丁']
    skipped_ids = {b['id'] for b in skipped}
    for label in '甲乙丙丁戊':
        rig.add('请全体同学提交新合成报告' + label + '。')
    original = rig.worker.queue.enqueue

    def legacy_result(batch_id, context, **kwargs):
        return donor_job if batch_id in skipped_ids else original(batch_id, context, **kwargs)

    monkeypatch.setattr(rig.worker.queue, 'enqueue', legacy_result)
    result = asyncio.run(rig.worker.cycle())
    assert result['calls'] == result['parsed'] == result['enqueued'] == len(rig.calls) == 4
    with rig.personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_llm_attempt WHERE job_id=?', (donor_job,)).fetchone()[0] == 0
        for batch in skipped:
            assert c.execute('SELECT count(*) FROM wechat_analysis_job WHERE batch_id=?', (batch['id'],)).fetchone()[0] == 0
