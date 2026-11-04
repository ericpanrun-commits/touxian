"""Free staging and new work must continue while paid historical jobs are held."""
import asyncio
from datetime import timedelta
import json

from server.tests_wechat.test_pipeline import setup_service, scan
from server.tests_wechat.test_multigroup import make_service
from server.wechat_collect.health import wechat_health
from server.wechat_collect.models import Observation, ObservedMessage


def test_database_empty_scan_resumes_committed_staging_without_model_or_recapture(personal, tmp_path, monkeypatch):
    from server.wechat_collect import service as module
    service, sources, calls = make_service(personal, tmp_path, count=1)
    sources['g0'].add('作业：请提交实验报告以及完整登记材料')
    original = module.prepare_batch
    def interrupted(*args, **kwargs):
        raise OSError('synthetic staging failure after durable capture')
    monkeypatch.setattr(module, 'prepare_batch', interrupted)
    asyncio.run(scan(service))
    first_health = wechat_health(personal, service.document, service.clock())['sources'][0]
    assert first_health['collection_state'] == 'healthy'
    assert first_health['latest_error_stage'] == 'staging'
    assert first_health['pending_staging_messages'] == 1
    assert first_health['consecutive_failures'] == 0
    monkeypatch.setattr(module, 'prepare_batch', original)
    async def resume():
        await scan(service)
        assert service.last_result == 'no_change'
        await service.process_queue()
        await service.process_queue()
    asyncio.run(resume())
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_message').fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM wechat_batch').fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM raw_item').fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0] == 0
    assert calls == []
    assert wechat_health(personal, service.document, service.clock())['sources'][0]['pending_staging_messages'] == 0


def test_local_only_capture_and_staging_repair_work_with_paid_and_cloud_holds(personal, tmp_path, monkeypatch):
    control = tmp_path / 'recovery-controls.json'
    control.write_text(json.dumps({'version':1,'hold_cloud_uploads':True,'pause_paid_ai':True}))
    monkeypatch.setenv('ATTENTION_RECOVERY_CONTROL_PATH',str(control))
    from server.wechat_collect import service as module
    service,sources,calls=make_service(personal,tmp_path,count=1)
    sources['g0'].add('作业：请提交实验报告以及完整登记材料')
    original=module.prepare_batch
    def interrupted(*args,**kwargs):
        raise OSError('synthetic durable capture interruption')
    monkeypatch.setattr(module,'prepare_batch',interrupted)
    asyncio.run(scan(service))
    monkeypatch.setattr(module,'prepare_batch',original)
    async def resume():
        await scan(service)
        await service.process_queue()
        await scan(service)
        await service.process_queue()
    asyncio.run(resume())
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_message').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM raw_item').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM wechat_batch').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM task').fetchone()[0]==0
    assert calls==[]


def test_held_history_does_not_starve_new_work_or_inflate_active_job_count(personal):
    first = ObservedMessage('请按要求提交完整实验报告与登记材料')
    second = ObservedMessage('所有车辆工程同学请办理校园卡信息核验并提交确认表')
    service, current, calls = setup_service(personal, [
        Observation('test','unverified',(first,),True,True),
        Observation('test','unverified',(first,second),True,True)])
    service.document.wechat.context_followup.enabled = False
    service.document.wechat.allowed_chats = [service.document.wechat.priority_chats[0].name]
    asyncio.run(scan(service))
    with personal.connection() as c:
        old_batch = c.execute('SELECT id FROM wechat_batch').fetchone()[0]
    held = [service.analysis.enqueue(old_batch, service.context.read(), force=True) for _ in range(4)]
    with personal.connection(write=True) as c:
        for index, job in enumerate(held):
            c.execute('UPDATE wechat_analysis_job SET created_at=? WHERE id=?',
                ((current[0] - timedelta(days=5-index)).isoformat(), job))
    async def collect_new():
        await scan(service)
        await service.process_queue()
    asyncio.run(collect_new())
    assert len(calls) == 1
    with personal.connection() as c:
        assert [r[0] for r in c.execute('SELECT job_id FROM wechat_llm_attempt')] and not c.execute(
            'SELECT 1 FROM wechat_llm_attempt WHERE job_id IN (?,?,?,?)', held).fetchone()
        assert c.execute("SELECT count(*) FROM wechat_analysis_job WHERE status='parsed'").fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM event').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM task').fetchone()[0] == 0
    # Three replaced jobs remain historical records, not three extra pending jobs.
    health = wechat_health(personal, service.document, service.clock())
    assert health['sources'][0]['pending_analysis'] == 1
