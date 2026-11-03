"""Multi-group privacy, isolation, noisy-chat costs and durable push checks.

All DBs/messages/providers in this file are synthetic; never inspect WeChat.
"""
import asyncio
import hashlib
import io
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.ai_transport import DeepSeekTransport
from server.notifications.config import BarkConfig
from server.notifications.wechat import WeChatBarkDispatcher, candidate_level
from server.personal_store import PersonalError
from server.source_metadata import snapshot_with_metadata
from server.tests_wechat.test_db_adapter import NOW
from server.tests_wechat.test_pipeline import Settings, output_for, scan
from server.wechat_collect.api import router, GroupGrantInput, GroupChangeInput
from server.wechat_collect.config import ChatConfig, SettingsDocument, WeChatConfig
from server.wechat_collect.db_reader import QueryBoundary, collect_increment, search_group_metadata
from server.wechat_collect.filtering import classify
from server.wechat_collect.models import Observation, ObservedMessage
from server.wechat_collect.service import WeChatService


class Shard:
    account='synthetic_multi'
    _keys={'one':b'test'}
    def __init__(self,path,room,name):
        self.path,self.room,self.name=path,room,name
        self.table='Msg_'+hashlib.md5(room.encode()).hexdigest()
        self.boundary=QueryBoundary(); self.boundary.allow(room)
        with sqlite3.connect(path) as c:
            c.execute(f'CREATE TABLE {self.table}(local_id INTEGER PRIMARY KEY,local_type,real_sender_id,create_time,message_content,source,packed_info_data,compress_content,server_id,sort_seq)')
    def add(self,text,stamp=NOW):
        with sqlite3.connect(self.path) as c:
            mid=c.execute(f'SELECT COALESCE(MAX(local_id),0)+1 FROM {self.table}').fetchone()[0]
            c.execute(f'INSERT INTO {self.table} VALUES (?,1,1,?,?,NULL,NULL,NULL,?,?)',(mid,int(stamp.timestamp()),text,str(mid),int(stamp.timestamp())*1000+mid))
    def resolve(self,name):
        assert name==self.name
        return self.room
    def _msg_conns(self,room):
        assert room==self.room
        c=sqlite3.connect(self.path); c.row_factory=sqlite3.Row; self.boundary.protect(c)
        return [(c,self.table)]
    def _message_dbs(self): return ['one']
    def _shard_label(self,c): return 'one'
    def _shard_maps(self,tables): return {}
    def _msg_row_to_dict(self,row,maps):
        return dict(local_id=row['local_id'],local_type=1,type='文本',server_id=str(row['server_id']),sender_id=1,sender_username='teacher_'+self.room,create_time=row['create_time'],content=row['message_content'],sort_seq=row['sort_seq'],shard='one')
    def get_nickname(self,user): return self.name+'老师'


class Adapter:
    is_database=True
    is_real=False
    certified=True
    def __init__(self,sources): self.sources=sources; self.reads=[]; self.searches=[]; self.errors=set(); self.last_metrics={}
    def search(self,query,**kwargs):
        self.searches.append(query)
        return {'items':[{'chatroom_id':s.room,'display_name':s.name,'last_activity_at':int(NOW.timestamp())} for s in self.sources.values() if query in s.name], 'has_more':False,'message_body_queries':0,'unauthorized_message_bodies_read':0,'messages_sent':0}
    def resolve(self,target,**kwargs):
        assert kwargs['policy'].config.allows(target)
        return {'internal_chat_id':target.chatroom_id,'account_scope':hashlib.sha256(Shard.account.encode()).hexdigest()[:24]}
    def read(self,target,binding,cursor,**kwargs):
        assert kwargs['policy'].config.allows(target)
        self.reads.append(target.id)
        if target.id in self.errors: raise PersonalError('synthetic group unavailable','database_read_error',503)
        value=collect_increment(self.sources[target.id],target.name,cursor.get('database'),now=NOW,limit=512)
        self.last_metrics={k:value[k] for k in ('metadata_queries','message_body_queries','messages_sent','unauthorized_message_bodies_read')}
        observed=tuple(ObservedMessage(row['content'],sender=target.name+'老师',source_time=row['create_time'],time_reliability='reliable',sender_reliability='reliable',database_identity=row) for row in value['messages'])
        return Observation(target.id,binding['account_scope'],observed,not value['has_more'],True,database_cursor=value['cursor'])


def make_service(personal,tmp_path,count=6):
    targets=[ChatConfig(id='g'+str(i),name='合成群'+str(i),chatroom_id=f'synthetic{i}@chatroom',priority='medium') for i in range(count)]
    sources={ch.id:Shard(tmp_path/(ch.id+'.sqlite3'),ch.chatroom_id,ch.name) for ch in targets}
    calls=[]
    def handler(request):
        payload=json.loads(json.loads(request.content)['messages'][-1]['content'].split('仅处理以下数据:\n')[1]); calls.append(payload)
        primary=[m for m in payload['messages'] if not m['context_only']]
        chosen=primary[-1]
        data=output_for({**payload,'messages':[chosen],'blocks':[next(b for b in payload['blocks'] if b['block_id']==chosen['block_id'])]})
        data['items'][0]['priority']='P1'
        return httpx.Response(200,json={'model':'deepseek-flash','choices':[{'message':{'content':json.dumps(data,ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'total_tokens':100}})
    adapter=Adapter(sources)
    service=WeChatService(personal,Settings(),adapter=adapter,clock=lambda:NOW,transport=DeepSeekTransport(httpx.MockTransport(handler)))
    config=WeChatConfig(enabled=True,allowed_chats=[ch.name for ch in targets],allowed_chatrooms=[ch.chatroom_id for ch in targets],priority_chats=targets)
    config.context_followup.enabled=False
    asyncio.run(service.save_settings(SettingsDocument(wechat=config),1))
    return service,sources,calls


def test_six_groups_only_two_changed_zero_model_on_repeat_and_no_cross_group(personal,tmp_path):
    service,sources,calls=make_service(personal,tmp_path)
    for source in sources.values(): source.add('旧消息不得读取',NOW-timedelta(days=2))
    async def run():
        await scan(service); await service.process_queue()
        assert not calls
        for key in ('g1','g4'):
            sources[key].add('作业：请提交'+key+'的材料')
            sources[key].add('补充说明：'+key+'材料提交到工程楼201')
        await scan(service); await service.process_queue()
        assert len(calls)==2
        before={key:service.repository.cursor(key)['database']['last_local_id'] for key in sources}
        assert before=={'g0':None,'g1':3,'g2':None,'g3':None,'g4':3,'g5':None}
        assert all(json.loads(service.repository.cursor(key)['database']['shard_watermarks_json'])=={'one':1} for key in ('g0','g2','g3','g5'))
        for _ in range(12): await scan(service); await service.process_queue()
        assert len(calls)==2
    asyncio.run(run())
    with personal.connection() as c:
        rows=c.execute("SELECT ca.*,b.chat_id,b.snapshot_id FROM wechat_candidate ca JOIN wechat_batch b ON b.id=ca.batch_id WHERE ca.model_item_id!='_pending'").fetchall()
        assert len(rows)==2 and {r['chat_id'] for r in rows}=={'g1','g4'}
        for row in rows:
            snap=snapshot_with_metadata(c,c.execute('SELECT * FROM raw_snapshot WHERE id=?',(row['snapshot_id'],)).fetchone())
            assert all(m['chat_name']=='合成群'+row['chat_id'][1:] for m in snap['wechat']['messages'])
            assert all(m['database_identity']['internal_chat_id']=='synthetic'+row['chat_id'][1:]+'@chatroom' for m in snap['wechat']['messages'])
            assert len([b for b in snap['blocks'] if b['source_type']=='text'])==2
            assert json.loads(row['data_json'])['needs_review']
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0
        assert tuple(c.execute('SELECT SUM(unauthorized_message_bodies_read),SUM(messages_sent) FROM wechat_group_state').fetchone())==(0,0)


def test_one_group_failure_does_not_stop_others(personal,tmp_path):
    service,sources,calls=make_service(personal,tmp_path,3)
    service.adapter.errors.add('g0'); sources['g2'].add('考试通知：请提交登记表')
    asyncio.run(scan(service)); asyncio.run(service.process_queue())
    cards={g['id']:g for g in service.repository.group_cards(service.document)}
    assert cards['g0']['last_error']=='database_read_error'
    assert cards['g2']['messages_new']==1 and len(calls)==1
    assert service.last_result=='partial'


def test_metadata_discovery_before_explicit_grant_and_remove_readd_preserves_cursor(personal,tmp_path):
    service,sources,calls=make_service(personal,tmp_path,2)
    sources['g1'].add('作业通知：请提交报告')
    asyncio.run(scan(service))
    cursor=service.repository.cursor('g1')['database']
    revision=service.document.revision
    asyncio.run(service.update_group('g1',GroupChangeInput(expected_revision=revision),remove=True))
    assert not service.repository.chat('g1')['enabled']
    service.adapter.reads.clear()
    asyncio.run(service.search_groups('合成群'))
    assert not service.adapter.reads and not calls
    asyncio.run(scan(service)); assert 'g1' not in service.adapter.reads
    grant=GroupGrantInput(chatroom_id='synthetic1@chatroom',display_name='合成群1',priority='low',expected_revision=service.document.revision)
    asyncio.run(service.add_group(grant))
    assert service.repository.cursor('g1')['database']==cursor
    assert service.document.wechat.priority_chats[-1].id=='g1'
    with pytest.raises(PersonalError): asyncio.run(service.update_group('g1',GroupChangeInput(expected_revision=revision,enabled=False)))
    app=FastAPI(); app.state.wechat_service=service; app.include_router(router)
    with TestClient(app) as client:
        assert client.get('/api/personal/wechat/chats/search?q=合成').json()['message_body_queries']==0
        assert client.patch('/api/personal/wechat/chats/g1',json={'expected_revision':service.document.revision,'enabled':False},headers={'Origin':'https://evil.invalid'}).status_code==403
        assert client.post('/api/personal/wechat/chats',json={'chatroom_id':'not-granted@chatroom','display_name':'合成群1','priority':'high','expected_revision':service.document.revision}).status_code==409


def test_metadata_query_guard_never_reads_session_previews_or_message_tables(tmp_path):
    path=tmp_path/'metadata.sqlite3'
    with sqlite3.connect(path) as c:
        c.executescript("CREATE TABLE contact(username,nick_name,remark); INSERT INTO contact VALUES ('one@chatroom','授权群',''); CREATE TABLE SessionTable(username,last_timestamp,summary); INSERT INTO SessionTable VALUES ('one@chatroom',123,'FORBIDDEN BODY'); CREATE TABLE Msg_forbidden(message_content);")
    guard=QueryBoundary()
    def connection():
        c=sqlite3.connect(path); c.row_factory=sqlite3.Row; return guard.protect(c)
    class Metadata:
        account='synthetic'
        boundary=guard
        _contact_conn=staticmethod(connection)
        def get_sessions(self,limit):
            with connection() as c: return [dict(r) for r in c.execute('SELECT username,last_timestamp FROM SessionTable')]
    result=search_group_metadata(Metadata(),'授权')
    assert result['items'][0]['chatroom_id']=='one@chatroom'
    assert result['message_body_queries']==0 and guard.authorized_body_reads==0
    c=connection()
    for sql in ('SELECT summary FROM SessionTable','SELECT message_content FROM Msg_forbidden'):
        with pytest.raises(sqlite3.DatabaseError): c.execute(sql)
    c.close()
    with sqlite3.connect(path) as c:
        c.executemany('INSERT INTO contact VALUES (?,?,?)',[(f'duplicate{i}@chatroom','同名群','') for i in range(40)])
    assert search_group_metadata(Metadata(),'同名群')['has_more']
    selected=search_group_metadata(Metadata(),'同名群',chatroom_id='duplicate39@chatroom')
    assert [r['chatroom_id'] for r in selected['items']]==['duplicate39@chatroom']
    assert guard.authorized_body_reads==0


@pytest.mark.parametrize('priority',['high','medium','low'])
@pytest.mark.parametrize('text',['作业明天交','调课通知','考试安排','报名','截止','集合','材料提交','@所有人重要通知','明早8点操场','补充：工程楼201','带好学生证','请准备个人证件','明早出发'])
def test_conservative_notice_signals_survive_every_priority(priority,text):
    assert classify({'raw_text':text,'message_type':'text'},priority=priority).level!='fast_reject'


def test_large_noisy_group_filters_1000_messages_with_one_model_call(personal,tmp_path):
    service,sources,calls=make_service(personal,tmp_path,1)
    noise=['收到','哈哈哈哈','[动画表情]','今天吃什么','晚上开黑吗','我也是','确实','[捂脸]']
    for i in range(1000): sources['g0'].add(noise[i%len(noise)])
    sources['g0'].add('作业通知：明天提交实验报告')
    async def run():
        for _ in range(3): await scan(service); await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1
    with personal.connection() as c:
        assert c.execute("SELECT count(*) FROM wechat_message_state WHERE status='filtered'").fetchone()[0]==1000
        assert c.execute("SELECT count(*) FROM wechat_candidate WHERE model_item_id!='_pending'").fetchone()[0]==1


def test_multigroup_bark_once_even_after_candidate_edits_or_restart(personal,tmp_path):
    service,sources,calls=make_service(personal,tmp_path,2)
    for source in sources.values(): source.add('作业：请提交完整材料')
    asyncio.run(scan(service)); asyncio.run(service.process_queue())
    with personal.connection(write=True) as c: c.execute("UPDATE wechat_bark_settings SET enabled=1,activated_at='2020-01-01'")
    pushes=[]
    def handler(request): pushes.append(json.loads(request.content)); return httpx.Response(200,json={'code':200})
    dispatcher=WeChatBarkDispatcher(personal,config=BarkConfig(True,'https://bark.invalid','synthetic-secret'),transport=httpx.MockTransport(handler),clock=lambda:NOW)
    dispatcher.tick(); dispatcher.tick()
    with personal.connection(write=True) as c:
        for r in c.execute("SELECT id,data_json FROM wechat_candidate WHERE model_item_id!='_pending'").fetchall():
            item=json.loads(r['data_json']); item['summary']+='（更新）'
            c.execute('UPDATE wechat_candidate SET data_json=? WHERE id=?',(json.dumps(item),r['id']))
    WeChatBarkDispatcher(personal,config=dispatcher.environment,transport=dispatcher.transport,clock=lambda:NOW).tick()
    assert len(pushes)==2 and {p['subtitle'] for p in pushes}=={'合成群0','合成群1'}
    assert all(p['title']=='Attention Desk · 微信' for p in pushes)
    assert candidate_level({'important':True,'relevant':True,'category':'task','action_required':True,'participation':'required','priority':'P2'},NOW,'low') is None
    with personal.connection() as c:
        assert c.execute("SELECT count(*) FROM wechat_bark_notification WHERE state='sent'").fetchone()[0]==2
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0


def test_worker_pipe_unicode_is_lossless_even_with_ascii_console(monkeypatch):
    from server.wechat_collect import db_worker
    request={'chat':'授权群','allowed_chats':['授权群'],'action':'resolve','verified_version':'synthetic','workdir':'synthetic'}
    incoming=SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))
    output=io.BytesIO(); console=io.TextIOWrapper(output,encoding='ascii',write_through=True)
    monkeypatch.setattr(db_worker.sys,'stdin',incoming); monkeypatch.setattr(db_worker.sys,'stdout',console)
    monkeypatch.setattr(db_worker,'wechat_version',lambda:'synthetic')
    monkeypatch.setattr(db_worker,'make_reader',lambda *a,**k:SimpleNamespace(account='用户🧑',resolve=lambda name:'emoji🧑@chatroom'))
    class Clock:
        @classmethod
        def now(cls,*args): return NOW
    monkeypatch.setattr(db_worker,'datetime',Clock)
    assert db_worker.main()==0
    result=json.loads(output.getvalue())
    assert result['ok'] and result['result']['internal_chat_id']=='emoji🧑@chatroom'

def test_pending_unchanged_group_cannot_consume_changed_groups_model_slots(personal,tmp_path):
    service,sources,calls=make_service(personal,tmp_path,6)
    service.document.wechat.budget.batches_per_cycle=2
    sources['g0'].add('作业：尚未分析的旧通知')
    asyncio.run(scan(service))
    with personal.connection() as c: old=c.execute("SELECT id FROM wechat_batch WHERE chat_id='g0'").fetchone()[0]
    old_job=service.analysis.enqueue(old,service.context.read())
    sources['g4'].add('考试：新增独立事项A'); sources['g5'].add('报名：新增独立事项B')
    asyncio.run(scan(service)); asyncio.run(service.process_queue())
    assert len(calls)==2
    with personal.connection() as c:
        assert c.execute('SELECT status FROM wechat_analysis_job WHERE id=?',(old_job,)).fetchone()[0]=='pending'
        assert {r[0] for r in c.execute("SELECT b.chat_id FROM wechat_analysis_job j JOIN wechat_batch b ON b.id=j.batch_id WHERE j.status='parsed'")}=={'g4','g5'}


def test_bound_legacy_grant_is_pinned_before_any_message_read(monkeypatch,tmp_path):
    from server.wechat_collect import db_adapter
    from server.wechat_collect.db_adapter import ReadOnlyWeChatDBAdapter
    from server.wechat_collect.scheduler import SchedulerPolicy
    requests=[]
    def runner(*args,**kwargs):
        requests.append(json.loads(kwargs['input']))
        return SimpleNamespace(stdout=json.dumps({'ok':False,'code':'synthetic_stop'}))
    monkeypatch.setattr(ReadOnlyWeChatDBAdapter,'certified',property(lambda self:True))
    monkeypatch.setattr(db_adapter,'ROOT',tmp_path)
    report=tmp_path/'artifacts/wechat-db0/results/report.json'; report.parent.mkdir(parents=True)
    report.write_text(json.dumps({'wechat_version':'synthetic'}),encoding='utf-8')
    config=WeChatConfig(enabled=True,allowed_chats=['旧授权群'])
    policy=SchedulerPolicy(config,lambda:NOW)
    adapter=ReadOnlyWeChatDBAdapter(runner=runner)
    binding={'internal_chat_id':'pinned@chatroom','account_scope':'synthetic'}
    with pytest.raises(PersonalError): adapter.read(config.priority_chats[0],binding,None,policy=policy)
    assert requests[0]['selected_chatroom']=='pinned@chatroom'
    assert 'pinned@chatroom' in requests[0]['allowed_chatrooms']
    requests.clear()
    target=ChatConfig(id='g',name='新授权群',chatroom_id='new@chatroom')
    config=WeChatConfig(enabled=True,allowed_chats=[target.name],allowed_chatrooms=['new@chatroom'],priority_chats=[target])
    with pytest.raises(PersonalError,match='绑定不一致'): adapter.read(target,binding,None,policy=SchedulerPolicy(config,lambda:NOW))
    assert not requests


def test_same_name_groups_resolve_only_the_selected_id_without_body_access(tmp_path,monkeypatch):
    import sys
    import server.wechat_collect as package
    from server.wechat_collect import db_reader
    # The real crypto core intentionally lives in the isolated DB-worker venv.
    # Exercise our resolver SQL without importing that optional native boundary.
    class Base:
        def __init__(self,**kwargs): pass
        def _contact_conn(self): pass
    core=SimpleNamespace(WeChatDB=Base)
    monkeypatch.setitem(sys.modules,'server.wechat_collect.replica_db_core',core)
    monkeypatch.setattr(package,'replica_db_core',core,raising=False)
    path=tmp_path/'contact.sqlite3'
    with sqlite3.connect(path) as c:
        c.executescript("CREATE TABLE contact(username,nick_name,remark); INSERT INTO contact VALUES ('one@chatroom','同名群',''),('two@chatroom','同名群','');")
    private=tmp_path/'private'; private.mkdir()
    monkeypatch.setattr(db_reader,'private_directory',lambda p:private)
    monkeypatch.setattr(core.WeChatDB,'__init__',lambda self,**kw:setattr(self,'account','synthetic'))
    def connection(self):
        c=sqlite3.connect(path); c.row_factory=sqlite3.Row; return self.boundary.protect(c)
    monkeypatch.setattr(core.WeChatDB,'_contact_conn',connection)
    selected=db_reader.make_reader(['同名群'],db_dir=str(tmp_path),selected_chatroom='two@chatroom')
    assert selected.resolve('同名群')=='two@chatroom'
    assert selected.allowed_ids=={'two@chatroom'} and selected.boundary.authorized_body_reads==0
    ambiguous=db_reader.make_reader(['同名群'],db_dir=str(tmp_path))
    with pytest.raises(db_reader.DBReadError,match='ambiguous'): ambiguous.resolve('同名群')
    assert ambiguous.boundary.authorized_body_reads==0
