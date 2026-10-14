-- Additive projections over existing immutable RawItem/RawSnapshot records.
ALTER TABLE official_source ADD COLUMN backfill_completed_at TEXT;
ALTER TABLE official_source ADD COLUMN created_at TEXT;
ALTER TABLE official_source ADD COLUMN updated_at TEXT;
ALTER TABLE official_source ADD COLUMN last_error_at TEXT;
-- Keep legacy polling_interval's >=3600 constraint intact for older tooling.
ALTER TABLE official_source ADD COLUMN fetch_interval INTEGER NOT NULL DEFAULT 3600 CHECK(fetch_interval>=900);
UPDATE official_source SET fetch_interval=polling_interval;
UPDATE official_source SET created_at=COALESCE(last_checked_at,strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now');
CREATE TABLE official_item (
    raw_item_id TEXT PRIMARY KEY REFERENCES raw_item(id),
    category TEXT NOT NULL,
    summary TEXT NOT NULL,
    importance TEXT NOT NULL CHECK(importance IN ('critical','high','normal','low')),
    relevance INTEGER NOT NULL CHECK(relevance BETWEEN 0 AND 100),
    reasons_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    title_hash TEXT NOT NULL,
    display_group TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0 CHECK(is_read IN (0,1)),
    is_saved INTEGER NOT NULL DEFAULT 0 CHECK(is_saved IN (0,1)),
    deadline_at TEXT,
    event_start_at TEXT,
    event_end_at TEXT,
    time_evidence_json TEXT NOT NULL DEFAULT '[]',
    fetched_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    rule_version TEXT NOT NULL
);
CREATE INDEX official_item_group ON official_item(display_group);
CREATE INDEX official_item_category ON official_item(category,raw_item_id);
CREATE INDEX official_item_importance ON official_item(importance,relevance);
CREATE INDEX official_item_relevance ON official_item(relevance DESC,raw_item_id);
CREATE INDEX official_item_read ON official_item(is_read,is_saved);
CREATE INDEX official_published ON raw_item(published_at DESC,id) WHERE kind='official_web';
CREATE INDEX official_source_items ON official_item_source(source_id,raw_item_id);
CREATE TABLE official_run (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source(id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    scanned INTEGER NOT NULL DEFAULT 0,
    discovered INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX official_run_recent ON official_run(started_at DESC,source_id);
CREATE INDEX official_run_batch ON official_run(batch_id,started_at);
CREATE TABLE official_job_lease (
    name TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);
CREATE TABLE official_notification (
    display_group TEXT PRIMARY KEY,
    raw_item_id TEXT NOT NULL REFERENCES raw_item(id),
    claimed_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT NOT NULL
);
