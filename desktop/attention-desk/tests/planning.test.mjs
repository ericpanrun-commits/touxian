import test from 'node:test';
import assert from 'node:assert/strict';
import { monthDays, shiftDay, shiftMonth, itemsOnDay, groupEvents, deadlineDisplay } from '../src/personal/planningHelpers.js';

test('civil calendar month is Monday-first, includes leap day and full weeks', () => {
  const days = monthDays(2028, 2);
  assert.equal(days.length % 7, 0);
  assert.equal(new Date(days[0] + 'T12:00Z').getUTCDay(), 1);
  assert.ok(days.includes('2028-02-29'));
  assert.ok(days.includes('2028-01-31'));
});
test('civil arithmetic preserves dates across DST and year boundaries', () => {
  assert.equal(shiftDay('2026-03-08', 1), '2026-03-09');
  assert.equal(shiftDay('2026-12-31', 1), '2027-01-01');
  assert.deepEqual(shiftMonth(2026, 12, 1), { year: 2027, month: 1 });
  assert.deepEqual(shiftMonth(2027, 1, -1), { year: 2026, month: 12 });
});
test('date ranges appear on both inclusive boundaries and only those days', () => {
  const values = [{ id: 'range', start_date: '2026-12-31', end_date: '2027-01-02' }];
  assert.equal(itemsOnDay(values, '2026-12-31').length, 1);
  assert.equal(itemsOnDay(values, '2027-01-02').length, 1);
  assert.equal(itemsOnDay(values, '2027-01-03').length, 0);
});
test('multiple deadlines group once per event without losing order or identity', () => {
  const grouped = groupEvents([{ id: 'd1', event_id: 'e1', event_title: '事项一' }, { id: 'd2', event_id: 'e2', event_title: '事项二' }, { id: 'd3', event_id: 'e1', event_title: '事项一' }]);
  assert.deepEqual(grouped.map(value => value.id), ['e1', 'e2']);
  assert.deepEqual(grouped[0].items.map(value => value.id), ['d1', 'd3']);
});
test('date-only display does not parse timezone or invent a clock', () => {
  assert.equal(deadlineDisplay({ value: '2026-10-12', precision: 'date' }), '2026-10-12');
  assert.equal(deadlineDisplay({ value: '2026-10-12', end_value: '2026-10-15', precision: 'range', range_precision: 'date' }), '2026-10-12 — 2026-10-15');
  assert.equal(deadlineDisplay({ value: '', precision: 'unknown' }), '日期待确认');
});
