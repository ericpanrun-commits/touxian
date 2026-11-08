import asyncio
from datetime import timedelta
import json

import pytest

from server.extraction_errors import ExtractionFailure
from server.tests_wechat.conftest import personal
from server.tests_wechat.test_pipeline import setup_service,scan
from server.tests_wechat.test_deepseek_content_gate import grant,write_grant
from server.wechat_collect.deepseek_worker import CONTROLS,STATE,TextAnalysisWorker,single_worker,run,launch_if_authorized
from server.wechat_collect.models import ObservedMessage,Observation


def prepared(personal,monkeypatch,*,response=None):
    observed=ObservedMessage('请全体同学提交实验报告。',sender='老师',
        source_time='2026-10-22T10:00:00+08:00',time_reliability='reliable')
    service,clock,calls=setup_service(personal,[Observation('test','unverified',(observed,),True,True)],response=response)
    service.synthetic_content_grant=False
    service.document.wechat.context_followup.enabled=False
    service.document.wechat.allowed_chats=['合成测试群']
    asyncio.run(service.save_settings(service.document,service.document.revision))
    asyncio.run(scan(service))
    write_grant(personal,grant())
    (personal.path.parent/'recovery-controls.json').write_text(json.dumps({'version':1,'hold_cloud_uploads':True,'pause_paid_ai':True}))
    (personal.path.parent/CONTROLS).write_text(json.dumps({'version':1,'hold_cloud_uploads':True,'pause_paid_ai':False}))
    monkeypatch.setenv('ATTENTION_RECOVERY_CONTROL_PATH',str((personal.path.parent/CONTROLS).resolve()))
    worker=TextAnalysisWorker(personal.path.parent,clock=service.clock,settings=service.analysis.settings,
        transport=service.analysis.transport,unlimited_test_tokens=True)
    return service,clock,calls,worker,observed


def new_scan(service,clock,observations,text='请全体同学报名新的实验活动。'):
    clock[0]+=timedelta(minutes=15)
    observed=ObservedMessage(text,source_time=clock[0].isoformat(),time_reliability='reliable')
    service.adapter.observations=[Observation('test','unverified',(*observations,observed),True,True)]
    asyncio.run(scan(service))
    return observed


def test_existing_intake_queue_is_consumed_and_next_15_minute_increment_is_unique(personal,monkeypatch):
    service,clock,calls,worker,first=prepared(personal,monkeypatch)
    result=asyncio.run(worker.cycle())
    assert result['enqueued']==result['parsed']==result['calls']==1
    assert asyncio.run(worker.cycle())['calls']==0
    new_scan(service,clock,[first])
    assert asyncio.run(worker.cycle())['calls']==1
    assert asyncio.run(worker.cycle())['calls']==0 and len(calls)==2
    wire=json.dumps(calls[1],ensure_ascii=False)
    assert '请全体同学提交实验报告。' not in wire
    assert worker.policy.config.budget.tokens_daily is None
    assert service.settings.read().wechat.budget.tokens_daily==100000
    global_controls=json.loads((personal.path.parent/'recovery-controls.json').read_text())
    assert global_controls['pause_paid_ai'] and global_controls['hold_cloud_uploads']


def test_pre_activation_backlog_cannot_use_processing_slots(personal,monkeypatch):
    service,clock,calls,worker,first=prepared(personal,monkeypatch)
    with personal.connection() as connection:
        old_batch=connection.execute('SELECT id FROM wechat_batch').fetchone()[0]
        floor=connection.execute('SELECT max(ingest_seq) FROM wechat_message').fetchone()[0]
    old_job=service.analysis.enqueue(old_batch,service.context.read())
    write_grant(personal,grant(chat_watermarks={'test':floor}))
    new_scan(service,clock,[first])
    result=asyncio.run(worker.cycle())
    assert result['parsed']==result['calls']==1 and len(calls)==1
    with personal.connection() as connection:
        assert connection.execute('SELECT status FROM wechat_analysis_job WHERE id=?',(old_job,)).fetchone()[0]=='pending'
        assert connection.execute('SELECT count(*) FROM wechat_llm_attempt WHERE job_id=?',(old_job,)).fetchone()[0]==0


def test_saved_projected_response_recovers_without_another_request(personal,monkeypatch):
    _,_,calls,worker,_=prepared(personal,monkeypatch)
    commit=worker.queue._commit
    def fail(*args): raise OSError('synthetic failed commit')
    monkeypatch.setattr(worker.queue,'_commit',fail)
    assert asyncio.run(worker.cycle())['calls']==1
    monkeypatch.setattr(worker.queue,'_commit',commit)
    assert asyncio.run(worker.cycle())['calls']==0 and len(calls)==1
    with personal.connection() as connection:
        assert connection.execute('SELECT status FROM wechat_analysis_job').fetchone()[0]=='parsed'


def test_two_unknown_failures_open_persisted_circuit_without_retries(personal,monkeypatch):
    service,clock,calls,worker,first=prepared(personal,monkeypatch,response='timeout')
    assert asyncio.run(worker.cycle())['calls']==1
    new_scan(service,clock,[first])
    result=asyncio.run(worker.cycle())
    assert result['state']=='circuit_open' and len(calls)==2
    asyncio.run(run(worker,once=True))
    saved=json.loads((personal.path.parent/STATE).read_text())
    assert saved['state']=='circuit_open' and saved['calls']==0
    restarted=TextAnalysisWorker(personal.path.parent,clock=service.clock,settings=service.analysis.settings,
        transport=service.analysis.transport)
    assert asyncio.run(restarted.cycle())['state']=='circuit_open'
    assert len(calls)==2


@pytest.mark.parametrize('change',['global_hold','worker_hold','wrong_environment','revoked','removed_chat'])
def test_worker_stops_when_authorization_or_isolation_changes(personal,monkeypatch,change):
    service,_,calls,worker,_=prepared(personal,monkeypatch)
    if change=='global_hold':
        (personal.path.parent/'recovery-controls.json').write_text(json.dumps({'version':1,'hold_cloud_uploads':True,'pause_paid_ai':False}))
    elif change=='worker_hold':
        (personal.path.parent/CONTROLS).write_text(json.dumps({'version':1,'hold_cloud_uploads':True,'pause_paid_ai':True}))
    elif change=='wrong_environment': monkeypatch.delenv('ATTENTION_RECOVERY_CONTROL_PATH')
    elif change=='revoked': write_grant(personal,grant(enabled=False))
    elif change=='removed_chat':
        service.document.wechat.allowed_chats=[]
        asyncio.run(service.save_settings(service.document,service.document.revision))
        assert asyncio.run(worker.cycle())['calls']==0
        assert calls==[]
        return
    with pytest.raises(ExtractionFailure): asyncio.run(worker.cycle())
    assert calls==[]


def test_single_worker_is_an_actual_os_mutex_not_a_pid_file(personal):
    with single_worker(personal.path) as first:
        assert first
        with single_worker(personal.path) as second: assert not second
    with single_worker(personal.path) as restarted: assert restarted


def test_desktop_launch_is_inert_without_explicit_grant(personal,monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:pytest.fail('must not start without authorization'))
    assert launch_if_authorized(personal.path.parent) is None


@pytest.mark.parametrize('uncapped',[False,True])
def test_authorized_launch_is_hidden_and_keeps_parent_controls(personal,monkeypatch,uncapped):
    import os
    import subprocess
    prepared(personal,monkeypatch)
    controls=json.loads((personal.path.parent/CONTROLS).read_text())
    controls['unlimited_test_tokens']=uncapped
    (personal.path.parent/CONTROLS).write_text(json.dumps(controls))
    before=os.environ['ATTENTION_RECOVERY_CONTROL_PATH']
    calls=[]
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:calls.append((a,kw)) or 'synthetic-process')
    assert launch_if_authorized(personal.path.parent)=='synthetic-process'
    arguments,options=calls[0]
    assert 'server.wechat_collect.deepseek_worker' in arguments[0]
    assert ('--unlimited-test-tokens' in arguments[0]) is uncapped
    assert options['creationflags'] & subprocess.CREATE_NO_WINDOW
    assert os.environ['ATTENTION_RECOVERY_CONTROL_PATH']==before
    assert json.loads((personal.path.parent/'recovery-controls.json').read_text())['pause_paid_ai'] is True
