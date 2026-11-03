"""Small projections over immutable captures and real student attempts."""
import json,re
from datetime import datetime, timedelta, timezone
from uuid import UUID
from ..personal_store import PersonalError, identifier, now, encode
from ..school.documents import digest
from .rules import state, timestamp, due_after, normalized, SHANGHAI, result_from_quote
from .activities import decorate_attempts,preview_sessions,counts,activity_due

NOTE_REVIEW_REQUIRED = '笔记已关联课堂，原图和识别结果已保存。新复盘尚需核对来源与内容，已有复盘仍可阅读。'


def unpack(row, *fields):
    value = dict(row)
    for field in fields:
        value[field.removesuffix('_json')] = json.loads(value.pop(field) or 'null')
    return value


def capture_projection(connection, row, *, include_lecture_choices=True):
    """One read owner for local detail and cloud, including unresolved notes."""
    value = unpack(row, 'context_json', 'parsed_json', 'error_json')
    if (value.get('context') or {}).get('note_share'):
        # An unconfirmed course must not hide the existing lecture the owner
        # needs to select. The form filters these choices after course selection.
        if include_lecture_choices:
            value['lecture_choices'] = [dict(item) for item in connection.execute(
                'SELECT id,lecture_date,title,course_id,version FROM lecture ORDER BY lecture_date DESC,id LIMIT 30')]
        context = value['context']
        automation = context.get('note_automation') or {}
        # Older automatic reviews kept only a generic wait error. Read the
        # already-finished job; a status repair must never replay a model call.
        if (automation.get('status') == 'needs_review' and context.get('lecture_id')
                and automation.get('message') == '笔记处理没有完成，原件和已有结果都已保留。'):
            job = connection.execute("""SELECT id,school_result_json FROM attachment_job
                WHERE operation='school_review' AND status='needs_review'
                AND json_extract(school_payload_json,'$.automatic_note')=1
                AND json_extract(school_payload_json,'$.lecture_id')=? AND created_at>=?
                ORDER BY created_at DESC,id DESC LIMIT 1""", (context['lecture_id'], value['created_at'])).fetchone()
            if job:
                result = json.loads(job['school_result_json'] or '{}')
                automation.update(message=NOTE_REVIEW_REQUIRED, review_job_id=job['id'], review_id=result.get('artifact_id'))
                value['error'] = {'code': 'note_review_required', 'message': NOTE_REVIEW_REQUIRED}
        if automation.get('status') == 'needs_review' and automation.get('review_id') and automation.get('document_id'):
            # Only a validated descendant of this exact failed review can close
            # its processing state. An unrelated review of the same class does
            # not prove that this note was processed.
            repaired = connection.execute("""WITH RECURSIVE descendants(id,lecture_id,depth) AS (
                SELECT id,lecture_id,0 FROM review_artifact WHERE id=?
                UNION ALL
                SELECT child.id,child.lecture_id,ancestor.depth+1 FROM review_artifact child
                JOIN descendants ancestor ON child.lecture_id=ancestor.lecture_id
                    AND json_extract(child.input_versions_json,'$.generation.recovered_from')=ancestor.id
                WHERE ancestor.depth<8
                ) SELECT a.id,a.job_id FROM descendants lineage JOIN review_artifact a ON a.id=lineage.id
                JOIN attachment_job j ON j.id=a.job_id
                JOIN document d ON d.id=?
                JOIN attachment f ON f.raw_item_id=? AND f.sha256=d.sha256
                JOIN snapshot_attachment sa ON sa.attachment_id=f.id AND sa.raw_snapshot_id=?
                JOIN lecture l ON l.id=a.lecture_id
                JOIN raw_item raw ON raw.id=f.raw_item_id AND raw.inbox_course_id=l.course_id
                WHERE a.lecture_id=? AND a.content_json IS NOT NULL AND a.error_json IS NULL
                AND l.course_id=?
                AND EXISTS(SELECT 1 FROM lecture_note n WHERE n.lecture_id=l.id AND n.document_id=d.id)
                AND j.status='completed'
                AND json_extract(a.input_versions_json,'$.generation.validation_status')='passed'
                AND EXISTS(SELECT 1 FROM json_each(a.source_evidence_json) s
                    WHERE json_extract(s.value,'$.document_id')=d.id AND json_extract(s.value,'$.source_kind')='notes')
                ORDER BY a.version DESC LIMIT 1""", (automation['review_id'], automation['document_id'],
                    value['raw_item_id'], value['snapshot_id'], context.get('lecture_id'), context.get('course_id'))).fetchone()
            if repaired:
                automation.update(status='completed', review_id=repaired['id'], review_job_id=repaired['job_id'],
                    message='笔记已整理，复盘可阅读。识别中的不确定内容仍需对照原页核对；AI建议未自动采用。')
                value.update(status='completed', error=None)
    return value


def request_key(value):
    try: return str(UUID(value))
    except (ValueError, TypeError, AttributeError): raise PersonalError('发送标识无效，请重新打开。')


class LearningStore:
    def __init__(self, personal): self.personal = personal

    def sync_topics(self):
        with self.personal.connection(write=True) as c:
            rows = c.execute('''SELECT s.*,t.course_id FROM document_section s JOIN textbook t ON t.id=s.textbook_id
                WHERE s.document_id=t.document_id AND t.is_fixture=0''').fetchall()
            for section in rows:
                linked = [dict(r) for r in c.execute('''SELECT l.id,l.lecture_date,l.title,ls.origin FROM lecture l JOIN lecture_section ls ON ls.lecture_id=l.id
                    WHERE ls.section_id=? AND ls.status='confirmed' AND l.course_id=?''', (section['id'],section['course_id']))]
                direct = [l for l in linked if (l['title'] in section['title'] or l['origin'] in {'automatic_note_topic_match','automatic_note_vision_match'}) and '前置参考' not in section['title']]
                evidence = [{'kind': 'lecture_material' if l in direct else 'lecture_reference', 'lecture_id': l['id'], 'date': l['lecture_date'],
                    'description': '有本节课堂材料，不代表独立会做' if l in direct else '仅作为该课教材参考，不证明这节当时上过课'} for l in linked]
                later = c.execute('''SELECT l.id,l.lecture_date FROM lecture l JOIN lecture_section ls ON ls.lecture_id=l.id
                    JOIN document_section s ON s.id=ls.section_id WHERE s.document_id=? AND s.start_page>?
                    AND ls.status='confirmed' AND l.course_id=? AND s.textbook_id=?
                    ORDER BY l.lecture_date LIMIT 1''', (section['document_id'], section['end_page'],section['course_id'],section['textbook_id'])).fetchone()
                proposed = bool(later and not direct)
                if proposed: evidence.append({'kind': 'progress_inference', 'lecture_id': later['id'], 'date': later['lecture_date'], 'description': '教材位置早于已有课次，推测以前学过，尚待一次确认'})
                tid = 'topic-' + digest(section['id'])[:24]
                c.execute('''INSERT OR IGNORE INTO learning_topic VALUES (?,?,?,?,?,?,?,?)''',
                    (tid, section['course_id'], section['id'], section['title'], 'suggested' if proposed else 'none', encode(evidence), now(), now()))
                old=c.execute('SELECT history_status,exposure_json FROM learning_topic WHERE id=?',(tid,)).fetchone()
                preserved=[e for e in json.loads(old['exposure_json']) if e['kind'] not in {'lecture_material','lecture_reference','progress_inference'}]
                updated=encode([*evidence,*preserved])
                history='suggested' if proposed and old['history_status']=='none' else old['history_status']
                if old['exposure_json']!=updated or history!=old['history_status']:
                    c.execute('UPDATE learning_topic SET exposure_json=?,history_status=?,updated_at=? WHERE id=?',(updated,history,now(),tid))

    def _topic(self, c, tid):
        row = c.execute('SELECT * FROM learning_topic WHERE id=?', (tid,)).fetchone()
        if not row: raise PersonalError('学习内容不存在，请重新读取。', 'not_found', 404)
        return unpack(row, 'exposure_json')

    def topic_for(self, c, course_id, text, suggested=''):
        # A model's tag is only usable when that wording also occurs in the source.
        label = suggested if suggested and suggested in text else ''
        if '无穷小' in text and '等价' in text:label='等价无穷小'
        for term in ['两个重要极限', '重要极限', '夹逼准则', '等价无穷小', '数列极限', '函数极限', '连续性','无穷小的比较']:
            if normalized(term).replace('的','') in normalized(text).replace('的',''):
                if term=='无穷小的比较' and label:continue
                label = '两个重要极限使用条件' if term in {'两个重要极限','重要极限'} else term
                break
        label = label or '本次学习练习'
        existing = [dict(r) for r in c.execute('SELECT * FROM learning_topic WHERE course_id=?', (course_id,))]
        related = [r for r in existing if normalized(label).replace('的','') in normalized(r['title']).replace('的','')]
        if len(related) == 1: return related[0]['id']
        tid = 'topic-' + digest(course_id + ':' + label)[:24]
        c.execute('INSERT OR IGNORE INTO learning_topic VALUES (?,?,NULL,?,\'none\',\'[]\',?,?)', (tid, course_id, label, now(), now()))
        return tid

    def topics(self):
        with self.personal.connection() as c:
            return [unpack(r, 'exposure_json') for r in c.execute('SELECT * FROM learning_topic ORDER BY created_at,id')]

    def confirm_history(self, topic_ids, learned):
        with self.personal.connection(write=True) as c:
            for tid in set(topic_ids):
                t = self._topic(c, tid)
                if t['history_status'] not in {'suggested', 'confirmed', 'dismissed'}:
                    raise PersonalError('这条内容不是历史补档建议，请重新读取。', 'not_historical', 409)
                evidence = [e for e in t['exposure'] if e['kind'] != 'user_history_confirmation']
                evidence.append({'kind': 'user_history_confirmation', 'learned': learned, 'date': now(), 'description': '用户确认以前学过；不是掌握证明'})
                c.execute('UPDATE learning_topic SET history_status=?,exposure_json=?,updated_at=? WHERE id=?',
                    ('confirmed' if learned else 'dismissed', encode(evidence), now(), tid))
        return {'saved': True, 'topic_ids': topic_ids, 'status': 'confirmed' if learned else 'dismissed'}

    @staticmethod
    def active_attempts(c, tid=None):
        items=[unpack(r, 'mistake_json') for r in c.execute('''SELECT a.*,e.topic_id,e.kind,e.question,e.course_id,e.fingerprint AS exercise_fingerprint,
            e.lecture_id AS first_seen_lecture_id,
            capture.raw_item_id AS source_raw_item_id,capture.snapshot_id AS source_snapshot_id,
            capture.context_json AS capture_context_json,capture.parsed_json AS capture_parsed_json,
            CASE WHEN a.capture_id IS NOT NULL THEN
                (SELECT CASE WHEN count(*)=1 THEN max(l.id) END FROM lecture l
                 WHERE l.course_id=e.course_id AND l.lecture_date=date(a.occurred_at,'+8 hours'))
                ELSE e.lecture_id END AS lecture_id
            FROM learning_attempt a JOIN learning_exercise e ON e.id=a.exercise_id
            LEFT JOIN learning_capture capture ON capture.id=a.capture_id
            WHERE (? IS NULL OR e.topic_id=?) AND NOT EXISTS(SELECT 1 FROM learning_attempt n WHERE n.supersedes_id=a.id)
            ORDER BY a.received_at,a.id''', (tid, tid))]
        return decorate_attempts(c,items)

    def topic(self, tid):
        with self.personal.connection() as c:
            topic = self._topic(c, tid)
            attempts = self.active_attempts(c, tid)
            plans = [dict(r) for r in c.execute('''SELECT f.* FROM learning_followup f JOIN learning_attempt a ON a.id=f.attempt_id
                JOIN learning_exercise e ON e.id=a.exercise_id WHERE e.topic_id=? ORDER BY f.due_at''', (tid,))]
            artifacts = [unpack(r, 'payload_json', 'source_json', 'error_json') for r in c.execute('''SELECT * FROM learning_artifact
                WHERE topic_id=? AND kind IN ('retro_summary','diagnostic','mistake_analysis') ORDER BY created_at DESC LIMIT 15''', (tid,))]
            exercises = [self.public_exercise(r) for r in c.execute('''SELECT e.* FROM learning_exercise e
                WHERE topic_id=? AND kind='diagnostic' ORDER BY question_order,id LIMIT 5''', (tid,))]
            if topic['section_id']:
                section = c.execute('SELECT * FROM document_section WHERE id=?', (topic['section_id'],)).fetchone()
                topic['section'] = dict(section) if section else None
            topic.update(attempts=attempts, followups=plans, artifacts=artifacts, diagnostic=exercises,
                         learning_state=state(topic, attempts, plans, now()))
            last=c.execute('''SELECT payload_json FROM learning_artifact WHERE kind='answer_receipt'
                AND EXISTS(SELECT 1 FROM json_each(payload_json,'$.topic_ids') WHERE value=?) ORDER BY created_at DESC LIMIT 1''',(tid,)).fetchone()
            topic['last_answer']=json.loads(last[0]) if last else None
        # No correct key is sent until the learner submits this generated question.
        return topic

    @staticmethod
    def public_exercise(row):
        value={k: v for k, v in unpack(row, 'options_json', 'sources_json').items() if k not in {'answer_json', 'explanation'}}
        value['first_seen_lecture_id']=value.pop('lecture_id')
        return value

    def record_capture(self, raw_id, sid, text, context):
        value = digest(encode({'sid': sid, 'text': text, 'protocol': 'learning-capture-v1'}))
        cid = 'capture-' + value[:32]
        with self.personal.connection(write=True) as c:
            c.execute('INSERT OR IGNORE INTO learning_capture VALUES (?,?,?,?,?,?,NULL,NULL,?)', (cid, raw_id, sid, value, 'received', encode(context), now()))
            return unpack(c.execute('SELECT * FROM learning_capture WHERE id=?',(cid,)).fetchone(), 'context_json', 'parsed_json', 'error_json')

    def capture_status(self, cid, status, *, parsed=None, error=None):
        with self.personal.connection(write=True) as c:
            c.execute('UPDATE learning_capture SET status=?,parsed_json=COALESCE(?,parsed_json),error_json=? WHERE id=?',
                (status, encode(parsed) if parsed is not None else None, encode(error) if error else None, cid))

    def save_page(self, capture, page, *, trusted):
        context = capture['context'];course = context.get('course_id')
        if not course:
            self.capture_status(capture['id'], 'needs_context', parsed=page.model_dump())
            return []
        ids = []
        with self.personal.connection(write=True) as c:
            if context.get('lecture_id'):
                bound=c.execute('SELECT course_id FROM lecture WHERE id=?',(context['lecture_id'],)).fetchone()
                if not bound or bound[0]!=course:raise PersonalError('确认的课次与课程不一致；来源保留，未强行关联。','lecture_context_mismatch',409)
            original=c.execute('SELECT original_text FROM raw_snapshot WHERE id=?',(capture['snapshot_id'],)).fetchone()
            source_text=original[0] if original else ''
            for q in page.questions:
                # Extracted strings must exist in the immutable material, checked by the service.
                tid = self.topic_for(c, course, q.question + '\n' + q.explanation+'\n'+context.get('preview_title',''), q.topic)
                fingerprint = digest(encode([course, normalized(q.question), q.options]))
                eid = 'exercise-' + fingerprint[:32]
                # Formatting changes across result pages must not split the same
                # exercise. Require all options and a unique exact normalized match.
                if len(q.options)>=2 and context.get('options_quality')!='ocr_unverified':
                    canonical=lambda options:{k:re.sub(r'\s+','',normalized(v)).replace('−','-').replace('–','-') for k,v in options.items()}
                    matches=[r for r in c.execute("SELECT id,fingerprint,question,options_json FROM learning_exercise WHERE course_id=? AND kind='classroom'",(course,))
                        if normalized(r['question'])==normalized(q.question) and canonical(json.loads(r['options_json']))==canonical(q.options)]
                    if len(matches)==1:eid,fingerprint=matches[0]['id'],matches[0]['fingerprint']
                c.execute('''INSERT OR IGNORE INTO learning_exercise
                    (id,course_id,topic_id,lecture_id,capture_id,artifact_id,kind,fingerprint,question,options_json,answer_json,explanation,sources_json,created_at)
                    VALUES (?,?,?,?,?,NULL,?,?,?,?,?,?,?,?)''',
                    (eid, course, tid, context.get('lecture_id'), capture['id'], 'classroom', fingerprint, q.question, encode(q.options),
                     encode({'text': q.correct_answer}) if q.correct_answer else None, q.explanation,
                     encode([{'raw_item_id':capture['raw_item_id'],'snapshot_id':capture['snapshot_id'],'quote':q.question,'quality':context['quality']}]), now()))
                if not q.my_answer.strip(): continue
                occurred = timestamp(q.submitted_at) if q.submitted_at else None
                # A future timestamp or missing submission label is never repaired by guessing.
                if occurred and occurred > datetime.now(timezone.utc)+timedelta(minutes=5): occurred = None
                result = result_from_quote(q.result_quote) if trusted else 'unknown'
                if trusted and context.get('capture_method')=='readonly_verified_capture' and context.get('result_origin') in {'platform_graded_score','published_answer_comparison'}:
                    # This context is supplied by the verified local capture adapter,
                    # never by page text or model output. Preserve the actual quote.
                    if q.result_quote and q.result_quote in source_text and q.question in source_text and q.my_answer:
                        result=q.result
                if context.get('answer_key_status')=='publication_conflict':result='unknown'
                stamp = occurred.astimezone(timezone.utc).isoformat() if occurred else None
                identity=[eid,q.my_answer,result,stamp or 'submission-time-unknown']
                if context.get('activity_type')=='preview':identity.extend(['preview',context['preview_session_id']])
                elif context.get('activity_type')=='practice':identity.append('practice')
                elif context.get('activity_id'):identity.extend(['classroom',context['activity_id']])
                key = digest(encode(identity))
                aid = 'attempt-' + key[:32]
                supersedes=c.execute('''SELECT id FROM learning_attempt WHERE exercise_id=? AND capture_id=? AND answer=? AND result='unknown'
                    ORDER BY received_at DESC LIMIT 1''',(eid,capture['id'],q.my_answer)).fetchone() if trusted and result!='unknown' else None
                if not supersedes and trusted and result!='unknown' and context.get('activity_id'):
                    supersedes=c.execute('''SELECT a.id FROM learning_attempt a JOIN learning_capture cap ON cap.id=a.capture_id
                        WHERE a.exercise_id=? AND a.answer=? AND a.result='unknown' AND json_extract(cap.context_json,'$.activity_id')=?
                        AND NOT EXISTS(SELECT 1 FROM learning_attempt n WHERE n.supersedes_id=a.id) ORDER BY a.received_at DESC LIMIT 1''',
                        (eid,q.my_answer,context['activity_id'])).fetchone()
                suggestion = {'category':'暂时无法判断','reason':'只有题目和答案时，无法可靠区分概念、计算或粗心。','origin':'local_evidence_rule','uncertain':True}
                independent=bool(trusted and context.get('independent') is True and context.get('independence_source')=='explicit_user_chat' and context.get('independence_statement'))
                origin=context.get('result_origin','platform_result_text')
                if origin not in {'platform_result_text','platform_graded_score','published_answer_comparison'}:origin='platform_result_text'
                c.execute('''INSERT OR IGNORE INTO learning_attempt
                    (id,exercise_id,capture_id,answer,result,result_origin,independent,occurred_at,received_at,fingerprint,mistake_json,supersedes_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (aid,eid,capture['id'],q.my_answer,result,origin if trusted else 'ocr_pending',int(independent),stamp,now(),key,encode(suggestion) if result=='incorrect' else '{}',supersedes[0] if supersedes else None))
                if result == 'incorrect':
                    c.execute("INSERT OR IGNORE INTO learning_followup VALUES (?,?,NULL,?,'pending',NULL,?)",('followup-'+digest(aid)[:24],aid,activity_due(stamp or now(),context),now()))
                ids.append(aid)
            materials=[m.model_dump() for m in page.preview_materials]
            if page.preview_completed_quote and not materials:
                materials=[{'title':context.get('preview_title','预习材料'),'kind':'other','completed':True,'evidence_quote':page.preview_completed_quote}]
            for material in materials:
                tid = self.topic_for(c,course,context.get('preview_title','')+'\n'+material['title'])
                t = self._topic(c,tid)
                completed=bool(material['completed'] and trusted)
                item = {'kind':'preview_completed' if completed else 'preview_material_received' if trusted else 'preview_suggestion',
                    'date':now(),'capture_id':capture['id'],'preview_session_id':context.get('preview_session_id'),
                    'raw_item_id':capture['raw_item_id'],'snapshot_id':capture['snapshot_id'],
                    'quote':material['evidence_quote'],'material_title':material['title'],'material_kind':material['kind'],
                    'available_at':material.get('available_at'),'completed_at':material.get('completed_at'),'source_reference':material.get('source_reference',''),
                    'description':'预习材料只产生接触证据，不代表会做题或掌握'}
                evidence = t['exposure']
                evidence=[e for e in evidence if not(e.get('capture_id')==capture['id'] and e.get('material_title')==material['title'])]
                c.execute('UPDATE learning_topic SET exposure_json=?,updated_at=? WHERE id=?',(encode([*evidence,item]),now(),tid))
        self.capture_status(capture['id'],'completed' if trusted else 'needs_review',parsed=page.model_dump())
        return ids

    def reserve_artifact(self, kind, input_hash, tid=None, sources=()):
        with self.personal.connection(write=True) as c:
            c.execute('''INSERT OR IGNORE INTO learning_artifact
                (id,kind,topic_id,input_hash,status,source_json,created_at) VALUES (?,?,?,?,'running',?,?)''',
                (identifier(),kind,tid,input_hash,encode(sources),now()))
            return unpack(c.execute('SELECT * FROM learning_artifact WHERE kind=? AND input_hash=?',(kind,input_hash)).fetchone(),'payload_json','source_json','error_json')

    def finish_artifact(self, artifact_id, payload=None, *, status='completed', model=None, usage=None, cost=None, error=None):
        with self.personal.connection(write=True) as c:
            c.execute('''UPDATE learning_artifact SET status=?,payload_json=?,model=?,usage_json=?,cost_json=?,error_json=?,completed_at=? WHERE id=? AND status='running' ''',
                (status,encode(payload) if payload is not None else None,model,encode(usage) if usage else None,encode(cost) if cost else None,encode(error) if error else None,now(),artifact_id))

    def save_quiz(self, artifact, questions, *, prior=None):
        with self.personal.connection(write=True) as c:
            topic = self._topic(c,artifact['topic_id'])
            sources = {s['source_id']:s for s in artifact['source']}
            ids=[]
            for index,q in enumerate(questions):
                fingerprint=digest(encode([artifact['id'],index,q.question]));eid='exercise-'+fingerprint[:32]
                c.execute('''INSERT OR IGNORE INTO learning_exercise
                    (id,course_id,topic_id,lecture_id,capture_id,artifact_id,kind,fingerprint,question,options_json,answer_json,explanation,sources_json,created_at,question_order)
                    VALUES (?,?,?,NULL,NULL,?,?,?,?,?,?,?,?,?,?)''',
                    (eid,topic['course_id'],topic['id'],artifact['id'],'retest' if prior else 'diagnostic',fingerprint,q.question,encode(q.options),encode({'key':q.correct_key}),q.explanation,encode([sources[s] for s in q.source_ids]),now(),index))
                ids.append(eid)
            if prior:
                c.execute("UPDATE learning_followup SET exercise_id=?,status='ready' WHERE attempt_id=? AND status!='resolved'",(ids[0],prior))
            return ids

    @staticmethod
    def followup_due(c,aid,stamp,repeat=False):
        seen=set()
        while aid and aid not in seen:
            seen.add(aid)
            row=c.execute('SELECT a.retest_of,cap.context_json FROM learning_attempt a LEFT JOIN learning_capture cap ON cap.id=a.capture_id WHERE a.id=?',(aid,)).fetchone()
            if not row:break
            if row['context_json']:return activity_due(stamp,json.loads(row['context_json']),repeat)
            aid=row['retest_of']
        return due_after(stamp,repeat=repeat)

    def answer(self, exercise_ids, answers, independent, key):
        key=request_key(key);signature=digest(encode([answers,independent]))
        receipt_hash=digest('answer:'+key)
        with self.personal.connection(write=True) as c:
            old=c.execute("SELECT * FROM learning_artifact WHERE kind='answer_receipt' AND input_hash=?",(receipt_hash,)).fetchone()
            if old:
                result=json.loads(old['payload_json'])
                if result['signature']!=signature: raise PersonalError('同次作答内容已变化，请开始新一轮。','idempotency_conflict',409)
                return result
            first=c.execute('SELECT artifact_id,kind FROM learning_exercise WHERE id=?',(next(iter(answers)),)).fetchone()
            if not first:raise PersonalError('题目不存在。','invalid_reference',400)
            required={r[0] for r in c.execute('SELECT id FROM learning_exercise WHERE artifact_id=?',(first['artifact_id'],))}
            if set(answers)!=required:raise PersonalError('请完成本次全部问题，再保存。')
            stamp=now();results=[];topics=set()
            for eid,answer in answers.items():
                e=c.execute("SELECT * FROM learning_exercise WHERE id=? AND kind IN ('diagnostic','retest','self_test')",(eid,)).fetchone()
                if not e: raise PersonalError('这道题不能通过自测接口作答。','invalid_reference',400)
                if c.execute("SELECT 1 FROM learning_attempt WHERE exercise_id=? AND result_origin='source_backed_quiz'",(eid,)).fetchone():
                    raise PersonalError('本题已作答并揭示解析。后续复测会准备不同题目。','already_answered',409)
                correct=json.loads(e['answer_json'] or '{}').get('key')
                if answer not in json.loads(e['options_json']): raise PersonalError('请选择题目中的一个答案。')
                plan=c.execute('SELECT f.*,a.occurred_at,a.received_at FROM learning_followup f JOIN learning_attempt a ON a.id=f.attempt_id WHERE f.exercise_id=?',(eid,)).fetchone()
                if plan and plan['status']=='resolved': raise PersonalError('这次复测已完成，请开始新的题目。','already_answered',409)
                result='correct' if answer==correct else 'incorrect'
                retest_success=bool(plan and independent and result=='correct' and (timestamp(stamp)-timestamp(plan['occurred_at'] or plan['received_at'])).total_seconds()>=86400)
                aid=identifier();fp=digest(key+eid)
                c.execute('''INSERT INTO learning_attempt
                    (id,exercise_id,answer,result,result_origin,independent,occurred_at,received_at,fingerprint,retest_of,retest_success)
                    VALUES (?,?,?,?,'source_backed_quiz',?,?,?,?,?,?)''',(aid,eid,answer,result,int(independent),stamp,stamp,fp,plan['attempt_id'] if plan else None,int(retest_success)))
                if result=='incorrect':
                    c.execute("INSERT INTO learning_followup VALUES (?,?,NULL,?,'pending',NULL,?)",('followup-'+digest(aid)[:24],aid,self.followup_due(c,plan['attempt_id'],stamp,True) if plan else due_after(stamp),stamp))
                if retest_success:
                    c.execute('''WITH RECURSIVE lineage(id) AS (SELECT ? UNION ALL
                        SELECT a.retest_of FROM learning_attempt a JOIN lineage l ON l.id=a.id WHERE a.retest_of IS NOT NULL)
                        UPDATE learning_followup SET status='resolved',resolved_attempt_id=? WHERE attempt_id IN (SELECT id FROM lineage)''',(plan['attempt_id'],aid))
                elif plan and result=='correct':
                    # An assisted/early success remains practice. Prepare a fresh question,
                    # since this one's answer has now been revealed.
                    c.execute("UPDATE learning_followup SET status='pending',exercise_id=NULL,due_at=? WHERE id=?",(self.followup_due(c,plan['attempt_id'],stamp,True),plan['id']))
                topics.add(e['topic_id'])
                results.append({'id':aid,'exercise_id':eid,'question':e['question'],'answer':answer,'correct_key':correct,'result':result,'explanation':e['explanation'],'retest_success':retest_success,'independent':independent,'retest_of':plan['attempt_id'] if plan else None})
            right=sum(r['result']=='correct' for r in results)
            # A small screen is a sample, not an entire-chapter certification.
            judgement='本次抽查基本还记得' if right==len(results) and independent else '本次抽查有些生疏' if right>=len(results)/2 else '本次抽查明显忘了，建议先看补档摘要' if right else '本次抽查需要重新学这些要点'
            if not independent: judgement='已记录练习；使用了提示，不作为独立成功证据'
            payload={'signature':signature,'items':results,'judgement':judgement,'scope':'只判断本次抽查范围；稍后独立复测才能形成更强证据。','topic_ids':list(topics)}
            c.execute('''INSERT INTO learning_artifact(id,kind,input_hash,status,payload_json,created_at,completed_at)
                VALUES (?,'answer_receipt',?,'completed',?,?,?)''',(identifier(),receipt_hash,encode(payload),stamp,stamp))
        return payload

    def overview(self):
        current=now();today=timestamp(current).astimezone(SHANGHAI).date()
        summaries=[]
        with self.personal.connection() as c:
            attempts=self.active_attempts(c)
            plans=[dict(r) for r in c.execute('''SELECT f.*,e.topic_id,t.title FROM learning_followup f JOIN learning_attempt a ON a.id=f.attempt_id
                JOIN learning_exercise e ON e.id=a.exercise_id JOIN learning_topic t ON t.id=e.topic_id
                WHERE NOT EXISTS(SELECT 1 FROM learning_attempt child WHERE child.retest_of=f.attempt_id AND child.result='incorrect')
                ORDER BY f.due_at''')]
            for row in c.execute('SELECT * FROM learning_topic ORDER BY updated_at DESC,id'):
                t=unpack(row,'exposure_json');aa=[a for a in attempts if a['topic_id']==t['id']];ff=[f for f in plans if f['topic_id']==t['id']]
                summaries.append({**t,'last_activity':max((a['received_at'] for a in aa),default=t['updated_at']),'learning_state':state(t,aa,ff,current),
                    'before_after_progress':any(a['before_after_progress'] for a in aa)})
            pending=[unpack(r,'context_json','error_json') for r in c.execute("SELECT id,raw_item_id,status,context_json,error_json,created_at FROM learning_capture WHERE status!='completed' ORDER BY created_at DESC LIMIT 10")]
            preview=sum(e['kind']=='preview_completed' and timestamp(e['date']).astimezone(SHANGHAI).date()==today for t in summaries for e in t['exposure'])
            courses=[dict(r) for r in c.execute('SELECT id,name FROM course ORDER BY name')]
            sessions=preview_sessions(c,attempts,plans)
            recent_lectures=[dict(r) for r in c.execute('''SELECT l.id,l.course_id,l.lecture_date,l.title,
                EXISTS(SELECT 1 FROM lecture_note n WHERE n.lecture_id=l.id) AS notes_available
                FROM lecture l WHERE l.lecture_date<=? ORDER BY l.lecture_date DESC,l.updated_at DESC LIMIT 3''',(today.isoformat(),))]
        dated=[a for a in attempts if a['activity_type']=='classroom' and a['occurred_at'] and timestamp(a['occurred_at']).astimezone(SHANGHAI).date()==today]
        undated=[a for a in attempts if a['activity_type']=='classroom' and not a['occurred_at'] and timestamp(a['received_at']).astimezone(SHANGHAI).date()==today]
        summaries.sort(key=lambda t:t['last_activity'],reverse=True)
        weak=[t for t in summaries if t['learning_state']['label'] in {'建议复习','不稳定'}]
        weak.sort(key=lambda t:(t['learning_state']['label']=='不稳定',t['last_activity']),reverse=True)
        progress=[t for t in summaries if t['learning_state']['label'] in {'在进步','基本稳定'}]
        history=[t for t in summaries if t['history_status'] in {'suggested','confirmed'}]
        due=[f for f in plans if f['status']!='resolved' and timestamp(f['due_at'])<=timestamp(current)]
        upcoming=[f for f in plans if f['status']!='resolved' and timestamp(f['due_at'])>timestamp(current)]
        return {'today':today.isoformat(),'courses':courses,'recent_lectures':recent_lectures,'classroom':counts(dated),'received_without_date':counts(undated),'preview_completed':preview,
            'preview_sessions':sessions[:10],'preview':sessions[0] if sessions else None,
            'weak':weak[:3],'progress':progress[:3],'history':history[:20],'topics':summaries[:50],'pending_captures':pending,
            'due':due[:3],'upcoming':upcoming[:3],'total_due':len(due),'history_pending_diagnostic':sum(not any(a['kind']=='diagnostic' and a['topic_id']==t['id'] for a in attempts) for t in history),
            'limits':{'topics_shown':min(len(summaries),50),'topics_total':len(summaries)},'automation':'学习通当前使用结果页一键分享或截图；未连接自动监控登录会话。'}
