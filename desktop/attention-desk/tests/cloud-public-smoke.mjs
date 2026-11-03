// Actual published shell and CORS/auth boundary. No credentials or owner writes.
import {chromium,webkit,expect} from 'playwright/test';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const origin='https://eric-os-workbench-ericpanrun.choicechar22.chatgpt.site';
const api='https://mantuehuzptactymjpqg.supabase.co/functions/v1/eric-app-api';
const reports=[];
for(const [name,engine] of [['chromium',chromium],['webkit',webkit]]){
  const browser=await engine.launch({headless:true}),page=await browser.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  try{
    const response=await page.goto(origin,{waitUntil:'networkidle',timeout:60000});assert.equal(response.status(),200);
    await expect(page.getByRole('heading',{name:'Eric OS',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'登录 Eric OS',exact:true})).toBeVisible();
    const boundary=await page.evaluate(async api=>{
      const manifest=await(await fetch('/manifest.webmanifest')).json(),health=await fetch(api+'/health'),privateData=await fetch(api+'/courses');
      return {manifest,health:health.status,anonymous_data:privateData.status,worker:!!navigator.serviceWorker};
    },api);
    assert.equal(boundary.manifest.display,'standalone');assert.equal(boundary.health,200);assert.equal(boundary.anonymous_data,401);assert.deepEqual(errors,[]);
    reports.push({engine:name,public_shell:200,login_visible:true,pwa_manifest:true,api_cors:true,anonymous_owner_data:401,errors});
  }finally{await browser.close();}
}
await writeFile('D:/Eric OS/backups/web-pwa-v1-20261029/public-release-smoke.json',JSON.stringify({origin,passed:true,reports,owner_login_tested:false},null,2)+'\n');
process.stdout.write(JSON.stringify({passed:true,origin,reports})+'\n');
