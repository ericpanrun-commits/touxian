"""Local evidence projection. Cloud analysis stays disconnected until authorized."""
import asyncio
import json
import logging
import re
from uuid import uuid5,NAMESPACE_URL
from ..personal_store import PersonalError, encode, now
from ..school.documents import digest
from .store import LearningStore, unpack, capture_projection
from .models import CapturedPage, Quiz, MistakeSuggestion
from .parser import is_learning, parse
from .rules import RELIABLE, normalized
from .local_questions import function_limit_diagnostic, retest as local_retest, retest_family
from .activities import activity_context


class LearningService:
    def __init__(self, personal, school, extraction, *, analysis_runner=None):
        self.personal,self.school,self.extraction=personal,school,extraction
        self.store=LearningStore(personal);self.analysis_runner=analysis_runner
        self.tasks={};self.semaphore=asyncio.Semaphore(2)
        with personal.connection(write=True) as c:
            c.execute("UPDATE learning_artifact SET status='interrupted',error_json=? WHERE status='running'",(encode({'message':'上次处理已中断，原件和费用记录保留；不会自动重放付费请求。'}),))
        self.store.sync_topics()

    def schedule(self, key, work):
        if key in self.tasks: return
        async def run():
            async with self.semaphore:
                try: await work()
                except asyncio.CancelledError: raise
                except Exception:
                    logging.getLogger(__name__).warning('Learning background item stopped; saved sources retained.')
        task=asyncio.create_task(run());self.tasks[key]=task
        task.add_done_callback(lambda _:self.tasks.pop(key,None))

    async def close(self):
        tasks=list(self.tasks.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

    def context(self, raw_id, sid, text, title=''):
        with self.personal.connection() as c:
            row=c.execute('SELECT * FROM raw_item WHERE id=?',(raw_id,)).fetchone()
            snap=c.execute('SELECT * FROM raw_snapshot WHERE id=? AND raw_item_id=?',(sid,raw_id)).fetchone()
            if not row or not snap: raise PersonalError('输入来源不存在。','not_found',404)
            course=row['inbox_course_id']
            explicit=bool(re.search(r'高等数学\s*(?:II|Ⅱ|2|二)|高数',text,re.I))
            if not course and explicit: course='course-math-ii'
            quality='text_extracted'
            blocks=c.execute('SELECT source_type,needs_review,raw_ocr_text FROM raw_source_block WHERE raw_snapshot_id=?',(sid,)).fetchall()
            if any(b['needs_review'] or b['raw_ocr_text'] or 'ocr' in b['source_type'] for b in blocks): quality='ocr_unverified'
            has_files=c.execute('SELECT 1 FROM attachment WHERE raw_item_id=?',(raw_id,)).fetchone()
            if has_files and (not blocks or row['inbox_kind']=='image'): quality='ocr_unverified'
            lecture=None
            dates=set(re.findall(r'提交时间[：:]\s*(\d{4}-\d\d-\d\d)',text))
            if course and len(dates)==1:
                matches=c.execute('SELECT id FROM lecture WHERE course_id=? AND lecture_date=?',(course,next(iter(dates)))).fetchall()
                if len(matches)==1: lecture=matches[0][0]
            activity=activity_context(text,title,raw_id,course)
            if activity['activity_type']=='preview':lecture=None
            return {'course_id':course,'lecture_id':lecture,'quality':quality,'course_basis':'existing_link' if row['inbox_course_id'] else 'explicit_course_name' if explicit else 'unresolved',
                'lecture_basis':'matching_submission_date' if lecture else '没有唯一课次，保留材料和作答，稍后自动重试关联','source_time':snap['source_time'],**activity}

    async def ingest(self, raw_id, sid):
        with self.personal.connection() as c:
            row=c.execute('''SELECT s.original_text,r.title_hint,i.kind FROM raw_snapshot s JOIN raw_review r ON r.raw_snapshot_id=s.id
                JOIN raw_item i ON i.id=s.raw_item_id WHERE s.id=? AND s.raw_item_id=?''',(sid,raw_id)).fetchone()
        if not row or not (row['kind']=='inbox_lecture_note' or is_learning(row[0]+'\n'+(row[1] or ''))): return
        text=row[0];title=row[1] or '';context=self.context(raw_id,sid,text+'\n'+title,title)
        if row['kind']=='inbox_lecture_note':context['note_share']=True
        capture=self.store.record_capture(raw_id,sid,text,context)
        if capture['status']!='received': return
        if row['kind']!='inbox_lecture_note' and context['course_id'] and context['course_basis']=='explicit_course_name':
            with self.personal.connection(write=True) as c:
                c.execute('UPDATE raw_item SET inbox_course_id=? WHERE id=? AND inbox_course_id IS NULL',(context['course_id'],raw_id))
        if row['kind']=='inbox_lecture_note' and self.school:
            await self._process_note(capture['id'],initial=True)
            return
        else:await self.auto_note(raw_id,title,text,context)
        capture=self.capture_detail(capture['id'])
        if context.get('note_share') or not re.search(r'我的答案|本人答案|已提交答案|回答正确|回答错误|答题结果|课堂题|提交时间|预习|学习通页面记录|ERIC_LEARNING_CAPTURE',text):
            # Ordinary notes/material do not become a page of unresolved exercise metadata.
            self.store.capture_status(capture['id'],'completed',parsed=CapturedPage().model_dump())
            if context['course_id'] and re.search(r'重要极限|夹逼准则|函数极限|数列极限|等价无穷小|连续性',text+'\n'+title):
                with self.personal.connection(write=True) as c:
                    tid=self.store.topic_for(c,context['course_id'],text+'\n'+title)
                    topic=self.store._topic(c,tid)
                    if not any(e.get('capture_id')==capture['id'] for e in topic['exposure']):
                        evidence={'kind':'material_received','capture_id':capture['id'],'date':now(),'description':'收到相关学习材料；不能证明已读完或独立会做'}
                        c.execute('UPDATE learning_topic SET exposure_json=?,updated_at=? WHERE id=?',(encode([*topic['exposure'],evidence]),now(),tid))
            return
        page=parse(text)
        if page.questions or page.preview_completed_quote or page.preview_materials:
            ids=self.store.save_page(capture,page,trusted=context['quality'] in RELIABLE)
            for aid in ids: self.schedule('mistake:'+aid,lambda aid=aid:self.prepare_mistake(aid))
        else:
            self.schedule('capture:'+capture['id'],lambda:self.parse_model(capture,text))

    async def auto_note(self,raw_id,title,text,context):
        if not self.school or context.get('course_id')!='course-math-ii' or not re.search(r'笔记|Apple\s*Notes',title,re.I):return
        match=re.search(r'20\d\d-\d\d-\d\d',title)
        if not match:return  # Receipt time is not the classroom date.
        from datetime import date
        try:day=date.fromisoformat(match[0]).isoformat()
        except ValueError:return
        with self.personal.connection() as c:
            files=c.execute("SELECT id FROM attachment WHERE raw_item_id=? AND extension='pdf' AND local_path IS NOT NULL",(raw_id,)).fetchall()
            lectures=c.execute('SELECT id,title FROM lecture WHERE course_id=? AND lecture_date=?',(context['course_id'],day)).fetchall()
        if len(files)!=1 or lectures:return  # Ambiguous same-day merges retain the current Lecture.
        try:
            doc=self.school.learning.register(files[0][0])
            if doc['status']!='ready':return
            from ..school.models import LectureInput
            lecture=self.school.store.create_lecture(LectureInput(lecture_date=day,title=title.removesuffix('.pdf'),notes_raw_item_id=raw_id),
                str(uuid5(NAMESPACE_URL,'learning-note:'+raw_id)))
            with self.personal.connection(write=True) as c:
                row=c.execute('SELECT id FROM learning_capture WHERE raw_item_id=? ORDER BY created_at DESC LIMIT 1',(raw_id,)).fetchone()
                if row:
                    context={**context,'lecture_id':lecture['id'],'lecture_basis':'explicit_course_and_note_date'}
                    c.execute('UPDATE learning_capture SET context_json=? WHERE id=?',(encode(context),row[0]))
        except PersonalError:
            return  # Original and the established manual School path remain available.

    async def call(self, artifact, schema, payload):
        if self.analysis_runner is None:
            self.store.finish_artifact(artifact['id'],status='needs_setup',error={'message':'本机保存和规则整理已完成；后台模型处理尚未启用。'})
            return None
        return await self.analysis_runner(artifact,schema,payload)

    def analysis_status(self):
        from .analysis import authorization
        with self.personal.connection() as c:result=authorization(c)
        return {**result,'available':self.analysis_runner is not None}

    def reserve_work(self,kind,fingerprint,tid=None,sources=()):
        artifact=self.store.reserve_artifact(kind,fingerprint,tid,sources)
        if artifact['status']=='needs_setup' and artifact.get('model') is None and self.analysis_status()['enabled']:
            with self.personal.connection(write=True) as c:
                attempted=c.execute("SELECT 1 FROM learning_artifact WHERE kind='learning_ai_call' AND json_extract(payload_json,'$.parent_id')=?",(artifact['id'],)).fetchone()
                if not attempted:
                    c.execute("UPDATE learning_artifact SET status='running',error_json=NULL,completed_at=NULL WHERE id=? AND status='needs_setup' AND model IS NULL",(artifact['id'],))
                    artifact['status']='running'
        return artifact

    def resume_background(self):
        # Local association and known recoverable note failures must also resume
        # when optional model analysis is paused or not configured.
        self.resume_shared_notes()
        if not self.analysis_runner or not self.analysis_status()['enabled']:return
        with self.personal.connection() as c:
            captures=[dict(r) for r in c.execute('''SELECT cap.id,s.original_text FROM learning_capture cap
                JOIN raw_snapshot s ON s.id=cap.snapshot_id JOIN learning_artifact a ON a.input_hash=cap.input_hash AND a.kind='capture_analysis'
                WHERE a.status='needs_setup' AND a.model IS NULL AND NOT EXISTS(SELECT 1 FROM learning_artifact call
                    WHERE call.kind='learning_ai_call' AND json_extract(call.payload_json,'$.parent_id')=a.id)
                ORDER BY cap.created_at LIMIT 8''')]
            topics=[r[0] for r in c.execute("SELECT id FROM learning_topic WHERE history_status='confirmed' LIMIT 20")]
            mistakes=[r[0] for r in c.execute("SELECT attempt_id FROM learning_followup WHERE status='pending' AND exercise_id IS NULL ORDER BY due_at LIMIT 8")]
        for cap in captures:
            self.schedule('capture:'+cap['id'],lambda cap=cap:self.parse_model(self.capture_detail(cap['id']),cap['original_text']))
        for tid in topics:self.schedule('history:'+tid,lambda tid=tid:self.prepare_history(tid))
        for aid in mistakes:self.schedule('mistake:'+aid,lambda aid=aid:self.prepare_mistake(aid))

    async def parse_model(self, capture, text):
        if len(text)>22000:
            self.store.capture_status(capture['id'],'needs_review',error={'message':'这份页面太长；原文已保存，请分享本次结果所在的一页。'});return
        artifact=self.reserve_work('capture_analysis',capture['input_hash'])
        if artifact['status']!='running': return
        page=await self.call(artifact,CapturedPage,{'operation':'extract_submitted_records','text':text})
        if page is None:
            self.store.capture_status(capture['id'],'needs_review',error={'message':'原件已保存，题目或结果尚未可靠辨认。'});return
        valid=all(q.question in text and (not q.my_answer or q.my_answer in text) and (not q.correct_answer or q.correct_answer in text)
            and (not q.result_quote or q.result_quote in text) and all(v in text for v in q.options.values())
            and (not q.explanation or q.explanation in text) and (not q.submitted_at or q.submitted_at in text) for q in page.questions)
        valid=valid and all(m.title in text and m.evidence_quote in text and (not m.available_at or m.available_at in text)
            and (not m.completed_at or m.completed_at in text) and (not m.source_reference or m.source_reference in text)
            and (not m.completed or any(v.completed and v.evidence_quote==m.evidence_quote for v in parse(m.evidence_quote).preview_materials)) for m in page.preview_materials)
        if not valid or page.preview_completed_quote and (page.preview_completed_quote not in text or not parse(page.preview_completed_quote).preview_completed_quote):
            self.store.capture_status(capture['id'],'needs_review',error={'message':'识别结果无法逐字对应原文，未记作可靠答题结果。'});return
        if not page.questions and not page.preview_completed_quote and not page.preview_materials:
            self.store.capture_status(capture['id'],'completed',parsed=page.model_dump());return
        # Unknown layouts are suggestions even when the input is copied text:
        # whole-page substring matches cannot prove an answer belongs to this question.
        ids=self.store.save_page(capture,page,trusted=False)
        for aid in ids: self.schedule('mistake:'+aid,lambda aid=aid:self.prepare_mistake(aid))

    def sources_for(self, topic):
        with self.personal.connection() as c:
            section=c.execute('SELECT * FROM document_section WHERE id=?',(topic.get('section_id'),)).fetchone()
            candidates=[dict(r) for r in c.execute('''SELECT b.*,p.printed_page_label,d.title FROM document_block b
                JOIN document_page p ON p.id=b.page_id AND p.current_extraction_key=b.extraction_key
                JOIN document d ON d.id=b.document_id JOIN textbook t ON t.document_id=b.document_id
                WHERE t.course_id=? AND t.is_fixture=0 AND b.extraction_quality IN ('text_extracted','user_verified','visually_checked')
                ORDER BY b.page_index,b.block_order''',(topic['course_id'],))]
        if section:
            candidates=[b for b in candidates if b['document_id']==section['document_id'] and section['start_page']<=b['page_index']<=section['end_page']]
        else:
            terms=[w for w in ['极限','夹逼','无穷小','连续'] if w in topic['title']]
            candidates=[b for b in candidates if any(w in (b.get('text') or '') for w in terms)]
            if '重要极限' in topic['title']:
                candidates.sort(key=lambda b: not any(w in (b.get('text') or '') for w in ['sin','重要极限','(1+']))
            elif '夹逼' in topic['title']:
                candidates.sort(key=lambda b: '夹逼' not in (b.get('text') or ''))
            elif '等价' in topic['title'] or '无穷小' in topic['title']:
                candidates.sort(key=lambda b: not all(term in (b.get('text') or '') for term in ['等价','tan','ln']))
        return [{'source_id':'learning-source-'+b['id'],'document_id':b['document_id'],'page':b['page_index'],
            'printed_page_label':b['printed_page_label'],'title':b['title'],'quote':b['text'],'quality':b['extraction_quality'],'kind':'textbook'}
            for b in candidates if b.get('text') and 40<=len(b['text'])<=3000][:8]

    async def prepare_history(self, tid):
        topic=self.store.topic(tid)
        if topic['history_status']!='confirmed': return
        sources=self.sources_for(topic);fingerprint=digest(encode([tid,sources,'retro-v1']))
        artifact=self.store.reserve_artifact('retro_summary',fingerprint,tid,sources)
        if artifact['status']=='running':
            self.store.finish_artifact(artifact['id'],{'title':'事后补档 · '+topic['title'],
                'notice':'这是根据真实教材整理的事后补档，没有当时课堂笔记，不能推断老师讲过或强调过什么。',
                'core':[{'text':s['quote'],'source_id':s['source_id']} for s in sources],
                'method':'先核对定义、变量趋向和适用条件，再做下方简短诊断。',
                'scope':'只使用已经可靠提取或核验的教材段落；没有可靠文字的部分保留原页，不补猜。'},
                status='completed' if sources else 'needs_source',error=None if sources else {'message':'该节目前只有未核对的 OCR。先保留以前学过；核对教材原页后再生成诊断。'})
        if not sources: return
        quiz=self.reserve_work('diagnostic',digest(fingerprint+':diagnostic-v1'),tid,sources)
        if quiz['status']!='running': return
        local=function_limit_diagnostic(sources)
        if local:
            self.store.save_quiz(quiz,local)
            self.store.finish_artifact(quiz['id'],{'generation':'local_source_gated','questions':[q.model_dump() for q in local]},model='local-math-templates-v1')
            return
        value=await self.call(quiz,Quiz,{'operation':'historical_diagnostic','topic':topic['title'],'sources':sources})
        if value:
            if self.valid_questions(value.questions,sources): self.store.save_quiz(quiz,value.questions)
            else: self.invalidate(quiz['id'],'诊断来源或选项不完整，题目没有发布。')

    @staticmethod
    def valid_questions(questions,sources):
        allowed={s['source_id'] for s in sources}
        return len({normalized(q.question) for q in questions})==len(questions) and all(set(q.source_ids)<=allowed and len(set(q.options.values()))==4 for q in questions)

    def invalidate(self, aid, message):
        with self.personal.connection(write=True) as c:
            c.execute("UPDATE learning_artifact SET status='needs_review',error_json=? WHERE id=?",(encode({'message':message}),aid))

    async def prepare_mistake(self, aid):
        with self.personal.connection() as c:
            row=c.execute('''SELECT a.*,e.topic_id,e.question,e.options_json,e.answer_json,e.explanation FROM learning_attempt a
                JOIN learning_exercise e ON e.id=a.exercise_id WHERE a.id=? AND a.result='incorrect' ''',(aid,)).fetchone()
            if not row: return
            a=dict(row)
            cap=c.execute('SELECT parsed_json,context_json FROM learning_capture WHERE id=?',(a['capture_id'],)).fetchone() if a['capture_id'] else None
            unverified_options=bool(cap and json.loads(cap[1] or '{}').get('options_quality')=='ocr_unverified')
            record=next((q for q in json.loads(cap[0] or '{}').get('questions',[]) if normalized(q['question'])==normalized(a['question']) and q.get('my_answer')==a['answer']),None) if cap else None
            if record:
                a['answer_json']=encode({'text':record['correct_answer']}) if record.get('correct_answer') else 'null'
                a['explanation']=record.get('explanation','')
            used=[r[0] for r in c.execute('''SELECT e.question FROM learning_attempt a JOIN learning_exercise e ON e.id=a.exercise_id WHERE a.retest_of=?''',(aid,))]
            plan=c.execute('SELECT status FROM learning_followup WHERE attempt_id=?',(aid,)).fetchone()
            if not plan or plan[0]=='resolved':return
        topic=self.store.topic(a['topic_id']);sources=self.sources_for(topic)
        if not sources:
            with self.personal.connection(write=True) as c:c.execute("UPDATE learning_followup SET status='needs_source' WHERE attempt_id=? AND status='pending'",(aid,))
            return
        artifact=self.reserve_work('mistake_analysis',digest(encode([aid,sources,len(used),'mistake-v1'])),a['topic_id'],sources)
        if artifact['status']!='running': return
        local=None
        for trial in range(8):
            candidate=local_retest(sources,a['question'],digest(aid+':'+str(len(used))+':'+str(trial)))
            if candidate and normalized(candidate.question) not in {normalized(q) for q in [a['question'],*used]}:
                local=candidate;break
        cloud_enabled=self.analysis_runner is not None and self.analysis_status()['enabled']
        if local and not cloud_enabled:
            self.store.save_quiz(artifact,[local],prior=aid)
            self.store.finish_artifact(artifact['id'],{'attempt_id':aid,'category':'暂时无法判断',
                'reason':'目前只有最终答案，没有推导；先用一道同类题区分是否仍不熟悉。','uncertain':True,
                'origin':'local_evidence_rule','retest':local.model_dump()},model='local-math-templates-v1')
            return
        value=await self.call(artifact,MistakeSuggestion,{'operation':'mistake_and_different_retest','sources':sources,
            'question':a['question'],'options':json.loads(a['options_json']),'my_answer':a['answer'],'platform_correct_answer':json.loads(a['answer_json'] or 'null'),
            'platform_explanation':a['explanation'],'student_work_available':False,'used_questions':used})
        if value:
            original_text='\n'.join([a['question'],*json.loads(a['options_json']).values(),a['answer'],a['explanation'] or ''])
            anchored=bool(value.evidence_quotes) and all(quote and quote in original_text for quote in value.evidence_quotes)
            missing_choices=bool(re.fullmatch(r'[A-D]+',a['answer'].strip(),re.I) and not json.loads(a['options_json']))
            if not anchored or value.category in {'粗心','计算错误'} or missing_choices or unverified_options:
                value.category='暂时无法判断';value.reason='只有最终答案，缺少足够推导，暂时不能可靠区分具体错因。'
                if missing_choices:value.reason='尚缺完整选项，且没有解题过程；不能仅凭答案字母推断具体错因。'
                if unverified_options:value.reason='图片选项尚未核验，且没有解题过程；暂时无法可靠判断具体错因。'
            else:value.reason='可能原因（AI 建议）：'+value.reason
            value.uncertain=True
            payload={**value.model_dump(),'attempt_id':aid,'origin':'ai_suggestion'}
            with self.personal.connection(write=True) as c:c.execute('UPDATE learning_artifact SET payload_json=? WHERE id=?',(encode(payload),artifact['id']))
            candidate=local or value.retest
            if candidate:
                family=retest_family(a['question'])
                supported=local_retest(sources,a['question'],digest(aid)) is not None
                if not supported or not family or family!=retest_family(candidate.question) or not self.valid_questions([candidate],sources) or normalized(candidate.question) in {normalized(q) for q in [a['question'],*used]}:
                    self.invalidate(artifact['id'],'复测需要同一具体要点的可靠依据，并与原题及已用题目不同。');return
                self.store.save_quiz(artifact,[candidate],prior=aid)
        elif local:
            # Keep a source-verified local question when a single cloud suggestion fails.
            self.store.save_quiz(artifact,[local],prior=aid)

    def capture_detail(self,cid):
        with self.personal.connection() as c:
            row=c.execute('SELECT * FROM learning_capture WHERE id=?',(cid,)).fetchone()
            if not row: raise PersonalError('学习输入不存在。','not_found',404)
            return capture_projection(c,row)

    async def confirm_capture(self,cid,course_id=None,*,lecture_id=None,lecture_date=None):
        capture=self.capture_detail(cid)
        if capture['context'].get('note_share'):
            from datetime import date,datetime
            if lecture_date:
                try:
                    from .rules import SHANGHAI
                    if date.fromisoformat(lecture_date)>datetime.now(SHANGHAI).date():raise ValueError()
                except ValueError:raise PersonalError('请选择真实的课堂日期。','invalid_lecture_date',400)
            if lecture_id:
                with self.personal.connection() as c:lesson=c.execute('SELECT * FROM lecture WHERE id=?',(lecture_id,)).fetchone()
                if not lesson or (course_id and lesson['course_id']!=course_id):raise PersonalError('选择的课程与课次不一致。','lecture_context_mismatch',409)
                course_id=lesson['course_id'];lecture_date=lesson['lecture_date']
            if lecture_date and not course_id:course_id=capture['context'].get('course_id')
            if course_id:
                with self.personal.connection(write=True) as c:
                    if not c.execute('SELECT 1 FROM course WHERE id=?',(course_id,)).fetchone():raise PersonalError('课程不存在。')
                    from .note_links import choose_course
                    choose_course(capture['context'],course_id)
                    if lecture_date and not lecture_id and capture['context'].get('confirmed_note_lecture_id'):
                        prior=c.execute('SELECT lecture_date FROM lecture WHERE id=?',(capture['context']['confirmed_note_lecture_id'],)).fetchone()
                        if not prior or prior[0]!=lecture_date:capture['context'].pop('confirmed_note_lecture_id',None)
                    capture['context'].update(course_id=course_id,course_basis='user_confirmation')
                    if lecture_id:capture['context']['confirmed_note_lecture_id']=lecture_id
                    if lecture_date:
                        capture['context']['confirmed_note_date']=lecture_date
                        capture['context'].pop('note_date_requires_confirmation',None)
                    c.execute('UPDATE raw_item SET inbox_course_id=? WHERE id=?',(course_id,capture['raw_item_id']))
                    c.execute('UPDATE learning_capture SET context_json=? WHERE id=?',(encode(capture['context']),cid))
            await self.resume_note(cid)
            return self.capture_detail(cid)
        if not capture['parsed']: raise PersonalError('这份输入还没有可核对的题目，请先查看原件。','not_parsed',409)
        if course_id:
            with self.personal.connection(write=True) as c:
                if not c.execute('SELECT 1 FROM course WHERE id=?',(course_id,)).fetchone():raise PersonalError('课程不存在。')
                context={**capture['context'],'course_id':course_id,'course_basis':'user_confirmation'}
                source=c.execute('SELECT s.original_text,r.title_hint FROM raw_snapshot s LEFT JOIN raw_review r ON r.raw_snapshot_id=s.id WHERE s.id=?',(capture['snapshot_id'],)).fetchone()
                if source:context.update(activity_context(source[0],source[1] or '',capture['raw_item_id'],course_id))
                c.execute('UPDATE learning_capture SET context_json=? WHERE id=?',(encode(context),cid))
            capture['context']=context
        if not capture['context'].get('course_id'):raise PersonalError('这份材料的课程还不确定。','course_required',409)
        ids=self.store.save_page(capture,CapturedPage.model_validate(capture['parsed']),trusted=True)
        for aid in ids:self.schedule('mistake:'+aid,lambda aid=aid:self.prepare_mistake(aid))
        return self.capture_detail(cid)

    def resume_shared_notes(self):
        with self.personal.connection() as c:
            ids=[r[0] for r in c.execute("""SELECT id FROM learning_capture WHERE json_extract(context_json,'$.note_share')=1 AND (
                json_extract(context_json,'$.note_automation.status') IN ('needs_vision_permission','needs_setup','local_ready','interrupted')
                OR (status='received' AND json_extract(context_json,'$.note_automation.status') IS NULL)
                OR json_extract(context_json,'$.note_link_update') IS NOT NULL
                OR (json_extract(context_json,'$.note_automation.status')='needs_review'
                    AND json_extract(context_json,'$.note_automation.message')='课次不存在。'
                    AND json_extract(context_json,'$.lecture_id') IS NULL)
                ) ORDER BY created_at LIMIT 8""")]
        for cid in ids:self.schedule('note:'+cid,lambda cid=cid:self.resume_note(cid))

    def resume_pending_note_links(self):
        with self.personal.connection() as c:
            ids=[row[0] for row in c.execute("""SELECT id FROM learning_capture
                WHERE json_extract(context_json,'$.note_share')=1
                AND json_extract(context_json,'$.note_link_update') IS NOT NULL
                ORDER BY created_at,id LIMIT 8""")]
        for cid in ids:self.schedule('note:'+cid,lambda cid=cid:self.resume_note(cid))

    async def resume_note(self,cid):
        await self._process_note(cid)

    async def _process_note(self,cid,*,initial=False):
        from .auto_notes import AutoNotes
        from .note_work import NoteWork
        work=NoteWork(self,cid);capture=work.capture;source=work.source
        if not source or not capture['context'].get('note_share'):return
        context=capture['context'];notes=AutoNotes(self)
        try:
            await notes.run(capture['raw_item_id'],source[1] or '',source[0],context,source_guard=work.current)
            work.current()
            try:await notes.review(context,source_guard=work.current)
            except PersonalError as error:
                if error.code=='note_context_changed':return
                notes.pause(context,'needs_review',str(error))
        except PersonalError as error:
            if error.code=='note_context_changed':return
            # A known local failure must not leave a received capture looking
            # as though it is still processing indefinitely. Saved originals
            # and existing paid-operation ledgers remain untouched.
            notes.pause(context,'needs_source' if error.code in {'file_unavailable','original_required','not_downloaded'} else 'needs_review',str(error))
        work.save(context,initial=initial)

    def quiz(self,followup_id):
        with self.personal.connection() as c:
            row=c.execute('''SELECT e.* FROM learning_followup f JOIN learning_exercise e ON e.id=f.exercise_id
                WHERE f.id=? AND f.status='ready' AND NOT EXISTS(SELECT 1 FROM learning_attempt a WHERE a.exercise_id=e.id) ''',(followup_id,)).fetchone()
            if not row:raise PersonalError('复测还在准备，或需要先补充可靠来源。','not_ready',409)
            return self.store.public_exercise(row)
