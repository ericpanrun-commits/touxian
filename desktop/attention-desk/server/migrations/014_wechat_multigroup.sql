CREATE TABLE wechat_group_state (
  chat_id TEXT PRIMARY KEY REFERENCES wechat_chat(id),
  last_scan_id TEXT REFERENCES wechat_scan(id),
  last_scan_at TEXT,
  last_status TEXT NOT NULL DEFAULT 'not_scanned',
  last_error TEXT,
  messages_new INTEGER NOT NULL DEFAULT 0,
  messages_filtered INTEGER NOT NULL DEFAULT 0,
  metadata_queries INTEGER NOT NULL DEFAULT 0,
  message_body_queries INTEGER NOT NULL DEFAULT 0,
  unauthorized_message_bodies_read INTEGER NOT NULL DEFAULT 0,
  messages_sent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX wechat_batch_chat_scan ON wechat_batch(chat_id,scan_id);
CREATE INDEX wechat_attempt_job ON wechat_llm_attempt(job_id,started_at);
