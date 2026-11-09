"""Synthetic payloads, fake HTTP transport; never runs app startup or a model."""
import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server import health_overview as health


@pytest.fixture
def dto():
    return json.loads((Path(__file__).resolve().parents[1] / 'tests/health-overview.synthetic.json').read_text(encoding='utf-8-sig'))


def client(peer='127.0.0.1', base='http://127.0.0.1:18788'):
    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app, base_url=base, client=(peer, 51000))


def test_guard_and_no_store(dto, monkeypatch):
    calls = []
    async def read():
        calls.append(True)
        return dto
    monkeypatch.setattr(health, 'read_overview', read)
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('APP_RUNTIME', 'desktop')
    result = client().get('/api/personal/health/overview')
    assert result.status_code == 200
    assert result.headers['Cache-Control'] == 'private, no-store'
    for peer, base, headers in [
        ('192.168.1.3', 'http://127.0.0.1:18788', {}),
        ('127.0.0.1', 'http://attacker.test:18788', {}),
        ('127.0.0.1', 'http://127.0.0.1:18788', {'Origin': 'https://evil.test'}),
        ('127.0.0.1', 'http://127.0.0.1:18788', {'Origin': 'http://localhost:9999'}),
        ('127.0.0.1', 'http://127.0.0.1:18788', {'Sec-Fetch-Site': 'cross-site'}),
    ]:
        assert client(peer, base).get('/api/personal/health/overview', headers=headers).status_code == 403
    assert len(calls) == 1
    monkeypatch.setenv('APP_RUNTIME', 'cloud')
    assert client().get('/api/personal/health/overview').status_code == 403


def test_fixed_upstream_and_whitelist(dto, monkeypatch):
    dto['private_export'] = 'never copied'
    dto['metrics'][0]['raw'] = 'never copied'
    calls = []
    def upstream(request):
        calls.append(request)
        assert request.method == 'GET'
        assert str(request.url) == health.ORIGIN + health.PATH + '?view=trend'
        assert request.headers['Authorization'] == 'Bearer ' + 'D' * 43
        assert 'cookie' not in request.headers
        return httpx.Response(200, json=dto)
    native = httpx.AsyncClient
    monkeypatch.setattr(health, 'configuration', lambda: ('D' * 43, 'trend'))
    monkeypatch.setattr(health.httpx, 'AsyncClient', lambda **kwargs: native(**kwargs, transport=httpx.MockTransport(upstream)))
    result = asyncio.run(health.read_overview())
    assert len(calls) == 1
    assert 'never copied' not in json.dumps(result)


@pytest.mark.parametrize('kind', ['401', '302', 'large', 'invalid', 'html', 'network', 'mode'])
def test_upstream_failure_is_local_to_health(dto, monkeypatch, kind):
    def upstream(request):
        if kind == 'network':
            raise httpx.ConnectError('PRIVATE token details', request=request)
        if kind == '401':
            return httpx.Response(401, text='PRIVATE')
        if kind == '302':
            return httpx.Response(302, headers={'location': 'https://evil.test'})
        if kind == 'large':
            return httpx.Response(200, content=b'x' * (health.LIMIT + 1), headers={'content-type': 'application/json'})
        if kind == 'html':
            return httpx.Response(200, text='<script>PRIVATE</script>')
        if kind == 'invalid':
            dto['metrics'][0]['chart']['points'][0]['x'] = float('inf')
            return httpx.Response(200, content=json.dumps(dto), headers={'content-type': 'application/json'})
        dto['mode'] = 'summary'
        return httpx.Response(200, json=dto)
    native = httpx.AsyncClient
    monkeypatch.setattr(health, 'configuration', lambda: ('D' * 43, 'trend'))
    monkeypatch.setattr(health.httpx, 'AsyncClient', lambda **kwargs: native(**kwargs, transport=httpx.MockTransport(upstream)))
    with pytest.raises(HTTPException) as failure:
        asyncio.run(health.read_overview())
    assert failure.value.status_code == 503
    assert 'PRIVATE' not in str(failure.value.detail)


def test_contract_invalid_payloads(dto):
    cases = [lambda v: v.update(schema='other'), lambda v: v['metrics'].pop(),
             lambda v: v['metrics'][0].update(latest_value=True),
             lambda v: v['metrics'][0]['days'][0].update(value='3'),
             lambda v: v['metrics'][0]['chart']['points'][0].update(index=-1),
             lambda v: v['analysis'].update(source_matches=False)]
    for mutate in cases:
        value = copy.deepcopy(dto)
        mutate(value)
        with pytest.raises((ValueError, TypeError, KeyError)):
            health.validate_overview(value)
    dto['mode'] = 'summary'
    assert 'days' not in health.validate_overview(dto)['metrics'][0]


def test_dpapi_configuration_uses_current_user_and_never_changes_database(tmp_path, monkeypatch):
    from server.ai_settings import protect
    path = tmp_path / 'health-overview.json'
    monkeypatch.setattr(health, 'config_path', lambda: path)
    with pytest.raises(HTTPException):
        health.configuration()
    value = {'mode': 'trend', 'token_dpapi': protect('D' * 43)}
    path.write_text(json.dumps(value), encoding='utf-8')
    assert 'D' * 43 not in path.read_text()
    before = path.read_bytes()
    assert health.configuration() == ('D' * 43, 'trend')
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_streamed_response_limit_and_total_timeout(monkeypatch):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'x' * 20_000
            yield b'x' * 20_000
    native = httpx.AsyncClient
    monkeypatch.setattr(health, 'configuration', lambda: ('D' * 43, 'trend'))
    monkeypatch.setattr(health.httpx, 'AsyncClient', lambda **kwargs: native(**kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream(), headers={'content-type': 'application/json'}))))
    with pytest.raises(HTTPException) as error:
        asyncio.run(health.read_overview())
    assert error.value.status_code == 503
    async def stalled(request):
        await asyncio.sleep(30)
        raise AssertionError('overall timeout did not cancel transport')
    monkeypatch.setattr(health.httpx, 'AsyncClient', lambda **kwargs: native(**kwargs, transport=httpx.MockTransport(stalled)))
    with pytest.raises(HTTPException) as error:
        asyncio.run(health.read_overview())
    assert error.value.status_code == 503
