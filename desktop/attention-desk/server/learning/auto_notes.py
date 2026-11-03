"""Local note routing authorized by the user's Phase 4.1 automatic-association request.

Uses existing Course/Lecture/section/source tables. No cloud request is made here.
"""
import asyncio,re,json
from datetime import date,datetime
from uuid import NAMESPACE_URL,uuid5
from ..personal_store import PersonalError,encode,now
from ..collector.safe_logs import record
from ..school.documents import digest
from ..school.models import LectureInput,GenerateInput,VisionInput
from ..school.profiles import profile_for
from ..courses import CourseStore
from ..documents.policy import IMAGE_EXTENSIONS
from .rules import SHANGHAI,normalized
from .store import NOTE_REVIEW_REQUIRED

def plain(text):return normalized(text).replace('的','')
def key(text):return str(uuid5(NAMESPACE_URL,'eric-auto-note-v1:'+text))
def vision_authorization(personal):
    with personal.connection() as c:
        row=c.execute("SELECT payload_json FROM learning_artifact WHERE kind='note_vision_authorization' ORDER BY rowid DESC LIMIT 1").fetchone()
    value=json.loads(row[0] or '{}') if row else {}
    return {'enabled':value.get('enabled') is True and value.get('scope_version')=='notes-rendered-pages-v1','scope_version':'notes-rendered-pages-v1','maximum_pages':2}
def section_title(text):
    return re.sub(r'^第[一二三四五六七八九十百\d]+章\s*第[一二三四五六七八九十百\d]+节\s*|^第[一二三四五六七八九十百\d]+节\s*','',text).replace('（前置参考）','').strip()

def resolve_note_day(title,body,source_time,received_at,*,allow_share_default=True):
    explicit=set(re.findall(r'20\d\d-\d\d-\d\d',title+'\n'+body[:160]))
    if len(explicit)>1:return None,'conflicting_note_dates'
    raw=next(iter(explicit),None)
    if raw:
        try:
            day=date.fromisoformat(raw)
            if day>datetime.now(SHANGHAI).date():return None,'future_note_date'
            return day.isoformat(),'note_header'
        except ValueError:return None,'invalid_note_date'
    if not allow_share_default:return None,'note_date_missing'
    # Current-day classroom sharing is an explicit Phase 4.1 default. It does
    # not create an exercise submission time or claim when teaching happened.
    value=source_time or received_at
    try:
        if len(value)==10:return date.fromisoformat(value).isoformat(),'share_day_default'
        stamp=datetime.fromisoformat(value)
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=SHANGHAI)
        return stamp.astimezone(SHANGHAI).date().isoformat(),'share_day_default'
    except (ValueError,TypeError):return None,'note_date_missing'

class AutoNotes:
    def __init__(self,service):self.service,self.school,self.personal=service,service.school,service.personal

    async def wait(self,job,*,allow_empty_text=False,allow_review=False):
        for _ in range(900):
            current=await asyncio.to_thread(self.school.jobs.get,job['id'])
            if current['status'] not in {'waiting','running'}:
                if allow_review and current['status']=='needs_review':return current
                if current['status']!='completed':
                    errors=(current.get('result') or {}).get('failed') or []
                    if not (allow_empty_text and errors and all(e.get('code')=='empty_text' for e in errors)):
                        raise PersonalError('笔记处理没有完成，原件和已有结果都已保留。','note_processing_incomplete',409)
                return current
            await asyncio.sleep(.4)
        raise PersonalError('笔记仍在处理，已有原件和进度保留。','note_processing_pending',409)

    @staticmethod
    def pause(context,status,message):context['note_automation']={'status':status,'message':message}

    def recover_review_outcome(self,work,value,context):
        """Inspect the existing result of an interrupted parent; never call AI."""
        with self.personal.connection() as c:
            row=c.execute('''SELECT j.* FROM school_request r JOIN attachment_job j ON j.id=r.result_id
                WHERE r.id=? AND r.operation='school_review' ''',(key('review:'+work['id']),)).fetchone()
            unknown=bool(row and c.execute("SELECT 1 FROM school_ai_attempt WHERE job_id=? AND status NOT IN ('completed','failed') LIMIT 1",(row['id'],)).fetchone())
        job=self.school.jobs.public(row) if row else None
        aid=(job.get('result') or {}).get('artifact_id') if job else None
        saved=self.school.artifacts.get(aid) if aid else None
        valid=bool(job and job['status']=='completed' and saved and saved['content'] and not saved['error'] and not unknown
            and not saved['lecture_version_changed'] and not saved['preview_evidence_changed']
            and (saved.get('generation') or {}).get('context_id')==context['context_id']
            and (saved.get('generation') or {}).get('validation_status')=='passed')
        value.update(status='completed' if valid else 'needs_review',
            message='笔记已整理，已恢复原来保存的复盘；没有重新调用模型。' if valid else
                NOTE_REVIEW_REQUIRED if job and job['status']=='needs_review' else '上次复盘中断，原件与处理记录已保留。请核对后继续，系统没有自动重发。')
        if job:value['review_job_id']=job['id']
        if aid:value['review_id']=aid
        error=None if valid else {'code':'note_review_required' if job and job['status']=='needs_review' else 'review_outcome_unknown','message':value['message']}
        with self.personal.connection(write=True) as c:
            c.execute("UPDATE learning_artifact SET status=?,payload_json=?,error_json=?,completed_at=? WHERE id=? AND kind='auto_note_review' AND status='interrupted'",
                ('completed' if valid else 'needs_review',encode(value),encode(error) if error else None,now(),work['id']))

    async def review(self,context,*,source_guard=None):
        if source_guard:source_guard()
        value=context.get('note_automation',{})
        if value.get('status') not in {'local_ready','needs_vision_permission','needs_source'}:return
        if not context.get('lecture_id'):return
        if not self.service.analysis_status()['enabled']:
            value.update(status='needs_setup',message='笔记已整理；后台文字处理处于暂停状态。');return
        lecture=self.school.store.lecture(context['lecture_id'])
        profile=profile_for({'lecture':lecture})
        if not profile.requires_textbook and value.get('status')=='needs_source':value['status']='local_ready'
        source_context=self.school.contexts.build(lecture['id'],allow_incomplete=True)
        note_ids={doc['id'] for doc in lecture['notes']}
        wants_images=any(ref['document_id'] in note_ids for ref in source_context['needs_vision'])
        if wants_images and not vision_authorization(self.personal)['enabled']:
            value.update(status='needs_vision_permission',message='原件、本地识别和课次已保存；手写内容仍需核验。允许发送少量笔记页图像后可继续自动复盘。');return
        if value.get('status')=='needs_source' and not wants_images:return
        from .analysis import LearningAnalysis
        config=self.school.extraction.settings._read()[1];LearningAnalysis.official(config)
        quote=self.school.quote_cost(lecture['id'])
        if quote['estimated_cost_cny'] is None or quote['estimated_cost_cny']>.6:
            value.update(status='needs_review',message='本次复盘超出小额自动范围，请核对后生成。');return
        work=self.service.store.reserve_artifact('auto_note_review',digest(encode([source_context['context_id'],'note-review-v1'])))
        if work['status']=='interrupted':
            self.recover_review_outcome(work,value,source_context);return
        if work['status']!='running':value.update(work.get('payload') or {});return
        with self.personal.connection() as c:
            count=c.execute("SELECT count(*) FROM learning_artifact WHERE kind='auto_note_review' AND date(created_at,'+8 hours')=date(?,'+8 hours')",(now(),)).fetchone()[0]
        if count>3:
            value.update(status='needs_review',message='今天的自动课堂复盘已达到小额范围，已有原件和关联保留。')
            self.service.store.finish_artifact(work['id'],dict(value),status='needs_review');return
        # Reuse both existing model ledgers. Reserve the conservative quote;
        # running requests retain that reserve even if their outcome is unknown.
        with self.personal.connection(write=True) as c:
            month=datetime.now(SHANGHAI).strftime('%Y-%m')
            school=[json.loads(r[0] or '{}') for r in c.execute("SELECT cost_json FROM school_ai_attempt WHERE started_at IS NOT NULL AND substr(date(started_at,'+8 hours'),1,7)=?",(month,))]
            learning=[json.loads(r[0] or '{}') for r in c.execute("SELECT cost_json FROM learning_artifact WHERE kind='learning_ai_call' AND model IS NOT NULL AND substr(date(created_at,'+8 hours'),1,7)=?",(month,))]
            reserved=[json.loads(r[0] or '{}') for r in c.execute("SELECT cost_json FROM learning_artifact WHERE kind='auto_note_review' AND status='running' AND substr(date(created_at,'+8 hours'),1,7)=?",(month,))]
            spent=sum(info.get('estimated_cost') if info.get('estimated_cost') is not None else info.get('reserved_cost_cny',.6) for info in [*school,*learning])
            held=sum(info.get('reserved_cost_cny',0) for info in reserved)
            budget_ok=spent+held+quote['estimated_cost_cny']<=20
            if budget_ok:c.execute('UPDATE learning_artifact SET cost_json=? WHERE id=?',(encode({'reserved_cost_cny':quote['estimated_cost_cny'],'accounting':'school_ai_attempts'}),work['id']))
        if not budget_ok:
            value.update(status='needs_review',message='本月后台学习处理已接近估算额度，原件与已有整理保留。')
            self.service.store.finish_artifact(work['id'],dict(value),status='needs_review');return
        try:
            if value.get('status')=='needs_source':
                refs=[ref for ref in source_context['needs_vision'] if ref['document_id'] in note_ids][:2]
                document_id=refs[0]['document_id'];pages=[ref['page'] for ref in refs if ref['document_id']==document_id]
                vision=await self.school.start_vision(lecture['id'],VisionInput(document_id=document_id,pages=pages,request_id=key('identify:'+work['id']),confirm_cost=True),automatic_note=True)
                await self.wait(vision)
                if source_guard:source_guard()
                clear='\n'.join(block['resolved_text'] for block in self.school.learning.blocks(list(note_ids))
                    if block['extraction_method']=='deepseek_vision' and block.get('vision_status')=='clear')
                with self.personal.connection() as c:
                    sections=[dict(row) for row in c.execute("SELECT s.* FROM document_section s JOIN textbook t ON t.id=s.textbook_id WHERE t.course_id=? AND t.document_id=s.document_id AND t.is_fixture=0",(lecture['course_id'],))]
                exact=[section for section in sections if len(section_title(section['title']))>=4 and plain(section_title(section['title'])) in plain(clear)]
                from .activities import related_topic
                matches=exact or [section for section in sections if clear and related_topic(section['title'],{'notes_evidence_text':clear})]
                if len(matches)!=1:
                    value.update(status='needs_source',message='笔记页已核验，教材小节仍不够明确；原页与转写保留，请在课堂中核对一次小节。')
                    self.service.store.finish_artifact(work['id'],dict(value),status='needs_source');return
                section=matches[0];lecture=self.school.store.lecture(lecture['id'])
                lecture=self.school.store.link_sections(lecture['id'],lecture['version'],[section['id']],source_guard=source_guard)
                with self.personal.connection(write=True) as c:
                    c.execute("UPDATE lecture_section SET origin='automatic_note_vision_match' WHERE lecture_id=? AND section_id=?",(lecture['id'],section['id']))
                    if context.get('automatic_lecture_title')==lecture['title'] and not lecture['artifacts']:
                        c.execute('UPDATE lecture SET title=?,version=version+1,updated_at=? WHERE id=?',
                            (lecture['lecture_date']+' '+lecture['course']['name']+' · '+section_title(section['title']),now(),lecture['id']))
                await self.wait(self.school.start_index(section['id'],key('bookpages:'+section['id'])))
                if source_guard:source_guard()
                self.service.store.sync_topics();lecture=self.school.store.lecture(lecture['id'])
                # The one recovery request has already used this review's image
                # allowance. Composition must not send another note page.
                wants_images=False
            if source_guard:source_guard()
            result=await self.school.generate(lecture['id'],GenerateInput(expected_version=lecture['version'],request_id=key('review:'+work['id']),confirm_cost=True,use_vision=wants_images,include_recent=False),automatic_note=True)
            with self.personal.connection(write=True) as c:
                c.execute("UPDATE learning_artifact SET payload_json=? WHERE id=? AND kind='auto_note_review' AND status='running'",
                    (encode({**value,'status':'processing','review_job_id':result['job']['id']}),work['id']))
            completed=await self.wait(result['job'],allow_review=True)
            if completed['status']=='needs_review':
                value.update(status='needs_review',review_job_id=completed['id'],review_id=(completed.get('result') or {}).get('artifact_id'),message=NOTE_REVIEW_REQUIRED)
                self.service.store.finish_artifact(work['id'],dict(value),status='needs_review',error={'code':'note_review_required','message':NOTE_REVIEW_REQUIRED});return
            value.update(status='completed',review_id=(completed.get('result') or {}).get('artifact_id'),message='笔记已自动整理，来源复盘可阅读。AI建议不自动采用，也不增加掌握或作答证据。')
            self.service.store.finish_artifact(work['id'],dict(value),model='existing-note-review-pipeline-v1')
        except PersonalError as error:
            value.update(status='needs_review',message=str(error))
            self.service.store.finish_artifact(work['id'],dict(value),status='needs_review',error={'code':error.code,'message':str(error)})
            if error.code=='note_context_changed':raise

    async def run(self,raw_id,title,text,context,*,source_guard=None):
        if source_guard:source_guard()
        if context.get('course_basis')=='user_unlinked':
            return self.pause(context,'needs_context','课程关联已移除，请选择课程后继续整理。')
        with self.personal.connection() as c:
            raw=dict(c.execute('SELECT * FROM raw_item WHERE id=?',(raw_id,)).fetchone())
            attachments=[dict(r) for r in c.execute("SELECT * FROM attachment WHERE raw_item_id=? AND local_path IS NOT NULL",(raw_id,))]
        if raw['inbox_course_id'] and context.get('course_id') and raw['inbox_course_id']!=context['course_id']:
            return self.pause(context,'needs_context','这份笔记的课程关联已有变化，请核对一次目标课程。原件与原有课堂均已保留。')
        if len(attachments)>1:
            return self.pause(context,'needs_context','本次原件已保存，请先核对这些资料是否属于同一课次。')
        if not attachments or attachments[0]['extension'] not in {'pdf',*IMAGE_EXTENSIONS}:
            return self.pause(context,'needs_source','笔记原件已保存；课堂处理支持 PDF、PNG、JPEG 或 WebP 图片。')
        attachment=attachments[0];words=title+'\n'+attachment['filename']+'\n'+text
        from .note_course import infer,source_times
        with self.personal.connection() as c:
            known=[dict(r) for r in c.execute('''SELECT l.* FROM lecture l JOIN lecture_note n ON n.lecture_id=l.id
                JOIN document d ON d.id=n.document_id WHERE d.sha256=?''',(attachment['sha256'],))]
        inference=infer(self.personal,raw_id,attachment['filename']+'\n'+title,text,context,known=known)
        context['course_inference']=inference
        context['note_time_evidence']=source_times(self.personal,raw_id)
        if inference['confidence']!='high':
            context['course_id']=None
            message=('建议归入'+inference['course_name']+'，证据仍需确认。') if inference.get('recommended_course_id') else '笔记已保存，课程暂不够明确，请核对一次课程。'
            return self.pause(context,'needs_context',message)
        context.update(course_id=inference['course_id'],course_basis=inference['basis'])
        course=context['course_id'];course_name=CourseStore(self.personal).get(course)['name']
        profile=profile_for({'lecture':{'course_id':course}})
        with self.personal.connection(write=True) as c:
            if source_guard:source_guard(c,assign_course=course)
            c.execute('UPDATE raw_item SET inbox_course_id=? WHERE id=? AND inbox_course_id IS NULL',(course,raw_id))
            self.school.store._check_note_course(c,raw_id,course)
            sections=[dict(r) for r in c.execute('''SELECT s.* FROM document_section s JOIN textbook t ON t.id=s.textbook_id
                WHERE t.course_id=? AND t.document_id=s.document_id AND t.is_fixture=0''',(course,))]
        all_matches=[s for s in sections if len(section_title(s['title']))>=4 and plain(section_title(s['title'])) in plain(words)]
        heading=attachment['filename']+'\n'+text[:160]
        heading_matches=[s for s in all_matches if plain(section_title(s['title'])) in plain(heading)]
        matched=heading_matches or (all_matches if len(all_matches)==1 else [])
        terms=[term for term in ['无穷小','等价无穷小','同阶','极限','连续性','夹逼','导数','微分','定积分'] if term in words]
        course=context.get('course_id')
        doc=await asyncio.to_thread(self.school.learning.register,attachment['id'])
        if source_guard:source_guard()
        if not doc['page_count']:
            await self.wait(self.school.start_catalog(doc['id'],key('catalog:'+doc['sha256'])))
            if source_guard:source_guard()
            doc=self.school.learning.get(doc['id'])
        with self.personal.connection() as c:
            self.school.store._check_note_course(c,raw_id,course)
            known=[dict(r) for r in c.execute('SELECT l.* FROM lecture l JOIN lecture_note n ON n.lecture_id=l.id WHERE n.document_id=? AND l.course_id=?',(doc['id'],course))]
        explicit=context.get('confirmed_note_lecture_id')
        if explicit:
            lesson=await asyncio.to_thread(self.school.store.confirm_note_lecture,raw_id,explicit,source_guard=source_guard);basis='user_confirmed_note_lecture'
        elif len(known)>1:return self.pause(context,'needs_context','同份笔记关联了多个课次，请核对一次，未新建课次。')
        elif known and context.get('confirmed_note_date') and known[0]['lecture_date']!=context['confirmed_note_date']:
            return self.pause(context,'needs_context','这份笔记已有课次。若需更正，请明确选择目标课次；原关联和原件均已保留。')
        elif known:lesson=known[0];basis='existing_note_document'
        else:
            if context.get('confirmed_note_date'):day,basis=context['confirmed_note_date'],'user_confirmed_note_date'
            elif inference.get('lecture_day'):day,basis=inference['lecture_day'],'verified_timetable_and_note_content'
            else:day,basis=resolve_note_day(attachment['filename'],text,inference.get('source_timestamp') or context['note_time_evidence'].get('pending_created_at') or context.get('source_time'),raw['created_at'],allow_share_default=profile.requires_textbook and not context.get('note_date_requires_confirmation'))
            if not day:return self.pause(context,'needs_context','课堂日期尚不能确定，请核对一次真实课次日期。原件已保存。')
            with self.personal.connection() as c:
                lessons=[dict(r) for r in c.execute('SELECT * FROM lecture WHERE course_id=? AND lecture_date=?',(course,day))]
            confirmed=context.get('confirmed_note_lecture_id')
            if not lessons and basis=='share_day_default' and matched:
                with self.personal.connection() as c:
                    previous=[dict(r) for r in c.execute("SELECT id,title,lecture_date FROM lecture WHERE course_id=? AND lecture_date<? AND lecture_date>=date(?,'-14 days')",(course,day,day))]
                if any(plain(section_title(section['title'])) in plain(prior['title']) for section in matched for prior in previous):
                    return self.pause(context,'needs_context','这份笔记与已收课堂主题相同，但没有课堂日期。请选择已有课次，或填真实课堂日期后继续。')
            if confirmed:lessons=[l for l in lessons if l['id']==confirmed]
            if len(lessons)>1:return self.pause(context,'needs_context','这门课当天已有多个课次，请核对一次，未重复建立。')
            if lessons:
                lesson=lessons[0]
                if not confirmed and matched and not any(plain(section_title(s['title'])) in plain(lesson['title']) for s in matched):
                    return self.pause(context,'needs_context','当天课次与笔记主题不一致，请核对一次，未强行合并。')
                if context.get('confirmed_note_date'):
                    lesson=await asyncio.to_thread(self.school.store.confirm_note_lecture,raw_id,lesson['id'],source_guard=source_guard)
                else:
                    with self.personal.connection(write=True) as c:
                        if source_guard:source_guard(c)
                        self.school.store._check_note_course(c,raw_id,course)
                        linked=c.execute('INSERT OR IGNORE INTO lecture_note VALUES (?,?,?)',(lesson['id'],doc['id'],raw_id))
                        if linked.rowcount:c.execute('UPDATE lecture SET version=version+1,updated_at=? WHERE id=?',(now(),lesson['id']))
            else:
                subject=section_title(max(matched,key=lambda s:len(section_title(s['title'])))['title']) if matched else '、'.join(terms[:2]) if profile.requires_textbook else ''
                lesson_title=day+' '+course_name+(' · '+subject if subject or profile.requires_textbook else '')
                confirmed_day=bool(context.get('confirmed_note_date'))
                scope=':'+course+':'+day if confirmed_day else ''
                lesson=await asyncio.to_thread(self.school.store.create_lecture,LectureInput(course_id=course,lecture_date=day,title=lesson_title,notes_raw_item_id=raw_id),key('lecture:'+raw_id+scope),automatic_note=True,source_guard=source_guard,replace_note_link=confirmed_day)
                context['automatic_lecture_title']=lesson['title']
        if source_guard:source_guard()
        context.update(lecture_id=lesson['id'],lecture_basis=basis)
        artifact=self.service.store.reserve_artifact('note_automation',digest(encode([doc['sha256'],lesson['id'],'note-v1'])))
        if artifact['status']=='interrupted' and artifact.get('model') is None:
            # This ledger owns only local association/OCR. Paid review and
            # vision ledgers are separate and must never be reopened here.
            with self.personal.connection(write=True) as c:
                resumed=c.execute("UPDATE learning_artifact SET status='running',error_json=NULL,completed_at=NULL WHERE id=? AND kind='note_automation' AND status='interrupted' AND model IS NULL",(artifact['id'],)).rowcount
            if resumed:artifact['status']='running'
        if artifact['status']!='running':
            value=artifact.get('payload') or {'status':artifact['status']}
            if value.get('status')=='needs_source' and any(s['link_status']=='confirmed' for s in self.school.store.lecture(lesson['id'])['sections']):value={**value,'status':'local_ready'}
            context['note_automation']=value;return
        try:
            await self.wait(self.school.start_note_index(doc['id'],key('pages:'+doc['sha256']),raw_item_id=raw_id,course_id=course),allow_empty_text=True)
            if source_guard:source_guard()
            lecture=self.school.store.lecture(lesson['id']);linked=[s for s in lecture['sections'] if s['link_status']=='confirmed']
            if not linked and matched:
                best=max(matched,key=lambda s:len(section_title(s['title'])))
                lecture=await asyncio.to_thread(self.school.store.link_sections,lecture['id'],lecture['version'],[best['id']],source_guard=source_guard);linked=[best]
                with self.personal.connection(write=True) as c:
                    if source_guard:source_guard(c)
                    c.execute("UPDATE lecture_section SET origin='automatic_note_topic_match' WHERE lecture_id=? AND section_id=?",(lecture['id'],best['id']))
            for section in linked:
                if section['end_page']-section['start_page']>=20:continue
                await self.wait(self.school.start_index(section['id'],key('bookpages:'+section['id'])))
                if source_guard:source_guard()
            self.service.store.sync_topics()
            ready=bool(linked) or not profile.requires_textbook
            value={'status':'local_ready' if ready else 'needs_source','lecture_id':lecture['id'],'document_id':doc['id'],
                'date_basis':basis,'course_basis':context['course_basis'],'message':'笔记已保存、关联并完成本地识别；正在准备来源复盘。' if ready else '笔记与课次已关联，教材小节尚不能可靠对应，请核对一次来源。'}
            context['note_automation']=value
            self.service.store.finish_artifact(artifact['id'],value,model='local-note-routing-v1')
        except (Exception,asyncio.CancelledError) as error:
            interrupted=isinstance(error,asyncio.CancelledError) or getattr(error,'code',None)=='note_context_changed'
            message=str(error) if isinstance(error,PersonalError) else '本地笔记整理未完成，原件和已保存页面保留。'
            self.pause(context,'needs_review',message)
            record(self.personal,'note_routing',error,job_id=artifact['id'],entity='learning_artifact',status='interrupted' if interrupted else 'needs_review')
            self.service.store.finish_artifact(artifact['id'],context['note_automation'],status='interrupted' if interrupted else 'needs_review',error={'code':getattr(error,'code','note_processing_failed'),'message':message})
            if interrupted:raise
