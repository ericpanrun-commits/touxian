import json
import sqlite3
from dataclasses import asdict
from datetime import datetime

import pytest

from server.personal_store import PersonalError, SCHEMA_VERSION
from server.user_context import RoleAssertion, UserContext
from server.wechat_collect.batching import build_batches
from server.wechat_collect.config import ChatConfig, SettingsDocument, WeChatConfig
from server.wechat_collect.filtering import classify
from server.wechat_collect.identity import reconcile, signature
from server.wechat_collect.models import Observation, ObservedMessage
from server.wechat_collect.scheduler import ScheduleState, SchedulerPolicy
from server.wechat_collect.spike import inspect_structure
from server.wechat_collect.store import WeChatStore


def at(value):
    return datetime.fromisoformat('2026-10-22T' + value + '+08:00')


@pytest.mark.parametrize('value,allowed',[('00:00:00',False),('05:59:59',False),('06:00:00',True),('23:58:00',True),('23:59:20',False)])
def test_hard_quiet_boundary(value, allowed):
    policy=SchedulerPolicy(WeChatConfig(enabled=True),lambda:at(value))
    assert policy.authorize(operation='llm',maximum_seconds=45).allowed is allowed


def test_spike_night_does_not_import_uia_or_windows(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules,'uiautomation',None)
    report=inspect_structure(clock=lambda:at('01:00:00'))
    assert report['real_operations']==0
    assert all(v['status']=='not_tested' for v in report['matrix'].values())
    assert report['safety']['messages_sent']==0


def test_schedule_coalesces_startup_six_and_resume():
    current=[at('05:59:00')]
    policy=SchedulerPolicy(WeChatConfig(enabled=True),lambda:current[0])
    schedule=ScheduleState(policy)
    schedule.resumed()
    assert not schedule.due()
    current[0]=at('06:00:00')
    assert schedule.due()=={'startup','resume','periodic','catch_up'}
    assert not schedule.due()
    current[0]=at('23:00:00')
    assert schedule.due()=={'periodic','core_reconcile'}


def test_unknown_role_is_not_false():
    context=UserContext(role_assertions={'班长':RoleAssertion(value='false',confirmed=True),'学习委员':RoleAssertion(value='false')})
    assert context.role('班长')=='false'
    assert context.role('学习委员')==context.role('干部')=='unknown'


def test_migration_and_occurrence_identity(personal):
    repository=WeChatStore(personal)
    doc=SettingsDocument(wechat=WeChatConfig(priority_chats=[ChatConfig(id='test',name='合成测试群')]))
    repository.configure(doc)
    chat=repository.chat('test')
    scan,_=repository.begin_scan('test',1)
    anchor=ObservedMessage('用于识别历史边界的独特测试通知',sender='老师')
    ack=ObservedMessage('收到',sender='同学')
    observation=Observation('test','unverified',(anchor,ack,ack),True,True)
    result,news,_=repository.capture(scan['id'],chat,observation)
    assert result.complete and len(news)==3 and news[1]['id']!=news[2]['id']
    next_scan,_=repository.begin_scan('test',1)
    result,news,_=repository.capture(next_scan['id'],chat,Observation('test','unverified',(anchor,ack,ack,ack),True))
    assert result.complete and len(news)==1
    assert len(repository.history('test'))==4
    with personal.connection() as c:
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert c.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==SCHEMA_VERSION
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0
    with pytest.raises(sqlite3.IntegrityError):
        with personal.connection(write=True) as c:
            c.execute("UPDATE wechat_message SET raw_text='changed'")


def test_no_anchor_does_not_claim_new():
    old=[dict(id='old',content_signature=signature(ObservedMessage('收到')),raw_text='收到')]
    result=reconcile(old,Observation('x','x',(ObservedMessage('收到'),ObservedMessage('收到')),True))
    assert not result.complete and not result.new_indices


@pytest.mark.parametrize('text,kind,expected',[
    ('收到','text','fast_reject'),('哈哈哈哈','text','fast_reject'),
    ('这是一段没有关键词的长篇内容'*12,'text','candidate'),
    ('@全体成员 今晚提交','text','force_analyze'),
    ('','image','candidate'),('上面的二维码','text','candidate')])
def test_conservative_filter(text,kind,expected):
    assert classify({'raw_text':text,'message_type':kind}).level==expected


def test_context_preserves_ack():
    assert classify({'raw_text':'收到','message_type':'text'},nearby_candidate=True).level=='candidate'


def test_budget_defaults_and_batch():
    config=WeChatConfig()
    assert (config.budget.http_daily,config.budget.tokens_daily,config.budget.vision_daily)==(30,100000,5)
    messages=[{'id':str(i),'raw_text':text,'message_type':'text'} for i,text in enumerate(['旧上下文','有个通知','大家今晚填一下','就是上面的二维码'])]
    plans=build_batches(messages,['1','2','3'])
    assert len(plans)==1 and plans[0].context==('0',) and plans[0].wait_for_context
