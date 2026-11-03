import asyncio
import json
from datetime import timedelta

import pytest

from server.documents.service import DocumentService
from server.extraction_models import ConfirmationInput
from server.extraction_service import ExtractionService
from server.personal_store import PersonalError
from server.review_store import ReviewStore
from server.wechat_collect.models import Observation,ObservedMessage
from server.tests_wechat.test_pipeline import setup_service,scan,Settings


def notice(text='请按要求提交实验报告和完整材料'):
    return ObservedMessage(text,sender='老师')


def test_daily_budget_keeps_queue_and_resets_in_shanghai(personal):
    old=notice()
    service,current,calls=setup_service(personal,[Observation('test','unverified',(old,),True,True)],time='2026-10-22T18:00:00+08:00')
    service.policy.config.budget.http_daily=1
    async def run():
        await scan(service)
        await service.process_queue()
        service.adapter.observations=[Observation('test','unverified',(old,notice('新增通知：请报名参加学院实验室活动')),True)]
        await scan(service)
        await service.process_queue()
        assert len(calls)==1
        assert service.repository.stats('2026-10-22')['pending_candidates']==1
        current[0]=(current[0]+timedelta(days=1)).replace(hour=6)
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==2
    assert service.repository.stats('2026-10-22')['llm_calls']==1
    assert service.repository.stats('2026-10-23')['llm_calls']==1
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_message').fetchone()[0]==2
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0


def test_only_explicit_confirmation_creates_task_and_ignores_ai_p0(personal):
    service,_,calls=setup_service(personal,[Observation('test','unverified',(notice(),),True,True)])
    async def run():
        await scan(service)
        await service.process_queue()
    asyncio.run(run())
    with personal.connection() as c:
        snapshot=c.execute('SELECT snapshot_id FROM wechat_batch').fetchone()[0]
    review=ReviewStore(personal)
    bundle=review.get(snapshot)
    projected=bundle['extraction']['parsed_output']
    event=projected['events'][0]
    spans={q['id']:{'block_id':q['block_id'],'quote':q['quote'],'start_offset':q['start'],'end_offset':q['end']} for q in projected['evidence']}
    payload={'id':event['id'],'title':event['title'],'summary':event['summary'],'category':event['category'],'relevance':'yes','participation':'yes','notice_kind':event['notice_kind'],
             'actions':[{'id':a['id'],'title':a['title'],'kind':a['kind'],'evidence':[spans[q] for q in a['evidence_ids']]} for a in event['actions']]}
    confirmed=review.confirm(snapshot,ConfirmationInput(expected_version=bundle['version'],extraction_id=bundle['extraction_id'],acknowledged_warnings=True,events=[payload]))
    saved=confirmed['events'][0]
    assert len(saved['tasks'])==1 and saved['priority']['level']=='P2'
    assert saved['evidence'][0]['block_id'].startswith('WX_')
    with personal.connection() as c:
        assert c.execute("SELECT status FROM wechat_candidate WHERE model_item_id!='_pending'").fetchone()[0]=='accepted'
    # Idempotent confirmation does not create another task or invoke the model.
    review.confirm(snapshot,ConfirmationInput(expected_version=bundle['version'],extraction_id=bundle['extraction_id'],acknowledged_warnings=True,events=[payload]))
    assert len(calls)==1


def test_generic_analysis_and_file_parsing_cannot_bypass_wechat_gate(personal):
    service,current,calls=setup_service(personal,[Observation('test','unverified',(notice(),),True,True)])
    asyncio.run(scan(service))
    with personal.connection() as c:
        snapshot=c.execute('SELECT snapshot_id FROM wechat_batch').fetchone()[0]
    current[0]=current[0].replace(hour=1)
    generic=ExtractionService(personal,Settings())
    with pytest.raises(PersonalError,match='微信来源必须'):
        asyncio.run(generic.start(snapshot))
    documents=DocumentService(personal)
    try:
        with pytest.raises(PersonalError) as error:
            documents.start(snapshot,['not-a-real-attachment'])
        assert error.value.code=='wechat_media_pipeline_required'
    finally:
        documents.close()
    assert calls==[]


def test_fabricated_quote_never_creates_formal_data(personal):
    from server.tests_wechat.test_pipeline import output_for
    def bad(payload):
        output=output_for(payload)
        output['evidence'][0]['quote']='不存在的原文'
        return output
    service,_,calls=setup_service(personal,[Observation('test','unverified',(notice(),),True,True)],response=bad)
    async def run():
        await scan(service)
        await service.process_queue()
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0
        assert c.execute('SELECT error_code FROM wechat_analysis_job').fetchone()[0]=='invalid_evidence'


def test_saved_response_recovery_never_repeats_http(personal,monkeypatch):
    service,_,calls=setup_service(personal,[Observation('test','unverified',(notice(),),True,True)])
    original=service.analysis._commit
    def failure(*args):
        raise OSError('synthetic write interruption after model response')
    monkeypatch.setattr(service.analysis,'_commit',failure)
    async def run():
        await scan(service)
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1
    monkeypatch.setattr(service.analysis,'_commit',original)
    asyncio.run(service.process_queue())
    assert len(calls)==1
    with personal.connection() as c:
        assert c.execute('SELECT status FROM wechat_analysis_job').fetchone()[0]=='parsed'


@pytest.mark.parametrize('phrase,source,expected',[
    ('今天','2026-10-22T09:00','2026-10-22'),('明天','2026-10-22T09:00','2026-10-23'),
    ('后天','2026-10-22T09:00','2026-10-24'),('本周五','2026-10-22T09:00','2026-10-23'),
    ('下周一','2026-10-22T09:00','2026-10-26'),('本月底','2026-10-22T09:00','2026-10-31'),
    ('10月30号','2026-10-22T09:00','2026-10-30'),('今晚12点','2026-10-22T09:00',None),
    ('明天',None,None),('10月1号','2026-10-22T09:00',None)])
def test_relative_dates_never_anchor_on_capture_time(phrase,source,expected):
    from server.wechat_collect.analysis import resolve_phrase
    assert resolve_phrase(phrase,source)==expected


def test_dates_convert_reliable_offsets_to_shanghai_and_validate_ranges():
    from server.wechat_collect.analysis import resolve_phrase,resolve_range
    assert resolve_phrase('今天','2026-10-21T18:00:00+00:00')=='2026-10-22'
    assert resolve_phrase('明天','invalid-source') is None
    assert resolve_range('本周内','2026-10-22T09:00','date')==('2026-10-19','2026-10-25')
    assert resolve_range('2026年10月30日14:00至16:00',None,'minute')==('2026-10-30T14:00','2026-10-30T16:00')
    assert resolve_range('从今天起至另行通知','2026-10-22T09:00','date') is None
