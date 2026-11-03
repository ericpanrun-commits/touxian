"""Isolated real HTTP/UI pipeline, synthetic messages and a mock DeepSeek provider."""
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime,timedelta
import asyncio
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
OUTPUT=Path(os.environ.get('ATTENTION_QA_OUTPUT') or __import__('tempfile').mkdtemp(prefix='attention-wechat-ui-'))
DATA=OUTPUT/('wechat-fixture-'+uuid4().hex[:8])
DATA.mkdir(parents=True)
os.environ.update(ATTENTION_PERSONAL_DB=str(DATA/'personal.sqlite3'),ATTENTION_OFFICIAL_POLL='0',ATTENTION_INBOX_POLL='0',ATTENTION_API_PORT='18765',AUTO_SYNC_ENABLED='0')
sys.path.insert(0,str(ROOT))

import httpx
from pydantic import SecretStr
from server.ai_settings import AIConfig
from server.ai_transport import DeepSeekTransport
from server.main import app
from server.wechat_collect.adapter import FakeAdapter
from server.wechat_collect.config import WeChatConfig,SettingsDocument,ChatConfig
from server.wechat_collect.models import Observation,ObservedMessage
from server.wechat_collect.service import WeChatService

NOW=datetime.fromisoformat('2026-10-22T10:00:00+08:00')
POLICY_NOW=datetime.now(NOW.tzinfo)
if POLICY_NOW.hour<6: POLICY_NOW=POLICY_NOW.replace(hour=10)

def fixture_clock():
    value=datetime.now(NOW.tzinfo)
    return value if value.hour>=6 else value.replace(hour=10)
GROUP='合成测试群（非真实聊天）'
calls=[]
fail_next=False
messages=[ObservedMessage(text,sender='合成班级老师',source_time=(NOW+timedelta(seconds=index)).isoformat(),time_reliability='reliable',sender_reliability='reliable') for index,text in enumerate([
    '【合成测试·任务】请在2026年10月24日17:00前提交实验报告，地点：工程楼201。',
    '【合成测试·截止】2026年10月25日是资料提交截止日期，请核对登记信息。',
    '【合成测试·活动】2026年10月26日14:00在工程楼301召开班会，请准时参加。',
    '【合成测试·通知】学院资料领取须知已更新，请保存这条通知供以后查看。',
])]

class Settings:
    def _read(self): return {},AIConfig()
    def resolved(self): return AIConfig(),SecretStr('fixture-only-not-a-real-key')

class FixtureAdapter(FakeAdapter):
    def read(self,chat,*args,**kwargs):
        if chat.id!='fixture': return Observation(chat.id,'unverified',(),True,True)
        return super().read(chat,*args,**kwargs)


def make_output(payload):
    items=[]
    evidence=[]
    mappings={message['block_id']:message for message in payload['messages']}
    for index,block in enumerate(payload['blocks']):
        mapping=mappings.get(block['block_id'])
        if not mapping or mapping['context_only'] or block['source_type']!='text' or '【合成测试' not in block['text']:
            continue
        text=block['text']
        category='task' if '·任务】' in text else 'deadline' if '·截止】' in text else 'event' if '·活动】' in text else 'notice'
        q='q'+str(index)
        mid=mapping['message_id']
        evidence.append({'id':q,'message_id':mid,'block_id':block['block_id'],'quote':text,'start':0,'end':len(text)})
        phrase={'task':'2026年10月24日17:00','deadline':'2026年10月25日','event':'2026年10月26日14:00'}.get(category)
        value={'task':'2026-10-24T17:00','deadline':'2026-10-25','event':'2026-10-26T14:00'}.get(category)
        time={'value':value,'end_value':None,'precision':'date' if category=='deadline' else 'minute','range_precision':None,'timezone':'Asia/Shanghai','original_text':phrase,'anchor_message_id':mid,'uncertain':False,'evidence_ids':[q]} if phrase else None
        title={'task':'提交实验报告（合成测试）','deadline':'核对资料提交截止（合成测试）','event':'参加班会（合成测试）','notice':'学院资料领取须知（合成测试）'}[category]
        if '·新增】' in text: title='自动出现的新候选（合成测试）'
        item={'id':'item-'+mid,'important':True,'relevant':True,'action_required':category!='notice','category':category,'title':title,'summary':text,
              'attention_level':'fyi' if category=='notice' else 'action','information_complete':True,'await_more_context':False,'start_time':time if category=='event' else None,'end_time':None,'deadline':time if category in {'task','deadline'} else None,
              'location':'工程楼201' if category=='task' else '工程楼301' if category=='event' else None,'action':'提交实验报告' if category=='task' else '核对登记信息' if category=='deadline' else '参加班会' if category=='event' else None,
              'target_people':[],'priority':'P1' if category!='notice' else 'P3','confidence':0.96,'evidence_message_ids':[mid],'reason':'合成通知给出了明确日期、地点或需要保存的信息。','time_text':phrase or '',
              'needs_review':True,'participation':'required','notice_kind':'new','related_candidate_id':None,
              'deadlines':[],'actions':[],'evidence_mapping':[{'field':'title','evidence_ids':[q]}],'uncertainties':[]}
        if item['action']:
            item['actions']=[{'id':'a'+str(index),'title':item['action'],'kind':'explicit','deadline_ids':[],'evidence_ids':[q]}]
        if item['location']: item['evidence_mapping'].append({'field':'location','evidence_ids':[q]})
        items.append(item)
    return {'schema_version':'wechat.v1','batch_id':payload['batch_id'],'items':items,'evidence':evidence}


def handler(request):
    global fail_next
    body=json.loads(request.content)
    payload=json.loads(body['messages'][-1]['content'].split('仅处理以下数据:\n')[1])
    calls.append(payload['batch_id'])
    raw=json.dumps(make_output(payload),ensure_ascii=False)
    if fail_next:
        raw='{"broken":'
        fail_next=False
    return httpx.Response(200,json={'model':'deepseek-flash','choices':[{'message':{'content':raw},'finish_reason':'stop'}],'usage':{'total_tokens':300}})

original_lifespan=app.router.lifespan_context

@asynccontextmanager
async def fixture_lifespan(application):
    async with original_lifespan(application):
        await application.state.wechat_service.close()
        personal=application.state.personal_store
        from PIL import Image
        spool=personal.path.parent/'wechat-spool'
        spool.mkdir(exist_ok=True)
        image=spool/'synthetic-notice.png'
        Image.new('RGB',(600,220),'white').save(image)
        if len(messages)==4:
            messages.append(ObservedMessage('[合成图片附件]',sender='合成班级老师',message_type='image',source_time=NOW.isoformat(),attachment_identity='synthetic-image',media_path=str(image)))
        service=WeChatService(personal,Settings(),adapter=FixtureAdapter([Observation('fixture','unverified',tuple(messages),True,True)]),clock=fixture_clock,transport=DeepSeekTransport(httpx.MockTransport(handler)),media_engine=lambda path,work:([{'text':'合成图片附件：仅用于查看原件的测试。'}],{}))
        config=WeChatConfig(enabled=True,allowed_chats=[GROUP],priority_chats=[ChatConfig(id='fixture',name=GROUP)])
        config.context_followup.enabled=False
        await service.save_settings(SettingsDocument(wechat=config),1)
        application.state.wechat_service=service
        await service.request_scan(manual=True,reason='synthetic_end_to_end')
        await asyncio.gather(*service.scan_tasks)
        await service.process_queue()
        await service.start()
        (OUTPUT/'wechat-fixture-runtime.json').write_text(json.dumps({'database':str(personal.path),'url':'http://127.0.0.1:18765','synthetic_only':True}),encoding='utf-8')
        try: yield
        finally: await service.close()

app.router.lifespan_context=fixture_lifespan

@app.get('/__test__/state')
def state():
    personal=app.state.personal_store
    with personal.connection() as c:
        return {'synthetic_only':True,'provider_calls':len(calls),'messages':c.execute('SELECT count(*) FROM wechat_message').fetchone()[0],
                'candidates':[dict(row) for row in c.execute('SELECT id,status,event_id FROM wechat_candidate')],
                'events':[dict(row) for row in c.execute('SELECT id,title,category,status FROM event')],
                'tasks':c.execute('SELECT count(*) FROM task').fetchone()[0],'deadlines':c.execute('SELECT count(*) FROM deadline').fetchone()[0],
                'messages_sent':0,'unauthorized_message_bodies_read':0}

@app.post('/__test__/scan')
async def rescan():
    service=app.state.wechat_service
    await service.request_scan(manual=True,reason='synthetic_repeat')
    await asyncio.gather(*service.scan_tasks)
    await service.process_queue()
    return state()

@app.post('/__test__/append')
async def append():
    service=app.state.wechat_service
    messages.append(ObservedMessage('【合成测试·新增】请保存新增的学院通知。'+str(len(messages)),sender='合成班级老师',source_time=NOW.isoformat(),time_reliability='reliable'))
    service.adapter.observations=[Observation('fixture','unverified',tuple(messages),True,True)]
    return await rescan()

@app.post('/__test__/append-failure')
async def append_failure():
    global fail_next
    fail_next=True
    service=app.state.wechat_service
    messages.append(ObservedMessage('【合成测试·失败】即使 AI 输出格式错误，这条原始消息仍然保留。',sender='合成班级老师',source_time=NOW.isoformat(),time_reliability='reliable'))
    service.adapter.observations=[Observation('fixture','unverified',tuple(messages),True,True)]
    return await rescan()

app.router.routes.sort(key=lambda route:0 if getattr(route,'path','').startswith('/__test__/') else 1)

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18765,access_log=False)

