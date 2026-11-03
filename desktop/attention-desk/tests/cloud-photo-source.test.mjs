import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';

const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/cloud/transport.js',import.meta.url))],bundle:true,write:false,format:'esm',platform:'neutral',plugins:[{
  name:'isolated-cloud-client',setup(builder){
    builder.onResolve({filter:/^\.\/client$/},()=>({path:'cloud-client',namespace:'fixture'}));
    builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:`
      export const local={},currentIdentity=()=>('identity' in globalThis.photoSourceFixture?globalThis.photoSourceFixture.identity:{owner:'owner-a',device:{id:'device-a'}}),capture=()=>{},progress=()=>{},saveCommand=()=>{},sourceResource=()=>null;
      export class CloudError extends Error {constructor(message,code,status){super(message);this.code=code;this.status=status;}}
      export async function read(path){const f=globalThis.photoSourceFixture;f.reads.push(path);if(f.onRead)await f.onRead();return f.document;}
      export async function fileUrl(resource,options){const f=globalThis.photoSourceFixture;f.tickets.push({resource,options});if(f.onTicket)await f.onTicket();return 'https://private.example.test/'+resource;}
    `,loader:'js'}));
  }
}]});
const {cloudPdfSource}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].contents).toString('base64'));

test('Photo notes use the same original private resource and are never sent to the PDF renderer',async()=>{
  const r=globalThis.photoSourceFixture={reads:[],tickets:[],document:{id:'existing-document',attachment_id:'existing-image',mime_type:'image/png',page_count:1}};
  assert.deepEqual(await cloudPdfSource({document_id:'existing-document',page:1},{force:true}),{
    url:'https://private.example.test/attachment:existing-image:original',resource:'attachment:existing-image:original',page:1,image:true,scope:'["owner-a","device-a"]'
  });
  assert.deepEqual(r.reads,['/documents/existing-document']);
  assert.deepEqual(r.tickets,[{resource:'attachment:existing-image:original',options:{force:true}}]);
  assert.equal(await cloudPdfSource({document_id:'existing-document',page:2}),null);
  assert.equal(r.tickets.length,1);
});

test('Existing PDF reading indexes and original-PDF fallback keep their behavior',async()=>{
  const r=globalThis.photoSourceFixture={reads:[],tickets:[],document:{id:'book',attachment_id:'existing-pdf',page_count:300,reading_resource_id:'pdf-view:existing-index'}};
  assert.deepEqual(await cloudPdfSource({document_id:'book',page:210}),{
    url:'https://private.example.test/pdf-view:existing-index',resource:'pdf-view:existing-index',page:210,scope:'["owner-a","device-a"]'
  });
  delete r.document.reading_resource_id;
  assert.equal((await cloudPdfSource({document_id:'book',page:1})).resource,'attachment:existing-pdf:original');
  assert.equal(await cloudPdfSource({document_id:'book',page:0}),null);
});

test('A source lookup cannot return an old account or device URL after either await',async()=>{
  for(const point of ['onRead','onTicket']){
    const f=globalThis.photoSourceFixture={reads:[],tickets:[],document:{attachment_id:'private-original',page_count:3}};
    f[point]=()=>{f.identity={owner:'owner-b',device:{id:'device-b'}};};
    await assert.rejects(()=>cloudPdfSource({document_id:'book',page:1}),{code:'identity_changed'});
    if(point==='onRead')assert.equal(f.tickets.length,0);
  }
});

test('A logged-out source lookup never mints a file URL',async()=>{
  const f=globalThis.photoSourceFixture={identity:null,reads:[],tickets:[]};
  await assert.rejects(()=>cloudPdfSource({document_id:'book',page:1}),{code:'sign_in_required'});
  assert.equal(f.reads.length+f.tickets.length,0);
});
