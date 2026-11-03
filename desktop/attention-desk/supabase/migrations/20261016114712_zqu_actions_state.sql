-- Isolated school state only. Never adopt an existing unmarked namespace/table.
-- This advisory transaction lock serializes cooperating installations.
SELECT pg_advisory_xact_lock(hashtext('zqu-workspace-actions-state-v1'));
DO $$
DECLARE ns oid;
BEGIN
  SELECT oid INTO ns FROM pg_namespace WHERE nspname='zqu_worker';
  IF ns IS NOT NULL AND obj_description(ns,'pg_namespace') IS DISTINCT FROM 'zqu-workspace:actions-state:v1' THEN
    RAISE EXCEPTION 'zqu_worker exists without school ownership marker; installation refused';
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS zqu_worker;
COMMENT ON SCHEMA zqu_worker IS 'zqu-workspace:actions-state:v1';

DO $$
DECLARE name text; existing oid;
  expected text[] := ARRAY['meta','sources','items','source_refs','aliases','notification_events','leases','crawl_runs','worker_runs','cloud_status'];
BEGIN
  IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='zqu_worker' AND c.relkind IN ('r','p','v','m','f','S') AND NOT c.relname=ANY(expected))
      OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='zqu_worker') THEN
    RAISE EXCEPTION 'Unexpected object in school namespace; installation refused';
  END IF;
  FOREACH name IN ARRAY expected
  LOOP
    existing := to_regclass(format('zqu_worker.%I',name));
    IF existing IS NOT NULL AND obj_description(existing,'pg_class') IS DISTINCT FROM 'zqu-workspace:actions-state:v1' THEN
      RAISE EXCEPTION 'Unmarked school-table name collision; installation refused';
    END IF;
  END LOOP;
END $$;
REVOKE ALL ON SCHEMA zqu_worker FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA zqu_worker TO service_role;

CREATE TABLE IF NOT EXISTS zqu_worker.meta (
    id smallint PRIMARY KEY CHECK(id=1), schema_version integer NOT NULL CHECK(schema_version=1),
    database_id text NOT NULL, snapshot_hash text NOT NULL,
    activation_at text NOT NULL, policy jsonb NOT NULL, enabled boolean NOT NULL DEFAULT true,
    import_completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    first_dry_run_verified boolean NOT NULL DEFAULT false,
    last_success_at timestamptz, last_failure_at timestamptz, last_error text
);
CREATE TABLE IF NOT EXISTS zqu_worker.sources (
    id text PRIMARY KEY, config jsonb NOT NULL, state jsonb NOT NULL,
    next_check_at timestamptz
);
CREATE TABLE IF NOT EXISTS zqu_worker.items (
    id text PRIMARY KEY, canonical_url text NOT NULL UNIQUE, body jsonb NOT NULL,
    content_hash text NOT NULL DEFAULT '', title_hash text NOT NULL DEFAULT '', display_group text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS items_content ON zqu_worker.items(content_hash,title_hash) WHERE content_hash<>'';
CREATE TABLE IF NOT EXISTS zqu_worker.source_refs (
    item_id text NOT NULL REFERENCES zqu_worker.items(id),
    source_id text NOT NULL REFERENCES zqu_worker.sources(id),
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(item_id,source_id)
);
CREATE INDEX IF NOT EXISTS source_refs_source ON zqu_worker.source_refs(source_id,item_id);
CREATE TABLE IF NOT EXISTS zqu_worker.aliases (
    url text PRIMARY KEY, item_id text NOT NULL REFERENCES zqu_worker.items(id)
);
CREATE INDEX IF NOT EXISTS aliases_item ON zqu_worker.aliases(item_id);
CREATE TABLE IF NOT EXISTS zqu_worker.notification_events (
    id text PRIMARY KEY, channel text NOT NULL DEFAULT 'bark', notification_type text NOT NULL DEFAULT 'new_item',
    item_id text NOT NULL REFERENCES zqu_worker.items(id), item_group text NOT NULL, dedup_key text NOT NULL UNIQUE,
    status text NOT NULL CHECK(status IN ('pending','sending','sent','failed','suppressed')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(), scheduled_at timestamptz,
    attempted_at timestamptz, request_started_at timestamptz, sent_at timestamptz, failed_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0), last_error text, suppressed_reason text,
    provider_message_id text, metadata jsonb NOT NULL DEFAULT '{}',
    UNIQUE(channel,item_id,notification_type), UNIQUE(channel,item_group,notification_type)
);
CREATE INDEX IF NOT EXISTS notification_due ON zqu_worker.notification_events(scheduled_at,id)
    WHERE status IN ('pending','failed') AND scheduled_at IS NOT NULL;
CREATE TABLE IF NOT EXISTS zqu_worker.leases (
    name text PRIMARY KEY, owner text NOT NULL, expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS zqu_worker.crawl_runs (
    id text PRIMARY KEY, batch_id text, source_id text NOT NULL REFERENCES zqu_worker.sources(id),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
    status text NOT NULL, counts jsonb NOT NULL DEFAULT '{}', error_code text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS crawl_runs_recent ON zqu_worker.crawl_runs(started_at DESC);
CREATE TABLE IF NOT EXISTS zqu_worker.worker_runs (
    id text PRIMARY KEY, github_run text NOT NULL, started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz, status text NOT NULL, dry_run boolean NOT NULL,
    summary jsonb NOT NULL DEFAULT '{}', elapsed_seconds numeric
);
CREATE INDEX IF NOT EXISTS worker_runs_recent ON zqu_worker.worker_runs(started_at DESC);

-- Status remains private. Any later public read surface requires separate review.
CREATE TABLE IF NOT EXISTS zqu_worker.cloud_status (
    id smallint PRIMARY KEY CHECK(id=1), last_cloud_run timestamptz,
    last_successful_cloud_run timestamptz, next_expected_run timestamptz,
    last_source_success timestamptz, last_bark_success timestamptz,
    last_run_status text, dry_run boolean NOT NULL DEFAULT true
);
DO $$
DECLARE name text;
BEGIN
  FOREACH name IN ARRAY ARRAY['meta','sources','items','source_refs','aliases','notification_events','leases','crawl_runs','worker_runs','cloud_status']
  LOOP
    EXECUTE format('COMMENT ON TABLE zqu_worker.%I IS %L',name,'zqu-workspace:actions-state:v1');
    EXECUTE format('ALTER TABLE zqu_worker.%I ENABLE ROW LEVEL SECURITY',name);
    EXECUTE format('REVOKE ALL ON zqu_worker.%I FROM PUBLIC,anon,authenticated',name);
    EXECUTE format('GRANT ALL ON zqu_worker.%I TO service_role',name);
  END LOOP;
END $$;
