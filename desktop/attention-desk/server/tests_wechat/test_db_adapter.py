"""Privacy and recovery scenarios using synthetic SQLite shards, never real WeChat."""
import asyncio
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from server.personal_store import PersonalError
from server.wechat_collect.config import ChatConfig, SettingsDocument, WeChatConfig
from server.wechat_collect.db_adapter import ReadOnlyWeChatDBAdapter
from server.wechat_collect.db_reader import QueryBoundary, DBReadError, collect_increment
from server.wechat_collect.db_media import describe_content
from server.wechat_collect.models import Observation, ObservedMessage
from server.wechat_collect.scheduler import SchedulerPolicy
from server.wechat_collect.store import WeChatStore

NOW = datetime.fromisoformat('2026-10-22T18:00:00+08:00')
GROUP = 'authorized@chatroom'
TABLE = 'Msg_' + hashlib.md5(GROUP.encode()).hexdigest()


class SyntheticDB:
    account = 'synthetic_account'
    _keys = {'one': b'synthetic'}
    def __init__(self, path):
        self.path = path
        self.boundary = QueryBoundary()
        self.boundary.allow(GROUP)
        connection = sqlite3.connect(path)
        connection.executescript(f'''CREATE TABLE {TABLE}(local_id INTEGER PRIMARY KEY,local_type,real_sender_id,create_time,message_content,source,packed_info_data,compress_content,server_id,sort_seq);
            CREATE TABLE Msg_forbidden(message_content); INSERT INTO Msg_forbidden VALUES ('UNAUTHORIZED SECRET');''')
        connection.close()

    def add(self, local_id, time=None, text='相同消息', seq=1):
        connection = sqlite3.connect(self.path)
        connection.execute(f'INSERT INTO {TABLE} VALUES (?,1,1,?,?,NULL,NULL,NULL,?,?)', (local_id,int((time or NOW).timestamp()),text,str(local_id),seq))
        connection.commit()
        connection.close()

    def resolve(self, name):
        assert name == '授权群'
        return GROUP

    def _msg_conns(self, user):
        assert user == GROUP
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        self.boundary.protect(connection)
        return [(connection,TABLE)]

    def _message_dbs(self): return ['one']
    def _shard_label(self, connection): return 'one'
    def _shard_maps(self, tables): return {}
    def _msg_row_to_dict(self, row, maps):
        return dict(local_id=row['local_id'],local_type=1,type='文本',server_id=str(row['server_id']),sender_id=1,sender_username='teacher',
                    create_time=row['create_time'],content=row['message_content'],sort_seq=row['sort_seq'],shard='one')
    def get_nickname(self, user): return '合成老师'


def test_sql_guard_rejects_other_chat_previews_and_writes():
    connection = sqlite3.connect(':memory:')
    connection.executescript('CREATE TABLE Msg_forbidden(message_content); CREATE TABLE SessionTable(username,summary);')
    guard = QueryBoundary()
    guard.protect(connection)
    for sql in ('SELECT message_content FROM Msg_forbidden', 'SELECT summary FROM SessionTable',
                'DELETE FROM SessionTable', 'PRAGMA quick_check', "ATTACH DATABASE ':memory:' AS escaped"):
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(sql).fetchall()
    assert guard.authorized_body_reads == 0
    connection.execute('SELECT username FROM SessionTable').fetchall()


def test_bootstrap_only_24_hours_then_metadata_only_and_equal_sequence_ids(tmp_path):
    db = SyntheticDB(tmp_path / 'source.sqlite3')
    db.add(1,NOW-timedelta(days=2),text='授权群旧历史不应取回')
    db.add(2,seq=100)
    first = collect_increment(db,'授权群',None,now=NOW)
    assert [m['local_id'] for m in first['messages']] == [2]
    quiet = collect_increment(db,'授权群',first['cursor'],now=NOW)
    assert quiet['messages'] == [] and quiet['message_body_queries'] == 0
    db.add(3,seq=100)
    db.add(4,seq=100)
    second = collect_increment(db,'授权群',quiet['cursor'],now=NOW)
    assert [m['local_id'] for m in second['messages']] == [3,4]
    assert db.boundary.denied_queries == 0


def test_pagination_does_not_lose_equal_sequence_rows(tmp_path):
    db = SyntheticDB(tmp_path / 'source.sqlite3')
    for local_id in range(1,8): db.add(local_id,seq=100)
    cursor = None
    seen = []
    for _ in range(4):
        result = collect_increment(db,'授权群',cursor,now=NOW,limit=2)
        seen += [m['local_id'] for m in result['messages']]
        cursor = result['cursor']
    assert seen == list(range(1,8)) and not result['has_more']
    assert collect_increment(db,'授权群',cursor,now=NOW)['message_body_queries'] == 0


def test_local_id_reset_is_error_instead_of_no_change(tmp_path):
    db = SyntheticDB(tmp_path / 'source.sqlite3')
    db.add(4)
    first = collect_increment(db,'授权群',None,now=NOW)
    c = sqlite3.connect(db.path)
    c.execute(f'DELETE FROM {TABLE}')
    c.commit()
    c.close()
    with pytest.raises(DBReadError,match='local_id_reset'):
        collect_increment(db,'授权群',first['cursor'],now=NOW)


def test_all_history_older_than_bootstrap_does_not_fetch_bodies(tmp_path):
    db = SyntheticDB(tmp_path / 'source.sqlite3')
    db.add(1,NOW-timedelta(days=2))
    result = collect_increment(db,'授权群',None,now=NOW)
    assert result['messages'] == [] and result['message_body_queries'] == 0
    assert json.loads(result['cursor']['shard_watermarks_json']) == {'one':1}


def test_stable_identity_dedupe_and_atomic_cursor_failure(personal,tmp_path):
    db = SyntheticDB(tmp_path / 'source.sqlite3')
    db.add(1)
    db.add(2)
    value = collect_increment(db,'授权群',None,now=NOW)
    repository = WeChatStore(personal)
    document = SettingsDocument(wechat=WeChatConfig(enabled=True,allowed_chats=['授权群']))
    repository.configure(document,enforce_allowlist=True)
    target = document.wechat.priority_chats[0]
    binding = {k:value['cursor'][k] for k in ['account_scope','internal_chat_id']}
    repository.bind_database(target.id,binding)
    observed = tuple(ObservedMessage(row['content'],sender='老师',source_time=row['create_time'],database_identity=row) for row in value['messages'])
    observation = Observation(target.id,binding['account_scope'],observed,True,True,database_cursor=value['cursor'])
    scan,_ = repository.begin_scan('synthetic',1)
    _,created,_ = repository.capture(scan['id'],repository.chat(target.id),observation)
    assert len(created) == 2 and created[0]['id'] != created[1]['id']
    _,duplicate,_ = repository.capture(scan['id'],repository.chat(target.id),observation)
    assert duplicate == []
    before = repository.cursor(target.id)['database']
    changed = dict(value['messages'][0],source_content_sha256='wrong')
    invalid = Observation(target.id,binding['account_scope'],(ObservedMessage('changed',database_identity=changed),),True,True,
                          database_cursor={**value['cursor'],'last_local_id':999})
    with pytest.raises(PersonalError,match='内容变化'):
        repository.capture(scan['id'],repository.chat(target.id),invalid)
    assert repository.cursor(target.id)['database'] == before
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM event').fetchone()[0] == 0
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []


def test_revoked_priority_chat_disabled_and_adapter_checks_allowlist_before_worker(personal):
    document = SettingsDocument(wechat=WeChatConfig(enabled=True,allowed_chats=['授权群'],priority_chats=[ChatConfig(id='revoked',name='已撤销群')]))
    repository = WeChatStore(personal)
    repository.configure(document,enforce_allowlist=True)
    assert repository.chat('revoked')['enabled'] == 0
    calls = []
    adapter = ReadOnlyWeChatDBAdapter(runner=lambda *a,**k:calls.append(a))
    policy = SchedulerPolicy(document.wechat,lambda:NOW)
    with pytest.raises(PersonalError) as exc:
        adapter.resolve(document.wechat.priority_chats[0],policy=policy)
    assert exc.value.code == 'chat_not_allowlisted' and calls == []


def test_database_quiet_hours_cannot_be_overridden():
    config = WeChatConfig(enabled=True,allowed_chats=['授权群'])
    adapter = ReadOnlyWeChatDBAdapter(runner=lambda *a,**k:pytest.fail('no worker during quiet hours'))
    policy = SchedulerPolicy(config,lambda:NOW.replace(hour=1))
    with pytest.raises(PersonalError) as exc:
        adapter.read(config.priority_chats[0],None,None,policy=policy,override=object())
    assert exc.value.code == 'quiet'


def test_media_output_excludes_crypto_material_and_keeps_correct_md5():
    content,reference = describe_content('<msg><img aeskey="DO_NOT_LOG" md5="'+'a'*32+'" /></msg>',
                                         {'local_type':3,'local_id':1,'shard':'one','type':'图片'})
    assert content == '[图片]' and reference['md5'] == 'a'*32
    assert 'DO_NOT_LOG' not in json.dumps([content,reference])


def test_no_change_cycle_calls_no_model_even_with_pending_jobs(personal):
    from server.tests_wechat.test_pipeline import Settings
    from server.wechat_collect.service import WeChatService
    adapter = ReadOnlyWeChatDBAdapter(runner=lambda *a,**k:pytest.fail('no worker'))
    service = WeChatService(personal,Settings(),adapter=adapter,clock=lambda:NOW)
    service.document.wechat.enabled = True
    service.last_result = 'no_change'
    service.analysis.recover_responses = lambda:pytest.fail('no recovery/model work during no-change')
    asyncio.run(service.process_queue())


def test_low_importance_model_candidate_still_requires_review(personal):
    from server.tests_wechat.test_pipeline import setup_service, scan, output_for
    def response(payload):
        result=output_for(payload)
        result['items'][0]['important']=False
        result['items'][0]['priority']='P3'
        return result
    service,_,calls=setup_service(personal,[Observation('test','unverified',(ObservedMessage('请核对已提交材料，重要性较低也需要人工决定'),),True,True)],response=response)
    async def run():
        await scan(service)
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1
    with personal.connection() as c:
        row=c.execute("SELECT status,data_json FROM wechat_candidate WHERE model_item_id!='_pending'").fetchone()
        assert row['status']=='needs_review' and json.loads(row['data_json'])['needs_review']
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0


def test_shared_file_cache_cannot_be_read_by_filename_guess(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from server.wechat_collect.db_media import cache_references
    reader=SimpleNamespace(account_dir=str(tmp_path),allowed_ids={GROUP})
    def forbidden(*args,**kwargs):
        pytest.fail('No global filename search or file-body access without message-bound resource identity')
    monkeypatch.setattr(Path,'glob',forbidden)
    monkeypatch.setattr(Path,'read_bytes',forbidden)
    message={'media_reference':{'kind':'file','title':'同名文件.pdf'}}
    assert cache_references(reader,GROUP,message)==[]
    assert message['media_reference']['cache_status']=='requires_message_bound_resource_path'
