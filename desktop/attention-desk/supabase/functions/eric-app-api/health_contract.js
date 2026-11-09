// Shared by the Edge adapter and both frontends. Deliberately has no storage.
export const HEALTH_ORIGIN = 'https://health-analyzer.healthdash26.workers.dev';
export const HEALTH_PATH = '/api/integrations/eric-os/overview';
export const HEALTH_LIMIT = 32 * 1024;
export const HEALTH_METRICS = {
  sleep_duration_hours: ['睡眠时长', '小时'], steps: ['步数', '步'],
  resting_heart_rate_bpm: ['静息心率', 'bpm'], hrv_rmssd_ms: ['HRV', 'ms'],
  active_energy_kcal: ['活动能量', 'kcal'], exercise_minutes: ['锻炼分钟', '分钟'],
};
const requireValue = condition => { if (!condition) throw new Error('invalid_health_overview'); };
const date = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const instant = value => value === null || typeof value === 'string' && value.length <= 64 && /^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const number = value => value === null || typeof value === 'number' && Number.isFinite(value);
const plain = value => value && typeof value === 'object' && !Array.isArray(value);
export function shanghaiDay(now = new Date()) {
  return new Date(now.getTime() + 8 * 3600_000).toISOString().slice(0, 10);
}

// Return a new whitelist projection, never the untrusted upstream object.
export function validateHealthOverview(value) {
  requireValue(plain(value) && value.schema === 'health-overview.v1' && value.timezone === 'Asia/Shanghai' && ['trend', 'summary'].includes(value.mode));
  for (const key of ['source_generated_at', 'received_at', 'fetched_at']) requireValue(instant(value[key]));
  requireValue(value.fetched_at !== null && (value.latest_health_date === null || date(value.latest_health_date)));
  const period = value.window;
  requireValue(plain(period) && period.days === 7 && date(period.start) && date(period.end) && Date.parse(period.end) - Date.parse(period.start) === 6 * 86400_000);
  requireValue(Array.isArray(value.metrics) && value.metrics.length === 6);
  const metrics = Object.entries(HEALTH_METRICS).map(([id, [, unit]], index) => {
    const item = value.metrics[index];
    requireValue(plain(item) && item.id === id && item.unit === unit && number(item.latest_value) && (item.latest_date === null || date(item.latest_date)) && ((item.latest_date === null) === (item.latest_value === null)));
    if (item.latest_date) requireValue(item.latest_date <= period.end && Date.parse(period.end) - Date.parse(item.latest_date) < 30 * 86400_000);
    const projected = { id, unit, latest_value: item.latest_value, latest_date: item.latest_date };
    if (value.mode === 'trend') {
      requireValue(Array.isArray(item.days) && item.days.length === 7);
      projected.days = item.days.map((day, i) => {
        requireValue(plain(day) && date(day.date) && Date.parse(day.date) === Date.parse(period.start) + i * 86400_000 && number(day.value) && ['complete', 'partial', 'missing'].includes(day.status));
        requireValue(day.status !== 'missing' || day.value === null);
        return { date: day.date, value: day.value, status: day.status };
      });
      requireValue(plain(item.chart) && JSON.stringify(item.chart.view_box) === '[0,0,320,120]' && Array.isArray(item.chart.points) && item.chart.points.length <= 7);
      const seen = new Set();
      projected.chart = { view_box: [0, 0, 320, 120], points: item.chart.points.map(point => {
        requireValue(plain(point) && Number.isInteger(point.index) && point.index >= 0 && point.index < 7 && !seen.has(point.index) && projected.days[point.index].value !== null);
        requireValue(typeof point.x === 'number' && Number.isFinite(point.x) && point.x >= 0 && point.x <= 320 && typeof point.y === 'number' && Number.isFinite(point.y) && point.y >= 0 && point.y <= 120);
        seen.add(point.index);
        return { index: point.index, x: point.x, y: point.y };
      }) };
      requireValue(seen.size === projected.days.filter(day => day.value !== null && day.status !== 'missing').length);
    }
    return projected;
  });
  const analysis = value.analysis;
  requireValue(plain(analysis) && ['idle', 'pending', 'analyzing', 'completed', 'failed', 'empty', 'unknown'].includes(analysis.state) && typeof analysis.source_matches === 'boolean' && instant(analysis.completed_at));
  requireValue(analysis.source_matches || ['empty', 'unknown'].includes(analysis.state));
  requireValue(analysis.state === 'completed' || analysis.completed_at === null);
  return {
    schema: value.schema, mode: value.mode, timezone: value.timezone,
    source_generated_at: value.source_generated_at, received_at: value.received_at,
    latest_health_date: value.latest_health_date, fetched_at: value.fetched_at,
    window: { start: period.start, end: period.end, days: 7 }, metrics,
    analysis: { state: analysis.state, source_matches: analysis.source_matches, completed_at: analysis.completed_at },
  };
}
