"""Unified collection and original-file viewing; no separate analysis endpoint."""
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from starlette.datastructures import UploadFile
from ..personal_store import PersonalError
from ..personal_api import perform
from ..review_api import request_key
from ..personal_models import normalize_time
from .policy import MAX_BYTES, SOFT_BYTES, DocumentError, MIMES

router=APIRouter(prefix='/api/personal',tags=['documents'])


def service_for(request):
    service=getattr(request.app.state,'document_service',None)
    if not service: raise PersonalError('文件服务尚未就绪。','not_ready',503)
    return service


class ParseInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    attachment_ids:list[str]=Field(min_length=1,max_length=10)
    confirm_large:bool=False
    redownload:bool=False


@router.post('/raw/{snapshot_id}/documents',status_code=202)
def parse_files(snapshot_id:str,payload:ParseInput,request:Request):
    return perform(lambda:service_for(request).start(snapshot_id,payload.attachment_ids,payload.confirm_large,payload.redownload))


@router.get('/document-jobs/{job_id}')
def parse_job(job_id:str,request:Request):
    return perform(lambda:service_for(request).job(job_id))


@router.post('/collect-files',status_code=201)
async def collect(request:Request):
    service=service_for(request)
    files=[]; temp=[]
    textbook=request.query_params.get('purpose')=='textbook' and request.query_params.get('inbox')=='1'
    from ..school.boundary import local_upload,TEXTBOOK_MAX_BYTES
    max_bytes=TEXTBOOK_MAX_BYTES if textbook else MAX_BYTES
    try:
        if textbook: local_upload(request)
        async with request.form(max_files=10,max_fields=5,max_part_size=150000) as form:
            body=str(form.get('original_text','')); title=str(form.get('title_hint','')); stamp=str(form.get('source_time','')) or None
            if len(body)>100000 or len(title)>200: raise PersonalError('文字或标题过长。')
            if stamp:
                try: stamp=normalize_time(stamp,'date' if len(stamp)==10 else 'minute')
                except ValueError: raise PersonalError('原消息时间格式无效。')
            large=form.get('confirm_large')=='true'
            for value in form.getlist('files'):
                if not isinstance(value,UploadFile): raise PersonalError('请选择文件。')
                if (value.size or 0)>max_bytes: raise PersonalError(f'文件超过 {max_bytes//1_000_000} MB 上限。','file_too_large',413)
                if (value.size or 0)>SOFT_BYTES and not large: raise PersonalError('文件过大，需要确认。','large_file_confirmation',413)
                path=service.files.work/(str(uuid4())+'.upload'); temp.append(path)
                size=0
                with path.open('wb') as out:
                    while data:=await value.read(65536):
                        size+=len(data)
                        if size>max_bytes: raise PersonalError(f'文件超过 {max_bytes//1_000_000} MB 上限。','file_too_large',413)
                        out.write(data)
                files.append((value.filename,value.content_type,path))
            if not files: raise PersonalError('请选择至少一个文件。')
            if textbook and (len(files)!=1 or not str(files[0][0] or '').lower().endswith('.pdf')): raise PersonalError('教材请每次选择一份完整 PDF 或章节扫描 PDF。')
            if request.query_params.get('inbox') == '1':
                from ..inbox_api import service_for as inbox_for
                from ..collector.security import CollectorError
                try:
                    intake = inbox_for(request)
                    return await intake.bridge(intake.submit('desktop', 'file', {
                        'submission_id': request_key(request.headers.get('idempotency-key')) or str(uuid4()),
                        'text': body, 'source_name': title, 'source_time': stamp, 'confirm_large': large, **({'purpose':'textbook'} if textbook else {})}, files))
                except CollectorError as error:
                    raise PersonalError(str(error), error.code, error.status)
            result=service.files.collect(files,body,title,stamp,request_key(request.headers.get('idempotency-key')))
            atts=[a['id'] for a in result['snapshot']['attachments'] if a['parse_status']!='parsed']
            if atts: result['document_job']=service.start(result['snapshot']['id'],atts,large)
            return result
    except (PersonalError,DocumentError) as e:
        return JSONResponse(status_code=getattr(e,'status',400),content={'detail':{'code':e.code,'message':str(e)}})
    finally:
        for path in temp: path.unlink(missing_ok=True)


@router.get('/attachments/{attachment_id}/original')
def original(attachment_id:str,request:Request,inline:bool=False):
    def get():
        service=service_for(request); att=service.files.get(attachment_id)
        preview = inline and att['extension']=='pdf'
        return FileResponse(service.files.path(att),media_type='application/pdf' if preview else 'application/octet-stream',
            filename=att['filename'],content_disposition_type='inline' if preview else 'attachment',headers={'X-Content-Type-Options':'nosniff'})
    return perform(get)


@router.get('/attachments/{attachment_id}/image')
def image(attachment_id:str,request:Request,page:int|None=Query(None,ge=1,le=200)):
    def get():
        service=service_for(request); att=service.files.get(attachment_id); path=service.files.path(att)
        # Viewing a saved original must not depend on successful OCR. Validation
        # still bounds decoding; no file is executed and no analysis is started.
        if att['extension']=='pdf':
            if page is None: raise PersonalError('请选择 PDF 页码。')
            import json
            manifest=json.loads(att['parse_manifest_json'] or 'null')
            if manifest:
                count=manifest['metadata']['page_count']
            else:
                from pypdf import PdfReader
                try: count=len(PdfReader(path).pages)
                except Exception: raise PersonalError('原 PDF 暂时无法预览，请下载原件核对。','preview_unavailable',400)
            if page>count: raise PersonalError('这份 PDF 没有该页。','invalid_page',400)
            target=service.files.work/f'{att["sha256"]}-page-{page}.png'
            if not target.exists():
                temporary=service.files.work/f'{uuid4()}.png'
                service.worker(path,f'render:{page}',temporary,30)
                from PIL import Image
                try:
                    with Image.open(temporary) as im: im.verify()
                except Exception: raise PersonalError('PDF 页面无法渲染。','render_failed',400)
                if not target.exists(): temporary.replace(target)
            return FileResponse(target,media_type='image/png',headers={'X-Content-Type-Options':'nosniff'})
        if att['extension'] not in ('png','jpg','jpeg'): raise PersonalError('这个文件没有图片预览。','unsupported',400)
        from .policy import validate_file
        validate_file(path,att['extension'])
        # OCR coordinates refer to EXIF-oriented original geometry.
        from PIL import Image,ImageOps
        target=service.files.work/f'{att["sha256"]}-oriented.png'
        if not target.exists():
            with Image.open(path) as im: ImageOps.exif_transpose(im).convert('RGB').save(target)
        return FileResponse(target,media_type='image/png',headers={'X-Content-Type-Options':'nosniff'})
    try: return perform(get)
    except DocumentError as e: raise HTTPException(400,detail={'code':e.code,'message':str(e)})


class UploadBodyLimit:
    """Bound the entire multipart stream before Starlette can spool an oversized file."""
    def __init__(self,app): self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('path')!='/api/personal/collect-files':
            return await self.app(scope,receive,send)
        from starlette.datastructures import QueryParams
        query=QueryParams(scope.get('query_string',b'').decode('ascii','replace'))
        textbook=query.get('purpose')=='textbook' and query.get('inbox')=='1'
        limit=251_000_000 if textbook else 60_000_000
        if textbook:
            from ..school.boundary import local_upload
            try: local_upload(Request(scope))
            except PersonalError as error:
                return await JSONResponse({'detail':{'code':error.code,'message':str(error)}},status_code=error.status)(scope,receive,send)
        headers=dict(scope.get('headers',[])); declared=headers.get(b'content-length',b'')
        if declared.isdigit() and int(declared)>limit:
            return await JSONResponse({'detail':{'code':'file_too_large','message':f'本次上传超过 {limit//1_000_000} MB 总上限，请拆分文件。'}},status_code=413)(scope,receive,send)
        chunks=[]; size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect': return
            size+=len(message.get('body',b''))
            if size>limit: return await JSONResponse({'detail':{'code':'file_too_large','message':'上传超过安全上限，已停止。'}},status_code=413)(scope,receive,send)
            chunks.append(message)
            if not message.get('more_body'): break
        index=0
        async def replay():
            nonlocal index
            if index<len(chunks):
                message=chunks[index]; index+=1; return message
            return await receive()
        await self.app(scope,replay,send)
