import asyncio
from datetime import timedelta

from server.review_store import ReviewStore
from server.tests_wechat.test_pipeline import setup_service,scan
from server.wechat_collect.models import Observation,ObservedMessage


def test_one_followup_combines_context_before_single_model_call(personal):
    intro=ObservedMessage('大家注意，这里有个重要通知',sender='老师')
    detail=ObservedMessage('请于明天提交完整实验报告',sender='老师')
    service,current,calls=setup_service(personal,[Observation('test','unverified',(intro,),True,True),Observation('test','unverified',(intro,detail),True)])
    async def run():
        await scan(service)
        await service.process_queue()
        assert len(calls)==0
        current[0]+=timedelta(seconds=120)
        await service.process_queue()
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1 and service.adapter.calls==2
    payload=calls[0]['messages'][-1]['content']
    assert intro.text in payload and detail.text in payload
    with personal.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM wechat_batch WHERE status='superseded'").fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM event').fetchone()[0]==0
    assert ReviewStore(personal).list()['total']==1


def test_cancel_pending_review_stops_paid_queue(personal):
    notice=ObservedMessage('请按要求提交本周实验报告和材料')
    service,_,calls=setup_service(personal,[Observation('test','unverified',(notice,),True,True)])
    async def run():
        await scan(service)
        with personal.connection() as c:
            snapshot=c.execute('SELECT snapshot_id FROM wechat_batch').fetchone()[0]
        review=ReviewStore(personal)
        bundle=review.get(snapshot)
        review.set_status(snapshot,bundle['version'],'cancelled')
        await service.process_queue()
    asyncio.run(run())
    assert calls==[]


def test_no_image_bytes_does_not_spend_text_tokens(personal):
    image=ObservedMessage('',message_type='image',attachment_identity='image-occurrence')
    service,current,calls=setup_service(personal,[Observation('test','unverified',(image,),True,True)])
    async def run():
        await scan(service)
        current[0]+=timedelta(seconds=120)
        # No certified followup: keep the original visible for manual review.
        service.adapter.is_real=True
        await service.process_queue()
    asyncio.run(run())
    assert calls==[]
    with personal.connection() as c:
        assert c.execute("SELECT status FROM wechat_candidate WHERE model_item_id='_pending'").fetchone()[0]=='media_pending'
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM wechat_candidate WHERE status='needs_review'").fetchone()[0]==0
