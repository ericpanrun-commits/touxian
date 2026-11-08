"""No real samples or credentials: verify the isolated evaluation journal."""
import asyncio
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from server.ai_settings import AIConfig
from server.extraction_errors import ExtractionFailure


@pytest.fixture
def evaluator(tmp_path, monkeypatch):
    module = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/deepseek-reviewed-evaluation.py'))
    # Function globals are the namespace runpy executed, not its returned copy.
    namespace = module['main'].__globals__
    namespace['OUT'] = tmp_path/'evaluation'
    namespace['prepare'] = lambda: [dict(id=f'sample-{i}', text='请全体同学明天提交实验报告',
        source_time='2026-11-08T10:00:00+08:00', time_reliability='reliable') for i in range(3)]
    namespace['AISettings'] = lambda _: SimpleNamespace(
        _read=lambda: ({}, AIConfig()), resolved=lambda: (AIConfig(), SecretStr('synthetic-key')))
    namespace['validate_output'] = lambda *args, **kwargs: ({'items':[]}, [])
    calls = []
    async def request(self, body, config, key, **kwargs):
        calls.append(body)
        return SimpleNamespace(raw_output='{"items":[]}', usage={'total_tokens':100})
    monkeypatch.setattr(namespace['DeepSeekTransport'], 'request', request)
    return namespace, calls


def test_saved_response_is_revalidated_without_another_request(evaluator):
    namespace, calls = evaluator
    asyncio.run(namespace['main'](True))
    assert len(calls) == 3
    asyncio.run(namespace['main'](True))
    assert len(calls) == 3
    results = json.loads((namespace['OUT']/'results.json').read_text(encoding='utf8'))
    assert all(r['validation'] == 'passed' for r in results)


def test_two_network_failures_stop_run_and_unknowns_are_not_repeated(evaluator, monkeypatch):
    namespace, calls = evaluator
    async def failure(*args, **kwargs):
        calls.append('failed')
        raise ExtractionFailure('timeout', 'synthetic timeout')
    monkeypatch.setattr(namespace['DeepSeekTransport'], 'request', failure)
    asyncio.run(namespace['main'](True))
    asyncio.run(namespace['main'](True))
    assert calls == ['failed', 'failed']
    results = json.loads((namespace['OUT']/'results.json').read_text(encoding='utf8'))
    assert len(results) == 2 and all(r['state'] == 'outcome_unknown' for r in results)


def test_validation_failure_keeps_receipt_and_does_not_rebill(evaluator):
    namespace, calls = evaluator
    def invalid(*args, **kwargs):
        raise ExtractionFailure('invalid_evidence', 'synthetic invalid quote')
    namespace['validate_output'] = invalid
    asyncio.run(namespace['main'](True))
    asyncio.run(namespace['main'](True))
    assert len(calls) == 3
    results = json.loads((namespace['OUT']/'results.json').read_text(encoding='utf8'))
    assert all(r['state'] == 'received' and r['validation'] == 'failed' for r in results)
    namespace['validate_output'] = lambda *args, **kwargs: ({'items':[]}, [])
    asyncio.run(namespace['main'](True, replay_only=True))
    results = json.loads((namespace['OUT']/'results.json').read_text(encoding='utf8'))
    assert len(calls) == 3
    assert all(r['validation'] == 'passed' and 'validation_error' not in r for r in results)


def test_preparation_never_resolves_key_or_calls_model(evaluator):
    namespace, calls = evaluator
    namespace['AISettings'] = lambda _: SimpleNamespace(_read=lambda: ({}, AIConfig()))
    asyncio.run(namespace['main'](False))
    assert calls == []
    assert not (namespace['OUT']/'evaluation-controls.json').exists()


def test_replay_only_never_resolves_credentials_or_fills_missing_receipt(evaluator):
    namespace, calls = evaluator
    namespace['AISettings'] = lambda _: SimpleNamespace(_read=lambda: ({}, AIConfig()))
    with pytest.raises(ValueError, match='saved_response_required'):
        asyncio.run(namespace['main'](True, replay_only=True))
    assert calls == []
