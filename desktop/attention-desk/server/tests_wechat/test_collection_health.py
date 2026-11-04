import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

from server.wechat_collect.config import ChatConfig, SettingsDocument, WeChatConfig
from server.wechat_collect.health import CollectionHealthMonitor, active_seconds, wechat_health
from server.wechat_collect.store import WeChatStore
from server.wechat_collect.models import Observation, ObservedMessage
from server.tests_wechat.test_pipeline import setup_service, scan


def configured(personal):
    doc = SettingsDocument(wechat=WeChatConfig(enabled=True, allowed_chats=['授权群'],
        priority_chats=[ChatConfig(id='health', name='授权群')]))
    store = WeChatStore(personal)
    store.configure(doc, enforce_allowlist=True)
    return doc, store


def test_empty_scan_is_success_and_overnight_is_not_failure(personal):
    doc, store = configured(personal)
    row, _ = store.begin_scan('unit', 1)
    # An empty read is recorded independently from notification arrival.
    store.record_group_scan('health', row['id'], 'no_change')
    stamp = datetime.now(timezone.utc).isoformat()
    with personal.connection(write=True) as c:
        c.execute('UPDATE wechat_cursor SET last_success_at=?', (stamp,))
    health = wechat_health(personal, doc, datetime.fromisoformat(stamp))
    item = health['sources'][0]
    assert item['collection_state'] == 'healthy_idle'
    assert item['last_notification_at'] is None and not item['alert_required']
    start = datetime.fromisoformat('2026-11-03T23:45:00+08:00')
    assert active_seconds(start, datetime.fromisoformat('2026-11-04T06:15:00+08:00')) == 1800


def test_three_pre_read_failures_have_independent_journal_and_alert(personal):
    doc, store = configured(personal)
    for _ in range(3):
        row, _ = store.begin_scan('unit', 1)
        store.record_group_scan('health', row['id'], 'error', error='wechat_version_changed_requires_verification')
        store.finish_scan(row['id'], 'error', {}, 'wechat_version_changed_requires_verification')
    health = wechat_health(personal, doc, datetime.fromisoformat('2026-11-04T12:00:00+08:00'))
    item = health['sources'][0]
    assert item['consecutive_failures'] == 3 and item['alert_required']
    assert item['latest_error'] == 'wechat_version_changed_requires_verification'
    assert item['failure_count_scope'] == 'per_source_journal'
    quiet = wechat_health(personal, doc, datetime.fromisoformat('2026-11-05T03:00:00+08:00'))
    assert quiet['sources'][0]['collection_state'] == 'quiet'
    assert not quiet['sources'][0]['alert_required']


def test_alert_claim_survives_restart_and_error_changes(personal):
    doc, _ = configured(personal)
    calls = []
    current = [datetime.fromisoformat('2026-11-04T12:00:00+08:00')]
    notifier = SimpleNamespace(send=lambda *a, **k: calls.append((a, k)) or 'os_accepted')
    service = SimpleNamespace(personal=personal, document=doc)
    health = {'quiet': False, 'sources': [{'source_id': 'wechat:health', 'alert_required': True}]}
    monitor = CollectionHealthMonitor(service, notifier=notifier, clock=lambda: current[0])
    monitor.deliver(health)
    current[0] += timedelta(minutes=16)
    restarted = CollectionHealthMonitor(service, notifier=notifier, clock=lambda: current[0])
    restarted.deliver(health)
    assert len(calls) == 1
    current[0] += timedelta(hours=6)
    restarted.deliver(health)
    assert len(calls) == 2


def test_unknown_delivery_is_claimed_without_resending(personal):
    doc, _ = configured(personal)
    calls = []
    def unknown(*args, **kwargs):
        calls.append(1)
        raise OSError('synthetic uncertain native delivery')
    service = SimpleNamespace(personal=personal, document=doc)
    monitor = CollectionHealthMonitor(service, notifier=SimpleNamespace(send=unknown),
        clock=lambda: datetime.fromisoformat('2026-11-04T12:00:00+08:00'))
    health = {'quiet': False, 'sources': [{'source_id': 'wechat:health', 'alert_required': True}]}
    monitor.deliver(health)
    monitor.deliver(health)
    assert calls == [1]
    assert json.loads(monitor.ledger.read_text())['sources']['wechat:health']['result'] == 'delivery_unknown'


def test_historical_pending_dispatch_cannot_spend(personal):
    service, current, calls = setup_service(personal, [Observation('test','unverified',
        (ObservedMessage('请按要求提交完整实验报告与登记材料'),), True, True)])
    asyncio.run(scan(service))
    with personal.connection() as c:
        batch = c.execute('SELECT id FROM wechat_batch').fetchone()[0]
    job = service.analysis.enqueue(batch, service.context.read())
    with personal.connection(write=True) as c:
        c.execute('UPDATE wechat_analysis_job SET created_at=? WHERE id=?',
                  ((current[0] - timedelta(days=3)).isoformat(), job))
    assert asyncio.run(service.analysis.run_one(job)) is False
    assert not calls
    with personal.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM wechat_llm_attempt').fetchone()[0] == 0
        assert c.execute('SELECT status FROM wechat_analysis_job WHERE id=?', (job,)).fetchone()[0] == 'pending'


def test_certificate_version_string_alone_is_rejected(tmp_path):
    from server.wechat_collect.db_adapter import ReadOnlyWeChatDBAdapter, COMMIT
    python = tmp_path / 'python.exe'
    python.write_bytes(b'unit-only')
    path = tmp_path / 'report.json'
    path.write_text(json.dumps({'DB_READ': 'PASS', 'commit': COMMIT, 'count': 20,
        'sender_prefix_mismatches': 0, 'unauthorized_message_bodies_read': 0,
        'wechat_version': '4.2.0.9'}))
    assert not ReadOnlyWeChatDBAdapter(python=python, report_path=path).certified


def test_fresh_job_from_outage_catch_up_requires_separate_approval(personal):
    service, current, calls = setup_service(personal, [Observation('test','unverified',
        (ObservedMessage('请按要求提交完整实验报告与登记材料'),), True, True)])
    asyncio.run(scan(service))
    with personal.connection(write=True) as c:
        batch = c.execute('SELECT id,scan_id FROM wechat_batch').fetchone()
        c.execute('UPDATE wechat_scan SET stats_json=? WHERE id=?',
                  (json.dumps({'recovery_requires_approval': 1}), batch['scan_id']))
    job = service.analysis.enqueue(batch['id'], service.context.read())
    assert asyncio.run(service.analysis.run_one(job)) is False and not calls
    with personal.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM wechat_llm_attempt').fetchone()[0] == 0


def test_sync_ack_hash_is_distinct_from_frontend_visibility(personal, monkeypatch):
    from server.cloud_sync.projection import CloudProjection
    doc, _ = configured(personal)
    with personal.connection(write=True) as c:
        c.execute("INSERT INTO raw_item(id,source_id,kind,submission_hash,created_at) VALUES ('raw-health','wechat:health','wechat_batch','unit-only','2026-11-04T00:00:00+00:00')")
    record = {'kind': 'raw_item', 'id': 'raw-health', 'source_hash': 'ack-hash'}
    monkeypatch.setattr(CloudProjection, 'records', lambda self: [record])
    cloud = SimpleNamespace(settings=SimpleNamespace(read=lambda: {'enabled': True}),
        hashes={}, state={'connected': True, 'last_error': None})
    monitor = CollectionHealthMonitor(SimpleNamespace(personal=personal, document=doc), cloud_sync=cloud)
    assert monitor.snapshot()['sync']['pending_records'] == 1
    cloud.hashes[('raw_item','raw-health')] = 'ack-hash'
    result = monitor.snapshot()
    assert result['sync']['state'] == 'synchronized' and result['sync']['pending_records'] == 0
    assert result['frontend_visibility'] == 'unverified'


def test_sync_observation_failure_cannot_silence_collection_health(personal):
    doc, store = configured(personal)
    for _ in range(3):
        row, _ = store.begin_scan('unit', 1)
        store.record_group_scan('health', row['id'], 'error', error='database_read_error')
    def unavailable():
        raise OSError('synthetic credentials or cloud unavailable')
    worker = SimpleNamespace(settings=SimpleNamespace(read=unavailable))
    monitor = CollectionHealthMonitor(SimpleNamespace(personal=personal, document=doc), cloud_sync=worker,
        clock=lambda: datetime.fromisoformat('2026-11-04T12:00:00+08:00'))
    result = monitor.snapshot()
    assert result['sources'][0]['alert_required']
    assert result['sync']['state'] == 'unknown'
    assert result['sync']['latest_error'] == 'sync_observation_failed'
