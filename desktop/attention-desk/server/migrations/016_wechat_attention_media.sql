-- Unparsed attachments have their own queue, independent of model items.
CREATE TABLE wechat_attention_media (
 message_id TEXT PRIMARY KEY REFERENCES wechat_message(id),
 batch_id TEXT NOT NULL REFERENCES wechat_batch(id),
 snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id),
 status TEXT NOT NULL DEFAULT 'media_pending' CHECK(status IN ('media_pending','resolved')),
 reason TEXT NOT NULL,created_at TEXT NOT NULL,resolved_at TEXT
);
CREATE INDEX wechat_attention_media_queue ON wechat_attention_media(status,created_at DESC);

-- Archive FYI sources without mislabelling automatic routing as a user cancel.
CREATE TABLE raw_review_attention (
 raw_snapshot_id TEXT PRIMARY KEY REFERENCES raw_snapshot(id),
 title_hint TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed','cancelled','archived')),
 version INTEGER NOT NULL DEFAULT 1,
 extraction_id TEXT REFERENCES ai_extraction(id),draft_json TEXT,confirmation_hash TEXT,
 confirmed_event_ids TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
);
INSERT INTO raw_review_attention SELECT * FROM raw_review;
DROP TABLE raw_review;
ALTER TABLE raw_review_attention RENAME TO raw_review;
