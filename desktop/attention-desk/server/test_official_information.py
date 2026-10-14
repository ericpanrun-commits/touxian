"""Phase 7 safety, migration, incremental ingestion and desktop projection contracts."""
import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.official_api import router
from server.official_http import HTMLResult, PublicHTMLClient
from server.official_parsers import parse_article, parse_list, published_date
from server.official_rules import classify, extract_times, text_hash
from server.official_service import OfficialSourceMonitor
from server.official_sources import SourceConfig, SourceFailure, canonical_url, url_identity
from server.official_store import OfficialStore
from server.personal_store import PersonalStore

TODAY=datetime.now(timezone.utc).date().isoformat()

def source(id='unit-source',**changes):
    return SourceConfig(id=id,name=id,school='肇庆学院',department='机械与汽车工程学院',category='学院通知',
        list_url=f'https://unit.zqu.edu.cn/{id}.htm',allowed_hosts=['unit.zqu.edu.cn'],parser_type='vsb_generic',
        source_type='official_college',priority='high',polling_interval=1800,max_items=5,**changes)

def listing(entries, next_page=''):
    return ('<ul>'+''.join(f'<li><a href="/info/1/{i}.htm" title="{title}">{title}</a><span>{publication}</span></li>' for i,title,publication in entries)+'</ul>'+ (f'<a href="{next_page}">下一页</a>' if next_page else '')).encode()

def document(title='车辆工程竞赛报名通知',body='请本科生报名。',date=TODAY):
    return f'<h1 id="article-title">{title}</h1><p id="article-info">{date}</p><div class="v_news_content"><p>{body}</p><a href="/files/forms.docx">报名表.docx</a></div>'.encode()

@pytest.fixture
def store(tmp_path):
    p=PersonalStore(tmp_path/'personal.sqlite3');p.initialize();OfficialStore(p).configure([source(),source('unit-other')]);return p

def monitor(tmp_path,store,mapping,ids=('unit-source',),**kwargs):
    config=tmp_path/'sources.json';config.write_text(json.dumps({'version':1,'sources':[source(id).model_dump() for id in ids]}),encoding='utf-8')
    calls=[]
    def factory(s,**options):
        def response(req):
            calls.append(str(req.url));value=mapping[str(req.url)]
            if isinstance(value,Exception): raise value
            return httpx.Response(200,headers={'content-type':'text/html'},content=value)
        return PublicHTMLClient(s,transport=httpx.MockTransport(response),resolver=lambda h:None,delay=0,retries=0,**options)
    return OfficialSourceMonitor(store,config_path=config,client_factory=factory,start_scheduler=False,**kwargs),calls

def scan(m,ids=None):
    m.start_scan(ids);m.worker.join(10);assert not m.worker.is_alive();return m.overview()

def seed(store,id=1,title='车辆工程竞赛报名通知',body='本科生参加竞赛。',s=None):
    s=s or source();url=f'https://unit.zqu.edu.cn/info/1/{id}.htm'
    article=parse_article(document(title,body),url,s,{'title':title,'published_at':TODAY})
    return OfficialStore(store).collect(s,url,url,article,HTMLResult(url,b'',200))

@pytest.mark.parametrize('raw,expected',[
    ('https://UNIT.ZQU.EDU.CN:443/a/../info/1/%32.htm?utm_source=x&z=2&a=1#top','https://unit.zqu.edu.cn/info/1/2.htm?a=1&z=2'),
    ('http://unit.zqu.edu.cn//info/1/2.htm','http://unit.zqu.edu.cn/info/1/2.htm')])
def test_url_normalization_and_storage_identity(raw,expected):
    assert canonical_url(raw,['unit.zqu.edu.cn'])==expected
    assert url_identity(expected).startswith('https://')

@pytest.mark.parametrize('url',['https://unit.zqu.edu.cn/%0aX','https://unit.zqu.edu.cn/%5csecret','file:///secret','https://unit.zqu.edu.cn.evil.test/','https://x@unit.zqu.edu.cn/'])
def test_untrusted_url_rejection(url):
    with pytest.raises(SourceFailure):canonical_url(url,['unit.zqu.edu.cn'])

@pytest.mark.parametrize('value,expected',[('2026年10月14日','2026-10-14'),('2026/2/29',None),('2024.2.29','2024-02-29'),('10月14日',None)])
def test_publication_dates(value,expected):assert published_date(value)==expected

@pytest.mark.parametrize('text,key,value',[
    ('报名截止：2026年10月20日18:00。','deadline_at','2026-10-20T18:00+08:00'),
    ('申请截至2026-10-20。','deadline_at','2026-10-20'),
    ('考试时间：2026年10月21日。','event_start_at','2026-10-21'),
    ('活动时间：2026年10月20日至2026年10月22日。','event_end_at','2026-10-22'),
    ('报名截止：10月20日。','deadline_at',None),
    ('报名截止：2026-02-30。','deadline_at',None),
    ('报名截止2026-10-20；提交截止2026-10-21。','deadline_at',None),
    ('发布于2026-10-14。','deadline_at',None),
    ('考试时间2026-10-21，另一次考试2026-10-22。','event_start_at',None)])
def test_conservative_time_roles(text,key,value):assert extract_times(text)[key]==value

@pytest.mark.parametrize('title,category,importance',[
    ('车辆工程竞赛报名','竞赛','high'),('选课截止时间变更','选课','critical'),('期末考试安排','考试','high'),
    ('国家奖学金申请','奖助学金','high'),('国际交流申请项目','国际交流','high'),('学院举行运动会','学院通知','low'),
    ('招聘会通知','招聘','high'),('学生专业实习通知','实习','high')])
def test_classification_relevance_importance(title,category,importance):
    result=classify({'title':title,'original_text':'公开原文'},source())
    assert result['category']==category and result['importance']==importance and result['relevance']>=90

def test_profile_is_configurable_and_publicity_is_low():
    s=source().model_copy(update={'department':'学校','source_type':'official_school','category':'校园新闻'})
    result=classify({'title':'学校召开工作会议','original_text':'新闻'},s)
    assert result['importance']=='low' and result['relevance']<=20
    assert text_hash('Ａ B\nＣ')==text_hash('ABC')

@pytest.mark.parametrize('title',['硕士研究生招生简章','2027届本科生毕业论文安排','2024级专业课通知','教师教学竞赛报名'])
def test_profile_demotes_inapplicable_audiences(title):
    result=classify({'title':title,'original_text':'车辆工程本科生相关介绍'},source())
    assert result['relevance']<=45

def test_generic_list_dates_title_dates_not_confused_and_pagination_bounded():
    s=source();body=listing([(1,'2020年1月1日材料补交通知',TODAY)],'https://evil.test/page')
    rows,next_page=parse_list(body,s.list_url,s)
    assert rows[0]['published_at']==TODAY and next_page is None

def test_malformed_html_sanitizes_scripts_events_urls_and_bounds_content():
    html=b'<h1>notice</h1><nav>menu</nav><div class="v_news_content" onclick="attack()"><p style="background:url(file:///x)">body</p><svg onload="attack()"></svg><script>attack()</script><img src="javascript:attack()"><a href="file:///secret">secret.docx</a></div>'
    article=parse_article(html,source().list_url,source(),{'title':'notice','published_at':None})
    assert article['original_text']=='body\nsecret.docx'
    assert all(bad not in article['body_html'] for bad in ['onclick','onload','javascript:','<script','file:///','style='])
    assert article['attachments']==[]

def test_http_retry_backoff_is_bounded_and_uses_conditional_headers():
    calls=[];waits=[]
    def handler(req):
        calls.append(req);assert req.headers['if-none-match']=='v1'
        return httpx.Response(503 if len(calls)<3 else 304)
    with PublicHTMLClient(source(),transport=httpx.MockTransport(handler),resolver=lambda h:None,delay=0) as client:
        client._pause=waits.append
        assert client.get(source().list_url,etag='v1').status==304
    assert len(calls)==3 and 1 in waits and 2 in waits

def test_list_to_db_incremental_repeat_and_body_update(tmp_path,store):
    s=source();url='https://unit.zqu.edu.cn/info/1/1.htm'
    mapping={s.list_url:listing([(1,'车辆工程竞赛报名通知',TODAY)]),url:document()}
    m,calls=monitor(tmp_path,store,mapping)
    assert scan(m)['scan']['new_count']==1
    o=OfficialStore(store);item=o.feed()['items'][0];assert o.detail(item['id'])['attachments'][0]['extension']=='docx'
    o.set_item_state(item['id'],is_read=True,is_saved=True)
    calls.clear();assert scan(m)['scan']['new_count']==0
    assert calls==[s.list_url]
    with store.connection(write=True) as c:c.execute("UPDATE official_fetch_state SET last_fetched_at='2000-01-01'")
    mapping[url]=document(body='更新的报名说明。')
    assert scan(m)['scan']['changed_count']==1
    detail=o.detail(item['id']);assert detail['version']==2 and detail['is_read'] and detail['is_saved'] and '更新' in detail['content']
    with store.connection() as c:assert c.execute('SELECT COUNT(*) FROM event').fetchone()[0]==0
    m.close()

def test_first_and_subsequent_scans_never_import_ancient_backlog(tmp_path,store):
    s=source();mapping={s.list_url:listing([(1,'旧通知','2010-01-01')])}
    m,calls=monitor(tmp_path,store,mapping)
    scan(m);scan(m)
    assert OfficialStore(store).feed()['total']==0 and calls==[s.list_url,s.list_url]

def test_failed_source_does_not_stop_other_source_and_health_recovers(tmp_path,store):
    a,b=source(),source('unit-other');url='https://unit.zqu.edu.cn/info/1/2.htm'
    mapping={a.list_url:httpx.ConnectError('failure'),b.list_url:listing([(2,'正常通知',TODAY)]),url:document('正常通知')}
    m,_=monitor(tmp_path,store,mapping,ids=(a.id,b.id))
    for _ in range(3):scan(m)
    rows={r['id']:r for r in m.overview()['sources']}
    assert rows[a.id]['health']=='degraded' and rows[b.id]['health']=='ok' and OfficialStore(store).feed()['total']==1
    mapping[a.list_url]=listing([(2,'正常通知',TODAY)])
    scan(m);assert m.overview()['sources'][0]['failure_count']==0

def test_canonical_http_variants_and_reprints_keep_references_and_state(store):
    first=seed(store,body='原文内容'*60)
    second=seed(store,2,body='原文内容'*60,s=source('unit-other'))
    o=OfficialStore(store);assert o.feed()['total']==1
    assert len(o.detail(first['raw_item_id'])['sources'])==2
    o.set_item_state(first['raw_item_id'],is_read=True,is_saved=True)
    assert o.detail(second['raw_item_id'])['is_saved']
    assert o.feed('unread')['total']==0 and o.feed('saved')['total']==1
    discovered=o.discover(source(),{'url':'http://unit.zqu.edu.cn/info/1/1.htm','title':'same','published_at':TODAY})
    assert not discovered['new'] and discovered['raw_item_id']==first['raw_item_id']

def test_same_title_different_content_not_merged_and_pagination_is_bounded(store):
    seed(store,body='first');seed(store,2,body='second')
    o=OfficialStore(store);page=o.feed(limit=1)
    assert page['total']==2 and page['has_more'] and len(page['items'])==1
    assert o.feed(offset=1,limit=1)['items'][0]['id']!=page['items'][0]['id']

def test_cross_process_lease_mutex_and_crash_recovery(store):
    o=OfficialStore(store);assert o.acquire_lease('parent')
    code='from pathlib import Path;from server.personal_store import PersonalStore;from server.official_store import OfficialStore;import sys;print(OfficialStore(PersonalStore(Path(sys.argv[1]))).acquire_lease("child"))'
    result=subprocess.run([sys.executable,'-c',code,str(store.path)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0 and result.stdout.strip()=='False'
    o.begin(source().id,'parent')
    with store.connection(write=True) as c:c.execute("UPDATE official_job_lease SET expires_at='2000-01-01'")
    assert o.acquire_lease('recovered') and not o.renew_lease('parent')
    assert o.recent_runs()[0]['status']=='interrupted'
    o.release_lease('parent');assert o.lease_active();o.release_lease('recovered');assert not o.lease_active()

def test_concurrent_lease_claim_has_exactly_one_winner(store):
    with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(lambda i:OfficialStore(store).acquire_lease(str(i)),range(6)))
    assert sum(results)==1

def test_transient_lease_release_failure_never_strands_local_scheduler(tmp_path,store,monkeypatch):
    s=source();m,_=monitor(tmp_path,store,{s.list_url:listing([])})
    # An explicitly empty verified list is valid; exercise a completed worker
    # whose final DB write fails, then recover after the persisted lease expires.
    monkeypatch.setattr('server.official_service.parse_list',lambda *args:([],None))
    release=m.store.release_lease
    monkeypatch.setattr(m.store,'release_lease',lambda owner:(_ for _ in ()).throw(sqlite3.OperationalError('busy')))
    scan(m)
    assert not m._run_lock.locked() and not m._run['running']
    with store.connection(write=True) as c:c.execute("UPDATE official_job_lease SET expires_at='2000-01-01'")
    monkeypatch.setattr(m.store,'release_lease',release)
    assert scan(m)['scan']['done']==1

def test_notifier_suppresses_backfill_updates_duplicates_and_limits_bursts(tmp_path,store):
    sent=[];notifier=SimpleNamespace(send=lambda text,**kw:sent.append(kw['delivery_id']) or 'os_accepted')
    s=source();mapping={s.list_url:listing([(1,'车辆工程竞赛报名通知',TODAY)]),'https://unit.zqu.edu.cn/info/1/1.htm':document()}
    m,_=monitor(tmp_path,store,mapping,notifier=notifier)
    scan(m);assert sent==[]
    for i in range(2,7):
        saved=seed(store,i,body=f'第{i}条新的报名通知');m._notify(saved['raw_item_id']);m._notify(saved['raw_item_id'])
    assert len(sent)==3 and len(set(sent))==3
    with store.connection() as c:assert c.execute('SELECT COUNT(*) FROM official_notification').fetchone()[0]==3
    with store.connection(write=True) as c:c.execute('UPDATE reminder_settings SET enabled=0')
    m._notify(seed(store,7)['raw_item_id']);assert len(sent)==3

def test_projection_api_filters_states_validation_and_no_ai(store):
    saved=seed(store);app=FastAPI();app.include_router(router);app.state.personal_store=store
    with TestClient(app) as client:
        path='/api/personal/official'
        assert client.get(path+'/feed?view=college').json()['total']==1
        assert client.get(path+'/feed?view=academic').json()['total']==0
        assert client.get(path+'/feed?limit=500').status_code==422
        assert client.get(path+'/feed?view=invalid').status_code==422
        assert client.patch(path+'/items/'+saved['raw_item_id'],json={'is_read':'yes'}).status_code==422
        assert client.patch(path+'/items/'+saved['raw_item_id'],json={'is_read':True,'is_saved':True}).json()['is_read']
        assert client.get(path+'/feed?view=saved').json()['total']==1
    with store.connection() as c:assert c.execute('SELECT COUNT(*) FROM ai_extraction').fetchone()[0]==0

def test_migration_007_preserves_all_old_rows_and_foreign_keys(tmp_path,monkeypatch):
    import server.personal_store as module
    monkeypatch.setattr(module,'SCHEMA_VERSION',6)
    p=PersonalStore(tmp_path/'old.sqlite3');p.initialize()
    from server.personal_models import CollectionInput
    p.create_collection(CollectionInput(original_text='原始通知保持不变',title='原有事项',category='其他'))
    with p.connection() as c:
        tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_migrations'")]
        before={t:([r[1] for r in c.execute(f'PRAGMA table_info("{t}")')],[tuple(r) for r in c.execute(f'SELECT * FROM "{t}"')]) for t in tables}
    monkeypatch.setattr(module,'SCHEMA_VERSION',7);p.initialize();p.initialize()
    with p.connection() as c:
        for table,(cols,rows) in before.items():assert [tuple(r) for r in c.execute('SELECT '+','.join('"'+x+'"' for x in cols)+f' FROM "{table}"')]==rows
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok'
