/* ERIC_SW is generated from the exact built assets. Cache only the public shell. */
const {cache:CACHE,files:FILES,shell:SHELL,entry:ENTRY}=ERIC_SW;
const PREFIX='eric-os-shell-';
const authKeys=['code','access_token','refresh_token','token_hash','error','error_description'];
const appUrl=url=>['/','/index.html'].includes(url.pathname)&&!authKeys.some(key=>url.searchParams.has(key))&&!/access_token=|refresh_token=/.test(url.hash);
const staticUrl=url=>url.origin===self.location.origin&&(/^\/assets\/[A-Za-z0-9_.-]+\.(js|mjs|css)$/.test(url.pathname)||/^\/pdfjs-[\d.]+\//.test(url.pathname)||/^\/icon(?:-[A-Za-z0-9-]+)?\.(png|svg)$/.test(url.pathname)||url.pathname==='/manifest.webmanifest');
const safeAsset=response=>response&&response.status===200&&!response.redirected&&!['opaque','opaqueredirect'].includes(response.type)&&!response.headers.get('Content-Type')?.includes('text/html');

function cleanResponse(response,body=response.body){
  const headers=new Headers(response.headers);
  for(const name of ['Content-Encoding','Content-Length','Location','Refresh'])headers.delete(name);
  // Construct a new response: clone() preserves the redirect URL list, which
  // cannot be returned for Safari navigation requests with redirect=manual.
  return new Response(body,{status:response.status,statusText:response.statusText,headers});
}
async function publicShell(requireCurrent=true){
  const response=await fetch(new Request('/',{cache:'reload',credentials:'omit',redirect:'follow'}));
  const final=new URL(response.url||self.location.origin+'/');
  if(response.status!==200||final.origin!==self.location.origin||final.pathname!=='/'||final.search||!response.headers.get('Content-Type')?.includes('text/html'))throw new Error('Public shell unavailable');
  const body=await response.text();
  if(!body.includes('<div id="root"></div>')||!body.includes('/manifest.webmanifest')||!body.includes(requireCurrent?ENTRY:'/assets/index-'))throw new Error('Not the public Eric OS shell');
  return cleanResponse(response,body);
}
self.addEventListener('install',event=>event.waitUntil((async()=>{
  const cache=await caches.open(CACHE);
  try{
    const html=await publicShell();
    await Promise.all(SHELL.map(async path=>{
      const response=await fetch(new Request(path,{cache:'reload',credentials:'omit',redirect:'error'}));
      if(!safeAsset(response))throw new Error('Static shell asset unavailable');
      await cache.put(path,response);
    }));
    await cache.put('/',html);
    await self.skipWaiting();
  }catch(error){await caches.delete(CACHE);throw error;}
})()));
self.addEventListener('activate',event=>event.waitUntil((async()=>{
  const previous=(await caches.keys()).filter(key=>key.startsWith(PREFIX)&&key!==CACHE);
  const retain=previous.at(-1);
  for(const key of previous){
    const cache=await caches.open(key);
    for(const request of await cache.keys()){
      const url=new URL(request.url),response=await cache.match(request);
      // Retain one generation of hashed assets for already open Capture tabs;
      // remove every old HTML/auth/API entry, including poisoned redirects.
      if(key===retain&&(!staticUrl(url)||!safeAsset(response)))await cache.delete(request);
    }
    if(key!==retain)await caches.delete(key);
  }
  await self.clients.claim();
  // A WindowClient URL can retain the document's initial address after SPA
  // navigation. It cannot distinguish a failed home page from an editing tab
  // that started at home. Never navigate an existing client during activation:
  // the next ordinary navigation uses the clean current shell, while open
  // documents retain their inputs and can load the previous hashed assets.
})()));
self.addEventListener('fetch',event=>{
  const request=event.request,url=new URL(request.url);
  if(request.method!=='GET'||url.origin!==self.location.origin)return;
  if(request.mode==='navigate'){
    if(!appUrl(url))return; // Auth callbacks, APIs and other paths use the network.
    event.respondWith((async()=>{
      const saved=await(await caches.open(CACHE)).match('/');
      return saved?cleanResponse(saved):publicShell(false);
    })());return;
  }
  if(!staticUrl(url)||url.search)return;
  event.respondWith((async()=>{
    const cache=await caches.open(CACHE),saved=await cache.match(url.pathname);
    if(saved&&safeAsset(saved))return saved;
    if(!FILES.includes(url.pathname)){
      const previous=(await caches.keys()).filter(key=>key.startsWith(PREFIX)&&key!==CACHE);
      for(const key of previous){const old=await(await caches.open(key)).match(url.pathname);if(safeAsset(old))return old;}
      return fetch(request);
    }
    const response=await fetch(request);
    if(safeAsset(response))await cache.put(url.pathname,response.clone());
    return response;
  })());
});
