export function registerWorker(){
  if(!('serviceWorker' in navigator))return;
  let registration,lastCheck=0;
  const update=()=>{
    if(!registration||document.visibilityState==='hidden'||!navigator.onLine||Date.now()-lastCheck<60_000)return;
    lastCheck=Date.now();void registration.update().catch(()=>{});
  };
  const start=async()=>{
    try{
      registration=await navigator.serviceWorker.register('/sw.js',{scope:'/',updateViaCache:'none'});
      update();
    }catch{}
  };
  if(document.readyState==='complete')void start();else window.addEventListener('load',()=>void start(),{once:true});
  window.addEventListener('online',update);
  window.addEventListener('focus',update);
  document.addEventListener('visibilitychange',update);
  // The worker takes control after complete installation. Existing editing tabs
  // keep their document and draft; their next navigation uses the new shell.
}
