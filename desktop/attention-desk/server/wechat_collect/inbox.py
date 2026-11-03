"""Read models and reversible decisions over the existing candidate/review records."""
import json
from ..personal_store import PersonalError, now


def list_candidates(personal, *, limit=20, offset=0, status='needs_review'):
    reviews="('pending')" if status=='needs_review' else "('pending','cancelled','confirmed','archived')"
    where="ca.status=? AND r.status IN "+reviews+" AND ba.superseded_by IS NULL"
    if status=='needs_review': where+=" AND ca.attention_level='action' AND ca.information_complete=1 AND ca.await_more_context=0"
    query=" FROM wechat_candidate ca JOIN wechat_batch ba ON ba.id=ca.batch_id JOIN wechat_chat ch ON ch.id=ba.chat_id JOIN raw_review r ON r.raw_snapshot_id=ba.snapshot_id WHERE "+where
    with personal.connection() as c:
        total=c.execute('SELECT COUNT(*)'+query,(status,)).fetchone()[0]
        rows=[dict(row) for row in c.execute('SELECT ca.*,ba.snapshot_id,ch.name AS chat_name,ch.enabled AS chat_enabled,r.version AS review_version'+query+' ORDER BY ca.created_at DESC,ca.id DESC LIMIT ? OFFSET ?',(status,limit,offset))]
        for row in rows:
            row['analysis']=json.loads(row.pop('data_json'))
            draft=c.execute('SELECT draft_json FROM raw_review WHERE raw_snapshot_id=?',(row['snapshot_id'],)).fetchone()
            saved=json.loads(draft['draft_json'] or '{}')
            focused=next((item for item in saved.get('events',[]) if item.get('id')==row['model_item_id']),None)
            if not focused and row['model_item_id']=='_pending':
                focused=next(iter(saved.get('events',[])),None)
            row['draft_preview']={key:focused.get(key) for key in ('title','summary','category','deadlines','actions','locations')} if focused else None
            source=[dict(message) for message in c.execute('SELECT m.id,m.sender,m.source_time,m.message_type,bm.role FROM wechat_batch_message bm JOIN wechat_message m ON m.id=bm.message_id WHERE bm.batch_id=? ORDER BY m.ingest_seq',(row['batch_id'],))]
            cited=set(row['analysis'].get('evidence_message_ids') or ())
            row['messages']=[message for message in source if message['id'] in cited or not cited and message['role']=='primary']
            row['source_type']='wechat'
            job=c.execute('SELECT status,error_code FROM wechat_analysis_job WHERE batch_id=? ORDER BY created_at DESC LIMIT 1',(row['batch_id'],)).fetchone()
            row['analysis_state']=dict(job) if job else {'status':'pending','error_code':None}
            row['attachment_count']=c.execute('SELECT count(DISTINCT b.attachment_id) FROM wechat_snapshot_message sm JOIN raw_source_block b ON b.raw_snapshot_id=sm.snapshot_id AND b.block_id=sm.block_id WHERE sm.snapshot_id=? AND b.attachment_id IS NOT NULL AND sm.message_id IN ('+','.join('?' for _ in row['messages'])+')',(row['snapshot_id'],*[m['id'] for m in row['messages']])).fetchone()[0] if row['messages'] else 0
            logical_items=c.execute("SELECT count(*) FROM wechat_candidate WHERE batch_id=? AND model_item_id!='_pending' AND status IN ('needs_review','accepted','fyi')",(row['batch_id'],)).fetchone()[0]
            if logical_items==1:row['attachment_count']=c.execute('SELECT count(*) FROM snapshot_attachment WHERE raw_snapshot_id=?',(row['snapshot_id'],)).fetchone()[0]
        return {'items':rows,'total':total,'limit':limit,'offset':offset}


def set_ignored(personal, candidate_id, ignored, *, expected_version=None):
    with personal.connection(write=True) as c:
        row=c.execute('SELECT ca.*,b.snapshot_id,r.status AS review_status,r.version AS review_version FROM wechat_candidate ca JOIN wechat_batch b ON b.id=ca.batch_id JOIN raw_review r ON r.raw_snapshot_id=b.snapshot_id WHERE ca.id=?',(candidate_id,)).fetchone()
        if not row:
            raise PersonalError('候选不存在。','not_found',404)
        if row['status']=='accepted':
            raise PersonalError('事项已确认，请在正式事项中管理。','review_closed',409)
        if not ignored and (row['attention_level']!='action' or not row['information_complete'] or row['await_more_context']):
            raise PersonalError('这条信息不属于待确认事项，已保留在信息归档或暂存区。','attention_not_action',409)
        desired='ignored' if ignored else 'needs_review'
        if row['status']==desired:
            return {'saved':True,'status':desired}
        if expected_version is not None and expected_version!=row['review_version']:
            raise PersonalError('候选已更新，请刷新后再操作。','conflict',409)
        if row['status']=='superseded':
            raise PersonalError('已有更新版本，请刷新候选。','superseded',409)
        c.execute('UPDATE wechat_candidate SET status=? WHERE id=?',(desired,candidate_id))
        pending=c.execute("SELECT COUNT(*) FROM wechat_candidate WHERE batch_id=? AND status='needs_review'",(row['batch_id'],)).fetchone()[0]
        c.execute('UPDATE raw_review SET status=?,version=version+1,updated_at=? WHERE raw_snapshot_id=?',('pending' if pending else 'cancelled',now(),row['snapshot_id']))
        c.execute('UPDATE wechat_batch SET status=? WHERE id=?',('needs_review' if pending else 'ignored',row['batch_id']))
        if not pending:
            c.execute("UPDATE wechat_analysis_job SET status='ignored' WHERE batch_id=? AND status IN ('pending','retry','budget_deferred')",(row['batch_id'],))
        return {'saved':True,'status':desired}


def briefing(personal,*,limit=3,at=None):
    """Automatically archived knowledge and grouped, non-actionable holding areas."""
    from .fyi import feed
    fyi=feed(personal,limit=limit,at=at)
    unresolved=list_candidates(personal,limit=20,status='unresolved')['items']
    media=list_candidates(personal,limit=100,status='media_pending')['items']
    with personal.connection() as c:
        resolved={r[0] for r in c.execute("SELECT message_id FROM wechat_attention_media WHERE status='resolved'")}
        queued=c.execute("SELECT q.*,m.sender,m.source_time,m.message_type,ch.name FROM wechat_attention_media q JOIN wechat_message m ON m.id=q.message_id JOIN wechat_chat ch ON ch.id=m.chat_id WHERE q.status='media_pending' ORDER BY q.created_at DESC LIMIT 100").fetchall()
        for row in queued:
            media.append({'id':'media:'+row['message_id'],'batch_id':row['batch_id'],'snapshot_id':row['snapshot_id'],'chat_name':row['name'],
                          'analysis':{'title':'未解析附件','reason':row['reason'],'attention_level':'ignore','information_complete':False,'await_more_context':True},
                          'attention_reason':row['reason'],'messages':[{'id':row['message_id'],'sender':row['sender'],'source_time':row['source_time'],'message_type':row['message_type']}]})
    groups={}
    for row in media:
        row['messages']=[m for m in row['messages'] if m['id'] not in resolved and m['message_type'] in {'image','file'}]
        if not row['messages']: continue
        group=groups.setdefault(row['batch_id'],{**row,'id':'media:'+row['batch_id'],'messages':[]})
        known={m['id'] for m in group['messages']}
        group['messages'].extend(m for m in row['messages'] if m['id'] not in known and m['message_type'] in {'image','file'})
    for row in groups.values():
        row['attachment_count']=len(row['messages'])
        row['analysis']={**row['analysis'],'title':'未解析附件','summary':f"{len(row['messages'])} 份附件尚未获得可靠正文，已保留原件。",'evidence_message_ids':[m['id'] for m in row['messages']]}
        with personal.connection() as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='wechat_media_work'").fetchone():
                work=c.execute('SELECT status,last_error,next_attempt_at FROM wechat_media_work WHERE message_id IN ('+','.join('?' for _ in row['messages'])+') ORDER BY last_attempt_at DESC LIMIT 1',tuple(m['id'] for m in row['messages'])).fetchone()
                if work:
                    from .media_pipeline import reason_text
                    row['attention_reason']='正在自动识别图片并结合微信上下文。' if work['status']=='analyzing' else reason_text(work['last_error'])
                    row['processing_state']=work['status'];row['next_attempt_at']=work['next_attempt_at']
    known={m['id'] for row in unresolved for m in row['messages']}
    with personal.connection() as c:
        from datetime import timezone
        stamp=at.astimezone(timezone.utc).isoformat() if at else now()
        held=c.execute("SELECT m.*,ch.name FROM wechat_context_buffer u JOIN wechat_message m ON m.id=u.message_id JOIN wechat_chat ch ON ch.id=m.chat_id WHERE u.status='waiting' AND u.expires_at>? ORDER BY m.captured_at DESC LIMIT 20",(stamp,)).fetchall()
        for row in held:
            if row['id'] in known: continue
            unresolved.append({'id':'context:'+row['id'],'snapshot_id':None,'chat_name':row['name'],'attention_reason':'先保留上下文，等待明确确认。',
                               'analysis':{'title':'未确认的信息','summary':row['raw_text'],'reason':'疑问或片段尚不足以形成行动事项。','attention_level':'ignore','information_complete':False,'await_more_context':True},
                               'messages':[{'id':row['id'],'sender':row['sender'],'source_time':row['source_time']}],'raw_text':row['raw_text']})
    return {'fyi':fyi,'unresolved':{'items':unresolved,'total':len(unresolved)},'media':{'items':list(groups.values()),'total':sum(r['attachment_count'] for r in groups.values())}}
