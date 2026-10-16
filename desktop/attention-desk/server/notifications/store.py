"""Persistent outbox in the EXISTING official_notification ledger."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta

from ..personal_store import encode, PersonalError
from ..planning.time import aware, stamp, utc_now
from .policy import NotificationPolicyConfig, evaluate, quiet_due

LOG = logging.getLogger(__name__)


class NotificationStore:
    def __init__(self, personal):
        self.personal = personal

    @staticmethod
    def _preferences(c):
        row = dict(c.execute("SELECT * FROM notification_preferences WHERE channel='bark'").fetchone())
        row['enabled'] = bool(row['enabled'])
        row['policy'] = NotificationPolicyConfig.model_validate(json.loads(row.pop('policy_json'))).model_dump()
        return row

    def preferences(self):
        with self.personal.connection() as c:
            return self._preferences(c)

    def save_preferences(self, enabled, policy, expected_version):
        with self.personal.connection(write=True) as c:
            if c.execute("UPDATE notification_preferences SET enabled=?,policy_json=?,version=version+1 WHERE channel='bark' AND version=?",
                         (enabled, encode(policy.model_dump()), expected_version)).rowcount != 1:
                raise PersonalError('手机通知设置已更新，请重新读取后核对。', 'conflict', 409)
        return self.preferences()

    @staticmethod
    def candidate(c, raw_id):
        row = c.execute('''SELECT i.*,r.title,r.canonical_url,r.published_at,r.discovered_at,r.official_backfill
            FROM official_item i JOIN raw_item r ON r.id=i.raw_item_id WHERE i.raw_item_id=?''', (raw_id,)).fetchone()
        if not row:
            return None, []
        item = dict(row)
        item['reasons'] = json.loads(item.pop('reasons_json'))
        sources = [dict(json.loads(r['config_json']), enabled=bool(r['enabled'])) for r in c.execute('''
            SELECT o.config_json,o.enabled FROM official_item_source a JOIN official_source o ON o.source_id=a.source_id
            WHERE a.raw_item_id=? ORDER BY a.first_seen_at,a.source_id''', (raw_id,))]
        return item, [s for s in sources if s['enabled']]

    @staticmethod
    def _insert(c, item, *, reason, due, at):
        group = item['display_group']
        dedup = 'bark:new_item:' + group
        event_id = hashlib.sha256(dedup.encode()).hexdigest()
        status = 'pending' if due else 'suppressed'
        changed = c.execute('''INSERT OR IGNORE INTO official_notification
            (display_group,raw_item_id,claimed_at,result,id,channel,item_group,status,created_at,scheduled_at,
             suppressed_reason,dedup_key,payload_metadata)
            VALUES (?,?,?, ?,?,'bark',?,?,?,?,?,?,?)''',
            ('bark:' + group, item['raw_item_id'], at, reason, event_id, group, status, at, due,
             None if due else reason, dedup, encode({'title': item.get('title', ''), 'category': item.get('category'),
                                                   'importance': item.get('importance'), 'policy_reason': reason}))).rowcount
        if changed:
            LOG.info('notification_%s channel=bark item_id=%s reason=%s', 'queued' if due else 'suppressed', item['raw_item_id'], reason)
        return changed

    def activate(self, config, *, at=None):
        if not config.configured or not config.enabled:
            return
        self.establish_baseline(at=at)

    def establish_baseline(self, *, at=None, ignore_disabled=False):
        instant = stamp(at or utc_now())
        with self.personal.connection(write=True) as c:
            prefs = self._preferences(c)
            if prefs['activation_at'] or not prefs['enabled'] and not ignore_disabled:
                return
            # The same write lock covers timestamp AND the complete existing DB baseline.
            c.execute("UPDATE notification_preferences SET activation_at=? WHERE channel='bark'", (instant,))
            for row in c.execute('''SELECT r.id AS raw_item_id,r.title,COALESCE(i.display_group,r.id) AS display_group
                    FROM raw_item r LEFT JOIN official_item i ON i.raw_item_id=r.id WHERE r.kind='official_web' ''').fetchall():
                self._insert(c, dict(row), reason='historical', due=None, at=instant)

    def reconcile(self, config, *, at=None, limit=100, dry_run=False):
        at = aware(at or utc_now())
        self.activate(config, at=at)
        with self.personal.connection(write=True) as c:
            prefs = self._preferences(c)
            if not prefs['activation_at']:
                return 0
            policy = NotificationPolicyConfig.model_validate(prefs['policy'])
            # Missing events are recoverable after a crash between ingestion commit and the next tick.
            rows = c.execute('''SELECT i.raw_item_id FROM official_item i WHERE NOT EXISTS
                (SELECT 1 FROM official_notification n WHERE n.channel='bark' AND
                 (n.raw_item_id=i.raw_item_id OR n.item_group=i.display_group))
                ORDER BY i.fetched_at,i.raw_item_id LIMIT ?''', (limit,)).fetchall()
            for row in rows:
                item, sources = self.candidate(c, row['raw_item_id'])
                reason, due = evaluate(item, sources, policy, activation_at=prefs['activation_at'], at=at)
                if not dry_run and not (config.enabled and config.configured and prefs['enabled']):
                    reason, due = 'disabled', None
                LOG.info('notification_candidate channel=bark item_id=%s', row['raw_item_id'])
                inserted = self._insert(c, item, reason=reason, due=due, at=stamp(at))
                if inserted and due and dry_run:
                    LOG.info('notification_would_send channel=bark item_id=%s', row['raw_item_id'])
            return len(rows)

    def recover(self, *, at):
        cutoff, instant = stamp(at - timedelta(minutes=2)), stamp(at)
        with self.personal.connection(write=True) as c:
            # Do not steal a live worker's claim. Pre-HTTP claims can safely resume.
            c.execute("""UPDATE official_notification SET status='pending',scheduled_at=?,request_started_at=NULL,
                attempt_count=MAX(0,attempt_count-1)
                WHERE channel='bark' AND status='sending' AND attempted_at<? AND request_started_at IS NULL""", (instant, cutoff))
            changed = c.execute("""UPDATE official_notification SET status='failed',last_error='delivery_unknown',result='delivery_unknown',
                scheduled_at=NULL,failed_at=?,finished_at=? WHERE channel='bark' AND status='sending'
                AND attempted_at<? AND request_started_at IS NOT NULL""", (instant, instant, cutoff)).rowcount
            if changed:
                c.execute("UPDATE notification_preferences SET last_failure_at=?,last_error='delivery_unknown' WHERE channel='bark'", (instant,))

    def claim(self, config, *, at):
        instant = stamp(at)
        with self.personal.connection(write=True) as c:
            prefs = self._preferences(c)
            if not (config.enabled and config.configured and prefs['enabled']):
                return None
            policy = NotificationPolicyConfig.model_validate(prefs['policy'])
            rows = c.execute('''SELECT * FROM official_notification WHERE channel='bark'
                AND status IN ('pending','failed') AND scheduled_at IS NOT NULL AND scheduled_at<=? AND attempt_count<3
                ORDER BY scheduled_at,id LIMIT 30''', (instant,)).fetchall()
            for raw in rows:
                row = dict(raw)
                item, sources = self.candidate(c, row['raw_item_id'])
                reason, due = evaluate(item, sources, policy, activation_at=prefs['activation_at'], at=at) if item else ('missing_item', None)
                if not due:
                    c.execute("UPDATE official_notification SET status='suppressed',suppressed_reason=?,scheduled_at=NULL WHERE id=?", (reason, row['id']))
                    continue
                due = stamp(quiet_due(at, policy)) if item['importance'] != 'critical' else instant
                if due > instant:
                    c.execute('UPDATE official_notification SET scheduled_at=? WHERE id=?', (due, row['id']))
                    continue
                if item['importance'] != 'critical':
                    last_hour = stamp(at - timedelta(hours=1))
                    used = c.execute("""SELECT COUNT(*) FROM official_notification WHERE channel='bark'
                        AND (request_started_at>? OR (status='sending' AND attempted_at>?))""", (last_hour,last_hour)).fetchone()[0]
                    if used >= policy.max_per_hour:
                        c.execute('UPDATE official_notification SET scheduled_at=? WHERE id=?', (stamp(at + timedelta(minutes=5)), row['id']))
                        continue
                c.execute("""UPDATE official_notification SET status='sending',attempt_count=attempt_count+1,attempted_at=?,
                    request_started_at=NULL,scheduled_at=NULL WHERE id=? AND status IN ('pending','failed')""", (instant, row['id']))
                row.update(attempt_count=row['attempt_count'] + 1, attempted_at=instant, item=item, sources=sources, policy=policy)
                return row
        return None

    def start_request(self, row, *, at):
        with self.personal.connection(write=True) as c:
            return c.execute("""UPDATE official_notification SET request_started_at=? WHERE id=? AND status='sending'
                AND attempted_at=? AND attempt_count=? AND (SELECT enabled FROM notification_preferences WHERE channel='bark')=1""",
                (stamp(at), row['id'], row['attempted_at'], row['attempt_count'])).rowcount == 1

    def finish(self, row, result, *, at):
        instant = stamp(at)
        retry = result.retryable and row['attempt_count'] < 3
        due = stamp(at + timedelta(seconds=30 * 2 ** (row['attempt_count'] - 1))) if retry else None
        with self.personal.connection(write=True) as c:
            changed = c.execute("""UPDATE official_notification SET status=?,result=?,last_error=?,scheduled_at=?,
                sent_at=?,failed_at=?,finished_at=?,provider_message_id=? WHERE id=? AND status='sending'
                AND attempted_at=? AND attempt_count=?""",
                ('sent' if result.accepted else 'failed', 'sent' if result.accepted else result.error, result.error,
                 due, instant if result.accepted else None, None if result.accepted else instant, instant,
                 result.provider_message_id, row['id'], row['attempted_at'], row['attempt_count'])).rowcount
            if changed:
                self._health(c, result, instant)
        LOG.info('notification_%s channel=bark item_id=%s attempt=%s result=%s',
                 'sent' if result.accepted else 'failed', row['raw_item_id'], row['attempt_count'], 'sent' if result.accepted else result.error)

    @staticmethod
    def _health(c, result, instant):
        if result.accepted:
            c.execute("UPDATE notification_preferences SET last_success_at=?,last_error=NULL WHERE channel='bark'", (instant,))
        else:
            c.execute("UPDATE notification_preferences SET last_failure_at=?,last_error=? WHERE channel='bark'", (instant, result.error))

    def claim_test(self, *, at):
        instant, cutoff = stamp(at), stamp(at - timedelta(seconds=60))
        with self.personal.connection(write=True) as c:
            return c.execute("""UPDATE notification_preferences SET test_claimed_at=? WHERE channel='bark'
                AND (test_claimed_at IS NULL OR test_claimed_at<?)""", (instant, cutoff)).rowcount == 1

    def finish_test(self, result, *, at):
        with self.personal.connection(write=True) as c:
            self._health(c, result, stamp(at))

    def history(self, *, limit=20, offset=0):
        with self.personal.connection() as c:
            total = c.execute("SELECT COUNT(*) FROM official_notification WHERE channel='bark'").fetchone()[0]
            rows = c.execute('''SELECT n.id,n.raw_item_id AS item_id,r.title,n.channel,n.status,n.created_at,n.scheduled_at,n.sent_at,
                n.failed_at,n.attempt_count,n.last_error,n.suppressed_reason FROM official_notification n JOIN raw_item r ON r.id=n.raw_item_id
                WHERE n.channel='bark' ORDER BY n.created_at DESC,n.id LIMIT ? OFFSET ?''', (limit, offset)).fetchall()
            return {'items': [dict(r) for r in rows], 'total': total, 'limit': limit, 'offset': offset}

    def item_status(self, raw_id):
        with self.personal.connection() as c:
            row = c.execute('''SELECT n.status,n.sent_at,n.scheduled_at,n.last_error,n.suppressed_reason FROM official_notification n
                JOIN official_item i ON i.raw_item_id=? WHERE n.channel='bark' AND
                (n.raw_item_id=i.raw_item_id OR n.item_group=i.display_group) LIMIT 1''', (raw_id,)).fetchone()
            return dict(row) if row else {'status': 'not_sent'}
