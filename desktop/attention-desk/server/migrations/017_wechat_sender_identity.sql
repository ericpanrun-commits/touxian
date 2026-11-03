-- Immutable identity evidence, separate from the existing stable message/cursor keys.
CREATE TABLE wechat_sender_identity (
 message_id TEXT PRIMARY KEY REFERENCES wechat_message(id),
 data_json TEXT NOT NULL,
 captured_at TEXT NOT NULL
);
CREATE TRIGGER wechat_sender_identity_immutable BEFORE UPDATE ON wechat_sender_identity
BEGIN SELECT RAISE(ABORT,'Captured sender identity is immutable'); END;
