import test from 'node:test';
import assert from 'node:assert/strict';
import { analysisProgress, analysisRequestBody } from '../src/personal/analysisState.js';

test('short notice uses existing view without invented progress', () => assert.equal(analysisProgress({}), null));
test('actual processed progress clamps to total and keeps cache counts', () => {
  const p = analysisProgress({ analysis_state: 'running', progress: { phase: 'analyzing', processed: 3, total: 12, cached: 2, calls: 1 } });
  assert.equal(p.label, '分析区域'); assert.equal(p.completed, 3); assert.equal(p.total, 12); assert.equal(p.cached, 2);
  assert.equal(analysisProgress({ progress: { processed: 15, total: 12 } }).completed, 12);
});
test('partial progress includes failed and omitted areas', () => assert.equal(analysisProgress({ progress: { failed_regions: 2, omitted_regions: 3 } }).incomplete, 5));
test('cancel state preserves completed count', () => assert.equal(analysisProgress({ analysis_state: 'cancelled', progress: { processed: 3, total: 12 } }).state, 'cancelled'));
test('explicit budget and remaining scope are separate from force', () => {
  assert.deepEqual(analysisRequestBody(true, true, 'remaining'), { force: true, confirm_budget: true, scope: 'remaining' });
  assert.deepEqual(analysisRequestBody(false, false, 'auto'), { force: false, confirm_budget: false, scope: 'auto' });
});
