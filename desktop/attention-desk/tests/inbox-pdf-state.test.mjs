import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {inboxCard as localCard} from '../src/inboxCard.js';

const compiled=await build({entryPoints:[fileURLToPath(new URL('../supabase/functions/eric-app-api/views.ts',import.meta.url))],bundle:true,write:false,format:'esm',platform:'neutral',target:'es2022'});
const {inboxCard:cloudCard,presentInbox}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].contents).toString('base64'));
const pdf={input_kind:'pdf',preview:'等待 AI 分析…',preview_source:'waiting',attachments:[{filename:'note.pdf',mime_type:'application/pdf'}]};

for(const [name,card] of [['local',localCard],['cloud',cloudCard]]) {
  test(name+' shows saved review despite preserved old waiting preview',()=>{
    const value={...pdf,note_processing:{status:'completed',summary:'Saved analysis',message:'笔记分析已完成，复盘待核对。',review_id:'existing-review'}};
    const before=JSON.stringify(value);
    assert.equal(card(value).body,'Saved analysis');
    assert.equal(card(value).summary,'Saved analysis');
    assert.equal(JSON.stringify(value),before);
  });
  for(const [state,message] of [['needs_context','请确认课程。'],['paused','AI 处理已暂停。'],['failed','PDF 提取失败。'],['needs_review','原件需要核对。']]) {
    test(name+' exposes '+state+' reason',()=>assert.equal(card({...pdf,note_processing:{status:state,message}}).body,message));
  }
  test(name+' does not invent a job for an unprocessed PDF',()=>{
    assert.equal(card(pdf).body,'PDF 已保存，尚无已确认的 AI 分析结果。');
    assert.equal(card({input_kind:'file',files:[{mime:'application/pdf'}]}).body,'PDF 已保存，尚无已确认的 AI 分析结果。');
    assert.equal(card({...pdf,processing_status:'failed',error:{message:'真实失败原因'}}).body,'真实失败原因');
  });
  test(name+' preserves confirmed human and completed image analysis',()=>{
    const note_processing={status:'needs_context',message:'请确认课程。'};
    assert.equal(card({...pdf,note_processing,preview_source:'human',preview:'Confirmed'}).body,'Confirmed');
    assert.equal(card({...pdf,note_processing,vision_status:'completed',vision_analysis:{summary:'Vision result'}}).body,'Vision result');
  });
}

test('cloud list/detail presentation survives refresh without exposing OCR',()=>{
  const saved={...pdf,note_processing:{status:'needs_context',message:'请确认课程。'},review:{snapshot:{original_text:'unverified OCR'}}};
  const first=presentInbox(saved,new URL('https://test.local/inbox'));
  assert.equal(first.preview,'请确认课程。');
  assert.equal(first.review.snapshot.original_text,undefined);
  saved.note_processing={status:'completed',message:'笔记分析已完成，复盘待核对。',summary:'Saved result'};
  assert.equal(presentInbox(saved,new URL('https://test.local/inbox')).preview,'Saved result');
  assert.equal(presentInbox(saved,new URL('https://test.local/inbox/id')).preview,'Saved result');
});

test('one-call classification reuses cloud fields and never becomes a waiting placeholder',()=>{
  const classification={note_mode:'note-course-tags-v1',note_classification:{status:'completed',course_id:'course-math-ii',knowledge_point:'导数',tags:['求导'],summary:null}};
  const saved={...pdf,classification,course:{id:'course-math-ii',name:'高等数学'},note_processing:{mode:'note-course-tags-v1',status:'completed',summary:'导数',message:'已分类'}};
  for(const path of ['/inbox','/inbox/id']) {
    const shown=presentInbox(saved,new URL('https://test.local'+path));
    assert.equal(shown.preview,'导数');
    assert.deepEqual(shown.classification,classification);
    assert.equal(shown.course.id,'course-math-ii');
    assert.equal(shown.attachments[0].mime_type,'application/pdf');
  }
});
