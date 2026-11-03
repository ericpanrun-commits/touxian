import test from 'node:test';
import assert from 'node:assert/strict';
import {createPdfDocumentSession} from '../src/personal/pdfDocumentSession.js';

const source=(page,values={})=>({scope:'account-a:device-a',resource:'private-book',url:'https://private.example.test/ticket-a',page,...values});
function loader(){
  const tasks=[];
  const load=()=>{const task={promise:Promise.resolve({id:tasks.length+1}),destroys:0,destroy(){this.destroys++;return Promise.resolve();}};tasks.push(task);return task;};
  return {load,tasks};
}

test('One viewer reuses an in-flight and loaded document across page changes',async()=>{
  const session=createPdfDocumentSession(),r=loader();
  const first=session.get(source(1),r.load),second=session.get(source(2),r.load);
  assert.equal(first,second);assert.equal(await first,await second);
  assert.equal(await session.get(source(1),r.load),await first);
  assert.equal(r.tasks.length,1);await session.clear();assert.equal(r.tasks[0].destroys,1);
  await session.get(source(1),r.load);assert.equal(r.tasks.length,2);await session.clear();
});

for(const field of ['scope','resource','url'])test('A changed '+field+' retires the old document before loading another',async()=>{
  const session=createPdfDocumentSession(),r=loader();await session.get(source(1),r.load);
  let close; r.tasks[0].destroy=function(){this.destroys++;return new Promise(resolve=>{close=resolve;});};
  const pending=session.get(source(1,{[field]:'changed'}),r.load);
  await new Promise(resolve=>setImmediate(resolve));assert.equal(r.tasks.length,1);
  close();await pending;assert.equal(r.tasks.length,2);await session.clear();
});

test('Closing while a document loads cannot revive it or start a stale loader',async()=>{
  const session=createPdfDocumentSession(),r=loader();
  const stale=session.get(source(1),r.load);await session.clear();
  await assert.rejects(stale,{name:'AbortError'});assert.equal(r.tasks.length,0);
  let finish;
  const pending=session.get(source(1),()=>{const task=r.load();task.promise=new Promise(resolve=>{finish=resolve;});return task;});
  await new Promise(resolve=>setImmediate(resolve));await session.clear();finish({old:true});
  await assert.rejects(pending,{name:'AbortError'});assert.equal(r.tasks[0].destroys,1);
  await session.get(source(1),r.load);assert.equal(r.tasks.length,2);await session.clear();
});

test('Private documents are not shared between separately mounted viewers',async()=>{
  const first=createPdfDocumentSession(),second=createPdfDocumentSession(),r=loader();
  await Promise.all([first.get(source(1),r.load),second.get(source(1),r.load)]);
  assert.equal(r.tasks.length,2);await Promise.all([first.clear(),second.clear()]);
});

test('Distinct-page, byte-progress and age bounds rotate at a read boundary',async()=>{
  let clock=0;const session=createPdfDocumentSession({now:()=>clock,maxPages:2,maxLoadedBytes:100,maxAgeMs:1000}),r=loader();
  await session.get(source(1),r.load);await session.get(source(2),r.load);await session.get(source(1),r.load);
  assert.equal(r.tasks.length,1);await session.get(source(3),r.load);assert.equal(r.tasks.length,2);
  r.tasks[1].onProgress({loaded:100});await session.get(source(3),r.load);assert.equal(r.tasks.length,3);
  // A late progress event from a retired task cannot expire its replacement.
  r.tasks[1].onProgress({loaded:10000});await session.get(source(3),r.load);assert.equal(r.tasks.length,3);
  clock=1000;await session.get(source(3),r.load);assert.equal(r.tasks.length,4);await session.clear();
});

test('A failed load is disposed and stays failed until an explicit new read',async()=>{
  const session=createPdfDocumentSession(),r=loader();
  await assert.rejects(session.get(source(1),()=>{const task=r.load();task.promise=Promise.reject(new Error('source unavailable'));return task;}),/source unavailable/);
  await new Promise(resolve=>setImmediate(resolve));assert.equal(r.tasks.length,1);assert.equal(r.tasks[0].destroys,1);
  await session.get(source(1),r.load);assert.equal(r.tasks.length,2);await session.clear();
});
