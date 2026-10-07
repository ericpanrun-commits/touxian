CREATE TABLE ai_extraction (
    id TEXT PRIMARY KEY,
    raw_snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    request_id TEXT UNIQUE,
    request_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('running','waiting','valid','needs_review','failed')),
    raw_output TEXT,
    parsed_output TEXT,
    validation_json TEXT,
    error_json TEXT,
    token_usage_json TEXT,
    latency_ms INTEGER,
    thinking INTEGER NOT NULL CHECK(thinking IN (0,1)),
    config_json TEXT NOT NULL,
    reused_from_id TEXT REFERENCES ai_extraction(id)
);
CREATE INDEX extraction_snapshot ON ai_extraction(raw_snapshot_id, created_at DESC);
CREATE INDEX extraction_cache ON ai_extraction(cache_key, created_at DESC);
CREATE UNIQUE INDEX one_running_extraction ON ai_extraction(cache_key) WHERE status = 'running';
CREATE TABLE extraction_request (
    request_id TEXT PRIMARY KEY,
    raw_snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id),
    request_hash TEXT NOT NULL,
    extraction_id TEXT NOT NULL REFERENCES ai_extraction(id)
);
CREATE TABLE raw_review (
    raw_snapshot_id TEXT PRIMARY KEY REFERENCES raw_snapshot(id),
    title_hint TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed','cancelled')),
    version INTEGER NOT NULL DEFAULT 1,
    extraction_id TEXT REFERENCES ai_extraction(id),
    draft_json TEXT,
    confirmation_hash TEXT,
    confirmed_event_ids TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX pending_review ON raw_review(status, updated_at DESC);
CREATE TABLE event_context (
    event_id TEXT PRIMARY KEY REFERENCES event(id),
    ai_extraction_id TEXT REFERENCES ai_extraction(id),
    candidate_id TEXT NOT NULL,
    relevance TEXT NOT NULL CHECK(relevance IN ('yes','no','unclear')),
    participation TEXT NOT NULL CHECK(participation IN ('yes','no','later')),
    notice_kind TEXT NOT NULL,
    audience_json TEXT,
    eligibility_json TEXT NOT NULL,
    materials_json TEXT NOT NULL,
    locations_json TEXT NOT NULL,
    uncertainties_json TEXT NOT NULL,
    review_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE event_task_plan (
    event_id TEXT PRIMARY KEY REFERENCES event(id),
    actions_json TEXT NOT NULL,
    deadline_ids_json TEXT NOT NULL,
    activated_at TEXT
);
