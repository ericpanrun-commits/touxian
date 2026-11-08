import {read,capture,local,currentIdentity,progress,saveCommand,fileUrl,sourceResource,CloudError} from './client';
const records=new Map();
let recordContext=null;
function remember(value) {
  if(!value||typeof value!=='object')return;
  if(value._cloud?.kind&&value._cloud?.id)records.set(value._cloud.kind+':'+value._cloud.id,value);
  if(Array.isArray(value))value.forEach(remember);else Object.values(value).forEach(remember);
}
export async function cloudRequest(path,options={}) {
  const context=JSON.stringify([currentIdentity()?.owner,currentIdentity()?.device?.id]);
  const check=()=>{if(JSON.stringify([currentIdentity()?.owner,currentIdentity()?.device?.id])!==context)throw new CloudError('登录设备已变化，请重新打开这份内容。','identity_changed',409);};
  if(recordContext!==context){records.clear();recordContext=context;}
  if(options.expectedIdentity&&context!==options.expectedIdentity)
    throw new CloudError('登录设备已变化，请重新打开这份内容。','identity_changed',409);
  if((!options.method||options.method==='GET')&&/^\/commands\/[a-f0-9-]{36}$/.test(path)) {
    const saved=await local.commands.get(path.split('/')[2]);
    check();
    if(saved?.owner===currentIdentity()?.owner)return {...saved.server,id:saved.id,state:saved.state,expected_revision:saved.expectedRevision??saved.server?.expected_revision,error:saved.error||saved.server?.error||null,result:saved.server?.result||null};
  }
  if(!options.method||options.method==='GET') {
    const result=await read(path,options);check();remember(result);
    const reviewMatch=/^\/school\/reviews\/([^/]+)$/.exec(path),readOwner=currentIdentity()?.owner;
    if(reviewMatch&&readOwner){
      const editPath=path+'/source-correction';
      const saved=await local.commands.where('owner').equals(readOwner).filter(item=>item.path===editPath).toArray();
      check();
      const latest=saved.sort((a,b)=>b.createdAt.localeCompare(a.createdAt))[0];
      if(latest?.state==='completed')return {...result,source_correction_result:latest.server?.result};
      if(latest)return {...result,source_correction_pending:{id:latest.id,body:latest.body,state:latest.state,error:latest.error||latest.server?.error}};
    }
    const match=/^\/learning\/captures\/([^/]+)$/.exec(path),owner=currentIdentity()?.owner;
    if(match&&owner){
      const target='learning_capture:'+decodeURIComponent(match[1]);
      const pending=await local.commands.where('owner').equals(owner).filter(item=>item.resourceKey===target&&['local_pending','pending','processing'].includes(item.state)).first();
      check();
      if(pending)return {...result,pending_confirmation:{id:pending.id,body:pending.body}};
    }
    return result;
  }
  if(path==='/learning/capture'&&options.method==='POST')return {...await capture({title:options.body.title,text:options.body.text}),queued:true};
  if(/^\/inbox\/[^/]+\/archive$/.test(path)&&options.method==='POST')return {...await progress('inbox',decodeURIComponent(path.split('/')[2]),options.body.expected_revision,{archived:options.body.archived}),queued:true};
  let target,match;
  if((match=/^\/inbox\/([^/]+)/.exec(path)))target='inbox:'+decodeURIComponent(match[1]);
  else if((match=/^\/events\/([^/]+)/.exec(path)))target='event:'+decodeURIComponent(match[1]);
  else if((match=/^\/raw\/([^/]+)/.exec(path)))target='raw_review:'+decodeURIComponent(match[1]);
  else if((match=/^\/school\/lectures\/([^/]+)/.exec(path)))target='lecture:'+decodeURIComponent(match[1]);
  else if((match=/^\/school\/reviews\/([^/]+)/.exec(path))) {
    const review=records.get('review:'+decodeURIComponent(match[1]));target=review?'lecture:'+review.lecture_id:null;
  } else if((match=/^\/learning\/captures\/([^/]+)/.exec(path)))target='learning_capture:'+decodeURIComponent(match[1]);
  else if((match=/^\/learning\/topics\/([^/]+)/.exec(path)))target='topic:'+decodeURIComponent(match[1]);
  else if(path==='/courses')target='course:new';
  else if(path==='/school/lectures')target='lecture:new';
  else if(path==='/school/textbooks')target='course:'+options.body?.course_id;
  else if(path==='/school/textbook/file'||path==='/school/sections')target='textbook:'+options.body?.textbook_id;
  else if((match=/^\/school\/sections\/([^/]+)\/index$/.exec(path))) {
    const sectionId=decodeURIComponent(match[1]),section=records.get('document_section:'+sectionId);
    const book=section?.textbook_id||[...records.values()].find(value=>value._cloud?.kind==='textbook'&&value.sections?.some(s=>s.id===sectionId))?.id;
    target=book?'textbook:'+book:null;
  } else if((match=/^\/school\/documents\/([^/]+)\/catalog$/.exec(path))) {
    const id=decodeURIComponent(match[1]);target='document:'+id;
    if(!records.has(target)){const doc=await read('/documents/'+encodeURIComponent(id),{signal:options.signal});check();remember(doc);}
  }
  else if(['/learning/history/confirm','/learning/analysis/authorization','/learning/answers','/learning/sync','/learning/external-reviews'].includes(path))target='facade:/learning';
  const source=records.get(target),expectedRevision=options.expectedRevision??(['course:new','lecture:new'].includes(target)?0:source?._cloud?.revision);
  if(!target||!Number.isSafeInteger(expectedRevision))throw new CloudError('请重新打开这份资料，读取最新内容后再保存。','missing_revision',409);
  return saveCommand(path,options.body,{method:options.method,target,expectedRevision});
}
export async function cloudFile(path,options) {
  const resource=sourceResource(path);
  if(resource)return await fileUrl(resource,options)+new URL(path,location.origin).hash;
  const url=new URL(path,location.origin);
  if(url.origin===location.origin)throw new CloudError('这份原件还在准备，请稍后重试。','source_unavailable');
  if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw new CloudError('来源链接无效。','invalid_source');
  if(/^(localhost|127\.|0\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.|\[|.*\.local$)/i.test(url.hostname))throw new CloudError('这份来源仍使用开发地址，请从云端已保存原件查看。','development_source');
  return url.href;
}

export async function cloudPdfSource(source,options) {
  if(!Number.isSafeInteger(Number(source.page))||Number(source.page)<1)return null;
  const identity=currentIdentity(),scope=identity?JSON.stringify([identity.owner,identity.device?.id]):null;
  if(!scope)throw new CloudError('请先登录 Eric OS。','sign_in_required',401);
  const check=()=>{const current=currentIdentity();if(!current||JSON.stringify([current.owner,current.device?.id])!==scope)throw new CloudError('登录设备已变化，请重新打开这份内容。','identity_changed',409);};
  const document=source.document_id?await read('/documents/'+encodeURIComponent(source.document_id)):null;
  check();
  const attachment=document?.attachment_id||source.attachment_id;
  if(!attachment||document?.page_count&&Number(source.page)>document.page_count)return null;
  const resource=document?.reading_resource_id||'attachment:'+attachment+':original';
  const url=await fileUrl(resource,options);check();
  return {url,page:Number(source.page),resource,scope,...(document?.mime_type?.startsWith('image/')?{image:true}:{})};
}
