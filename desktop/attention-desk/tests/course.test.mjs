import assert from 'node:assert/strict';
import test from 'node:test';
import { emptyEvent, reviewDraft, wechatConfirmation } from '../src/personal/reviewHelpers.js';

function bundle() {
  return {
    version: 4, extraction_id: 'extraction',
    course_suggestions: { candidate: { id: 'course-math-ii', name: '高等数学 II' } },
    wechat_candidates: [{ id: 'wechat-candidate', model_item_id: 'candidate', status: 'needs_review' }],
    extraction: { validation: { warnings: [] }, parsed_output: { evidence: [], events: [{
      id: 'candidate', title: '高数作业', summary: '', category: '课程', notice_kind: 'new',
      audience: null, eligibility: [], materials: [], locations: [], uncertainties: [], deadlines: [], actions: [],
    }] } },
  };
}

test('new and extracted matters leave course empty until the user chooses', () => {
  assert.equal(emptyEvent('普通事项').course_id, null);
  const source = bundle();
  assert.equal(reviewDraft(source).events[0].course_id, null);
  assert.equal(reviewDraft(source, 'wechat-candidate').events[0].course_id, null);
  assert.equal(source.course_suggestions.candidate.id, 'course-math-ii');
});

test('saved course choice survives review without mutating the saved draft', () => {
  const source = bundle();
  source.draft = reviewDraft(source);
  source.draft.events[0].course_id = 'course-math-ii';
  const reopened = reviewDraft(source, 'wechat-candidate');
  assert.equal(reopened.events[0].course_id, 'course-math-ii');
  reopened.events[0].course_id = null;
  assert.equal(source.draft.events[0].course_id, 'course-math-ii');
});

test('Inbox human association prefills review but never adopts a machine suggestion', () => {
  const source = bundle();
  source.item = { inbox_course_id: 'course-math-ii' };
  assert.equal(reviewDraft(source).events[0].course_id, 'course-math-ii');
  source.draft = reviewDraft(source);
  source.draft.events[0].course_id = null;
  assert.equal(reviewDraft(source).events[0].course_id, null);
  delete source.draft;
  source.extraction = null;
  delete source.wechat_candidates;
  assert.equal(reviewDraft(source).events[0].course_id, 'course-math-ii');
});

test('quick WeChat confirm never silently adopts a suggested course or overwrites a human choice', () => {
  const source = bundle();
  const candidate = { id: 'wechat-candidate', model_item_id: 'candidate', analysis: {
    relevant: true, action_required: true, participation: 'required',
  } };
  const payload = wechatConfirmation(source, candidate);
  assert.equal(payload.events[0].course_id, null);
  source.draft = reviewDraft(source);
  source.draft.events[0].course_id = 'course-math-ii';
  assert.equal(wechatConfirmation(source, candidate), null);
});
