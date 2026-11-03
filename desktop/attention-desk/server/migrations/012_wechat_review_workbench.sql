-- Review lifecycle and bounded local repair; collector identity is unchanged.
ALTER TABLE wechat_candidate ADD COLUMN confirmation_hash TEXT;
ALTER TABLE wechat_llm_attempt ADD COLUMN json_repair_attempted INTEGER NOT NULL DEFAULT 0 CHECK(json_repair_attempted IN (0,1));
CREATE INDEX wechat_candidate_inbox ON wechat_candidate(status,created_at DESC,id);
