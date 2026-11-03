"""Candidate pushes use a separate ledger and can never confirm an event."""
import json
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.notifications.config import BarkConfig
from server.notifications.wechat import WeChatBarkDispatcher, candidate_level
from server.notifications.wechat_api import router
from server.personal_store import PersonalError
from server.tests_wechat.test_workbench import populated

NOW = datetime.fromisoformat('2026-10-23T12:00:00+08:00')
KEY = 'synthetic-private-device-key'
BASE = dict(attention_level='action',information_complete=True,await_more_context=False,important=True, relevant=True, category='task', action_required=True,
            participation='required', priority='P1', notice_kind='new', title='提交作业')


@pytest.mark.parametrize('change,expected', [
    ({}, 'active'), ({'category':'deadline'}, 'active'),
    ({'category':'notice','notice_kind':'information'}, None),
    ({'category':'event'}, None), ({'important':False}, None),
    ({'relevant':None}, None), ({'relevant':False}, None),
    ({'participation':'optional'}, None), ({'analysis_origin':'local_pending'}, None),
    ({'priority':'P0'}, 'timeSensitive'), ({'category':'course_change'}, 'timeSensitive'),
    ({'deadline':{'value':'2026-10-23T17:00'}}, 'timeSensitive'),
    ({'notice_kind':'update'}, 'timeSensitive'),
])
def test_importance_policy(change, expected):
    assert candidate_level({**BASE, **change}, NOW) == expected


def setup(personal, handler):
    populated(personal)
    clock = [NOW]
    dispatcher = WeChatBarkDispatcher(personal, config=BarkConfig(True, 'https://bark.invalid', KEY),
                                      transport=httpx.MockTransport(handler), clock=lambda: clock[0])
    with personal.connection(write=True) as c:
        c.execute("UPDATE wechat_bark_settings SET enabled=1,activated_at='2020-01-01'")
        # Only one synthetic candidate is actionable; model scores alone don't qualify notices.
        rows = c.execute("SELECT id,data_json FROM wechat_candidate WHERE status='needs_review'").fetchall()
        for row in rows:
            item = json.loads(row['data_json'])
            item['priority'] = 'P1'
            c.execute('UPDATE wechat_candidate SET data_json=? WHERE id=?',(json.dumps(item),row['id']))
    return dispatcher, clock


def test_post_once_across_restart_without_confirming(personal):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'code':200})
    dispatcher, _ = setup(personal, handler)
    dispatcher.tick(); dispatcher.tick()
    restarted = WeChatBarkDispatcher(personal, config=dispatcher.environment, transport=dispatcher.transport, clock=lambda: NOW)
    restarted.tick()
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert requests[0].method == 'POST' and requests[0].url.path == '/push'
    assert body['device_key'] == KEY and body['title'] == 'Attention Desk · 微信'
    assert body['subtitle'] == '合成测试群' and body['level'] == 'active'
    assert not {'confidence','schema','reason'} & body.keys()
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM event').fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM wechat_bark_notification WHERE state='sent'").fetchone()[0] == 1
    assert KEY not in json.dumps(dispatcher.settings())


@pytest.mark.parametrize('mode,count', [('connect',3),('read',1),('crash',0)])
def test_bounded_retry_and_uncertain_outcome(personal, mode, count):
    requests = []
    def handler(request):
        requests.append(request)
        raise httpx.ConnectTimeout(KEY) if mode == 'connect' else httpx.ReadTimeout(KEY)
    dispatcher, clock = setup(personal, handler)
    if mode == 'crash':
        dispatcher.tick()
        # Simulate process loss after a persisted claim and before a durable outcome.
        with personal.connection(write=True) as c:
            c.execute("UPDATE wechat_bark_notification SET state='sending',retry_at='2020-01-01'")
        requests.clear()
    for _ in range(6):
        dispatcher.tick(); clock[0] += timedelta(minutes=10)
    assert len(requests) == count
    with personal.connection() as c:
        rows = [dict(row) for row in c.execute('SELECT * FROM wechat_bark_notification')]
    assert KEY not in json.dumps(rows)
    assert rows[0]['state'] == 'failed' and not rows[0]['retryable']


def test_quiet_ignored_and_old_history_do_not_push(personal):
    requests=[]
    dispatcher,clock=setup(personal, lambda request: requests.append(request) or httpx.Response(200,json={'code':200}))
    clock[0]=NOW.replace(hour=3); dispatcher.tick()
    assert not requests
    clock[0]=NOW
    with personal.connection(write=True) as c:
        c.execute("UPDATE wechat_candidate SET status='ignored'")
    dispatcher.tick()
    assert not requests
    with personal.connection(write=True) as c:
        c.execute("UPDATE wechat_candidate SET status='needs_review' WHERE model_item_id!='_pending'")
        c.execute("UPDATE wechat_bark_settings SET activated_at='2099-01-01'")
    dispatcher.tick()
    assert not requests


def test_secret_masking_origin_guard_encryption_and_test_cooldown(personal):
    requests=[]
    dispatcher,_=setup(personal, lambda request: requests.append(request) or httpx.Response(200,json={'code':200}))
    app=FastAPI();app.state.wechat_bark=dispatcher;app.include_router(router)
    client=TestClient(app)
    path='/api/personal/notifications/wechat-bark'
    public=client.get(path+'/settings').json()
    payload=dict(enabled=True,server='https://api.day.app',device_key=KEY,expected_version=public['version'])
    denied=client.put(path+'/settings',json=payload,headers={'origin':'https://untrusted.example'})
    assert denied.status_code==403
    saved=client.put(path+'/settings',json=payload)
    assert saved.status_code==200 and KEY not in saved.text
    with personal.connection() as c:
        encrypted=c.execute('SELECT protected_key FROM wechat_bark_settings').fetchone()[0]
    assert encrypted and KEY not in encrypted
    assert client.post(path+'/test',json={}).json()['accepted']
    assert client.post(path+'/test',json={}).json()['result']=='test_cooldown'
    assert len(requests)==1
    bad=client.put(path+'/settings',json={**payload,'server':'https://api.day.app/'+KEY})
    assert bad.status_code==400 and KEY not in bad.text
    with pytest.raises(PersonalError):
        dispatcher.save({**payload,'expected_version':1})

@pytest.mark.parametrize('category',['exam','registration','notice'])
def test_explicit_evidenced_action_pushes_regardless_of_category_label(category):
    item={**BASE,'category':category,'priority':'P2','actions':[{'kind':'explicit','title':'提交材料','evidence_ids':['q1']}]}
    assert candidate_level(item,NOW,'high')=='active'
    assert candidate_level(item,NOW,'medium')=='active'
    assert candidate_level(item,NOW,'low') is None
    item['important']=False
    assert candidate_level(item,NOW,'high') is None
