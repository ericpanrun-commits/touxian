"""A read-only recovery plan must include conditional vision, without any spend."""
import asyncio
import hashlib
import json

import pytest

from server.tests_wechat.test_pipeline import setup_service, scan
from server.tests_wechat.test_media_pipeline import NOW, observe_images
from server.wechat_collect.models import Observation, ObservedMessage
from server.wechat_collect.recovery import backlog_plan
from server.personal_store import encode


def queued_image(personal, monkeypatch, *, vision=True):
    control=personal.path.with_name('recovery-controls.json')
    control.write_text(json.dumps({'version':1,'hold_cloud_uploads':True,'pause_paid_ai':True}))
    monkeypatch.setenv('ATTENTION_RECOVERY_CONTROL_PATH',str(control))
    service,clock,calls=setup_service(personal,[Observation('test','unverified',tuple(observe_images(personal,1)),True,True)],time=NOW.isoformat())
    service.document.wechat.context_followup.enabled=False
    service.media_engine=lambda *args:([],{})
    asyncio.run(scan(service))
    with personal.connection() as c:
        batch=c.execute('SELECT id FROM wechat_batch LIMIT 1').fetchone()[0]
    service.analysis.enqueue(batch,service.context.read())
    with personal.connection(write=True) as c:
        c.execute('INSERT INTO wechat_media_analysis VALUES (?,?,?,?)',('synthetic-vision-budget',batch,int(vision),service.clock().isoformat()))
    return service,clock,calls


def test_vision_then_text_requires_two_requests_and_later_budget_recheck(personal,monkeypatch):
    service,clock,calls=queued_image(personal,monkeypatch)
    def counts():
        with personal.connection() as c:
            return {name:c.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('wechat_llm_attempt','wechat_analysis_job','raw_snapshot','event','task')}
    before=counts()
    plan=backlog_plan(personal,at=service.clock())
    assert plan['paid_plan']['jobs']==1
    assert plan['paid_plan']['vision_requests']==1
    assert plan['paid_plan']['text_requests']==1
    assert plan['paid_plan']['http_requests']==2
    assert plan['paid_plan']['reserved_tokens'] is None
    assert plan['paid_plan']['known_initial_reserved_tokens']>0
    assert plan['paid_plan']['jobs_requiring_post_vision_budget_check']==1
    assert plan['jobs'][0]['text_disclosure']['may_change_after_vision'] is True
    assert plan['paid_plan']['max_output_tokens']==2*plan['jobs'][0]['max_output_tokens']
    assert plan['jobs'][0]['request_plan']['vision_images']==1
    assert 'data:image' not in json.dumps(plan) and str(personal.path.parent) not in json.dumps(plan)
    assert before==counts() and calls==[]


def test_explicitly_disabled_vision_stays_text_only_even_for_legacy_adapter(personal,monkeypatch):
    service,clock,calls=queued_image(personal,monkeypatch,vision=False)
    plan=backlog_plan(personal,at=service.clock(),allow_vision=True)
    assert plan['paid_plan']['http_requests']==1
    assert plan['paid_plan']['vision_requests']==0
    assert plan['paid_plan']['reserved_tokens']>0
    assert plan['paid_plan']['jobs_requiring_post_vision_budget_check']==0
    assert plan['jobs'][0]['text_disclosure']['may_change_after_vision'] is False
    assert calls==[]


def test_invalid_vision_original_does_not_create_a_fake_complete_budget(personal,monkeypatch):
    service,clock,calls=queued_image(personal,monkeypatch)
    with personal.connection(write=True) as c:
        c.execute('UPDATE wechat_media SET width=1,height=100')
    plan=backlog_plan(personal,at=service.clock())
    assert plan['paid_plan']['vision_preflight_errors']==1
    assert plan['paid_plan']['reserved_tokens'] is None
    assert plan['jobs'][0]['request_plan']['vision_preflight_error']=='vision_image_budget'
    assert calls==[]


def test_historical_text_job_keeps_one_request_and_a_known_reservation(personal):
    service,clock,calls=setup_service(personal,[Observation('test','unverified',(ObservedMessage('所有同学请提交完整的实验报告与登记材料。'),),True,True)])
    service.document.wechat.context_followup.enabled=False
    asyncio.run(scan(service))
    with personal.connection() as c:batch=c.execute('SELECT id FROM wechat_batch LIMIT 1').fetchone()[0]
    service.analysis.enqueue(batch,service.context.read())
    plan=backlog_plan(personal,at=service.clock())
    assert plan['paid_plan']['jobs']==plan['paid_plan']['http_requests']==1
    assert plan['paid_plan']['vision_requests']==0
    assert plan['paid_plan']['reserved_tokens']==plan['jobs'][0]['reserved_tokens_if_requested']>0
    assert calls==[]


def test_review_plan_and_actual_mock_request_use_the_same_captured_message_order(personal):
    messages = (ObservedMessage('所有同学请提交完整的实验报告。'),
                ObservedMessage('请同时提交课程登记材料。'))
    service, _, calls = setup_service(personal, [Observation('test','unverified',messages,True,True)])
    service.document.wechat.context_followup.enabled = False
    asyncio.run(scan(service))
    with personal.connection(write=True) as c:
        batch = c.execute('SELECT id FROM wechat_batch').fetchone()[0]
        ids = sorted(r[0] for r in c.execute("SELECT message_id FROM wechat_batch_message WHERE batch_id=? AND role='primary'", (batch,)))
        assert len(ids) == 2
        # Ensure chronological ordinal differs from the table's identity order.
        for ordinal, message_id in enumerate(reversed(ids)):
            c.execute('UPDATE wechat_batch_message SET ordinal=? WHERE batch_id=? AND message_id=?',
                      (ordinal, batch, message_id))
    job = service.analysis.enqueue(batch, service.context.read())
    plan = backlog_plan(personal, at=service.clock())
    expected = list(reversed(ids))
    assert plan['jobs'][0]['primary_source_ids'] == expected
    assert asyncio.run(service.analysis.run_one(job)) is True
    payload = json.loads(calls[0]['messages'][-1]['content'].split('仅处理以下数据:\n', 1)[1])
    assert payload['primary_message_ids'] == ['m1','m2']
    with personal.connection() as connection:
        expected_text=[connection.execute('SELECT raw_text FROM wechat_message WHERE id=?',(mid,)).fetchone()[0] for mid in expected]
    assert [block['text'] for block in payload['blocks']] == expected_text
    assert plan['jobs'][0]['content_gate'] == 'approved_text_projection'
    assert plan['jobs'][0]['text_disclosure']['request_sha256'] == hashlib.sha256(encode(calls[0]).encode()).hexdigest()


def test_disclosure_summary_marks_ocr_and_context_without_exporting_text(personal):
    messages = (ObservedMessage('所有同学请提交完整的实验报告。',sender='测试姓名'),
                ObservedMessage('联系人 test@example.test，详情 https://example.test。',sender='测试老师'))
    service, _, calls = setup_service(personal, [Observation('test','unverified',messages,True,True)])
    service.document.wechat.context_followup.enabled = False
    asyncio.run(scan(service))
    with personal.connection(write=True) as c:
        batch = c.execute('SELECT id,snapshot_id FROM wechat_batch').fetchone()
        c.execute("INSERT INTO raw_source_block(raw_snapshot_id,block_id,source_type,ordinal,text,needs_review) VALUES (?,'synthetic-ocr','image_ocr',999,'测试图片派生文字',1)",(batch['snapshot_id'],))
    service.analysis.enqueue(batch['id'], service.context.read())
    result = backlog_plan(personal, at=service.clock())
    disclosure = result['jobs'][0]['text_disclosure']
    assert 'image_ocr' not in disclosure['block_source_types']
    assert disclosure['sender_fields_nonempty'] == 0
    assert disclosure['indicators']['email'] and disclosure['indicators']['url']
    assert disclosure['indicators_are_lower_bounds'] and disclosure['requires_content_review']
    serialized = json.dumps(result, ensure_ascii=False)
    assert '测试姓名' not in serialized and 'test@example.test' not in serialized and 'https://example.test' not in serialized
    assert calls == []


@pytest.mark.parametrize('success_kind', ['vision', 'text'])
def test_unknown_attempt_takes_priority_over_any_earlier_success(personal, monkeypatch, success_kind):
    service, clock, calls = queued_image(personal, monkeypatch)
    with personal.connection(write=True) as c:
        job = c.execute('SELECT id FROM wechat_analysis_job').fetchone()[0]
        for number, kind, status, unknown in ((1, success_kind, 'success', 0),
                (2, 'text', 'outcome_unknown', 1)):
            c.execute('INSERT INTO wechat_llm_attempt(id,job_id,attempt_number,kind,started_at,status,reserved_tokens,outcome_unknown) VALUES (?,?,?,?,?,?,?,?)',
                      (f'attempt-{number}',job,number,kind,service.clock().isoformat(),status,100,unknown))
    result = backlog_plan(personal, at=service.clock())
    assert result['jobs'][0]['decision'] == 'hold_unknown_no_retry'
    assert result['jobs'][0]['has_unknown_attempt'] and result['unknown_attempt_jobs'] == 1
    assert result['paid_plan']['jobs'] == 0 and calls == []


def test_successful_vision_alone_is_not_a_successful_text_analysis(personal, monkeypatch):
    service, clock, calls = queued_image(personal, monkeypatch)
    with personal.connection(write=True) as c:
        job = c.execute('SELECT id FROM wechat_analysis_job').fetchone()[0]
        c.execute('INSERT INTO wechat_llm_attempt(id,job_id,attempt_number,kind,started_at,status,reserved_tokens) VALUES (?,?,?,?,?,?,?)',
                  ('vision-attempt',job,1,'vision',service.clock().isoformat(),'success',100))
    result = backlog_plan(personal, at=service.clock())
    assert result['jobs'][0]['decision'] == 'hold_completed_vision_requires_review'
    assert result['jobs'][0]['request_plan'] is None
    assert result['jobs'][0]['reserved_tokens_if_requested'] is None
    assert result['paid_plan']['jobs'] == 0 and calls == []


def test_inactive_unknown_attempt_is_still_visible_in_the_dry_run(personal, monkeypatch):
    service, clock, calls = queued_image(personal, monkeypatch)
    with personal.connection(write=True) as c:
        job = c.execute('SELECT id FROM wechat_analysis_job').fetchone()[0]
        # Even if an inconsistent legacy flag says zero, a timeout is uncertain.
        c.execute('INSERT INTO wechat_llm_attempt(id,job_id,attempt_number,kind,started_at,status,reserved_tokens,outcome_unknown) VALUES (?,?,?,?,?,?,?,?)',
                  ('unknown-attempt',job,1,'text',service.clock().isoformat(),'timeout',100,0))
        c.execute("UPDATE wechat_batch SET status='ignored' WHERE id=(SELECT batch_id FROM wechat_analysis_job WHERE id=?)",(job,))
    result = backlog_plan(personal, at=service.clock())
    assert result['jobs'][0]['decision'] == 'skip_inactive_or_replaced'
    assert result['jobs'][0]['has_unknown_attempt'] and result['unknown_attempt_jobs'] == 1
    assert result['paid_plan']['jobs'] == 0 and calls == []
