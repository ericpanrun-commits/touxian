"""Bounded, serial source polling into the shared RawItem review pipeline."""
from __future__ import annotations

import os
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from .official_http import PublicHTMLClient
from .official_parsers import parse_article, parse_list
from .official_sources import SourceConfig, SourceFailure, load_sources
from .official_store import OfficialStore
from .runtime import RuntimeConfig
from .cloud_store import CloudStore

LOG = logging.getLogger(__name__)


class OfficialSourceMonitor:
    def __init__(self, personal, *, config_path=None, client_factory=PublicHTMLClient, start_scheduler=True, notifier=None, notifications=None, runtime=None,
                 repository=None, runtime_store=None, batch_seconds=900, article_limit=240):
        self.runtime = runtime or RuntimeConfig.from_env()
        self.store = repository if repository is not None else OfficialStore(personal)
        self.batch_seconds, self.article_limit = batch_seconds, article_limit
        self.client_factory = client_factory
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._run = {'running': False, 'done': 0, 'total': 0, 'failed': 0, 'current': '', 'new_count': 0, 'changed_count': 0}
        self.configuration_error = ''
        self.configured = set()
        self.worker = self.scheduler = None
        self.notifier = notifier
        self.owner = None
        try:
            sources = load_sources(config_path)
            self.store.configure(sources)
            self.configured = {s.id for s in sources}
        except Exception:
            self.configuration_error = '官网来源配置未配置或无法读取，请检查本地来源配置文件。手动输入和已有事项仍可使用。'
        from .notifications.service import NotificationDispatcher
        self.notifications = notifications or NotificationDispatcher(personal, notifier=notifier, runtime=self.runtime)
        self.cloud_store = runtime_store if runtime_store is not None else CloudStore(personal,self.runtime)
        self.cloud_store.initialize()
        if start_scheduler and self.runtime.automatic_sync_allowed and os.environ.get('ATTENTION_OFFICIAL_POLL', '1') != '0':
            self.scheduler = threading.Thread(target=self._schedule, name='official-source-scheduler', daemon=True)
            self.scheduler.start()

    def close(self):
        self.stop_event.set()
        if self.scheduler:
            self.scheduler.join(timeout=2)
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=45)
        self.cloud_store.release()

    def overview(self):
        sources = [row for row in self.store.source_rows() if row['id'] in self.configured]
        with self.lock:
            scan = dict(self._run)
        if not self._run_lock.locked():
            persisted=self.store.last_scan()
            if persisted: scan.update(persisted)
        scan['running'] = scan['running'] or self.store.lease_active()
        runs = self.store.recent_runs(limit=100)
        return {'configuration': {'status': 'unconfigured' if self.configuration_error else 'configured', 'error': self.configuration_error},
                'school': sources[0]['school'] if sources else None, 'sources': sources, 'scan': scan, 'recent_runs':runs,
                'summary': {'source_count': len(sources), 'enabled_count': sum(s['enabled'] for s in sources),
                            'new_count': sum(s['last_discovered_count'] for s in sources),
                            'pending_count': self.store.pending_count()}}

    def set_enabled(self, source_id, enabled):
        if not isinstance(source_id, str) or source_id not in self.configured or type(enabled) is not bool:
            raise ValueError('来源编号或启用状态无效。')
        self.store.set_enabled(source_id, enabled)
        return self.overview()

    def _prepare_scan(self, source_ids=None):
        if self.configuration_error:
            raise ValueError(self.configuration_error)
        sources = {row['id']: row for row in self.store.source_rows() if row['id'] in self.configured}
        ids = source_ids if source_ids is not None else [s['id'] for s in sources.values() if s['enabled']]
        if not isinstance(ids, list) or not ids or any(value not in sources or not sources[value]['enabled'] for value in ids):
            raise ValueError('请启用并选择一个有效来源后检查。')
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError('已有来源检查正在进行，请等待完成。')
        owner = str(uuid4())
        try:
            acquired = self.store.acquire_lease(owner)
        except Exception:
            self._run_lock.release()
            raise
        if not acquired:
            self._run_lock.release()
            raise RuntimeError('另一进程正在同步官网，请等待完成。')
        self.owner = owner
        ids = list(dict.fromkeys(ids))
        with self.lock:
            self._run = {'running': True, 'done': 0, 'total': len(ids), 'failed': 0, 'current': '', 'new_count': 0, 'changed_count': 0}
        return ids

    def start_scan(self, source_ids=None):
        ids = self._prepare_scan(source_ids)
        self.worker = threading.Thread(target=self._run_scan, args=(ids,), name='official-source-check', daemon=True)
        self.worker.start()
        return self.overview()

    def run_once(self, source_ids=None):
        """Synchronous finite cycle for Actions; shares the exact desktop crawler."""
        ids = self._prepare_scan(source_ids)
        self._run_scan(ids)
        with self.lock:
            return dict(self._run)

    def _run_scan(self, source_ids):
        cache = {}
        owner=self.owner
        heartbeat_done=threading.Event()
        lease_lost=threading.Event()
        def heartbeat():
            while not heartbeat_done.wait(15):
                try:
                    if not self.store.renew_lease(owner):
                        lease_lost.set(); return
                except Exception:
                    lease_lost.set(); return
        renewal=threading.Thread(target=heartbeat,name='official-lease-heartbeat',daemon=True)
        renewal.start()
        remaining=self.article_limit
        started=time.monotonic()
        try:
            for source_id in source_ids:
                if self.stop_event.is_set() or lease_lost.is_set() or remaining<=0 or time.monotonic()-started>=self.batch_seconds:
                    break
                current = next((s for s in self.store.source_rows() if s['id'] == source_id), None)
                if not current or not current['enabled']:
                    continue
                source = SourceConfig.model_validate({k: current[k] for k in SourceConfig.model_fields})
                with self.lock:
                    self._run['current'] = source.name
                run_id=self.store.begin(source_id,owner)
                LOG.info('source_sync_started instance=%s source_id=%s',self.runtime.instance_id,source_id)
                backfill=not current['backfill_completed_at']
                cutoff=(datetime.now(timezone.utc)-timedelta(days=source.backfill_days)).date().isoformat()
                counts = {'new': 0, 'discovered': 0, 'changed': 0, 'unchanged': 0, 'saved': 0, 'requests': 0, 'scanned':0,'failed':0}
                error = None
                client = None
                try:
                    with self.client_factory(source, stop=self.stop_event) as client:
                        entries, visited = {}, set()
                        page = source.list_url
                        for _ in range(source.max_pages):
                            if not page or page in visited:
                                break
                            visited.add(page)
                            try:
                                result = client.get(page)
                                rows, page = parse_list(result.body, result.url, source)
                            except SourceFailure as exc:
                                error = error or exc
                                break
                            for entry in rows:
                                if not entry['published_at'] or entry['published_at']>=cutoff or self.store.fetch_state(entry['url']):
                                    entries.setdefault(entry['url'], entry)
                            counts['scanned']+=len(rows)
                            if len(entries) >= source.max_items:
                                break
                            if rows and all(e['published_at'] and e['published_at']<cutoff for e in rows):
                                break
                            if not backfill and rows and all(self.store.fetch_state(e['url']) for e in rows):
                                break
                        work=sorted(entries.values(),key=lambda e:(e['published_at'] or '',e['title']),reverse=True)[:source.max_items]
                        if not backfill:
                            work+=self.store.revalidation_entries(source,{e['url'] for e in work})
                        for entry in work:
                            if remaining<=0 or time.monotonic()-started>=self.batch_seconds:
                                error=error or SourceFailure('batch_limit','本轮达到文章上限，下一轮继续补全。',status='partial')
                                break
                            if self.stop_event.is_set() or lease_lost.is_set():
                                raise SourceFailure('interrupted', '检查因应用退出而中断，已取得原文保留。', status='unreachable')
                            discovered = self.store.discover(source, entry, backfill=backfill)
                            counts['discovered'] += int(discovered['new'])
                            try:
                                state = self.store.fetch_state(entry['url'])
                                if state and not backfill and state['title']==entry['title'] and state['published_at']==entry['published_at'] and state['last_fetched_at']>(datetime.now(timezone.utc)-timedelta(seconds=source.revalidate_interval)).isoformat():
                                    counts['unchanged']+=1
                                    continue
                                remaining-=1
                                if entry['url'] in cache:
                                    result, article = cache[entry['url']]
                                else:
                                    result = client.get(entry['url'], etag=state['etag'] if state else None, last_modified=state['last_modified'] if state else None)
                                    if result.status == 304:
                                        if not state:
                                            raise SourceFailure('invalid_304', '网站返回未变化，但本机没有对应原文。')
                                        saved = self.store.unchanged(source_id, entry['url'], state)
                                        counts['unchanged'] += 1
                                        counts['saved'] += 1
                                        continue
                                    article = parse_article(result.body, result.url, source, entry)
                                    cache[entry['url']] = (result, article)
                                saved = self.store.collect(source, entry['url'], result.url, article, result, backfill=backfill)
                                counts[saved['kind']] += 1
                                counts['saved'] += 1
                                if saved['kind']=='new' and not backfill:
                                    self._notify(saved['raw_item_id'])
                            except SourceFailure as exc:
                                self.store.fetch_error(discovered['raw_item_id'], exc)
                                error = error or exc
                                counts['failed']+=1
                                if counts['failed']>=3:
                                    break
                except SourceFailure as exc:
                    error = error or exc
                except Exception:
                    error = SourceFailure('collection_error', '该来源检查未完成，已有原文和事项保留。')
                counts['requests'] = client.request_count if client else 0
                if error and not counts['failed']: counts['failed']=1
                self.store.finish(source_id, counts, error,run_id)
                LOG.info('source_sync_finished instance=%s source_id=%s inserted=%s updated=%s failed=%s',self.runtime.instance_id,source_id,counts['new'],counts['changed'],counts['failed'])
                with self.lock:
                    self._run['done'] += 1
                    self._run['failed'] += int(error is not None)
                    self._run['new_count'] += counts['new']
                    self._run['changed_count'] += counts['changed']
        finally:
            heartbeat_done.set()
            renewal.join(timeout=2)
            try:
                self.store.release_lease(owner)
            except sqlite3.DatabaseError:
                # A temporarily busy DB must not strand the in-process mutex.
                # The persisted lease still expires safely without renewal.
                pass
            finally:
                with self.lock:
                    self._run['running'] = False
                    self._run['current'] = ''
                if self._run_lock.locked():
                    self._run_lock.release()

    def _schedule(self):
        while not self.stop_event.wait(self.runtime.poll_seconds):
            try:
                if not self.runtime.automatic_sync_allowed or not self.cloud_store.scheduler_claim():
                    continue
                LOG.info('scheduler_tick instance=%s environment=%s',self.runtime.instance_id,self.runtime.environment)
                self.notifications.tick()
                self.cloud_store.maintenance()
                if not self.configuration_error and not self._run_lock.locked():
                    stamp = datetime.now(timezone.utc).isoformat()
                    due = [s['id'] for s in self.store.source_rows() if s['id'] in self.configured and s['enabled'] and (not s['next_check_at'] or s['next_check_at'] <= stamp)]
                    if due:
                        self.start_scan(due)
            except Exception:
                # A transient local-store failure must not permanently kill polling.
                LOG.warning('scheduler_cycle_failed instance=%s',self.runtime.instance_id)
                continue

    def _notify(self,raw_id):
        self.notifications.notify_windows(raw_id)

    def notice_detail(self, source_id, url):
        # Compatibility route returns a saved raw review, never a second event model.
        from .review_store import ReviewStore
        with self.store.personal.connection() as c:
            row = c.execute('''SELECT f.latest_snapshot_id FROM official_fetch_state f JOIN official_url_alias a ON a.raw_item_id=f.raw_item_id
                JOIN official_item_source s ON s.raw_item_id=f.raw_item_id WHERE a.url=? AND s.source_id=?''', (url, source_id)).fetchone()
            if not row:
                raise ValueError('没有已保存的原文，请先检查来源。')
            return ReviewStore._bundle(c, row['latest_snapshot_id'])

    def set_read(self, *_):
        raise ValueError('官网通知请在统一工作台核对或忽略。')

    def set_action(self, *_):
        raise ValueError('官网通知请在统一工作台确认事项后完成。')

    def set_interval(self, hours):
        if hours not in {6, 12, 24}:
            raise ValueError('检查间隔可选 6、12、24 小时；停用请关闭对应来源。')
        with self.store.personal.connection(write=True) as c:
            c.execute('UPDATE official_source SET polling_interval=?,fetch_interval=?', (hours * 3600,hours * 3600))
