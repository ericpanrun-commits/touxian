"""Synthetic source records only; no WeChat or account access."""
from datetime import timedelta
import json
import sqlite3
import pytest

from server.tests_wechat.test_db_adapter import SyntheticDB,NOW,GROUP,TABLE
from server.wechat_collect.db_reader import collect_increment
from server.wechat_collect.sender import resolve_sender,display_name


def message(**change):
    return dict(content='wxid_alice:\n请提交实验报告',sender_id=14,sender_username='wxid_alice',shard='one',local_type=1,type='文本',**change)


def test_system_chatroom_envelope_with_empty_mapping_is_not_a_member_conflict():
    source=message();source.update(content=GROUP+':\n某条系统提示',local_type=10000,type='系统消息',sender_username='')
    result=resolve_sender(source,GROUP)
    assert result['sender_status']=='system' and result['sender']=='系统消息'
    assert result['sender_username'] is None and result['sender_prefix_matches'] is None
    assert result['sender_identity']['raw_sender_id']==14
    assert result['sender_identity']['envelope_sender']==GROUP


def test_true_conflict_keeps_both_ids_and_never_guesses_display_or_binding():
    source=message();source['content']='wxid_bob:\n请提交实验报告'
    def no_display(*args):raise AssertionError('An unresolved identity must not be shown as a person')
    result=resolve_sender(source,GROUP,display=no_display)
    assert result['sender_username'] is None and result['sender_reliability']=='unknown'
    assert result['sender_prefix_matches'] is False
    assert result['sender_identity']['mapped_username']=='wxid_alice'
    assert result['sender_identity']['envelope_sender']=='wxid_bob'
    assert '14' in result['sender']


def test_missing_mapping_does_not_use_prefix_as_an_unverified_identity():
    source=message();source['sender_username']=''
    result=resolve_sender(source,GROUP)
    assert result['sender_username'] is None and result['sender_status']=='unresolved'
    assert result['sender_identity']['raw_sender_id']==14


def test_prose_colon_is_not_stripped_or_mistaken_for_a_sender():
    source=message();source['content']='通知:\n请提交实验报告'
    result=resolve_sender(source,GROUP)
    assert result['content']==source['content'] and result['sender_username']=='wxid_alice'
    assert result['sender_prefix_matches'] is None


def test_same_numeric_id_in_another_shard_keeps_distinct_username():
    first=resolve_sender(message(),GROUP)
    source=message();source.update(shard='two',content='wxid_bob:\n请提交材料',sender_username='wxid_bob')
    second=resolve_sender(source,GROUP)
    assert first['sender_username']=='wxid_alice' and second['sender_username']=='wxid_bob'
    assert first['sender_identity']['raw_sender_id']==second['sender_identity']['raw_sender_id']


def test_group_nickname_remark_and_contact_refresh_never_change_identity():
    with sqlite3.connect(':memory:') as c:
        c.executescript('CREATE TABLE contact(id,username,remark,nick_name);CREATE TABLE chat_room(id,username);CREATE TABLE chatroom_member(room_id,member_id,display_name);')
        c.executemany('INSERT INTO contact VALUES(?,?,?,?)',[(999,'wxid_alice','备注A','昵称A'),(14,'wxid_other','相同昵称','相同昵称')])
        c.executemany('INSERT INTO chat_room VALUES(?,?)',[(1,'a@chatroom'),(2,'b@chatroom')])
        c.executemany('INSERT INTO chatroom_member VALUES(?,?,?)',[(1,999,'A群昵称'),(2,999,'B群昵称')])
        assert display_name(c,'a@chatroom','wxid_alice')==('A群昵称','group_nickname')
        assert display_name(c,'b@chatroom','wxid_alice')==('B群昵称','group_nickname')
        c.execute("UPDATE chatroom_member SET display_name='新群昵称' WHERE room_id=1")
        assert display_name(c,'a@chatroom','wxid_alice')[0]=='新群昵称'
        c.execute('DELETE FROM chatroom_member')
        assert display_name(c,'a@chatroom','wxid_alice')==('备注A','remark')
        c.execute("UPDATE contact SET remark='',nick_name='新联系人昵称' WHERE username='wxid_alice'")
        assert display_name(c,'a@chatroom','wxid_alice')==('新联系人昵称','nickname')
        assert display_name(c,'a@chatroom','wxid_missing')==('wxid_missing','username')
        assert display_name(None,'a@chatroom','wxid_alice')==('wxid_alice','username')


class MappedDB(SyntheticDB):
    def __init__(self,path):
        super().__init__(path)
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE Name2Id(user_name)')
            c.executemany('INSERT INTO Name2Id(rowid,user_name) VALUES(?,?)',[(1,'wxid_alice'),(14,'')])
    def _shard_maps(self,tables):
        return {'one':{r[0]:r[1] for r in tables[0][0].execute('SELECT rowid,user_name FROM Name2Id')}}
    def _msg_row_to_dict(self,row,maps):
        return dict(local_id=row['local_id'],local_type=row['local_type'],type='系统消息' if row['local_type']==10000 else '文本',server_id=str(row['server_id']),sender_id=row['real_sender_id'],sender_username=maps['one'].get(row['real_sender_id'],''),create_time=row['create_time'],content=row['message_content'],sort_seq=row['sort_seq'],shard='one')


def test_system_and_unresolved_sender_do_not_block_cursor_and_mapping_refreshes(tmp_path):
    db=MappedDB(tmp_path/'source.sqlite3')
    db.add(1,text=GROUP+':\n某条系统提示')
    db.add(2,text='wxid_bob:\n请提交材料')
    db.add(3,text='wxid_alice:\n请提交报告')
    with sqlite3.connect(db.path) as c:c.execute(f'UPDATE {TABLE} SET local_type=10000,real_sender_id=14 WHERE local_id=1')
    result=collect_increment(db,'授权群',None,now=NOW)
    assert [m['sender_status'] for m in result['messages']]==['system','unresolved','resolved']
    assert json.loads(result['cursor']['shard_watermarks_json'])=={'one':3}
    assert result['messages'][1]['sender_username'] is None
    repeated=collect_increment(db,'授权群',result['cursor'],now=NOW)
    assert repeated['messages']==[] and repeated['message_body_queries']==0
    with sqlite3.connect(db.path) as c:c.execute("UPDATE Name2Id SET user_name='wxid_bob' WHERE rowid=1")
    db.add(4,text='wxid_bob:\n请补交新材料')
    updated=collect_increment(db,'授权群',repeated['cursor'],now=NOW+timedelta(minutes=1))
    assert len(updated['messages'])==1 and updated['messages'][0]['sender_username']=='wxid_bob'


def test_real_shard_mapper_keeps_same_numeric_id_separate_and_rejects_account_switch(tmp_path):
    from pathlib import Path
    import importlib.util,os,subprocess,sys
    root=Path(__file__).resolve().parents[2]
    runtime=root/'artifacts/wechat-db0/venv/Scripts/python.exe'
    if not runtime.exists():
        if importlib.util.find_spec('cryptography'):runtime=Path(sys.executable)
        else:pytest.skip('Isolated DB crypto runtime not installed')
    result=subprocess.run([str(runtime),'-m','server.tests_wechat.sender_shard_check',str(tmp_path)],cwd=root,text=True,encoding='utf-8',capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'passed':True,'distinct_users':2,'numeric_id':1,'cross_account_rejected':True}
