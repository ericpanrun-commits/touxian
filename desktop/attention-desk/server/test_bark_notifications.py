"""Phase 7.1 isolated HTTP, outbox, policy, restart, concurrency and 500-row safety."""
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.notifications.api import router
from server.notifications.bark import BarkClient
from server.notifications.config import BarkConfig
from server.notifications.policy import NotificationPolicyConfig, click_url, evaluate, presentation, quiet_due
from server.notifications.service import NotificationDispatcher
from server.notifications.store import NotificationStore
from server.official_store import OfficialStore
from server.personal_store import PersonalStore
from server.planning.time import stamp, utc_now
from server.runtime import RuntimeConfig
from server.test_official_information import source, seed, document, listing, monitor, scan, TODAY


@pytest.fixture
def config():
    # Generated synthetic credentials; never copy a user's real key into test fixtures.
    return BarkConfig.from_env({'BARK_DEVICE_KEY': secrets.token_urlsafe(24),'BARK_ENABLED':'true'})


@pytest.fixture
def store(tmp_path):
    personal = PersonalStore(tmp_path / 'test.sqlite3')
    personal.initialize()
    OfficialStore(personal).configure([source(), source('unit-other')])
    return personal


def dispatcher(store, config, *, handler=None, clock=utc_now):
    calls = []
    def receive(request):
        calls.append(json.loads(request.content))
        return handler(request) if handler else httpx.Response(200, json={'code': 200})
    client = BarkClient(config, transport=httpx.MockTransport(receive), sleep=lambda _: None)
    runtime = RuntimeConfig(environment='production',role='cloud',auto_sync=True,notification_authority=True,storage_verified=True)
    service = NotificationDispatcher(store, config=config, client=client, clock=clock,runtime=runtime)
    policy = NotificationPolicyConfig(quiet_enabled=False)
    prefs = service.repository.preferences()
    service.repository.save_preferences(True, policy, prefs['version'])
    return service, calls


def item(store, saved):
    with store.connection() as c:
        return NotificationStore.candidate(c, saved['raw_item_id'])


def test_config_defaults_override_masking_and_disabled(config):
    assert config.enabled and config.configured
    assert not BarkConfig.from_env({}).enabled
    assert not BarkConfig.from_env({'BARK_DEVICE_KEY':config.device_key}).enabled
    assert not BarkConfig.from_env({'BARK_DEVICE_KEY': config.device_key, 'BARK_ENABLED': 'false'}).enabled
    assert not BarkConfig.from_env({'BARK_DEVICE_KEY': 'your_bark_device_key_here'}).configured
    for public in [repr(config), json.dumps(config.public())]:
        assert config.device_key not in public
    override = BarkConfig.from_env({'BARK_PUSH_URL': f'https://api.day.app/{config.device_key}/title/body?group=x'})
    assert override.configured and override.endpoint == 'https://api.day.app/push'
    assert override.device_key == config.device_key
    prefix = BarkConfig.from_env({'BARK_SERVER_URL': 'https://push.example.test/bark', 'BARK_PUSH_URL': f'https://push.example.test/bark/{config.device_key}'})
    assert prefix.endpoint == 'https://push.example.test/bark/push'


def test_backend_dotenv_and_process_environment_precedence(tmp_path, monkeypatch, config):
    import server.notifications.config as module
    root = tmp_path / 'app' / 'server'
    (root / 'notifications').mkdir(parents=True)
    monkeypatch.setattr(module, '__file__', str(root / 'notifications' / 'config.py'))
    for key in ['BARK_ENABLED', 'BARK_DEVICE_KEY', 'BARK_PUSH_URL', 'BARK_SERVER_URL']:
        monkeypatch.delenv(key, raising=False)
    (root / '.env').write_text('BARK_ENABLED=true\nBARK_DEVICE_KEY='+config.device_key, encoding='utf-8')
    assert BarkConfig.from_env().device_key == config.device_key
    monkeypatch.setenv('BARK_ENABLED', 'false')
    assert not BarkConfig.from_env().enabled


@pytest.mark.parametrize('env', [{'BARK_SERVER_URL': 'javascript:bad'}, {'BARK_PUSH_URL': 'https://user:pass@api.day.app/key'},
    {'BARK_DEVICE_KEY': 'has space'}, {'BARK_ENABLED': 'maybe'}, {'BARK_SERVER_URL': 'https://api.day.app:wrong'}])
def test_invalid_config_never_echoes_inputs(env):
    config = BarkConfig.from_env(env)
    assert config.error == 'invalid_config' and not config.configured


@pytest.mark.parametrize('status,attempts,error', [(200, 1, None), (400, 1, 'unauthorized'), (401, 1, 'unauthorized'),
    (403, 1, 'unauthorized'), (422, 1, 'rejected'), (302, 1, 'rejected'), (429, 3, 'rate_limited'), (500, 3, 'server_error')])
def test_http_bounded_retry_and_status(config, status, attempts, error):
    calls, delays = [], []
    def handler(request):
        calls.append(request)
        assert str(request.url) == 'https://api.day.app/push'
        assert request.headers['User-Agent'] == 'ZQU-Workspace/7.1'
        assert request.extensions['timeout']['connect'] == 3 and request.extensions['timeout']['read'] == 8
        return httpx.Response(status, json={'code': 200, 'message': config.device_key}, headers={'location': 'https://evil.test'})
    result = BarkClient(config, transport=httpx.MockTransport(handler), sleep=delays.append).send({'title': '中文 🟠', 'body': '正文'})
    assert len(calls) == attempts and result.error == error
    assert delays == ([1, 2] if attempts == 3 else [])
    assert config.device_key not in repr(result) and config.device_key not in json.dumps(result.public())


@pytest.mark.parametrize('exception,attempts,error', [(httpx.ConnectTimeout, 3, 'connect_timeout'),
    (httpx.ConnectError, 3, 'unreachable'), (httpx.ReadTimeout, 1, 'delivery_unknown'),
    (httpx.WriteTimeout, 1, 'delivery_unknown'), (httpx.ReadError, 1, 'delivery_unknown')])
def test_network_failures_safe_and_ambiguous_not_retried(config, exception, attempts, error, caplog):
    calls = []
    def handler(request):
        calls.append(1)
        raise exception(config.device_key, request=request)
    result = BarkClient(config, transport=httpx.MockTransport(handler), sleep=lambda _: None).send({'body': 'test'})
    assert len(calls) == attempts and result.error == error
    assert config.device_key not in caplog.text + repr(result)


@pytest.mark.parametrize('content', [b'invalid', b'[]', b'{"code": 500}', b'{}'])
def test_uncertain_provider_success_response_never_replayed(config, content):
    result = BarkClient(config, transport=httpx.MockTransport(lambda req: httpx.Response(200, content=content))).send({'body': 'test'})
    assert result.error == 'delivery_unknown' and not result.retryable


def test_policy_critical_high_direct_academic_and_suppression(store):
    saved = seed(store)
    candidate, sources = item(store, saved)
    at = utc_now(); activation = stamp(at - timedelta(minutes=1)); policy = NotificationPolicyConfig(quiet_enabled=False)
    def decide(**changes):
        return evaluate({**candidate, **changes}, sources, policy, activation_at=activation, at=at)
    assert decide(importance='high', relevance=90)[1]
    assert decide(importance='critical', relevance=1)[0] == 'critical'
    assert decide(importance='normal', relevance=98)[0] == 'vehicle_direct'
    assert decide(importance='normal', category='考试', relevance=95, reasons=['考试'])[0] == 'academic_core'
    assert decide(importance='normal', category='学院通知', relevance=95, reasons=['机械与汽车工程学院', '2026级'])[0] == 'college_grade'
    assert decide(relevance=20)[0] == 'low_relevance'
    assert decide(importance='low')[0] == 'low_importance'
    assert decide(official_backfill=1)[0] == 'historical'
    assert decide(discovered_at=activation)[0] == 'historical'
    assert decide(published_at='2000-01-01')[0] == 'historical'
    assert decide(published_at=None)[0] == 'unknown_publication'
    assert decide(deadline_at=stamp(at-timedelta(hours=1)))[0] == 'expired'
    assert decide(event_end_at='2000-01-01')[0] == 'expired'
    assert decide(canonical_url='https://evil.test/')[0] == 'untrusted_source'


@pytest.mark.parametrize('hour,minute,expected', [(15, 29, '2026-10-15T15:29'), (15, 30, '2026-10-15T23:30'),
    (23, 0, '2026-10-15T23:30'), (23, 30, '2026-10-15T23:30'), (0, 0, '2026-10-15T00:00')])
def test_quiet_hours_boundaries(hour, minute, expected):
    at = datetime(2026,10,15,hour,minute,tzinfo=timezone.utc)
    assert stamp(quiet_due(at, NotificationPolicyConfig())).startswith(expected)
    assert quiet_due(at, NotificationPolicyConfig(quiet_enabled=False)) == at


def test_payload_truncation_deadline_unicode_and_official_url(store, config):
    saved = seed(store); candidate, sources = item(store, saved); at = utc_now()
    candidate.update(title='🟠'+'中文 English 🎉'*100, summary='公开摘要'*1000, deadline_at=stamp(at + timedelta(hours=1)), importance='critical')
    payload = presentation(candidate, sources, NotificationPolicyConfig(), config, at=at, notification_id='stable')
    assert len(payload['title']) <= 120 and len(payload['body']) <= 400
    assert payload['title'].startswith('🔴') and payload['title'].endswith('…')
    assert '即将截止：' in payload['body'] and '机械与汽车工程学院' in payload['body']
    assert payload['url'].startswith('https://unit.zqu.edu.cn/') and payload['level'] == 'timeSensitive'
    assert payload['group'] == 'ZQU' and 'device_key' not in payload and 'sound' not in payload
    for url in ['javascript:alert(1)', 'data:text/plain,x', 'file:///secret', 'http://localhost/a', 'https://unit.zqu.edu.cn.evil.test/a', 'https://user@unit.zqu.edu.cn/a']:
        assert click_url(url, sources) is None


def test_optional_bark_fields_and_quiet_queue_critical_bypass(store, config):
    captured = []
    def receive(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'code':200})
    client = BarkClient(config, transport=httpx.MockTransport(receive))
    payload = {'title':'测试', 'body':'公开正文', 'icon':'https://www.zqu.edu.cn/logo.png',
               'copy':'公开文字', 'sound':'minuet', 'url':'https://www.zqu.edu.cn/', 'level':'active'}
    assert client.send(payload).accepted
    assert all(captured[0][key] == value for key,value in payload.items())
    service, calls = dispatcher(store, config)
    candidate, sources = item(store, seed(store))
    # Choose the discovery date, then enter the default Shanghai quiet range.
    from zoneinfo import ZoneInfo
    local = utc_now().astimezone(ZoneInfo('Asia/Shanghai'))
    at = local.replace(hour=23,minute=45,second=0,microsecond=0)
    policy = NotificationPolicyConfig()
    activation = stamp(utc_now()-timedelta(minutes=1))
    _, due = evaluate(candidate, sources, policy, activation_at=activation, at=at)
    assert due and datetime.fromisoformat(due) > at
    critical = {**candidate, 'importance':'critical'}
    assert evaluate(critical, sources, policy, activation_at=activation, at=at)[1] == stamp(at)


def test_pipeline_persistent_dedup_restart_update_reprint_and_atomic_claim(store, config):
    service, calls = dispatcher(store, config)
    saved = seed(store)
    assert service.tick() == 1 and len(calls) == 1
    assert service.repository.item_status(saved['raw_item_id'])['status'] == 'sent'
    service.tick(); restarted, _ = dispatcher(store, config); restarted.tick()
    seed(store, body='车辆工程更新后的竞赛报名要求。'); restarted.tick()
    assert len(calls) == 1
    seed(store, 2, body='车辆工程更新后的竞赛报名要求。'); restarted.tick()
    assert len(service.repository.history()['items']) == 1
    fresh = seed(store, 3, body='独立的车辆工程报名项目。')
    service.repository.reconcile(config)
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(lambda _: NotificationStore(store).claim(config, at=utc_now()), range(6)))
    assert sum(bool(r) for r in claims) == 1
    assert next(r for r in claims if r)['raw_item_id'] == fresh['raw_item_id']


def test_500_historical_baseline_then_only_one_new_push(store, config):
    for i in range(500):
        seed(store, i, body=f'历史信息 {i} 车辆工程报名。')
    service, calls = dispatcher(store, config)
    service.tick()
    assert calls == []
    history = service.repository.history(limit=20)
    assert history['total'] == 500 and all(r['suppressed_reason'] == 'historical' for r in history['items'])
    seed(store, 501, body='全新的车辆工程报名要求。')
    assert service.tick() == 1 and len(calls) == 1
    service.tick(); assert len(calls) == 1


def test_retry_state_backoff_maximum_and_ingestion_survives(store, config):
    now = [utc_now()]
    service, calls = dispatcher(store, config, handler=lambda req: httpx.Response(503), clock=lambda: now[0])
    saved = seed(store)
    service.tick(); service.tick(); assert len(calls) == 1
    for advance in [30,60]:
        now[0] += timedelta(seconds=advance); service.tick()
    status = service.repository.history()['items'][0]
    assert status['status'] == 'failed' and status['attempt_count'] == 3 and status['scheduled_at'] is None
    now[0] += timedelta(days=1); service.tick(); assert len(calls) == 3
    assert OfficialStore(store).detail(saved['raw_item_id'])['content']


def test_crash_recovery_before_http_resumes_but_after_http_never_repeats(store, config):
    service, calls = dispatcher(store, config); seed(store)
    service.repository.reconcile(config)
    at = utc_now(); row = service.repository.claim(config, at=at)
    assert row
    service.repository.recover(at=at + timedelta(seconds=10))
    assert service.repository.claim(config, at=at + timedelta(seconds=10)) is None
    service.repository.recover(at=at + timedelta(minutes=3))
    resumed = service.repository.claim(config, at=at + timedelta(minutes=3))
    assert resumed and resumed['id'] == row['id']
    assert resumed['attempt_count'] == 1  # A crash before HTTP must not consume the retry budget.
    service.repository.start_request(resumed, at=at+timedelta(minutes=3))
    service.repository.recover(at=at+timedelta(minutes=6))
    assert service.repository.history()['items'][0]['last_error'] == 'delivery_unknown'
    assert service.settings()['last_error'] == 'delivery_unknown'
    assert service.repository.claim(config, at=at+timedelta(minutes=6)) is None and calls == []


@pytest.mark.parametrize('http_status', [200, 500])
def test_actual_crawler_to_policy_to_dispatch_and_failure_isolation(tmp_path, store, config, http_status):
    service, calls = dispatcher(store, config, handler=lambda req: httpx.Response(http_status, json={'code':200}))
    s = source(); mapping = {s.list_url: listing([(1, '车辆工程竞赛报名通知', TODAY)]),
        'https://unit.zqu.edu.cn/info/1/1.htm': document()}
    m, _ = monitor(tmp_path, store, mapping, notifications=service, notifier=SimpleNamespace(send=lambda *a, **kw:'os_accepted'))
    assert scan(m)['scan']['failed'] == 0
    service.tick(); assert calls == []  # Initial SOURCE backfill is protected even after activation.
    mapping[s.list_url] = listing([(2, '车辆工程第二项目报名通知', TODAY)])
    mapping['https://unit.zqu.edu.cn/info/1/2.htm'] = document('车辆工程第二项目报名通知', '新项目公开报名要求。')
    assert scan(m)['scan']['failed'] == 0
    service.tick(); assert len(calls) == 1
    assert m.store.feed()['total'] == 2 and service.repository.history()['items'][0]['status'] == ('sent' if http_status==200 else 'failed')
    scan(m); service.tick(); assert len(calls) == 1
    m.close()


def test_settings_api_test_separate_history_cooldown_secret_and_disable(store, config):
    service, calls = dispatcher(store, config)
    app = FastAPI(); app.include_router(router); app.state.official_monitor = SimpleNamespace(notifications=service)
    with TestClient(app) as client:
        base = '/api/personal/notifications/bark'
        settings = client.get(base+'/settings').json()
        assert settings['configured'] and config.device_key not in json.dumps(settings)
        assert client.post(base+'/test').status_code == 422  # No cross-origin form trigger.
        assert client.post(base+'/test', json={}).json()['accepted']
        assert not client.post(base+'/test', json={}).json()['accepted'] and len(calls) == 1
        assert client.get(base+'/history').json()['total'] == 0
        payload = {'enabled': False, 'expected_version': settings['version'], 'policy': settings['policy']}
        assert client.put(base+'/settings',json=payload).json()['status'] == 'disabled'
        assert client.put(base+'/settings',json=payload).status_code == 409
        assert not client.post(base+'/test',json={}).json()['accepted']
        seed(store); service.tick(); assert len(calls) == 1
        assert config.device_key not in client.get(base+'/history').text
        assert client.get(base+'/history?limit=1000').status_code == 422


def test_missing_config_is_clear_and_no_network(store):
    service, calls = dispatcher(store, BarkConfig.from_env({}))
    assert service.settings()['status'] == 'unconfigured'
    assert service.test_notification()['result'] == 'not_configured'
    seed(store); service.tick(); assert calls == []


def test_migration_008_keeps_old_ledger_rows(tmp_path, monkeypatch):
    monkeypatch.setattr('server.personal_store.SCHEMA_VERSION', 7)
    store = PersonalStore(tmp_path/'old.sqlite3'); store.initialize(); OfficialStore(store).configure([source()])
    # Seed old schema without the new column through the original SQL contract.
    with store.connection(write=True) as c:
        c.execute("INSERT INTO raw_item(id,source_id,kind,submission_hash,created_at) VALUES('old','unit-source','official_web','old',?)", (stamp(utc_now()),))
        c.execute("INSERT INTO official_notification VALUES('group','old',?,?,'os_accepted')", (stamp(utc_now()), stamp(utc_now())))
    monkeypatch.setattr('server.personal_store.SCHEMA_VERSION', 8)
    store.initialize(); store.initialize()
    with store.connection() as c:
        row = dict(c.execute('SELECT * FROM official_notification').fetchone())
        assert row['display_group'] == 'group' and row['raw_item_id'] == 'old' and row['result'] == 'os_accepted'
        assert row['channel'] == 'windows' and row['status'] == 'sent'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert c.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
