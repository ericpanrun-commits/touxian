"""Transactional capture state. No method automatically creates formal records."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from ..personal_store import PersonalError, encode, identifier, now
from .identity import reconcile, signature
from .models import Reconciliation
from .scheduler import SHANGHAI

COUNTERS = ('scans_total','scans_no_change','messages_seen','messages_new','messages_duplicate',
            'messages_filtered','messages_sent_to_llm','llm_calls','important_items_found','errors',
            'scans_deferred','scans_partial','identity_ambiguous','coverage_gaps','vision_calls',
            'ocr_calls','llm_context_messages','llm_retries','llm_outcome_unknown','budget_deferred')


class WeChatStore:
    def __init__(self, personal):
        self.personal = personal

    def configure(self, document, *, enforce_allowlist=False):
        with self.personal.connection(write=True) as c:
            c.execute('UPDATE wechat_chat SET enabled=0')
            for chat in document.wechat.priority_chats:
                enabled = chat.enabled and (not enforce_allowlist or document.wechat.allows(chat))
                stamp = now()
                source_id = 'wechat:' + chat.id
                c.execute('INSERT OR IGNORE INTO source VALUES (?,?,?,?)',(source_id,'wechat',chat.name,stamp))
                existing = c.execute('SELECT * FROM wechat_chat WHERE id=?',(chat.id,)).fetchone()
                if existing:
                    # Rename is not proof that a different chat is the same one.
                    changed = existing['name'] != chat.name
                    c.execute('UPDATE wechat_chat SET name=?,config_json=?,enabled=?,updated_at=?,binding_json=CASE WHEN ? THEN NULL ELSE binding_json END,capability=CASE WHEN ? THEN ? ELSE capability END WHERE id=?',
                              (chat.name,encode(chat.model_dump()),int(enabled),stamp,changed,changed,'not_tested',chat.id))
                else:
                    c.execute('INSERT INTO wechat_chat(id,account_scope_id,source_id,name,config_json,enabled,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                              (chat.id,'unverified',source_id,chat.name,encode(chat.model_dump()),int(enabled),stamp,stamp))
                    c.execute('INSERT INTO wechat_cursor(chat_id) VALUES (?)',(chat.id,))

    def chats(self):
        with self.personal.connection() as c:
            return [dict(r) for r in c.execute('SELECT ch.*,cu.last_success_at,cu.last_observed_at,cu.gap_json FROM wechat_chat ch JOIN wechat_cursor cu ON cu.chat_id=ch.id ORDER BY ch.id')]

    def record_group_scan(self, chat_id, scan_id, status, counts=None, *, error=None, metrics=None):
        counts,metrics=counts or {},metrics or {}
        with self.personal.connection(write=True) as c:
            c.execute('INSERT INTO wechat_group_state(chat_id,last_scan_id,last_scan_at,last_status,last_error,messages_new,messages_filtered,metadata_queries,message_body_queries,unauthorized_message_bodies_read,messages_sent) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET last_scan_id=excluded.last_scan_id,last_scan_at=excluded.last_scan_at,last_status=excluded.last_status,last_error=excluded.last_error,messages_new=excluded.messages_new,messages_filtered=excluded.messages_filtered,metadata_queries=excluded.metadata_queries,message_body_queries=excluded.message_body_queries,unauthorized_message_bodies_read=excluded.unauthorized_message_bodies_read,messages_sent=excluded.messages_sent',
                      (chat_id,scan_id,now(),status,error,counts.get('messages_new',0),counts.get('messages_filtered',0),metrics.get('metadata_queries',0),metrics.get('message_body_queries',0),metrics.get('unauthorized_message_bodies_read',0),metrics.get('messages_sent',0)))

    def group_cards(self, document):
        result=[]
        with self.personal.connection() as c:
            for target in document.wechat.priority_chats:
                if not document.wechat.allows(target):
                    continue
                chat=c.execute('SELECT ch.*,cu.last_success_at,cu.last_observed_at FROM wechat_chat ch JOIN wechat_cursor cu ON cu.chat_id=ch.id WHERE ch.id=?',(target.id,)).fetchone()
                if not chat:
                    continue
                state=c.execute('SELECT * FROM wechat_group_state WHERE chat_id=?',(target.id,)).fetchone()
                state=dict(state) if state else {'last_status':'not_scanned','last_scan_id':None,'messages_new':0,'messages_filtered':0,'last_scan_at':None,'last_error':None}
                binding=json.loads(chat['binding_json'] or '{}')
                cursor=c.execute('SELECT internal_chat_id,last_local_id,last_sort_seq,last_successful_scan,shard_watermarks_json,bootstrap_since FROM wechat_db_cursor WHERE chat_id=?',(target.id,)).fetchone()
                candidates=c.execute("SELECT COUNT(*) AS total,COALESCE(SUM(ca.important),0) AS important FROM wechat_candidate ca JOIN wechat_batch b ON b.id=ca.batch_id WHERE b.chat_id=? AND b.scan_id=? AND b.superseded_by IS NULL AND ca.attention_level='action' AND ca.information_complete=1 AND ca.await_more_context=0 AND ca.model_item_id!='_pending' AND ca.status!='superseded'",(target.id,state['last_scan_id'])).fetchone()
                pending=c.execute("SELECT COUNT(*) FROM wechat_candidate ca JOIN wechat_batch b ON b.id=ca.batch_id WHERE b.chat_id=? AND ca.status='needs_review' AND b.superseded_by IS NULL",(target.id,)).fetchone()[0]
                llm=c.execute('SELECT COUNT(*) FROM wechat_llm_attempt a JOIN wechat_analysis_job j ON j.id=a.job_id JOIN wechat_batch b ON b.id=j.batch_id WHERE b.chat_id=? AND b.scan_id=?',(target.id,state['last_scan_id'])).fetchone()[0]
                analysis=c.execute('SELECT j.status,j.error_code FROM wechat_analysis_job j JOIN wechat_batch b ON b.id=j.batch_id WHERE b.chat_id=? ORDER BY j.created_at DESC LIMIT 1',(target.id,)).fetchone()
                result.append({'id':target.id,'chatroom_id':target.chatroom_id or binding.get('internal_chat_id'),'name':target.name,'priority':target.priority,'enabled':bool(target.enabled),'bound':bool(binding),'last_success_at':chat['last_success_at'],'last_cursor':dict(cursor) if cursor else None,**state,'candidates_new':candidates['total'],'important_new':candidates['important'],'pending_review':pending,'llm_calls':llm,'analysis':dict(analysis) if analysis else None})
        return result

    def chat(self, chat_id):
        with self.personal.connection() as c:
            row = c.execute('SELECT * FROM wechat_chat WHERE id=?',(chat_id,)).fetchone()
            if not row:
                raise PersonalError('群未配置。','chat_not_configured',404)
            return dict(row)

    def history(self, chat_id, limit=200):
        with self.personal.connection() as c:
            return [dict(r) for r in reversed(c.execute('SELECT * FROM wechat_message WHERE chat_id=? ORDER BY ingest_seq DESC LIMIT ?',(chat_id,limit)).fetchall())]

    def cursor(self, chat_id):
        with self.personal.connection() as c:
            value = dict(c.execute('SELECT * FROM wechat_cursor WHERE chat_id=?',(chat_id,)).fetchone())
            row = c.execute('SELECT * FROM wechat_db_cursor WHERE chat_id=?', (chat_id,)).fetchone()
            value['database'] = dict(row) if row else None
            return value

    def bind_database(self, chat_id, binding):
        with self.personal.connection(write=True) as c:
            current = c.execute('SELECT account_scope_id,binding_json FROM wechat_chat WHERE id=?', (chat_id,)).fetchone()
            if current['binding_json']:
                previous = json.loads(current['binding_json'])
                if previous.get('internal_chat_id') != binding['internal_chat_id'] or previous.get('account_scope') != binding['account_scope']:
                    raise PersonalError('账号或群 ID 已变化，需要重新核对绑定。', 'binding_mismatch', 409)
            c.execute("UPDATE wechat_chat SET account_scope_id=?,binding_json=?,capability='GO',binding_version=binding_version+1,updated_at=? WHERE id=?",
                      (binding['account_scope'], encode(binding), now(), chat_id))

    def begin_scan(self, reason, revision, *, request_key=None, override_expires_at=None):
        with self.personal.connection(write=True) as c:
            if request_key:
                old = c.execute('SELECT * FROM wechat_scan WHERE request_key=?',(request_key,)).fetchone()
                if old:
                    return dict(old), False
            scan_id = identifier()
            c.execute('INSERT INTO wechat_scan(id,request_key,reason,created_at,status,config_revision,override_expires_at) VALUES (?,?,?,?,?,?,?)',
                      (scan_id,request_key,reason,now(),'running',revision,override_expires_at))
            return dict(c.execute('SELECT * FROM wechat_scan WHERE id=?',(scan_id,)).fetchone()), True

    def finish_scan(self, scan_id, status, stats, error=None):
        with self.personal.connection(write=True) as c:
            c.execute('UPDATE wechat_scan SET completed_at=?,status=?,stats_json=?,error_code=? WHERE id=?',(now(),status,encode(stats),error,scan_id))

    def capture(self, scan_id, chat, observation):
        if observation.chat_id != chat['id'] or observation.account_scope != chat['account_scope_id']:
            raise PersonalError('群或微信账号身份不一致，未保存消息。','binding_mismatch',409)
        if observation.database_cursor is not None:
            return self._capture_database(scan_id, chat, observation)
        history = self.history(chat['id'])
        result = reconcile(history, observation)
        stats = {'messages_seen':len(observation.messages),'messages_new':len(result.new_indices),
                 'messages_duplicate':len(result.matched),'coverage_gaps':int(not result.complete)}
        new_messages = []
        with self.personal.connection(write=True) as c:
            chat_scan_id, stamp = identifier(), now()
            c.execute('INSERT INTO wechat_chat_scan(id,scan_id,chat_id,status,reason,pages,anchor_found,covers_tail,created_at,completed_at,stats_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                      (chat_scan_id,scan_id,chat['id'],'complete' if result.complete else 'partial',result.reason,observation.pages,int(result.complete),int(observation.covers_tail),stamp,stamp,encode(stats)))
            seq = c.execute('SELECT COALESCE(MAX(ingest_seq),0) FROM wechat_message WHERE chat_id=?',(chat['id'],)).fetchone()[0]
            matches = dict(result.matched)
            for index in result.new_indices:
                message = observation.messages[index]
                seq += 1
                mid = identifier()
                # This is an occurrence checksum, NOT a cross-scan dedupe key.
                checksum = hashlib.sha256(encode([chat['id'],mid,signature(message)]).encode()).hexdigest()
                c.execute('INSERT INTO wechat_message VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (mid,chat['id'],seq,message.sender,message.sender_reliability,message.text,message.message_type,message.source_time,message.time_reliability,stamp,signature(message),checksum,message.attachment_identity,scan_id,chat['name']))
                c.execute('INSERT INTO wechat_message_state(message_id) VALUES (?)',(mid,))
                matches[index] = mid
                new_messages.append(dict(c.execute('SELECT * FROM wechat_message WHERE id=?',(mid,)).fetchone()))
            for ordinal,message in enumerate(observation.messages):
                c.execute('INSERT INTO wechat_observation VALUES (?,?,?,?,?,?,?,?)',
                          (identifier(),chat_scan_id,ordinal,matches.get(ordinal),signature(message),
                           'new' if ordinal in result.new_indices else 'overlap' if ordinal in result.matched else 'unresolved',
                           result.reason,None if ordinal in matches else encode(asdict(message))))
            anchor_ids = [m['id'] for m in history] + [m['id'] for m in new_messages]
            c.execute('UPDATE wechat_cursor SET last_observed_at=?,gap_json=?,version=version+1 WHERE chat_id=?',
                      (stamp,None if result.complete else encode({'reason':result.reason,'scan_id':scan_id}),chat['id']))
            if result.complete:
                c.execute('UPDATE wechat_cursor SET anchor_ids_json=?,last_success_at=? WHERE chat_id=?',(encode(anchor_ids[-200:]),stamp,chat['id']))
        return result, new_messages, stats

    def _capture_database(self, scan_id, chat, observation):
        result = Reconciliation(complete=observation.covers_tail, reason='database_cursor' if observation.covers_tail else 'database_backlog')
        messages = []
        cursor = observation.database_cursor
        if cursor['account_scope'] != observation.account_scope:
            raise PersonalError('数据库游标账号不一致。', 'binding_mismatch', 409)
        with self.personal.connection(write=True) as c:
            stamp, chat_scan_id = now(), identifier()
            c.execute('INSERT INTO wechat_chat_scan(id,scan_id,chat_id,status,reason,pages,anchor_found,covers_tail,created_at,completed_at,stats_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                      (chat_scan_id,scan_id,chat['id'],'complete' if result.complete else 'partial',result.reason,observation.pages,1,int(result.complete),stamp,stamp,'{}'))
            seq = c.execute('SELECT COALESCE(MAX(ingest_seq),0) FROM wechat_message WHERE chat_id=?', (chat['id'],)).fetchone()[0]
            for ordinal, message in enumerate(observation.messages):
                identity = message.database_identity
                if not identity or identity['internal_chat_id'] != cursor['internal_chat_id']:
                    raise PersonalError('数据库消息缺少稳定来源标识。', 'invalid_database_identity', 409)
                existing = c.execute('SELECT message_id,secondary_fingerprint FROM wechat_db_message WHERE chat_id=? AND account_scope=? AND shard=? AND local_id=?',
                                     (chat['id'],observation.account_scope,identity['shard'],identity['local_id'])).fetchone()
                if existing:
                    if existing['secondary_fingerprint'] != identity['source_content_sha256']:
                        raise PersonalError('同一数据库 ID 的内容变化，已保留原 Evidence 并暂停。', 'database_identity_changed', 409)
                    mid = existing['message_id']
                    result.matched[ordinal] = mid
                else:
                    mid = identifier()
                    seq += 1
                    checksum = hashlib.sha256(encode([chat['id'],observation.account_scope,identity['shard'],identity['local_id']]).encode()).hexdigest()
                    c.execute('INSERT INTO wechat_message VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                              (mid,chat['id'],seq,message.sender,message.sender_reliability,message.text,message.message_type,message.source_time,message.time_reliability,stamp,signature(message),checksum,message.attachment_identity,scan_id,chat['name']))
                    c.execute('INSERT INTO wechat_db_message VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                              (mid,chat['id'],observation.account_scope,identity['internal_chat_id'],identity['shard'],identity['local_id'],identity.get('server_id'),identity['sort_seq'],identity['create_time_unix'],identity['source_content_sha256'],encode(identity.get('media_reference'))))
                    if identity.get('sender_identity'):
                        c.execute('INSERT INTO wechat_sender_identity VALUES (?,?,?)', (mid,encode(identity['sender_identity']),stamp))
                    c.execute('INSERT INTO wechat_message_state(message_id) VALUES (?)', (mid,))
                    messages.append(dict(c.execute('SELECT * FROM wechat_message WHERE id=?', (mid,)).fetchone()))
                    result.new_indices.append(ordinal)
                c.execute('INSERT INTO wechat_observation VALUES (?,?,?,?,?,?,?,?)',
                          (identifier(),chat_scan_id,ordinal,mid,signature(message),'database_id','shard_and_local_id',None))
            c.execute('INSERT INTO wechat_db_cursor VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET account_scope=excluded.account_scope,internal_chat_id=excluded.internal_chat_id,last_local_id=excluded.last_local_id,last_create_time=excluded.last_create_time,last_sort_seq=excluded.last_sort_seq,secondary_fingerprint=excluded.secondary_fingerprint,last_successful_scan=excluded.last_successful_scan,shard_watermarks_json=excluded.shard_watermarks_json,bootstrap_since=excluded.bootstrap_since',
                      (chat['id'],observation.account_scope,cursor['internal_chat_id'],cursor.get('last_local_id'),cursor.get('last_create_time'),cursor.get('last_sort_seq'),cursor.get('secondary_fingerprint'),cursor.get('last_successful_scan'),cursor['shard_watermarks_json'],cursor['bootstrap_since']))
            c.execute('UPDATE wechat_cursor SET last_observed_at=?,last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END,gap_json=?,version=version+1 WHERE chat_id=?',
                      (stamp,int(result.complete),stamp,None if result.complete else encode({'reason':result.reason}),chat['id']))
        return result, messages, {'messages_seen':len(observation.messages),'messages_new':len(messages),'messages_duplicate':len(result.matched),'coverage_gaps':int(not result.complete)}

    def filter_message(self, message_id, decision, rule_version):
        with self.personal.connection(write=True) as c:
            c.execute('UPDATE wechat_message_state SET status=?,filter_level=?,filter_reason=?,rule_version=? WHERE message_id=?',
                      ('filtered' if decision.level=='fast_reject' else 'unresolved' if decision.level=='await_context' else 'candidate',decision.level,decision.reason,rule_version,message_id))

    def scans(self, limit=50, offset=0):
        with self.personal.connection() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM wechat_scan ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',(limit,offset))]
            for row in rows:
                row['stats'] = json.loads(row.pop('stats_json'))
            return {'items':rows,'total':c.execute('SELECT COUNT(*) FROM wechat_scan').fetchone()[0]}

    def recover(self):
        with self.personal.connection(write=True) as c:
            c.execute("UPDATE wechat_scan SET status='interrupted',completed_at=?,error_code='interrupted' WHERE status='running'",(now(),))
            # A process crash after request dispatch has unknown billing outcome; never replay automatically.
            c.execute("UPDATE wechat_llm_attempt SET status='outcome_unknown',outcome_unknown=1,completed_at=? WHERE status='running'",(now(),))
            c.execute("UPDATE wechat_analysis_job SET status='outcome_unknown',error_code='interrupted' WHERE status='running'")

    def stats(self, day):
        local = datetime.fromisoformat(day).replace(tzinfo=SHANGHAI)
        start, end = local.astimezone(timezone.utc).isoformat(), (local+timedelta(days=1)).astimezone(timezone.utc).isoformat()
        result = dict.fromkeys(COUNTERS,0)
        with self.personal.connection() as c:
            scans = c.execute('SELECT * FROM wechat_scan WHERE created_at>=? AND created_at<?',(start,end)).fetchall()
            result['scans_total'] = len(scans)
            for row in scans:
                result['scans_no_change'] += row['status']=='no_change'
                result['scans_deferred'] += row['status']=='deferred'
                result['scans_partial'] += row['status']=='partial'
                result['errors'] += bool(row['error_code'] and row['status']=='error')
                for key,value in json.loads(row['stats_json']).items():
                    if key in result:
                        result[key] += value
            attempts = c.execute('SELECT * FROM wechat_llm_attempt WHERE started_at>=? AND started_at<?',(start,end)).fetchall()
            result['llm_calls'] = len(attempts)
            result['vision_calls'] = sum(a['kind']=='vision' for a in attempts)
            result['llm_outcome_unknown'] = sum(a['outcome_unknown'] for a in attempts)
            result['errors'] += sum(a['status'] not in {'success','running'} for a in attempts)
            result['llm_retries'] = sum(a['attempt_number']>1 for a in attempts)
            result['messages_sent_to_llm'] = c.execute("SELECT COUNT(DISTINCT bm.message_id) FROM wechat_batch_message bm JOIN wechat_analysis_job j ON j.batch_id=bm.batch_id JOIN wechat_llm_attempt a ON a.job_id=j.id WHERE bm.role='primary' AND a.kind='text' AND a.started_at>=? AND a.started_at<?",(start,end)).fetchone()[0]
            result['llm_context_messages'] = c.execute("SELECT COUNT(DISTINCT bm.message_id) FROM wechat_batch_message bm JOIN wechat_analysis_job j ON j.batch_id=bm.batch_id JOIN wechat_llm_attempt a ON a.job_id=j.id WHERE bm.role='context' AND a.kind='text' AND a.started_at>=? AND a.started_at<?",(start,end)).fetchone()[0]
            usages = [json.loads(a['token_usage_json'] or '{}') for a in attempts]
            result['llm_tokens_if_available'] = sum(u['total_tokens'] for u in usages) if all('total_tokens' in u for u in usages) else None
            result['important_items_found'] = c.execute("SELECT COUNT(*) FROM wechat_candidate WHERE attention_level='action' AND important=1 AND created_at>=? AND created_at<?",(start,end)).fetchone()[0]
            result['pending_candidates'] = c.execute("SELECT COUNT(*) FROM wechat_analysis_job WHERE status IN ('pending','budget_deferred','retry')").fetchone()[0]
            result['candidates_today']=c.execute("SELECT COUNT(*) FROM wechat_candidate WHERE attention_level='action' AND status!='superseded' AND created_at>=? AND created_at<?",(start,end)).fetchone()[0]
            result['pending_review']=c.execute("SELECT COUNT(*) FROM wechat_candidate WHERE status='needs_review'").fetchone()[0]
        return result
