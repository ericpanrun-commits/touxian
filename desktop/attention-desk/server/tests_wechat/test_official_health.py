"""Cloud source observation uses an authenticated GET and cannot stop local alarms."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from server.tests_wechat.test_collection_health import configured
from server.wechat_collect.health import CollectionHealthMonitor

NOW = datetime.fromisoformat('2026-11-04T12:00:00+08:00')


def environment(personal, request):
    document, store = configured(personal)
    cloud = SimpleNamespace(settings=SimpleNamespace(read=lambda: None), request=request)
    wechat = SimpleNamespace(personal=personal, document=document)
    official = SimpleNamespace(runtime=SimpleNamespace(automatic_sync_allowed=False))
    calls = []
    notifier = SimpleNamespace(send=lambda *a, **k: calls.append((a, k)) or 'os_accepted')
    at = [NOW]
    monitor = CollectionHealthMonitor(wechat, cloud, official, notifier=notifier, clock=lambda: at[0])
    return monitor, store, calls, at


def test_cloud_observation_is_get_only_and_includes_independent_source_alarm(personal):
    requests = []
    async def request(method, path):
        requests.append((method, path))
        return {'owner': 'cloud', 'state': 'observed', 'checked_at': NOW.isoformat(),
            'missing_original_count': 1,
            'sources': [{'source_id': 'official:school', 'name': 'School public notices',
                'collection_state': 'partial', 'consecutive_failures': 11, 'alert_required': True,
                'missing_original_count': 1, 'sync_state': 'original_unavailable',
                'original_text': 'NEVER_RETURN', 'secret': 'NEVER_RETURN'}]}
    monitor, _, calls, _ = environment(personal, request)
    asyncio.run(monitor._refresh_official())
    assert requests == [('GET', '/worker/official-health')]
    health = monitor.snapshot()
    assert health['official']['sources'][0]['consecutive_failures'] == 11
    assert health['official']['missing_original_count'] == 1
    assert health['official']['sources'][0]['missing_original_count'] == 1
    assert health['official']['sources'][0]['sync_state'] == 'original_unavailable'
    assert 'NEVER_RETURN' not in str(health)
    monitor.deliver(health)
    assert len(calls) == 1
    monitor.deliver(health)
    assert len(calls) == 1


def test_cloud_outage_and_bad_response_do_not_remove_local_failure_evidence(personal):
    async def unavailable(*args):
        raise OSError('synthetic cloud unavailable')
    monitor, store, calls, _ = environment(personal, unavailable)
    for _ in range(3):
        row, _ = store.begin_scan('unit', 1)
        store.record_group_scan('health', row['id'], 'error', error='database_read_error')
        asyncio.run(monitor._refresh_official())
    health = monitor.snapshot()
    assert health['sources'][0]['collection_state'] == 'failed'
    assert health['official']['state'] == 'observation_failed'
    assert health['official']['observer']['alert_required']
    monitor.deliver(health)
    assert len(calls) == 1  # One aggregate notice for both failures.


def test_stale_cloud_snapshot_is_unknown_and_alerts_about_observation(personal):
    async def request(*args):
        return {'owner': 'cloud', 'state': 'observed', 'checked_at': NOW.isoformat(),
            'sources': [{'source_id': 'official:school', 'collection_state': 'healthy_idle', 'alert_required': False}]}
    monitor, _, _, at = environment(personal, request)
    asyncio.run(monitor._refresh_official())
    at[0] += timedelta(minutes=16)
    value = monitor.snapshot()['official']
    assert value['state'] == 'stale_observation'
    assert value['sources'][0]['collection_state'] == 'unknown'
    assert value['sources'][0]['last_known_collection_state'] == 'healthy_idle'
    assert value['observer']['alert_required']


def test_malformed_cloud_source_is_not_installed_into_the_local_snapshot(personal):
    async def request(*args):
        return {'owner': 'cloud', 'state': 'observed', 'checked_at': NOW.isoformat(),
            'sources': [{'source_id': 'official:school', 'alert_required': True}]}
    monitor, _, _, _ = environment(personal, request)
    asyncio.run(monitor._refresh_official())
    health = monitor.snapshot()
    assert health['official']['state'] == 'observation_failed'
    assert health['official']['sources'] == []
    assert len(health['sources']) == 1


def test_overdue_cloud_collector_alert_is_deduplicated_and_stale_snapshot_is_unknown(personal):
    async def request(*args):
        return {'owner': 'cloud', 'state': 'observed', 'checked_at': NOW.isoformat(), 'sources': [],
            'collector': {'source_id': 'official:collector-scheduler', 'name': '官网定时采集',
                'state': 'overdue', 'alert_required': True, 'last_run_at': NOW.isoformat(),
                'secret': 'NEVER_RETURN'}}
    monitor, _, calls, at = environment(personal, request)
    asyncio.run(monitor._refresh_official())
    health = monitor.snapshot()
    assert health['official']['collector']['state'] == 'overdue'
    assert 'NEVER_RETURN' not in str(health)
    health['quiet'] = True
    monitor.deliver(health)
    assert calls == []
    health['quiet'] = False
    monitor.deliver(health)
    monitor.deliver(health)
    assert len(calls) == 1
    at[0] += timedelta(minutes=16)
    stale = monitor.snapshot()['official']
    assert stale['collector']['state'] == 'unknown'
    assert stale['collector']['last_known_state'] == 'overdue'
    assert stale['collector']['alert_required'] is False
    assert stale['observer']['alert_required'] is True


def test_invalid_collector_payload_keeps_last_observation_as_unknown(personal):
    payload = {'owner': 'cloud', 'state': 'observed', 'checked_at': NOW.isoformat(), 'sources': [],
        'collector': {'source_id': 'official:collector-scheduler', 'state': 'on_schedule', 'alert_required': False}}
    async def request(*args): return payload
    monitor, _, _, _ = environment(personal, request)
    asyncio.run(monitor._refresh_official())
    payload['collector'] = {'source_id': 'official:collector-scheduler', 'state': 'on_schedule', 'alert_required': True}
    asyncio.run(monitor._refresh_official())
    health = monitor.snapshot()['official']
    assert health['state'] == 'observation_failed'
    assert health['collector']['state'] == 'unknown'
    assert health['collector']['last_known_state'] == 'on_schedule'
    assert health['collector']['alert_required'] is False
