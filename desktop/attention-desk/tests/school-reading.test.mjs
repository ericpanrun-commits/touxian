import test from 'node:test';
import assert from 'node:assert/strict';
import { preferredLectureReview, reviewStateLabel } from '../src/personal/schoolReading.js';

const versions=[
  {id:'new-incomplete',version:3,status:'needs_review',has_content:false},
  {id:'ready-new',version:2,status:'needs_review',has_content:true},
  {id:'adopted-old',version:1,status:'accepted',has_content:true},
];

test('a newer failed version cannot hide the latest readable review',()=>{
  assert.equal(preferredLectureReview({artifacts:versions}),'ready-new');
  assert.equal(reviewStateLabel(versions[0],{needs_review:'待核对'}),'未完成');
  assert.equal(reviewStateLabel(versions[1],{needs_review:'待核对'}),'待核对');
});

test('adoption and explicit reading choice have distinct priority',()=>{
  const lecture={artifacts:versions,accepted_artifact_id:'adopted-old'};
  assert.equal(preferredLectureReview(lecture),'adopted-old');
  assert.equal(preferredLectureReview(lecture,'ready-new'),'ready-new');
  assert.equal(preferredLectureReview(lecture,'new-incomplete'),'new-incomplete');
  assert.equal(preferredLectureReview({...lecture,accepted_artifact_id:'new-incomplete'}),'ready-new');
});

test('an explicit queued result survives a stale lecture list until it refreshes',()=>{
  assert.equal(preferredLectureReview({artifacts:versions},'not-in-cached-list-yet'),'not-in-cached-list-yet');
});

test('legacy and empty summaries do not invent a readable version',()=>{
  assert.equal(preferredLectureReview({artifacts:[{id:'legacy',status:'needs_review'}]}),'legacy');
  assert.equal(preferredLectureReview({artifacts:[versions[0]]}),'new-incomplete');
  assert.equal(preferredLectureReview({artifacts:[]}),null);
});
