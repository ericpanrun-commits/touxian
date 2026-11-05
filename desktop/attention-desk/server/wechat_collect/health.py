"""Collection health from existing journals; no notification content or model calls."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import tempfile
import threading

from .scheduler import SHANGHAI

LOG = logging.getLogger(__name__)
SUCCESS = {'complete', 'changed', 'no_change'}
SNAPSHOT_TIMEOUT_SECONDS = 45


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
            latest_capture = history[0] if history and state and history[0]['scan_id'] == state['last_scan_id'] else None
            staging_failed = bool(latest_capture and json.loads(latest_capture['stats_json']).get('staging_errors'))
            if staging_failed:
                status = ('changed' if state['messages_new'] else 'no_change') if latest_capture['status'] in SUCCESS else latest_capture['status']
            stale = age >= 45 * 60
            collection = ('disabled' if not enabled else 'quiet' if quiet else
                'failed' if status in {'error','interrupted'} else 'partial' if status == 'partial'
                else 'stale' if stale else 'healthy_idle' if status == 'no_change'
                else 'healthy' if status == 'changed' else 'not_scanned')
            jobs = {r['status']: r['n'] for r in c.execute("SELECT j.status,COUNT(*) AS n FROM wechat_analysis_job j JOIN wechat_batch b ON b.id=j.batch_id JOIN raw_review r ON r.raw_snapshot_id=b.snapshot_id AND r.extraction_id=j.extraction_id WHERE b.chat_id=? AND b.snapshot_id=j.snapshot_id AND b.superseded_by IS NULL AND b.status NOT IN ('ignored','archived','superseded') GROUP BY j.status", (target.id,))}
            pending = sum(jobs.get(s, 0) for s in ('pending','retry','budget_deferred','running'))
            errors = sum(jobs.get(s, 0) for s in ('error','outcome_unknown','manual_review'))
            unqueued = c.execute("SELECT count(*) FROM wechat_batch WHERE chat_id=? AND superseded_by IS NULL AND status IN ('pending','waiting_context','media_pending')", (target.id,)).fetchone()[0]
            unstaged = c.execute("SELECT count(*) FROM wechat_message m JOIN wechat_message_state s ON s.message_id=m.id WHERE m.chat_id=? AND s.status IN ('new','candidate') AND NOT EXISTS(SELECT 1 FROM wechat_batch_message bm WHERE bm.message_id=m.id AND bm.role='primary')", (target.id,)).fetchone()[0]
            last_notification = c.execute('SELECT MAX(captured_at) FROM wechat_message WHERE chat_id=?', (target.id,)).fetchone()[0]
            sources.append({'source_id': chat['source_id'], 'chat_id': target.id, 'name': target.name,
                'enabled': enabled, 'collection_state': collection,
                'last_scan_at': state['last_scan_at'] if state else None,
                'last_scan_status': status, 'last_successful_scan_at': last_ok,
                'last_notification_at': last_notification,
                'consecutive_failures': failures,
                'failure_count_scope': 'legacy_global_lower_bound' if estimated else 'per_source_journal',
                'latest_error': state['last_error'] if state else None,
                'latest_error_stage': 'staging' if staging_failed else 'collection' if state and state['last_error'] else None,
                'staging_state': 'pending' if unstaged else 'idle', 'pending_staging_messages': unstaged,
                'active_seconds_since_success': age, 'pending_analysis': pending + unqueued,
                'analysis_errors': errors, 'analysis_statuses': jobs,
                'alert_required': bool(enabled and not quiet and (failures >= 3 or stale))})
    return {'checked_at': at.isoformat(), 'quiet': quiet, 'sources': sources,
            'pending_analysis': sum(s['pending_analysis'] for s in sources),
            'analysis_errors': sum(s['analysis_errors'] for s in sources),
            'pending_staging_messages': sum(s['pending_staging_messages'] for s in sources),
            'collection_failed': any(s['collection_state'] in {'failed','partial','stale'} for s in sources)}


class CollectionHealthMonitor:
    """Independent health tick, using the existing Windows notification adapter.

    A small atomic local ledger claims before delivery. Unknown delivery remains
    claimed across restarts. One aggregate notification per 15 minutes, and at
    most one per source per six hours; changing error strings cannot cause storms.
    """
    def __init__(self, wechat, cloud_sync=None, official=None, *, notifier=None, clock=None,
                 snapshot_timeout=SNAPSHOT_TIMEOUT_SECONDS):
        from ..reminders.windows import WindowsNotifier
        self.wechat, self.cloud_sync, self.official = wechat, cloud_sync, official
        self.personal = wechat.personal
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.notifier = notifier or WindowsNotifier()
        self.ledger = self.personal.path.with_name('collection-health-alerts.json')
        self.latest = None
        self.task = None
        self.official_task = None
        self.official_snapshot = None
        self.official_last_attempt = None
        self.official_error = None
        self.official_failures = 0
        self.observation_failures = 0
        self.last_observed_health = None
        self.snapshot_timeout = snapshot_timeout
        self.snapshot_handle = None
        self.snapshot_overdue = False
        self.stop = asyncio.Event()

    def observe(self):
        """Never present an old healthy snapshot as a current observation."""
        try:
            health = self.snapshot()
        except Exception:
            return self._observation_failed('health_snapshot_unavailable')
        return self._observation_succeeded(health)

    def _observation_succeeded(self, health):
        self.observation_failures = 0
        self.last_observed_health = health
        health['observation_state'] = 'observed'
        health['observation_error'] = None
        return self._observation_status(health)

    def _observation_failed(self, reason):
        self.observation_failures += 1
        LOG.error('collection_health %s', reason)
        return self._observation_status(self._unavailable_snapshot(reason))

    def _observation_status(self, health):
        health['observation_failures'] = self.observation_failures
        health['observer'] = {'source_id': 'system:collection-health-observer',
            'name': '采集健康读取', 'alert_required': self.observation_failures >= 3 and not health['quiet']}
        return health

    def _begin_snapshot(self):
        """A stuck read must not hold up the loop or prevent process shutdown."""
        loop = asyncio.get_running_loop()
        handle = loop.create_future()

        def finish(result):
            if not handle.done():
                handle.set_result(result)

        def read():
            try:
                result = (True, self.snapshot())
            except BaseException:
                result = (False, None)
            try:
                loop.call_soon_threadsafe(finish, result)
            except RuntimeError:
                pass  # The application may already have closed its event loop.

        threading.Thread(target=read, name='collection-health-snapshot', daemon=True).start()
        return handle

    async def observe_bounded(self):
        """Poll one outstanding read until it finishes; never overlap retries."""
        if self.snapshot_handle is None:
            try:
                self.snapshot_handle = self._begin_snapshot()
            except Exception:
                return self._observation_failed('health_snapshot_unavailable')
            self.snapshot_overdue = False
        try:
            ok, health = await asyncio.wait_for(asyncio.shield(self.snapshot_handle),
                                                timeout=self.snapshot_timeout)
        except TimeoutError:
            self.snapshot_overdue = True
            return self._observation_failed('health_snapshot_timeout')
        self.snapshot_handle = None
        overdue, self.snapshot_overdue = self.snapshot_overdue, False
        if not ok:
            return self._observation_failed('health_snapshot_unavailable')
        if overdue:
            # A late result describes an older point in time. Do not revive it
            # as current health; the next tick can start a fresh read.
            return self._observation_failed('health_snapshot_expired')
        return self._observation_succeeded(health)

    def _unavailable_snapshot(self, reason='health_snapshot_unavailable'):
        at = self.clock()
        previous = self.last_observed_health or {}
        sources = []
        for row in previous.get('sources', []):
            sources.append({'source_id': row['source_id'], 'chat_id': row.get('chat_id'),
                'name': row.get('name'), 'enabled': row.get('enabled'),
                'collection_state': 'unknown', 'last_known_collection_state': row.get('collection_state'),
                'last_known_checked_at': previous.get('checked_at'),
                'last_scan_at': row.get('last_scan_at'),
                'last_successful_scan_at': row.get('last_successful_scan_at'),
                'last_notification_at': row.get('last_notification_at'),
                'consecutive_failures': None, 'latest_error': reason,
                'latest_error_stage': 'observation', 'pending_analysis': None,
                'analysis_errors': None, 'pending_sync': None, 'sync_state': 'unknown',
                'alert_required': False})
        health = {'checked_at': at.isoformat(), 'quiet': at.astimezone(SHANGHAI).hour < 6,
            'observation_state': 'observation_failed', 'observation_error': reason,
            'last_successful_observation_at': previous.get('checked_at'),
            'sources': sources, 'pending_analysis': None, 'analysis_errors': None,
            'pending_staging_messages': None, 'collection_failed': None,
            'frontend_visibility': 'unverified',
            'sync': {'state': 'unknown', 'pending_records': None, 'latest_error': reason}}
        if self.official is not None:
            if not self.official.runtime.automatic_sync_allowed:
                # The cloud observer is independent of the local SQLite read.
                try:
                    health['official'] = self._official_observation()
                except Exception:
                    health['official'] = {'owner': 'cloud', 'state': 'observation_failed', 'sources': []}
            else:
                health['official'] = {'owner': 'local', 'state': 'observation_failed', 'sources': []}
        return health

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
            if external:
                health['official'] = self._official_observation()
            else:
                try:
                    health['official']['sources'] = self.official.store.source_rows()
                except Exception:
                    health['official']['state'] = 'observation_failed'
        return health

    def _official_observation(self):
        result = dict(self.official_snapshot or {'owner': 'cloud', 'state': 'external_observation_required', 'sources': []})
        result['sources'] = [dict(row) for row in result['sources']]
        checked = datetime.fromisoformat(result['checked_at']) if result.get('checked_at') else None
        stale = bool(checked and (self.clock() - checked).total_seconds() > 15 * 60)
        if self.official_error or stale:
            result['state'] = 'observation_failed' if self.official_error else 'stale_observation'
            result['latest_error'] = self.official_error or 'source_health_stale'
            for row in result['sources']:
                row['last_known_collection_state'] = row['collection_state']
                row['collection_state'] = 'unknown'
                row['alert_required'] = False
        result['observation_failures'] = self.official_failures
        result['last_observation_attempt'] = self.official_last_attempt.isoformat() if self.official_last_attempt else None
        result['observer'] = {'source_id': 'official:health-observer', 'name': '官网健康读取',
            'alert_required': self.official_failures >= 3 or stale}
        return result

    async def _refresh_official(self):
        self.official_last_attempt = self.clock()
        try:
            if self.cloud_sync is None:
                raise ValueError('source_health_unconfigured')
            # GET only. Independent task with its own bound: an unavailable cloud
            # health endpoint cannot stall local collection alarms or the worker.
            value = await asyncio.wait_for(self.cloud_sync.request('GET', '/worker/official-health'), timeout=10)
            if (not isinstance(value, dict) or value.get('owner') != 'cloud' or value.get('state') != 'observed'
                    or not isinstance(value.get('sources'), list) or len(value['sources']) > 200):
                raise ValueError('invalid_source_health')
            checked = datetime.fromisoformat(value['checked_at'])
            if checked.tzinfo is None or abs((self.clock() - checked).total_seconds()) > 15 * 60:
                raise ValueError('invalid_source_health_time')
            allowed = {'source_id','id','name','owner','enabled','last_scan_at','last_successful_scan_at',
                'last_notification_at','consecutive_failures','last_scan_status','latest_error','collection_state',
                'stale_after_seconds','pending_content','pending_analysis','analysis_method','collected_items',
                'unmapped_raw_count','pending_sync','sync_state','sync_evidence','frontend_visibility','alert_required'}
            sources = []
            for row in value['sources']:
                if (not isinstance(row, dict) or not isinstance(row.get('source_id'), str)
                        or not row['source_id'].startswith('official:') or type(row.get('alert_required')) is not bool
                        or not isinstance(row.get('collection_state'), str)):
                    raise ValueError('invalid_source_health_source')
                sources.append({key: row[key] for key in allowed if key in row})
            self.official_snapshot = {key: value[key] for key in
                ('owner','state','checked_at','count_unit','pending_content','pending_sync','unmapped_raw_count') if key in value}
            self.official_snapshot['sources'] = sources
            self.official_error = None
            self.official_failures = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            self.official_error = 'source_health_unavailable'
            self.official_failures += 1

    def _observe_sync(self, health):
        if self.cloud_sync is not None:
            worker = self.cloud_sync
            config = worker.settings.read()
            if config and config.get('enabled'):
                state = dict(worker.state)
                held = bool(state.get('uploads_held'))
                catalogued = (getattr(worker, 'local_only_catalogued', False) if held
                              else getattr(worker, 'workspace', None) is not None)
                if not catalogued:
                    # The worker fills hashes page by page. Until the complete
                    # remote catalogue has arrived, absent hashes prove nothing.
                    health['sync'] = {'state': 'held_local_only' if held else 'unknown',
                        'pending_records': None, 'latest_error': state.get('last_error'),
                        'evidence': 'record_catalogue_not_ready'}
                    for source in health['sources']:
                        source['pending_sync'] = None
                        source['sync_state'] = 'held_local_only' if held else 'unknown'
                    return
                from ..cloud_sync.projection import CloudProjection
                # Each read owns its temporary backup. A timed-out daemon read
                # must never race a new monitor after service shutdown/restart.
                root = self.personal.path.parent / 'collection-health-projection'
                root.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='read-', dir=root) as cache:
                    projection = CloudProjection(self.personal, Path(cache))
                    records = projection.records()
                with self.personal.connection() as c:
                    raw_sources = {r['id']: r['source_id'] for r in c.execute("SELECT id,source_id FROM raw_item WHERE kind IN ('wechat','official_web','wechat_batch')")}
                # source_id is authoritative; kind labels evolved historically.
                with self.personal.connection() as c:
                    raw_sources.update({r['raw_item_id']: r['source_id'] for r in c.execute('SELECT b.raw_item_id,ch.source_id FROM wechat_batch b JOIN wechat_chat ch ON ch.id=b.chat_id WHERE b.raw_item_id IS NOT NULL')})
                records = [r for r in records if r['kind'] in {'raw_item','inbox_item','inbox'} and r['id'] in raw_sources]
                pending = [r for r in records if worker.hashes.get((r['kind'],r['id'])) != r['source_hash']]
                health['sync'] = {'state': 'held_local_only' if state.get('uploads_held') else 'failed' if state.get('last_error') else
                    'pending' if pending else 'synchronized' if state.get('connected') else 'unknown',
                    'pending_records': len(pending), 'latest_error': state.get('last_error'),
                    'evidence': 'per_record_source_hash_acknowledged_by_publish'}
                for source in health['sources']:
                    source['pending_sync'] = sum(raw_sources[r['id']] == source['source_id'] for r in pending)
                    source_records = sum(raw_sources[r['id']] == source['source_id'] for r in records)
                    source['sync_state'] = 'held_local_only' if state.get('uploads_held') else ('failed' if state.get('last_error') else 'pending') if source['pending_sync'] else 'synchronized' if source_records else 'not_collected'

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
        official = health.get('official') or {}
        rows = [*health['sources'], *official.get('sources', [])]
        if health.get('observer'):
            rows.append(health['observer'])
        if official.get('observer'):
            rows.append(official['observer'])
        for row in rows:
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
                f'{len(due)} 个通知来源采集失败、扫描过期或健康状态无法读取。',
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
        tasks = [task for task in (self.task, self.official_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.snapshot_handle is not None:
            self.snapshot_handle.cancel()
            self.snapshot_handle = None

    async def _run(self):
        while not self.stop.is_set():
            try:
                self.latest = await self.observe_bounded()
                await asyncio.to_thread(self.deliver, self.latest)
                if self.official is not None and not self.official.runtime.automatic_sync_allowed:
                    elapsed = (self.clock() - self.official_last_attempt).total_seconds() if self.official_last_attempt else None
                    if ((self.official_task is None or self.official_task.done())
                            and (elapsed is None or elapsed >= (60 if self.official_error else 300))):
                        self.official_task = asyncio.create_task(self._refresh_official(), name='official-health-read')
            except Exception:
                LOG.error('collection_health check_failed')
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=60)
            except TimeoutError:
                pass
