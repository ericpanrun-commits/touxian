"""Synthetic queue checks: reject non-approved destinations before key/ledger use."""
import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from server.ai_transport import DeepSeekTransport
from server.tests_wechat.test_pipeline import setup_service, scan
from server.wechat_collect.models import Observation, ObservedMessage


def enqueue_captured(service, personal):
    asyncio.run(scan(service))
    with personal.connection() as c:
        batch_id = c.execute('SELECT id FROM wechat_batch').fetchone()[0]
    return service.analysis.enqueue(batch_id, service.context.read())


@pytest.mark.parametrize('base,allowed', [
    ('https://api.deepseek.com', True),
    ('https://api.deepseek.com/v1', True),
    ('https://api.deepseek.com:443/chat/completions', True),
    ('https://api.deepseek.com/v1/chat/completions', True),
    ('https://api.deepseek.com:444', False),
    ('https://api.deepseek.com/upload', False),
    ('https://api.deepseek.com/v1/../upload', False),
    ('https://api.deepseek.com.evil.test', False),
    ('https://localhost', False),
])
def test_queue_destination_before_credentials_and_reservation(personal, monkeypatch, base, allowed):
    observation = Observation('test', 'unverified',
        (ObservedMessage('请于2026年10月30日前提交完整材料'),), True, True)
    service, _, calls = setup_service(personal, [observation])
    enqueue_captured(service, personal)
    with personal.connection(write=True) as c:
        job = c.execute('SELECT id,config_json FROM wechat_analysis_job').fetchone()
        config = json.loads(job['config_json'])
        config['base_url'] = base
        c.execute('UPDATE wechat_analysis_job SET config_json=? WHERE id=?',
                  (json.dumps(config), job['id']))
    if not allowed:
        monkeypatch.setattr(service.analysis.settings, 'resolved',
                            lambda: pytest.fail('Rejected endpoint must not read credentials'))
    assert asyncio.run(service.analysis.run_one(job['id'])) is allowed
    assert len(calls) == int(allowed)
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0] == int(allowed)
        if not allowed:
            assert c.execute('SELECT error_code FROM wechat_analysis_job').fetchone()[0] == 'endpoint_not_approved'
        assert c.execute('SELECT count(*) FROM event').fetchone()[0] == 0


@pytest.mark.parametrize('status,expected', [(429, 2), (502, 2), (503, 2), (401, 1), (402, 1), (403, 1)])
def test_retries_are_bounded_and_each_attempt_is_reserved(personal, status, expected):
    observation = Observation('test', 'unverified',
        (ObservedMessage('请于2026年10月30日前提交完整材料'),), True, True)
    service, clock, _ = setup_service(personal, [observation])
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={'Retry-After': '60'})
    service.analysis.transport = DeepSeekTransport(httpx.MockTransport(handler))
    job_id = enqueue_captured(service, personal)
    asyncio.run(service.analysis.run_one(job_id))
    asyncio.run(service.analysis.run_one(job_id))
    assert len(calls) == 1  # No immediate retry, including before Retry-After.
    for _ in range(3):
        clock[0] += timedelta(seconds=61)
        asyncio.run(service.analysis.run_one(job_id))
    assert len(calls) == expected
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_llm_attempt WHERE reserved_tokens>0').fetchone()[0] == expected
        assert c.execute('SELECT status FROM wechat_analysis_job').fetchone()[0] == 'error'


def test_token_budget_denies_request_before_http(personal):
    observation = Observation('test', 'unverified',
        (ObservedMessage('请于2026年10月30日前提交完整材料'),), True, True)
    service, _, calls = setup_service(personal, [observation])
    service.policy.config.budget.tokens_daily = 4096
    asyncio.run(scan(service))
    asyncio.run(service.process_queue())
    assert calls == []
    with personal.connection() as c:
        assert c.execute('SELECT status FROM wechat_analysis_job').fetchone()[0] == 'budget_deferred'
        assert c.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0] == 0
