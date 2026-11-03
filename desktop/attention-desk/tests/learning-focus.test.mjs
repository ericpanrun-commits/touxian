import test from 'node:test';
import assert from 'node:assert/strict';
import { learningFocus } from '../src/personal/learningFocusModel.js';

const base=()=>({today:'2026-10-28',courses:[{id:'math',name:'高等数学 II'}],preview_sessions:[],weak:[],progress:[],due:[],upcoming:[],pending_captures:[]});
test('a catalogue entry alone does not attribute empty learning evidence to that course',()=>{
 const data=base();data.courses.unshift({id:'history',name:'中国近现代史纲要'});
 const view=learningFocus(data);assert.equal(view.course,'我的学习');assert.equal(view.courseId,undefined);
});
test('actual classroom ownership chooses the course even when catalogue order changes',()=>{
 const data=base();data.courses.unshift({id:'computing',name:'程序设计'});data.recent_lectures=[{id:'lecture-math',course_id:'math',lecture_date:data.today,notes_available:1}];
 const view=learningFocus(data);assert.equal(view.course,'高等数学 II');assert.equal(view.notesReceived,true);
});
test('focus groups both genuine retests on one date and never promotes tentative classroom improvement',()=>{
 const data=base();data.upcoming=['函数极限','等价无穷小'].map((title,i)=>({id:String(i),title,due_at:'2026-10-30T01:00:00+00:00',status:'ready'}));
 data.progress=[{id:'t',title:'等价',learning_state:{label:'在进步',retest_successes:0}}];
 const view=learningFocus(data);assert.equal(view.retests.length,1);assert.equal(view.retests[0].count,2);assert.equal(view.retests[0].day,'2026-10-30');assert.equal(view.progress.length,0);
});
test('first screen caps and deduplicates priorities and verified progress',()=>{
 const data=base();data.preview={course_id:'math',activity_date:'2026-10-28',weaknesses:[{topic_id:'same',title:'等价'}]};
 data.weak=[{id:'same',title:'等价'},...Array.from({length:7},(_,i)=>({id:String(i),title:'薄弱'+i}))];
 data.progress=Array.from({length:5},(_,i)=>({id:'p'+i,title:'进步'+i,learning_state:{retest_successes:1}}));
 const view=learningFocus(data);assert.equal(view.priorities.length,3);assert.equal(view.progress.length,2);assert.equal(new Set(view.priorities.map(t=>t.id)).size,3);
});
test('old imported activity never becomes today because it was recently received',()=>{
 const data=base();data.preview={course_id:'math',activity_date:'2026-10-26',received_at:'2026-10-28T12:00:00+08:00'};
 const view=learningFocus(data);assert.equal(view.isToday,false);assert.equal(view.day,'2026-10-26');
});
test('today notes without preview are visible without borrowing yesterday exercises',()=>{
 const data=base();data.preview={course_id:'math',activity_date:'2026-10-27',exercises:{total:8,correct:7,incorrect:1,unknown:0}};
 data.recent_lectures=[{id:'today',course_id:'math',lecture_date:'2026-10-28',notes_available:1}];
 const view=learningFocus(data);assert.equal(view.isToday,true);assert.equal(view.notesReceived,true);assert.equal(view.previewScore.total,0);
});
test('empty evidence and unresolved results stay honest',()=>{
 const data=base();assert.equal(learningFocus(data).priorities.length,0);
 data.preview={course_id:'math',activity_date:'2026-10-28',classroom_exercises:{total:11,correct:4,incorrect:0,unknown:7}};
 const view=learningFocus(data);assert.equal(view.classroomScore.unknown,7);assert.equal(view.progress.length,0);
});
