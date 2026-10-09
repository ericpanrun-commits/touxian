-- Preserve the legacy status contract used by existing collectors/reviews.
-- analysis_state records the richer pipeline lifecycle independently.
ALTER TABLE ai_extraction ADD COLUMN analysis_state TEXT CHECK(analysis_state IN ('running','validated','partial','truncated','failed','cancelled'));
ALTER TABLE ai_extraction ADD COLUMN progress_json TEXT;
ALTER TABLE ai_extraction ADD COLUMN plan_json TEXT;
CREATE TABLE ai_chunk_extraction (
    id TEXT PRIMARY KEY,
    extraction_id TEXT NOT NULL REFERENCES ai_extraction(id),
    chunk_id TEXT NOT NULL,
    parent_chunk_id TEXT,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','running','validated','truncated','failed','cancelled','split')),
    input_hash TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    source_snapshot_hash TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    retry_reason TEXT NOT NULL,
    reused_from_key TEXT,
    raw_output TEXT,
    parsed_output TEXT,
    validation_json TEXT,
    error_json TEXT,
    token_usage_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX chunks_by_extraction ON ai_chunk_extraction(extraction_id, created_at, id);
CREATE TABLE extraction_chunk_cache (
    cache_key TEXT PRIMARY KEY,
    reuse_key TEXT NOT NULL,
    source_snapshot_hash TEXT NOT NULL,
    chunk_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    thinking INTEGER NOT NULL,
    parsed_output TEXT NOT NULL,
    token_usage_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX chunk_content_reuse ON extraction_chunk_cache(reuse_key, created_at DESC);
