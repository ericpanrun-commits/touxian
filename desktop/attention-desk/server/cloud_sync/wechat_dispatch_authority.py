"""Durable send/revoke ordering. Not registered with the running worker.

This owns imported grants; it does not infer human consent. Import requires an
independent verifier of the exact descriptor. All revocations of imported grants
must use revoke(). An external boolean is not a substitute for this authority.

A prepared transport must do connection setup BEFORE handoff(), then accept
immutable bytes synchronously without waiting for a remote response. Invocation
of that adapter is the conservative irreversible boundary: even an exception
can mean partial transmission. HTTP clients with hidden awaits/retries cannot
be passed as adapters. Integration must supply an actual boundary adapter.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import sqlite3
import time


class DispatchDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class Grant:
    identity: str
    binding: str
    expires: float
    once: bool

    def __post_init__(self):
        if (not isinstance(self.identity, str) or not 1 <= len(self.identity) <= 128
                or not isinstance(self.binding, str) or len(self.binding) != 64
                or any(c not in '0123456789abcdef' for c in self.binding)
                or type(self.expires) not in (int, float) or not math.isfinite(self.expires)
                or type(self.once) is not bool):
            raise ValueError('invalid_grant')


class DispatchAuthority:
    """SQLite orders competing processes, including revocation during reconnect.

Reservation commits before any possible network handoff. A crash/exception
never refunds a single-use grant. A reserved attempt is NOT proof of delivery
or absence; the existing reconciliation journal remains responsible for that.
"""
    def __init__(self, path, *, clock=time.time):
        self.path = Path(path).resolve()
        self.clock = clock

    def initialize(self):
        # Explicit initialization only, in the caller's isolated directory.
        with sqlite3.connect(self.path) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS send_grant (
                    id TEXT PRIMARY KEY, binding TEXT NOT NULL,
                    expires REAL NOT NULL, once_only INTEGER NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS send_attempt (
                    id TEXT PRIMARY KEY, grant_id TEXT NOT NULL,
                    digest TEXT NOT NULL, state TEXT NOT NULL,
                    FOREIGN KEY(grant_id) REFERENCES send_grant(id)
                );
            ''')

    @contextmanager
    def _transaction(self):
        # Do not accidentally create a new authority after deletion/path change.
        db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True,
                             timeout=0.1, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def import_verified(self, grant, verifier):
        if type(grant) is not Grant or verifier(grant) is not True:
            raise DispatchDenied('independent_approval_required')
        with self._transaction() as db:
            if self.clock() >= grant.expires:
                raise DispatchDenied('grant_expired')
            # Never overwrite a revoked/consumed grant or reuse its identity.
            db.execute('INSERT INTO send_grant(id,binding,expires,once_only) VALUES (?,?,?,?)',
                       (grant.identity, grant.binding, grant.expires, int(grant.once)))

    def revoke(self, identity):
        with self._transaction() as db:
            changed = db.execute('UPDATE send_grant SET revoked=1 WHERE id=?', (identity,))
            if changed.rowcount != 1:
                raise DispatchDenied('unknown_grant')
        # A lock timeout is an error, NEVER a successful revocation. The caller
        # must keep a stop pending until this transaction commits.

    def _grant(self, db, identity, binding):
        row = db.execute('SELECT * FROM send_grant WHERE id=?', (identity,)).fetchone()
        if (row is None or row['binding'] != binding or row['revoked']
                or self.clock() >= row['expires']):
            raise DispatchDenied('grant_not_current')
        return row

    def reserve(self, identity, binding, attempt, payload):
        if type(payload) is not bytes or not payload or len(payload) > 1024 * 1024:
            raise DispatchDenied('invalid_payload')
        if not isinstance(attempt, str) or not 1 <= len(attempt) <= 128:
            raise DispatchDenied('invalid_attempt')
        digest = hashlib.sha256(payload).hexdigest()
        with self._transaction() as db:
            row = self._grant(db, identity, binding)
            if row['once_only'] and row['consumed']:
                raise DispatchDenied('grant_consumed')
            db.execute('INSERT INTO send_attempt VALUES (?,?,?,?)',
                       (attempt, identity, digest, 'reserved'))
            if row['once_only']:
                db.execute('UPDATE send_grant SET consumed=1 WHERE id=?', (identity,))
        return digest

    def handoff(self, identity, binding, attempt, payload, *, protected_snapshot, accept):
        """One synchronous transfer, with content and authorization held together.

        protected_snapshot() must yield exact immutable bytes while enforcing
        exclusion against ALL content writers (e.g. locked_source below plus
        leases on file-owned settings). It must not merely return a checked copy.
        accept() must be a preconnected, nonretrying transport boundary, not an
        async request function. No remote response is awaited in this method.
        """
        if type(payload) is not bytes:
            raise DispatchDenied('invalid_payload')
        digest = hashlib.sha256(payload).hexdigest()
        # Commit dispatch consumption before invoking any transport. This also
        # prevents concurrent/restarted callers from replaying a reserved ID.
        with self._transaction() as db:
            self._grant(db, identity, binding)
            row = db.execute('SELECT * FROM send_attempt WHERE id=?', (attempt,)).fetchone()
            if (row is None or row['grant_id'] != identity or row['digest'] != digest
                    or row['state'] != 'reserved'):
                raise DispatchDenied('attempt_not_available')
            db.execute("UPDATE send_attempt SET state='unconfirmed' WHERE id=?", (attempt,))
        # Revocation in the gap above wins this second transaction. The consumed
        # attempt is never automatically refunded, even if no bytes were sent.
        with self._transaction() as db:
            self._grant(db, identity, binding)
            deadline = time.monotonic() + 0.5
            with protected_snapshot() as current:
                if type(current) is not bytes or current != payload:
                    raise DispatchDenied('content_changed')
                self._grant(db, identity, binding)  # expiry may cross during build
                if time.monotonic() >= deadline:
                    raise DispatchDenied('handoff_deadline')
                accept(payload)

    def attempt_state(self, attempt):
        with self._transaction() as db:
            row = db.execute('SELECT state FROM send_attempt WHERE id=?', (attempt,)).fetchone()
            return row['state'] if row else None


@contextmanager
def locked_source(path, build):
    """Block independent SQLite writers without changing Collector or schema.

    Only use for the bounded handoff interval, after connection preparation.
    build must read all source/review data via this connection. File-owned
    context/settings need their own lease; this does not protect those files.
    """
    path = Path(path).resolve()
    db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True,
                         timeout=0.1, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('PRAGMA query_only=ON')
        yield build(db)
    finally:
        db.rollback()
        db.close()
