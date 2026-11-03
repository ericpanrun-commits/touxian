"""Run the upstream mapper in the isolated crypto runtime, against synthetic DBs."""
from pathlib import Path
from datetime import datetime
import hashlib,json,sqlite3,sys
from server.wechat_collect.replica_db_core import WeChatDB
from server.wechat_collect.db_reader import collect_increment,DBReadError,QueryBoundary

def check(directory):
    room='synthetic@chatroom';table='Msg_'+hashlib.md5(room.encode()).hexdigest()
    now=datetime.fromisoformat('2026-10-23T12:00:00+08:00');paths=[]
    for index,user in enumerate(('wxid_alice','wxid_bob')):
        path=directory/f'message_{index}.db'
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE Name2Id(user_name)');c.execute('INSERT INTO Name2Id(rowid,user_name) VALUES(1,?)',(user,))
            c.execute(f'CREATE TABLE {table}(local_id,local_type,real_sender_id,create_time,message_content,source,packed_info_data,compress_content,server_id,sort_seq)')
            c.execute(f'INSERT INTO {table} VALUES(1,1,1,?,?,NULL,NULL,NULL,1,1)',(int(now.timestamp()),user+':\n请提交实验报告'))
        paths.append(path)
    class Reader(WeChatDB):
        def __init__(self):
            self.account='synthetic_account';self._keys={str(p):b'synthetic' for p in paths}
            self.boundary=QueryBoundary();self.boundary.allow(room)
        def resolve(self,name):return room
        def _message_dbs(self):return [str(p) for p in paths]
        def _msg_conns(self,user):
            result=[]
            for path in paths:
                c=sqlite3.connect(path);c.row_factory=sqlite3.Row;self.boundary.protect(c);result.append((c,table))
            return result
        def get_nickname(self,user):return user
    reader=Reader();result=collect_increment(reader,'synthetic',None,now=now)
    assert {m['sender_username'] for m in result['messages']}=={'wxid_alice','wxid_bob'}
    assert {m['sender_id'] for m in result['messages']}=={1}
    assert len({m['stable_id'] for m in result['messages']})==2
    assert all(m['sender_prefix_matches'] is True for m in result['messages'])
    assert collect_increment(reader,'synthetic',result['cursor'],now=now)['message_body_queries']==0
    reader.account='another_synthetic_account'
    try:collect_increment(reader,'synthetic',result['cursor'],now=now)
    except DBReadError as error:assert str(error)=='database_account_or_chat_changed'
    else:raise AssertionError('Account switch must be rejected before body reads')
    assert reader.boundary.denied_queries==0
    return {'passed':True,'distinct_users':2,'numeric_id':1,'cross_account_rejected':True}

if __name__=='__main__':print(json.dumps(check(Path(sys.argv[1]))))
