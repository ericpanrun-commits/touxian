import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { validateHealthOverview, shanghaiDay } from '../supabase/functions/eric-app-api/health_contract.js';
import { healthFixture } from './health-fixture.mjs';

test('shared Overview whitelist preserves six metrics and zero while stripping unknown private fields', () => {
  const value=healthFixture();value.secret='must-not-leak';value.metrics[0].raw='must-not-leak';value.analysis.report='must-not-leak';
  const result=validateHealthOverview(value);
  assert.equal(result.metrics.length,6);assert.equal(result.metrics[0].days[2].value,0);
  assert.ok(!JSON.stringify(result).includes('must-not-leak'));
  const summary={...value,mode:'summary'};
  assert.ok(!('days' in validateHealthOverview(summary).metrics[0]));
});

test('rejects invalid schema, identity-shaped values, dates, units, numbers and executable geometry', () => {
  for(const mutate of [v=>v.schema='other',v=>v.metrics.pop(),v=>v.timezone='UTC',v=>v.window.start='2026-02-30',v=>v.window.days=30,v=>v.metrics[0].unit='ml',v=>v.metrics[0].latest_value=NaN,v=>v.metrics[0].days[0].value='123',v=>v.metrics[0].days[0].date=v.window.end,v=>v.metrics[0].chart.view_box=['javascript:bad'],v=>v.metrics[0].chart.points[0].x=Infinity,v=>v.metrics[0].chart.points[0].index=-1,v=>v.metrics[0].chart.points.push(v.metrics[0].chart.points[0]),v=>v.analysis.source_matches=false]) {
    const value=healthFixture();mutate(value);assert.throws(()=>validateHealthOverview(value));
  }
  assert.equal(shanghaiDay(new Date('2026-11-09T15:59:59Z')),'2026-11-09');
  assert.equal(shanghaiDay(new Date('2026-11-09T16:00:00Z')),'2026-11-10');
});

test('Health transport bypasses the offline cache and public SW stays unchanged in responsibility', async () => {
  const cloud=await readFile(new URL('../src/cloud/health.js',import.meta.url),'utf8');
  assert.ok(cloud.includes("request('/health/overview', { signal, scope })"));
  assert.ok(!/\bread\(|local\.|localStorage|indexedDB|cache\.put/.test(cloud));
  const page=await readFile(new URL('../src/health/HealthOverview.jsx',import.meta.url),'utf8');
  assert.ok(!/setInterval|localStorage|sessionStorage|indexedDB|caches\./.test(page));
  const sw=await readFile(new URL('../src/cloud/service-worker.js',import.meta.url),'utf8');
  assert.ok(sw.includes('url.origin !== self.location.origin'));
});
