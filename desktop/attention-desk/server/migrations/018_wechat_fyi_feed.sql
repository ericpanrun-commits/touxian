CREATE TABLE wechat_fyi_state (
 candidate_id TEXT PRIMARY KEY REFERENCES wechat_candidate(id),
 state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','archived','ignored','converted')),
 source_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 pinned_until TEXT,
 archive_reason TEXT,
 event_id TEXT REFERENCES event(id),
 version INTEGER NOT NULL DEFAULT 1,
 updated_at TEXT NOT NULL
);
CREATE INDEX wechat_fyi_feed ON wechat_fyi_state(state,source_at DESC,candidate_id);
