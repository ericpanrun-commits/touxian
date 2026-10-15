-- Extend the existing official notification ledger without replacing any rows.
-- display_group remains the legacy primary delivery identity; Bark keys are namespaced.
ALTER TABLE raw_item ADD COLUMN official_backfill INTEGER NOT NULL DEFAULT 1 CHECK(official_backfill IN (0,1));
ALTER TABLE official_notification ADD COLUMN id TEXT;
ALTER TABLE official_notification ADD COLUMN channel TEXT NOT NULL DEFAULT 'windows';
ALTER TABLE official_notification ADD COLUMN item_group TEXT;
ALTER TABLE official_notification ADD COLUMN notification_type TEXT NOT NULL DEFAULT 'new_item';
ALTER TABLE official_notification ADD COLUMN status TEXT NOT NULL DEFAULT 'sent' CHECK(status IN ('pending','sending','sent','failed','suppressed'));
ALTER TABLE official_notification ADD COLUMN created_at TEXT;
ALTER TABLE official_notification ADD COLUMN scheduled_at TEXT;
ALTER TABLE official_notification ADD COLUMN attempted_at TEXT;
ALTER TABLE official_notification ADD COLUMN request_started_at TEXT;
ALTER TABLE official_notification ADD COLUMN sent_at TEXT;
ALTER TABLE official_notification ADD COLUMN failed_at TEXT;
ALTER TABLE official_notification ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE official_notification ADD COLUMN last_error TEXT;
ALTER TABLE official_notification ADD COLUMN suppressed_reason TEXT;
ALTER TABLE official_notification ADD COLUMN dedup_key TEXT;
ALTER TABLE official_notification ADD COLUMN provider_message_id TEXT;
ALTER TABLE official_notification ADD COLUMN payload_metadata TEXT NOT NULL DEFAULT '{}';
UPDATE official_notification SET id='windows:'||display_group,item_group=display_group,
    created_at=claimed_at,attempted_at=claimed_at,attempt_count=1,dedup_key='windows:new_item:'||display_group,
    status=CASE WHEN result='os_accepted' THEN 'sent' ELSE 'failed' END,
    sent_at=CASE WHEN result='os_accepted' THEN finished_at END,
    failed_at=CASE WHEN result<>'os_accepted' THEN COALESCE(finished_at,claimed_at) END;
CREATE UNIQUE INDEX official_notification_id ON official_notification(id);
CREATE UNIQUE INDEX official_notification_dedup ON official_notification(dedup_key);
CREATE UNIQUE INDEX official_notification_channel_group ON official_notification(channel,item_group,notification_type);
CREATE UNIQUE INDEX official_notification_channel_item ON official_notification(channel,raw_item_id,notification_type);
CREATE INDEX official_notification_due ON official_notification(channel,status,scheduled_at);
CREATE TABLE notification_preferences (
    channel TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    activation_at TEXT,
    policy_json TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    last_success_at TEXT,
    last_failure_at TEXT,
    last_error TEXT,
    test_claimed_at TEXT
);
INSERT INTO notification_preferences(channel) VALUES ('bark');
