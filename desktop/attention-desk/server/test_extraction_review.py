"""Candidate-only extraction, error retention and explicit confirmation acceptance."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from datetime import datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from server.ai_settings import AIConfig, AISettings
from server.extraction_errors import ExtractionFailure
from server.extraction_models import ConfirmationInput, RawInput
from server.extraction_provider import DeepSeekProvider, ProviderResult
from server.extraction_service import ExtractionService
from server.extraction_validation import CHINA, deadline_state, parse_output, validate_candidate
from server.personal_api import router as personal_router
from server.personal_store import PersonalError, PersonalStore
from server.review_api import router
from server.review_store import ReviewStore


@pytest.fixture
def store(tmp_path):
    result = PersonalStore(tmp_path / 'personal.sqlite3')
    result.initialize()
    return result


def candidate(text, dates=None, actions=None, **fields):
    spans = []
    def evidence(quote):
        start = text.index(quote)
        key = f'q{len(spans)}'
        spans.append(dict(id=key, quote=quote, start=start, end=start + len(quote)))
        return [key]
    action_values = [dict(id=f'a{i}', title=quote, kind='explicit', evidence_ids=evidence(quote)) for i, quote in enumerate(actions or ['提交申请'])]
    deadline_values = [dict(id=f'd{i}', role=f'提交阶段{i + 1}', original_text=quote, normalized_date=value, precision='date' if value else 'unknown',
                           related_action_ids=[action_values[min(i, len(action_values) - 1)]['id']] if action_values else [], evidence_ids=evidence(quote),
                           uncertain=value is None, relation='current') for i, (quote, value) in enumerate(dates or [('2026年10月12日前', '2026-10-12')])]
    event = dict(id='event1', title='申请报名', summary='', category='报名', audience=None, participation='required', notice_kind='new', eligibility=[],
                 deadlines=deadline_values, actions=action_values, materials=[], locations=[], uncertainties=[])
    event.update(fields)
    return dict(events=[event], evidence=spans)


def bundle_for(store, text, parsed, source_time=None):
    review = ReviewStore(store)
    raw = review.collect(RawInput(original_text=text, source_time=source_time), str(uuid4()))
    checked = parse_output(json.dumps(parsed, ensure_ascii=False))
    validation = validate_candidate(checked, raw['snapshot'])
    return review, raw, checked, validation


def confirmation(raw, parsed, participation='yes'):
    spans = {value['id']: dict(quote=value['quote'], start_offset=value['start'], end_offset=value['end']) for value in parsed['evidence']}
    facts = lambda values: [dict(text=value['text'], evidence=[spans[key] for key in value['evidence_ids']]) for value in values]
    events = []
    for value in parsed['events']:
        current = [date for date in value['deadlines'] if date['relation'] == 'current']
        events.append(dict(id=value['id'], title=value['title'], summary=value['summary'], category=value['category'], relevance='yes', participation=participation,
                           notice_kind=value['notice_kind'], audience=facts([value['audience']])[0] if value['audience'] else None,
                           eligibility=facts(value['eligibility']), materials=facts(value['materials']), locations=facts(value['locations']), uncertainties=value['uncertainties'],
                           deadlines=[dict(id=date['id'], role=date['role'], value=date['normalized_date'], precision=date['precision'], original_text=date['original_text'],
                                           evidence=[spans[key] for key in date['evidence_ids']], confirmed_by_user=True) for date in current],
                           actions=[dict(id=action['id'], title=action['title'], kind=action['kind'], evidence=[spans[key] for key in action['evidence_ids']],
                                         deadline_id=next((date['id'] for date in current if action['id'] in date['related_action_ids']), None)) for action in value['actions']]))
    return ConfirmationInput(expected_version=raw['version'], extraction_id=raw['extraction_id'], acknowledged_warnings=True, events=events)


def counts(store):
    with store.connection() as connection:
        return {table: connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('raw_item', 'raw_snapshot', 'event', 'deadline', 'task', 'evidence', 'ai_extraction')}


class TestSettings:
    __test__ = False
    def __init__(self):
        self.config = AIConfig()
    def _read(self): return {}, self.config
    def resolved(self): return self.config, SecretStr('test-key-for-transport-only')


class FixtureProvider:
    name = 'deepseek'
    def __init__(self, output=None, failure=None): self.output, self.failure, self.calls = output, failure, 0
    async def extract(self, snapshot, config, key):
        self.calls += 1
        if self.failure: raise self.failure
        return ProviderResult(json.dumps(self.output, ensure_ascii=False), {'total_tokens': 44}, 'fixture-model')


async def finish(service):
    await asyncio.gather(*list(service.tasks.values()))


def test_case_1_single_deadline_action_raw_first_then_confirm_restart(store):
    text = '📌请于2026年10月12日前提交申请。'
    provider = FixtureProvider(candidate(text))
    review = ReviewStore(store)
    raw = review.collect(RawInput(original_text=text))
    assert counts(store)['event'] == counts(store)['task'] == 0
    async def run():
        service = ExtractionService(store, TestSettings(), provider)
        started = await service.start(raw['snapshot']['id'])
        assert started['extraction']['status'] == 'running'
        assert counts(store)['event'] == counts(store)['task'] == 0
        await finish(service)
        return review.get(raw['snapshot']['id'])
    raw = asyncio.run(run())
    assert raw['extraction']['status'] == 'valid'
    assert counts(store)['event'] == 0
    payload = confirmation(raw, raw['extraction']['parsed_output'])
    saved = review.confirm(raw['snapshot']['id'], payload)['events'][0]
    assert saved['status'] == 'needs_review'
    assert len(saved['deadlines']) == len(saved['tasks']) == 1
    assert saved['tasks'][0]['deadline_id'] == saved['deadlines'][0]['id']
    assert saved['deadlines'][0]['value'] == '2026-10-12'
    assert saved['context']['extraction']['provider'] == 'deepseek'
    assert review.confirm(raw['snapshot']['id'], payload)['events'][0]['id'] == saved['id']
    reopened = PersonalStore(store.path); reopened.initialize()
    assert reopened.get_event(saved['id']) == saved
    for evidence in saved['evidence']:
        assert text[evidence['start_offset']:evidence['end_offset']] == evidence['quote']
        assert evidence['raw_snapshot_id'] == raw['snapshot']['id']


def test_case_2_multiple_dates_keep_associations(store):
    text = '2026年10月10日前提交申请；2026年10月12日前补交材料；2026年10月20日参加活动。'
    dates = [('2026年10月10日前', '2026-10-10'), ('2026年10月12日前', '2026-10-12'), ('2026年10月20日', '2026-10-20')]
    review, raw, parsed, _ = bundle_for(store, text, candidate(text, dates, ['提交申请', '补交材料', '参加活动']))
    saved = review.confirm(raw['snapshot']['id'], confirmation(raw, parsed))['events'][0]
    assert len(saved['deadlines']) == len(saved['tasks']) == 3
    assert [task['deadline_id'] for task in saved['tasks']] == [date['id'] for date in saved['deadlines']]


@pytest.mark.parametrize('choice', ['yes', 'no', 'later'])
def test_case_3_optional_participation_controls_tasks(store, choice):
    text = '有意报名的同学于2026年10月12日前提交申请。'
    review, raw, parsed, validation = bundle_for(store, text, candidate(text, participation='optional'))
    assert any(value['code'] == 'participation_decision' for value in validation['warnings'])
    saved = review.confirm(raw['snapshot']['id'], confirmation(raw, parsed, choice))['events'][0]
    assert len(saved['tasks']) == (1 if choice == 'yes' else 0)
    assert saved['status'] == ('cancelled' if choice == 'no' else 'needs_review')
    if choice == 'later':
        assert saved['context']['planned_actions']
        from server.personal_models import TaskInput
        with pytest.raises(PersonalError) as failure:
            store.add_task(saved['id'], TaskInput(expected_version=saved['version'], title='尚未参加的任务'))
        assert failure.value.code == 'participation_required'
        with pytest.raises(PersonalError): store.change_status(saved['id'], saved['version'], 'in_progress')
        assert counts(store)['task'] == 0
        saved = review.participation(saved['id'], saved['version'], 'yes')
        assert len(saved['tasks']) == 1
        with pytest.raises(PersonalError, match='已按你的确认创建'):
            review.participation(saved['id'], saved['version'], 'yes')


def test_case_4_relative_date_without_source_time_is_unresolved(store):
    text = '明天下午提交申请。'
    _, _, parsed, validation = bundle_for(store, text, candidate(text, [('明天下午', None)]))
    assert parsed['events'][0]['deadlines'][0]['uncertain']
    assert 'relative_without_source_time' in {value['code'] for value in validation['warnings']}
    assert counts(store)['event'] == 0


def test_case_5_missing_year_and_unrelated_year_do_not_confirm_year(store):
    text = '2025年获奖项目介绍。请于10月12日前提交申请。'
    _, _, parsed, validation = bundle_for(store, text, candidate(text, [('10月12日前', '2026-10-12')]))
    assert parsed['events'][0]['deadlines'][0]['uncertain']
    assert 'uncertain_year' in {value['code'] for value in validation['warnings']}


def test_case_6_postponement_keeps_previous_but_only_selected_current_date(store):
    text = '原定2026年10月12日截止，现延长至2026年10月15日，请提交申请。'
    value = candidate(text, [('2026年10月12日', '2026-10-12'), ('2026年10月15日', '2026-10-15')], notice_kind='update')
    value['events'][0]['deadlines'][0]['relation'] = 'previous'
    review, raw, parsed, validation = bundle_for(store, text, value)
    assert 'update' in {item['code'] for item in validation['warnings']}
    saved = review.confirm(raw['snapshot']['id'], confirmation(raw, parsed))['events'][0]
    assert [date['value'] for date in saved['deadlines']] == ['2026-10-15']


def test_case_7_cancellation_does_not_create_tasks_or_mutate_existing(store):
    text = '2026年10月12日前提交申请。'
    review, raw, parsed, _ = bundle_for(store, text, candidate(text))
    existing = review.confirm(raw['snapshot']['id'], confirmation(raw, parsed))['events'][0]
    cancellation_text = '取消2026年10月12日的报名，不再提交申请。'
    review, raw, parsed, validation = bundle_for(store, cancellation_text, candidate(cancellation_text, [('2026年10月12日', '2026-10-12')], notice_kind='cancellation'))
    assert 'cancellation' in {item['code'] for item in validation['warnings']}
    saved = review.confirm(raw['snapshot']['id'], confirmation(raw, parsed))['events'][0]
    assert saved['tasks'] == []
    assert store.get_event(existing['id']) == existing


def test_case_8_mentioned_material_is_not_an_available_attachment(store):
    text = '2026年10月12日前提交申请，需附件1。'
    value = candidate(text)
    start = text.index('附件1')
    value['evidence'].append(dict(id='material', quote='附件1', start=start, end=start + 3))
    value['events'][0]['materials'] = [dict(text='附件1', evidence_ids=['material'])]
    _, _, _, validation = bundle_for(store, text, value)
    assert 'material_missing' in {item['code'] for item in validation['warnings']}


def test_case_9_fabricated_evidence_cannot_enter_formal_records(store):
    text = '2026年10月12日前提交申请。'
    value = candidate(text); value['evidence'][0]['quote'] = '不存在的行动'
    review, raw, parsed, validation = bundle_for(store, text, value)
    assert validation['status'] == 'needs_review'
    assert 'invalid_evidence' in {item['code'] for item in validation['warnings']}
    with pytest.raises(PersonalError): review.confirm(raw['snapshot']['id'], confirmation(raw, parsed))
    assert counts(store)['event'] == counts(store)['task'] == counts(store)['deadline'] == counts(store)['evidence'] == 0


def test_case_10_fabricated_high_risk_is_outside_schema(store):
    text = '2026年10月12日前提交申请。'
    value = candidate(text); value['events'][0]['risk'] = 100
    with pytest.raises(ExtractionFailure) as failure: parse_output(json.dumps(value))
    assert failure.value.code == 'invalid_schema'
    assert counts(store)['event'] == 0


@pytest.mark.parametrize('failure', [ExtractionFailure('timeout', '分析超时'), ExtractionFailure('truncated', '输出被截断', raw_output='{"events":[', usage={'total_tokens': 40})])
def test_cases_11_12_provider_failure_retains_raw_and_supports_manual_confirmation(store, failure):
    text = '2026年10月12日前提交申请。'
    provider = FixtureProvider(failure=failure)
    review = ReviewStore(store); raw = review.collect(RawInput(original_text=text))
    async def run():
        service = ExtractionService(store, TestSettings(), provider)
        await service.start(raw['snapshot']['id']); await finish(service)
    asyncio.run(run())
    raw = review.get(raw['snapshot']['id'])
    assert raw['snapshot']['original_text'] == text
    assert raw['extraction']['status'] == 'failed'
    assert raw['extraction']['error']['code'] == failure.code
    assert raw['extraction']['raw_output'] == failure.raw_output
    assert counts(store)['event'] == 0
    assert len(review.confirm(raw['snapshot']['id'], confirmation(raw, candidate(text)))['events'][0]['tasks']) == 1


def test_cache_concurrent_identical_raw_retries_force_and_config_change(store):
    text = '2026年10月12日前提交申请。'
    provider = FixtureProvider(candidate(text)); settings = TestSettings()
    review = ReviewStore(store)
    first = review.collect(RawInput(original_text=text))
    second = review.collect(RawInput(original_text=text))
    async def run():
        service = ExtractionService(store, settings, provider)
        request_id = str(uuid4())
        start = await service.start(first['snapshot']['id'], request_id=request_id)
        replay = await service.start(first['snapshot']['id'], request_id=request_id)
        follower = await service.start(second['snapshot']['id'])
        assert start['extraction_id'] == replay['extraction_id']
        assert follower['extraction']['status'] == 'waiting'
        await finish(service)
        assert provider.calls == 1
        assert review.get(second['snapshot']['id'])['extraction']['validation']['evidence'][0]['raw_snapshot_id'] == second['snapshot']['id']
        await service.start(first['snapshot']['id']); assert provider.calls == 1
        # Cache persists across service restarts; a new instance makes no call.
        restarted = ExtractionService(store, settings, provider)
        await restarted.start(first['snapshot']['id']); assert not restarted.tasks
        forced_id = str(uuid4())
        forced = await restarted.start(first['snapshot']['id'], force=True, request_id=forced_id)
        assert forced['extraction_id'] != start['extraction_id']
        await finish(restarted)
        await restarted.start(first['snapshot']['id'], force=True, request_id=forced_id)
        assert provider.calls == 2
        settings.config.thinking = True
        await restarted.start(first['snapshot']['id']); await finish(restarted)
        assert provider.calls == 3
    asyncio.run(run())


def test_raw_idempotency_draft_conflict_cancel_resume_and_api(store):
    app = FastAPI(); app.include_router(personal_router); app.include_router(router)
    app.state.personal_store = store
    with TestClient(app) as client:
        key = str(uuid4()); payload = dict(original_text='原文保留。', title_hint='手动整理')
        response = client.post('/api/personal/raw', json=payload, headers={'Idempotency-Key': key})
        assert response.status_code == 201
        raw = response.json(); path = '/api/personal/raw/' + raw['snapshot']['id']
        assert client.post('/api/personal/raw', json=payload, headers={'Idempotency-Key': key}).json()['snapshot']['id'] == raw['snapshot']['id']
        assert client.post('/api/personal/raw', json={'original_text': '修改内容'}, headers={'Idempotency-Key': key}).status_code == 409
        draft = client.put(path + '/draft', json={'expected_version': raw['version'], 'draft': {'events': []}}).json()
        assert client.put(path + '/draft', json={'expected_version': raw['version'], 'draft': {}}).status_code == 409
        cancelled = client.post(path + '/cancel', json={'expected_version': draft['version']}).json()
        resumed = client.post(path + '/resume', json={'expected_version': cancelled['version']}).json()
        assert resumed['draft'] == {'events': []}
        assert counts(store)['event'] == 0
        schema = client.get('/api/personal/extraction-schema').json()
        assert schema['version'] == '2' and schema['schema']['additionalProperties'] is False


def test_windows_key_encryption_public_redaction_preserve_clear_and_corrupt(tmp_path, monkeypatch):
    if os.name != 'nt': pytest.skip('Windows DPAPI acceptance')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False); monkeypatch.delenv('ATTENTION_DEEPSEEK_API_KEY', raising=False)
    settings = AISettings(tmp_path / 'ai-settings.json'); secret = 'synthetic-key-for-dpapi-test'
    public = settings.save({'api_key': secret})
    assert public['has_api_key'] and secret not in json.dumps(public)
    assert secret not in settings.path.read_text()
    restarted = AISettings(settings.path)
    assert restarted.resolved()[1].get_secret_value() == secret
    restarted.save({'api_key': '', 'thinking': True})
    assert restarted.resolved()[1].get_secret_value() == secret
    before = settings.path.read_text()
    with pytest.raises(PersonalError): settings.save({'base_url': 'https://service/?api_key=' + secret})
    assert settings.path.read_text() == before
    settings.save({'clear_api_key': True}); assert not settings.public()['has_api_key']
    settings.path.write_text('{invalid')
    with pytest.raises(PersonalError): settings.save({'api_key': secret})
    assert settings.path.read_text() == '{invalid'


@pytest.mark.parametrize('status,code,attempts', [(401, 'unauthorized', 1), (403, 'forbidden', 1), (402, 'insufficient_balance', 1),
                                              (429, 'rate_limited', 2), (503, 'provider_error', 2), (500, 'provider_error', 1)])
def test_real_provider_http_errors_are_bounded_and_do_not_echo_key(status, code, attempts):
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(status, json={'message': 'test-secret-do-not-log'})
    provider = DeepSeekProvider(transport=httpx.MockTransport(transport))
    with pytest.raises(ExtractionFailure) as failure:
        asyncio.run(provider.extract({'id': 'snapshot', 'original_text': '通知'}, AIConfig(), SecretStr('test-secret-do-not-log')))
    assert failure.value.code == code and len(calls) == attempts
    assert 'test-secret-do-not-log' not in str(failure.value)


def test_provider_payload_thinking_timeout_truncated_and_missing_key():
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"events":['}, 'finish_reason': 'length'}], 'usage': {'total_tokens': 12}})
    provider = DeepSeekProvider(transport=httpx.MockTransport(transport))
    with pytest.raises(ExtractionFailure) as failure:
        asyncio.run(provider.extract({'id': 'snapshot', 'original_text': '通知'}, AIConfig(), SecretStr('test-key')))
    assert failure.value.code == 'truncated' and failure.value.raw_output == '{"events":[' and failure.value.usage == {'total_tokens': 12}
    body = json.loads(calls[0].content)
    assert body['thinking'] == {'type': 'disabled'} and body['response_format'] == {'type': 'json_object'}
    assert body['model'] == 'deepseek-flash' and body['max_tokens'] == 4096
    assert str(calls[0].url) == 'https://api.deepseek.com/chat/completions'
    def timeout(request): raise httpx.ReadTimeout('contains-test-key', request=request)
    with pytest.raises(ExtractionFailure) as failure:
        asyncio.run(DeepSeekProvider(transport=httpx.MockTransport(timeout)).extract({'id': 'snapshot', 'original_text': '通知'}, AIConfig(), SecretStr('test-key')))
    assert failure.value.code == 'timeout' and 'test-key' not in str(failure.value)
    with pytest.raises(ExtractionFailure) as failure:
        asyncio.run(provider.extract({'id': 'snapshot', 'original_text': '通知'}, AIConfig(), SecretStr('')))
    assert failure.value.code == 'missing_key' and len(calls) == 1


@pytest.mark.parametrize('raw', ['{"events":[', '{"events":[],"events":[],"evidence":[]}', '```json\n{}\n```', '{"events":[],"evidence":[],"risk":NaN}'])
def test_strict_json_refuses_partial_duplicate_fenced_and_nonfinite(raw):
    with pytest.raises(ExtractionFailure) as failure: parse_output(raw)
    assert failure.value.code == ('truncated' if raw == '{"events":[' else 'invalid_json')


def test_date_only_never_synthesizes_time_and_minute_uses_china_timezone():
    at = datetime(2026, 10, 12, 23, 59, tzinfo=CHINA)
    assert not deadline_state('2026-10-12', 'date', at=at)['overdue']
    assert deadline_state('2026-10-12T12:00', 'minute', at=at)['overdue']
    assert deadline_state('2026-10-13', 'date', at=at)['days_remaining'] == 1


def test_remote_scores_and_action_cannot_create_p0_but_local_risk_still_can():
    from server import main
    judgments = {
        'importance': {'value': 80}, 'urgency': {'value': 90}, 'risk': {'value': 0},
        'action_required': {'value': False, 'metadata': {'kind': 'optional'}}, 'record_kind': {'value': 'announcement'},
        'is_announcement': {'value': True}, 'deadline': {'value': None}, 'audience': {'value': 'all'},
        'category': {'value': 'activity', 'confidence': 0.9},
    }
    base = {'judgments': judgments, 'window': {'end': '2026-10-07T09:00'}}
    main.apply_jev_answers(base, {'risk': {'score': 100, 'confidence': 1}, 'action_required': {'probability': 1, 'confidence': 1}})
    assert base['local_priority_guard']['risk'] == 0
    assert not base['local_priority_guard']['required_action']
    # Even accepted remote semantic claims cannot satisfy the local guard.
    base['judgments']['risk']['value'] = 100
    base['judgments']['action_required'].update(value=True, metadata={'kind': 'required'})
    main.TRIAGE.reduce_priority(base, datetime(2026, 10, 7).date())
    assert base['decision']['priority'] != 'P0' and base['decision']['p0_basis'] is None
    historical = copy.deepcopy(base)
    del historical['local_priority_guard']
    historical['judgments']['deadline']['value'] = {'kind': 'deadline', 'normalized': '2026-10-06', 'days_from_as_of': -1}
    main.TRIAGE.reduce_priority(historical, datetime(2026, 10, 7).date())
    assert historical['decision']['priority'] != 'P0'
    base['local_priority_guard'] = {'risk': 100, 'required_action': True, 'is_announcement': True}
    main.TRIAGE.reduce_priority(base, datetime(2026, 10, 7).date())
    assert base['decision']['priority'] == 'P0' and base['decision']['p0_basis']


@pytest.mark.parametrize('mutate,error', [(lambda value: '{"events":[', 'truncated'),
                                        (lambda value: json.dumps({**value, 'risk': 100}), 'invalid_schema')])
def test_invalid_output_versions_keep_raw_and_diagnostics(store, mutate, error):
    text = '2026年10月12日前提交申请。'
    class OutputProvider:
        name = 'deepseek'
        async def extract(self, *_): return ProviderResult(mutate(candidate(text)), {'total_tokens': 20}, 'fixture')
    review = ReviewStore(store); raw = review.collect(RawInput(original_text=text))
    async def run():
        service = ExtractionService(store, TestSettings(), OutputProvider())
        await service.start(raw['snapshot']['id']); await finish(service)
    asyncio.run(run())
    result = review.get(raw['snapshot']['id'])
    assert result['extraction']['error']['code'] == error
    assert result['extraction']['raw_output'] and result['extraction']['token_usage']['total_tokens'] == 20
    assert result['snapshot']['original_text'] == text and counts(store)['event'] == 0


def test_missing_key_persisted_and_recovered_interruption_requires_explicit_retry(store, tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False); monkeypatch.delenv('ATTENTION_DEEPSEEK_API_KEY', raising=False)
    review = ReviewStore(store); raw = review.collect(RawInput(original_text='保存后再分析。'))
    async def run():
        service = ExtractionService(store, AISettings(tmp_path / 'settings.json'))
        await service.start(raw['snapshot']['id']); await finish(service)
        assert review.get(raw['snapshot']['id'])['extraction']['error']['code'] == 'missing_key'
        with store.connection(write=True) as connection:
            connection.execute("UPDATE ai_extraction SET status='running'")
        service.recover_interrupted()
        restarted = await service.start(raw['snapshot']['id'])
        assert restarted['extraction']['error']['code'] == 'interrupted'
        assert not service.tasks
    asyncio.run(run())
    assert counts(store)['event'] == counts(store)['task'] == 0


def test_service_redacts_secret_from_failed_provider_raw(store):
    review = ReviewStore(store); raw = review.collect(RawInput(original_text='原文。'))
    provider = FixtureProvider(failure=ExtractionFailure('truncated', '被截断', raw_output='test-key-for-transport-only'))
    async def run():
        service = ExtractionService(store, TestSettings(), provider)
        await service.start(raw['snapshot']['id']); await finish(service)
    asyncio.run(run())
    assert review.get(raw['snapshot']['id'])['extraction']['raw_output'] == '[redacted]'
