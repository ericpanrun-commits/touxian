"""UTC lifecycle, public navigation, process sharing and stale QR regressions."""
import asyncio
import copy
import json
import socket
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs
import httpx
import pytest
from fastapi import FastAPI
from server.collector.app import create_app, COOKIE
from server.collector.controller import CollectorController
from server.collector.control_api import router
from server.collector.endpoint import public_base_url
from server.collector.security import PairingAuthority, CollectorError, digest, SESSION_SECONDS
from server.test_collector import rig, network, FakeServer

PUBLIC = 'http://192.168.1.199:8473'  # Deliberate test fixture, never a saved preference.
START = datetime(2026, 10, 8, 12, tzinfo=timezone(timedelta(hours=8)))


@pytest.mark.parametrize('seconds,valid', [(0, True), (299, True), (300, False), (301, False)])
def test_utc_five_minute_boundaries(seconds, valid):
    tick = [START]
    authority = PairingAuthority(clock=lambda: tick[0])
    token = authority.regenerate()
    assert authority.public()['pairing']['created_at'] == '2026-10-08T04:00:00Z'
    assert authority.public()['pairing']['expires_at'] == '2026-10-08T04:05:00Z'
    tick[0] += timedelta(seconds=seconds)
    if valid:
        assert authority.validate(token)['pairing_valid']
    else:
        with pytest.raises(CollectorError): authority.validate(token)
        assert authority.public()['pairing']['state'] == 'expired'


def test_naive_clock_is_rejected_instead_of_silently_mixing_timezones():
    with pytest.raises(ValueError, match='aware_datetime'):
        PairingAuthority(clock=lambda: START.replace(tzinfo=None)).regenerate()


def test_safe_state_history_confirmation_rotation_revoke_and_restart(tmp_path):
    path = tmp_path / 'pairing-state.json'
    authority = PairingAuthority(diagnostics_path=path)
    token = authority.regenerate()
    authority.trace('GET', '/pair', token, 'valid')
    assert authority.current['state'] == 'created'
    authority.validate(token)
    assert authority.current['state'] == 'validated'
    cookie, session = authority.pair(token)
    consumed = copy.deepcopy(authority.current)
    assert consumed['history'] == ['created', 'validated', 'confirmed', 'consumed']
    assert consumed['confirmed_at'] == consumed['consumed_at']
    assert consumed['consume_reason'] == 'explicit_post_confirm'
    old = authority.regenerate(); old_id = authority.current['id']
    new = authority.regenerate()
    assert authority.session(cookie) == session
    assert next(r for r in authority.records if r['id'] == old_id)['consume_reason'] == 'regenerated'
    with pytest.raises(CollectorError): authority.validate(old)
    # Simulate a process crash: only safe metadata is loaded. Credentials are
    # deliberately not restored, and the fresh desktop status cannot show QR.
    restarted = PairingAuthority(diagnostics_path=path)
    assert restarted.epoch != authority.epoch and not restarted.public()['pairing_available']
    assert restarted.records[-1]['state'] == 'revoked'
    assert restarted.records[-1]['consume_reason'] == 'collector_restart'
    with pytest.raises(CollectorError): restarted.validate(new)
    with pytest.raises(CollectorError): restarted.session(cookie)
    saved = path.read_text(encoding='utf-8')
    assert all(secret not in saved for secret in [token, old, new, cookie, session.csrf, digest(token)])
    assert consumed['id'] in saved


def test_diagnostics_are_bounded_and_reject_unsafe_path_result(tmp_path):
    path = tmp_path / 'state.json'
    authority = PairingAuthority(diagnostics_path=path)
    for _ in range(70): token = authority.regenerate()
    for _ in range(140): authority.trace('GET', '/pair', token, 'valid')
    authority.trace('GET', '/pair?token=' + token, token, 'valid')
    authority.trace('POST', '/api/pair/confirm', token, 'secret-error-' + token)
    data = json.loads(path.read_text())
    assert len(data['tokens']) == 64 and len(data['requests']) == 128
    assert token not in path.read_text()
    assert all(r['path'] == '/pair' and len(r['token_id']) == 12 for r in data['requests'])


def test_session_diagnostics_distinguish_cookie_container_from_revocation(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            authority = PairingAuthority(diagnostics_path=tmp_path / 'safe-state.json')
            app = create_app(r.service, authority, PUBLIC, tmp_path / 'app')
            token = authority.regenerate()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as paired, \
                       httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as separate:
                approved = await paired.post('/api/pair/confirm', json={'token': token})
                cookie = paired.cookies.get(COOKIE)
                assert approved.status_code == 200
                assert (await paired.get('/api/session')).status_code == 200
                assert authority.session_requests[-1]['result'] == 'authenticated'
                authority.regenerate()
                assert (await separate.get('/api/session')).status_code == 401
                assert authority.session_requests[-1]['result'] == 'cookie_missing'
                assert authority.public()['paired_devices'] == 1
                assert authority.device_records[-1]['state'] == 'approved'
                authority.revoke('network_changed')
                assert (await paired.get('/api/session')).status_code == 401
                assert authority.session_requests[-1]['result'] == 'unknown_session'
                assert authority.session_requests[-1]['cookie_present'] is True
                assert authority.device_records[-1]['state'] == 'revoked'
                assert authority.device_records[-1]['revoke_reason'] == 'network_changed'
                saved = (tmp_path / 'safe-state.json').read_text()
                assert all(secret not in saved for secret in [token, cookie, digest(cookie), approved.json()['csrf']])
                assert all(row['path'] == '/api/session' and row['method'] == 'GET' for row in authority.session_requests)
    asyncio.run(run())


def test_device_diagnostics_expiry_and_restart_do_not_restore_auth(tmp_path):
    tick = [START]
    path = tmp_path / 'safe-state.json'
    authority = PairingAuthority(clock=lambda: tick[0], diagnostics_path=path)
    cookie, _ = authority.pair(authority.regenerate())
    tick[0] += timedelta(seconds=SESSION_SECONDS)
    assert authority.public()['paired_devices'] == 0
    saved = json.loads(path.read_text())
    assert saved['devices'][-1]['state'] == 'expired'
    assert saved['devices'][-1]['revoke_reason'] == 'ttl_elapsed'
    cookie, _ = authority.pair(authority.regenerate())
    restarted = PairingAuthority(clock=lambda: tick[0], diagnostics_path=path)
    assert restarted.device_records[-1]['revoke_reason'] == 'collector_restart'
    assert restarted.device_records[-1]['state'] == 'revoked'
    with pytest.raises(CollectorError): restarted.session(cookie, diagnose=True)
    assert restarted.session_requests[-1]['result'] == 'unknown_session'


def test_session_diagnostics_are_bounded_without_secrets(tmp_path):
    path = tmp_path / 'safe-state.json'
    authority = PairingAuthority(diagnostics_path=path)
    for _ in range(70):
        authority.revoke()
        cookie, session = authority.pair(authority.regenerate())
    for _ in range(140): authority.session(cookie, diagnose=True)
    saved = json.loads(path.read_text())
    assert len(saved['devices']) == 64 and len(saved['session_requests']) == 128
    assert len(saved['session_requests'][-1]['device_id']) == 12
    assert cookie not in path.read_text() and session.csrf not in path.read_text()
    assert digest(cookie) not in path.read_text()


@pytest.mark.parametrize('origin', [
    'http://127.0.0.1:8473', 'http://localhost:8473', 'http://[::1]:8473',
    'http://0.0.0.0:8473', 'http://8.8.8.8:8473', 'http://169.254.2.3:8473',
    PUBLIC + '/collect', PUBLIC + '?token=secret', PUBLIC + '#fragment',
    'http://user:pass@192.168.1.199:8473', 'https://192.168.1.199:8473',
])
def test_production_public_base_url_rejects_loopback_and_non_lan(origin):
    with pytest.raises(CollectorError, match='物理局域网'): public_base_url(origin)


def test_loopback_requires_explicit_isolated_fixture_opt_in():
    assert public_base_url(PUBLIC) == PUBLIC
    assert public_base_url('http://127.0.0.1:18473', allow_loopback_fixture=True) == 'http://127.0.0.1:18473'
    with pytest.raises(CollectorError): public_base_url('http://localhost:18473', allow_loopback_fixture=True)


def test_ten_gets_then_confirm_public_flow_and_cookie_permissions(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            authority = PairingAuthority(diagnostics_path=tmp_path / 'state.json')
            app = create_app(r.service, authority, PUBLIC, tmp_path / 'app')
            token = authority.regenerate()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as phone:
                for _ in range(10):
                    response = await phone.get('/pair', params={'token': token})
                    assert response.status_code == 200 and 'set-cookie' not in response.headers
                    assert response.headers['cache-control'] == 'no-store'
                assert authority.validate(token)['pairing_valid'] and not authority.sessions
                root = await phone.get('/')
                assert root.headers['location'] == '/collect'
                config = await phone.get('/collector-config')
                assert config.json() == {'public_base_url': PUBLIC, 'fixture': False}
                paired = await phone.post('/api/pair/confirm', json={'token': token})
                assert paired.status_code == 200 and paired.json()['collector_path'] == '/collect'
                cookie = paired.headers['set-cookie']
                assert all(s in cookie for s in ['HttpOnly', 'SameSite=strict', 'Path=/', f'Max-Age={SESSION_SECONDS}', 'expires='])
                assert 'Secure' not in cookie and 'Domain=' not in cookie
                assert (await phone.get('/api/session')).status_code == 200
                assert (await phone.post('/api/pair/confirm', json={'token': token})).status_code == 401
                for value in [PUBLIC, root.headers['location'], config.text, paired.json()['collector_path'], (await phone.get('/collect')).text]:
                    assert not any(bad in value for bad in ['127.0.0.1', 'localhost', '::1'])
                authority.regenerate()
                assert (await phone.get('/api/session')).status_code == 200
                for path in ['/api/personal/ai-settings', '/api/personal/events/x/delete', '/api/settings', '/api/secrets']:
                    assert (await phone.post(path, json={}, headers={'X-Collector-CSRF': paired.json()['csrf']})).status_code == 404
                authority.revoke()
                assert (await phone.get('/api/session')).status_code == 401
                assert (await phone.post('/api/file', content=b'bad body')).status_code == 401
            saved = (tmp_path / 'state.json').read_text()
            assert token not in saved and cookie not in saved
            assert len([r for r in authority.requests if r['method'] == 'GET']) == 10
    asyncio.run(run())


def test_control_and_listener_share_state_qr_generation_fences_and_restart(tmp_path, monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            class Listener:
                def setsockopt(self, *args): pass
                def bind(self, value): assert value == ('192.168.1.199', 8473)
                def listen(self, *args): pass
                def close(self): pass
            monkeypatch.setattr('server.collector.controller.socket', SimpleNamespace(socket=lambda *a: Listener(), AF_INET=socket.AF_INET,
                                SOCK_STREAM=socket.SOCK_STREAM, SOL_SOCKET=socket.SOL_SOCKET, SO_EXCLUSIVEADDRUSE=4))
            targets = []
            import qrcode
            original = qrcode.QRCode.add_data
            def capture(self, value, *args, **kwargs):
                targets.append(value)
                return original(self, value, *args, **kwargs)
            monkeypatch.setattr(qrcode.QRCode, 'add_data', capture)
            controller = CollectorController(r.store, r.docs, r.extraction, tmp_path / 'app', detector=lambda port: network(ip='192.168.1.199'), server_factory=FakeServer)
            local = FastAPI(); local.include_router(router); local.state.collector = controller
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local), base_url='http://127.0.0.1:8765') as desktop:
                enabled = (await desktop.post('/api/personal/collector/enable', json={'interface_id': '3', 'port': 8473})).json()
                assert enabled['public_base_url'] == PUBLIC and not enabled['pairing_available']
                assert (await desktop.get('/api/personal/collector/qr')).status_code == 409
                first = (await desktop.post('/api/personal/collector/regenerate')).json()
                target = urlsplit(targets[-1]); token = parse_qs(target.query)['token'][0]
                assert target.scheme + '://' + target.netloc == PUBLIC and target.path == '/pair'
                qr = await desktop.get('/api/personal/collector/qr', params={'pairing_id': first['pairing_id']})
                assert qr.status_code == 200 and qr.headers['cache-control'] == 'no-store'
                second = (await desktop.post('/api/personal/collector/regenerate')).json()
                assert second['pairing_id'] != first['pairing_id']
                assert (await desktop.get('/api/personal/collector/qr', params={'pairing_id': first['pairing_id']})).status_code == 409
                assert (await desktop.get('/api/personal/collector/qr', params={'pairing_id': second['pairing_id']})).status_code == 200
                lan = controller.server.config.app
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=lan), base_url=PUBLIC) as phone:
                    assert (await phone.post('/api/pair/validate', json={'token': token})).status_code == 401
                    new_token = parse_qs(urlsplit(targets[-1]).query)['token'][0]
                    assert (await phone.post('/api/pair/confirm', json={'token': new_token})).status_code == 200
                    state = (await desktop.get('/api/personal/collector')).json()
                    assert state['paired_devices'] == 1 and state['pairing']['state'] == 'consumed'
                    assert (await desktop.get('/api/personal/collector/qr', params={'pairing_id': second['pairing_id']})).status_code == 409
                await desktop.post('/api/personal/collector/stop')
            await controller.close()
            reopened = CollectorController(r.store, r.docs, r.extraction, tmp_path / 'app', detector=lambda port: network(ip='192.168.1.199'), server_factory=FakeServer)
            state = reopened.status()
            assert not state['enabled'] and state['pairing_id'] is None and state['server_epoch'] != first['server_epoch']
            assert not any(value in reopened.settings_path.read_text() for value in [PUBLIC, token])
            with pytest.raises(CollectorError): reopened.qr(first['pairing_id'])
            await reopened.close()
    asyncio.run(run())
