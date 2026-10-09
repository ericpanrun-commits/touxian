"""Atomic reconciliation and claiming; facts are read, never changed here."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from ..personal_store import PersonalError, identifier
from ..planning.time import aware, reminder_civil, stamp, system_zone, utc_now
from .policy import fact_key, schedules

LOG = logging.getLogger(__name__)
ACTIVE = {'scheduled','snoozed','failed'}
RESULTS = {'os_accepted','os_blocked','unsupported','backend_unavailable','delivery_failed','delivery_unknown'}


def settings_row(row):
    value = dict(row)
    value['enabled'] = bool(value['enabled'])
    value['date_offsets'] = json.loads(value.pop('date_offsets_json'))
    value['minute_offsets'] = json.loads(value.pop('minute_offsets_json'))
    value.pop('id')
    return value


class ReminderStore:
    def __init__(self,store):
        self.store = store

    def settings(self):
        with self.store.connection() as db:
            return settings_row(db.execute('SELECT * FROM reminder_settings WHERE id=1').fetchone())

    def save_settings(self,payload,*,at=None):
        instant = stamp(at or utc_now())
        with self.store.connection(write=True) as db:
            version = db.execute('SELECT version FROM reminder_settings WHERE id=1').fetchone()[0]
            if version != payload.expected_version:
                raise PersonalError('提醒设置已在其他页面更新，请刷新后核对。','conflict',409)
            db.execute("""UPDATE reminder_settings SET version=version+1,enabled=?,date_offsets_json=?,minute_offsets_json=?,
                daily_time=?,privacy=?,grace_hours=?,updated_at=? WHERE id=1""",
                (int(payload.enabled),json.dumps(payload.date_offsets),json.dumps(payload.minute_offsets),payload.daily_time,payload.privacy,payload.grace_hours,instant))
        self.reconcile(at=at)
        return self.settings()

    def recover_interrupted(self,*,at=None):
        # A process may have died after Show but before the commit. Never blindly
        # resend ambiguous dispatches. They remain visible for explicit snooze.
        instant = stamp(at or utc_now())
        with self.store.connection(write=True) as db:
            for row in db.execute("SELECT * FROM reminder WHERE status='dispatching'").fetchall():
                db.execute("UPDATE reminder SET status='failed',attempts=3,next_attempt_at=NULL,result='delivery_unknown',updated_at=? WHERE id=?",(instant,row['id']))
                self._attempt(db,row,instant,'delivery_unknown')

    @staticmethod
    def _eligible(deadline,tasks):
        if deadline['event_status'] in {'done','cancelled'}:
            return False
        event_tasks = tasks[deadline['event_id']]
        if event_tasks and all(task['status']=='done' for task in event_tasks):
            return False
        related = [task for task in event_tasks if task['deadline_id'] in {None,deadline['id']}]
        return not related or any(task['status'] != 'done' for task in related)

    def reconcile(self,*,at=None):
        at = aware(at or utc_now())
        instant = stamp(at)
        with self.store.connection(write=True) as db:
            settings = settings_row(db.execute('SELECT * FROM reminder_settings WHERE id=1').fetchone())
            tasks = defaultdict(list)
            for task in db.execute('SELECT event_id,deadline_id,status FROM task'):
                tasks[task['event_id']].append(dict(task))
            desired = {}
            for row in db.execute('SELECT d.*,e.status AS event_status FROM deadline d JOIN event e ON e.id=d.event_id'):
                deadline = dict(row)
                if not self._eligible(deadline,tasks):
                    continue
                for schedule in schedules(deadline,settings):
                    desired[schedule['dedupe_key']] = {**schedule,'event_id':deadline['event_id'],'deadline_id':deadline['id']}
            existing = {row['dedupe_key']:dict(row) for row in db.execute('SELECT * FROM reminder')}
            for key,row in existing.items():
                if key not in desired and row['status'] in ACTIVE:
                    db.execute("UPDATE reminder SET status='cancelled',result='projection_changed',updated_at=?,next_attempt_at=NULL WHERE id=?",(instant,row['id']))
            grace_start = stamp(at-timedelta(hours=settings['grace_hours']))
            for key,proposal in desired.items():
                old = existing.get(key)
                due = proposal['scheduled_at']
                if old:
                    # Snooze is a user choice, not overwritten by policy ticks.
                    if old['status']=='scheduled':
                        db.execute('UPDATE reminder SET scheduled_at=?,policy_version=?,updated_at=? WHERE id=? AND scheduled_at<>?',(due,settings['version'],instant,old['id'],due))
                    elif old['status']=='cancelled' and old['result']=='projection_changed' and not old['fired_at'] and due > instant:
                        db.execute("UPDATE reminder SET status='scheduled',scheduled_at=?,attempts=0,next_attempt_at=NULL,result=NULL,updated_at=?,policy_version=? WHERE id=?",(due,instant,settings['version'],old['id']))
                    continue
                if due < grace_start:
                    continue
                db.execute("""INSERT INTO reminder(id,event_id,deadline_id,reminder_type,scheduled_at,status,created_at,updated_at,dedupe_key,policy_version,fact_key)
                    VALUES(?,?,?,?,?,'scheduled',?,?,?,?,?)""",
                    (identifier(),proposal['event_id'],proposal['deadline_id'],proposal['reminder_type'],due,instant,instant,key,settings['version'],proposal['fact_key']))

    def claim(self,*,at=None,limit=3):
        at = aware(at or utc_now())
        instant = stamp(at)
        with self.store.connection(write=True) as db:
            settings = settings_row(db.execute('SELECT * FROM reminder_settings WHERE id=1').fetchone())
            if not settings['enabled']:
                return []
            cutoff = stamp(at-timedelta(hours=settings['grace_hours']))
            rows = db.execute("""SELECT * FROM reminder WHERE status IN ('scheduled','snoozed','failed') AND attempts<3
                AND COALESCE(snoozed_until,scheduled_at)<=? AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                ORDER BY COALESCE(snoozed_until,scheduled_at) DESC,id""",(instant,instant)).fetchall()
            claimed,seen = [],set()
            for raw in rows:
                row = dict(raw)
                due = row['snoozed_until'] or row['scheduled_at']
                if due < cutoff:
                    db.execute("UPDATE reminder SET status='cancelled',result='outside_grace',updated_at=? WHERE id=?",(instant,row['id']))
                    continue
                if row['deadline_id'] in seen:
                    db.execute("UPDATE reminder SET status='cancelled',result='coalesced_on_wake',updated_at=? WHERE id=?",(instant,row['id']))
                    continue
                if len(claimed) >= limit:
                    continue
                deadline = db.execute('SELECT d.*,e.status AS event_status FROM deadline d JOIN event e ON e.id=d.event_id WHERE d.id=?',(row['deadline_id'],)).fetchone()
                tasks = defaultdict(list)
                for task in db.execute('SELECT event_id,deadline_id,status FROM task WHERE event_id=?',(row['event_id'],)):
                    tasks[task['event_id']].append(dict(task))
                valid = deadline and self._eligible(dict(deadline),tasks) and any(item['dedupe_key']==row['dedupe_key'] for item in schedules(dict(deadline),settings))
                if not valid:
                    db.execute("UPDATE reminder SET status='cancelled',result='projection_changed',updated_at=? WHERE id=?",(instant,row['id']))
                    continue
                db.execute("UPDATE reminder SET status='dispatching',claim_at=?,attempts=attempts+1,updated_at=? WHERE id=?",(instant,instant,row['id']))
                row.update(attempts=row['attempts']+1,status='dispatching',claim_at=instant)
                claimed.append(row)
                seen.add(row['deadline_id'])
            return claimed

    def finish(self,row,result,*,at=None):
        result = result if result in RESULTS else 'delivery_failed'
        at = aware(at or utc_now())
        instant = stamp(at)
        success = result == 'os_accepted'
        next_attempt = None if success or row['attempts']>=3 else stamp(at+timedelta(seconds=120 if row['attempts']==1 else 600))
        with self.store.connection(write=True) as db:
            changed = db.execute("""UPDATE reminder SET status=?,result=?,fired_at=CASE WHEN ? THEN ? ELSE fired_at END,
                next_attempt_at=?,updated_at=? WHERE id=? AND (status='dispatching' OR (status='fired' AND ?='delivery_failed')) AND generation=? AND claim_at=?""",
                ('fired' if success else 'failed',result,success,instant,next_attempt,instant,row['id'],result,row['generation'],row['claim_at'])).rowcount
            if changed:
                self._attempt(db,row,instant,result)
        LOG.info('reminder=%s scheduled_at=%s attempt=%s result=%s',row['id'],row['scheduled_at'],row['attempts'],result)

    @staticmethod
    def _attempt(db,row,instant,result):
        db.execute('INSERT INTO reminder_attempt VALUES(?,?,?,?,?,?,?)',(identifier(),row['id'],row['scheduled_at'],instant,row['attempts'],row['generation'],result))

    def act(self,reminder_id,action,*,at=None,expected_generation=None):
        at = aware(at or utc_now())
        instant = stamp(at)
        with self.store.connection(write=True) as db:
            raw = db.execute('SELECT * FROM reminder WHERE id=?',(reminder_id,)).fetchone()
            if not raw:
                raise PersonalError('提醒不存在。','not_found',404)
            row = dict(raw)
            if expected_generation is not None and expected_generation != row['generation']:
                raise PersonalError('提醒已更新，请刷新后重试。','conflict',409)
            if row['status'] in {'cancelled','dismissed','dispatching'}:
                raise PersonalError('该提醒已关闭、取消或正在发送，请刷新后核对。','reminder_inactive',409)
            if action=='dismiss':
                db.execute("UPDATE reminder SET status='dismissed',dismissed_at=?,updated_at=?,next_attempt_at=NULL WHERE id=?",(instant,instant,reminder_id))
            else:
                if action not in {'10m','1h','tomorrow'}:
                    raise PersonalError('请选择支持的稍后提醒间隔。')
                if action=='tomorrow':
                    tz = system_zone()
                    due = reminder_civil(at.astimezone(tz).date()+timedelta(days=1),self._clock(db),tz)
                else:
                    due = at+timedelta(minutes=10 if action=='10m' else 60)
                db.execute("""UPDATE reminder SET status='snoozed',snoozed_until=?,updated_at=?,generation=generation+1,
                    attempts=0,next_attempt_at=NULL,result=NULL,claim_at=NULL WHERE id=?""",(stamp(due),instant,reminder_id))
        return self.get(reminder_id)

    @staticmethod
    def _clock(db):
        return db.execute('SELECT daily_time FROM reminder_settings WHERE id=1').fetchone()[0]

    def get(self,reminder_id):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM reminder WHERE id=?',(reminder_id,)).fetchone()
            if not row:
                raise PersonalError('提醒不存在。','not_found',404)
            return dict(row)

    def center(self,*,limit=100,offset=0):
        from ..planning.query import PlanningQuery
        projections = {item['id']:item for item in PlanningQuery(self.store).deadlines(include_terminal=True)}
        with self.store.connection() as db:
            total = db.execute('SELECT COUNT(*) FROM reminder').fetchone()[0]
            rows = db.execute("""SELECT r.*,e.title AS event_title,e.status AS event_status,d.label AS deadline_label,d.value AS deadline_value,
                d.precision AS deadline_precision,d.timezone AS deadline_timezone,
                (SELECT COUNT(*) FROM task t WHERE t.event_id=e.id AND t.status<>'done') AS remaining_task_count
                FROM reminder r JOIN event e ON e.id=r.event_id LEFT JOIN deadline d ON d.id=r.deadline_id
                ORDER BY CASE r.status WHEN 'failed' THEN 0 WHEN 'fired' THEN 1 WHEN 'snoozed' THEN 2 WHEN 'scheduled' THEN 3 ELSE 4 END,
                COALESCE(r.snoozed_until,r.scheduled_at),r.id LIMIT ? OFFSET ?""",(limit,offset))
            items = []
            for row in rows:
                projection = projections.get(row['deadline_id'],{})
                items.append({**dict(row),'overdue':projection.get('overdue',False),'relative_label':projection.get('relative_label','日期待确认')})
            return {'items':items,'total':total,'limit':limit,'offset':offset}

    def notification_data(self,row):
        from ..planning.query import PlanningQuery
        # Reads current titles/tasks rather than embedding private payloads in jobs.
        values = PlanningQuery(self.store).deadlines(event_id=row['event_id'],deadline_id=row['deadline_id'])
        return values[0] if values and fact_key(values[0])==row['fact_key'] and values[0]['state']!='needs_confirmation' else None
