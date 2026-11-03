import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Field } from './Forms';
import { personalFileUrl, personalPdfSource } from './api';
import {createPdfDocumentSession} from './pdfDocumentSession';
const PdfSourcePage=lazy(()=>import('./PdfSourcePage'));

export function SourceLink({ href, children, ...props }) {
  const [result,setResult] = useState({path:href,url:null,error:false});
  const [attempt,setAttempt] = useState(0);
  useEffect(() => {
    let current=true;
    let last=0;
    const refresh=()=>personalFileUrl(href,{force:attempt>0}).then(url=>{if(current){last=Date.now();setResult({path:href,url,error:false});}}).catch(()=>{if(current)setResult({path:href,url:null,error:true});});
    const recover=()=>{if(document.visibilityState==='visible'&&Date.now()-last>480_000)void refresh();};
    void refresh();const timer=setInterval(recover,480_000);
    document.addEventListener('visibilitychange',recover);window.addEventListener('online',recover);
    return ()=>{current=false;clearInterval(timer);document.removeEventListener('visibilitychange',recover);window.removeEventListener('online',recover);};
  },[href,attempt]);
  if(result.path===href&&result.url)return <a {...props} href={result.url}>{children}</a>;
  return result.path===href&&result.error?<button type="button" className={props.className || 'personal-text-button'} onClick={()=>setAttempt(value=>value+1)}>重试打开原件</button>:<span className="personal-meta" role="status">正在准备原件…</span>;
}

export function sourceLabel(source) {
  return [source.filename || (source.source_type === 'web' ? '网页正文' : source.block_id?.startsWith('WX_') ? '微信消息' : '粘贴原文'), source.page ? `第 ${source.page} 页` : source.section, source.source_type === 'image_ocr' ? 'OCR · 请核对' : source.source_type === 'image_vision' ? source.section?.includes('非原文') ? '视觉描述 · 非原文' : '视觉转写 · 请核对原图' : ''].filter(Boolean).join(' · ');
}

export function FileList({ snapshot, busy, onParse }) {
  const [preview, setPreview] = useState(null);
  const labels = { not_downloaded: '未下载', downloaded: '已保存 · 待解析', parsing: '本地解析中', parsed: '已解析', unsupported: '格式不支持', failed: '解析未完成' };
  return snapshot.attachments?.length ? <div className="personal-document-files"><h3>文件与截图</h3><p className="personal-meta">{snapshot.wechat ? '微信附件保存在本机，识别文字仅作辅助，请对照原件核对。不会自动请求微信中未缓存的图片。' : '解析在本机完成，原文件保留。点击分析时，只把提取文字发送到 DeepSeek。'}</p><ul>{snapshot.attachments.map(a => <li key={a.id}>
    <div><strong>{a.filename}</strong><span className="personal-meta"> · {snapshot.wechat && a.downloaded_at ? a.mime_type?.startsWith('image/') ? '图片已保存' : '原件已保存' : labels[a.parse_status]}{a.parse_manifest?.metadata?.evidence_tier === 'thumb' ? ' · 缓存缩略图' : ''}{a.version ? ` · 文件第 ${a.version} 版` : ''}{a.size ? ` · ${(a.size / 1000000).toFixed(2)} MB` : ''}</span></div>
    {a.parse_error ? <p className="personal-field-error">{a.parse_error}</p> : null}
    <div className="personal-evidence-links">{a.downloaded_at ? <SourceLink className="personal-text-button" href={`/api/personal/attachments/${a.id}/original`}>下载已保存原文件</SourceLink> : a.original_url ? <a className="personal-text-button" href={a.original_url} target="_blank" rel="noopener noreferrer">打开官网附件链接</a> : null}
      {a.downloaded_at && a.mime_type?.startsWith('image/') ? <button type="button" className="personal-text-button" onClick={() => setPreview(preview === a.id ? null : a.id)}>{preview === a.id ? '收起图片' : '查看图片'}</button> : null}
      {onParse ? <button type="button" className="personal-text-button" disabled={busy || a.parse_status === 'parsing'} onClick={() => onParse(a)}>{a.parse_status === 'parsed' && a.original_url ? '重新下载核对版本' : a.parse_status === 'parsed' ? '重新读取已解析内容' : a.downloaded_at ? '解析已保存文件' : '下载并解析'}</button> : null}</div>
    {preview === a.id ? <SourceImage source={{ attachment_id: a.id }} /> : null}
  </li>)}</ul>{snapshot.attachment_jobs?.[0]?.error?.filter(e => e.local_saved && !snapshot.attachments.some(a => a.id === e.attachment_id)).map(e => <p key={e.attachment_id} className="personal-field-error">{e.filename}：{e.message} <a className="personal-text-button" href={`/api/personal/attachments/${e.attachment_id}/original`}>下载解析失败的新版本原文件</a></p>)}</div> : null;
}

export function SourceImage({ source }) {
  const path = source.document_id ? `/api/personal/school/documents/${source.document_id}/pages/${source.page}/image` : `/api/personal/attachments/${source.attachment_id}/image${source.page ? `?page=${source.page}` : ''}`;
  const identity=source.document_id?'document:'+source.document_id:'attachment:'+source.attachment_id;
  return <SourceDocument key={identity} source={source} path={path}/>;
}

function SourceDocument({source,path}) {
  const [session]=useState(createPdfDocumentSession);
  useEffect(()=>()=>{void session.clear();},[session]);
  return <SourceImageContent key={path} source={source} path={path} session={session}/>;
}

function SourceImageContent({source,path,session}) {
  const [failed, setFailed] = useState(false);
  const [url,setUrl] = useState(null);
  const [pdf,setPdf]=useState(null);
  const [attempt,setAttempt]=useState(0),automaticRetry=useRef(false),originalImage=useRef(false);
  useEffect(()=>{automaticRetry.current=false;originalImage.current=false;},[path]);
  useEffect(() => { let current=true,last=0;setFailed(false);setUrl(null);setPdf(null);
    const load=()=>{originalImage.current=false;return personalFileUrl(path,{force:attempt>0}).then(value=>{if(current){last=Date.now();setUrl(value);setFailed(false);}}).catch(async()=>{
      try{const value=await personalPdfSource(source,{force:attempt>0});if(current){last=Date.now();originalImage.current=!!value?.image;if(value?.image){setUrl(value.url);setPdf(null);}else setPdf(value);setFailed(!value);}}
      catch{if(current)setFailed(true);}
    });};
    const recover=()=>{if(document.visibilityState==='visible'&&Date.now()-last>480_000)void load();};
    void load();const timer=setInterval(recover,480_000);document.addEventListener('visibilitychange',recover);window.addEventListener('online',recover);
    return()=>{current=false;clearInterval(timer);document.removeEventListener('visibilitychange',recover);window.removeEventListener('online',recover);}; }, [path,attempt]);
  const box = source.bounding_box;
  const imageFailed=async()=>{
    if(!automaticRetry.current){automaticRetry.current=true;setAttempt(value=>value+1);return;}
    if(originalImage.current){setFailed(true);return;}
    try{const value=await personalPdfSource(source,{force:true});if(value){originalImage.current=!!value.image;if(value.image){setUrl(value.url);setPdf(null);}else{setPdf(value);setUrl(null);}}else setFailed(true);}catch{setFailed(true);}
  };
  return <div className="personal-source-image-wrap">
    {failed ? <p className="personal-field-error" role="alert">原图暂时无法显示，完整原文件仍然保留。<button type="button" className="personal-text-button" onClick={()=>setAttempt(value=>value+1)}>重新读取原图</button></p> : !url&&!pdf ? <p className="personal-meta" role="status">正在读取原图…</p> : <div className="personal-source-image">{pdf?<Suspense fallback={<p className="personal-meta" role="status">正在准备页面…</p>}><PdfSourcePage source={pdf} session={session} onRetry={()=>setAttempt(value=>value+1)} /></Suspense>:<img key={attempt} src={url} alt={source.page ? `原文件第 ${source.page} 页` : '已保存的来源图片'} onError={()=>void imageFailed()} />}
      {box ? <span aria-label="引用对应文字区域" className="personal-source-image-mark" style={{ left: `${box.x / box.image_width * 100}%`, top: `${box.y / box.image_height * 100}%`, width: `${box.width / box.image_width * 100}%`, height: `${box.height / box.image_height * 100}%` }} /> : null}</div>}
  </div>;
}

export function SourceBlockPicker({ blocks, value, onChange, name = 'source_block', label = '查看来源位置' }) {
  return blocks?.length ? <Field name={name} label={label}><select value={value || ''} onChange={event => onChange(event.target.value)}><option value="" disabled>选择正文、文件页或段落</option>{blocks.map(b => <option key={b.block_id} value={b.block_id}>{sourceLabel(b)} · {b.text.slice(0, 30)}</option>)}</select></Field> : null;
}
