const KEY='ericNavigationIndex';

// Canonical child screens also use History directly. Label their entries so a
// cancelled Back/Forward returns to the exact entry without adding duplicates.
export function guardHistory({isDirty,onRoute,onConfirm}){
  const originalPush=history.pushState,originalReplace=history.replaceState;
  let index=Number.isSafeInteger(history.state?.[KEY])?history.state[KEY]:0;
  let returning=null,allowNext=false;
  const labeled=(state,value)=>({...((state&&typeof state==='object')?state:{}),[KEY]:value});
  originalReplace.call(history,labeled(history.state,index),'');
  const push=function(state,title,url){originalPush.call(history,labeled(state,index+1),title,url);index++;};
  const replace=function(state,title,url){originalReplace.call(history,labeled(state,index),title,url);};
  history.pushState=push;history.replaceState=replace;
  const back=event=>{
    const destination=event.state?.[KEY];
    if(returning){
      event.stopImmediatePropagation();
      if(destination!==index){if(Number.isSafeInteger(destination))history.go(index-destination);return;}
      const pending=returning;returning=null;
      onConfirm(()=>{allowNext=true;history.go(pending.destination-index);});return;
    }
    if(!allowNext&&isDirty()&&Number.isSafeInteger(destination)&&destination!==index){
      event.stopImmediatePropagation();returning={destination};history.go(index-destination);return;
    }
    allowNext=false;if(Number.isSafeInteger(destination))index=destination;
    onRoute();
  };
  window.addEventListener('popstate',back,true);
  return()=>{window.removeEventListener('popstate',back,true);if(history.pushState===push)history.pushState=originalPush;if(history.replaceState===replace)history.replaceState=originalReplace;};
}
