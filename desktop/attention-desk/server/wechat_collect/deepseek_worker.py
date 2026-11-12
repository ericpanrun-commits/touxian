"""Dedicated text-analysis consumer of the existing 15-minute intake queue.

Never starts Collector, a web server, CloudSync, Qwen or an attachment parser.
Automatic releases honor the real global holds as well as dedicated controls.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import ctypes
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import traceback
from uuid import uuid4

from ..ai_settings import AISettings
from ..extraction_errors import ExtractionFailure
from ..personal_store import PersonalStore
from ..user_context import UserContextStore
from .analysis import AnalysisQueue
from .config import ConfigStore
from .deepseek_content_gate import leased_grant,binding_digest
from .scheduler import SchedulerPolicy, utc_now

LOG = logging.getLogger('attention.deepseek')
CONTROLS = 'deepseek-worker-controls.json'
STATE = 'deepseek-worker-state.json'


def checked_controls(path, *, paid_paused, cloud_held=True):
    try:
        value=json.loads(path.read_text(encoding='utf8'))
        if value.get('version')!=1 or type(value.get('hold_cloud_uploads')) is not bool or (cloud_held is not None and value['hold_cloud_uploads'] is not cloud_held) or value.get('pause_paid_ai') is not paid_paused:
            raise ValueError('invalid_controls')
    except (OSError, ValueError, AttributeError):
        raise ExtractionFailure('worker_controls_required','专用分析开关无效，保持停止。',attempts=0) from None


def save_state(path, state):
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('w',encoding='utf8') as output:
            json.dump(state,output,ensure_ascii=False,indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def single_worker(database):
    """A process crash releases this Windows mutex; a stale PID file is irrelevant."""
    if os.name!='nt':
        raise RuntimeError('Windows worker required')
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
    kernel.CreateMutexW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    identity=hashlib.sha256(str(database.resolve()).casefold().encode()).hexdigest()[:20]
    handle=kernel.CreateMutexW(None,False,'Local\\AttentionDesk-DeepSeek-'+identity)
    if not handle:
        raise RuntimeError('worker_lock_unavailable')
    already_running=ctypes.get_last_error()==183
    try:
        yield not already_running
    finally:
        kernel.CloseHandle(handle)


class TextAnalysisWorker:
    def __init__(self, directory, *, clock=utc_now, settings=None, transport=None, unlimited_test_tokens=False, automatic_mode=False):
        self.directory=Path(directory).resolve()
        database=self.directory/'personal.sqlite3'
        if not database.is_file():
            raise ValueError('existing_database_required')
        self.personal=PersonalStore(database)  # Never initialize or migrate production.
        self.clock=clock
        self.config=ConfigStore(self.directory/'wechat.yaml')
        self.policy=SchedulerPolicy(self.config.read().wechat,clock)
        self.queue=AnalysisQueue(self.personal,settings or AISettings(self.directory/'ai-settings.json'),
            self.policy,clock=clock,transport=transport,allow_vision=False)
        self.unlimited_test_tokens=unlimited_test_tokens
        self.automatic_mode=automatic_mode
        if automatic_mode:
            self.queue.automation_directory=self.directory
        self.recovered=False
        self.failures=0
        self.promotion_versions={}
        self.circuit_until=None
        try:
            previous=json.loads((self.directory/STATE).read_text(encoding='utf8'))
            self.circuit_until=datetime.fromisoformat(previous['circuit_until']) if previous.get('circuit_until') else None
        except (OSError,ValueError,KeyError,TypeError):
            pass

    def scoped_batches(self, grant):
        """Exclude the old backlog before taking the bounded processing slots."""
        selected=[]
        lower=datetime.fromisoformat(grant.not_before)
        with self.personal.connection() as connection:
            rows=connection.execute("SELECT b.*,ch.source_id AS grant_source_id,ch.binding_json AS grant_binding FROM wechat_batch b JOIN wechat_chat ch ON ch.id=b.chat_id WHERE ch.enabled=1 AND b.superseded_by IS NULL AND b.status NOT IN ('ignored','superseded','archived') ORDER BY b.created_at,b.id")
            for row in rows:
                batch=dict(row)
                if batch['chat_id'] not in grant.chat_watermarks:
                    continue
                if binding_digest(batch['grant_source_id'],batch['grant_binding'])!=grant.source_bindings[batch['chat_id']]:
                    continue
                messages=connection.execute("SELECT m.ingest_seq,m.captured_at,m.source_time,m.time_reliability FROM wechat_batch_message bm JOIN wechat_message m ON m.id=bm.message_id WHERE bm.batch_id=? AND bm.role='primary'",(batch['id'],)).fetchall()
                try:
                    eligible=bool(messages) and all(m['time_reliability']=='reliable'
                        and m['ingest_seq']>grant.chat_watermarks[batch['chat_id']]
                        and datetime.fromisoformat(m['source_time'])>=lower
                        and datetime.fromisoformat(m['captured_at'])>=lower for m in messages)
                    ready=datetime.fromisoformat(batch['ready_at'])<=self.clock()
                except (TypeError,ValueError):
                    eligible,ready=False,False
                if eligible and ready:
                    selected.append(batch)
        return selected

    def recover_scoped(self, job_ids):
        # The singleton and still-paused original app make these abandoned
        # leases ours to reconcile. Never silently retry an uncertain request.
        if not self.recovered:
            with self.personal.connection(write=True) as connection:
                for job_id in job_ids:
                    changed=connection.execute("UPDATE wechat_llm_attempt SET status='outcome_unknown',outcome_unknown=1,completed_at=? WHERE job_id=? AND status='running'",(self.clock().isoformat(),job_id))
                    if changed.rowcount:
                        connection.execute("UPDATE wechat_analysis_job SET status='outcome_unknown',error_code='outcome_unknown' WHERE id=?",(job_id,))
            self.recovered=True
        self.queue.recover_responses(job_ids=set(job_ids))

    async def cycle(self):
        checked_controls(self.directory/'recovery-controls.json',paid_paused=not self.automatic_mode,
                         cloud_held=None if self.automatic_mode else True)
        checked_controls(self.directory/CONTROLS,paid_paused=False)
        expected=str((self.directory/CONTROLS).resolve())
        if os.environ.get('ATTENTION_RECOVERY_CONTROL_PATH')!=expected:
            raise ExtractionFailure('worker_control_scope_mismatch','专用进程开关不匹配。',attempts=0)
        self.policy.config=self.config.read().wechat
        active=None
        if self.automatic_mode:
            from .automation import activation
            try:
                with activation(self.directory,permission='analyze',at=self.clock()) as active:
                    self.policy.config.budget.http_daily=active.value['http_daily']
                    self.policy.config.budget.tokens_daily=active.value['tokens_daily']
            except (OSError,ValueError,KeyError,TypeError):
                return {'state':'automation_approval_required','calls':0}
        if self.unlimited_test_tokens:
            self.policy.config.budget.tokens_daily=None
        permission=self.policy.authorize(operation='llm',maximum_seconds=60)
        if not permission.allowed:
            return {'state':permission.reason,'calls':0}
        if self.circuit_until and self.clock()<self.circuit_until:
            return {'state':'circuit_open','calls':0,'circuit_until':self.circuit_until.isoformat()}
        with leased_grant(self.personal) as grant:
            if grant.mode!='continuous':
                raise ExtractionFailure('continuous_grant_required','持续分析需要明确新增内容授权。',attempts=0)
            # Collection authorization remains necessary as well as the separate
            # DeepSeek grant. A removed/disabled configured group cannot run.
            configured={chat.id for chat in self.policy.config.priority_chats
                if chat.enabled and self.policy.config.allows(chat)}
            grant=grant.model_copy(update={'chat_watermarks':{
                key:value for key,value in grant.chat_watermarks.items() if key in configured}})
            batches=self.scoped_batches(grant)
        if active:
            with self.personal.connection() as c:
                batches=[b for b in batches if b['status']!='accepted' and active.contains(c,b)]
        scoped_jobs=[]
        with self.personal.connection() as connection:
            for batch in batches:
                scoped_jobs.extend(row[0] for row in connection.execute('SELECT id FROM wechat_analysis_job WHERE batch_id=?',(batch['id'],)))
        self.recover_scoped(scoped_jobs)
        from .recovery import eligible_job_ids
        result={'state':'ready','eligible_batches':len(batches),'enqueued':0,'parsed':0,'failed':0,'calls':0}
        processed=0
        for batch in batches:
            if processed>=self.policy.config.budget.batches_per_cycle:
                break
            with self.personal.connection() as connection:
                job=connection.execute('SELECT * FROM wechat_analysis_job WHERE batch_id=? AND snapshot_id=? ORDER BY created_at DESC LIMIT 1',(batch['id'],batch['snapshot_id'])).fetchone()
                if not job:
                    if self.queue.completed_job(connection,batch):
                        continue
                    review=connection.execute('SELECT status FROM raw_review WHERE raw_snapshot_id=?',(batch['snapshot_id'],)).fetchone()
                    if not review or review['status'] not in {'pending','archived'}:
                        continue
                    captured=connection.execute("SELECT MAX(m.captured_at) FROM wechat_batch_message bm JOIN wechat_message m ON m.id=bm.message_id WHERE bm.batch_id=? AND bm.role='primary'",(batch['id'],)).fetchone()[0]
                    # Keep unstarted history unchanged; the existing dispatch hold requires review.
                    if captured and self.clock()-datetime.fromisoformat(captured)>=timedelta(hours=24) and not active:
                        continue
            if not job:
                job_id=self.queue.enqueue(batch['id'],UserContextStore(self.directory).read())
                with self.personal.connection() as connection:
                    job=connection.execute('SELECT * FROM wechat_analysis_job WHERE id=?',(job_id,)).fetchone()
                if not job or job['batch_id']!=batch['id'] or job['snapshot_id']!=batch['snapshot_id']:
                    continue
                result['enqueued']+=1
            elif job['status'] in {'pending','retry','budget_deferred'}:
                job_id=job['id']
            else:
                continue
            with self.personal.connection() as connection:
                if job['status'] not in {'pending','retry','budget_deferred'} or not eligible_job_ids(connection,[job],at=self.clock(),limit=1,automatic=active):
                    continue
                before=connection.execute('SELECT count(*) FROM wechat_llm_attempt WHERE job_id=?',(job_id,)).fetchone()[0]
            success=await self.queue.run_one(job_id)
            with self.personal.connection() as connection:
                after=connection.execute('SELECT count(*) FROM wechat_llm_attempt WHERE job_id=?',(job_id,)).fetchone()[0]
                state=connection.execute('SELECT status,error_code FROM wechat_analysis_job WHERE id=?',(job_id,)).fetchone()
            processed+=int(success or after>before)
            result['calls']+=after-before
            result['parsed']+=int(success)
            result['failed']+=int(state['status'] in {'error','outcome_unknown'})
            if success:
                self.failures=0
            elif after>before:
                self.failures+=1
            if self.failures>=2:
                self.circuit_until=self.clock()+timedelta(minutes=15)
                result.update(state='circuit_open',circuit_until=self.circuit_until.isoformat())
                self.failures=0
                break
        if active:
            active.check(permission='analyze',at=self.clock())
            from .automation import promote
            with self.personal.connection() as c:
                completed=[r[0] for b in batches for r in c.execute("SELECT j.id FROM wechat_analysis_job j JOIN raw_review r ON r.raw_snapshot_id=? WHERE j.batch_id=? AND j.status='parsed'",(b['snapshot_id'],b['id'])) if
                    self.promotion_versions.get(r[0])!=c.execute('SELECT version FROM raw_review WHERE raw_snapshot_id=?',(b['snapshot_id'],)).fetchone()[0]]
            if active.value['permissions']['create_tasks']:
                result.update(promote(self.personal,active,job_ids=completed))
                with self.personal.connection() as c:
                    for job in completed:
                        version=c.execute('SELECT r.version FROM wechat_analysis_job j JOIN wechat_batch b ON b.id=j.batch_id JOIN raw_review r ON r.raw_snapshot_id=b.snapshot_id WHERE j.id=?',(job,)).fetchone()
                        self.promotion_versions[job]=version[0]
            with self.personal.connection() as c:
                pending=[dict(r) for b in batches for r in c.execute("SELECT * FROM wechat_analysis_job WHERE batch_id=? AND status IN ('pending','retry','budget_deferred')",(b['id'],))]
                runnable=eligible_job_ids(c,pending,at=self.clock(),limit=len(pending)+1,automatic=active)
            result['pending_runnable']=len(runnable)
            try:previous=json.loads((self.directory/STATE).read_text('utf-8'))
            except (OSError,ValueError):previous={}
            result['stalled_cycles']=(previous.get('stalled_cycles',0)+1) if runnable and not result['calls'] else 0
            result['alert_required']=bool(result['stalled_cycles']>=2 or result['failed'])
        return result


async def run(worker, *, once=False, interval=60):
    while True:
        try:
            result=await worker.cycle()
        except ExtractionFailure as exc:
            result={'state':exc.code,'calls':0}
        except Exception as exc:
            # Do not log exception arguments containing source or credentials.
            frame=traceback.extract_tb(exc.__traceback__)[-1]
            result={'state':'local_worker_error','calls':0,'error_type':type(exc).__name__,
                    'error_location':Path(frame.filename).name+':'+str(frame.lineno)}
        from ..notification_supervisor import process_token
        state={'pid':os.getpid(),'process_token':process_token(),'last_tick':worker.clock().isoformat(),
            'service':'wechat-personal-actions-v2' if worker.automatic_mode else 'legacy-analysis',
            'global_holds_preserved':True,'cloud_enabled':False,**result}
        save_state(worker.directory/STATE,state)
        LOG.info('cycle %s',json.dumps(state,ensure_ascii=True))
        if once:
            return state
        await asyncio.sleep(interval)


def launch_if_authorized(directory, *, automatic=False):
    """Used by the existing desktop entrypoint on future user logins.

    Missing/revoked grant is a no-op. Never creates controls or grants, and never
    changes the desktop process's environment or releases its global holds.
    """
    directory=Path(directory).resolve()
    try:
        checked_controls(directory/'recovery-controls.json',paid_paused=not automatic,cloud_held=None if automatic else True)
        checked_controls(directory/CONTROLS,paid_paused=False)
        with leased_grant(PersonalStore(directory/'personal.sqlite3')) as grant:
            if grant.mode!='continuous':
                return None
    except ExtractionFailure:
        return None
    if os.name!='nt':
        return None
    from ..notification_supervisor import adopt
    prior=adopt(directory/STATE,'wechat-personal-actions-v2')
    if prior:return prior
    # Uncapped tokens are an explicit testing setting, not a default for every
    # user or grant. HTTP rate, batch, retry and circuit limits remain intact.
    controls=json.loads((directory/CONTROLS).read_text(encoding='utf8'))
    command=[sys.executable,'-m','server.wechat_collect.deepseek_worker','--data-directory',str(directory)]
    if controls.get('unlimited_test_tokens') is True:
        command.append('--unlimited-test-tokens')
    return subprocess.Popen(command,
        cwd=Path(__file__).resolve().parents[2],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,close_fds=True,
        # DETACHED_PROCESS would make Windows ignore CREATE_NO_WINDOW.
        creationflags=subprocess.CREATE_NO_WINDOW)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-directory',type=Path,required=True)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--unlimited-test-tokens',action='store_true')
    args=parser.parse_args()
    directory=args.data_directory.resolve()
    checked_controls(directory/'recovery-controls.json',paid_paused=False,cloud_held=None)
    checked_controls(directory/CONTROLS,paid_paused=False)
    os.environ['ATTENTION_RECOVERY_CONTROL_PATH']=str(directory/CONTROLS)
    with single_worker(directory/'personal.sqlite3') as acquired:
        if not acquired:
            return
        logs=directory/'logs'
        logs.mkdir(exist_ok=True)
        handler=RotatingFileHandler(logs/'deepseek-analysis.log',maxBytes=1_000_000,backupCount=2,encoding='utf8')
        logging.basicConfig(level=logging.INFO,handlers=[handler],format='%(asctime)s %(name)s %(message)s')
        worker=TextAnalysisWorker(directory,unlimited_test_tokens=args.unlimited_test_tokens,automatic_mode=True)
        worker.queue.exclusive_worker=True  # Only after the real singleton was acquired above.
        asyncio.run(run(worker,once=args.once))


def launch_automatic_if_authorized(directory):
    from .automation import activation
    try:
        with activation(directory,permission='analyze'):pass
    except (OSError,ValueError,KeyError,TypeError):return None
    return launch_if_authorized(directory,automatic=True)


if __name__=='__main__':
    main()
