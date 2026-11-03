-- Additive intake state. Formal event/task/reminder tables are not modified.
CREATE TABLE wechat_chat (
 id TEXT PRIMARY KEY, account_scope_id TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES source(id),
 name TEXT NOT NULL, config_json TEXT NOT NULL, binding_json TEXT, capability TEXT NOT NULL DEFAULT 'not_tested',
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)), binding_version INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE wechat_cursor (
 chat_id TEXT PRIMARY KEY REFERENCES wechat_chat(id), anchor_ids_json TEXT NOT NULL DEFAULT '[]',
 last_processed_message TEXT, last_success_at TEXT, last_observed_at TEXT,
 gap_json TEXT, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE wechat_scan (
 id TEXT PRIMARY KEY, request_key TEXT UNIQUE, reason TEXT NOT NULL, created_at TEXT NOT NULL,
 completed_at TEXT, status TEXT NOT NULL, config_revision INTEGER NOT NULL, parent_scan_id TEXT REFERENCES wechat_scan(id),
 override_expires_at TEXT, stats_json TEXT NOT NULL DEFAULT '{}', error_code TEXT
);
CREATE INDEX wechat_scan_date ON wechat_scan(created_at DESC,id);
CREATE TABLE wechat_chat_scan (
 id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES wechat_scan(id), chat_id TEXT NOT NULL REFERENCES wechat_chat(id),
 status TEXT NOT NULL, reason TEXT, pages INTEGER NOT NULL DEFAULT 0,
 anchor_found INTEGER NOT NULL DEFAULT 0, covers_tail INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, completed_at TEXT, stats_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX wechat_chat_scan_parent ON wechat_chat_scan(scan_id,chat_id);
CREATE TABLE wechat_message (
 id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES wechat_chat(id), ingest_seq INTEGER NOT NULL,
 sender TEXT NOT NULL, sender_reliability TEXT NOT NULL, raw_text TEXT NOT NULL, message_type TEXT NOT NULL,
 source_time TEXT, time_reliability TEXT NOT NULL, captured_at TEXT NOT NULL,
 content_signature TEXT NOT NULL, message_hash TEXT NOT NULL UNIQUE, attachment_identity TEXT NOT NULL DEFAULT '',
 first_scan_id TEXT NOT NULL REFERENCES wechat_scan(id), chat_name_at_capture TEXT NOT NULL, UNIQUE(chat_id,ingest_seq)
);
CREATE INDEX wechat_message_signature ON wechat_message(chat_id,content_signature);
CREATE INDEX wechat_message_date ON wechat_message(chat_id,captured_at);
CREATE TRIGGER wechat_message_immutable BEFORE UPDATE ON wechat_message
BEGIN SELECT RAISE(ABORT,'Captured messages are immutable'); END;
CREATE TRIGGER wechat_message_preserved BEFORE DELETE ON wechat_message
BEGIN SELECT RAISE(ABORT,'Captured messages must be preserved'); END;
CREATE TABLE wechat_observation (
 id TEXT PRIMARY KEY, chat_scan_id TEXT NOT NULL REFERENCES wechat_chat_scan(id), observation_ordinal INTEGER NOT NULL,
 message_id TEXT REFERENCES wechat_message(id), signature TEXT NOT NULL, match_method TEXT NOT NULL,
 reason TEXT, unmatched_json TEXT, UNIQUE(chat_scan_id,observation_ordinal)
);
CREATE TABLE wechat_message_state (
 message_id TEXT PRIMARY KEY REFERENCES wechat_message(id), status TEXT NOT NULL DEFAULT 'new',
 filter_level TEXT, filter_reason TEXT, rule_version TEXT, media_revision INTEGER NOT NULL DEFAULT 0,
 last_processed_at TEXT, error_code TEXT
);
CREATE TABLE wechat_media (
 id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES wechat_message(id), sha256 TEXT NOT NULL,
 phash TEXT, local_path TEXT NOT NULL, capture_kind TEXT NOT NULL, width INTEGER, height INTEGER,
 status TEXT NOT NULL DEFAULT 'saved', revision INTEGER NOT NULL DEFAULT 1, engine TEXT,
 blocks_json TEXT, error_code TEXT, created_at TEXT NOT NULL, UNIQUE(message_id,sha256,revision)
);
CREATE INDEX wechat_media_hash ON wechat_media(sha256,engine,status);
CREATE TABLE wechat_batch (
 id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES wechat_chat(id),
 raw_item_id TEXT REFERENCES raw_item(id), snapshot_id TEXT REFERENCES raw_snapshot(id),
 scan_id TEXT NOT NULL REFERENCES wechat_scan(id), input_hash TEXT NOT NULL,
 status TEXT NOT NULL, ready_at TEXT NOT NULL, followup_count INTEGER NOT NULL DEFAULT 0,
 previous_batch_id TEXT REFERENCES wechat_batch(id), superseded_by TEXT REFERENCES wechat_batch(id),
 warnings_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);
CREATE INDEX wechat_batch_due ON wechat_batch(status,ready_at);
CREATE TABLE wechat_batch_message (
 batch_id TEXT NOT NULL REFERENCES wechat_batch(id), message_id TEXT NOT NULL REFERENCES wechat_message(id),
 content_revision INTEGER NOT NULL DEFAULT 0, role TEXT NOT NULL CHECK(role IN ('primary','context')),
 ordinal INTEGER NOT NULL, PRIMARY KEY(batch_id,message_id,content_revision)
);
CREATE UNIQUE INDEX wechat_primary_once ON wechat_batch_message(message_id,content_revision) WHERE role='primary';
CREATE TABLE wechat_analysis_job (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES wechat_batch(id),
 snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id), input_key TEXT NOT NULL UNIQUE,
 profile_json TEXT NOT NULL, config_json TEXT NOT NULL, status TEXT NOT NULL,
 lease_at TEXT, next_attempt_at TEXT, extraction_id TEXT REFERENCES ai_extraction(id),
 error_code TEXT, created_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX wechat_job_due ON wechat_analysis_job(status,next_attempt_at);
CREATE TABLE wechat_llm_attempt (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES wechat_analysis_job(id),
 attempt_number INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('text','vision')),
 started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL,
 reserved_tokens INTEGER NOT NULL, token_usage_json TEXT, outcome_unknown INTEGER NOT NULL DEFAULT 0, raw_output TEXT,
 UNIQUE(job_id,attempt_number)
);
CREATE INDEX wechat_attempt_date ON wechat_llm_attempt(started_at,kind);
CREATE TABLE wechat_candidate (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES wechat_batch(id), model_item_id TEXT NOT NULL,
 important INTEGER NOT NULL, relevant INTEGER, status TEXT NOT NULL,
 data_json TEXT NOT NULL, previous_candidate_id TEXT REFERENCES wechat_candidate(id),
 event_id TEXT REFERENCES event(id), created_at TEXT NOT NULL, UNIQUE(batch_id,model_item_id)
);
CREATE INDEX wechat_candidate_status ON wechat_candidate(status,created_at DESC);
CREATE TABLE wechat_snapshot_message (
 snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id), block_id TEXT NOT NULL,
 message_id TEXT NOT NULL REFERENCES wechat_message(id), media_id TEXT REFERENCES wechat_media(id),
 content_revision INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(snapshot_id,block_id),
 FOREIGN KEY(snapshot_id,block_id) REFERENCES raw_source_block(raw_snapshot_id,block_id)
);
CREATE TRIGGER wechat_mapping_immutable BEFORE UPDATE ON wechat_snapshot_message
BEGIN SELECT RAISE(ABORT,'Message evidence mappings are immutable'); END;
CREATE TRIGGER wechat_mapping_preserved BEFORE DELETE ON wechat_snapshot_message
BEGIN SELECT RAISE(ABORT,'Message evidence mappings must be preserved'); END;
-- Preserve the established block/FK layout while adding a distinct derived-text kind.
CREATE TABLE raw_source_block_v10 (
 raw_snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id), block_id TEXT NOT NULL,
 attachment_id TEXT REFERENCES attachment(id),
 source_type TEXT NOT NULL CHECK(source_type IN ('text','web','pdf','docx','image_ocr','image_vision')),
 ordinal INTEGER NOT NULL, text TEXT NOT NULL, page INTEGER, section TEXT,
 bounding_box_json TEXT, words_json TEXT NOT NULL DEFAULT '[]', raw_ocr_text TEXT,
 confidence REAL, needs_review INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(raw_snapshot_id,block_id)
);
INSERT INTO raw_source_block_v10 SELECT * FROM raw_source_block;
DROP TABLE raw_source_block;
ALTER TABLE raw_source_block_v10 RENAME TO raw_source_block;
CREATE TRIGGER source_block_no_update BEFORE UPDATE ON raw_source_block
BEGIN SELECT RAISE(ABORT,'Evidence source blocks are immutable'); END;
CREATE TRIGGER source_block_no_delete BEFORE DELETE ON raw_source_block
BEGIN SELECT RAISE(ABORT,'Evidence source blocks are immutable'); END;
