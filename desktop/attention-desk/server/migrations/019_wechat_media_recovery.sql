CREATE TABLE wechat_media_work (
 message_id TEXT PRIMARY KEY REFERENCES wechat_message(id),
 status TEXT NOT NULL DEFAULT 'pending',
 media_hash TEXT,
 reference_json TEXT NOT NULL DEFAULT '{}',
 batch_id TEXT REFERENCES wechat_batch(id),
 input_key TEXT,
 attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TEXT,
 last_error TEXT,
 last_attempt_at TEXT,
 completed_at TEXT
);
CREATE TABLE wechat_media_analysis (
 input_key TEXT PRIMARY KEY,
 batch_id TEXT NOT NULL UNIQUE REFERENCES wechat_batch(id),
 vision_allowed INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE TABLE wechat_media_parse_cache (
 sha256 TEXT NOT NULL,
 engine TEXT NOT NULL,
 version TEXT NOT NULL,
 status TEXT NOT NULL,
 blocks_json TEXT NOT NULL DEFAULT '[]',
 detail_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL,
 PRIMARY KEY(sha256,engine,version)
);
CREATE INDEX wechat_media_work_due ON wechat_media_work(status,next_attempt_at);
