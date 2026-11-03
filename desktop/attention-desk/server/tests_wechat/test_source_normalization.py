"""Deterministic, source-bound spelling recovery; no fuzzy evidence matching."""
import copy
import json

import pytest

from server.extraction_errors import ExtractionFailure
from server.tests_wechat.test_pipeline import output_for,setup_service,scan
from server.wechat_collect.analysis import validate_output
from server.wechat_collect.models import Observation,ObservedMessage


def fixture(text='作业通知：提交材料',block_id='WX_m1'):
    message={'message_id':'m1','block_id':block_id,'raw_text':text,'time_reliability':'reliable','source_time':'2026-10-22T10:00:00+08:00'}
    block={'block_id':block_id,'text':text,'source_type':'text'}
    payload={'batch_id':'b1','messages':[message],'blocks':[block]}
    return output_for(payload),{'id':'b1','primary_ids':['m1']},{'blocks':[block],'wechat':{'messages':[message],'warnings':[]}}


def test_unique_exact_quote_repairs_only_counting_error():
    output,batch,snapshot=fixture()
    output['evidence'][0].update(start=1,end=999)
    parsed,warnings=validate_output(json.dumps(output),batch,snapshot,{})
    assert parsed['evidence'][0]['start']==0
    assert parsed['evidence'][0]['end']==len(snapshot['blocks'][0]['text'])
    assert 'exact_quote_offset_normalized' in warnings
    assert parsed['items'][0]['needs_review']


@pytest.mark.parametrize('change',[{'quote':'不存在的改写'}, {'message_id':'other_group'}, {'block_id':'WX_other_group'}])
def test_never_repairs_fabricated_quote_or_other_group(change):
    output,batch,snapshot=fixture()
    output['evidence'][0].update(change)
    with pytest.raises(ExtractionFailure): validate_output(json.dumps(output),batch,snapshot,{})


def test_ambiguous_quote_stays_invalid():
    output,batch,snapshot=fixture('提交材料，提交材料')
    output['evidence'][0].update(quote='提交材料',start=1,end=2)
    with pytest.raises(ExtractionFailure): validate_output(json.dumps(output),batch,snapshot,{})

def test_overlapping_repeated_quotes_are_ambiguous_too():
    output,batch,snapshot=fixture('哈哈哈')
    output['evidence'][0].update(quote='哈哈',start=1,end=2)
    with pytest.raises(ExtractionFailure): validate_output(json.dumps(output),batch,snapshot,{})


def test_explicit_derived_block_alias_maps_to_its_own_message():
    output,batch,snapshot=fixture(block_id='WX_m1_MEDIA_0')
    output['items'][0]['evidence_message_ids']=['m1_MEDIA_0']
    output['evidence'][0]['message_id']='m1_MEDIA_0'
    parsed,warnings=validate_output(json.dumps(output),batch,snapshot,{})
    assert parsed['items'][0]['evidence_message_ids']==['m1']
    assert parsed['evidence'][0]['message_id']=='m1'
    assert 'message_block_alias_normalized' in warnings


def test_evidence_message_alias_only_maps_when_unique_and_cited():
    output,batch,snapshot=fixture()
    output['items'][0]['actions'][0]['evidence_ids']=['m1']
    parsed,warnings=validate_output(json.dumps(output),batch,snapshot,{})
    assert parsed['items'][0]['actions'][0]['evidence_ids']==['q1']
    assert 'unique_evidence_id_normalized' in warnings
    output['evidence'].append({**output['evidence'][0],'id':'q2'})
    with pytest.raises(ExtractionFailure): validate_output(json.dumps(output),batch,snapshot,{})


def test_date_spelling_is_lossless_and_still_source_backed():
    output,batch,snapshot=fixture('请在2026年10月24日17:00前提交材料')
    output['items'][0]['deadline']={'value':'2026-10-24T17:00:00+08:00','end_value':None,'precision':'minute','range_precision':None,'timezone':'Asia/Shanghai','original_text':'2026年10月24日17:00','anchor_message_id':'m1','uncertain':False,'evidence_ids':['q1']}
    parsed,warnings=validate_output(json.dumps(output),batch,snapshot,{})
    assert parsed['items'][0]['deadline']['value']=='2026-10-24T17:00'
    assert 'civil_datetime_normalized' in warnings
    output['items'][0]['deadline']['value']='2026-10-25T17:00:00+08:00'
    parsed,warnings=validate_output(json.dumps(output),batch,snapshot,{})
    assert parsed['items'][0]['deadline']['precision']=='unknown'
    for value in ('2026-10-24T17:00:01+08:00','2026-10-24T17:00:00+00:00'):
        output['items'][0]['deadline']['value']=value
        with pytest.raises(ExtractionFailure): validate_output(json.dumps(output),batch,snapshot,{})


def test_no_items_can_ignore_unused_evidence_without_creating_candidate():
    output,batch,snapshot=fixture()
    output['items']=[]; output['evidence'][0]['quote']='wrong text'
    parsed,warnings=validate_output(json.dumps(output),batch,snapshot,{})
    assert parsed['items']==[] and parsed['evidence']==[]
    assert 'unused_evidence_ignored' in warnings


def test_failed_validation_preserves_actual_api_usage(personal):
    import asyncio
    def bad(payload):
        output=output_for(payload); output['evidence'][0]['quote']='not a real quote'
        return output
    service,_,calls=setup_service(personal,[Observation('test','unverified',(ObservedMessage('作业通知：请提交报告'),),True,True)],response=bad)
    async def run(): await scan(service); await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1
    with personal.connection() as c:
        row=c.execute('SELECT status,token_usage_json FROM wechat_llm_attempt').fetchone()
        assert row['status']=='invalid_evidence'
        assert json.loads(row['token_usage_json'])['total_tokens']==100
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0
