// One in-memory document per mounted source viewer. Never a global file cache.
export function createPdfDocumentSession({now=Date.now,maxPages=8,maxLoadedBytes=16_000_000,maxAgeMs=480_000}={}) {
  let current=null,closing=Promise.resolve();
  const cancelled=()=>Object.assign(new Error('PDF reading context changed'),{name:'AbortError'});
  function retire(entry) {
    if(!entry||entry.retired)return;
    entry.retired=true;
    closing=closing.then(async()=>{if(entry.task)await entry.task.destroy();}).catch(()=>{});
  }
  function clear() {
    const previous=current;current=null;retire(previous);return closing;
  }
  function get(source,load) {
    const key=JSON.stringify([source.scope||null,source.resource||null,source.url]);
    const page=Number(source.page);
    if(!Number.isSafeInteger(page)||page<1)throw new RangeError('Invalid PDF page');
    const expires=current&&(now()-current.started>=maxAgeMs||current.loaded>=maxLoadedBytes
      ||(!current.pages.has(page)&&current.pages.size>=maxPages));
    if(current&&current.key===key&&!expires){current.pages.add(page);return current.promise;}
    retire(current);
    const entry={key,pages:new Set([page]),loaded:0,started:now(),task:null,retired:false,promise:null};
    const wait=closing;current=entry;
    entry.promise=(async()=>{
      await wait;
      if(current!==entry||entry.retired)throw cancelled();
      try {
        entry.task=load();
        entry.task.onProgress=({loaded})=>{if(current===entry&&Number.isFinite(loaded))entry.loaded=Math.max(entry.loaded,loaded);};
        const document=await entry.task.promise;
        if(current!==entry||entry.retired)throw cancelled();
        return document;
      }catch(error){if(current===entry)current=null;retire(entry);throw error;}
    })();
    // A view can close before its loading promise is awaited. Keep rejection
    // handling attached while still propagating the failure to its reader.
    void entry.promise.catch(()=>{});
    return entry.promise;
  }
  return {get,clear};
}
