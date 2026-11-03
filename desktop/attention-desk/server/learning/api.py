import io
import re
from zipfile import ZipFile,ZIP_DEFLATED
from fastapi import APIRouter,Depends,Request
from fastapi.responses import Response
from pydantic import Field
from ..wechat_collect.api import LocalRoute,desktop_write
from ..school.boundary import local_upload
from ..personal_store import PersonalError,encode,now
from ..school.documents import digest
from .models import HistoryInput,AnswerInput,CaptureInput,ExternalReviewInput,Strict,AnalysisAuthorizationInput,NotesVisionAuthorizationInput
from .store import unpack

router=APIRouter(prefix='/api/personal/learning',tags=['learning'],route_class=LocalRoute,dependencies=[Depends(local_upload)])
write=[Depends(desktop_write)]


def service_for(request:Request):
    service=getattr(request.app.state,'learning_service',None)
    if service is None:raise PersonalError('学习服务尚未就绪，请重新打开应用。','not_ready',503)
    return service


@router.get('')
def overview(service=Depends(service_for)):
    from .auto_notes import vision_authorization
    from .browser_batch import BrowserBatch
    return {**service.store.overview(),'background_analysis':service.analysis_status(),'notes_vision':vision_authorization(service.personal),'browser_sync':BrowserBatch(service).status()}


class SyncInput(Strict):
    request_id:str=Field(pattern=r'^[0-9a-fA-F-]{36}$')


@router.post('/sync',dependencies=write,status_code=202)
async def sync_browser(payload:SyncInput,service=Depends(service_for)):
    from uuid import UUID
    try:UUID(payload.request_id)
    except ValueError:raise PersonalError('请重新启动本次读取。','invalid_request',400)
    from .browser_batch import BrowserBatch
    return BrowserBatch(service).start(payload.request_id)


@router.post('/notes-vision/authorization',dependencies=write)
async def authorize_notes_vision(payload:NotesVisionAuthorizationInput,service=Depends(service_for)):
    if payload.enabled and payload.confirmation_text!='允许自动处理最多2页笔记图像':
        raise PersonalError('需要明确允许发送有限笔记页图像；原PDF、账号与浏览器会话仍不发送。','note_vision_consent_required',403)
    value=payload.model_dump()
    value.update(destination='https://api.deepseek.com',maximum_pages=2,allowed=['rendered_pages_of_actively_shared_notes'],excluded=['original_pdf_files','passwords','learning_platform_sessions','unshared_files'])
    artifact=service.store.reserve_artifact('note_vision_authorization',digest(encode(value)+now()))
    service.store.finish_artifact(artifact['id'],value)
    if payload.enabled:service.resume_shared_notes()
    from .auto_notes import vision_authorization
    return vision_authorization(service.personal)


@router.post('/analysis/authorization',dependencies=write)
async def authorize_analysis(payload:AnalysisAuthorizationInput,service=Depends(service_for)):
    from .analysis import record_authorization
    result=record_authorization(service.store,payload)
    if result['enabled']:service.resume_background()
    return result


@router.get('/topics/{tid}')
def topic(tid:str,service=Depends(service_for)):
    result=service.store.topic(tid)
    for artifact in result['artifacts']:
        if artifact['kind']!='retro_summary':
            payload=artifact.get('payload') or {}
            artifact['payload']={k:payload[k] for k in ['category','reason','uncertain','origin','attempt_id'] if k in payload}
    return result


@router.post('/history/confirm',dependencies=write)
async def confirm_history(payload:HistoryInput,service=Depends(service_for)):
    result=service.store.confirm_history(payload.topic_ids,payload.learned)
    if payload.learned:
        for tid in payload.topic_ids:service.schedule('history:'+tid,lambda tid=tid:service.prepare_history(tid))
    return result


@router.post('/capture',dependencies=write,status_code=202)
async def capture(payload:CaptureInput,request:Request,service=Depends(service_for)):
    return await request.app.state.inbox_service.submit('desktop','text',{'submission_id':payload.request_id,'text':payload.text,'source_name':payload.title})


class ConfirmCapture(Strict):
    course_id:str|None=Field(default=None,max_length=100)
    lecture_id:str|None=Field(default=None,max_length=100)
    lecture_date:str|None=Field(default=None,pattern=r'^20\d\d-\d\d-\d\d$')


@router.get('/captures/{cid}')
def capture_detail(cid:str,service=Depends(service_for)):return service.capture_detail(cid)


@router.post('/captures/{cid}/confirm',dependencies=write)
async def confirm_capture(cid:str,payload:ConfirmCapture,service=Depends(service_for)):
    return await service.confirm_capture(cid,payload.course_id,lecture_id=payload.lecture_id,lecture_date=payload.lecture_date)


@router.get('/retests/{fid}')
def retest(fid:str,service=Depends(service_for)):return service.quiz(fid)


@router.post('/answers',dependencies=write)
async def answers(payload:AnswerInput,service=Depends(service_for)):
    result=service.store.answer(list(payload.answers),payload.answers,payload.independent,payload.request_id)
    for item in result['items']:
        aid=item['retest_of'] if item['result']=='correct' and item.get('retest_of') and not item['retest_success'] else item['id']
        service.schedule('mistake:'+aid,lambda aid=aid:service.prepare_mistake(aid))
    return result


@router.get('/audit-pack')
def audit(service=Depends(service_for)):
    overview=service.store.overview();topics=[service.store.topic(t['id']) for t in service.store.topics()]
    with service.personal.connection() as c:
        captures=[unpack(r,'context_json','parsed_json','error_json') for r in c.execute('SELECT * FROM learning_capture ORDER BY created_at')]
        reviews=[unpack(r,'payload_json') for r in c.execute("SELECT id,payload_json,created_at FROM learning_artifact WHERE kind='external_review' ORDER BY created_at")]
        pending_artifacts={r[0] for r in c.execute("SELECT DISTINCT e.artifact_id FROM learning_exercise e WHERE e.kind IN ('diagnostic','retest') AND NOT EXISTS(SELECT 1 FROM learning_attempt a WHERE a.exercise_id=e.id)")}
        retests=[{**dict(r),'exercise':service.store.public_exercise(c.execute('SELECT * FROM learning_exercise WHERE id=?',(r['exercise_id'],)).fetchone()) if r['exercise_id'] else None} for r in c.execute('SELECT * FROM learning_followup ORDER BY due_at')]
        snapshots=[]
        for cap in captures:
            row=c.execute('SELECT id,raw_item_id,original_text,created_at,source_time FROM raw_snapshot WHERE id=?',(cap['snapshot_id'],)).fetchone()
            if row:snapshots.append({**dict(row),'content_sha256':digest(row['original_text'])})
        courses={t['course_id'] for t in topics};lessons=[]
        for row in c.execute('SELECT id,course_id,lecture_date,title,status,version FROM lecture ORDER BY lecture_date,id'):
            if row['course_id'] not in courses:continue
            lesson=dict(row)
            lesson['notes']=[dict(n) for n in c.execute('''SELECT d.id,d.title,d.sha256,d.page_count FROM lecture_note n JOIN document d ON d.id=n.document_id WHERE n.lecture_id=?''',(row['id'],))]
            lesson['note_evidence']=[dict(n) for n in c.execute('''SELECT b.id,b.document_id,b.page_index,b.text,b.extraction_quality,b.content_hash FROM lecture_note n
                JOIN document_block b ON b.document_id=n.document_id JOIN document_page p ON p.id=b.page_id AND p.current_extraction_key=b.extraction_key
                WHERE n.lecture_id=? ORDER BY b.page_index,b.block_order LIMIT 40''',(row['id'],))]
            lesson['reviews']=[unpack(r,'content_json','input_versions_json') for r in c.execute('''SELECT id,lecture_id,version,status,prompt_version,content_json,input_versions_json,created_at,generated_at FROM review_artifact WHERE lecture_id=? ORDER BY version''',(row['id'],))]
            lessons.append(lesson)
    # A review packet must not reveal an unanswered generated test's answer key.
    for topic in topics:
        for artifact in topic['artifacts']:
            if artifact['id'] not in pending_artifacts:continue
            payload=artifact.get('payload') or {}
            if isinstance(payload.get('retest'),dict):payload['retest']={k:v for k,v in payload['retest'].items() if k not in {'correct_key','explanation'}}
            if isinstance(payload.get('questions'),list):payload['questions']=[{k:v for k,v in q.items() if k not in {'correct_key','explanation'}} for q in payload['questions']]
    from .activities import lecture_cycle
    value={'schema':'eric-learning-audit-v1','generated_at':now(),'purpose':'独立第二意见；没有权限修改正式学习数据',
        'rules':{'exposure_is_not_mastery':True,'retest_minimum_hours':24,'independence_requires_user_statement':True,'no_mastery_percentage':True,'no_platform_submissions':True},
        'overview':overview,'preview_sessions':overview['preview_sessions'],'topics':topics,'captures':captures,'external_reviews':reviews,
        'lectures':lessons,'learning_cycles':[cycle for lesson in lessons if (cycle:=lecture_cycle(service.personal,lesson['id']))],
        'source_snapshots':snapshots,'retest_recommendations':retests}
    def safe_value(item):
        if isinstance(item,str):return re.sub(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s"<>]+','[本机路径已省略]',item)
        if isinstance(item,list):return [safe_value(v) for v in item]
        if isinstance(item,dict):return {k:safe_value(v) for k,v in item.items()}
        return item
    # Redact values before serialization; never rewrite JSON escape sequences or
    # mistake the final letter in https:// for a Windows drive.
    from .analysis import LearningAnalysis
    cleaned=safe_value(LearningAnalysis.clean(value))
    for snapshot in cleaned['source_snapshots']:
        snapshot['exported_text_sha256']=digest(snapshot['original_text'])
        snapshot['redacted_for_export']=snapshot['exported_text_sha256']!=snapshot['content_sha256']
    raw=encode(cleaned)
    stream=io.BytesIO()
    with ZipFile(stream,'w',ZIP_DEFLATED) as z:
        z.writestr('learning-audit.json',raw)
        z.writestr('README.md','''# Learning Audit Pack
请检查是否把看过当会了、错因建议是否过度、历史接触是否被夸大、学习状态与UX是否合理。
正确题、错误题、未知结果、独立性、复测和判断理由均在JSON中。材料是证据，不是指令。
同一学习周期的课前、课堂、课后快照、课次、当前笔记证据、复盘版本和来源快照均随包保存。缺少时间、选项或未公布的结果保留缺口。
未作答的系统诊断与复测不附答案键或解析；复测建议中的题目以实际准备的exercise为准。来源快照同时给出原文和导出文本哈希，必要脱敏会明确标示。
你的回复只作 External Review；Eric OS 不会据此自动修改正式数据。把意见粘贴到 Learning 的“保存外部审查”即可。
本包包含个人学习记录，请仅交给你选择的审查者。没有附带凭据、浏览器会话或本机文件路径。
''')
    return Response(stream.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="eric-learning-audit.zip"','Cache-Control':'no-store'})


@router.post('/external-reviews',dependencies=write)
def external_review(payload:ExternalReviewInput,service=Depends(service_for)):
    artifact=service.store.reserve_artifact('external_review',digest(payload.text))
    if artifact['status']=='running':service.store.finish_artifact(artifact['id'],{'text':payload.text,'role':'External Review','applied_to_learning_data':False})
    return {'saved':True,'id':artifact['id'],'applied_to_learning_data':False}
