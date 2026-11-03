import asyncio
import json

from server.tests_wechat.test_pipeline import output_for,scan,setup_service
from server.user_context import RoleAssertion
from server.wechat_collect.models import Observation,ObservedMessage


def test_committed_messages_resume_staging_without_another_capture(personal,monkeypatch):
    from server.wechat_collect import service as module
    notice=ObservedMessage('请按要求提交完整实验报告与登记材料')
    service,_,calls=setup_service(personal,[Observation('test','unverified',(notice,),True,True)])
    original=module.prepare_batch
    def interrupted(*args,**kwargs):
        raise OSError('synthetic interruption after cursor commit')
    monkeypatch.setattr(module,'prepare_batch',interrupted)
    asyncio.run(scan(service))
    with personal.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM wechat_message').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM wechat_batch').fetchone()[0]==0
        assert c.execute('SELECT last_success_at FROM wechat_cursor').fetchone()[0]
    monkeypatch.setattr(module,'prepare_batch',original)
    asyncio.run(service.process_queue())
    asyncio.run(service.process_queue())
    assert len(calls)==1 and service.adapter.calls==1
    with personal.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM wechat_batch').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM event').fetchone()[0]==0


def test_missing_image_cannot_drop_neighboring_notification(personal):
    missing=ObservedMessage('',message_type='image',attachment_identity='missing-image',media_path=str(personal.path.parent/'not-owned.png'))
    notice=ObservedMessage('请按要求提交完整实验报告与登记材料')
    service,_,calls=setup_service(personal,[Observation('test','unverified',(missing,notice),True,True)])
    async def run():
        await scan(service)
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1
    with personal.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM wechat_batch_message').fetchone()[0]==2
        assert 'media_unavailable' in c.execute('SELECT warnings_json FROM wechat_batch').fetchone()[0]
        assert c.execute('SELECT COUNT(*) FROM event').fetchone()[0]==0


def test_role_restriction_only_applies_to_cited_notice(personal):
    restricted=ObservedMessage('请各班班长于今晚提交统计表')
    personal_notice=ObservedMessage('车辆工程专业学生请按要求提交完整实验报告')
    service,_,calls=setup_service(personal,[Observation('test','unverified',(restricted,personal_notice),True,True)])
    context=service.context.read()
    context.role_assertions={'班长':RoleAssertion(value='false',confirmed=True)}
    service.context.save(context,context.profile_version)
    async def run():
        await scan(service)
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1
    with personal.connection() as c:
        item=json.loads(c.execute("SELECT data_json FROM wechat_candidate WHERE model_item_id!='_pending'").fetchone()[0])
        assert item['relevant'] is True


def test_old_context_cannot_be_reissued_as_new_candidate(personal):
    old=ObservedMessage('历史通知：请按要求提交完整实验报告')
    new=ObservedMessage('新增通知：请报名参加本周学校实验室培训')
    def repeat_context(payload):
        old_message=next(m for m in payload['messages'] if m['context_only'])
        old_block=next(b for b in payload['blocks'] if b['block_id']==old_message['block_id'])
        return output_for({**payload,'messages':[old_message],'blocks':[old_block]})
    service,_,calls=setup_service(personal,[Observation('test','unverified',(old,new),True,True)],response=repeat_context)
    baseline,_=service.repository.begin_scan('baseline',1)
    service.repository.capture(baseline['id'],service.repository.chat('test'),Observation('test','unverified',(old,),True,True))
    service.repository.finish_scan(baseline['id'],'changed',{})
    # Mark the seed as already consumed; only the following observation is new input.
    from server.wechat_collect.filtering import FilterDecision
    service.repository.filter_message(service.repository.history('test')[0]['id'],FilterDecision('fast_reject','test_history'), 'fixture')
    async def run():
        await scan(service)
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1
    with personal.connection() as c:
        assert c.execute('SELECT error_code FROM wechat_analysis_job').fetchone()[0]=='context_only_item'
        assert c.execute('SELECT COUNT(*) FROM event').fetchone()[0]==0
