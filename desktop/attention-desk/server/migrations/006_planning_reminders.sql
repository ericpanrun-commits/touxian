-- Rebuilds run inside the existing atomic migration transaction. FK validation
-- happens before commit; child references and all legacy values are retained.
CREATE TABLE deadline_v6 (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES event(id),
    label TEXT NOT NULL,
    value TEXT NOT NULL,
    precision TEXT NOT NULL CHECK(precision IN ('date','minute','range','unknown')),
    timezone TEXT,
    original_text TEXT NOT NULL DEFAULT '',
    confirmed_by_user INTEGER NOT NULL CHECK(confirmed_by_user IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    end_value TEXT,
    range_precision TEXT CHECK(range_precision IN ('date','minute')),
    uncertain INTEGER NOT NULL DEFAULT 0 CHECK(uncertain IN (0,1)),
    fold INTEGER CHECK(fold IN (0,1)),
    source_snapshot_id TEXT REFERENCES raw_snapshot(id),
    UNIQUE(id,event_id)
);
INSERT INTO deadline_v6 (id,event_id,label,value,precision,timezone,original_text,confirmed_by_user,created_at,updated_at)
SELECT id,event_id,label,value,precision,timezone,original_text,confirmed_by_user,created_at,updated_at FROM deadline;
DROP TABLE deadline;
ALTER TABLE deadline_v6 RENAME TO deadline;
CREATE INDEX deadline_event ON deadline(event_id);
CREATE INDEX deadline_value ON deadline(value,event_id);
CREATE UNIQUE INDEX event_item_identity ON event(id,raw_item_id);
CREATE TABLE evidence_v6 (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    raw_snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id),
    field_path TEXT NOT NULL,
    quote TEXT NOT NULL,
    start_offset INTEGER NOT NULL CHECK(start_offset>=0),
    end_offset INTEGER NOT NULL CHECK(end_offset>start_offset),
    created_at TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'text',
    block_id TEXT,
    attachment_id TEXT REFERENCES attachment(id),
    page INTEGER,
    section TEXT,
    bounding_box_json TEXT,
    raw_item_id TEXT NOT NULL,
    FOREIGN KEY(event_id,raw_item_id) REFERENCES event(id,raw_item_id),
    FOREIGN KEY(raw_snapshot_id,raw_item_id) REFERENCES raw_snapshot(id,raw_item_id),
    FOREIGN KEY(raw_snapshot_id,block_id) REFERENCES raw_source_block(raw_snapshot_id,block_id)
);
INSERT INTO evidence_v6
SELECT e.*,v.raw_item_id FROM evidence e JOIN event v ON v.id=e.event_id;
DROP TABLE evidence;
ALTER TABLE evidence_v6 RENAME TO evidence;
CREATE INDEX evidence_event ON evidence(event_id,field_path);
CREATE UNIQUE INDEX evidence_legacy_span ON evidence(event_id,field_path,raw_snapshot_id,start_offset,end_offset) WHERE block_id IS NULL;
CREATE UNIQUE INDEX evidence_block_span ON evidence(event_id,field_path,raw_snapshot_id,block_id,start_offset,end_offset) WHERE block_id IS NOT NULL;
CREATE TABLE reminder_settings (
    id INTEGER PRIMARY KEY CHECK(id=1),
    version INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    date_offsets_json TEXT NOT NULL DEFAULT '[3,1,0]',
    minute_offsets_json TEXT NOT NULL DEFAULT '[24,2]',
    daily_time TEXT NOT NULL DEFAULT '09:00',
    privacy TEXT NOT NULL DEFAULT 'title' CHECK(privacy IN ('full','title','hidden')),
    grace_hours INTEGER NOT NULL DEFAULT 6 CHECK(grace_hours BETWEEN 0 AND 24),
    updated_at TEXT NOT NULL
);
INSERT INTO reminder_settings(id,updated_at) VALUES(1,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
CREATE TABLE reminder (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES event(id),
    deadline_id TEXT REFERENCES deadline(id),
    task_id TEXT REFERENCES task(id),
    reminder_type TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('scheduled','dispatching','fired','dismissed','snoozed','cancelled','failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    fired_at TEXT,
    dismissed_at TEXT,
    snoozed_until TEXT,
    dedupe_key TEXT NOT NULL UNIQUE,
    policy_version INTEGER NOT NULL,
    fact_key TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    result TEXT,
    claim_at TEXT
);
CREATE INDEX reminder_due ON reminder(status,scheduled_at,next_attempt_at);
CREATE INDEX reminder_event ON reminder(event_id,deadline_id);
CREATE TABLE reminder_attempt (
    id TEXT PRIMARY KEY,
    reminder_id TEXT NOT NULL REFERENCES reminder(id),
    scheduled_at TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    result TEXT NOT NULL
);
CREATE INDEX reminder_attempt_parent ON reminder_attempt(reminder_id,attempted_at);
