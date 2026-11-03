-- Additive DB identity; preserve the original Evidence/message tables and cursors.
CREATE TABLE wechat_db_cursor (
 chat_id TEXT PRIMARY KEY REFERENCES wechat_chat(id), account_scope TEXT NOT NULL,
 internal_chat_id TEXT NOT NULL, last_local_id INTEGER, last_create_time INTEGER,
 last_sort_seq INTEGER, secondary_fingerprint TEXT, last_successful_scan TEXT,
 shard_watermarks_json TEXT NOT NULL DEFAULT '{}', bootstrap_since INTEGER NOT NULL
);
CREATE TABLE wechat_db_message (
 message_id TEXT PRIMARY KEY REFERENCES wechat_message(id), chat_id TEXT NOT NULL REFERENCES wechat_chat(id),
 account_scope TEXT NOT NULL, internal_chat_id TEXT NOT NULL, shard TEXT NOT NULL,
 local_id INTEGER NOT NULL, server_id TEXT, sort_seq INTEGER NOT NULL,
 create_time INTEGER NOT NULL, secondary_fingerprint TEXT NOT NULL, media_reference_json TEXT,
 UNIQUE(chat_id,account_scope,shard,local_id)
);
CREATE INDEX wechat_db_message_sequence ON wechat_db_message(chat_id,sort_seq,shard,local_id);
CREATE TRIGGER wechat_db_identity_immutable BEFORE UPDATE ON wechat_db_message
BEGIN SELECT RAISE(ABORT,'Database message identities are immutable'); END;
