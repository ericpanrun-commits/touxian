"""Hourly finite worker. No FastAPI app, browser, Docker or local SQLite state."""
import json
import logging
import os
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..notifications.config import BarkConfig
from ..notifications.service import NotificationDispatcher
from ..official_service import OfficialSourceMonitor
from .database import PostgresDatabase
from .notifications import PostgresNotificationStore
from .runtime import ActionsRuntime
from .store import OneShotRuntimeStore, PostgresOfficialStore

LABELS={'sources_checked':'Sources checked','succeeded':'Succeeded','failed':'Failed',
    'new_articles':'New articles','updated':'Updated','candidates':'Notification candidates',
    'sent':'Bark sent','suppressed':'Suppressed','would_send':'Would send',
    'duplicate_prevented':'Acceptance repeat prevented','low_relevance_suppressed':'Acceptance low relevance suppressed'}


def emit_summary(summary, destination=None):
    safe={key:summary.get(key,0) for key in LABELS}
    safe.update(status=summary.get('status','failed'),dry_run=summary.get('dry_run',True),
                elapsed_seconds=summary.get('elapsed_seconds',0))
    print(json.dumps(safe,ensure_ascii=False))
    if destination:
        lines=['## ZQU hourly worker','',f"Status: {safe['status']} · Dry run: {safe['dry_run']}",'']
        lines.extend(f'- {label}: {safe[key]}' for key,label in LABELS.items())
        lines.append(f"- Elapsed seconds: {safe['elapsed_seconds']}")
        with Path(destination).open('a',encoding='utf-8') as handle:handle.write('\n'.join(lines)+'\n')


def run_once(runtime, database, *, bark_config=None, client=None, client_factory=None, config_path=None):
    started=time.monotonic()
    if runtime.environment!='production' or not runtime.execution_verified or not runtime.auto_sync:
        raise ValueError('仅受保护的 GitHub Actions 生产任务可以执行远程官网周期。')
    store=PostgresOfficialStore(database)
    meta=store.meta()
    if meta['schema_version']!=1 or not runtime.expected_database_id or meta['database_id']!=runtime.expected_database_id:
        raise ValueError('远程数据库身份或版本不匹配。')
    if not runtime.dry_run and not meta['first_dry_run_verified']:
        raise ValueError('必须先完成首轮真实静默验证，再开启 Bark。')
    configuration=bark_config or BarkConfig.from_env(dict(os.environ))
    if not runtime.dry_run and (not runtime.notification_authority or not configuration.enabled or not configuration.configured):
        raise ValueError('正式运行需要明确通知权限与有效的 Bark Secrets。')
    runtime=replace(runtime,storage_verified=True)
    owner=str(uuid4())
    if not store.acquire_lease(owner,'actions',900):
        return {'status':'skipped_concurrent','dry_run':runtime.dry_run}
    run_id=str(uuid4())
    monitor=None
    timer=None
    summary={key:0 for key in LABELS}
    summary.update(status='failed',dry_run=runtime.dry_run)
    try:
        with database.connection() as db:
            db.execute("UPDATE zqu_worker.worker_runs SET status='interrupted',finished_at=clock_timestamp() WHERE finished_at IS NULL")
            db.execute("INSERT INTO zqu_worker.worker_runs(id,github_run,status,dry_run) VALUES(%s,%s,'running',%s)",(run_id,runtime.run_id,runtime.dry_run))
        repository=PostgresNotificationStore(database,owner)
        dispatcher=NotificationDispatcher(None,config=configuration,client=client,
            runtime=runtime,repository=repository,official_repository=store)
        options={'client_factory':client_factory} if client_factory else {}
        monitor=OfficialSourceMonitor(None,runtime=runtime,repository=store,runtime_store=OneShotRuntimeStore(),
            notifications=dispatcher,start_scheduler=False,config_path=config_path,batch_seconds=420,article_limit=80,**options)
        if monitor.configuration_error:
            raise ValueError('官方来源配置无效。')
        # A one-shot deadline, not a resident scheduler. HTTP has its own bounded timeout.
        timer=threading.Timer(450,monitor.stop_event.set)
        timer.daemon=True;timer.start()
        instant=datetime.now(timezone.utc).isoformat()
        due=[row for row in store.source_rows() if row['id'] in monitor.configured and row['enabled']
             and (not row['next_check_at'] or row['next_check_at']<=instant)]
        due.sort(key=lambda row:({'high':0,'normal':1,'low':2}[row['priority']],row['next_check_at'] or '',row['id']))
        if due:
            scan=monitor.run_once([row['id'] for row in due])
            summary.update(sources_checked=scan['done'],failed=scan['failed'],succeeded=scan['done']-scan['failed'],
                           new_articles=scan['new_count'],updated=scan['changed_count'])
        dispatcher.tick(limit=10)
        summary.update(repository.counters)
        healthy=not dispatcher.last_cycle_error and (not due or summary['succeeded']>0)
        summary['status']='ok' if healthy and not summary['failed'] else 'partial' if healthy else 'failed'
        summary['elapsed_seconds']=round(time.monotonic()-started,2)
        with database.connection() as db:
            # Retain identities and dedup forever; only detailed operation logs expire.
            db.execute("DELETE FROM zqu_worker.crawl_runs WHERE started_at<clock_timestamp()-interval '30 days'")
            db.execute("DELETE FROM zqu_worker.worker_runs WHERE started_at<clock_timestamp()-interval '30 days' AND id<>%s",(run_id,))
            if runtime.dry_run and summary['succeeded']>0 and summary['would_send']<=10 and not dispatcher.last_cycle_error:
                db.execute('UPDATE zqu_worker.meta SET first_dry_run_verified=true WHERE id=1')
            db.execute('UPDATE zqu_worker.worker_runs SET finished_at=clock_timestamp(),status=%s,summary=%s,elapsed_seconds=%s WHERE id=%s',
                (summary['status'],Jsonb(summary),summary['elapsed_seconds'],run_id))
            db.execute('''INSERT INTO zqu_worker.cloud_status(id,last_cloud_run,last_successful_cloud_run,next_expected_run,
                last_source_success,last_bark_success,last_run_status,dry_run)
                SELECT 1,clock_timestamp(),CASE WHEN %s THEN clock_timestamp() END,clock_timestamp()+interval '1 hour',
                  (SELECT max((state->>'last_success_at')::timestamptz) FROM zqu_worker.sources),last_success_at,%s,%s
                FROM zqu_worker.meta WHERE id=1 ON CONFLICT(id) DO UPDATE SET last_cloud_run=excluded.last_cloud_run,
                  last_successful_cloud_run=COALESCE(excluded.last_successful_cloud_run,cloud_status.last_successful_cloud_run),
                  next_expected_run=excluded.next_expected_run,last_source_success=excluded.last_source_success,
                  last_bark_success=excluded.last_bark_success,last_run_status=excluded.last_run_status,dry_run=excluded.dry_run''',
                (healthy,summary['status'],runtime.dry_run))
        return summary
    finally:
        if timer:timer.cancel()
        if monitor:monitor.close()
        store.release_lease(owner,'actions')


def main():
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(name)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    database=None
    try:
        runtime=ActionsRuntime.from_env(dict(os.environ))
        if not runtime.database_url:
            raise ValueError('尚未配置远程数据库。')
        database=PostgresDatabase(runtime.database_url)
        mode=os.environ.get('ZQU_RUN_MODE','cycle')
        if mode=='acceptance':
            from .acceptance import run_acceptance
            summary=run_acceptance(runtime,database,event=os.environ.get('GITHUB_EVENT_NAME'))
        elif mode=='cycle':summary=run_once(runtime,database)
        else:raise ValueError('未知任务模式。')
        emit_summary(summary,os.environ.get('GITHUB_STEP_SUMMARY'))
        return 1 if summary['status']=='failed' else 0
    except Exception:
        # DB/client exceptions can include credentials; never print their repr/traceback.
        emit_summary({'status':'failed_configuration_or_database','dry_run':True},os.environ.get('GITHUB_STEP_SUMMARY'))
        logging.error('worker_failed code=configuration_or_database; inspect secret names, migration and Actions context')
        return 1
    finally:
        if database:database.close()


if __name__=='__main__':raise SystemExit(main())
