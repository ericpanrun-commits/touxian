CREATE TABLE wechat_bark_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  enabled INTEGER NOT NULL DEFAULT 0,
  server_url TEXT,
  protected_key TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  activated_at TEXT,
  last_test_at TEXT
);
INSERT INTO wechat_bark_settings(id) VALUES(1);
CREATE TABLE wechat_bark_notification (
  candidate_id TEXT PRIMARY KEY REFERENCES wechat_candidate(id),
  state TEXT NOT NULL CHECK(state IN ('not_sent','sending','sent','failed')),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  retry_at TEXT,
  retryable INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  level TEXT NOT NULL,
  created_at TEXT NOT NULL,
  sent_at TEXT
);
