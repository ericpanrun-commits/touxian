"""Eight representative message types through real HTTP/SQLite; all providers mocked."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime,timedelta
import json
import os
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
OUTPUT=Path(os.environ.get('ATTENTION_QA_OUTPUT') or __import__('tempfile').mkdtemp(prefix='attention-quality-'))
DATA=OUTPUT/('quality-fixture-'+uuid4().hex[:8]);DATA.mkdir(parents=True)
os.environ.update(ATTENTION_PERSONAL_DB=str(DATA/'personal.sqlite3'),ATTENTION_OFFICIAL_POLL='0',ATTENTION_INBOX_POLL='0',ATTENTION_API_PORT='18767',AUTO_SYNC_ENABLED='0')

import httpx
from fastapi import Query
from PIL import Image
from server.main import app
from server.ai_transport import DeepSeekTransport
from server.notifications.wechat import WeChatBarkDispatcher
from server.notifications.config import BarkConfig
from server.tests_wechat.test_pipeline import Settings,scan,output_for
from server.wechat_collect.adapter import FakeAdapter
from server.wechat_collect.config import ChatConfig,SettingsDocument,WeChatConfig
from server.wechat_collect.models import Observation,ObservedMessage
from server.wechat_collect.service import WeChatService
from server.wechat_collect.inbox import briefing,list_candidates

ZONE=__import__('zoneinfo').ZoneInfo('Asia/Shanghai')
NOW=datetime.now(ZONE)
if NOW.hour<6 or NOW.hour>=23: NOW=NOW.replace(hour=12)
GROUP='合成质量验收群'
messages=[];model_calls=[];phone_calls=[]


def answer(payload):
    result={'schema_version':'wechat.v1','batch_id':payload['batch_id'],'items':[],'evidence':[]}
    for message in payload['messages']:
        if message['context_only']: continue
        block=next(b for b in payload['blocks'] if b['block_id']==message['block_id'])
        text=block['text']; kind=next((key for key in ('任务','查询','网络','获奖','活动','确认') if '【'+key+'】' in text),None)
        if not kind: continue
        made=output_for({**payload,'messages':[message],'blocks':[block]})
        item=made['items'][0];q=made['evidence'][0];qid='q'+str(len(result['items']))
        q['id']=qid;item.update(id='i'+message['message_id'],priority='P1')
        item['actions'][0]['evidence_ids']=[qid];item['evidence_mapping'][0]['evidence_ids']=[qid]
        titles={'任务':'实验室安全学习与材料提交（合成）','查询':'成绩查询方式（合成）','网络':'考试校园网要求（合成）','获奖':'无关获奖名单（合成）','活动':'自愿校园活动（合成）','确认':'课程上课地点改为二田（合成）'}
        item['title']=titles[kind]
        if kind in {'查询','网络'}: item.update(attention_level='fyi',category='notice',action_required=False,actions=[])
        if kind in {'获奖','活动'}: item.update(attention_level='ignore',category='event' if kind=='活动' else 'notice',action_required=False,actions=[],participation='optional' if kind=='活动' else 'unclear')
        if kind=='确认':
            item.update(category='course_change',action_required=False,action=None,actions=[])
            old=next(m for m in payload['messages'] if '上课地点是不是' in next(b['text'] for b in payload['blocks'] if b['block_id']==m['block_id']))
            old_block=next(b for b in payload['blocks'] if b['block_id']==old['block_id'])
            extra=qid+'old'
            result['evidence'].append({'id':extra,'message_id':old['message_id'],'block_id':old_block['block_id'],'quote':old_block['text'],'start':0,'end':len(old_block['text'])})
            item['evidence_message_ids'].append(old['message_id'])
            item['evidence_mapping']=[{'field':'title','evidence_ids':[qid,extra]}]
        result['items'].append(item);result['evidence'].append(q)
    return result


def model(request):
    payload=json.loads(json.loads(request.content)['messages'][-1]['content'].split('仅处理以下数据:\n')[1])
    model_calls.append(payload)
    return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(answer(payload),ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'total_tokens':500}})


def phone(request):
    phone_calls.append(json.loads(request.content))
    return httpx.Response(200,json={'code':200})

original=app.router.lifespan_context
@asynccontextmanager
async def lifespan(application):
    async with original(application):
        await application.state.wechat_service.close();await application.state.wechat_bark.close()
        spool=DATA/'wechat-spool';spool.mkdir(exist_ok=True)
        texts=['【任务】请各位同学完成安全学习与考试，并提交承诺书和成绩单。','【查询】各位老师，成绩查不到的同学可联系老师查询。','【网络】练习需要校园网，考试必须使用校园网。','【获奖】优秀内务一等奖、二等奖、三等奖。','【活动】有意参加的同学自愿报名校园健身活动。']
        for index,text in enumerate(texts): messages.append(ObservedMessage(text,sender='合成老师',source_time=(NOW+timedelta(seconds=index)).isoformat(),time_reliability='reliable'))
        for index in range(2):
            path=spool/f'image{index}.png';Image.new('RGB',(120,100),'white').save(path)
            messages.append(ObservedMessage('[图片]',sender='合成老师',message_type='image',attachment_identity=str(index),media_path=str(path),source_time=NOW.isoformat()))
        messages.append(ObservedMessage('上课地点是不是改成二田？',sender='合成同学',source_time=NOW.isoformat(),time_reliability='reliable'))
        service=WeChatService(application.state.personal_store,Settings(),adapter=FakeAdapter([Observation('quality','unverified',tuple(messages),True,True)]),clock=lambda:NOW,transport=DeepSeekTransport(httpx.MockTransport(model)),media_engine=lambda *a:([{'text':'回 回'}],{}))
        config=WeChatConfig(enabled=True,allowed_chats=[GROUP],priority_chats=[ChatConfig(id='quality',name=GROUP)])
        config.context_followup.enabled=False
        await service.save_settings(SettingsDocument(wechat=config),1)
        application.state.wechat_service=service
        dispatcher=WeChatBarkDispatcher(service.personal,config=BarkConfig(True,'https://bark.invalid','synthetic-only'),transport=httpx.MockTransport(phone),clock=lambda:NOW)
        application.state.wechat_bark=dispatcher
        with service.personal.connection(write=True) as c: c.execute("UPDATE wechat_bark_settings SET enabled=1,activated_at='2020-01-01'")
        await scan(service);await service.process_queue();dispatcher.tick()
        try: yield
        finally: await service.close();await dispatcher.close()
app.router.lifespan_context=lifespan

@app.get('/__test__/state')
def state():
    service=app.state.wechat_service;info=briefing(service.personal,at=NOW)
    with service.personal.connection() as c:
        return {'synthetic_only':True,'model_calls':len(model_calls),'bark_calls':len(phone_calls),'bark':phone_calls,'pending':list_candidates(service.personal)['total'],'fyi':info['fyi']['total'],'media':info['media']['total'],'media_groups':len(info['media']['items']),'unresolved':info['unresolved']['total'],'events':c.execute('SELECT count(*) FROM event').fetchone()[0],'messages_sent':0,'unauthorized_message_bodies_read':0}

@app.post('/__test__/advance')
async def advance(days:int=Query(ge=1,le=40)):
    global NOW
    NOW+=timedelta(days=days)
    from server.wechat_collect.fyi import refresh
    refresh(app.state.wechat_service.personal,NOW)
    app.state.wechat_bark.tick()
    return state()

async def append(text):
    service=app.state.wechat_service
    messages.append(ObservedMessage(text,sender='合成同学',source_time=(NOW+timedelta(minutes=len(messages))).isoformat(),time_reliability='reliable'))
    service.adapter.observations=[Observation('quality','unverified',tuple(messages),True,True)]
    await scan(service);await service.process_queue();app.state.wechat_bark.tick()
    return state()
@app.post('/__test__/question')
async def question(): return await append('考试时间是不是改到周五？')
@app.post('/__test__/confirm')
async def confirm(): return await append('【确认】老师确认了，改到二田。')
@app.post('/__test__/repeat')
async def repeat():
    service=app.state.wechat_service;await scan(service);await service.process_queue();app.state.wechat_bark.tick();return state()
app.router.routes.sort(key=lambda route:0 if getattr(route,'path','').startswith('/__test__/') else 1)
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18767,access_log=False)
