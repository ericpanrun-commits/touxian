import assert from 'node:assert/strict';
import test from 'node:test';
import { roleLabel, splitContextList, displayWechatTime, WECHAT_STATUS } from '../src/personal/wechatHelpers.js';
import { reviewDraft, wechatConfirmation } from '../src/personal/reviewHelpers.js';

test('unknown relevance is distinct from an explicit role exclusion', () => {
  assert.equal(roleLabel(null), '相关性待核对');
  assert.equal(roleLabel(false), '角色或范围不符');
  assert.notEqual(roleLabel(true), roleLabel(false));
});

test('context lists are explicit unique user values', () => {
  assert.deepEqual(splitContextList('课程一、课程二\n课程一, 项目甲'), ['课程一', '课程二', '项目甲']);
});

test('Wechat timestamps explicitly use Shanghai and missing stays missing', () => {
  assert.equal(displayWechatTime(null), '尚无记录');
  assert.match(displayWechatTime('2026-10-21T22:00:00Z'), /6:00|06:00/);
  assert.match(WECHAT_STATUS.quiet, /06:00/);
});

test('uncertain dates never start selected, ranges retain their endpoint', () => {
  const base = { id: 'e', title: '通知', summary: '', category: 'notice', participation: 'required', notice_kind: 'new', audience: null, eligibility: [], materials: [], locations: [], uncertainties: [], actions: [] };
  const bundle = { title_hint: '', extraction: { parsed_output: { evidence: [], events: [{ ...base, deadlines: [
    { id: 'd1', role: '待核对', normalized_date: null, precision: 'unknown', relation: 'current', uncertain: true, original_text: '今晚12点', evidence_ids: [] },
    { id: 'd2', role: '活动范围', normalized_date: '2026-10-22', end_value: '2026-10-25', range_precision: 'date', precision: 'range', relation: 'current', uncertain: false, original_text: '活动期间', evidence_ids: [] },
  ] }] } } };
  const draft = reviewDraft(bundle);
  assert.equal(draft.events[0].deadlines[0].selected, false);
  assert.equal(draft.events[0].deadlines[1].end_value, '2026-10-25');
  assert.equal(draft.events[0].participation, 'later');
});

function focusedReview() {
  const event = id => ({ id, title: id, summary: '', category: 'notice', notice_kind: 'new', audience: null, eligibility: [], materials: [], locations: [], uncertainties: [], deadlines: [], actions: [] });
  return { version: 4, extraction_id: 'ex', wechat_candidates: [
    { id: 'ca', model_item_id: 'a', status: 'needs_review' },
    { id: 'cb', model_item_id: 'b', status: 'accepted' },
    { id: 'cc', model_item_id: 'c', status: 'needs_review' },
  ], extraction: { validation: { warnings: [] }, parsed_output: { evidence: [], events: ['a','b','c'].map(event) } } };
}

test('candidate review excludes handled siblings and recovers the unsaved sibling from extraction', () => {
  const bundle = focusedReview();
  bundle.draft = { events: [{ id: 'a', title: '我已保存的改动' }] };
  assert.equal(reviewDraft(bundle, 'ca').events[0].title, '我已保存的改动');
  assert.deepEqual(reviewDraft(bundle, 'cc').events.map(event => event.id), ['c']);
  assert.deepEqual(reviewDraft(bundle, 'cb').events, []);
});

test('quick confirmation preserves a human draft and only confirms the intended candidate', () => {
  const bundle = focusedReview();
  const candidate = { id: 'cc', model_item_id: 'c', analysis: { relevant: true, action_required: true, participation: 'required' } };
  bundle.draft = { events: [{ id: 'a', title: '兄弟候选的草稿' }] };
  const payload = wechatConfirmation(bundle, candidate);
  assert.deepEqual(payload.events.map(event => event.id), ['c']);
  assert.equal(payload.events[0].relevance, 'yes');
  assert.equal(payload.expected_version, 4);
  bundle.draft.events.push({ id: 'c', title: '人工修改不能被覆盖' });
  assert.equal(wechatConfirmation(bundle, candidate), null);
});

test('uncertain role or date routes to editing rather than manufacturing a confirmed value', () => {
  const bundle = focusedReview();
  const candidate = { id: 'ca', model_item_id: 'a', analysis: { relevant: null, action_required: true, participation: 'required' } };
  assert.equal(wechatConfirmation(bundle, candidate), null);
  candidate.analysis.relevant = true;
  bundle.extraction.parsed_output.events[0].deadlines.push({ id: 'unclear', relation: 'current', normalized_date: null, uncertain: true, evidence_ids: [] });
  assert.equal(wechatConfirmation(bundle, candidate), null);
});
