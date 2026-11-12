"""Targeted tests for the new lifecycle, retry, date and monitoring behavior."""
import asyncio
from datetime import timedelta
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from server.ai_transport import DeepSeekTransport
from server.extraction_models import ReviewDeadline
from server.notification_supervisor import Supervisor
from server.notification_health import snapshot
from server.personal_store import encode
from server.tests_wechat.test_pipeline import output_for
from server.tests_wechat.test_queue_starvation_regression import rig
from server.tests_wechat.test_notification_automation import enable,fresh,counts
from server.wechat_collect.deepseek_worker import run


@pytest.mark.parametrize('failure',[500,503,'connect'])
def test_definite_temporary_failures_retry_once_and_then_resume(rig,failure):
    enable(rig);fresh(rig);calls=[]
    def respond(request):
        calls.append(json.loads(request.content))
        if len(calls)==1:
            if failure=='connect':raise httpx.ConnectError('isolated handshake refused')
            return httpx.Response(failure,headers={'Retry-After':'30'})
        payload=json.loads(calls[-1]['messages'][-1]['content'].split('仅处理以下数据:\n')[1])
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output_for(payload),ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'total_tokens':100}})
    rig.worker.queue.transport=DeepSeekTransport(httpx.MockTransport(respond))
    assert asyncio.run(rig.worker.cycle())['calls']==1
    assert asyncio.run(rig.worker.cycle())['calls']==0
    rig.clock[0]+=timedelta(seconds=31)
    assert asyncio.run(rig.worker.cycle())['created']==1
    assert asyncio.run(rig.worker.cycle())['calls']==0
    assert len(calls)==2 and counts(rig)['event']==1


def test_persistent_failure_stops_after_two_attempts_without_blocking_next_notice(rig):
    enable(rig);fresh(rig);calls=[]
    rig.worker.queue.transport=DeepSeekTransport(httpx.MockTransport(lambda request:(calls.append(request) or httpx.Response(503,headers={'Retry-After':'1'}))))
    assert asyncio.run(rig.worker.cycle())['calls']==1
    rig.clock[0]+=timedelta(seconds=2)
    assert asyncio.run(rig.worker.cycle())['calls']==1
    rig.clock[0]+=timedelta(minutes=16)
    for _ in range(3):assert asyncio.run(rig.worker.cycle())['calls']==0
    assert len(calls)==2
    rig.worker.queue.transport=rig.service.analysis.transport
    fresh(rig,'请全体同学填写新的实验登记表。')
    assert asyncio.run(rig.worker.cycle())['created']==1


def test_unknown_read_timeout_is_retained_without_charging_again(rig):
    enable(rig);fresh(rig);calls=[]
    def timeout(request):calls.append(request);raise httpx.ReadTimeout('unknown delivery')
    rig.worker.queue.transport=DeepSeekTransport(httpx.MockTransport(timeout))
    asyncio.run(rig.worker.cycle());rig.clock[0]+=timedelta(minutes=20)
    for _ in range(3):assert asyncio.run(rig.worker.cycle())['calls']==0
    assert len(calls)==1
    with rig.personal.connection() as c:
        assert c.execute('SELECT status FROM wechat_analysis_job').fetchone()[0]=='outcome_unknown'


def test_real_scheduler_loop_consumes_three_periods_without_manual_cycle_calls(rig,monkeypatch):
    enable(rig);fresh(rig);periods=[]
    async def period(_):
        periods.append(1)
        if len(periods)==3:raise asyncio.CancelledError()
        fresh(rig,'请全体同学提交自动周期报告'+str(len(periods))+'。')
    monkeypatch.setattr('server.wechat_collect.deepseek_worker.asyncio.sleep',period)
    with pytest.raises(asyncio.CancelledError):asyncio.run(run(rig.worker))
    assert counts(rig)=={'event':3,'task':3,'wechat_llm_attempt':3}


def test_automatic_dates_are_source_verified_and_not_recorded_as_user_confirmed(rig):
    enable(rig);fresh(rig,'请全体同学明天提交实验报告。')
    def respond(request):
        p=json.loads(json.loads(request.content)['messages'][-1]['content'].split('仅处理以下数据:\n')[1]);out=output_for(p)
        t={'original_text':'明天','value':'2026-11-13','precision':'date','end_value':None,'range_precision':None,
           'timezone':'Asia/Shanghai','uncertain':False,'anchor_message_id':p['messages'][-1]['message_id'],'evidence_ids':['q1']}
        out['items'][0]['deadlines']=[{'id':'due','role':'提交截止','relation':'current','time':t}]
        out['items'][0]['actions'][0]['deadline_ids']=['due']
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(out,ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'total_tokens':100}})
    rig.worker.queue.transport=DeepSeekTransport(httpx.MockTransport(respond))
    assert asyncio.run(rig.worker.cycle())['created']==1
    with rig.personal.connection() as c:
        d=dict(c.execute('SELECT * FROM deadline').fetchone())
        assert d['value']=='2026-11-13' and d['confirmed_by_user']==0
        event_id=c.execute('SELECT id FROM event').fetchone()[0]
    detail=rig.personal.get_event(event_id)
    assert detail['deadlines'][0]['verified_automatically'] is True
    assert detail['priority']['deadlines'][d['id']]['confirmed'] is True
    from server.personal_priority import personal_priority
    assert personal_priority(detail,at=rig.clock[0])['deadlines'][d['id']]['state']=='upcoming'
    assert personal_priority(detail,at=rig.clock[0])['deadlines'][d['id']]['days_remaining']==1
    assert personal_priority(detail,at=rig.clock[0])['level']=='P1'
    with pytest.raises(ValidationError):
        ReviewDeadline.model_validate({'id':'d','role':'截止','value':'2026-11-13','precision':'date','confirmed_by_user':False})
    from server.cloud_sync.automatic_actions import build_automatic_preview
    from server.wechat_collect.deepseek_content_gate import leased_grant
    with leased_grant(rig.personal) as grant:
        with rig.personal.connection() as c:
            j=c.execute('SELECT id FROM wechat_analysis_job').fetchone()[0]
            value=build_automatic_preview(c,grant,j)
            from unittest.mock import patch
            with patch('server.planning.time.utc_now',return_value=rig.clock[0]+timedelta(days=8)):
                assert build_automatic_preview(c,grant,j)==value
    (rig.personal.path.parent/'automatic-date-preview.json').write_text(encode(value),'utf-8')
    from server.personal_models import DeadlineInput
    added=rig.personal.add_deadline(event_id,DeadlineInput(expected_version=detail['version'],label='另加未确认日期',
        value='2026-11-14',precision='date',confirmed_by_user=False))
    original=next(x for x in added['deadlines'] if x['id']==d['id'])
    other=next(x for x in added['deadlines'] if x['id']!=d['id'])
    assert original['verified_automatically'] is True
    assert not other.get('verified_automatically')
    assert added['priority']['deadlines'][other['id']]['state']=='needs_confirmation'
    edited=rig.personal.edit_deadline(event_id,d['id'],DeadlineInput(expected_version=added['version'],label=d['label'],
        value='2026-11-15',precision='date',confirmed_by_user=False))
    assert not next(x for x in edited['deadlines'] if x['id']==d['id']).get('verified_automatically')
    assert edited['priority']['deadlines'][d['id']]['state']=='needs_confirmation'


def test_supervision_restarts_actual_exit_with_persisted_backoff_but_never_kills_slow_work(tmp_path):
    clock=[1000.0];children=[]
    class Child:
        pid=42;code=None
        def poll(self):return self.code
        def terminate(self):pytest.fail('A slow API request must not be killed')
    def launch():child=Child();children.append(child);return child
    sup=Supervisor(tmp_path,{'analysis':launch},clock=lambda:clock[0])
    sup.tick();clock[0]+=3600;sup.tick();assert len(children)==1
    for i in range(5):
        children[-1].code=1;sup.tick()
        assert sup.next_at['analysis']>clock[0]
        if i<4:clock[0]=sup.next_at['analysis'];sup.tick()
    restarted=Supervisor(tmp_path,{'analysis':launch},clock=lambda:clock[0])
    assert restarted.tick()['analysis']['state']=='restart_backoff' and len(children)==5


def test_health_distinguishes_legitimate_long_cycles_from_stale_worker_and_backlog(rig):
    enable(rig);directory=rig.personal.path.parent
    (directory/'cloud-sync/notification-automation').mkdir(parents=True,exist_ok=True)
    def status(age):
        t=(rig.clock[0]-timedelta(seconds=age)).isoformat()
        (directory/'deepseek-worker-state.json').write_text(encode({'last_tick':t,'state':'ready','calls':0,'pending_runnable':1,'stalled_cycles':2,'alert_required':False}),'utf-8')
        (directory/'cloud-sync/notification-automation/status.json').write_text(encode({'at':t,'state':'idle'}),'utf-8')
    status(180);assert not snapshot(directory,rig.clock[0])['alert_required']
    status(301);assert snapshot(directory,rig.clock[0])['alert_required']
    assert 'analysis_heartbeat_stale' in snapshot(directory,rig.clock[0])['problems']
