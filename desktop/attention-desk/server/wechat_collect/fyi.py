"""Independent FYI feed lifecycle; never promotes AI classifications or sends pushes."""
from datetime import datetime,timedelta,timezone
import json

from ..personal_store import PersonalError,now

UTC=timezone.utc
JOIN=" FROM wechat_candidate ca JOIN wechat_batch b ON b.id=ca.batch_id JOIN wechat_chat ch ON ch.id=b.chat_id JOIN raw_review r ON r.raw_snapshot_id=b.snapshot_id JOIN wechat_fyi_state f ON f.candidate_id=ca.id "
SELECT="SELECT ca.*,b.snapshot_id,ch.name AS chat_name,r.version AS review_version,f.state AS fyi_state,f.source_at,f.expires_at,f.pinned_until,f.version AS fyi_version,f.archive_reason,f.event_id AS converted_event_id"
ELIGIBLE="ca.attention_level='fyi' AND ca.status IN ('fyi','accepted') AND b.superseded_by IS NULL"


def instant(value):
    try:
        result=datetime.fromisoformat(value)
        if result.tzinfo is None:
            from .scheduler import SHANGHAI
            result=result.replace(tzinfo=SHANGHAI)
        return result.astimezone(UTC)
    except (TypeError,ValueError):
        return None


def refresh(personal,at=None):
    at=(at or datetime.now(UTC)).astimezone(UTC)
    stamp=at.isoformat()
    with personal.connection(write=True) as c:
        missing=c.execute("SELECT ca.id,ca.data_json,ca.created_at,ca.batch_id FROM wechat_candidate ca WHERE ca.attention_level='fyi' AND ca.status='fyi' AND NOT EXISTS(SELECT 1 FROM wechat_fyi_state f WHERE f.candidate_id=ca.id)").fetchall()
        for row in missing:
            cited=set(json.loads(row['data_json']).get('evidence_message_ids') or [])
            messages=c.execute('SELECT m.id,m.source_time,m.time_reliability FROM wechat_batch_message bm JOIN wechat_message m ON m.id=bm.message_id WHERE bm.batch_id=?',(row['batch_id'],)).fetchall()
            times=[instant(m['source_time']) for m in messages if m['id'] in cited and m['time_reliability']=='reliable']
            source=max((t for t in times if t is not None),default=instant(row['created_at']) or at)
            c.execute('INSERT INTO wechat_fyi_state(candidate_id,source_at,expires_at,updated_at) VALUES (?,?,?,?)',(row['id'],source.isoformat(),(source+timedelta(days=7)).isoformat(),stamp))
        c.execute("UPDATE wechat_fyi_state SET state='archived',archive_reason='expired',pinned_until=NULL,version=version+1,updated_at=? WHERE state='active' AND julianday(expires_at)<=julianday(?) AND (pinned_until IS NULL OR julianday(pinned_until)<=julianday(?))",(stamp,stamp,stamp))


def hydrate(c,rows):
    result=[]
    for raw in rows:
        row=dict(raw)
        row['analysis']=json.loads(row.pop('data_json'))
        cited=set(row['analysis'].get('evidence_message_ids') or [])
        source=c.execute('SELECT m.id,m.sender,m.source_time,m.message_type FROM wechat_batch_message bm JOIN wechat_message m ON m.id=bm.message_id WHERE bm.batch_id=? ORDER BY m.ingest_seq',(row['batch_id'],)).fetchall()
        row['messages']=[dict(m) for m in source if m['id'] in cited]
        row['source_type']='wechat'
        result.append(row)
    return result


def get(c,candidate_id):
    row=c.execute(SELECT+JOIN+' WHERE ca.id=? AND '+ELIGIBLE,(candidate_id,)).fetchone()
    if not row:
        raise PersonalError('这条参考信息已更新或不存在，请刷新。','not_found',404)
    return hydrate(c,[row])[0]


def feed(personal,*,limit=3,offset=0,view='recent',at=None):
    at=at or datetime.now(UTC)
    refresh(personal,at)
    state="f.state='active'" if view=='recent' else "f.state IN ('archived','ignored','converted')"
    query=JOIN+' WHERE '+ELIGIBLE+' AND '+state
    with personal.connection() as c:
        counts={r[0]:r[1] for r in c.execute("SELECT f.state,count(*)"+JOIN+' WHERE '+ELIGIBLE+' GROUP BY f.state')}
        rows=c.execute(SELECT+query+" ORDER BY CASE WHEN julianday(f.pinned_until)>julianday(?) THEN 0 ELSE 1 END,f.source_at DESC,ca.id DESC LIMIT ? OFFSET ?",(at.isoformat(),limit,offset)).fetchall()
        return {'items':hydrate(c,rows),'total':c.execute('SELECT count(*)'+query).fetchone()[0],
                'limit':limit,'offset':offset,'view':view,'recent_total':counts.get('active',0),
                'archived_total':sum(counts.get(s,0) for s in ('archived','ignored','converted')),'retention_days':7}


def mutate(personal,candidate_id,action,expected_version,*,at=None):
    at=(at or datetime.now(UTC)).astimezone(UTC)
    refresh(personal,at)
    with personal.connection(write=True) as c:
        row=get(c,candidate_id)
        if row['fyi_version']!=expected_version:
            raise PersonalError('这条信息已在其他页面更新。当前内容已保留，请重新读取后重试。','conflict',409)
        if row['fyi_state']=='converted':
            raise PersonalError('信息已转为正式事项，请在事项中管理。','review_closed',409)
        state=row['fyi_state'];expires=row['expires_at'];pin=row['pinned_until'];reason=row['archive_reason']
        if action in {'archive','ignore'}:
            state='archived' if action=='archive' else 'ignored';reason='manual';pin=None
        elif action=='restore':
            state='active';expires=(at+timedelta(days=7)).isoformat();reason=None
        elif action=='pin':
            state='active';pin=(at+timedelta(days=30)).isoformat();reason=None
        elif action=='unpin':
            pin=None
            if instant(expires)<=at:state='archived';reason='expired'
        else:
            raise PersonalError('请选择有效操作。')
        c.execute('UPDATE wechat_fyi_state SET state=?,expires_at=?,pinned_until=?,archive_reason=?,version=version+1,updated_at=? WHERE candidate_id=?',(state,expires,pin,reason,at.isoformat(),candidate_id))
        return {'saved':True,'item':get(c,candidate_id)}


def conversion_preview(personal,candidate_id,*,at=None):
    from ..review_store import ReviewStore
    refresh(personal,at)
    with personal.connection() as c:
        row=get(c,candidate_id)
        if row['fyi_state']=='converted':
            raise PersonalError('信息已经转为事项，请打开已有事项。','review_closed',409)
        bundle=ReviewStore._bundle(c,row['snapshot_id'])
        item=row['analysis']
        event={'id':row['model_item_id'],'selected':True,'title':item['title'],'summary':item.get('summary',''),
               'category':'notice','relevance':'yes' if item.get('relevant') is True else 'unclear','participation':'later',
               'notice_kind':'new','audience':None,'eligibility':[],'deadlines':[],'actions':[],'materials':[],'locations':[],'uncertainties':[]}
        bundle['draft']={'acknowledged_warnings':False,'events':[event]}
        bundle['fyi_version']=row['fyi_version']
        bundle['fyi_conversion']=True
        return bundle
