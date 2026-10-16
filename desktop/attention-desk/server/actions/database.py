"""Small thread-owned PostgreSQL connections, TLS and bounded transactions."""
from contextlib import contextmanager
from pathlib import Path
import threading

SUPABASE_CA = Path(__file__).with_name('certs') / 'supabase-prod-ca-2021.crt'


class PostgresDatabase:
    def __init__(self, dsn, *, connect=None):
        self._dsn, self._connect = dsn, connect
        self._local = threading.local()
        self._connections = []
        self._guard = threading.Lock()

    @contextmanager
    def connection(self):
        if not hasattr(self._local,'connection') or self._local.connection.closed:
            import psycopg
            from psycopg.rows import dict_row
            connect = self._connect or psycopg.connect
            db = connect(self._dsn,autocommit=True,connect_timeout=8,prepare_threshold=None,
                         sslmode='verify-full',sslrootcert=str(SUPABASE_CA),row_factory=dict_row,
                         application_name='zqu-actions-worker')
            self._local.connection = db
            with self._guard:self._connections.append(db)
        db = self._local.connection
        with db.transaction():
            db.execute("SET LOCAL statement_timeout='8s'")
            db.execute("SET LOCAL lock_timeout='5s'")
            yield db

    def close(self):
        for connection in self._connections:
            connection.close()
        self._connections.clear()
