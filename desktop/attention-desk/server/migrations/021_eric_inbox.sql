-- Submission is the existing RawItem. These columns contain processing metadata,
-- never another copy of original text, files, candidates, or jobs.
ALTER TABLE raw_item ADD COLUMN inbox_kind TEXT CHECK(inbox_kind IN ('text','image','pdf','file','url'));
ALTER TABLE raw_item ADD COLUMN inbox_status TEXT CHECK(inbox_status IN ('received','extracting','classifying','completed','needs_review','failed','recovery_needed','needs_manual_content'));
ALTER TABLE raw_item ADD COLUMN inbox_stage TEXT;
ALTER TABLE raw_item ADD COLUMN inbox_original_snapshot_id TEXT REFERENCES raw_snapshot(id);
ALTER TABLE raw_item ADD COLUMN inbox_snapshot_id TEXT REFERENCES raw_snapshot(id);
ALTER TABLE raw_item ADD COLUMN inbox_course_id TEXT REFERENCES course(id);
ALTER TABLE raw_item ADD COLUMN inbox_project_ref TEXT;
ALTER TABLE raw_item ADD COLUMN inbox_classification_json TEXT;
ALTER TABLE raw_item ADD COLUMN inbox_error_json TEXT;
ALTER TABLE raw_item ADD COLUMN inbox_updated_at TEXT;
ALTER TABLE raw_item ADD COLUMN inbox_fingerprint TEXT;
ALTER TABLE raw_item ADD COLUMN inbox_version INTEGER NOT NULL DEFAULT 1;
CREATE INDEX raw_inbox_state ON raw_item(inbox_status,created_at DESC);
CREATE UNIQUE INDEX raw_inbox_fingerprint ON raw_item(inbox_fingerprint) WHERE inbox_fingerprint IS NOT NULL;
CREATE INDEX IF NOT EXISTS raw_snapshot_item_created ON raw_snapshot(raw_item_id,created_at DESC,id DESC);
-- Durable receive acknowledgements / duplicate provenance, not a second input store.
CREATE TABLE inbox_receipt (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    request_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    raw_item_id TEXT NOT NULL REFERENCES raw_item(id),
    created_at TEXT NOT NULL,
    UNIQUE(owner,request_id)
);
CREATE INDEX inbox_receipt_raw ON inbox_receipt(raw_item_id);
-- Only added Inbox-triggered extraction uses this optional cost provenance.
ALTER TABLE ai_extraction ADD COLUMN cost_json TEXT;
