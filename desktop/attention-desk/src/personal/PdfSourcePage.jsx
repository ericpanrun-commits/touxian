import {useEffect,useRef,useState} from 'react';
import * as pdfjs from 'pdfjs-dist/legacy/build/pdf.mjs';
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url';
import {createPdfDocumentSession} from './pdfDocumentSession';

pdfjs.GlobalWorkerOptions.workerSrc=workerUrl;
const assets='/pdfjs-'+pdfjs.version+'/';

export default function PdfSourcePage({source,onRetry,session}) {
  const canvas=useRef(null),lane=useRef(Promise.resolve()),[result,setResult]=useState(null);
  const [ownedSession]=useState(createPdfDocumentSession),documents=session||ownedSession;
  const key=JSON.stringify([source.scope||null,source.resource||null,source.url,source.page]);
  const state=result?.key===key?result.state:'loading';
  useEffect(()=>()=>{if(!session)void ownedSession.clear();},[session,ownedSession]);
  useEffect(()=>{
    let active=true,render,page;
    const previous=lane.current;
    const work=(async()=>{
      try{
        const pdf=await documents.get(source,()=>pdfjs.getDocument({url:source.url,disableStream:true,disableAutoFetch:true,rangeChunkSize:524288,
          cMapUrl:assets+'cmaps/',cMapPacked:true,standardFontDataUrl:assets+'standard_fonts/',wasmUrl:assets+'wasm/',iccUrl:assets+'iccs/',
          isEvalSupported:false,enableXfa:false,canvasMaxAreaInBytes:16_000_000}));
        page=await pdf.getPage(source.page);
        await previous;
        if(!active)return;
        const natural=page.getViewport({scale:1}),width=Math.min(1440,Math.max(720,canvas.current.parentElement.clientWidth*(window.devicePixelRatio||1)));
        const scale=Math.min(width/natural.width,Math.sqrt(4_000_000/(natural.width*natural.height)));
        const viewport=page.getViewport({scale}),element=canvas.current;
        element.width=Math.ceil(viewport.width);element.height=Math.ceil(viewport.height);
        render=page.render({canvasContext:element.getContext('2d'),viewport});
        await render.promise;
        if(active)setResult({key,state:'ready'});
      }catch{if(active)setResult({key,state:'failed'});}
      finally{try{page?.cleanup();}catch{/* Closing the source also destroys page resources. */}}
    })();
    lane.current=work.catch(()=>{});
    return()=>{active=false;render?.cancel();};
  },[source.url,source.page,source.scope,source.resource,documents,key]);
  return <>
    {state==='loading'?<p className="personal-meta" role="status">正在读取第 {source.page} 页…</p>:null}
    {state==='failed'?<p className="personal-field-error" role="alert">这一页暂时没有打开，原文件已保留。<button type="button" className="personal-text-button" onClick={()=>{void documents.clear();onRetry();}}>重新读取这一页</button></p>:null}
    <canvas ref={canvas} role="img" aria-label={`原文件第 ${source.page} 页`} hidden={state!=='ready'} style={{display:state==='ready'?'block':'none',width:'100%',height:'auto'}} />
  </>;
}
