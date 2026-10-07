ALTER TABLE raw_item ADD COLUMN external_id TEXT;
ALTER TABLE raw_item ADD COLUMN canonical_url TEXT;
ALTER TABLE raw_item ADD COLUMN original_url TEXT;
ALTER TABLE raw_item ADD COLUMN title TEXT;
ALTER TABLE raw_item ADD COLUMN published_at TEXT;
ALTER TABLE raw_item ADD COLUMN discovered_at TEXT;
ALTER TABLE raw_item ADD COLUMN fetch_error TEXT NOT NULL DEFAULT '';
ALTER TABLE raw_item ADD COLUMN last_fetch_attempt_at TEXT;
CREATE UNIQUE INDEX official_canonical_url ON raw_item(canonical_url) WHERE kind='official_web';
CREATE TABLE official_source (
    source_id TEXT PRIMARY KEY REFERENCES source(id),
    config_json TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    polling_interval INTEGER NOT NULL CHECK(polling_interval >= 3600),
    status TEXT NOT NULL DEFAULT 'not_checked',
    last_checked_at TEXT,
    last_success_at TEXT,
    next_check_at TEXT,
    error TEXT NOT NULL DEFAULT '',
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_new_count INTEGER NOT NULL DEFAULT 0,
    last_discovered_count INTEGER NOT NULL DEFAULT 0,
    last_changed_count INTEGER NOT NULL DEFAULT 0,
    last_unchanged_count INTEGER NOT NULL DEFAULT 0,
    last_request_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE official_item_source (
    raw_item_id TEXT NOT NULL REFERENCES raw_item(id),
    source_id TEXT NOT NULL REFERENCES source(id),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY(raw_item_id, source_id)
);
CREATE TABLE official_url_alias (
    url TEXT PRIMARY KEY,
    raw_item_id TEXT NOT NULL REFERENCES raw_item(id)
);
CREATE TABLE raw_snapshot_web (
    raw_snapshot_id TEXT PRIMARY KEY REFERENCES raw_snapshot(id),
    source_id TEXT NOT NULL REFERENCES source(id),
    page_title TEXT NOT NULL,
    original_url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    attachments_json TEXT NOT NULL,
    images_json TEXT NOT NULL,
    body_html TEXT NOT NULL,
    body_status TEXT NOT NULL,
    version_hash TEXT NOT NULL,
    version INTEGER NOT NULL,
    UNIQUE(raw_snapshot_id, version_hash)
);
CREATE TRIGGER raw_snapshot_web_no_update BEFORE UPDATE ON raw_snapshot_web
BEGIN
    SELECT RAISE(ABORT, 'RawSnapshot metadata is immutable');
END;
CREATE TRIGGER raw_snapshot_web_no_delete BEFORE DELETE ON raw_snapshot_web
BEGIN
    SELECT RAISE(ABORT, 'RawSnapshot metadata is immutable');
END;
CREATE TABLE official_fetch_state (
    raw_item_id TEXT PRIMARY KEY REFERENCES raw_item(id),
    etag TEXT,
    last_modified TEXT,
    last_fetched_at TEXT NOT NULL,
    latest_snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id)
);
