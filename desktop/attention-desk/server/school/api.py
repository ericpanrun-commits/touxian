"""Desktop-only learning pilot; existing Inbox intake owns original files."""
from uuid import uuid4
import asyncio
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import Field
from ..personal_store import PersonalError
from ..wechat_collect.api import LocalRoute, desktop_write
from .boundary import local_upload
from .models import (Strict, LectureInput, BookFileInput, SectionInput, SectionLinks,
                     GenerateInput, VisionInput, FeedbackInput, VersionInput, PageAuditInput, AcceptanceInput, SourceCorrectionInput,
                     COURSE,BookMetadataInput)
from .export import context_pack
from .proposals import stage_proposals

router = APIRouter(prefix='/api/personal/school', tags=['school'], route_class=LocalRoute,
                   dependencies=[Depends(local_upload)])
write = [Depends(desktop_write)]


def service_for(request: Request):
    service = getattr(request.app.state, 'school_service', None)
    if service is None: raise PersonalError('课堂服务尚未就绪，请重启应用。', 'not_ready', 503)
    return service


class JobRequest(Strict):
    request_id: str
    retry: bool = False


class NoteRegister(Strict):
    raw_item_id: str
    request_id: str
    course_id: str = Field(default=COURSE,min_length=1,max_length=256)


@router.get('')
def overview(limit:int=Query(20,ge=1,le=20), offset:int=Query(0,ge=0),course_id:str=COURSE, service=Depends(service_for)):
    return service.store.overview(limit,offset,course_id=course_id)


@router.get('/notes')
def notes(course_id:str=COURSE,service=Depends(service_for)):
    return {'items':service.store.note_choices(course_id)}


@router.post('/notes/register', dependencies=write)
def register_note(payload:NoteRegister, service=Depends(service_for)):
    # Registration is local metadata only; it never changes the Inbox interpretation.
    doc=service.store._note_document(payload.raw_item_id,payload.course_id,require_ready=False)
    return {'document':doc,'job':service.start_catalog(doc['id'],payload.request_id)}


@router.post('/lectures', dependencies=write, status_code=201)
def create_lecture(payload:LectureInput, idempotency_key:str=Header(), service=Depends(service_for)):
    return service.store.create_lecture(payload,idempotency_key)


@router.get('/lectures/{lecture_id}')
def lecture(lecture_id:str, service=Depends(service_for)):
    result=service.store.lecture(lecture_id)
    result['ai_usage']=service.cost(lecture_id=lecture_id)
    return result


@router.get('/textbook')
def book(course_id:str=COURSE,service=Depends(service_for)):
    return service.store.book(course_id)


@router.get('/textbooks')
def textbooks(course_id:str|None=None,service=Depends(service_for)):
    return {'items':service.store.books(course_id)}


@router.get('/textbooks/{textbook_id}')
def textbook(textbook_id:str,service=Depends(service_for)):
    return service.store.textbook(textbook_id)


@router.post('/textbooks',dependencies=write,status_code=201)
def register_textbook(payload:BookMetadataInput,idempotency_key:str=Header(),service=Depends(service_for)):
    return service.store.register_book(payload,idempotency_key)


@router.post('/textbook/register-canonical', dependencies=write)
async def register_canonical(service=Depends(service_for)):
    from .library import register_canonical as register
    return await asyncio.to_thread(register,service)


@router.post('/textbook/acceptance', dependencies=write)
def accept_textbook(payload:AcceptanceInput,service=Depends(service_for)):
    from ..personal_store import encode,now
    from .models import TEXTBOOK
    book=service.store.book();artifact=service.artifacts.get(payload.artifact_id)
    if book['version']!=payload.expected_version or not book['document'] or book['document']['sha256']!=payload.document_sha256:
        raise PersonalError('验收对应的教材版本已变化。','conflict',409)
    if book['is_fixture'] or artifact['is_fixture'] or not artifact['content'] or artifact['lecture_version_changed']:
        raise PersonalError('测试材料、未完成或来源已变化的复盘不能登记真实验收通过。','review_incomplete',409)
    source_book=artifact['input_versions'].get('textbook')
    if not source_book or source_book['id']!=book['id'] or source_book['document_id']!=book['document_id']:
        raise PersonalError('复盘没有使用当前正式教材。','source_scope',400)
    required={'notes','definition','important_limit','method_conditions','textbook_supplement'}
    if {c.kind for c in payload.citation_checks}!=required:
        raise PersonalError('需要课堂笔记和四类教材引用的实际抽查记录。','citation_checks_required',400)
    sources={s['source_id']:s for s in artifact['sources']}
    for check in payload.citation_checks:
        source=sources.get(check.source_id)
        if not source or source['page']!=check.page or source['source_kind']!=('notes' if check.kind=='notes' else 'textbook'):
            raise PersonalError('抽查来源与本次复盘不一致。','source_scope',400)
    with service.personal.connection(write=True) as c:
        c.execute("UPDATE textbook SET acceptance_status='accepted',acceptance_json=?,accepted_at=?,updated_at=? WHERE id=? AND version=?",
                  (encode(payload.model_dump()),now(),now(),TEXTBOOK,payload.expected_version))
    return service.store.book()


@router.put('/textbook/file', dependencies=write)
def book_file(payload:BookFileInput, service=Depends(service_for)):
    book=service.store.link_book(payload.raw_item_id,payload.expected_version,payload.file_role,textbook_id=payload.textbook_id)
    return {'textbook':book, 'job':service.start_catalog(book['document_id'],str(uuid4()))}


@router.post('/sections', dependencies=write, status_code=201)
def add_section(payload:SectionInput, service=Depends(service_for)):
    return service.store.add_section(payload)


@router.put('/lectures/{lecture_id}/sections', dependencies=write)
def map_sections(lecture_id:str, payload:SectionLinks, service=Depends(service_for)):
    return service.store.link_sections(lecture_id,payload.expected_version,payload.section_ids)


@router.get('/lectures/{lecture_id}/section-suggestions')
def suggestions(lecture_id:str, service=Depends(service_for)):
    return {'items':service.retrieval.suggest(service.store.lecture(lecture_id)), 'automatic_confirmation':False}


@router.post('/sections/{section_id}/index', dependencies=write, status_code=202)
def index(section_id:str,payload:JobRequest,service=Depends(service_for)):
    return service.start_index(section_id,payload.request_id,retry=payload.retry)


@router.post('/documents/{doc_id}/catalog', dependencies=write, status_code=202)
def catalog(doc_id:str,payload:JobRequest,service=Depends(service_for)):
    return service.start_catalog(doc_id,payload.request_id,retry=payload.retry)


@router.get('/documents/{doc_id}/pages')
def pages(doc_id:str,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0),service=Depends(service_for)):
    return {'items':service.learning.pages(doc_id,limit=limit,start=offset+1),'document':service.learning.get(doc_id)}


@router.get('/documents/{doc_id}/pages/{page}/image')
def image(doc_id:str,page:int,service=Depends(service_for)):
    return FileResponse(service.learning.image_path(doc_id,page),media_type='image/png',headers={'Cache-Control':'private, max-age=3600'})


@router.post('/documents/{doc_id}/pages/{page}/audit-transcription', dependencies=write)
def audit_transcription(doc_id:str,page:int,payload:PageAuditInput,service=Depends(service_for)):
    from ..personal_store import encode,now
    doc=service.learning.get(doc_id);current=service.learning.page(doc_id,page)
    if doc['sha256']!=payload.document_sha256 or current['version']!=payload.expected_version:
        raise PersonalError('原件或本页提取版本已变化，请先重新核对。','conflict',409)
    text='\n\n'.join(payload.paragraphs)
    result=service.learning.store_page(doc_id,page,{'extracted_text':text,'blocks':[{'text':p,'extraction_quality':'vision_uncertain' if i in payload.uncertain_paragraphs else 'visually_checked'} for i,p in enumerate(payload.paragraphs)]},
        method='visual_audit_transcription',quality='visually_checked',metadata={'reviewer':payload.reviewer,
            'audit_render_sha256':payload.audit_render_sha256,'original_sha256':doc['sha256'],
            'coverage':'selected visually checked paragraphs; original OCR retained','user_adopted':False,'uncertainties':payload.uncertainties})
    if payload.uncertain_paragraphs:
        with service.personal.connection(write=True) as c:
            c.execute("UPDATE document_page SET vision_status='uncertain',status='needs_review' WHERE document_id=? AND page_index=?",(doc_id,page))
    if payload.printed_page_label:
        with service.personal.connection(write=True) as c:
            c.execute("UPDATE document_page SET printed_page_label=?,label_origin='visually_checked' WHERE document_id=? AND page_index=?",
                      (payload.printed_page_label,doc_id,page))
    return service.learning.page(doc_id,page)


@router.get('/jobs/{job_id}')
def job(job_id:str,service=Depends(service_for)):
    result=service.jobs.get(job_id)
    result['cost']=service.cost(job_id)
    return result


@router.get('/lectures/{lecture_id}/retrieve')
def retrieve(lecture_id:str,q:str=Query(min_length=1,max_length=2000),service=Depends(service_for)):
    result=service.retrieval.search(q,lecture_id=lecture_id)
    return {**result,'items':[service.contexts.block_source(b,'textbook',title=b['textbook_title'],section=b['section_title']) for b in result['items']]}


@router.get('/lectures/{lecture_id}/context')
def context(lecture_id:str,service=Depends(service_for)):
    return service.contexts.build(lecture_id,allow_incomplete=True)


@router.get('/lectures/{lecture_id}/page-source')
def page_source(lecture_id:str,document_id:str,page:int=Query(ge=1,le=2000),service=Depends(service_for)):
    lecture=service.store.lecture(lecture_id)
    notes={d['id'] for d in lecture['notes']}
    sections=service.retrieval.sections(lecture_id)
    if document_id not in notes and not any(s['document_id']==document_id and s['start_page']<=page<=s['end_page'] for s in sections):
        raise PersonalError('请选择本次笔记或已确认教材章节内的页面。','source_scope',400)
    doc=service.learning.get(document_id)
    section=next((s for s in sections if s['document_id']==document_id and s['start_page']<=page<=s['end_page']),None)
    return service.contexts.image_source(doc,page,'notes' if document_id in notes else 'textbook',
                title=section['textbook_title'] if document_id not in notes and section else None,
                section=section['title'] if section else None)


@router.get('/lectures/{lecture_id}/cost')
def quote(lecture_id:str,service=Depends(service_for)):
    return service.quote_cost(lecture_id)


@router.get('/lectures/{lecture_id}/context-pack')
def export(lecture_id:str,service=Depends(service_for)):
    return Response(context_pack(service,lecture_id),media_type='application/zip',
                    headers={'Content-Disposition':f'attachment; filename="lecture-{lecture_id}-context.zip"','Cache-Control':'no-store'})


@router.post('/lectures/{lecture_id}/generate', dependencies=write, status_code=202)
async def generate(lecture_id:str,payload:GenerateInput,service=Depends(service_for)):
    return await service.generate(lecture_id,payload)


@router.post('/lectures/{lecture_id}/vision', dependencies=write, status_code=202)
async def vision(lecture_id:str,payload:VisionInput,service=Depends(service_for)):
    return await service.start_vision(lecture_id,payload)


@router.get('/reviews/{artifact_id}')
def review(artifact_id:str,service=Depends(service_for)):
    return service.artifacts.get(artifact_id)


@router.post('/reviews/{artifact_id}/feedback', dependencies=write)
def feedback(artifact_id:str,payload:FeedbackInput,service=Depends(service_for)):
    return service.artifacts.feedback(artifact_id,payload)


@router.post('/reviews/{artifact_id}/proposals', dependencies=write)
def proposals(artifact_id:str,payload:VersionInput,service=Depends(service_for)):
    return stage_proposals(service,artifact_id,payload.expected_version)


@router.post('/reviews/{artifact_id}/source-correction', dependencies=write)
def source_correction(artifact_id:str,payload:SourceCorrectionInput,service=Depends(service_for)):
    from .source_correction import save_correction
    return save_correction(service.personal,artifact_id,payload)
