import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

// Compile the real shared component; no source-text surrogate or new dependency.
const output = new URL('../artifacts/qr-notice-tests/Evidence.mjs', import.meta.url);
await mkdir(new URL('.', output), {recursive:true});
const built = await build({entryPoints:[fileURLToPath(new URL('../src/personal/Evidence.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'esm',jsx:'automatic',packages:'external',define:{'import.meta.env':'{}'},plugins:[{
  name:'unused-pdf-renderer-for-ssr',setup(builder){
    // PDF has browser-only globals and is not rendered by these text-only snapshots.
    builder.onResolve({filter:/^\.\/PdfSourcePage$/},()=>({path:'pdf',namespace:'unused-pdf'}));
    builder.onLoad({filter:/.*/,namespace:'unused-pdf'},()=>({contents:'export default function PdfSourcePage(){return null}',loader:'js'}));
  },
}]});
await writeFile(output,built.outputFiles[0].contents);
const {OriginalNotice} = await import(output.href);
const hint = '需扫码，二维码请返回原微信群查看';
function render({wechat=true,warnings=[]}={}) {
  return renderToStaticMarkup(createElement(OriginalNotice,{snapshot:{original_text:'仅用于扫码提示回归的合成通知。',attachments:[],blocks:[],attachment_jobs:[],...(wechat?{wechat:{messages:[]}}:{}),document_warnings:warnings},onClear:()=>{}}));
}

test('v23 QR warning appears once on the actual shared original-notice component',()=>{
  const html=render({warnings:[{code:'qr_in_original_chat',message:'<script>untrusted</script>'},{code:'qr_in_original_chat'}]});
  assert.equal(html.split(hint).length-1,1);
  assert.match(html,/role="note"/);
  assert.ok(!html.includes('<script>untrusted</script>'));
});

test('ordinary notices and unrelated warnings do not acquire a QR instruction',()=>{
  for(const options of [{},{warnings:[{code:'other_warning'}]},{wechat:false,warnings:[{code:'qr_in_original_chat'}]}]) assert.ok(!render(options).includes(hint));
});
