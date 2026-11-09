import { AppFault, UUID } from './store.ts';
import { officialHealth } from './official_health.ts';

// Constant SQL; the sole parameter is the already-authenticated workspace UUID.
// No original text, contact, URL, credential, event, task or review content is read.
export const SOURCE_HEALTH_SQL = `
  WITH snapshots AS MATERIALIZED (
    SELECT CASE WHEN e.kind='raw_review' THEN e.payload#>>'{snapshot,raw_item_id}'
      ELSE e.payload->>'raw_item_id' END AS raw_id
    FROM public.eric_app_records e
    WHERE e.workspace_id=$1::uuid AND e.kind IN ('raw_review','snapshot') AND NOT e.deleted
  ), item_projection AS (
    SELECT i.id, i.content_hash,
      EXISTS(SELECT 1 FROM public.eric_app_records e
        WHERE e.workspace_id=$1::uuid AND e.kind='raw_item' AND e.id=i.id AND NOT e.deleted) AS has_raw,
      EXISTS(SELECT 1 FROM public.eric_app_records e
        WHERE e.workspace_id=$1::uuid AND e.kind='inbox' AND e.id=i.id AND NOT e.deleted) AS has_inbox,
      EXISTS(SELECT 1 FROM public.eric_app_records e
        WHERE e.workspace_id=$1::uuid AND e.kind='inbox_item' AND e.id=i.id AND NOT e.deleted) AS has_inbox_item,
      EXISTS(SELECT 1 FROM snapshots original WHERE original.raw_id=i.id) AS has_original
    FROM zqu_worker.items i
  ), totals AS (
    SELECT r.source_id, COUNT(*) AS item_count, MAX(r.first_seen_at)::text AS last_notification_at,
      COUNT(*) FILTER(WHERE i.content_hash='') AS pending_content,
      COUNT(*) FILTER(WHERE NOT i.has_raw) AS unmapped_raw_count,
      COUNT(*) FILTER(WHERE i.has_raw AND NOT i.has_original) AS missing_original_count,
      COUNT(*) FILTER(WHERE i.has_raw AND i.has_original AND (NOT i.has_inbox OR NOT i.has_inbox_item)) AS pending_projection_count
    FROM zqu_worker.source_refs r JOIN item_projection i ON i.id=r.item_id GROUP BY r.source_id
  )
  SELECT s.id, s.config->>'name' AS name,
    COALESCE((s.state->>'enabled')::boolean,(s.config->>'enabled')::boolean,false) AS enabled,
    s.config->>'polling_interval' AS polling_interval, s.state->>'status' AS status,
    s.state->>'last_checked_at' AS last_scan_at, s.state->>'last_success_at' AS last_success_at,
    s.state->>'failure_count' AS failure_count, s.state->>'error' AS error,
    s.state->>'last_new_count' AS last_new_count,
    worker.last_cloud_run::text AS worker_last_run_at,
    worker.next_expected_run::text AS worker_next_expected_at,
    worker.last_run_status AS worker_last_status,
    (SELECT expires_at::text FROM zqu_worker.leases WHERE name='actions' AND expires_at > clock_timestamp()) AS worker_active_until,
    COALESCE(t.item_count,0) AS item_count, t.last_notification_at,
    COALESCE(t.pending_content,0) AS pending_content,
    COALESCE(t.unmapped_raw_count,0) AS unmapped_raw_count,
    COALESCE(t.missing_original_count,0) AS missing_original_count,
    COALESCE(t.pending_projection_count,0) AS pending_projection_count
  FROM zqu_worker.sources s LEFT JOIN totals t ON t.source_id=s.id
    LEFT JOIN zqu_worker.cloud_status worker ON worker.id=1 ORDER BY s.id LIMIT 200`;

async function clientFor(databaseUrl: string): Promise<any> {
  // Lazy import keeps the optional observation path out of every existing route.
  const { default: postgres } = await import('npm:postgres@3.4.7');
  return postgres(databaseUrl, { max: 1, prepare: false, ssl: true,
    idle_timeout: 20, max_lifetime: 60, connect_timeout: 5, fetch_types: false,
    onnotice: () => {}, debug: false });
}

export function createOfficialHealthReader(options: {
  databaseUrl: string; clock?: () => Date; clientFactory?: (url: string) => Promise<any>;
}) {
  const clock = options.clock || (() => new Date());
  let client: Promise<any> | undefined;
  const cache = new Map<string, {expires: number; value: any}>();
  const inflight = new Map<string, Promise<any>>();
  return async function read(workspace: string): Promise<any> {
    if (!UUID.test(workspace)) throw new AppFault(400,'invalid_reference','工作区标识无效。');
    if (!options.databaseUrl) throw new AppFault(503,'source_health_not_configured','官网健康状态暂时无法读取。');
    const old = cache.get(workspace);
    if (old && old.expires > clock().getTime()) return old.value;
    if (inflight.has(workspace)) return await inflight.get(workspace);
    const pending = (async () => {
      try {
        client ||= (options.clientFactory || clientFor)(options.databaseUrl);
        const sql = await client;
        const rows = await sql.begin('read only', async (transaction: any) => {
          await transaction`SET LOCAL statement_timeout='5s'`;
          await transaction`SET LOCAL lock_timeout='2s'`;
          return await transaction.unsafe(SOURCE_HEALTH_SQL, [workspace]);
        });
        const value = officialHealth(rows, clock());
        cache.set(workspace, {expires: clock().getTime() + 60_000, value});
        // Fixed project source metadata, but projection counts stay workspace scoped.
        if (cache.size > 16) cache.delete(cache.keys().next().value!);
        return value;
      } catch {
        client = undefined;
        throw new AppFault(503,'source_health_unavailable','官网健康状态暂时无法读取。');
      } finally { inflight.delete(workspace); }
    })();
    inflight.set(workspace, pending);
    return await pending;
  };
}


