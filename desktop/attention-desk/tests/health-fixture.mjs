import { HEALTH_METRICS } from '../supabase/functions/eric-app-api/health_contract.js';

export function healthFixture(now = new Date()) {
  const end = new Date(now.getTime() + 8 * 3600_000).toISOString().slice(0, 10);
  const dateAt = offset => new Date(Date.parse(end) + offset * 86400_000).toISOString().slice(0, 10);
  const instant = now.toISOString();
  return {
    schema: 'health-overview.v1', mode: 'trend', timezone: 'Asia/Shanghai',
    source_generated_at: instant, received_at: instant, latest_health_date: end, fetched_at: instant,
    window: { start: dateAt(-6), end, days: 7 },
    metrics: Object.entries(HEALTH_METRICS).map(([id, [, unit]], metric) => ({
      id, unit, latest_value: 40 + metric, latest_date: dateAt(-1),
      days: Array.from({ length: 7 }, (_, index) => ({ date: dateAt(index - 6), value: index === 1 ? null : index === 2 ? 0 : 35 + metric + index, status: index === 1 ? 'missing' : index === 6 ? 'partial' : 'complete' })),
      chart: { view_box: [0, 0, 320, 120], points: [0, 2, 3, 4, 5, 6].map(index => ({ index, x: 18 + index * 48, y: 80 - index * 8 })) },
    })), analysis: { state: 'completed', source_matches: true, completed_at: instant },
  };
}

if (process.argv.includes('--json')) console.log(JSON.stringify(healthFixture(new Date('2026-11-09T04:00:00Z'))));
