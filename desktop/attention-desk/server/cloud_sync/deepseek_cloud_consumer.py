"""Bounded consumer of completed analysis, without Collector or UI integration.

Construction never creates permission or state. An owner must initialize the
isolated runtime explicitly AFTER independently approving the cloud scope.
All content still goes through ReviewedDeepSeekDispatch's protected transport.
"""
from contextlib import contextmanager,closing
from datetime import timedelta
from pathlib import Path
import sqlite3

from .deepseek_cloud_scope import ScopedCloudPublication,instant,validate_future_scope
from .deepseek_preview import build_deepseek_preview
from .wechat_dispatch_authority import DispatchDenied
from .wechat_incremental_journal import freeze_bundle
from ..extraction_errors import ExtractionFailure


class ConsumerState:
    """Local scheduling metadata only; no plaintext, token, or production writes."""
    def __init__(self,path,scope):
        self.path=Path(path).resolve()
        self.scope=scope

    def initialize(self):
        # Fail on any prior/partial activation. Never reset counters on restart.
        with self.path.open('xb'):pass
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('''
                CREATE TABLE runtime(binding TEXT NOT NULL,cursor INTEGER NOT NULL,
                    failures INTEGER NOT NULL,cooldown REAL NOT NULL);
                CREATE TABLE jobs(job_id TEXT PRIMARY KEY,receipt_hash TEXT,bundle_hash TEXT,
                    state TEXT NOT NULL,checked REAL NOT NULL);
                CREATE TABLE checks(at REAL NOT NULL);
            ''')
            db.execute('INSERT INTO runtime VALUES (?,0,0,0)',(self.scope.binding(),))
            if self.scope.policy=='wechat-personal-actions-v2':
                db.execute('CREATE TABLE deferred_jobs(job_id TEXT PRIMARY KEY,review_version INTEGER NOT NULL)')
            db.commit()

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=.1)
        db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute('SELECT * FROM runtime').fetchall()
            if len(rows)!=1 or rows[0]['binding']!=self.scope.binding():
                raise DispatchDenied('runtime_scope_changed')
            yield db
            db.commit()
        finally:db.close()

    def reserve_status(self,at):
        """At most one metadata GET per rolling 15 minutes, 30 per 24 hours.

        Reserve before connecting, including failed/unknown GETs. No cache is
        carried across processes; a restart waits for the next eligible slot.
        """
        stamp=instant(at).timestamp()
        from ..wechat_collect.automation import POLICY as ACTION_POLICY
        automatic=self.scope.policy==ACTION_POLICY
        with self.connection() as db:
            latest=db.execute('SELECT MAX(at) FROM checks').fetchone()[0]
            if latest is not None and stamp-latest<(60 if automatic else 900):return False
            if db.execute('SELECT COUNT(*) FROM checks WHERE at>?',(stamp-86400,)).fetchone()[0]>=(max(1440,self.scope.daily_limit*2) if automatic else 30):return False
            db.execute('INSERT INTO checks VALUES (?)',(stamp,))
            return True

    def result(self,job_id,state,at):
        with self.connection() as db:
            db.execute('UPDATE jobs SET state=?,checked=? WHERE job_id=?',(state,instant(at).timestamp(),job_id))
            if state=='needs_reconciliation':
                row=db.execute('SELECT failures FROM runtime').fetchone()
                failures=row['failures']+1
                cooldown=instant(at+timedelta(minutes=15)).timestamp() if failures>=2 else 0
                db.execute('UPDATE runtime SET failures=?,cooldown=?',(failures,cooldown))
            elif state in {'acknowledged','fenced_absent'}:
                db.execute('UPDATE runtime SET failures=0,cooldown=0')


TERMINAL={'acknowledged','held','blocked','credential_revoked','delivery_attempt_limit_reached'}
HALT={'credential_revoked','grant_not_current','cloud_approval_changed','cloud_evidence_changed',
      'cloud_scope_or_session_changed','cloud_authorization_inactive','global_holds_required'}


def verify_status(value,scope,automation_hash=None):
    expected={'protocol':'wechat-receipt-fence-v1','workspace_id':scope.workspace_id,
              'processor_id':scope.processor_id,'epoch':scope.epoch,'generation':scope.generation}
    if scope.policy=='wechat-personal-actions-v2':
        expected.update(protocol=scope.policy,activation_hash=automation_hash,not_before=scope.not_before,
                        source_ids=[s.source_id for s in scope.sources])
    if type(value) is not dict or value!=expected or type(value.get('generation')) is not int:
        raise DispatchDenied('publisher_identity_changed')


class DeepSeekCloudConsumer:
    """One owner, one bounded tick. The caller schedules ticks; no implicit loop.

    status_read(config) may only perform the fixed metadata GET; it must never
    publish or mutate ownership. sender_factory(current) must bind the existing
    protected dispatch to local.snapshot. A fresh status is advisory preflight;
    the server's atomic epoch/fence check is the publication authority.
    """
    def __init__(self,*,scope,state,local,authority,journal,status_read,sender_factory,clock,release_current):
        self.scope,self.state,self.local=scope,state,local
        self.authority,self.journal=authority,journal
        self.status_read,self.sender_factory=status_read,sender_factory
        self.clock,self.release_current=clock,release_current
        self.status_at=None

    def current(self,publication=None):
        scope=self.scope
        if publication is not None and (type(publication) is not ScopedCloudPublication or publication.scope!=scope):
            raise DispatchDenied('cloud_scope_or_session_changed')
        if not instant(scope.not_before)<=instant(self.clock())<instant(scope.expires_at):
            raise DispatchDenied('cloud_authorization_inactive')
        if self.release_current() is not True:raise DispatchDenied('release_changed')
        # A read-only check, never reserve dummy content or consume an attempt.
        with closing(sqlite3.connect(self.authority.path.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            self.authority._grant(db,scope.approval_id,scope.binding())
        if publication is not None:
            if self.status_at is None or not 0<=(instant(self.clock())-self.status_at).total_seconds()<60:
                raise DispatchDenied('publisher_status_stale')
        return True

    def discover(self):
        """Scan up to 100 completed jobs per tick; wrap to catch late completions.

        The read connection cannot change the source database. Every eligible
        preview is rebuilt under the source lock at the actual send boundary.
        """
        self.current()
        with self.local.files(self.scope) as (deepseek,_,_):
            with self.state.connection() as state:
                cursor=state.execute('SELECT cursor FROM runtime').fetchone()[0]
                path=self.local.directory/'personal.sqlite3'
                source=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
                source.row_factory=sqlite3.Row
                try:
                    source.execute('PRAGMA query_only=ON');source.execute('BEGIN')
                    automatic=self.scope.policy=='wechat-personal-actions-v2'
                    if automatic:
                        source.execute('ATTACH DATABASE ? AS notification_runtime',(self.state.path.resolve().as_uri()+'?mode=ro',))
                        rows=source.execute('''SELECT j.rowid AS position,j.id,r.version AS review_version FROM wechat_analysis_job j
                            JOIN wechat_batch b ON b.id=j.batch_id JOIN raw_item raw ON raw.id=b.raw_item_id
                            JOIN raw_review r ON r.raw_snapshot_id=b.snapshot_id
                            LEFT JOIN notification_runtime.jobs known ON known.job_id=j.id
                            LEFT JOIN notification_runtime.deferred_jobs waiting ON waiting.job_id=j.id AND waiting.review_version=r.version
                            WHERE j.status='parsed' AND j.rowid>? AND julianday(raw.created_at)>=julianday(?)
                              AND known.job_id IS NULL AND waiting.job_id IS NULL ORDER BY j.rowid LIMIT 100''',(cursor,self.scope.not_before)).fetchall()
                    else:
                        rows=source.execute('''SELECT j.rowid AS position,j.id FROM wechat_analysis_job j
                        JOIN wechat_batch b ON b.id=j.batch_id JOIN raw_item r ON r.id=b.raw_item_id
                        WHERE j.status='parsed' AND j.rowid>? AND julianday(r.created_at)>=julianday(?)
                        ORDER BY j.rowid LIMIT 100''',(cursor,self.scope.not_before)).fetchall()
                    count=0
                    for row in rows:
                        if state.execute('SELECT 1 FROM jobs WHERE job_id=?',(row['id'],)).fetchone():continue
                        try:
                            preview=self.local.build_preview(source,deepseek,row['id'],self.scope) if hasattr(self.local,'build_preview') else build_deepseek_preview(source,deepseek,row['id'])
                            validate_future_scope(source,self.scope,preview)
                            frozen=freeze_bundle(preview['bundle'],self.scope.binding())
                            receipt=preview['analysis_receipt_sha256'];bundle=frozen['bundle_hash'];outcome='ready'
                        except (ValueError,KeyError,TypeError,ExtractionFailure) as error:
                            from ..wechat_collect.automation import POLICY as ACTION_POLICY
                            if self.scope.policy==ACTION_POLICY and str(error)=='no_automatic_personal_tasks':
                                state.execute('INSERT OR REPLACE INTO deferred_jobs VALUES (?,?)',(row['id'],row['review_version']))
                                continue
                            # Rejected content stays local. No error strings or text are persisted.
                            receipt=bundle=None;outcome='held'
                        state.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',
                            (row['id'],receipt,bundle,outcome,instant(self.clock()).timestamp()))
                        count+=outcome=='ready'
                    state.execute('UPDATE runtime SET cursor=?',(rows[-1]['position'] if len(rows)==100 else 0,))
                    return count
                finally:source.rollback();source.close()

    async def tick(self):
        self.journal._require_owner()
        self.current()
        discovered=self.discover()
        with self.state.connection() as db:
            runtime=db.execute('SELECT * FROM runtime').fetchone()
            if runtime['cooldown']>instant(self.clock()).timestamp():return {'state':'circuit_open','discovered':discovered}
            placeholders=','.join('?' for _ in TERMINAL)
            jobs=db.execute(f'SELECT * FROM jobs WHERE state NOT IN ({placeholders}) ORDER BY checked,job_id LIMIT ?',
                            (*sorted(TERMINAL),self.scope.cycle_limit)).fetchall()
        if not jobs:return {'state':'idle','discovered':discovered,'requests':0}
        # If no publish/reconcile allowance remains, do not spend a metadata GET.
        from zoneinfo import ZoneInfo
        at=instant(self.clock())
        seconds=60 if self.scope.policy=='wechat-personal-actions-v2' else 900
        self.journal._check_quota(self.journal._read(),at.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),
            int(at.timestamp()//seconds),self.scope.daily_limit,self.scope.cycle_limit,cycle_seconds=seconds)
        if not self.state.reserve_status(at):return {'state':'status_backoff','discovered':discovered}
        with self.local.files(self.scope) as (_,__,config):
            if (not config or config.get('enabled') is not True or config.get('endpoint')!=self.scope.destination
                or config.get('workspace_id')!=self.scope.workspace_id or config.get('processor_id')!=self.scope.processor_id):
                raise DispatchDenied('cloud_connection_changed')
            config=dict(config)
        # No file lease or SQLite write lock is held across a remote await.
        value=await self.status_read(config)
        verify_status(value,self.scope,getattr(self.local,'automation_hash',None))
        self.status_at=instant(self.clock())
        sender=self.sender_factory(self.current)
        results=[]
        for job in jobs:
            publication=ScopedCloudPublication(self.scope,job['job_id'],job['receipt_hash'],job['bundle_hash'])
            result=await sender.step(publication)
            state=result['state']
            self.state.result(job['job_id'],state,self.clock())
            results.append(state)
            if state in HALT or state in {'daily_limit_reached','cycle_limit_reached'}:break
            with self.state.connection() as db:
                if db.execute('SELECT cooldown FROM runtime').fetchone()[0]>instant(self.clock()).timestamp():break
        return {'state':'checked','discovered':discovered,'results':results}
