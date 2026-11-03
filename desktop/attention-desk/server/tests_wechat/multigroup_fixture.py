"""Isolated multi-group browser fixture; no real WeChat/model/Bark access."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
OUTPUT=Path(os.environ.get('ATTENTION_QA_OUTPUT') or __import__('tempfile').mkdtemp(prefix='attention-multi-qa-'))
DATA=OUTPUT/('multi-fixture-'+uuid4().hex[:8]); DATA.mkdir(parents=True)
os.environ.update(ATTENTION_PERSONAL_DB=str(DATA/'personal.sqlite3'),ATTENTION_OFFICIAL_POLL='0',ATTENTION_INBOX_POLL='0',ATTENTION_API_PORT='18766',AUTO_SYNC_ENABLED='0')

from server.main import app
from server.tests_wechat.test_multigroup import make_service
from server.tests_wechat.test_pipeline import scan
from server.wechat_collect.config import SettingsDocument

original=app.router.lifespan_context
calls=[]
sources={}

@asynccontextmanager
async def lifespan(application):
    global calls,sources
    async with original(application):
        await application.state.wechat_service.close()
        await application.state.wechat_bark.close()
        service,sources,calls=await asyncio.to_thread(make_service,application.state.personal_store,DATA,4)
        # New grants use durable room-based IDs; route them to the same synthetic shard.
        read=service.adapter.read
        def read_alias(target,*args,**kwargs):
            temporary=target.id not in sources
            if temporary: sources[target.id]=next(s for s in list(sources.values()) if s.room==target.chatroom_id)
            try: return read(target,*args,**kwargs)
            finally:
                if temporary: sources.pop(target.id,None)
        service.adapter.read=read_alias
        config=service.document.model_dump()
        config['wechat']['priority_chats']=config['wechat']['priority_chats'][:1]
        config['wechat']['allowed_chats']=config['wechat']['allowed_chats'][:1]
        config['wechat']['allowed_chatrooms']=config['wechat']['allowed_chatrooms'][:1]
        await service.save_settings(SettingsDocument.model_validate(config),service.document.revision)
        for index in range(3): sources['g'+str(index)].add('作业通知：提交合成群'+str(index)+'的独立报告')
        await scan(service); await service.process_queue()
        application.state.wechat_service=service
        try: yield
        finally: await service.close()

app.router.lifespan_context=lifespan

@app.get('/__test__/state')
def state():
    service=app.state.wechat_service
    with service.personal.connection() as c:
        return {'synthetic_only':True,'provider_calls':len(calls),'messages':c.execute('SELECT count(*) FROM wechat_message').fetchone()[0],
                'groups':service.repository.group_cards(service.document),'reads':service.adapter.reads,'metadata_searches':service.adapter.searches,
                'candidates':c.execute("SELECT count(*) FROM wechat_candidate WHERE model_item_id!='_pending'").fetchone()[0],
                'events':c.execute('SELECT count(*) FROM event').fetchone()[0], 'messages_sent':0,'unauthorized_message_bodies_read':0,
                'bark_network_calls':0}

@app.post('/__test__/scan')
async def rescan():
    service=app.state.wechat_service
    service.adapter.reads.clear()
    await scan(service); await service.process_queue()
    return state()

app.router.routes.sort(key=lambda route:0 if getattr(route,'path','').startswith('/__test__/') else 1)

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18766,access_log=False)
