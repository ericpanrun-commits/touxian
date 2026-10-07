CREATE TABLE attachment (
    id TEXT PRIMARY KEY,
    raw_item_id TEXT NOT NULL REFERENCES raw_item(id),
    origin_snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id),
    filename TEXT NOT NULL,
    original_url TEXT,
    local_path TEXT,
    mime_type TEXT,
    extension TEXT,
    size INTEGER,
    sha256 TEXT,
    downloaded_at TEXT,
    parse_status TEXT NOT NULL CHECK(parse_status IN ('not_downloaded','downloaded','parsing','parsed','unsupported','failed')),
    parse_error TEXT NOT NULL DEFAULT '',
    extracted_text TEXT,
    extractor TEXT,
    extractor_version TEXT,
    parse_manifest_json TEXT,
    needs_review INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX attachment_item ON attachment(raw_item_id,original_url,version);
CREATE TRIGGER parsed_attachment_no_update BEFORE UPDATE ON attachment WHEN OLD.parse_status='parsed'
BEGIN SELECT RAISE(ABORT,'Parsed attachment versions are immutable'); END;
CREATE TRIGGER attachment_no_delete BEFORE DELETE ON attachment
BEGIN SELECT RAISE(ABORT,'Original attachments must be preserved'); END;
CREATE TABLE snapshot_attachment (
    raw_snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id),
    attachment_id TEXT NOT NULL REFERENCES attachment(id),
    PRIMARY KEY(raw_snapshot_id,attachment_id)
);
CREATE TRIGGER snapshot_attachment_no_update BEFORE UPDATE ON snapshot_attachment
BEGIN SELECT RAISE(ABORT,'Snapshot attachment references are immutable'); END;
CREATE TRIGGER snapshot_attachment_no_delete BEFORE DELETE ON snapshot_attachment
BEGIN SELECT RAISE(ABORT,'Snapshot attachment references are immutable'); END;
CREATE TABLE raw_source_block (
    raw_snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id),
    block_id TEXT NOT NULL,
    attachment_id TEXT REFERENCES attachment(id),
    source_type TEXT NOT NULL CHECK(source_type IN ('text','web','pdf','docx','image_ocr')),
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    page INTEGER,
    section TEXT,
    bounding_box_json TEXT,
    words_json TEXT NOT NULL DEFAULT '[]',
    raw_ocr_text TEXT,
    confidence REAL,
    needs_review INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(raw_snapshot_id,block_id)
);
CREATE TRIGGER source_block_no_update BEFORE UPDATE ON raw_source_block
BEGIN SELECT RAISE(ABORT,'Evidence source blocks are immutable'); END;
CREATE TRIGGER source_block_no_delete BEFORE DELETE ON raw_source_block
BEGIN SELECT RAISE(ABORT,'Evidence source blocks are immutable'); END;
CREATE TABLE snapshot_document (
    raw_snapshot_id TEXT PRIMARY KEY REFERENCES raw_snapshot(id),
    body_snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id),
    body_text TEXT NOT NULL,
    version INTEGER NOT NULL,
    manifest_hash TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER snapshot_document_no_update BEFORE UPDATE ON snapshot_document
BEGIN SELECT RAISE(ABORT,'Document snapshot manifests are immutable'); END;
CREATE TRIGGER snapshot_document_no_delete BEFORE DELETE ON snapshot_document
BEGIN SELECT RAISE(ABORT,'Document snapshot manifests are immutable'); END;
CREATE TABLE attachment_job (
    id TEXT PRIMARY KEY,
    raw_item_id TEXT NOT NULL REFERENCES raw_item(id),
    source_snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id),
    attachment_ids_json TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    result_snapshot_id TEXT REFERENCES raw_snapshot(id),
    error_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX attachment_job_snapshot ON attachment_job(source_snapshot_id,created_at);
CREATE TABLE evidence_v4 (
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
    FOREIGN KEY(event_id,raw_snapshot_id) REFERENCES event(id,raw_snapshot_id),
    FOREIGN KEY(raw_snapshot_id,block_id) REFERENCES raw_source_block(raw_snapshot_id,block_id)
);
INSERT INTO evidence_v4 (id,event_id,raw_snapshot_id,field_path,quote,start_offset,end_offset,created_at,source_type)
SELECT e.id,e.event_id,e.raw_snapshot_id,e.field_path,e.quote,e.start_offset,e.end_offset,e.created_at,
CASE WHEN i.kind='official_web' THEN 'web' ELSE 'text' END
FROM evidence e JOIN raw_snapshot s ON s.id=e.raw_snapshot_id JOIN raw_item i ON i.id=s.raw_item_id;
DROP TABLE evidence;
ALTER TABLE evidence_v4 RENAME TO evidence;
CREATE INDEX evidence_event ON evidence(event_id,field_path);
CREATE UNIQUE INDEX evidence_legacy_span ON evidence(event_id,field_path,raw_snapshot_id,start_offset,end_offset) WHERE block_id IS NULL;
CREATE UNIQUE INDEX evidence_block_span ON evidence(event_id,field_path,raw_snapshot_id,block_id,start_offset,end_offset) WHERE block_id IS NOT NULL;
