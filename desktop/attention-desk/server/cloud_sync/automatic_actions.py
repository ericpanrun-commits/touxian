"""Minimal v2 task publication, discovered from committed automatic receipts."""
import copy
import hashlib
import json

from ..personal_store import PersonalStore, encode
from ..wechat_collect.automation import POLICY
from .deepseek_preview import _ReadSnapshot
from .minimization import selected_text_preview,redact_text
from .minimized_projection import minimized_projection_preview
from .projection import record
from .wechat_outbound_validation import validate_outbound, _safe_text

CORE={'raw_item','raw_review','inbox','inbox_item'}
FORMAL={'event','task','deadline'}


def text_lineage(c,raw_id,first,second,extraction_id):
    snapshots=[c.execute('SELECT * FROM raw_snapshot WHERE id=?',(sid,)).fetchone() for sid in (first,second)]
    reviews=[c.execute('SELECT * FROM raw_review WHERE raw_snapshot_id=?',(sid,)).fetchone() for sid in (first,second)]
    if any(not s or s['raw_item_id']!=raw_id for s in snapshots) or any(not r or r['extraction_id']!=extraction_id for r in reviews):return False
    if any(snapshots[0][k]!=snapshots[1][k] for k in ('original_text','source_time','source_timezone','hash')):return False
    for table,column,ignored in [('wechat_snapshot_message','snapshot_id',{'snapshot_id'}),('raw_source_block','raw_snapshot_id',{'raw_snapshot_id','attachment_id'})]:
        rows=[[{k:r[k] for k in r.keys() if k not in ignored} for r in c.execute('SELECT * FROM '+table+' WHERE '+column+'=? ORDER BY block_id',(sid,))] for sid in (first,second)]
        if not rows[0] or rows[0]!=rows[1]:return False
    return True


def v1_source_records(records):
    """Validate the same strict text projection without authorizing v1 tasks."""
    core={r['kind']:copy.deepcopy(r) for r in records if r['kind'] in CORE}
    if set(core)!=CORE: raise ValueError('automatic_source_missing')
    review=core['raw_review']['payload']; summary=core['inbox_item']['payload']; detail=core['inbox']['payload']
    if (review['status'] not in {'pending','confirmed'} or summary['needs_manual'] is not False
            or summary['processing_status']!='complete' or summary['processing_stage']!='complete'
            or not review['confirmed_event_ids']):
        raise ValueError('automatic_source_state_invalid')
    review['status']='archived'; review['confirmed_event_ids']=[]
    summary.update(needs_manual=True,processing_status='needs_review',processing_stage='review',review_status='archived')
    detail.update(summary); detail['review']=review
    return core


def validate_actions(bundle):
    records=bundle['records']
    if not 6<=len(records)<=128 or bundle.get('policy')!=POLICY or bundle['record_count']!=len(records):
        raise ValueError('invalid_automatic_bundle')
    if len({(r['kind'],r['id']) for r in records})!=len(records):
        raise ValueError('duplicate_automatic_record')
    if any(r['kind'] not in CORE|FORMAL or record(r['kind'],r['id'],r['payload'])!=r for r in records):
        raise ValueError('invalid_automatic_hash')
    core=v1_source_records(records)
    raw=core['raw_item']['payload']; sid=core['raw_review']['id']
    validate_outbound(core,raw['id'],sid,raw['source_id'])
    events={r['id']:r['payload'] for r in records if r['kind']=='event'}
    tasks={r['id']:r['payload'] for r in records if r['kind']=='task'}
    deadlines={r['id']:r['payload'] for r in records if r['kind']=='deadline'}
    original_review=next(r['payload'] for r in records if r['kind']=='raw_review')
    if not events or set(original_review['confirmed_event_ids'])!=set(events):
        raise ValueError('automatic_event_scope_changed')
    for eid,event in events.items():
        if (event['id']!=eid or event['raw_item_id']!=raw['id'] or event['raw_snapshot_id']!=sid
                or event['status']!='needs_action' or event['automation']['policy']!=POLICY
                or event['raw_snapshot']!=original_review['snapshot'] or not event['tasks']
                or not _safe_text(event['title']) or not _safe_text(event['summary'])):
            raise ValueError('automatic_event_invalid')
        for task in event['tasks']:
            if (tasks.get(task['id'])!=task or task['event_id']!=eid or task['origin']!='explicit'
                    or task['status']!='todo' or not _safe_text(task['title'])
                    or not any(q['field_path']=='tasks.'+task['id']+'.title' and task['title'] in q['quote'] for q in event['evidence'])):
                raise ValueError('automatic_task_invalid')
        for deadline in event['deadlines']:
            if (deadlines.get(deadline['id'])!=deadline or deadline['event_id']!=eid
                    or deadline['confirmed_by_user'] is not False or deadline.get('verified_automatically') is not True or not _safe_text(deadline['original_text'])):
                raise ValueError('automatic_deadline_invalid')
        blocks={b['block_id']:b['text'] for b in event['raw_snapshot']['blocks']}
        for evidence in event['evidence']:
            text=blocks.get(evidence['block_id'],'')
            if (evidence['event_id']!=eid or evidence['raw_snapshot_id']!=sid or not _safe_text(evidence['quote'])
                    or text[evidence['start']:evidence['end']]!=evidence['quote']):
                raise ValueError('automatic_evidence_changed')
    if set(tasks)!={t['id'] for e in events.values() for t in e['tasks']} or set(deadlines)!={d['id'] for e in events.values() for d in e['deadlines']}:
        raise ValueError('unowned_automatic_children')
    return core


def freeze_actions(bundle, scope_id):
    from .wechat_incremental_journal import SHA, IDENTITY
    core=validate_actions(bundle)
    if (not SHA.fullmatch(scope_id) or bundle.get('preview_only') is not True
            or bundle.get('upload_authorized') is not False or bundle.get('resources')!=[]):
        raise ValueError('automatic_permission_missing')
    raw=core['raw_item']; review=core['raw_review']; records=bundle['records']
    messages=review['payload']['snapshot']['wechat']['messages']
    mids=sorted(m['message_id'] for m in messages)
    if len(mids)!=len(set(mids)) or any(not IDENTITY.fullmatch(mid) for mid in mids):
        raise ValueError('invalid_automatic_source')
    guards=bundle['local_guards']
    if (len(guards)!=1 or guards[0]['raw_id']!=raw['id'] or guards[0]['snapshot_id']!=review['id']
            or any(not SHA.fullmatch(guards[0][k]) for k in ('raw_guard','review_guard'))):
        raise ValueError('automatic_guards_missing')
    actual=hashlib.sha256(encode(records).encode()).hexdigest()
    if actual!=bundle['bundle_hash']: raise ValueError('automatic_bundle_changed')
    return {'dedupe_key':hashlib.sha256(encode([raw['payload']['source_id'],raw['id'],mids]).encode()).hexdigest(),
        'scope_id':scope_id,'source_id':raw['payload']['source_id'],'raw_id':raw['id'],'snapshot_id':review['id'],
        'message_ids':mids,'bundle_hash':actual,
        'records':sorted(({'kind':r['kind'],'id':r['id'],'source_hash':r['source_hash']} for r in records),key=lambda r:(r['kind'],r['id'])),
        'raw_guard':guards[0]['raw_guard'],'review_guard':guards[0]['review_guard']}


def metadata_valid(value):
    from .wechat_incremental_journal import SHA, IDENTITY
    rows=value['records']
    if not isinstance(rows,list) or not 6<=len(rows)<=128: return False
    if any(set(r)!={'kind','id','source_hash'} or r['kind'] not in CORE|FORMAL
           or not IDENTITY.fullmatch(r['id']) or not SHA.fullmatch(r['source_hash']) for r in rows): return False
    if rows!=sorted(rows,key=lambda r:(r['kind'],r['id'])) or len({(r['kind'],r['id']) for r in rows})!=len(rows): return False
    core={r['kind']:r['id'] for r in rows if r['kind'] in CORE}
    return core=={'raw_item':value['raw_id'],'raw_review':value['snapshot_id'],'inbox':value['raw_id'],'inbox_item':value['raw_id']} and {'event','task'}<={r['kind'] for r in rows}


def build_automatic_preview(c, deepseek, job_id):
    """Read committed events, never full event DTOs or uncommitted model output."""
    job=c.execute("SELECT * FROM wechat_analysis_job WHERE id=? AND status='parsed'",(job_id,)).fetchone()
    if not job: raise ValueError('automatic_analysis_not_complete')
    batch=c.execute('SELECT * FROM wechat_batch WHERE id=?',(job['batch_id'],)).fetchone()
    if not batch or batch['superseded_by']: raise ValueError('automatic_source_replaced')
    batch=dict(batch)
    selected=[]
    for r in c.execute("SELECT d.event_id,d.after_json FROM user_decision d JOIN event e ON e.id=d.event_id WHERE d.action='wechat_auto_created' AND e.raw_item_id=?",(batch['raw_item_id'],)):
        proof=json.loads(r['after_json'])['automation']
        if proof['job_id']==job_id and proof['policy']==POLICY:
            selected.append((r['event_id'],proof))
    if not selected: raise ValueError('no_automatic_personal_tasks')
    snapshots={r[0] for eid,_ in selected for r in c.execute('SELECT raw_snapshot_id FROM event WHERE id=?',(eid,))}
    if len(snapshots)!=1:raise ValueError('automatic_event_snapshot_mixed')
    event_snapshot=next(iter(snapshots))
    if (not text_lineage(c,batch['raw_item_id'],job['snapshot_id'],event_snapshot,job['extraction_id'])
            or not text_lineage(c,batch['raw_item_id'],event_snapshot,batch['snapshot_id'],job['extraction_id'])):
        raise ValueError('automatic_snapshot_lineage_changed')
    batch['snapshot_id']=event_snapshot
    # Recheck the original DeepSeek scope without sending or reanalysing.
    from ..wechat_collect.deepseek_content_gate import prepare_authorized_projection
    from ..wechat_collect.analysis import primary_message_ids, snapshot_with_metadata
    b=dict(batch); b['primary_ids']=primary_message_ids(c,b['id'])
    snapshot=snapshot_with_metadata(c,c.execute('SELECT * FROM raw_snapshot WHERE id=?',(batch['snapshot_id'],)).fetchone())
    prepare_authorized_projection(_ReadSnapshot(c),b,snapshot,deepseek)
    evidence=[dict(r) for eid,_ in selected for r in c.execute('SELECT * FROM evidence WHERE event_id=?',(eid,))]
    message_quotes={}
    for e in evidence:
        mapping=c.execute('SELECT message_id FROM wechat_snapshot_message WHERE snapshot_id=? AND block_id=?',(batch['snapshot_id'],e['block_id'])).fetchone()
        if not mapping: raise ValueError('automatic_evidence_missing')
        message_quotes.setdefault(mapping[0],[])
        if e['quote'] not in message_quotes[mapping[0]]: message_quotes[mapping[0]].append(e['quote'])
    personal=_ReadSnapshot(c)
    selections=[{'raw_item_id':batch['raw_item_id'],'snapshot_id':batch['snapshot_id'],
        'title':c.execute('SELECT title FROM event WHERE id=?',(selected[0][0],)).fetchone()[0],
        'reason':'已由独立授权及原文规则验证的个人行动。',
        'messages':[{'id':mid,'excerpts':quotes} for mid,quotes in sorted(message_quotes.items())]}]
    preview=selected_text_preview(personal,selections)
    bundle=minimized_projection_preview(personal,preview,lineage_batch_id=batch['id'])
    records=bundle['records']; bykind={r['kind']:r['payload'] for r in records}
    review=bykind['raw_review']; summary=bykind['inbox_item']; detail=bykind['inbox']
    # This protocol publishes the immutable source of the committed event,
    # rather than the mutable local Inbox cursor or unrelated pending edits.
    bykind['raw_item']['inbox_version']=1
    bykind['raw_review']['item']=bykind['raw_item']
    review.update(status='confirmed',version=1,
        updated_at=max(c.execute('SELECT created_at FROM event WHERE id=?',(eid,)).fetchone()[0] for eid,_ in selected))
    review['confirmed_event_ids']=[eid for eid,_ in selected]
    summary.update(needs_manual=False,processing_status='complete',processing_stage='complete',review_status='confirmed',version=1)
    detail.update(summary); detail['review']=review
    minimal_snapshot=review['snapshot']; blocks={b['block_id']:b['text'] for b in minimal_snapshot['blocks']}
    names=[r[0] for r in c.execute('SELECT DISTINCT sender FROM wechat_message WHERE sender IS NOT NULL')]
    for eid,proof in selected:
        # Read-only detail construction; explicit fields below exclude full originals and private context.
        original=PersonalStore._detail(c,eid)
        event={k:original[k] for k in ('id','raw_item_id','raw_snapshot_id','title','summary','category','status','version','created_at','updated_at')}
        for key in ('title','summary','category'):event[key]=redact_text(event[key],known_names=names)
        tasks=[{k:t[k] for k in ('id','event_id','title','order','deadline_id','origin','status','created_at','updated_at')} for t in original['tasks']]
        deadlines=[{k:d[k] for k in ('id','event_id','label','value','precision','original_text','confirmed_by_user','verified_automatically','created_at','updated_at','end_value','range_precision','timezone')} for d in original['deadlines']]
        for t in tasks:t['title']=redact_text(t['title'],known_names=names)
        for d in deadlines:
            for key in ('label','original_text'):d[key]=redact_text(d[key],known_names=names)
        quotes=[]
        for q in original['evidence']:
            text=blocks.get(q['block_id'],''); quote=redact_text(q['quote'],known_names=names); start=text.find(quote)
            if start<0 or text.find(quote,start+1)>=0: raise ValueError('automatic_excerpt_ambiguous')
            quotes.append({k:q[k] for k in ('id','event_id','raw_snapshot_id','field_path','block_id','quote')}
                          | {'quote':quote,'start':start,'end':start+len(quote)})
        event.update(tasks=tasks,deadlines=deadlines,evidence=quotes,raw_snapshot=minimal_snapshot,
            source={'id':bykind['raw_item']['source_id'],'kind':'wechat','name':'微信通知'},
            course=None,course_id=None,evidence_snapshots={},source_update=None,
            context={'relevance':'yes','participation':'yes','notice_kind':'new','uncertainties':[],
                     'tasks_activated':True,'planned_actions':[], 'audience':None,'eligibility':[],'materials':[],'locations':[]},
            automation=proof)
        from ..personal_priority import personal_priority
        # Relative display changes over time; an outbound receipt must not.
        from datetime import datetime
        event['priority']=personal_priority(event,at=datetime.fromisoformat(event['created_at']))
        records.append(record('event',eid,event))
        records.extend(record('task',t['id'],t) for t in tasks)
        records.extend(record('deadline',d['id'],d) for d in deadlines)
    # Changes above happened after record construction: rebind every hash.
    bundle.update(policy=POLICY,records=[record(r['kind'],r['id'],r['payload']) for r in records],
                  local_guards=[{'raw_id':batch['raw_item_id'],'snapshot_id':batch['snapshot_id'],
                    'raw_guard':hashlib.sha256(encode(bykind['raw_item']).encode()).hexdigest(),
                    'review_guard':hashlib.sha256(encode(review).encode()).hexdigest()}])
    bundle['record_count']=len(records); bundle['bundle_hash']=hashlib.sha256(encode(bundle['records']).encode()).hexdigest()
    validate_actions(bundle)
    attempt=c.execute("SELECT id,raw_output FROM wechat_llm_attempt WHERE job_id=? AND status='success' AND kind='text'",(job_id,)).fetchall()
    if len(attempt)!=1: raise ValueError('automatic_receipt_ambiguous')
    return {'preview_only':True,'upload_authorized':False,'job_id':job_id,'bundle':bundle,
        'analysis_receipt_sha256':hashlib.sha256(encode([job_id,attempt[0]['id'],attempt[0]['raw_output']]).encode()).hexdigest(),
        'cloud_outbound_validation':'passed'}
