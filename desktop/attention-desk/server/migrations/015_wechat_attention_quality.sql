-- Attention is independent of immutable source evidence and collector cursors.
ALTER TABLE wechat_candidate ADD COLUMN attention_level TEXT CHECK(attention_level IN ('action','fyi','ignore'));
ALTER TABLE wechat_candidate ADD COLUMN information_complete INTEGER NOT NULL DEFAULT 0;
ALTER TABLE wechat_candidate ADD COLUMN await_more_context INTEGER NOT NULL DEFAULT 1;
ALTER TABLE wechat_candidate ADD COLUMN attention_reason TEXT;
ALTER TABLE wechat_candidate ADD COLUMN auto_archived_at TEXT;
CREATE INDEX wechat_candidate_attention ON wechat_candidate(attention_level,status,created_at DESC);
CREATE TABLE wechat_attention_state (
 candidate_id TEXT PRIMARY KEY REFERENCES wechat_candidate(id),
 policy_version TEXT NOT NULL,input_hash TEXT NOT NULL,
 original_data_json TEXT NOT NULL,previous_status TEXT NOT NULL,
 decision_json TEXT NOT NULL,classified_at TEXT NOT NULL
);
CREATE TABLE wechat_context_buffer (
 message_id TEXT PRIMARY KEY REFERENCES wechat_message(id),
 chat_id TEXT NOT NULL REFERENCES wechat_chat(id),topic TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'waiting' CHECK(status IN ('waiting','resolved','expired')),
 created_at TEXT NOT NULL,expires_at TEXT NOT NULL,
 resolved_by_batch_id TEXT REFERENCES wechat_batch(id)
);
CREATE INDEX wechat_context_waiting ON wechat_context_buffer(chat_id,status,expires_at);
