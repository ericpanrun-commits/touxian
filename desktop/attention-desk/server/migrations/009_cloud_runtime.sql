-- Durable identity, scheduler observability and bounded backup bookkeeping.
CREATE TABLE production_identity (
    id INTEGER PRIMARY KEY CHECK(id=1),
    database_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT INTO production_identity VALUES(1,lower(hex(randomblob(16))),strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
CREATE TABLE worker_runtime (
    name TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_scheduler_tick TEXT,
    last_backup_at TEXT,
    last_backup_error TEXT
);
