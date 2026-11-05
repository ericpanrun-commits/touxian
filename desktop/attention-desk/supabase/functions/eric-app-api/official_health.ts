// A health read model over the existing cloud collector; never returns bodies or secrets.
export function officialHealth(rows: any[], at = new Date()): any {
  const instant = at.getTime();
  const count = (value: unknown) => {
    if (typeof value !== 'number' && (typeof value !== 'string' || !/^\d+$/.test(value))) return null;
    const n = Number(value);
    return Number.isSafeInteger(n) && n >= 0 ? n : null;
  };
  const timestamp = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value)) ? value : null;
  const sources = rows.map(row => {
    const enabled = row.enabled === true;
    const lastSuccess = timestamp(row.last_success_at), lastScan = timestamp(row.last_scan_at);
    const failures = count(row.failure_count);
    const interval = Math.max(3600, count(row.polling_interval) || 3600);
    // The finite hourly worker rotates 29 configured sources across bounded
    // batches; give two normal rotation windows rather than alarming hourly.
    const staleAfter = Math.max(43200, interval * 3);
    const anchor = lastSuccess || lastScan;
    const stale = Boolean(anchor && instant - Date.parse(anchor) >= staleAfter * 1000);
    const status = typeof row.status === 'string' ? row.status : 'not_checked';
    const failed = ['unreachable','auth_required','error','failed','interrupted'].includes(status);
    const rawCount = count(row.item_count), unmapped = count(row.unmapped_raw_count);
    const pendingSync = count(row.pending_projection_count), pendingContent = count(row.pending_content);
    const missingOriginal = count(row.missing_original_count);
    const lastNew = count(row.last_new_count);
    return {
      source_id: 'official:' + row.id, id: row.id, name: row.name, owner: 'cloud', enabled,
      last_scan_at: lastScan, last_successful_scan_at: lastSuccess,
      last_notification_at: timestamp(row.last_notification_at),
      consecutive_failures: failures, last_scan_status: status,
      latest_error: typeof row.error === 'string' && /^[a-z][a-z0-9_]{0,80}$/.test(row.error) ? row.error : row.error ? 'source_error' : null,
      collection_state: !enabled ? 'disabled' : failures === null ? 'unknown' : failed ? 'failed' :
        status === 'partial' ? 'partial' : stale ? 'stale' : status === 'ok' && lastSuccess ?
        lastNew === 0 ? 'healthy_idle' : 'healthy' : 'not_checked',
      stale_after_seconds: staleAfter, pending_content: pendingContent,
      pending_analysis: 0, analysis_method: 'existing_deterministic_rules',
      collected_items: rawCount, unmapped_raw_count: unmapped, missing_original_count: missingOriginal, pending_sync: pendingSync,
      sync_state: rawCount === null || unmapped === null || pendingSync === null || missingOriginal === null ? 'unknown' : unmapped > 0 ?
        'integration_missing' : missingOriginal > 0 ? 'original_unavailable' : pendingSync > 0 ? 'pending' : rawCount ? 'synchronized' : 'not_checked',
      sync_evidence: 'existing_raw_and_inbox_ids_only', frontend_visibility: 'unverified',
      alert_required: Boolean(enabled && (failures !== null && failures >= 3 || stale)),
    };
  });
  type CountKey = 'pending_content' | 'pending_sync' | 'missing_original_count' | 'unmapped_raw_count';
  const total = (key: CountKey): number | null => sources.some(row => row[key] === null) ? null :
    sources.reduce((n, row) => n + (row[key] ?? 0), 0);
  return { owner: 'cloud', state: 'observed', checked_at: at.toISOString(), sources,
    count_unit: 'source_item_associations',
    pending_content: total('pending_content'), pending_sync: total('pending_sync'),
    missing_original_count: total('missing_original_count'), unmapped_raw_count: total('unmapped_raw_count') };
}
