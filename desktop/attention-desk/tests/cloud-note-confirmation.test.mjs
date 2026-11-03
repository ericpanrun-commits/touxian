import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';

const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/cloud/transport.js',import.meta.url))],bundle:true,write:false,format:'esm',platform:'neutral',plugins:[{
 name:'isolated-command-journal',setup(builder){
  builder.onResolve({filter:/^\.\/client$/},()=>({path:'client',namespace:'fixture'}));
  builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:`
   export const currentIdentity=()=>globalThis.noteFixture.identity||({owner:'owner-a'}),capture=()=>{},progress=()=>{},saveCommand=(...args)=>{globalThis.noteFixture.saved=args;return {queued:true};},fileUrl=()=>{},sourceResource=()=>{};
   export class CloudError extends Error {}
   export async function read(){return structuredClone(globalThis.noteFixture.record);}
   export const local={commands:{async get(id){return globalThis.noteFixture.commands.find(item=>item.id===id);},where(field){return {equals(value){return {filter(test){return {async first(){await globalThis.noteFixture.beforeCommands?.();return globalThis.noteFixture.commands.find(item=>item[field]===value&&test(item));},async toArray(){await globalThis.noteFixture.beforeCommands?.();return globalThis.noteFixture.commands.filter(item=>item[field]===value&&test(item));}};}};}};}}};
  `}));
 }
}]});
const {cloudRequest}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].contents).toString('base64'));

test('Identity changes during the local review-command lookup reject the old private read',async()=>{
 let resume,started;
 const gate=new Promise(resolve=>{resume=resolve;}),began=new Promise(resolve=>{started=resolve;});
 globalThis.noteFixture={record:{id:'review-a',lecture_id:'lesson-a',_cloud:{kind:'review',id:'review-a',revision:1}},commands:[],beforeCommands:()=>{started();return gate;}};
 const pending=cloudRequest('/school/reviews/review-a');await began;
 globalThis.noteFixture.identity={owner:'owner-b'};resume();await assert.rejects(pending,/登录设备已变化/);
});

test('A new account cannot reuse an old account record to queue an edit',async()=>{
 globalThis.noteFixture={record:{id:'note-private',_cloud:{kind:'learning_capture',id:'note-private',revision:1}},commands:[]};
 await cloudRequest('/learning/captures/note-private');globalThis.noteFixture.identity={owner:'owner-b'};
 await assert.rejects(cloudRequest('/learning/captures/note-private/confirm',{method:'POST',body:{}}),/读取最新内容/);
 assert.equal(globalThis.noteFixture.saved,undefined);
});
test('Reopening an unresolved note restores its own committed pending confirmation',async()=>{
 const record={id:'note-a',context:{note_share:true,course_id:null},_cloud:{kind:'learning_capture',id:'note-a',revision:4}};
 const body={course_id:'course-math-ii',lecture_id:'existing-lesson',lecture_date:'2026-10-28'};
 globalThis.noteFixture={record,commands:[
  {id:'other-owner',owner:'owner-b',resourceKey:'learning_capture:note-a',state:'local_pending',body:{course_id:'private-other-course'}},
  {id:'other-note',owner:'owner-a',resourceKey:'learning_capture:note-b',state:'pending',body:{}},
  {id:'old-failure',owner:'owner-a',resourceKey:'learning_capture:note-a',state:'failed',body:{}},
  {id:'same-confirmation',owner:'owner-a',resourceKey:'learning_capture:note-a',state:'local_pending',body}
 ]};
 const value=await cloudRequest('/learning/captures/note-a');
 assert.deepEqual(value.pending_confirmation,{id:'same-confirmation',body});
 assert.equal(value._cloud.revision,4);assert.equal(record.pending_confirmation,undefined);
 globalThis.noteFixture.commands.at(-1).state='completed';
 assert.equal((await cloudRequest('/learning/captures/note-a')).pending_confirmation,undefined);
});

test('Confirmation UI observes the same committed command state that controls duplicate submission',async()=>{
 const id='55555555-5555-4555-8555-555555555555';
 globalThis.noteFixture={record:null,commands:[{id,owner:'owner-a',state:'pending',server:{id,state:'completed',result:{status:'completed'}}}]};
 assert.equal((await cloudRequest('/commands/'+id)).state,'pending');
 globalThis.noteFixture.commands[0].state='completed';
 const completed=await cloudRequest('/commands/'+id);
 assert.equal(completed.state,'completed');assert.equal(completed.result.status,'completed');
});

test('Editing pins its original revision even after a newer read arrives',async()=>{
 globalThis.noteFixture={record:{id:'note-a',_cloud:{kind:'learning_capture',id:'note-a',revision:9}},commands:[]};
 await cloudRequest('/learning/captures/note-a');
 const path='/learning/captures/note-a/confirm',body={course_id:'course-math-ii'};
 await cloudRequest(path,{method:'POST',body,expectedRevision:4});
 assert.equal(globalThis.noteFixture.saved[2].expectedRevision,4);
 assert.equal(globalThis.noteFixture.saved[2].target,'learning_capture:note-a');
 await cloudRequest(path,{method:'POST',body});
 assert.equal(globalThis.noteFixture.saved[2].expectedRevision,9);
});
