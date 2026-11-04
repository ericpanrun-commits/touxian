"""Collection health from existing journals; no notification content or model calls."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path

from .scheduler import SHANGHAI

LOG = logging.getLogger(__name__)
SUCCESS = {'complete', 'changed', 'no_change'}


def active_seconds(start, end):
    """Elapsed collection time, excluding the fixed 00:00–06:00 quiet window."""
    start, end = start.astimezone(SHANGHAI), end.astimezone(SHANGHAI)
    total = 0.0
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        left, right = max(start, day + timedelta(hours=6)), min(end, day + timedelta(days=1))
        total += max(0, (right - left).total_seconds())
        day += timedelta(days=1)
    return total


def wechat_health(personal, document, at):
    sources = []
    quiet = at.astimezone(SHANGHAI).hour < 6
    with personal.connection() as c:
        global_history = c.execute('SELECT * FROM wechat_scan ORDER BY created_at DESC,id DESC LIMIT 2000').fetchall()
        global_failures = 0
        for scan in global_history:
            if scan['status'] in SUCCESS:
                break
            if scan['status'] in {'error', 'partial', 'interrupted'}:
                global_failures += 1
        for target in document.wechat.priority_chats:
            if not document.wechat.allows(target):
                continue
            chat = c.execute('SELECT ch.*,cu.last_success_at FROM wechat_chat ch JOIN wechat_cursor cu ON cu.chat_id=ch.id WHERE ch.id=?', (target.id,)).fetchone()
            if not chat:
                continue
            state = c.execute('SELECT * FROM wechat_group_state WHERE chat_id=?', (target.id,)).fetchone()
            history = c.execute('SELECT * FROM wechat_chat_scan WHERE chat_id=? ORDER BY created_at DESC,id DESC LIMIT 2000', (target.id,)).fetchall()
            failures = 0
            for entry in history:
                if entry['status'] in SUCCESS:
                    break
                if entry['status'] in {'error','partial','interrupted'}:
                    failures += 1
            # Pre-fix errors have only a group-state snapshot and a global journal.
            # Do not invent historical per-group counts from successful rows.
            estimated = bool(state and state['last_status'] in {'error','partial','interrupted'}
                and (not history or history[0]['scan_id'] != state['last_scan_id']))
            if estimated:
                failures = max(1, global_failures)
            enabled = document.wechat.enabled and target.enabled and bool(chat['enabled'])
            last_ok = chat['last_success_at']
            age = active_seconds(datetime.fromisoformat(last_ok or chat['created_at']), at)
            status = state['last_status'] if state else 'not_scanned'
            stale = age >= 45 * 60
            collection = ('disabled' if not enabled else 'quiet' if quiet else
                'failed' if status in {'error','interrupted'} else 'partial' if status == 'partial'
                else 'stale' if stale else 'healthy_idle' if status == 'no_change'
                else 'healthy' if status == 'changed' else 'not_scanned')
            jobs = {r['status']: r['n'] for r in c.execute("SELECT j.status,COUNT(*) AS n FROM wechat_analysis_job j JOIN wechat_batch b ON b.id=j.batch_id WHERE b.chat_id=? AND b.superseded_by IS NULL AND b.status NOT IN ('ignored','archived','superseded') GROUP BY j.status", (target.id,))}
            pending = sum(jobs.get(s, 0) for s in ('pending','retry','budget_deferred','running'))
            errors = sum(jobs.get(s, 0) for s in ('error','outcome_unknown','manual_review'))
            unqueued = c.execute("SELECT count(*) FROM wechat_batch WHERE chat_id=? AND superseded_by IS NULL AND status IN ('pending','waiting_context','media_pending')", (target.id,)).fetchone()[0]
            last_notification = c.execute('SELECT MAX(captured_at) FROM wechat_message WHERE chat_id=?', (target.id,)).fetchone()[0]
            sources.append({'source_id': chat['source_id'], 'chat_id': target.id, 'name': target.name,
                'enabled': enabled, 'collection_state': collection,
                'last_scan_at': state['last_scan_at'] if state else None,
                'last_scan_status': status, 'last_successful_scan_at': last_ok,
                'last_notification_at': last_notification,
                'consecutive_failures': failures,
                'failure_count_scope': 'legacy_global_lower_bound' if estimated else 'per_source_journal',
                'latest_error': state['last_error'] if state else None,
                'active_seconds_since_success': age, 'pending_analysis': pending + unqueued,
                'analysis_errors': errors, 'analysis_statuses': jobs,
                'alert_required': bool(enabled and not quiet and (failures >= 3 or stale))})
    return {'checked_at': at.isoformat(), 'quiet': quiet, 'sources': sources,
            'pending_analysis': sum(s['pending_analysis'] for s in sources),
            'analysis_errors': sum(s['analysis_errors'] for s in sources),
            'collection_failed': any(s['collection_state'] in {'failed','partial','stale'} for s in sources)}


class CollectionHealthMonitor:
    """Independent health tick, using the existing Windows notification adapter.

    A small atomic local ledger claims before delivery. Unknown delivery remains
    claimed across restarts. One aggregate notification per 15 minutes, and at
    most one per source per six hours; changing error strings cannot cause storms.
    """
    def __init__(self, wechat, cloud_sync=None, official=None, *, notifier=None, clock=None):
        from ..reminders.windows import WindowsNotifier
        self.wechat, self.cloud_sync, self.official = wechat, cloud_sync, official
        self.personal = wechat.personal
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.notifier = notifier or WindowsNotifier()
        self.ledger = self.personal.path.with_name('collection-health-alerts.json')
        self.latest = None
        self.task = None
        self.stop = asyncio.Event()

    def snapshot(self):
        health = wechat_health(self.personal, self.wechat.document, self.clock())
        if getattr(self.wechat, 'lock', None) and self.wechat.lock.locked():
            with self.personal.connection() as c:
                scan = c.execute("SELECT created_at FROM wechat_scan WHERE status='running' ORDER BY created_at DESC LIMIT 1").fetchone()
            if scan and self.clock() - datetime.fromisoformat(scan['created_at']) < timedelta(minutes=5):
                for source in health['sources']:
                    source['collection_state'] = 'scanning'
                    source['alert_required'] = False
        health['frontend_visibility'] = 'unverified'
        health['sync'] = {'state': 'unconfigured', 'pending_records': None}
        try:
            self._observe_sync(health)
        except Exception:
            # Cloud projection or credentials cannot silence collection alarms.
            health['sync'] = {'state': 'unknown', 'pending_records': None,
                              'latest_error': 'sync_observation_failed'}
        for source in health['sources']:
            source.setdefault('pending_sync', None)
            source.setdefault('sync_state', 'unconfigured')
        if self.official is not None:
            external = not self.official.runtime.automatic_sync_allowed
            health['official'] = {'owner': 'cloud' if external else 'local',
                'state': 'external_observation_required' if external else 'observed',
                'sources': []}
            if not external:
                try:
                    health['official']['sources'] = self.official.store.source_rows()
                except Exception:
                    health['official']['state'] = 'observation_failed'
        return health

    def _observe_sync(self, health):
        if self.cloud_sync is not None:
            worker = self.cloud_sync
            config = worker.settings.read()
            if config and config.get('enabled'):
                from ..cloud_sync.projection import CloudProjection
                # Own backup path: never race the sync worker's projection snapshot.
                projection = CloudProjection(self.personal, self.personal.path.parent / 'collection-health-projection')
                records = projection.records()
                with self.personal.connection() as c:
                    raw_sources = {r['id']: r['source_id'] for r in c.execute("SELECT id,source_id FROM raw_item WHERE kind IN ('wechat','official_web','wechat_batch')")}
                # source_id is authoritative; kind labels evolved historically.
                with self.personal.connection() as c:
                    raw_sources.update({r['raw_item_id']: r['source_id'] for r in c.execute('SELECT b.raw_item_id,ch.source_id FROM wechat_batch b JOIN wechat_chat ch ON ch.id=b.chat_id WHERE b.raw_item_id IS NOT NULL')})
                records = [r for r in records if r['kind'] in {'raw_item','inbox_item','inbox'} and r['id'] in raw_sources]
                pending = [r for r in records if worker.hashes.get((r['kind'],r['id'])) != r['source_hash']]
                state = dict(worker.state)
                health['sync'] = {'state': 'failed' if state.get('last_error') else
                    'pending' if pending else 'synchronized' if state.get('connected') else 'unknown',
                    'pending_records': len(pending), 'latest_error': state.get('last_error'),
                    'evidence': 'per_record_source_hash_acknowledged_by_publish'}
                for source in health['sources']:
                    source['pending_sync'] = sum(raw_sources[r['id']] == source['source_id'] for r in pending)
                    source['sync_state'] = 'failed' if state.get('last_error') else 'pending' if source['pending_sync'] else health['sync']['state']

    def deliver(self, health):
        if health['quiet']:
            return
        at = self.clock()
        try:
            ledger = json.loads(self.ledger.read_text(encoding='utf-8')) if self.ledger.exists() else {'sources': {}}
        except (OSError, ValueError):
            LOG.error('collection_health alert_ledger_unavailable')
            return  # Fail closed on deduplication failure.
        sources = ledger.setdefault('sources', {})
        due = []
        for row in health['sources']:
            old = sources.get(row['source_id'], {})
            if row['alert_required'] and (not old.get('claimed_at') or
                    at - datetime.fromisoformat(old['claimed_at']) >= timedelta(hours=6)):
                due.append(row)
        if not due or ledger.get('last_claim_at') and at - datetime.fromisoformat(ledger['last_claim_at']) < timedelta(minutes=15):
            return
        stamp = at.isoformat()
        delivery_id = 'collection-health:' + hashlib.sha256((stamp + '|'.join(r['source_id'] for r in due)).encode()).hexdigest()
        for row in due:
            sources[row['source_id']] = {'claimed_at': stamp, 'result': 'delivery_unknown'}
        ledger['last_claim_at'] = stamp
        self._save_ledger(ledger)  # Durable claim before contacting the OS.
        try:
            result = self.notifier.send(['Attention Desk · 通知采集异常',
                f'{len(due)} 个授权群连续扫描失败或超过 45 分钟未成功扫描。',
                '请打开本机工作台检查采集健康状态；原文与历史数据仍保留。'], delivery_id=delivery_id)
        except Exception:
            result = 'delivery_unknown'
        for row in due:
            sources[row['source_id']]['result'] = result
        self._save_ledger(ledger)
        LOG.warning('collection_health alert sources=%s result=%s', len(due), result)

    def _save_ledger(self, value):
        temporary = self.ledger.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=True), encoding='utf-8')
        temporary.replace(self.ledger)

    async def start(self):
        self.task = asyncio.create_task(self._run(), name='collection-health')

    async def close(self):
        self.stop.set()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def _run(self):
        while not self.stop.is_set():
            try:
                self.latest = await asyncio.to_thread(self.snapshot)
                await asyncio.to_thread(self.deliver, self.latest)
            except Exception:
                LOG.error('collection_health check_failed')
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=60)
            except TimeoutError:
                pass
