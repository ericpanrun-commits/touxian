"""PostgreSQL outbox adapter for the shared NotificationDispatcher and policy."""
import hashlib
import logging
from datetime import timedelta

from psycopg.types.json import Jsonb

from ..notifications.policy import NotificationPolicyConfig, evaluate, quiet_due
from .store import PostgresOfficialStore

LOG=logging.getLogger(__name__)


class PostgresNotificationStore:
    def __init__(self, database, owner, *, item_ids=None):
        self.database,self.owner=database,owner
        self.item_ids=list(item_ids) if item_ids is not None else None
        self.counters={'candidates':0,'sent':0,'suppressed':0,'would_send':0}

    @staticmethod
    def _preferences(db,lock=False):
        row=db.execute('SELECT * FROM zqu_worker.meta WHERE id=1'+(' FOR UPDATE' if lock else '')).fetchone()
        return {**row,'policy':NotificationPolicyConfig.model_validate(row['policy']).model_dump()}

    def preferences(self):
        with self.database.connection() as db:return self._preferences(db)

    def activate(self, config, *, at=None):
        # Import is the only baseline writer. Runtime startup must never reset it.
        with self.database.connection() as db:
            row=self._preferences(db)
            if not row['activation_at'] or not row['import_completed_at']:
                raise ValueError('历史通知基线尚未验证。')

    def reconcile(self, config, *, at, limit=100, dry_run=False):
        with self.database.connection() as db:
            prefs=self._preferences(db)
            policy=NotificationPolicyConfig.model_validate(prefs['policy'])
            rows=db.execute('''SELECT i.id FROM zqu_worker.items i WHERE i.content_hash<>'' AND NOT EXISTS
                (SELECT 1 FROM zqu_worker.notification_events n WHERE n.channel='bark'
                AND (n.item_id=i.id OR n.item_group=i.display_group))
                AND (%s::text[] IS NULL OR i.id=ANY(%s))
                AND (%s::text[] IS NOT NULL OR COALESCE(i.body->>'acceptance_test','false')<>'true')
                ORDER BY i.id LIMIT %s''',(self.item_ids,self.item_ids,self.item_ids,limit)).fetchall()
            for row in rows:
                item,sources=PostgresOfficialStore.candidate(db,row['id'])
                reason,due=evaluate(item,sources,policy,activation_at=prefs['activation_at'],at=at)
                if not dry_run and not (config.enabled and config.configured and prefs['enabled']):reason,due='disabled',None
                dedup='bark:new_item:'+item['display_group']
                event_id=hashlib.sha256(dedup.encode()).hexdigest()
                changed=db.execute('''INSERT INTO zqu_worker.notification_events(id,item_id,item_group,dedup_key,status,
                    scheduled_at,suppressed_reason,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                    (event_id,row['id'],item['display_group'],dedup,'pending' if due else 'suppressed',due,None if due else reason,
                    Jsonb({'title':item['title'],'category':item['category'],'policy_reason':reason,
                           'acceptance_test':bool(item.get('acceptance_test'))}))).rowcount
                if changed:
                    self.counters['candidates']+=1
                    self.counters['suppressed']+=int(not due)
                    self.counters['would_send']+=int(bool(due) and dry_run)
                    LOG.info('notification_%s item_id=%s reason=%s','would_send' if due and dry_run else 'queued' if due else 'suppressed',row['id'],reason)

    def recover(self, *, at):
        with self.database.connection() as db:
            db.execute('''UPDATE zqu_worker.notification_events SET status='pending',scheduled_at=%s,
                attempt_count=GREATEST(0,attempt_count-1) WHERE status='sending' AND attempted_at<%s AND request_started_at IS NULL
                AND (%s::text[] IS NULL OR item_id=ANY(%s))
                AND (%s::text[] IS NOT NULL OR COALESCE(metadata->>'acceptance_test','false')<>'true')''',
                (at,at-timedelta(minutes=2),self.item_ids,self.item_ids,self.item_ids))
            changed=db.execute('''UPDATE zqu_worker.notification_events SET status='failed',last_error='delivery_unknown',
                failed_at=%s,scheduled_at=NULL WHERE status='sending' AND attempted_at<%s AND request_started_at IS NOT NULL
                AND (%s::text[] IS NULL OR item_id=ANY(%s))
                AND (%s::text[] IS NOT NULL OR COALESCE(metadata->>'acceptance_test','false')<>'true')''',
                (at,at-timedelta(minutes=2),self.item_ids,self.item_ids,self.item_ids)).rowcount
            if changed:db.execute("UPDATE zqu_worker.meta SET last_failure_at=%s,last_error='delivery_unknown' WHERE id=1",(at,))

    def claim(self, config, *, at):
        with self.database.connection() as db:
            # Serialize global hourly limits as well as each event's claim.
            prefs=self._preferences(db,lock=True)
            if not (config.enabled and config.configured and prefs['enabled']):return None
            if not db.execute("SELECT 1 FROM zqu_worker.leases WHERE name='actions' AND owner=%s AND expires_at>clock_timestamp()",(self.owner,)).fetchone():return None
            policy=NotificationPolicyConfig.model_validate(prefs['policy'])
            rows=db.execute('''SELECT * FROM zqu_worker.notification_events WHERE channel='bark' AND status IN ('pending','failed')
                AND scheduled_at<=%s AND attempt_count<3
                AND (%s::text[] IS NULL OR item_id=ANY(%s))
                AND (%s::text[] IS NOT NULL OR COALESCE(metadata->>'acceptance_test','false')<>'true')
                ORDER BY scheduled_at,id LIMIT 30 FOR UPDATE SKIP LOCKED''',(at,self.item_ids,self.item_ids,self.item_ids)).fetchall()
            for row in rows:
                item,sources=PostgresOfficialStore.candidate(db,row['item_id'])
                reason,due=evaluate(item,sources,policy,activation_at=prefs['activation_at'],at=at) if item else ('missing_item',None)
                if not due:
                    db.execute("UPDATE zqu_worker.notification_events SET status='suppressed',suppressed_reason=%s,scheduled_at=NULL WHERE id=%s",(reason,row['id']))
                    self.counters['suppressed']+=1
                    continue
                due=quiet_due(at,policy) if item['importance']!='critical' else at
                if due>at:
                    db.execute('UPDATE zqu_worker.notification_events SET scheduled_at=%s WHERE id=%s',(due,row['id']));continue
                if item['importance']!='critical':
                    used=db.execute('''SELECT count(*) AS n FROM zqu_worker.notification_events WHERE channel='bark'
                        AND (request_started_at>%s OR (status='sending' AND attempted_at>%s))''',(at-timedelta(hours=1),at-timedelta(hours=1))).fetchone()['n']
                    if used>=policy.max_per_hour:
                        db.execute('UPDATE zqu_worker.notification_events SET scheduled_at=%s WHERE id=%s',(at+timedelta(hours=1),row['id']));continue
                claimed=db.execute('''UPDATE zqu_worker.notification_events SET status='sending',attempt_count=attempt_count+1,
                    attempted_at=%s,request_started_at=NULL,scheduled_at=NULL WHERE id=%s AND status IN ('pending','failed') RETURNING *''',(at,row['id'])).fetchone()
                return {**claimed,'raw_item_id':row['item_id'],'item':item,'sources':sources,'policy':policy}
        return None

    def start_request(self, row, *, at):
        with self.database.connection() as db:
            return db.execute('''UPDATE zqu_worker.notification_events SET request_started_at=%s WHERE id=%s AND status='sending'
                AND attempted_at=%s AND attempt_count=%s AND (SELECT enabled FROM zqu_worker.meta WHERE id=1)
                AND EXISTS(SELECT 1 FROM zqu_worker.leases WHERE name='actions' AND owner=%s AND expires_at>clock_timestamp())''',
                (at,row['id'],row['attempted_at'],row['attempt_count'],self.owner)).rowcount==1

    def finish(self, row, result, *, at):
        retry=result.retryable and row['attempt_count']<3
        due=at+timedelta(seconds=30*2**(row['attempt_count']-1)) if retry else None
        with self.database.connection() as db:
            changed=db.execute('''UPDATE zqu_worker.notification_events SET status=%s,last_error=%s,scheduled_at=%s,
                sent_at=%s,failed_at=%s,provider_message_id=%s WHERE id=%s AND status='sending' AND attempted_at=%s AND attempt_count=%s''',
                ('sent' if result.accepted else 'failed',result.error,due,at if result.accepted else None,
                None if result.accepted else at,result.provider_message_id,row['id'],row['attempted_at'],row['attempt_count'])).rowcount
            if changed:
                if result.accepted:db.execute('UPDATE zqu_worker.meta SET last_success_at=%s,last_error=NULL WHERE id=1',(at,))
                else:db.execute('UPDATE zqu_worker.meta SET last_failure_at=%s,last_error=%s WHERE id=1',(at,result.error))
                self.counters['sent']+=int(result.accepted)
        LOG.info('notification_%s item_id=%s attempt=%s','sent' if result.accepted else 'failed',row['raw_item_id'],row['attempt_count'])
