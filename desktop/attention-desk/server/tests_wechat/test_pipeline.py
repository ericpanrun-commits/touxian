import asyncio
import json
import hashlib
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from server.ai_settings import AIConfig
from server.ai_transport import DeepSeekTransport
from server.personal_store import PersonalError
from server.review_store import ReviewStore
from server.user_context import RoleAssertion
from server.wechat_collect.adapter import FakeAdapter
from server.wechat_collect.api import router
from server.wechat_collect.config import ChatConfig, SettingsDocument, WeChatConfig
from server.wechat_collect.models import Observation, ObservedMessage
from server.wechat_collect.service import WeChatService


class Settings:
    def _read(self): return {},AIConfig()
    def resolved(self): return AIConfig(),SecretStr('mock-only-key')


def output_for(payload,*,role=None,confidence=0.95):
    block=payload['blocks'][-1]
    message=payload['messages'][-1]
    quote=block['text']
    return {'schema_version':'wechat.v1','batch_id':payload['batch_id'],
            'items':[{'id':'notice1','is_school_notice':True,'important':True,'relevant':True,'action_required':True,'category':'task',
                      'attention_level':'action','information_complete':True,'await_more_context':False,'title':'提交材料','summary':quote,'start_time':None,'end_time':None,'deadline':None,
                      'location':None,'action':'提交材料','target_people':[role] if role else [],
                      'priority':'P0','confidence':confidence,'evidence_message_ids':[message['message_id']],
                      'reason':'原文有要求','time_text':'','needs_review':False,'participation':'required','notice_kind':'new',
                      'related_candidate_id':None,'deadlines':[],
                      'actions':[{'id':'action1','title':'提交材料','kind':'explicit','deadline_ids':[],'evidence_ids':['q1']}],
                      'evidence_mapping':[{'field':'action','evidence_ids':['q1']}],'uncertainties':[]}],
            'evidence':[{'id':'q1','message_id':message['message_id'],'block_id':block['block_id'],'quote':quote,'start':0,'end':len(quote)}]}


def setup_service(personal,observations,*,time='2026-10-22T10:00:00+08:00',response=None,activity_guard=None):
    current=[datetime.fromisoformat(time)]
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        if response=='timeout':
            raise httpx.ReadTimeout('mock interruption')
        payload=json.loads(calls[-1]['messages'][-1]['content'].split('仅处理以下数据:\n')[1])
        data=response(payload) if callable(response) else output_for(payload)
        return httpx.Response(200,json={'model':'deepseek-flash','choices':[{'message':{'content':json.dumps(data,ensure_ascii=False)},'finish_reason':'stop'}], 'usage':{'total_tokens':100,'prompt_tokens':80,'completion_tokens':20}})
    service=WeChatService(personal,Settings(),adapter=FakeAdapter(observations),clock=lambda:current[0],
                          transport=DeepSeekTransport(httpx.MockTransport(handler)),activity_guard=activity_guard)
    # Only this fixture grants its fictional source text. Production never
    # derives a grant from stored messages or from a configured API key.
    service.synthetic_content_grant=True
    document=SettingsDocument(wechat=WeChatConfig(enabled=True,priority_chats=[ChatConfig(id='test',name='合成测试群')]))
    asyncio.run(service.save_settings(document,1))
    return service,current,calls


async def scan(service):
    result=await service.request_scan(manual=True,request_key=str(uuid4()))
    await asyncio.gather(*list(service.scan_tasks))
    if getattr(service,'synthetic_content_grant',False):
        from server.wechat_collect.deepseek_content_gate import FILENAME, POLICY
        with service.personal.connection() as connection:
            rows=connection.execute("SELECT * FROM wechat_message m WHERE message_type='text' AND EXISTS (SELECT 1 FROM wechat_batch_message bm WHERE bm.message_id=m.id AND bm.role='primary')").fetchall()
            chats=connection.execute('SELECT id FROM wechat_chat').fetchall()
        if rows:
            grant={'version':1,'enabled':True,'authorization_id':'offline-synthetic-fixture',
                'destination':'https://api.deepseek.com','policy':POLICY,'mode':'reviewed',
                'not_before':'2000-01-01T00:00:00+08:00','chat_watermarks':{r['id']:0 for r in chats},
                'reviewed_samples':[{'original_sha256':hashlib.sha256(r['raw_text'].encode()).hexdigest(),
                    'text':r['raw_text'],'source_time':r['source_time'],'time_reliability':r['time_reliability']} for r in rows]}
            (service.personal.path.parent/FILENAME).write_text(json.dumps(grant,ensure_ascii=False),encoding='utf8')
    return result


def test_100_no_change_zero_llm(personal):
    message=ObservedMessage('用于测试增量边界的历史消息，不应重复分析',sender='老师')
    observation=Observation('test','unverified',(message,),True,True)
    service,clock,calls=setup_service(personal,[observation])
    first,_=service.repository.begin_scan('baseline',1)
    service.repository.capture(first['id'],service.repository.chat('test'),observation)
    service.repository.finish_scan(first['id'],'changed',{})
    with personal.connection(write=True) as c:
        c.execute("UPDATE wechat_message_state SET status='parsed'")
    async def run():
        for _ in range(100):
            await scan(service)
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==0 and service.repository.stats('2026-10-22')['llm_calls']==0
    assert service.repository.scans(100)['items'][0]['status']=='no_change'
    with personal.connection() as c:
        assert c.execute("SELECT count(*) FROM wechat_scan WHERE status='no_change'").fetchone()[0]==100


def test_normal_chat_never_calls_model(personal):
    observation=Observation('test','unverified',(ObservedMessage('收到'),ObservedMessage('哈哈哈哈')),True,True)
    service,_,calls=setup_service(personal,[observation])
    async def run():
        await scan(service)
        await service.process_queue()
    asyncio.run(run())
    assert calls==[]
    with personal.connection() as c:
        assert c.execute("SELECT count(*) FROM wechat_message_state WHERE status='filtered'").fetchone()[0]==2


@pytest.mark.parametrize('role_value,expected',[('false',False),('unknown',None),('true',True)])
def test_role_gate_candidates_only(personal,role_value,expected):
    message=ObservedMessage('请各班班长于今晚提交统计表',sender='老师',source_time='2026-10-22T09:00',time_reliability='reliable')
    service,current,calls=setup_service(personal,[Observation('test','unverified',(message,),True,True)],response=lambda p:output_for(p,role='班长'))
    context=service.context.read()
    context.role_assertions={'班长':RoleAssertion(value=role_value,confirmed=role_value!='unknown')}
    service.context.save(context,context.profile_version)
    async def run():
        await scan(service)
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1
    assert calls[0]['thinking']=={'type':'disabled'}
    with personal.connection() as c:
        item=json.loads(c.execute("SELECT data_json FROM wechat_candidate WHERE model_item_id!='_pending'").fetchone()[0])
        assert item['relevant'] is expected
        if expected is not True:
            assert not item['action_required'] and not item['needs_review']
            assert item['attention_level']!='action'
            assert c.execute("SELECT count(*) FROM wechat_candidate WHERE status='needs_review'").fetchone()[0]==0
        assert all(c.execute('SELECT count(*) FROM '+t).fetchone()[0]==0 for t in ('event','task','deadline','reminder'))
        snapshot=c.execute('SELECT snapshot_id FROM wechat_batch').fetchone()[0]
    bundle=ReviewStore(personal).get(snapshot)
    assert bundle['snapshot']['wechat']['messages'][0]['sender']=='老师'
    if expected is True: assert bundle['extraction']['parsed_output']['events'][0]['actions']
    else: assert bundle['extraction']['parsed_output']['events']==[]


def test_api_timeout_is_not_automatically_retried(personal):
    observation=Observation('test','unverified',(ObservedMessage('请于2026年10月30日前提交完整材料'),),True,True)
    service,_,calls=setup_service(personal,[observation],response='timeout')
    async def run():
        await scan(service)
        await service.process_queue()
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1
    with personal.connection() as c:
        assert c.execute('SELECT status FROM wechat_analysis_job').fetchone()[0]=='outcome_unknown'
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0


def test_quiet_hours_zero_adapter_and_model(personal):
    service,_,calls=setup_service(personal,[],time='2026-10-22T02:00:00+08:00')
    async def run():
        with pytest.raises(PersonalError,match='quiet'):
            await scan(service)
        await service.process_queue()
    asyncio.run(run())
    assert service.adapter.calls==0 and calls==[]


def test_activity_and_closed_wechat_do_not_crash(personal):
    service,_,calls=setup_service(personal,[PersonalError('不可用','unavailable',503)],activity_guard=lambda:False)
    asyncio.run(scan(service))
    assert service.adapter.calls==0 and service.repository.scans()['items'][0]['status']=='deferred'
    service.activity_guard=lambda:True
    asyncio.run(scan(service))
    assert service.repository.scans()['items'][0]['status']=='error' and calls==[]


def test_status_has_no_probe_side_effect_and_origin_guard(personal):
    service,_,calls=setup_service(personal,[])
    app=FastAPI()
    app.state.wechat_service=service
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get('/api/personal/wechat/status').status_code==200
        assert client.put('/api/personal/wechat/settings',json=service.document.model_dump(),headers={'Origin':'https://evil.example'}).status_code==403
        assert client.post('/api/personal/wechat/chats/test/bind',json={}).status_code==409
    assert service.adapter.calls==0 and calls==[]
