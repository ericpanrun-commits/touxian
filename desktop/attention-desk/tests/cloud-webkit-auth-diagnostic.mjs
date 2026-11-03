import {webkit} from 'playwright/test';
import {build} from 'esbuild';
const browser=await webkit.launch({headless:true});
try{
 const context=await browser.newContext({serviceWorkers:'block'});
 const calls=[],failure=[];
 const owner='11111111-1111-4111-8111-111111111111',session='22222222-2222-4222-8222-222222222222';
 const token=[Buffer.from('{"alg":"HS256","typ":"JWT"}').toString('base64url'),Buffer.from(JSON.stringify({sub:owner,session_id:session,role:'authenticated',exp:Math.floor(Date.now()/1000)+3600})).toString('base64url'),Buffer.alloc(32,1).toString('base64url')].join('.');
 await context.route('https://mantuehuzptactymjpqg.supabase.co/**',async route=>{calls.push(route.request().method()+' '+new URL(route.request().url()).pathname);const headers={'Access-Control-Allow-Origin':'http://127.0.0.1:4176','Access-Control-Allow-Headers':'authorization,apikey,content-type,x-client-info,x-supabase-api-version','Access-Control-Allow-Methods':'GET,HEAD,POST,PUT,DELETE,OPTIONS'};if(route.request().method()==='OPTIONS')return route.fulfill({status:204,headers});return route.fulfill({headers,contentType:'application/json',body:JSON.stringify({access_token:token,refresh_token:'synthetic-refresh',expires_in:3600,token_type:'bearer',user:{id:owner,email:'synthetic@example.test',aud:'authenticated',role:'authenticated',app_metadata:{},user_metadata:{},created_at:new Date().toISOString()}})});});
 const page=await context.newPage();page.on('console',msg=>{if(msg.type()==='error')failure.push(msg.text());});
 await page.goto('http://127.0.0.1:4176/');
 const compiled=await build({stdin:{contents:"import{createClient}from'@supabase/supabase-js';window.diagnosticAuth=createClient('https://mantuehuzptactymjpqg.supabase.co','synthetic-public-key',{auth:{storageKey:'eric-diagnostic-synthetic',autoRefreshToken:true,persistSession:true}});",resolveDir:process.cwd()},bundle:true,write:false,format:'iife',platform:'browser'});
 await page.addScriptTag({content:compiled.outputFiles[0].text});
 const result=await page.evaluate(async()=>{try{const value=await window.diagnosticAuth.auth.signInWithPassword({email:'synthetic@example.test',password:'synthetic-password'});return {hasSession:!!value.data.session,error:value.error&&{name:value.error.name,code:value.error.code,message:value.error.message},secure:isSecureContext,randomUUID:typeof crypto.randomUUID};}catch(error){return {name:error.name,message:error.message};}});
 console.log(JSON.stringify({result,calls,failure}));
}finally{await browser.close();}
