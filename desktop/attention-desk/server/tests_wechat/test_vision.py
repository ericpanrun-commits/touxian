import asyncio
import json
from datetime import datetime

import httpx
from PIL import Image

from server.ai_transport import DeepSeekTransport
from server.tests_wechat.test_pipeline import Settings,output_for,scan
from server.wechat_collect.adapter import FakeAdapter
from server.wechat_collect.config import ChatConfig,SettingsDocument,WeChatConfig,ContextFollowup
from server.wechat_collect.models import Observation,ObservedMessage
from server.wechat_collect.service import WeChatService
from server.review_store import ReviewStore


def test_vision_is_new_candidate_only_budgeted_and_traceable(personal):
    spool=personal.path.parent/'wechat-spool'
    spool.mkdir()
    image=spool/'synthetic.png'
    Image.new('RGB',(100,100),'white').save(image)
    observed=ObservedMessage('',message_type='image',attachment_identity='synthetic-image',media_path=str(image))
    calls=[]
    def handler(request):
        body=json.loads(request.content)
        calls.append(body)
        content=body['messages'][-1]['content']
        if isinstance(content,list):
            metadata=[json.loads(p['text']) for p in content if p['type']=='text' and p['text'].startswith('{')]
            output={'images':[{'media_id':metadata[0]['media_id'],'blocks':[{'text':'请提交完整实验报告','bounding_box':{'x':0,'y':0,'width':80,'height':20}}],'uncertainty':'需对照原图核对'}]}
        else:
            output=output_for(json.loads(content.split('仅处理以下数据:\n')[1]))
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output,ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'total_tokens':100}})
    def fake_ocr_failure(*args): raise ValueError('synthetic local OCR failure')
    service=WeChatService(personal,Settings(),adapter=FakeAdapter([Observation('test','unverified',(observed,),True,True)]),
                          clock=lambda:datetime.fromisoformat('2026-10-22T10:00:00+08:00'),transport=DeepSeekTransport(httpx.MockTransport(handler)),media_engine=fake_ocr_failure)
    document=SettingsDocument(wechat=WeChatConfig(enabled=True,context_followup=ContextFollowup(enabled=False),priority_chats=[ChatConfig(id='test',name='合成图片群')]))
    asyncio.run(service.save_settings(document,1))
    async def run():
        await scan(service)
        await service.process_queue()
        await scan(service)
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==2
    assert isinstance(calls[0]['messages'][0]['content'],list)
    assert calls[0]['thinking']==calls[1]['thinking']=={'type':'disabled'}
    with personal.connection() as c:
        snapshot=c.execute('SELECT snapshot_id FROM wechat_batch').fetchone()[0]
        assert c.execute("SELECT count(*) FROM wechat_llm_attempt WHERE kind='vision'").fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert c.execute("SELECT raw_output FROM wechat_llm_attempt WHERE kind='vision'").fetchone()[0]
    bundle=ReviewStore(personal).get(snapshot)
    block=next(b for b in bundle['snapshot']['blocks'] if b['source_type']=='image_vision')
    assert block['needs_review'] and block['attachment_id'] and block['bounding_box']['image_width']==100
    assert ReviewStore(personal).list()['total']==1
    with personal.connection() as c:
        oldest=c.execute('SELECT id FROM raw_snapshot ORDER BY created_at LIMIT 1').fetchone()[0]
    assert ReviewStore(personal).get(oldest)['snapshot']['latest_wechat_snapshot_id']==snapshot
    assert service.repository.stats('2026-10-22')['llm_calls']==2
