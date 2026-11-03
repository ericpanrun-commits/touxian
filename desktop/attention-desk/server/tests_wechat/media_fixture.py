"""Browser-only media fixture. No real account, OCR, model or Bark connection."""
import json,os
from contextlib import asynccontextmanager
from datetime import datetime,timedelta
from pathlib import Path
from uuid import uuid4

OUTPUT=Path(os.environ['ATTENTION_QA_OUTPUT'])/('media-fixture-'+uuid4().hex[:8]);OUTPUT.mkdir(parents=True)
os.environ.update(ATTENTION_PERSONAL_DB=str(OUTPUT/'personal.sqlite3'),ATTENTION_OFFICIAL_POLL='0',ATTENTION_INBOX_POLL='0',AUTO_SYNC_ENABLED='0')
import httpx
from PIL import Image,ImageDraw
from server.main import app
from server.ai_transport import DeepSeekTransport
from server.tests_wechat.test_pipeline import Settings,scan,output_for
from server.wechat_collect.config import ChatConfig,SettingsDocument,WeChatConfig
from server.wechat_collect.adapter import FakeAdapter
from server.wechat_collect.models import Observation,ObservedMessage
from server.wechat_collect.service import WeChatService
from server.wechat_collect.inbox import list_candidates,briefing

NOW=datetime.fromisoformat('2026-10-23T12:00:00+08:00');calls=[]

def model(request):
    body=json.loads(request.content);content=body['messages'][-1]['content'];calls.append('vision' if isinstance(content,list) else 'text')
    if isinstance(content,list):
        rows=[json.loads(p['text']) for p in content if p['type']=='text' and p['text'].startswith('{')]
        result={'images':[{'media_id':r['media_id'],'blocks':[{'text':'请完成实验室安全学习，并提交安全承诺书。','bounding_box':{'x':10,'y':10,'width':250,'height':100}}],'uncertainty':''} for r in rows]}
    else:
        payload=json.loads(content.split('仅处理以下数据:\n')[1])
        block=next(b for b in payload['blocks'] if b['source_type']=='image_vision')
        message=next(m for m in payload['messages'] if m['block_id']==block['block_id'])
        result=output_for({**payload,'messages':[message],'blocks':[block]})
        result['items'][0].update(title='实验室安全学习与承诺书（合成）',priority='P1')
    return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(result,ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'total_tokens':100}})

original=app.router.lifespan_context
@asynccontextmanager
async def lifespan(application):
    async with original(application):
        await application.state.wechat_service.close();await application.state.wechat_bark.close()
        spool=OUTPUT/'wechat-spool';spool.mkdir()
        messages=[ObservedMessage('重要通知见图',sender='合成班长',source_time=NOW.isoformat(),sender_reliability='reliable',time_reliability='reliable')]
        for i in range(3):
            path=spool/f'notice-{i}.png';im=Image.new('RGB',(320,240),('#e3efff','#fff0df','#e6f1e8')[i]);ImageDraw.Draw(im).text((18,20),f'Local evidence page {i+1}',fill='#223344');im.save(path)
            messages.append(ObservedMessage('[图片]',message_type='image',sender='合成班长',sender_reliability='reliable',source_time=(NOW+timedelta(seconds=i+1)).isoformat(),time_reliability='reliable',media_path=str(path),attachment_identity='image-'+str(i)))
        messages.append(ObservedMessage('大家记得周五前完成',sender='合成班长',sender_reliability='reliable',source_time=(NOW+timedelta(seconds=5)).isoformat(),time_reliability='reliable'))
        service=WeChatService(application.state.personal_store,Settings(),adapter=FakeAdapter([Observation('media','unverified',tuple(messages),True,True)]),clock=lambda:NOW,transport=DeepSeekTransport(httpx.MockTransport(model)),media_engine=lambda *a:([{'text':'回 回'}],{}))
        config=WeChatConfig(enabled=True,priority_chats=[ChatConfig(id='media',name='合成媒体验收群')]);config.context_followup.enabled=False
        await service.save_settings(SettingsDocument(wechat=config),1)
        application.state.wechat_service=service
        await scan(service);await service.process_queue()
        try:yield
        finally:await service.close()
app.router.lifespan_context=lifespan

@app.get('/__test__/state')
def state():
    s=app.state.wechat_service
    with s.personal.connection() as c:
        return {'synthetic_only':True,'calls':calls,'pending':list_candidates(s.personal)['total'],'media':briefing(s.personal,at=NOW)['media']['total'],'events':c.execute('SELECT count(*) FROM event').fetchone()[0],'messages_sent':0}

@app.post('/__test__/recover')
async def recover():
    await app.state.wechat_service.media_pipeline.tick(force=True)
    return state()
app.router.routes.sort(key=lambda r:0 if getattr(r,'path','').startswith('/__test__/') else 1)
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18768,access_log=False)
