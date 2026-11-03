// Browser plugin not available. Exercise a real old production worker and
// the staged worker, without any live authentication, API or learner writes.
import {chromium,webkit,expect} from 'playwright/test';
import {createServer} from 'node:http';
import {readFile,mkdir,mkdtemp,writeFile,readdir} from 'node:fs/promises';
import {resolve,sep,extname} from 'node:path';
import assert from 'node:assert/strict';

const oldRoot=resolve(process.env.ERIC_OLD_WEB_ROOT||''),newRoot=resolve('dist/client');
if(!process.env.ERIC_OLD_WEB_ROOT||oldRoot===newRoot)throw new Error('An isolated extracted published build is required');
const output=resolve(process.env.ERIC_SW_UPGRADE_EVIDENCE||'D:/Eric OS/backups/web-pwa-v1-20261029/course-workspaces/worker-upgrade');await mkdir(output,{recursive:true});
let root=oldRoot,apiRequests=0,authRequests=0;
const requests=[],errors=[];
const server=createServer(async(req,res)=>{
  const url=new URL(req.url,'http://127.0.0.1');requests.push(url.pathname);
  const send=(status,type,body,extra={})=>{res.writeHead(status,{'Content-Type':type,'Cache-Control':'no-store',...extra});res.end(body);};
  if(url.pathname==='/prime')return send(200,'text/html','<!doctype html><title>Isolated upgrade</title>');
  if(url.pathname==='/index.html'||url.pathname==='/old-redirect')return send(302,'text/plain','redirect',{Location:'/'});
  if(url.pathname==='/auth/callback')return send(302,'text/plain','auth redirect',{Location:'/?code=isolated-callback'});
  if(url.pathname==='/'&&url.searchParams.has('code')){authRequests++;return send(200,'text/plain','AUTH_NETWORK_ONLY');}
  if(url.pathname==='/api/private'){apiRequests++;return send(200,'application/json',JSON.stringify({serial:apiRequests}));}
  try{
    const path=resolve(root,'.'+(url.pathname==='/'?'/index.html':url.pathname));
    if(!path.startsWith(root+sep))return send(403,'text/plain','Forbidden');
    const mime={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.webmanifest':'application/manifest+json','.json':'application/json'}[extname(path)]||'application/octet-stream';
    return send(200,mime,await readFile(path));
  }catch{return send(404,'text/plain','Missing static asset');}
});
await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
const base='http://127.0.0.1:'+server.address().port;
const cacheName=source=>JSON.parse(/^const ERIC_SW=(.+);$/m.exec(source)[1]).cache;
const oldCache=cacheName(await readFile(resolve(oldRoot,'sw.js'),'utf8')),newCache=cacheName(await readFile(resolve(newRoot,'sw.js'),'utf8'));
assert.notEqual(oldCache,newCache);
const engine=process.env.ERIC_WEB_ENGINE==='webkit'?webkit:chromium;
const profile=await mkdtemp(resolve(output,'profile-'));
const context=await engine.launchPersistentContext(profile,{headless:true,viewport:{width:390,height:844},serviceWorkers:'allow'});
try{
  await context.addInitScript(()=>{const original=fetch;window.fetch=(input,options)=>new URL(typeof input==='string'?input:input.url,location.href).origin===location.origin?original(input,options):Promise.reject(new TypeError('Isolated upgrade: private network disabled'));});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/');await expect(page.getByRole('heading',{name:'Eric OS',exact:true})).toBeVisible();
  await page.evaluate(async()=>{await navigator.serviceWorker.ready;if(!navigator.serviceWorker.controller)await new Promise(resolve=>navigator.serviceWorker.addEventListener('controllerchange',resolve,{once:true}));});
  await expect.poll(()=>page.evaluate(()=>caches.keys())).toContain(oldCache);
  const draft={owner:'11111111-1111-4111-8111-111111111111',form:'capture',requestId:'22222222-2222-4222-8222-222222222222',title:'Retained draft during upgrade',text:'Never submitted',purpose:'lecture_note',files:[]};
  await page.evaluate(async({oldCache,draft})=>{
    const request=indexedDB.open('eric-os-v1'),db=await new Promise((resolve,reject)=>{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
    if(!db.objectStoreNames.contains('drafts'))throw new Error('Published app did not initialize its real draft store');
    const tx=db.transaction('drafts','readwrite');tx.objectStore('drafts').put(draft);await new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);});db.close();
    const response=await fetch('/old-redirect');if(!response.redirected)throw new Error('Redirect poison fixture is not redirected');
    const cache=await caches.open(oldCache);await cache.put('/',response);await cache.put('/auth/callback',new Response('old auth HTML'));await cache.put('/api/private',new Response('{"old":true}'));
    history.replaceState({},'', '/?view=capture');
    window.ericUpgradeDocument='retained-capture-document';
  },{oldCache,draft});
  const documentNavigations=[];page.on('request',request=>{if(request.isNavigationRequest()&&request.frame()===page.mainFrame())documentNavigations.push(request.url());});
  root=newRoot;
  await page.evaluate(async()=>{const registration=await navigator.serviceWorker.getRegistration();await registration.update();});
  await expect.poll(()=>page.evaluate(()=>caches.keys()),{timeout:30000}).toContain(newCache);
  await expect.poll(()=>page.evaluate(async oldCache=>{const cache=await caches.open(oldCache);return {oldHtml:Boolean(await cache.match('/')),controlled:Boolean(navigator.serviceWorker.controller)};},oldCache),{timeout:30000}).toEqual({oldHtml:false,controlled:true});
  assert.equal(await page.evaluate(()=>window.ericUpgradeDocument),'retained-capture-document','An existing Capture document must not be forcibly reloaded during worker activation');
  assert.deepEqual(documentNavigations,[]);
  const oldAssets=(await readdir(resolve(oldRoot,'assets'))).filter(name=>/^(Pages|SchoolWorkspace)-.+\.js$/.test(name)).map(name=>'/assets/'+name);
  assert.equal(oldAssets.length,2);
  for(const path of oldAssets)assert.equal((await page.request.get(base+path)).status(),404,'The new origin must not accidentally retain the old bundle');
  await page.evaluate(async paths=>{for(const path of paths)await import(path);},oldAssets);
  const saved=await page.evaluate(async owner=>{const request=indexedDB.open('eric-os-v1'),db=await new Promise((resolve,reject)=>{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});const get=db.transaction('drafts').objectStore('drafts').get([owner,'capture']);const value=await new Promise((resolve,reject)=>{get.onsuccess=()=>resolve(get.result);get.onerror=()=>reject(get.error);});db.close();return value;},draft.owner);
  assert.deepEqual(saved,draft);
  const navigation=[];
  for(const path of ['/','/index.html','/?view=today','/?view=capture']){
    const response=await page.goto(base+path);await expect(page.getByRole('heading',{name:'Eric OS',exact:true})).toBeVisible();
    assert.equal(response.status(),200);assert.equal(response.request().redirectedFrom(),null);assert.equal(response.fromServiceWorker(),true);navigation.push({path,status:response.status(),worker:true,redirected:false});
  }
  await page.screenshot({path:resolve(output,'upgraded-safari-shell.png'),fullPage:true});
  const authResponse=await page.goto(base+'/auth/callback');assert.equal(await authResponse.text(),'AUTH_NETWORK_ONLY');assert.equal(authRequests,1);assert.equal(authResponse.fromServiceWorker(),false);
  assert.deepEqual(await page.evaluate(async()=>[(await(await fetch('/api/private')).json()).serial,(await(await fetch('/api/private')).json()).serial]),[1,2]);
  const cached=await page.evaluate(async()=>{const result=[];for(const name of await caches.keys())for(const request of await(await caches.open(name)).keys())result.push({name,path:new URL(request.url).pathname});return result;});
  assert.equal(cached.some(item=>item.path.startsWith('/auth/')||item.path.startsWith('/api/')),false);
  assert.deepEqual(errors,[]);
  await writeFile(resolve(output,'report.json'),JSON.stringify({passed:true,engine:process.env.ERIC_WEB_ENGINE||'chromium',oldCache,newCache,navigation,unchangedDraft:true,oldLazyAssetsAvailable:true,captureNotReloaded:true,authNetworkOnly:true,apiUncached:true,actualHomeScreenTested:false,errors},null,2));
  process.stdout.write(JSON.stringify({passed:true,oldCache,newCache,oldLazyAssetsAvailable:true,unchangedDraft:true,navigation,output})+'\n');
}finally{await context.close();await new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});}
