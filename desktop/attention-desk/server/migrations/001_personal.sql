CREATE TABLE source (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE raw_item (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES source(id),
    kind TEXT NOT NULL,
    submission_id TEXT UNIQUE,
    submission_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE raw_snapshot (
    id TEXT PRIMARY KEY,
    raw_item_id TEXT NOT NULL REFERENCES raw_item(id),
    original_text TEXT NOT NULL CHECK(length(original_text) > 0),
    created_at TEXT NOT NULL,
    source_time TEXT,
    source_timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    hash TEXT NOT NULL,
    UNIQUE(id, raw_item_id)
);
CREATE TRIGGER raw_snapshot_no_update BEFORE UPDATE ON raw_snapshot
BEGIN
    SELECT RAISE(ABORT, 'RawSnapshot is immutable');
END;
CREATE TRIGGER raw_snapshot_no_delete BEFORE DELETE ON raw_snapshot
BEGIN
    SELECT RAISE(ABORT, 'RawSnapshot is immutable');
END;
CREATE TABLE event (
    id TEXT PRIMARY KEY,
    raw_item_id TEXT NOT NULL REFERENCES raw_item(id),
    raw_snapshot_id TEXT NOT NULL,
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    summary TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '其他',
    status TEXT NOT NULL DEFAULT 'needs_review'
        CHECK(status IN ('needs_review','needs_action','in_progress','done','cancelled')),
    previous_status TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(id, raw_snapshot_id),
    FOREIGN KEY(raw_snapshot_id, raw_item_id) REFERENCES raw_snapshot(id, raw_item_id)
);
CREATE INDEX event_updated ON event(updated_at DESC, id);
CREATE TABLE deadline (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES event(id),
    label TEXT NOT NULL,
    value TEXT NOT NULL,
    precision TEXT NOT NULL CHECK(precision IN ('date','minute')),
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    original_text TEXT NOT NULL DEFAULT '',
    confirmed_by_user INTEGER NOT NULL CHECK(confirmed_by_user IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(id, event_id)
);
CREATE INDEX deadline_event ON deadline(event_id);
CREATE TABLE task (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES event(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo','done')),
    sort_order INTEGER NOT NULL CHECK(sort_order >= 0),
    deadline_id TEXT,
    origin TEXT NOT NULL CHECK(origin IN ('explicit','suggested','manual')),
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(deadline_id, event_id) REFERENCES deadline(id, event_id)
);
CREATE INDEX task_event ON task(event_id, sort_order);
CREATE TABLE evidence (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    raw_snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id),
    field_path TEXT NOT NULL,
    quote TEXT NOT NULL,
    start_offset INTEGER NOT NULL CHECK(start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK(end_offset > start_offset),
    created_at TEXT NOT NULL,
    FOREIGN KEY(event_id, raw_snapshot_id) REFERENCES event(id, raw_snapshot_id),
    UNIQUE(event_id, field_path, raw_snapshot_id, start_offset, end_offset)
);
CREATE INDEX evidence_event ON evidence(event_id, field_path);
CREATE TABLE user_decision (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES event(id),
    action TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX decision_event ON user_decision(event_id, created_at);
