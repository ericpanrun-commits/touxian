import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';

Object.defineProperty(globalThis,'navigator',{configurable:true,value:{userAgent:'Windows',platform:'Win32',onLine:true}});
globalThis.window={addEventListener(){},removeEventListener(){},dispatchEvent(){}};
const fixture=globalThis.privateReadFixture={};
const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/cloud/client.js',import.meta.url))],bundle:true,write:false,format:'esm',platform:'neutral',plugins:[{
  name:'isolated-private-read',setup(builder){
    builder.onResolve({filter:/^(?:@supabase\/supabase-js|dexie|@noble\/hashes\/sha2\.js|\.\/config|\.\/binaryStore)$/},args=>({path:args.path,namespace:'private-read'}));
    builder.onLoad({filter:/.*/,namespace:'private-read'},args=>({loader:'js',contents:args.path==='@supabase/supabase-js'?`
      export const createClient=()=>({auth:{async getSession(){return {data:{session:globalThis.privateReadFixture.session},error:null};},
        async refreshSession(){const f=globalThis.privateReadFixture;f.refreshes++;f.session={...f.session,access_token:'refreshed-token'};return {data:{session:f.session},error:null};},
        async signOut(){globalThis.privateReadFixture.session=null;return {error:null};}}});
    `:args.path==='dexie'?`
      export default class Dexie {constructor(){this.identity={async get(){return globalThis.privateReadFixture.saved;},async put(value){globalThis.privateReadFixture.saved=structuredClone(value);},async delete(){globalThis.privateReadFixture.saved=null;}};
        this.cache={async put(value){const f=globalThis.privateReadFixture;f.writes++;f.cache.set(JSON.stringify([value.owner,value.path]),structuredClone(value));},async get(key){return globalThis.privateReadFixture.cache.get(JSON.stringify(key));}};
        this.drafts={async put(value){const f=globalThis.privateReadFixture;await f.beforeDraftPut?.();f.drafts.set(JSON.stringify([value.owner,value.form]),structuredClone(value));},async get(key){return globalThis.privateReadFixture.drafts.get(JSON.stringify(key));},async delete(key){globalThis.privateReadFixture.drafts.delete(JSON.stringify(key));}};
        this.commands={where(){return {equals(owner){return {filter(predicate){return {async first(){const f=globalThis.privateReadFixture;await f.beforeCommandRead?.();return f.commands.find(item=>item.owner===owner&&predicate(item));}};}};}};},async add(value){const f=globalThis.privateReadFixture;await f.beforeCommandAdd?.();f.commands.push(structuredClone(value));}};}
        version(){return {stores:()=>this};}}
    `:args.path==='./config'?`export const API='https://api.example.test',SUPABASE_URL='https://unused.example.test',SUPABASE_PUBLISHABLE_KEY='public-fixture',UPLOAD_PART_BYTES=4000000;`
      :args.path==='./binaryStore'?`export const storeFile=()=>{},fileReference=()=>{},requireFile=()=>{},readFilePart=()=>{},releaseUnusedFile=()=>{},requestPersistentStorage=()=>{};`
      :`export const sha256={};`}));
  }
}]});
const api=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].contents).toString('base64'));
globalThis.fetch=async(url,options)=>{
  fixture.fetches.push(url);
  if(url.endsWith('/session'))return Response.json({device:{id:fixture.device},account:{email:fixture.session.user.id+'@example.test'}});
  return fixture.handler(url,options);
};
async function reset(){
  Object.assign(fixture,{session:{user:{id:'owner-a'},access_token:'original-token'},device:'device-a',saved:null,cache:new Map(),drafts:new Map(),commands:[],beforeDraftPut:null,beforeCommandRead:null,beforeCommandAdd:null,fetches:[],writes:0,refreshes:0,
    handler:()=>Response.json({id:'private-book'})});
  await api.restoreIdentity();
}
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}

test('A delayed editor writer cannot attach old text to a different owner or device',async()=>{
  await reset();const context=await api.editorDraft('source-correction:review',{action:'context'});
  await api.editorDraft('source-correction:review',{action:'write',context,value:{text:'owner-a draft'}});
  fixture.device='replacement-device';await api.restoreIdentity();
  await assert.rejects(api.editorDraft('source-correction:review',{action:'write',context,value:{text:'stale'}}),{code:'identity_changed'});
  assert.equal(await api.editorDraft('source-correction:review'),null);
  assert.equal(fixture.drafts.size,1);
});

test('An identity change while looking up queued edits stores no command',async()=>{
  await reset();const waiting=deferred(),started=deferred();
  fixture.beforeCommandRead=()=>{started.resolve();return waiting.promise;};
  const save=api.saveCommand('/school/reviews/review/source-correction',{text:'private'},{target:'lecture:test',expectedRevision:1});
  await started.promise;fixture.session={user:{id:'owner-b'},access_token:'other'};fixture.device='device-b';await api.restoreIdentity();
  waiting.resolve();await assert.rejects(save,{code:'identity_changed'});assert.equal(fixture.commands.length,0);
});

test('An already-started edit transaction retains its original owner across sign-in changes',async()=>{
  await reset();const waiting=deferred(),started=deferred();
  fixture.beforeCommandAdd=()=>{started.resolve();return waiting.promise;};
  const save=api.saveCommand('/school/reviews/review/source-correction',{text:'private'},{target:'lecture:test',expectedRevision:1});
  await started.promise;fixture.session={user:{id:'owner-b'},access_token:'other'};fixture.device='device-b';await api.restoreIdentity();
  waiting.resolve();await assert.rejects(save,{code:'identity_changed'});
  assert.equal(fixture.commands.length,1);assert.equal(fixture.commands[0].owner,'owner-a');
});

test('An old in-flight response never enters the new account or returns its cached data',async()=>{
  await reset();const waiting=deferred(),started=deferred();
  fixture.handler=()=>{started.resolve();return waiting.promise;};
  const read=api.read('/documents/book');await started.promise;
  fixture.session={user:{id:'owner-b'},access_token:'other-account'};fixture.device='device-b';await api.restoreIdentity();
  waiting.resolve(Response.json({private:'owner-a'}));await assert.rejects(read,{code:'identity_changed'});
  assert.equal(fixture.writes,0);assert.equal(fixture.cache.size,0);
});

test('An SDK-account change is rejected before a private fetch or offline fallback',async()=>{
  await reset();await api.read('/documents/book');const count=fixture.fetches.length;
  fixture.session={user:{id:'owner-b'},access_token:'other-account'};
  await assert.rejects(()=>api.read('/documents/book'),{code:'identity_changed'});
  assert.equal(fixture.fetches.length,count);
});

test('Device replacement discards the pending old ticket and requires a new one',async()=>{
  await reset();const waiting=deferred(),started=deferred();
  fixture.handler=()=>{started.resolve();return waiting.promise;};
  const old=api.fileUrl('private-book');await started.promise;
  fixture.device='replacement-device';await api.restoreIdentity();
  waiting.resolve(Response.json({url:'https://private.example.test/old-device'}));
  await assert.rejects(old,{code:'identity_changed'});
  fixture.handler=()=>Response.json({url:'https://private.example.test/replacement-device'});
  assert.equal(await api.fileUrl('private-book'),'https://private.example.test/replacement-device');
});

test('Same-account temporary failure retains cached reads but revoked access does not',async()=>{
  await reset();await api.read('/documents/book');
  fixture.handler=()=>{throw new TypeError('offline');};
  const cached=await api.read('/documents/book');assert.equal(cached.id,'private-book');assert.ok(cached._offline);
  fixture.handler=()=>Response.json({detail:{code:'device_revoked',message:'Revoked test device'}},{status:403});
  await assert.rejects(()=>api.read('/documents/book'),{code:'device_revoked',status:403});
});

test('Access-token refresh preserves the same owner/device read and retries only once',async()=>{
  await reset();let calls=0;
  fixture.handler=()=>++calls===1?Response.json({detail:{code:'sign_in_required'}},{status:401}):Response.json({id:'same-private-book'});
  assert.equal((await api.read('/documents/book')).id,'same-private-book');
  assert.equal(fixture.refreshes,1);assert.equal(calls,2);assert.equal(api.currentIdentity().device.id,'device-a');
});
