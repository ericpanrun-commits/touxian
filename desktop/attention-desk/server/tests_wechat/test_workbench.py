import asyncio
import copy
import json

import httpx
import pytest

from server.ai_transport import DeepSeekTransport
from server.extraction_errors import ExtractionFailure
from server.extraction_models import ConfirmationInput
from server.review_store import ReviewStore
from server.tests_wechat.test_pipeline import setup_service,scan,output_for
from server.test_extraction_review import confirmation
from server.wechat_collect.inbox import list_candidates,set_ignored
from server.wechat_collect.json_output import parse_output
from server.wechat_collect.models import Observation,ObservedMessage


def two_items(payload):
    result=output_for(payload)
    other=copy.deepcopy(result['items'][0])
    other.update(id='notice2',title='另一条通知',category='notice',attention_level='fyi',action_required=False,actions=[])
    result['items'].append(other)
    return result

def two_actions(payload):
    result=two_items(payload)
    result['items'][1].update(title='报名实验培训',category='task',attention_level='action',action_required=True,
                             actions=[{**result['items'][0]['actions'][0],'id':'action2','title':'报名实验培训'}])
    return result


def populated(personal, response=two_items):
    observation=Observation('test','unverified',(ObservedMessage('请提交完整实验报告，并报名下周的实验培训',sender='老师'),),True,True)
    service,clock,calls=setup_service(personal,[observation],response=response)
    async def run():
        await scan(service)
        await service.process_queue()
    asyncio.run(run())
    return service,clock,calls


def confirm_one(personal,item):
    review=ReviewStore(personal)
    bundle=review.get(item['snapshot_id'])
    parsed=copy.deepcopy(bundle['extraction']['parsed_output'])
    parsed['events']=[event for event in parsed['events'] if event['id']==item['model_item_id']]
    request=confirmation(bundle,parsed)
    blocks={span['quote']:span['block_id'] for span in parsed['evidence']}
    for event in request.events:
        for action in event.actions:
            for evidence in action.evidence:
                evidence.block_id=blocks[evidence.quote]
    return review.confirm(item['snapshot_id'],request),request


def test_confirm_one_does_not_close_other_candidate_and_replay_is_idempotent(personal):
    service,_,calls=populated(personal,response=two_actions)
    inbox=list_candidates(personal)
    assert inbox['total']==2 and len(calls)==1
    first=inbox['items'][0]
    saved,request=confirm_one(personal,first)
    assert saved['review']['status']=='pending'
    assert saved['events'][0]['status']=='needs_action'
    remaining=list_candidates(personal)
    assert remaining['total']==1 and remaining['items'][0]['id']!=first['id']
    other=remaining['items'][0]
    confirm_one(personal,other)
    replay=ReviewStore(personal).confirm(first['snapshot_id'],request)
    assert replay['events'][0]['id']==saved['events'][0]['id']
    assert list_candidates(personal)['total']==0
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==2
        assert c.execute('SELECT count(*) FROM wechat_message').fetchone()[0]==1
    asyncio.run(scan(service))
    assert list_candidates(personal)['total']==0 and len(calls)==1


def test_ignored_candidate_can_be_restored_after_sibling_was_confirmed(personal):
    _,_,calls=populated(personal,response=two_actions)
    first,second=list_candidates(personal)['items']
    set_ignored(personal,first['id'],True,expected_version=first['review_version'])
    confirm_one(personal,second)
    ignored=list_candidates(personal,status='ignored')
    assert ignored['total']==1
    set_ignored(personal,first['id'],False)
    assert list_candidates(personal)['total']==1
    confirm_one(personal,first)
    assert len(calls)==1 and list_candidates(personal)['total']==0


def test_saved_drafts_for_different_candidates_do_not_overwrite_each_other(personal):
    populated(personal,response=two_actions)
    first,second=list_candidates(personal)['items']
    review=ReviewStore(personal)
    bundle=review.get(first['snapshot_id'])
    review.save_draft(first['snapshot_id'],bundle['version'],{'events':[{'id':first['model_item_id'],'title':'我修改的标题'}]})
    bundle=review.get(first['snapshot_id'])
    saved=review.save_draft(first['snapshot_id'],bundle['version'],{'events':[{'id':second['model_item_id'],'title':'第二条草稿'}]})
    assert {event['title'] for event in saved['draft']['events']}=={'我修改的标题','第二条草稿'}


@pytest.mark.parametrize('raw',[ '{"a":1,"a":2}', '{"a":1}\n{"b":2}', '{"a":NaN}', '{"a":'])
def test_local_repair_rejects_ambiguous_or_damaged_values(raw):
    with pytest.raises(ExtractionFailure): parse_output(raw)


def test_local_envelope_repair_preserves_values_and_needs_no_llm():
    value={'text':'逗号, } 与引号 " 和中文','number':1}
    for raw in ('```json\n'+json.dumps(value,ensure_ascii=False)+'\n```',json.dumps(value,ensure_ascii=False)+'\n说明：以上是结果。'):
        parsed,repaired=parse_output(raw)
        assert parsed==value and repaired


def test_provider_invalid_json_is_repaired_once_or_remains_in_review(personal):
    observation=Observation('test','unverified',(ObservedMessage('请核对并提交完整实验报告',sender='老师'),),True,True)
    service,_,calls=setup_service(personal,[observation])
    http_calls=[]
    def handler(request):
        http_calls.append(request)
        data=json.loads(request.content)
        payload=json.loads(data['messages'][-1]['content'].split('仅处理以下数据:\n')[1])
        raw=json.dumps(output_for(payload),ensure_ascii=False)+'\n说明：请人工核对。'
        return httpx.Response(200,json={'model':'deepseek-flash','choices':[{'message':{'content':raw},'finish_reason':'stop'}],'usage':{'total_tokens':100}})
    service.analysis.transport=DeepSeekTransport(httpx.MockTransport(handler))
    async def run():
        await scan(service)
        await service.process_queue()
    asyncio.run(run())
    assert len(http_calls)==1 and list_candidates(personal)['total']==1
    with personal.connection() as c:
        row=c.execute('SELECT status,json_repair_attempted FROM wechat_llm_attempt').fetchone()
        assert row['status']=='success' and row['json_repair_attempted']==1
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0


def test_ignore_during_model_request_does_not_reappear(personal):
    observation=Observation('test','unverified',(ObservedMessage('请核对并提交完整实验报告',sender='老师'),),True,True)
    service,_,_=setup_service(personal,[observation])
    async def run():
        entered=asyncio.Event()
        release=asyncio.Event()
        async def handler(request):
            data=json.loads(request.content)
            payload=json.loads(data['messages'][-1]['content'].split('仅处理以下数据:\n')[1])
            entered.set()
            await release.wait()
            return httpx.Response(200,json={'model':'deepseek-flash','choices':[{'message':{'content':json.dumps(output_for(payload),ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'total_tokens':100}})
        service.analysis.transport=DeepSeekTransport(httpx.MockTransport(handler))
        await scan(service)
        task=asyncio.create_task(service.process_queue())
        await entered.wait()
        assert list_candidates(personal)['total']==0
        item=list_candidates(personal,status='unresolved')['items'][0]
        set_ignored(personal,item['id'],True)
        release.set()
        await task
    asyncio.run(run())
    assert list_candidates(personal)['total']==0
    assert list_candidates(personal,status='ignored')['total']>=1


@pytest.mark.parametrize('repairable',[True,False])
def test_saved_pre_migration_json_failure_gets_only_one_local_repair(personal,repairable):
    service,_,calls=populated(personal)
    with personal.connection(write=True) as c:
        attempt=dict(c.execute('SELECT * FROM wechat_llm_attempt').fetchone())
        raw=attempt['raw_output']+'\n以上为分析结果。' if repairable else '{"broken":'
        c.execute("UPDATE wechat_llm_attempt SET status='invalid_json',json_repair_attempted=0,raw_output=?",(raw,))
        c.execute("UPDATE wechat_analysis_job SET status='error',error_code='invalid_json'")
        c.execute("UPDATE ai_extraction SET status='failed',error_json=?",(json.dumps({'code':'invalid_json'}),))
    assert service.analysis.repair_saved_failures()==int(repairable)
    assert service.analysis.repair_saved_failures()==0
    assert len(calls)==1  # Stored output recovery must never make a provider call.
    with personal.connection() as c:
        assert c.execute('SELECT json_repair_attempted FROM wechat_llm_attempt').fetchone()[0]==1
        extraction=dict(c.execute('SELECT status,error_json FROM ai_extraction').fetchone())
        assert extraction['status']==('needs_review' if repairable else 'failed')
        assert (extraction['error_json'] is None)==repairable
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0
