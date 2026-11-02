import test from 'node:test';
import assert from 'node:assert/strict';
import { FAILED_COPY, LOW_COPY, WAITING_COPY, inboxCard } from '../src/inboxCard.js';

const OCR = '1： 35PM\n\n1\n\nWed Oct OCR';
const image = {
  id: 'e32765f5-43c9-4a20-8e50-d7afc9e7c773',
  title: 'IMG_1080.png',
  input_kind: 'image',
  preview: OCR,
  course: { name: '高等数学 II' },
  attachments: [{ filename: 'IMG_1080.png', mime_type: 'image/png', extension: 'png' }],
  review: { snapshot: { original_text: OCR + ' longer' } },
};

test('IMG_1080 list body waits instead of showing OCR preview', () => {
  const card = inboxCard(image);
  assert.equal(card.body, WAITING_COPY);
  assert.equal(card.bodySource, 'waiting');
  assert.equal(card.course, '高等数学 II');
  assert.equal(card.body.includes('35PM'), false);
});

test('completed vision supplies summary, counts, and hides OCR', () => {
  const card = inboxCard({
    ...image,
    vision_status: 'completed',
    vision_confidence: 'high',
    vision_analysis: { title: '极限复习', summary: '导数定义', key_points: ['极限'], equations: ['f\'(x)'], probable_course: '高等数学 II' },
  });
  assert.equal(card.body, '导数定义');
  assert.equal(card.title, '极限复习');
  assert.equal(card.keyPointCount, 1);
  assert.equal(card.equationCount, 1);
  assert.equal(card.body.includes('35PM'), false);
});

test('failed and low confidence use the fixed copy', () => {
  assert.equal(inboxCard({ ...image, vision_status: 'failed' }).body, FAILED_COPY);
  assert.equal(inboxCard({ ...image, vision_status: 'completed', vision_confidence: 'low', vision_analysis: { summary: '不确定' } }).body, LOW_COPY);
});

test('human confirmed preview stays, plain text is unchanged', () => {
  assert.equal(inboxCard({ ...image, preview_source: 'human', preview: '我确认的笔记' }).body, '我确认的笔记');
  assert.equal(inboxCard({ input_kind: 'text', preview: '明天下午交作业' }).body, '明天下午交作业');
});
