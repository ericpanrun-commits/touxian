"""SQLite authority lease, safe backups and passive worker status."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .personal_store import SCHEMA_VERSION, now

LOG = logging.getLogger(__name__)


def _mount_filesystem(root):
    """A mount alone is insufficient: /data must not be tmpfs or overlay."""
    try:
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            mount, details = line.split(' - ', 1)
            location = mount.split()[4]
            for encoded, decoded in [('\\040', ' '), ('\\011', '\t'), ('\\012', '\n'), ('\\134', '\\')]:
                location = location.replace(encoded, decoded)
            if Path(location).resolve() == root:
                return details.split()[0]
    except (OSError, ValueError, IndexError):
        pass
    return None


def validate_storage(runtime, *, is_mount=os.path.ismount, filesystem=_mount_filesystem):
    root = Path(runtime.data_root).resolve()
    database = Path(runtime.database_path).resolve()
    if (not runtime.cloud_production or root == Path(root.anchor) or str(root) in {'/app', '/tmp'}
            or not database.is_relative_to(root) or database == root or not is_mount(root)):
        raise ValueError('生产数据库必须位于已验证的独立持久挂载目录中。')
    if os.name != 'nt' and filesystem(root) not in {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs'}:
        raise ValueError('生产数据库挂载必须使用持久磁盘文件系统，拒绝内存盘或容器临时层。')
    return root, database


class ProcessLock:
    """Second protection on the SAME volume; never a substitute for a DB lease."""
    def __init__(self, root):
        self.path = Path(root) / '.worker.lock'
        self.file = None

    def acquire(self):
        self.file = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b'0'); self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close(); self.file = None
            raise RuntimeError('同一生产卷已有后台进程运行，拒绝第二个实例。') from None

    def close(self):
        if self.file:
            self.file.close(); self.file = None


class CloudStore:
    def __init__(self, personal, runtime):
        self.personal, self.runtime = personal, runtime
        self.owner = runtime.instance_id + ':' + uuid4().hex

    def initialize(self):
        with self.personal.connection(write=True) as db:
            identity = db.execute('SELECT database_id FROM production_identity WHERE id=1').fetchone()[0]
            if self.runtime.expected_database_id and identity != self.runtime.expected_database_id:
                raise ValueError('生产数据库身份不匹配，拒绝启动。')
            db.execute("""INSERT INTO worker_runtime(name,instance_id,environment,started_at)
                VALUES('official',?,?,?) ON CONFLICT(name) DO UPDATE SET instance_id=excluded.instance_id,
                environment=excluded.environment,started_at=excluded.started_at,last_scheduler_tick=NULL""",
                (self.runtime.instance_id,self.runtime.environment,now()))

    def scheduler_claim(self, *, at=None):
        at = at or datetime.now(timezone.utc)
        instant, expiry = at.isoformat(timespec='microseconds'), (at+timedelta(seconds=90)).isoformat(timespec='microseconds')
        with self.personal.connection(write=True) as db:
            row = db.execute("SELECT * FROM official_job_lease WHERE name='scheduler'").fetchone()
            if row and row['owner'] != self.owner and row['expires_at'] > instant:
                return False
            db.execute("""INSERT INTO official_job_lease VALUES('scheduler',?,?,?) ON CONFLICT(name) DO UPDATE
                SET owner=excluded.owner,expires_at=excluded.expires_at,heartbeat_at=excluded.heartbeat_at""", (self.owner,expiry,instant))
            db.execute("UPDATE worker_runtime SET last_scheduler_tick=?,instance_id=? WHERE name='official'",(instant,self.runtime.instance_id))
        return True

    def release(self):
        with self.personal.connection(write=True) as db:
            db.execute("DELETE FROM official_job_lease WHERE name='scheduler' AND owner=?",(self.owner,))

    def maintenance(self):
        if not self.runtime.storage_verified:
            return
        with self.personal.connection() as db:
            row = db.execute("SELECT last_backup_at FROM worker_runtime WHERE name='official'").fetchone()
        if row and row[0] and row[0][:10] == now()[:10]:
            return
        try:
            safe_backup(self.personal.path, Path(self.runtime.data_root)/'backups', keep=self.runtime.backup_keep)
            with self.personal.connection(write=True) as db:
                db.execute("UPDATE worker_runtime SET last_backup_at=?,last_backup_error=NULL WHERE name='official'",(now(),))
        except Exception:
            with self.personal.connection(write=True) as db:
                # Mark this day's attempt to avoid a backup/error flood every 30 seconds.
                db.execute("UPDATE worker_runtime SET last_backup_at=?,last_backup_error='backup_failed' WHERE name='official'",(now(),))
            LOG.error('backup_failed instance=%s',self.runtime.instance_id)

    def status(self):
        at = datetime.now(timezone.utc)
        with self.personal.connection() as db:
            db.execute('SELECT 1').fetchone()
            version = db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]
            row = db.execute("SELECT * FROM worker_runtime WHERE name='official'").fetchone()
            row = dict(row) if row else {}
            times = dict(db.execute('''SELECT MAX(started_at) AS last_sync_started,MAX(finished_at) AS last_sync_finished,
                MAX(CASE WHEN status='ok' THEN finished_at END) AS last_successful_source_sync FROM official_run''').fetchone())
            bark = dict(db.execute("SELECT last_success_at,last_failure_at FROM notification_preferences WHERE channel='bark'").fetchone())
        tick = row.get('last_scheduler_tick')
        stale = self.runtime.automatic_sync_allowed and (not tick or (at-datetime.fromisoformat(tick)).total_seconds() > max(120,2*self.runtime.poll_seconds))
        return {**self.runtime.public(),'status':'degraded' if stale else 'online',
                'scheduler':'stale' if stale else 'healthy' if self.runtime.automatic_sync_allowed and tick else 'disabled',
                'last_scheduler_tick':tick,**times,'last_bark_success':bark['last_success_at'],
                'last_bark_failure':bark['last_failure_at'],'last_backup_at':row.get('last_backup_at'),
                'backup_status':'failed' if row.get('last_backup_error') else 'available' if row.get('last_backup_at') else 'pending',
                'migration_complete':version==SCHEMA_VERSION,
                'database_bytes':self.personal.path.stat().st_size,
                'disk_free_bytes':shutil.disk_usage(self.personal.path.parent).free}


def safe_backup(database, destination, *, keep=7):
    database, destination = Path(database).resolve(), Path(destination).resolve()
    if not database.is_file() or not 1 <= keep <= 14:
        raise ValueError('需要现有数据库和 1–14 的备份保留数量。')
    destination.mkdir(parents=True, exist_ok=True)
    name = 'production-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3'
    target = destination/name
    with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro',uri=True,timeout=5)) as source, closing(sqlite3.connect(target)) as backup:
        source.backup(backup)
        if backup.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or backup.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('数据库备份校验失败。')
    if os.name != 'nt':
        target.chmod(0o600)
    # Exact pattern, resolved containment, regular files only; no recursive delete.
    for old in sorted(destination.glob('production-*.sqlite3'),reverse=True)[keep:]:
        if old.resolve().parent==destination and old.is_file() and not old.is_symlink():
            old.unlink()
    return target


def database_manifest(path):
    with closing(sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro',uri=True,timeout=5)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('数据库完整性验证失败。')
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        result={'counts':{},'table_hashes':{}}
        for table in tables:
            rows=db.execute('SELECT * FROM "'+table.replace('"','""')+'"').fetchall()
            encoded=sorted(json.dumps(row,ensure_ascii=False,sort_keys=True,default=lambda value:value.hex() if isinstance(value,bytes) else str(value)) for row in rows)
            result['counts'][table]=len(rows)
            result['table_hashes'][table]=hashlib.sha256('\n'.join(encoded).encode()).hexdigest()
        identity=db.execute('SELECT database_id FROM production_identity WHERE id=1').fetchone() if 'production_identity' in tables else None
        result['database_id']=identity[0] if identity else None
        row=db.execute("SELECT activation_at FROM notification_preferences WHERE channel='bark'").fetchone()
        result['bark_activation_at']=row[0] if row else None
        return result


def verify_migration(before, after):
    """Compare every pre-existing business column, including read/save/dedup state."""
    with closing(sqlite3.connect(f'{Path(before).resolve().as_uri()}?mode=ro',uri=True)) as source, closing(sqlite3.connect(f'{Path(after).resolve().as_uri()}?mode=ro',uri=True)) as target:
        for (table,) in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_migrations'"):
            quoted = '"' + table.replace('"','""') + '"'
            columns = [row[1] for row in source.execute('PRAGMA table_info('+quoted+')')]
            selection = ','.join('"'+column.replace('"','""')+'"' for column in columns)
            query = 'SELECT '+selection+' FROM '+quoted
            # SQLite values have stable repr for this same-DB migration comparison.
            if sorted(map(repr,source.execute(query).fetchall())) != sorted(map(repr,target.execute(query).fetchall())):
                raise ValueError('迁移改变了已有业务记录：'+table)
    return True
