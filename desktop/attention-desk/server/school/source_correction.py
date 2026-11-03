"""Explicit source checking of retained text; no provider, paid retry or adoption."""
import copy
import json
from .documents import ReadDocuments, digest
from .context import ContextBuilder
from .retrieval import Retrieval
from .store import SchoolStore, request_id
from .profiles import profile_for
from .protocol import SLOT, ProtocolFailure, compose_review, extract_evidence, apply_targeted_repair
from ..extraction_errors import ExtractionFailure
from ..personal_store import PersonalError, encode, identifier, now

AREAS={'understanding_gaps':'理解核对','method_cards':'方法卡片','core_concepts':'本节核心',
    'notes_taught':'课堂笔记','must_master':'必须掌握','textbook_supplement':'教材补充',
    'connections':'旧知识联系','self_tests':'自测','suggested_actions':'复习建议'}
TEXT_FIELDS={'text','signal','method','conditions','common_misuse','question','answer','name'}


def at(document, path):
    value=document
    for part in path.strip('/').split('/'):
        value=value[int(part)] if isinstance(value,list) else value[part]
    return value


def retained_candidate(c, artifact_id):
    row=c.execute('SELECT * FROM review_artifact WHERE id=?',(artifact_id,)).fetchone()
    if not row or row['content_json'] is not None or row['status']!='needs_review':return None
    versions=json.loads(row['input_versions_json']);generation=versions.get('generation') or {}
    if json.loads(row['error_json'] or '{}').get('stage')!='composition':return None
    base={k:v for k,v in versions.items() if k!='generation'}
    source=row
    if generation.get('recovered_from'):
        source=c.execute('SELECT * FROM review_artifact WHERE id=?',(generation['recovered_from'],)).fetchone()
        if not source or source['lecture_id']!=row['lecture_id']:return None
        old=json.loads(source['input_versions_json'])
        if {k:v for k,v in old.items() if k!='generation'}!=base:return None
        if old.get('generation',{}).get('recovered_from'):return None
    old=json.loads(source['input_versions_json']).get('generation') or {}
    records=old.get('attempts') or []
    if len(records)!=2 or [x.get('stage') for x in records]!=['evidence','composition']:return None
    attempts=[dict(r) for r in c.execute('SELECT * FROM school_ai_attempt WHERE job_id IN (?,?)',(source['job_id'],row['job_id']))]
    if not attempts or any(a['status'] not in {'completed','failed'} for a in attempts):return None
    selected=[next((a for a in attempts if a['id']==record.get('id')),None) for record in records]
    if any(a is None or not a['raw_output'] for a in selected):return None
    evidence_attempt,composition_attempt=selected
    context={'lecture':base['lecture'],'sources':json.loads(row['source_evidence_json']),'mistakes':{'available':False},'before_lecture':base.get('before_lecture')}
    profile=profile_for(context)
    composition_versions={'lecture-fact-composition-cycle-v5','lecture-fact-composition-cycle-v6'} if profile.requires_textbook else {profile.composition}
    if (evidence_attempt['status']!='completed' or evidence_attempt['prompt_version']!=profile.evidence
            or composition_attempt['status']!='failed' or composition_attempt['prompt_version'] not in composition_versions):return None
    evidence=extract_evidence(evidence_attempt['raw_output'],context)
    if evidence!=generation.get('evidence_extraction'):return None
    raw=composition_attempt['raw_output']
    try:compose_review(raw,evidence,context)
    except ProtocolFailure as failure:original_failure=failure
    else:return None
    repairs=[a for a in attempts if a['prompt_version'].startswith('lecture-targeted-repair-')]
    if generation.get('repair_used'):
        if len(repairs)!=1 or not repairs[0]['raw_output']:return None
        raw=apply_targeted_repair(repairs[0]['raw_output'],raw,original_failure)
    elif repairs:return None
    try:compose_review(raw,evidence,context)
    except ProtocolFailure as failure:issues=failure.issues
    else:return None
    draft=json.loads(raw);fields=[];seen=set();facts={f['fact_id']:f for f in evidence['facts']}
    for issue in issues:
        key=issue['path'];parts=key.strip('/').split('/')
        if len(parts)<3 or parts[0] not in AREAS or not parts[1].isdigit() or parts[-1] not in TEXT_FIELDS:return None
        value=at(draft,key)
        if not isinstance(value,str):return None
        if key in seen:continue
        seen.add(key);split=SLOT.split(value);slots=split[1::2]
        if len(slots)>4:return None
        item=at(draft,'/'+ '/'.join(parts[:2]))
        if parts[0]=='core_concepts':item=item.get('explanation',item)
        sources=[facts[fid]['source_id'] for fid in item.get('fact_ids',[]) if fid in facts]
        fields.append({'key':key,'label':AREAS[parts[0]]+' · 第 '+str(int(parts[1])+1)+' 项',
            'segments':split[::2],'slots':slots,'source_ids':sources,
            'max_length':{'name':100,'signal':300,'common_misuse':400,'question':500}.get(parts[-1],600),
            'issues':[i['message'] for i in issues if i['path']==key]})
    if not fields or len(fields)>8:return None
    fingerprint=digest(encode({'artifact_id':artifact_id,'draft':draft,'inputs':base,'evidence':evidence}))
    return {'row':dict(row),'base':base,'generation':generation,'context':context,'evidence':evidence,'draft':draft,
        'public':{'draft_hash':fingerprint,'expected_version':base['lecture']['version'],'fields':fields},'draft_hash':fingerprint}


def descriptor(c, artifact_id):
    try:
        candidate=retained_candidate(c,artifact_id)
        return candidate['public'] if candidate else None
    except (ExtractionFailure,ValueError,KeyError,TypeError,IndexError):return None


def save_correction(personal, artifact_id, payload):
    """One transaction for exact input checks, append-only version and request receipt."""
    key=request_id(payload.request_id)
    signature=digest(encode({'parent':artifact_id,**payload.model_dump(exclude={'request_id'})}))
    with personal.connection(write=True) as c:
        old=SchoolStore._request(c,key,'school_source_correction',signature)
        if old:return {'artifact_id':old,'saved':True}
        same=c.execute("SELECT id FROM review_artifact WHERE provider='source_checked' AND input_hash=? AND content_json IS NOT NULL",(signature,)).fetchone()
        if same:
            SchoolStore._request(c,key,'school_source_correction',signature,same['id'])
            return {'artifact_id':same['id'],'saved':True}
        try:candidate=retained_candidate(c,artifact_id)
        except (ExtractionFailure,ValueError,KeyError,TypeError,IndexError):candidate=None
        if not candidate:raise PersonalError('这份草稿不能安全接续校订，原件与已有版本保留。','correction_unavailable',409)
        parent=candidate['row'];lecture=SchoolStore._lecture(c,parent['lecture_id'],payload.expected_version)
        if payload.draft_hash!=candidate['draft_hash']:
            raise PersonalError('校订对应的草稿已变化，请重新读取后核对。','conflict',409)
        # Share the existing transaction view used by durable domain commands.
        from ..cloud_sync.commands import TransactionView
        view=TransactionView(personal,c);documents=ReadDocuments(view)
        context=ContextBuilder(view,SchoolStore(view,None),documents,Retrieval(view,documents),persist_sources=False).build(parent['lecture_id'])
        if context['input_versions']!=candidate['base'] or context['context_id']!=candidate['generation'].get('context_id'):
            raise PersonalError('来源或学习记录已有更新，请先核对新来源。原校订保留。','conflict',409)
        fields={f['key']:f for f in candidate['public']['fields']}
        if len({change.key for change in payload.changes})!=len(payload.changes):
            raise PersonalError('同一项目只需校订一次。','invalid_correction',400)
        draft=copy.deepcopy(candidate['draft']);audit=[]
        for change in payload.changes:
            field=fields.get(change.key)
            if not field or len(change.segments)!=len(field['segments']) or any('[[' in s or ']]' in s for s in change.segments):
                raise PersonalError('请只修改需要核对的说明；原文引用由系统保留。','invalid_correction',400)
            text=change.segments[0]
            for slot,segment in zip(field['slots'],change.segments[1:]):text+='[['+slot+']]'+segment
            target=at(draft,'/'+change.key.strip('/').rsplit('/',1)[0]);field_name=change.key.rsplit('/',1)[1]
            before=target[field_name];target[field_name]=text
            if before!=text:audit.append({'key':change.key,'before':before,'after':text})
        if not audit:raise PersonalError('请先填写校订后的说明。','unchanged_correction',400)
        try:content,sources,validation=compose_review(encode(draft),candidate['evidence'],context)
        except ProtocolFailure as failure:
            raise PersonalError('校订后仍有来源或表述问题，输入已保留：'+'；'.join(dict.fromkeys(i['message'] for i in failure.issues)),
                'correction_validation',409) from None
        version=c.execute('SELECT COALESCE(max(version),0)+1 FROM review_artifact WHERE lecture_id=?',(lecture['id'],)).fetchone()[0]
        aid,job_id,stamp=identifier(),identifier(),now()
        original_job=c.execute('SELECT * FROM attachment_job WHERE id=?',(parent['job_id'],)).fetchone()
        if not original_job:raise PersonalError('原处理记录暂不可核对。','correction_unavailable',409)
        revision={'parent_artifact_id':artifact_id,'reviewer':payload.reviewer,'created_at':stamp,'draft_hash':candidate['draft_hash'],
            'changes':audit,'source_quality_upgraded':False,'new_model_calls':0}
        versions=copy.deepcopy(candidate['base']);versions['source_checked_revision']=revision
        versions['generation']={'mode':'source_correction','recovered_from':artifact_id,'context_id':context['context_id'],
            'validation_status':'passed','validation':validation,'model_calls':0}
        cost={'estimated_cost_cny':0,'image_calls':0,'text_calls':0,'images':0,'attempts':[],'stages':{}}
        c.execute('''INSERT INTO attachment_job(id,raw_item_id,source_snapshot_id,attachment_ids_json,operation,status,phase,
            created_at,completed_at,school_payload_json,school_result_json,school_fingerprint,school_request_id)
            VALUES (?,?,?,?,'school_source_correction','completed','来源校订已核验保存，待核对采用',?,?,?,?,?,?)''',
            (job_id,original_job['raw_item_id'],original_job['source_snapshot_id'],original_job['attachment_ids_json'],stamp,stamp,
                encode({'lecture_id':lecture['id'],'parent_artifact_id':artifact_id}),encode({'artifact_id':aid,'validation':'passed','new_model_calls':0}),signature,key))
        c.execute('''INSERT INTO review_artifact(id,lecture_id,version,content_json,generated_at,provider,model,prompt_version,
            input_versions_json,source_evidence_json,input_hash,status,cost_json,job_id,created_at)
            VALUES (?,?,?,?,?,'source_checked','source_check','source-correction-v1',?,?,?,'needs_review',?,?,?)''',
            (aid,lecture['id'],version,encode(content),stamp,encode(versions),encode(sources),signature,encode(cost),job_id,stamp))
        SchoolStore._request(c,key,'school_source_correction',signature,aid)
        c.execute("UPDATE lecture SET status=CASE WHEN accepted_artifact_id IS NULL THEN 'needs_review' ELSE status END,updated_at=? WHERE id=?",(stamp,lecture['id']))
    return {'artifact_id':aid,'saved':True}
