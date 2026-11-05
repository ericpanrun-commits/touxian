import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';

test('Generated course artwork loads from the public shell cache offline; private image paths never enter it',async()=>{
  const origin='https://eric-fixture.example',events=new Map(),path='/assets/course-atlas-test.webp';
  const source=await readFile(new URL('../src/cloud/service-worker.js',import.meta.url),'utf8');
  let network=0;
  runInNewContext(source,{
    ERIC_SW:{cache:'eric-os-shell-artwork',files:[path],shell:[path],entry:'/assets/index.js'},URL,Request,Response,Headers,
    self:{location:{origin},addEventListener(name,handler){events.set(name,handler);}},
    caches:{async open(){return {async match(key){return key===path?new Response('cached-webp-bytes',{headers:{'Content-Type':'image/webp'}}):null;}};}},
    fetch(){network++;throw new Error('Offline');},
  });
  let response;
  events.get('fetch')({request:new Request(origin+path),respondWith(value){response=value;}});
  assert.equal(await (await response).text(),'cached-webp-bytes');assert.equal(network,0);
  let intercepted=false;
  events.get('fetch')({request:new Request(origin+'/api/private.webp'),respondWith(){intercepted=true;}});
  assert.equal(intercepted,false);
});

test('Legacy cleanup finishes activation without navigating any existing document',async()=>{
  const origin='https://eric-fixture.example',events=new Map(),removed=[];
  let claimed=false,rootNavigation=false,captureNavigation=false,finishNavigation;
  const navigation=new Promise(resolve=>{finishNavigation=resolve;});
  const oldHtml=new Response('Synthetic old shell',{headers:{'Content-Type':'text/html'}});
  Object.defineProperty(oldHtml,'redirected',{value:true});
  const clients=[{url:origin+'/',navigate(){rootNavigation=true;return navigation;}},
    {url:origin+'/?view=capture',navigate(){captureNavigation=true;return navigation;}}];
  const source=await readFile(new URL('../src/cloud/service-worker.js',import.meta.url),'utf8');
  runInNewContext(source,{
    ERIC_SW:{cache:'eric-os-shell-new',files:[],shell:[],entry:'/assets/new.js'},URL,Request,Response,Headers,
    self:{location:{origin},addEventListener(name,listener){events.set(name,listener);},clients:{async claim(){claimed=true;},async matchAll(){return clients;}}},
    caches:{async keys(){return ['eric-os-shell-old','eric-os-shell-new'];},async open(){return {async keys(){return [new Request(origin+'/index.html')];},async match(){return oldHtml;},async delete(request){removed.push(request.url);return true;}};},async delete(){return true;}},
  });
  let activation;
  events.get('activate')({waitUntil(promise){activation=promise;}});
  let timer;
  try{
    const completed=await Promise.race([activation.then(()=>true),new Promise(resolve=>{timer=setTimeout(()=>resolve(false),500);})]);
    assert.equal(completed,true,'Activation must not await a navigation that is waiting for activation itself');
    // A root client can already be an editing SPA tab. Its initial URL is not
    // permission to reload it, even when an old redirected shell is present.
    assert.equal(claimed,true);assert.equal(rootNavigation,false);assert.equal(captureNavigation,false);
    assert.deepEqual(removed,[origin+'/index.html']);
  }finally{clearTimeout(timer);finishNavigation();}
});
