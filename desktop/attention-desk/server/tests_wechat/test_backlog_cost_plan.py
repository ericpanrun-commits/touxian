"""A read-only recovery plan must include conditional vision, without any spend."""
import asyncio
import json

from server.tests_wechat.test_pipeline import setup_service, scan
from server.tests_wechat.test_media_pipeline import NOW, observe_images
from server.wechat_collect.models import Observation, ObservedMessage
from server.wechat_collect.recovery import backlog_plan


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
