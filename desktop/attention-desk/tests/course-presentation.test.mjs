import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';

async function load(path) {
  const result = await build({entryPoints:[fileURLToPath(new URL(path,import.meta.url))],bundle:true,write:false,format:'esm',platform:'node',loader:{'.webp':'dataurl'}});
  return import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].contents).toString('base64'));
}
const {courseTheme}=await load('../src/personal/courseVisuals.js');
const {classDetails}=await load('../src/personal/coursePresentation.js');

test('Course name aliases and future subjects keep meaningful covers without binding record IDs',()=>{
  assert.equal(courseTheme({id:'future-id',name:'高等数学 II'}).key,'math');
  assert.equal(courseTheme('高等数学I1').key,'math');
  assert.equal(courseTheme('大学体育1').key,'sport');
  assert.equal(courseTheme('人工智能与计算机程序设计').key,'computing');
  assert.equal(courseTheme('工程图学1').key,'engineering');
  assert.equal(courseTheme('未来选修课').key,'general');
  assert.equal(courseTheme({name:'新课程',visualTheme:'history'}).key,'history');
  assert.equal(courseTheme({name:'新课程',visualTheme:'invalid'}).key,'general');
});

test('Current classroom data overrides source metadata; unknown rooms remain unknown',()=>{
  const uid='bf69f5cf4f3449c6e15e@openai-timetable';
  assert.equal(classDetails({uid,title:'工程图学1'}).location,'新教学楼B310');
  assert.equal(classDetails({uid,title:'工程图学1',location:'调课后的教室'}).location,'调课后的教室');
  assert.equal(classDetails({title:'晚修'}).location,'一教420');
  assert.equal(classDetails({title:'晚修',location:'数据库确认的新地点'}).location,'数据库确认的新地点');
  assert.equal(classDetails({title:'未知课程'}).location,'');
});
