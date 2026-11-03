"""One-course orchestration using the existing parser executor and Job table."""
from __future__ import annotations
import asyncio
import json
import logging
import threading
from pathlib import Path
from uuid import uuid4
from pydantic import ValidationError
from ..ai_transport import strict_json
from ..extraction_errors import ExtractionFailure
from ..inbox_cost import estimate
from ..personal_store import PersonalError, encode, identifier, now
from .artifacts import ReviewArtifacts
from .context import ContextBuilder
from .documents import LearningDocuments, digest
from .jobs import SchoolJobs
from .models import PROMPT_VERSION, VISION_PROMPT_VERSION, PROCESS_VERSION, VisionOutput, require_course
from .provider import SchoolProvider
from .retrieval import Retrieval
from .store import SchoolStore, request_id
from .profiles import MATH as MATH_PROFILE, GENERAL, profile_for
from .validation import validate_review
from .protocol import (EVIDENCE_PROMPT_VERSION, COMPOSITION_PROMPT_VERSION, REPAIR_PROMPT_VERSION,
                       ProtocolFailure, catalog, extract_evidence, compose_review, apply_targeted_repair, repair_roots)

STAGE_PROMPTS = {'evidence': EVIDENCE_PROMPT_VERSION, 'composition': COMPOSITION_PROMPT_VERSION, 'repair': REPAIR_PROMPT_VERSION}


def require_note_text(context, *, allow_vision=False):
    if profile_for(context).requires_textbook:
        return
    if any(s['source_kind'] == 'notes' and s['kind'] == 'text' and s['text'].strip() for s in context['sources']):
        return
    note_ids = {d['id'] for d in context['input_versions']['documents']}
    if allow_vision and any(ref['document_id'] in note_ids for ref in context['needs_vision']):
        return
    raise PersonalError('原件已保留，但还没有可读的笔记文字。请先核对原页或允许少量页面识别，再生成复盘。', 'notes_text_required', 409)


class SchoolService:
    def __init__(self, personal, documents, extraction, *, provider=None, fixture_mode=False, mistake_reader=None):
        self.personal, self.documents, self.extraction = personal, documents, extraction
        self.learning = LearningDocuments(personal, documents)
        self.store = SchoolStore(personal, self.learning)
        self.jobs, self.artifacts = SchoolJobs(personal), ReviewArtifacts(personal)
        self.retrieval = Retrieval(personal, self.learning)
        self.contexts = ContextBuilder(personal, self.store, self.learning, self.retrieval, mistake_reader)
        self.provider, self.fixture_mode = provider or SchoolProvider(), fixture_mode
        self.tasks, self.futures = {}, {}
        self.lock, self.semaphore = asyncio.Lock(), asyncio.Semaphore(1)
        self.stopping = threading.Event()
        self.local_recovery_task = None
        self.jobs.recover()

    def _schedule_local(self, job_id, runner):
        if self.stopping.is_set() or job_id in self.futures: return
        future = self.documents.executor.submit(runner, job_id)
        self.futures[job_id] = future
        future.add_done_callback(lambda _: self.futures.pop(job_id, None))

    def start_local_recovery(self):
        if self.local_recovery_task is None:
            self.local_recovery_task=asyncio.create_task(self._local_recovery_loop())

    async def _local_recovery_loop(self):
        while not self.stopping.is_set():
            try:await asyncio.to_thread(self.recover_local_once)
            except asyncio.CancelledError:raise
            except Exception:logging.getLogger(__name__).warning('Local school recovery will retry; saved originals retained.')
            await asyncio.sleep(15)

    def recover_local_once(self):
        """Bounded resume of registered local work, with zero AI entry points."""
        with self.personal.connection() as c:
            rows=c.execute("""SELECT j.* FROM attachment_job j WHERE j.status='recovery_needed'
                AND j.operation IN ('school_catalog','school_index')
                AND NOT EXISTS(SELECT 1 FROM school_ai_attempt a WHERE a.job_id=j.id)
                ORDER BY j.created_at,j.id LIMIT 8""").fetchall()
        for row in rows:
            if self.stopping.is_set():return
            try:
                payload=json.loads(row['school_payload_json'] or '{}')
                doc=self.learning.get(payload['document_id'])
                if payload.get('processing_version')!=PROCESS_VERSION:raise ValueError('processing version changed')
                if row['operation']=='school_index':
                    pages=payload.get('pages')
                    if not isinstance(pages,list) or not pages or any(type(p) is not int or p<1 or p>doc['page_count'] for p in pages):raise ValueError('page scope changed')
                    if payload.get('section_id'):
                        with self.personal.connection() as c:
                            section=c.execute('''SELECT s.* FROM document_section s JOIN textbook t ON t.id=s.textbook_id
                                WHERE s.id=? AND s.document_id=t.document_id''',(payload['section_id'],)).fetchone()
                        if not section or section['document_id']!=doc['id'] or pages!=list(range(section['start_page'],section['end_page']+1)) or len(pages)>80:raise ValueError('chapter changed')
                    elif payload.get('purpose')!='lecture_note' or pages!=list(range(1,doc['page_count']+1)) or len(pages)>12:raise ValueError('note scope changed')
                with self.personal.connection(write=True) as c:
                    current=c.execute('SELECT * FROM attachment_job WHERE id=?',(row['id'],)).fetchone()
                    resumed=self.jobs._resume_interrupted_local(c,current)
                if resumed:self._schedule_local(row['id'],self._catalog if row['operation']=='school_catalog' else self._index)
            except (PersonalError,ValueError,KeyError,TypeError):
                with self.personal.connection(write=True) as c:
                    c.execute("UPDATE attachment_job SET status='needs_review',phase='原件保留，处理范围需要核对',error_json=?,completed_at=? WHERE id=? AND status='recovery_needed'",
                        (encode([{'code':'source_scope_changed','message':'这项本地处理的来源或章节已有变化，原件与已有结果保留。'}]),now(),row['id']))

    def _schedule_ai(self, job_id, runner):
        if job_id in self.tasks: return
        task = asyncio.create_task(runner(job_id))
        self.tasks[job_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(job_id, None))

    def start_catalog(self, doc_id, key, *, retry=False):
        doc = self.learning.get(doc_id)
        att = self.learning.attachment(doc_id)
        payload = {'document_id': doc_id, 'processing_version': PROCESS_VERSION}
        job, schedule = self.jobs.create('school_catalog', payload, att, digest(encode(payload)), key, allow_local_retry=retry,resume_interrupted_local=True)
        if schedule: self._schedule_local(job['id'], self._catalog)
        return job

    def _catalog(self, job_id):
        payload = self.jobs.get(job_id)['payload']; doc_id = payload['document_id']
        self.jobs.update(job_id, 'running', '读取 PDF 结构与页数')
        try:
            if self.stopping.is_set(): raise PersonalError('本地处理已中断，原件保留。', 'interrupted', 409)
            doc = self.learning.get(doc_id)
            if doc['status'] != 'ready':
                destination = self.learning.cache / (job_id + '-catalog.json')
                self.documents.worker(self.learning.files.path(self.learning.attachment(doc_id)), 'school-catalog', destination, 90)
                result = json.loads(destination.read_text(encoding='utf-8'))
                if result.get('error'): raise PersonalError(result['error']['message'], result['error']['code'], 409)
                self.learning.catalog(doc_id, result)
            with self.personal.connection(write=True) as c:
                c.execute("UPDATE textbook SET status='ready',updated_at=? WHERE document_id=?", (now(), doc_id))
            self.jobs.update(job_id, 'completed', '页数登记完成；正文只解析所选章节', result={'document_id': doc_id, 'page_count': self.learning.get(doc_id)['page_count']})
        except Exception as error: self._local_error(job_id, error)

    def start_index(self, section_id, key, *, retry=False):
        with self.personal.connection() as c:
            row = c.execute('''SELECT s.* FROM document_section s JOIN textbook t ON t.id=s.textbook_id
                WHERE s.id=? AND s.document_id=t.document_id''', (section_id,)).fetchone()
            if not row: raise PersonalError('当前教材没有这个章节。', 'invalid_reference', 400)
            section = dict(row)
        payload = {'document_id': section['document_id'], 'section_id': section_id,
                   'pages': list(range(section['start_page'], section['end_page'] + 1)), 'processing_version': PROCESS_VERSION}
        job, schedule = self.jobs.create('school_index', payload, self.learning.attachment(section['document_id']), digest(encode(payload)), key, allow_local_retry=retry,resume_interrupted_local=True)
        if schedule: self._schedule_local(job['id'], self._index)
        return job

    def start_note_index(self, document_id, key, *, raw_item_id=None, course_id=None):
        """Reuse local OCR for a user-shared classroom note; no model request."""
        doc=self.learning.get(document_id)
        att=self.store.note_attachment(document_id,raw_item_id,course_id) if raw_item_id else self.learning.attachment(document_id)
        with self.personal.connection() as c:
            row=c.execute('SELECT inbox_course_id FROM raw_item WHERE id=?',(att['raw_item_id'],)).fetchone()
            if not row or not row[0]:raise PersonalError('笔记尚未可靠关联课程。','course_link_required',409)
            require_course(row[0],c)
        if not doc['page_count'] or doc['page_count']>12:raise PersonalError('笔记超过自动处理范围，原件已保留。','note_page_scope',409)
        payload={'document_id':document_id,'pages':list(range(1,doc['page_count']+1)),'processing_version':PROCESS_VERSION,'purpose':'lecture_note'}
        job,schedule=self.jobs.create('school_index',payload,att,digest(encode(payload)),key,resume_interrupted_local=True)
        if schedule:self._schedule_local(job['id'],self._index)
        return job

    def _index(self, job_id):
        payload = self.jobs.get(job_id)['payload']; doc_id = payload['document_id']
        completed, failed, cached, ocr = [], [], 0, 0
        for index in payload['pages']:
            if self.stopping.is_set():
                self.jobs.update(job_id, 'recovery_needed', '处理已中断，已成功页面保留', result={'completed': completed, 'failed': failed, 'total': len(payload['pages'])})
                return
            self.jobs.update(job_id, 'running', f'解析 PDF 第 {index} 页', result={'processed': len(completed)+len(failed), 'total': len(payload['pages']), 'cached': cached, 'ocr_pages': ocr})
            try:
                page = self.learning.page(doc_id, index)
                if page['current_extraction_key'] and page['status'] in {'indexed', 'needs_review'}:
                    cached += 1; completed.append(index); continue
                destination = self.learning.cache / f'{job_id}-page-{index}.json'
                self.documents.worker(self.learning.files.path(self.learning.attachment(doc_id)), f'school-page:{index}', destination, 180)
                result = json.loads(destination.read_text(encoding='utf-8'))
                if result.get('error'): raise PersonalError(result['error']['message'], result['error']['code'], 409)
                self.learning.store_page(doc_id, index, result)
                if result['metadata'].get('needs_review'): ocr += 1
                completed.append(index)
            except Exception as error:
                code = getattr(error, 'code', 'parse_failed')
                message = str(error) if isinstance(error, PersonalError) else '本页提取未完成，原页保留，可明确恢复。'
                self.learning.page_failed(doc_id, index, code, message)
                failed.append({'page': index, 'code': code, 'message': message})
        self.jobs.update(job_id, 'partial' if failed and completed else 'failed' if failed else 'completed',
                         '部分页面需要核对' if failed else '当前章节解析完成',
                         result={'completed': completed, 'failed': failed, 'processed': len(completed)+len(failed), 'total': len(payload['pages']), 'cached': cached, 'ocr_pages': ocr, 'local_cost_cny': 0}, errors=failed)

    def _local_error(self, job_id, error):
        code = getattr(error, 'code', 'processing_failed')
        message = str(error) if isinstance(error, PersonalError) else '本地处理未完成；原件保留，请查看后恢复。'
        self.jobs.update(job_id, 'recovery_needed' if code == 'interrupted' else 'failed', '需要核对', errors=[{'code': code, 'message': message}])

    def quote_cost(self, lecture_id):
        context = self.contexts.build(lecture_id, allow_incomplete=True)
        config = self.extraction.settings._read()[1]
        stage_estimates = {stage: estimate('deepseek', config.model, usage, config.base_url) for stage, usage in {
            'evidence': {'prompt_tokens': 18000, 'completion_tokens': 2048},
            'composition': {'prompt_tokens': 24000, 'completion_tokens': 8192},
            'repair': {'prompt_tokens': 16000, 'completion_tokens': 4096}}.items()}
        vision = estimate('deepseek', 'deepseek-flash', {'prompt_tokens': 12000, 'completion_tokens': 4096}, config.base_url)
        images = min(4, len(context['needs_vision']))
        value = None if any(x['estimated_cost'] is None for x in stage_estimates.values()) or vision['estimated_cost'] is None else round(sum(x['estimated_cost'] for x in stage_estimates.values()) + ((images+1)//2)*vision['estimated_cost'], 6)
        return {'estimated_cost_cny': value, 'basis': '按有限上下文和输出预算的保守参考估算；实际以记录用量为准，非账单硬上限。',
                'max_requests': 3 + (images+1)//2, 'max_images': images, 'normal_text_requests': 2, 'max_targeted_repairs': 1,
                'stage_estimates': stage_estimates, 'text_model': config.model, 'vision_model': 'deepseek-flash',
                'local_ocr_cost': 0, 'embedding_calls': 0, 'context_missing': context['missing']}

    async def generate(self, lecture_id, payload, *, automatic_note=False):
        async with self.lock:
            key = request_id(payload.request_id)
            body = payload.model_dump(exclude={'request_id', 'repair_from'})
            if payload.repair_from is not None:body['repair_from'] = payload.repair_from
            signature = digest(encode({'lecture_id':lecture_id,'payload':body,**({'automatic_note':True} if automatic_note else {})}))
            with self.personal.connection() as c:
                previous = self.store._request(c,key,'school_review',signature)
            if previous:
                job = self.jobs.get(previous)
                return {'job':job,'artifact_id':(job.get('result') or {}).get('artifact_id'),'reused':True}
            if payload.repair_from is not None and payload.force:
                raise PersonalError('局部修复不能同时要求重新生成。', 'invalid_request', 400)
            lecture = self.store.lecture(lecture_id)
            profile = profile_for({'lecture': lecture})
            if lecture['version'] != payload.expected_version: raise PersonalError('课次已更新，请重新读取。', 'conflict', 409)
            if profile.requires_textbook and lecture['textbook'] and lecture['textbook']['is_fixture'] and not self.fixture_mode:
                raise PersonalError('测试教材不能充当正式教材；请导入已确认版本。', 'fixture_not_formal', 409)
            context = self.contexts.build(lecture_id, include_recent=payload.include_recent)
            if context['is_fixture'] and not self.fixture_mode:
                raise PersonalError('测试教材不能充当正式教材；请导入已确认版本。', 'fixture_not_formal', 409)
            require_note_text(context, allow_vision=payload.use_vision)
            if automatic_note and payload.use_vision:
                from ..learning.auto_notes import vision_authorization
                if not vision_authorization(self.personal)['enabled']:raise PersonalError('笔记页图像尚未获得发送授权。','note_vision_consent_required',403)
            config = self.extraction.settings._read()[1]
            input_hash = digest(encode({'context': context['context_id'], 'model': config.model, 'prompt': profile.prompt}))
            if not payload.force:
                cached = self.artifacts.cached(lecture_id, input_hash)
                if cached:
                    with self.personal.connection(write=True) as c:
                        self.store._request(c,key,'school_review',signature,cached['job_id'])
                    return {'artifact_id': cached['id'], 'job': self.jobs.get(cached['job_id']), 'reused': True}
            plan = {'lecture_id': lecture_id, 'lecture_version': lecture['version'], 'include_recent': payload.include_recent,
                    'use_vision': payload.use_vision, 'model': config.model, 'force': payload.force,
                    'initial_context_id': context['context_id'], 'prompt_version': profile.prompt}
            if automatic_note:plan['automatic_note']=True
            if not payload.force:
                from .recovery import saved_composition
                recovery = saved_composition(self.personal, context, config.model)
                if recovery:plan['repair_from'] = recovery['artifact_id']
                if payload.repair_from is not None and (not recovery or recovery['artifact_id'] != payload.repair_from):
                    raise PersonalError('这份草稿的来源或修复额度已变化，未发送模型请求。', 'conflict', 409)
            fingerprint = digest(encode({**plan, 'request': key if payload.force else None}))
            first_note=lecture['notes'][0]
            att = self.store.note_attachment(first_note['id'],first_note['raw_item_id'],lecture['course_id'])
            job, schedule = self.jobs.create('school_review', plan, att, fingerprint, key, request_signature=signature)
            if schedule: self._schedule_ai(job['id'], self._review)
            return {'job': job, 'artifact_id': (job.get('result') or {}).get('artifact_id'), 'reused': not schedule}

    async def start_vision(self, lecture_id, payload, *, automatic_note=False):
        async with self.lock:
            lecture = self.store.lecture(lecture_id)
            profile = profile_for({'lecture': lecture})
            allowed = {d['id'] for d in lecture['notes']}
            sections = self.retrieval.sections(lecture_id)
            if payload.document_id not in allowed:
                if not any(s['document_id'] == payload.document_id and all(s['start_page'] <= p <= s['end_page'] for p in payload.pages) for s in sections):
                    raise PersonalError('视觉核对仅限本次笔记或已确认的教材章节页面。', 'source_scope', 400)
            if automatic_note:
                from ..learning.auto_notes import vision_authorization
                if payload.document_id not in allowed or len(set(payload.pages))>2 or not vision_authorization(self.personal)['enabled']:
                    raise PersonalError('自动核验仅限已分享笔记最多2页。','note_vision_scope_exceeded',403)
            for index in payload.pages: self.learning.page(payload.document_id, index)
            key = request_id(payload.request_id)
            plan = {'lecture_id': lecture_id, 'document_id': payload.document_id, 'pages': sorted(set(payload.pages)), 'force': payload.force}
            if automatic_note:plan['automatic_note']=True
            fingerprint = digest(encode({**plan, 'request': key if payload.force else None, 'prompt': profile.vision}))
            job, schedule = self.jobs.create('school_vision', plan, self.learning.attachment(payload.document_id), fingerprint, key)
            if schedule: self._schedule_ai(job['id'], self._vision_job)
            return job

    async def _vision_job(self, job_id):
        async with self.semaphore:
            payload = self.jobs.get(job_id)['payload']
            try:
                self.jobs.update(job_id, 'running', '核对少量选定页面')
                if payload['force']:
                    with self.personal.connection(write=True) as c:
                        for index in payload['pages']:
                            c.execute('UPDATE document_page SET vision_status=NULL WHERE document_id=? AND page_index=?', (payload['document_id'], index))
                await self._vision_batch(job_id, payload['lecture_id'], [{'document_id': payload['document_id'], 'page': index} for index in payload['pages']])
                pages=[self.learning.page(payload['document_id'],index) for index in payload['pages']]
                warning=any(p['vision_status']!='clear' for p in pages)
                self.jobs.update(job_id, 'completed', '页面核对结束；存在不确定或不可辨认内容，请检查原页' if warning else '视觉转写已保存；仍需人工核对',
                    result={'cost':self.cost(job_id),'needs_review':True,'pages':[{'page':p['page_index'],'quality':p['vision_status']} for p in pages]})
            except asyncio.CancelledError:
                self.jobs.update(job_id, 'recovery_needed', '视觉结果未知，不自动重试', errors=[{'code':'interrupted','message':'原页保留，请明确核对或导出。'}])
                raise
            except Exception as error:
                self.jobs.update(job_id, 'failed', '视觉核对未完成，原页保留', result={'cost':self.cost(job_id)}, errors=[{'code':getattr(error,'code','vision_failed'),'message':str(error) if isinstance(error,(PersonalError,ExtractionFailure)) else '模型结果未完成，原页保留。'}])

    def _attempt(self, job_id, lecture_id, kind, config, fingerprint, image_count, stage=None):
        aid = identifier()
        with self.personal.connection(write=True) as c:
            lecture = SchoolStore._lecture(c, lecture_id)
            profile = profile_for({'lecture': lecture})
            prompt = profile.vision if kind == 'vision' else profile.stage_prompts.get(stage, profile.prompt)
            c.execute('''INSERT INTO school_ai_attempt(id,job_id,lecture_id,kind,provider,model,prompt_version,input_hash,status,created_at)
                VALUES (?,?,?,?,'deepseek',?,?,?,'prepared',?)''',
                (aid, job_id, lecture_id, kind, config.model, prompt, fingerprint, now()))
        def before_attempt():
            with self.personal.connection(write=True) as c:
                count = c.execute("SELECT count(*) FROM school_ai_attempt WHERE job_id=? AND status!='prepared'", (job_id,)).fetchone()[0]
                kind_count = c.execute("SELECT count(*) FROM school_ai_attempt WHERE job_id=? AND kind=? AND started_at IS NOT NULL", (job_id, kind)).fetchone()[0]
                stage_count = c.execute("SELECT count(*) FROM school_ai_attempt WHERE job_id=? AND prompt_version=? AND started_at IS NOT NULL", (job_id, prompt)).fetchone()[0]
                if count >= 5 or kind_count >= (2 if kind == 'vision' else 3) or stage and stage_count:
                    raise ExtractionFailure('request_budget', '最多两阶段生成与一次定向修复；不会继续自动调用。', attempts=0)
                changed = c.execute("UPDATE school_ai_attempt SET status='running',image_count=?,started_at=? WHERE id=? AND status='prepared'", (image_count, now(), aid)).rowcount
                if changed != 1: raise ExtractionFailure('attempt_already_started', '同一模型请求不能重复发起。', attempts=0)
        return aid, before_attempt

    def _record_attempt(self, attempt_id, config, response=None, error=None, image_count=0):
        with self.personal.connection() as c:
            started = c.execute('SELECT started_at,image_count,kind,prompt_version FROM school_ai_attempt WHERE id=?',(attempt_id,)).fetchone()
        usage = response.usage if response else getattr(error, 'usage', None)
        model = response.response_model or config.model if response else config.model
        stage = next((name for profile in (MATH_PROFILE, GENERAL) for name, prompt in profile.stage_prompts.items()
            if prompt == started['prompt_version']), 'vision' if started['kind'] == 'vision' else 'legacy_text')
        cost = {**estimate('deepseek', model, usage, config.base_url), 'cloud_images': started['image_count'], 'stage': stage}
        if not started['started_at']:
            cost.update(estimated_cost=0,basis='请求未发出；本地校验未产生模型费用。')
        raw = response.raw_output if response else getattr(error, 'raw_output', None)
        with self.personal.connection(write=True) as c:
            c.execute('''UPDATE school_ai_attempt SET status=?,model=?,usage_json=?,cost_json=?,raw_output=?,error_json=?,completed_at=? WHERE id=?''',
                      ('completed' if response else 'unknown' if getattr(error, 'code', '') in {'timeout', 'connection_error', 'interrupted','unknown_outcome'} else 'failed',
                       model, encode(usage) if usage else None, encode(cost), raw,
                       encode({'code': getattr(error, 'code', 'ai_failed'), 'message': str(error) if isinstance(error, ExtractionFailure) else '模型结果未完成。',
                               'stage': getattr(error, 'stage', stage), 'issues': getattr(error, 'issues', [])}) if error else None, now(), attempt_id))
        return cost

    async def _vision_batch(self, job_id, lecture_id, references):
        plan=self.jobs.get(job_id)['payload']
        profile = profile_for({'lecture': self.store.lecture(lecture_id)})
        if plan.get('automatic_note'):
            from ..learning.auto_notes import vision_authorization
            note_ids={doc['id'] for doc in self.store.lecture(lecture_id)['notes']}
            if not vision_authorization(self.personal)['enabled'] or len(references)>2 or any(ref['document_id'] not in note_ids for ref in references):
                raise PersonalError('自动图像处理仅允许已分享课堂笔记中的最多2页；教材页不在此范围。','note_vision_scope_exceeded',403)
        pages = []
        for ref in references:
            page = self.learning.page(ref['document_id'], ref['page'])
            if page.get('vision_status'): continue
            doc = self.learning.get(ref['document_id'])
            source = self.contexts.image_source(doc, ref['page'])
            pages.append({**source, 'path': str(await asyncio.to_thread(self.learning.image_path, doc['id'], ref['page']))})
            if not profile.requires_textbook:
                pages[-1]['review_profile'] = profile.key
        if not pages: return
        config, key = self.extraction.settings.resolved()
        config = config.model_copy(update={'model': 'deepseek-flash', 'max_output_tokens': 4096, 'thinking': False,'timeout':max(config.timeout,120)})
        fingerprint = digest(encode({'pages': [(p['document_id'], p['page_hash']) for p in pages], 'prompt': profile.vision, 'model': config.model}))
        aid, gate = self._attempt(job_id, lecture_id, 'vision', config, fingerprint, len(pages))
        with self.personal.connection(write=True) as c:
            for p in pages:
                c.execute("UPDATE document_page SET vision_status='prepared',vision_attempt_id=?,vision_input_hash=? WHERE document_id=? AND page_index=?",
                          (aid, fingerprint, p['document_id'], p['page']))
        response = None
        try:
            response = await self.provider.vision(pages, config, key, gate)
            output = VisionOutput.model_validate(strict_json(response.raw_output))
            mapping = {p['source_id']: p for p in pages}
            if {p.source_id for p in output.pages} != set(mapping) or len(output.pages) != len(mapping):
                raise ExtractionFailure('vision_source', '视觉结果的来源标识不匹配，未采用。', raw_output=response.raw_output, usage=response.usage)
            for result in output.pages:
                source = mapping[result.source_id]
                if result.transcript.strip() and result.clarity != 'unreadable':
                    self.learning.store_page(source['document_id'], source['page'], {'extracted_text': result.transcript, 'metadata': {}},
                        method='deepseek_vision', quality='vision_unverified' if result.clarity == 'clear' else 'vision_uncertain',
                        metadata={'attempt_id': aid, 'uncertainties': result.uncertainties, 'clarity': result.clarity, 'needs_review': True})
                with self.personal.connection(write=True) as c:
                    c.execute('UPDATE document_page SET vision_status=? WHERE document_id=? AND page_index=?', (result.clarity, source['document_id'], source['page']))
            self._record_attempt(aid, config, response=response, image_count=len(pages))
        except (ExtractionFailure, ValidationError) as error:
            if isinstance(error, ValidationError): error = ExtractionFailure('vision_schema', '视觉转写未通过结构校验，原页保留。', raw_output=response.raw_output if response else None, usage=response.usage if response else None)
            self._record_attempt(aid, config, error=error, image_count=len(pages))
            with self.personal.connection(write=True) as c:
                c.execute("UPDATE document_page SET vision_status='failed' WHERE vision_attempt_id=?", (aid,))
            raise error
        except asyncio.CancelledError:
            error = ExtractionFailure('interrupted', '视觉请求已中断，结果未知，不自动重复。')
            self._record_attempt(aid, config, error=error, image_count=len(pages))
            with self.personal.connection(write=True) as c:
                c.execute("UPDATE document_page SET vision_status='unknown' WHERE vision_attempt_id=?", (aid,))
            raise
        except Exception:
            error = ExtractionFailure('unknown_outcome', '视觉结果未知；不自动重复调用。')
            self._record_attempt(aid, config, error=error, image_count=len(pages))
            with self.personal.connection(write=True) as c:
                c.execute("UPDATE document_page SET vision_status='unknown' WHERE vision_attempt_id=?", (aid,))
            raise error

    def cost(self, job_id=None, *, lecture_id=None):
        with self.personal.connection() as c:
            column='lecture_id' if lecture_id is not None else 'job_id'
            attempts = [dict(r) for r in c.execute('SELECT kind,image_count,started_at,status,usage_json,cost_json FROM school_ai_attempt WHERE '+column+'=?', (lecture_id if lecture_id is not None else job_id,))]
        total, unknown, stages = 0, False, {}
        for attempt in attempts:
            info = json.loads(attempt['cost_json'] or 'null')
            if not info or info['estimated_cost'] is None: unknown = True
            else: total += info['estimated_cost']
            stage = (info or {}).get('stage', 'vision' if attempt['kind'] == 'vision' else 'legacy_text')
            attempt['stage'] = stage
            entry = stages.setdefault(stage, {'calls': 0, 'estimated_cost_cny': 0})
            entry['calls'] += bool(attempt['started_at'])
            if not info or info['estimated_cost'] is None: entry['estimated_cost_cny'] = None
            elif entry['estimated_cost_cny'] is not None: entry['estimated_cost_cny'] = round(entry['estimated_cost_cny'] + info['estimated_cost'], 8)
        return {'estimated_cost_cny': None if unknown else round(total, 8), 'image_calls': sum(a['kind'] == 'vision' and bool(a['started_at']) for a in attempts),
                'images': sum(a['image_count'] for a in attempts), 'text_calls': sum(a['kind'] == 'text' and bool(a['started_at']) for a in attempts),
                'local_ocr_cost': 0, 'embedding_calls': 0, 'attempts': attempts, 'stages': stages}

    async def _text_stage(self, job_id, lecture_id, stage, config, key, fingerprint, call, validate):
        aid, gate = self._attempt(job_id, lecture_id, 'text', config, fingerprint, 0, stage)
        response = None
        try:
            response = await call(config, key, gate)
            result = validate(response.raw_output)
            self._record_attempt(aid, config, response=response)
            return result, aid
        except (ExtractionFailure, asyncio.CancelledError) as error:
            failure = error if isinstance(error, ExtractionFailure) else ExtractionFailure('interrupted', '生成请求已中断，结果未知，不自动重复。')
            if response is not None:
                failure.usage = response.usage
                # A repair validator sees a merged document, but the audit must
                # retain the actual provider response (the original patch JSON).
                failure.raw_output = response.raw_output
            self._record_attempt(aid, config, error=failure)
            failure.original_raw = response.raw_output if response is not None else None
            failure.attempt_id = aid
            if isinstance(error, asyncio.CancelledError):
                raise error
            raise failure
        except Exception:
            failure = ExtractionFailure('unknown_outcome', '模型结果未知，不自动重复调用。',
                                        raw_output=response.raw_output if response else None, usage=response.usage if response else None)
            self._record_attempt(aid, config, error=failure)
            raise failure

    async def _review(self, job_id):
        plan = self.jobs.get(job_id)['payload']; lecture_id = plan['lecture_id']; artifact_id = None
        async with self.semaphore:
            try:
                self.jobs.update(job_id, 'running', '构建有限来源上下文')
                context = self.contexts.build(lecture_id, include_recent=plan['include_recent'])
                profile = profile_for(context)
                if plan['prompt_version'] != profile.prompt:
                    raise PersonalError('复盘规则已更新，原件与已有版本保留，请重新确认生成。', 'review_profile_changed', 409)
                if context['lecture']['version'] != plan['lecture_version']:
                    raise PersonalError('来源关联已变化，请明确重新生成。', 'conflict', 409)
                from .recovery import saved_composition
                recovery = saved_composition(self.personal, context, plan['model']) if plan.get('repair_from') else None
                if plan.get('repair_from') and (not recovery or recovery['artifact_id'] != plan['repair_from']):
                    raise PersonalError('原修复范围或调用状态已变化，未重新发送；原件与旧复盘保留。', 'conflict', 409)
                if plan['use_vision'] and recovery is None:
                    if plan.get('automatic_note'):
                        from ..learning.auto_notes import vision_authorization
                        if not vision_authorization(self.personal)['enabled']:raise PersonalError('笔记页图像发送已暂停。','note_vision_consent_required',403)
                    note_ids={doc['id'] for doc in self.store.lecture(lecture_id)['notes']}
                    refs = [ref for ref in context['needs_vision'] if not plan.get('automatic_note') or ref['document_id'] in note_ids][:2 if plan.get('automatic_note') else 4]
                    for offset in range(0, len(refs), 2):
                        self.jobs.update(job_id, 'running', f'核对少量页面图像（{offset+1}–{min(offset+2,len(refs))}）')
                        await self._vision_batch(job_id, lecture_id, refs[offset:offset+2])
                    context = self.contexts.build(lecture_id, include_recent=plan['include_recent'])
                require_note_text(context)
                if context['is_fixture'] and not self.fixture_mode:
                    raise PersonalError('测试教材不能充当正式教材。', 'fixture_not_formal', 409)
                config, key = self.extraction.settings.resolved()
                if config.model != plan['model']:
                    raise PersonalError('生成模型配置已变化，请明确重新发起。', 'conflict', 409)
                config = config.model_copy(update={'max_output_tokens': 8192, 'thinking': False,'timeout':max(config.timeout,120)})
                fingerprint = digest(encode({'context': context['context_id'], 'model': config.model, 'prompt': profile.prompt}))
                artifact_id = self.artifacts.reserve(lecture_id, context, config.model, profile.prompt, fingerprint, job_id)
                generation = {'protocol': profile.prompt, 'stage_prompts': profile.stage_prompts, 'context_id': context['context_id'],
                              'model': config.model, 'max_targeted_repairs': 1, 'repair_used': False, 'attempts': []}
                if recovery:
                    generation.update(recovered_from=recovery['artifact_id'], repair_used=True,
                        reused_attempts=[{'stage': 'evidence', 'id': recovery['evidence_attempt_id']},
                                         {'stage': 'composition', 'id': recovery['composition_attempt_id']}],
                        evidence_extraction=recovery['evidence'])
                self.artifacts.record_generation(artifact_id, generation)
                self.jobs.update(job_id, 'running', '提取并核验事实证据', result={'artifact_id': artifact_id})

                async def run_stage(stage, call, validate, evidence=None):
                    limits = {'evidence': 2048, 'composition': 8192, 'repair': 4096}
                    try:
                        value, aid = await self._text_stage(job_id, lecture_id, stage, config.model_copy(update={'max_output_tokens': limits[stage]}),
                            key, fingerprint + ':' + stage, call, validate)
                        generation['attempts'].append({'stage': stage, 'id': aid, 'validation': 'passed'})
                        return value
                    except ProtocolFailure as failure:
                        generation['attempts'].append({'stage': stage, 'id': failure.attempt_id, 'validation': 'failed', 'issues': failure.issues})
                        self.artifacts.record_generation(artifact_id, generation)
                        if generation['repair_used'] or not failure.original_raw or any(i['path'] == '/' for i in failure.issues) or not repair_roots(stage, failure.issues) or len(repair_roots(stage, failure.issues)) > 8:
                            raise
                        generation['repair_used'] = True
                        self.artifacts.record_generation(artifact_id, generation)
                        self.jobs.update(job_id, 'running', '仅修复未通过校验的具体项目（最多一次）')
                        repaired, aid = await self._text_stage(job_id, lecture_id, 'repair', config.model_copy(update={'max_output_tokens': 4096}),
                            key, fingerprint + ':repair',
                            lambda cfg, secret, gate: self.provider.repair(stage, failure.original_raw, failure.issues, evidence, context, cfg, secret, gate),
                            lambda patch: validate(apply_targeted_repair(patch, failure.original_raw, failure)))
                        generation['attempts'].append({'stage': 'repair', 'id': aid, 'validation': 'passed', 'repaired_stage': stage})
                        return repaired

                if recovery:
                    extracted = recovery['evidence']; failure = recovery['failure']
                    self.jobs.update(job_id, 'running', '复用已保存证据，仅修复具体条目（最多一次）')
                    try:
                        (content, evidence, validation), aid = await self._text_stage(job_id, lecture_id, 'repair',
                            config.model_copy(update={'max_output_tokens': 4096}), key, fingerprint + ':repair',
                            lambda cfg, secret, gate: self.provider.repair('composition', recovery['original_raw'],
                                failure.issues, extracted, context, cfg, secret, gate),
                            lambda patch: compose_review(apply_targeted_repair(patch, recovery['original_raw'], failure), extracted, context))
                        generation['attempts'].append({'stage': 'repair', 'id': aid, 'validation': 'passed', 'repaired_stage': 'composition'})
                    except ExtractionFailure as error:
                        generation['attempts'].append({'stage': 'repair', 'id': getattr(error, 'attempt_id', None),
                            'validation': 'failed', 'issues': getattr(error, 'issues', [])})
                        self.artifacts.record_generation(artifact_id, generation)
                        raise
                else:
                    extracted = await run_stage('evidence',
                        lambda cfg, secret, gate: self.provider.extract(context, cfg, secret, gate),
                        lambda raw: extract_evidence(raw, context))
                    generation['evidence_extraction'] = extracted
                    self.artifacts.record_generation(artifact_id, generation)
                    self.jobs.update(job_id, 'running', '根据已验证事实组织十部分复盘')
                    content, evidence, validation = await run_stage('composition',
                        lambda cfg, secret, gate: self.provider.compose(extracted, context, cfg, secret, gate),
                        lambda raw: compose_review(raw, extracted, context), extracted)
                generation['validation'] = validation
                generation['validation_status'] = 'passed'
                current = self.contexts.build(lecture_id, include_recent=plan['include_recent'])
                if current['context_id'] != context['context_id'] or current['lecture']['version'] != plan['lecture_version']:
                    raise PersonalError('生成期间来源或课次已变化，保留草稿并明确重新发起。', 'conflict', 409)
                self.artifacts.record_generation(artifact_id, generation)
                self.artifacts.finish(artifact_id, content, evidence, self.cost(job_id))
                self.jobs.update(job_id, 'completed', '自动复盘已验证保存；请核对不确定内容后采用',
                    result={'artifact_id': artifact_id, 'cost': self.cost(job_id), 'validation': 'passed', 'needs_review': True,
                            'uncertain_fact_ids': extracted['uncertain_fact_ids'], 'targeted_repair_used': generation['repair_used']})
            except asyncio.CancelledError:
                if artifact_id:
                    with self.personal.connection(write=True) as c:
                        c.execute("UPDATE review_artifact SET status='recovery_needed',error_json=? WHERE id=? AND content_json IS NULL",
                                  (encode({'code':'interrupted','message':'模型结果未知，不自动重试；原件与旧版本保留。'}),artifact_id))
                self.jobs.update(job_id, 'recovery_needed', '处理已中断；不会自动重放模型请求', errors=[{'code': 'interrupted', 'message': '原件和历史复盘保留，请明确恢复或导出上下文。'}])
                raise
            except Exception as error:
                code = getattr(error, 'code', 'review_failed')
                message = str(error) if isinstance(error, (PersonalError, ExtractionFailure)) else '复盘未完成，原件与历史版本保留。'
                needs_review = isinstance(error, ProtocolFailure)
                failure = {'code': code, 'message': message, 'stage': getattr(error, 'stage', None), 'issues': getattr(error, 'issues', [])}
                if artifact_id:
                    self.artifacts.finish(artifact_id, None, context['sources'], self.cost(job_id), error=failure, requires_review=needs_review)
                self.jobs.update(job_id, 'needs_review' if needs_review else 'failed', '校验仍未通过；停止自动调用，请核对来源' if needs_review else '需要核对或明确重试',
                    result={'artifact_id': artifact_id, 'cost': self.cost(job_id), 'needs_review': True}, errors=[failure])

    async def close(self):
        self.stopping.set()
        if self.local_recovery_task is not None:
            self.local_recovery_task.cancel()
            await asyncio.gather(self.local_recovery_task,return_exceptions=True)
        for task in list(self.tasks.values()): task.cancel()
        if self.tasks: await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
        if self.futures: await asyncio.gather(*(asyncio.wrap_future(f) for f in list(self.futures.values())), return_exceptions=True)
