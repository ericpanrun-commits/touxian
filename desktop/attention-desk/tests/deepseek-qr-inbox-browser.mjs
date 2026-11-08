// Browser plugin not available: Playwright on isolated built production source.
// All non-local requests are intercepted. No real login or data upload.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
const base=process.env.QR_PREVIEW||'http://127.0.0.1:4186';
const out=new URL('../artifacts/deepseek-qr-20261108/',import.meta.url);
const bundle=JSON.parse(await readFile(new URL('synthetic-qr-bundle.json',out),'utf8'));
const rows=Object.fromEntries(bundle.records.map(r=>[r.kind,r.payload]));
const user={id:'7f6950b1-4119-4fe1-94ee-28f498754710',email:'synthetic@example.test',aud:'authenticated',role:'authenticated'};
const token=[{alg:'HS256',typ:'JWT'},{sub:user.id,exp:Math.floor(Date.now()/1000)+3600,aud:'authenticated'},{}].map(x=>Buffer.from(JSON.stringify(x)).toString('base64url')).join('.');
const browser=await chromium.launch({headless:true});
const errors=[],media=[],unknown=[];
try {
 for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:1000},serviceWorkers:'block'});
  await context.addInitScript(({token,user})=>localStorage.setItem('eric-os-auth-v1',JSON.stringify({access_token:token,refresh_token:'synthetic-only',expires_at:Math.floor(Date.now()/1000)+3600,user})),{token,user});
  await context.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.origin===base)return route.continue();
   if(/attachments|file-ticket|storage\/|image/.test(url.pathname))media.push(url.pathname);
   const path=url.pathname.replace('/functions/v1/eric-app-api','');
   const headers={'Access-Control-Allow-Origin':base,'Access-Control-Allow-Headers':'*','Access-Control-Allow-Methods':'GET,POST,OPTIONS'};
   const send=v=>route.fulfill({headers,contentType:'application/json',body:JSON.stringify(v)});
   if(req.method()==='OPTIONS')return route.fulfill({status:204,headers});
   if(path==='/auth/v1/user')return send(user);
   if(path==='/session')return send({device:{id:'e2daaa76-2bbd-40db-aa3b-a18140e66043',label:'Synthetic',platform:'web'},account:user,timezone:'Asia/Shanghai'});
   if(path==='/system')return send({cloud_available:true,processing_online:false,cursor:1,waiting:0});
   if(path==='/inbox')return send({items:[rows.inbox_item],total:1});
   if(path==='/inbox/'+rows.inbox.id)return send(rows.inbox);
   if(path==='/raw/'+rows.raw_review.raw_snapshot_id)return send(rows.raw_review);
   if(path==='/changes')return send({items:[],cursor:1,more:false});
   if(['/courses','/projects','/activity','/capture/receipts','/learning/captures'].includes(path))return send({items:[],total:0});
   unknown.push(path);return send({items:[],total:0});
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/?view=inbox&inbox_scope=all&inbox_status=all',{waitUntil:'networkidle'});
  await page.getByRole('button').filter({hasText:rows.inbox.title}).click();
  const hint=page.getByRole('note').filter({hasText:'需扫码，二维码请返回原微信群查看'});
  await hint.waitFor({state:'visible'});
  assert.equal(await hint.count(),1);
  assert((await page.locator('body').innerText()).includes(rows.raw_review.snapshot.original_text));
  assert.equal(await page.locator('.personal-source-image-wrap img').count(),0);
  await hint.scrollIntoViewIfNeeded();
  await page.screenshot({path:new URL('inbox-'+width+'.png',out).pathname.replace(/^\/(\w:)/,'$1'),fullPage:false});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await context.close();
 }
 assert.deepEqual(errors,[]);assert.deepEqual(media,[]);
 await writeFile(new URL('browser-report.json',out),JSON.stringify({passed:true,widths:[1440,390],errors,mediaRequests:media,unhandledMockPaths:[...new Set(unknown)],actualCloudAuth:false},null,2));
 console.log(JSON.stringify({passed:true,widths:[1440,390],mediaRequests:0,errors:0}));
}finally{await browser.close();}
