"""Preview sessions are contexts over the existing captures and attempts, not a second ledger."""
import json
import re
from datetime import date,datetime,timedelta,timezone
from ..personal_store import encode
from ..school.documents import digest
from .rules import normalized,timestamp,SHANGHAI,due_after


def header(text):
    return re.split(r'(?m)^\s*(?:第[0-9一二三四五六七八九十]+题|\d+[.、．]\s*|(?:题目|题干)[：:])',text,maxsplit=1)[0][:2500]


def activity_context(text,title,raw_id,course_id):
    head=title+'\n'+header(text)
    kind='preview' if re.search(r'预习',head) and not re.search(r'非预习|不是预习',head) else 'classroom' if re.search(r'课堂题|课堂练习|随堂|课堂测验|即时答题',head) else 'practice'
    result={'activity_type':kind}
    if kind!='preview':return result
    name=re.search(r'(?:预习任务|预习名称|任务名称|预习主题)[：:]\s*([^\n]{1,180})',head)
    external=re.search(r'(?:预习任务ID|预习ID|任务ID)[：:]\s*([\w-]{1,100})',head,re.I)
    dates=set(re.findall(r'(?:课次日期|上课日期|课堂日期|授课日期|课程日期|上课时间)[：:]\s*(20\d\d-\d\d-\d\d)',head))
    dates={d for d in dates if valid_date(d)}
    target=next(iter(dates)) if len(dates)==1 else None
    submitted=set(re.findall(r'(?:提交时间|预习完成时间)[：:]\s*(20\d\d-\d\d-\d\d)',text))
    submitted={d for d in submitted if valid_date(d)}
    day=next(iter(submitted)) if len(submitted)==1 else None
    if day and day>datetime.now(SHANGHAI).date().isoformat():day=None
    label=name[1].strip() if name else '课前预习'
    # Explicit task identity or a named dated session can unite material/result pages.
    # Otherwise retain the original item's identity; never merge unrelated tasks by receipt time.
    key=external[1] if external and course_id else [label,target or day] if name and course_id and (target or day) else ['original',raw_id]
    result.update(preview_session_id='preview-'+digest(encode([course_id,key]))[:24],preview_title=label,
                  target_lecture_date=target,activity_date=day,
                  session_basis='explicit_task_id' if external else 'named_dated_task' if name and (target or day) else 'original_item')
    return result


def valid_date(value):
    try:date.fromisoformat(value);return True
    except (ValueError,TypeError):return False


def day_of(value):
    moment=timestamp(value)
    return moment.astimezone(SHANGHAI).date().isoformat() if moment else None


def activity_due(value,context,repeat=False):
    due=timestamp(due_after(value,repeat=repeat))
    target=context.get('target_lecture_date') if context.get('activity_type')=='preview' else None
    if target and valid_date(target):
        earliest=timestamp((date.fromisoformat(target)+timedelta(days=1)).isoformat()+'T09:00:00+08:00')
        if earliest>due:due=earliest
    return due.astimezone(timezone.utc).isoformat()


def lectures(c,course):
    result=[dict(r) for r in c.execute('''SELECT l.*,EXISTS(SELECT 1 FROM lecture_note n WHERE n.lecture_id=l.id) AS notes_available
        FROM lecture l WHERE l.course_id=? ORDER BY l.lecture_date,l.id''',(course,))]
    for lesson in result:
        blocks=c.execute('''SELECT b.text FROM lecture_note n JOIN document_block b ON b.document_id=n.document_id
            JOIN document_page p ON p.id=b.page_id AND p.current_extraction_key=b.extraction_key
            WHERE n.lecture_id=? AND b.extraction_quality IN ('text_extracted','visually_checked','user_verified')
            ORDER BY b.page_index,b.block_order LIMIT 20''',(lesson['id'],)).fetchall()
        lesson['notes_evidence_text']='\n'.join(r[0] or '' for r in blocks)[:20000]
    return result


def resolve_preview(context,occurred,available):
    target=context.get('target_lecture_date')
    basis='explicit_lecture_date'
    if not target:
        source_day=context.get('activity_date') or day_of(occurred)
        if not source_day:
            # A unique topic match can route the source without claiming a
            # submission time or proving it happened before the lecture.
            title=normalized(context.get('preview_title','')).replace('的','')
            candidates=[l for l in available if len(title)>=4 and title in normalized(l['title']).replace('的','')]
            return (candidates[0],'unique_lecture_topic') if len(candidates)==1 else (None,'submission_date_unknown')
        target=(date.fromisoformat(source_day)+timedelta(days=1)).isoformat()
        basis='inferred_unique_next_day'
    matches=[l for l in available if l['lecture_date']==target]
    return (matches[0],basis) if len(matches)==1 else (None,'no_unique_lecture')


def related_topic(topic,lecture):
    plain=lambda s:normalized(s).replace('的','')
    terms={'重要极限':['重要极限','important limit','important sine limit','important exponential limit'],
        '夹逼':['夹逼','squeeze theorem'],'等价无穷小':['等价无穷小','equivalent infinitesimal'],
        '数列极限':['数列极限','sequence limit'],'函数极限':['函数极限','function limit','limit of a function'],
        '连续':['连续','continuity']}
    terms['无穷小的比较']=['无穷小的比较','高阶无穷小','同阶无穷小']
    note_text=plain(lecture.get('notes_evidence_text',''))
    # A title is routing metadata. Only reliable current note content can support coverage.
    return any(plain(t) in plain(topic) and any(plain(alias) in note_text for alias in aliases) for t,aliases in terms.items())


def decorate_attempts(c,items):
    available={};by_id={a['id']:a for a in items}
    for a in items:
        context=json.loads(a.pop('capture_context_json',None) or '{}')
        parsed=json.loads(a.pop('capture_parsed_json',None) or '{}')
        a['activity_type']=context.get('activity_type','classroom') if a['kind']=='classroom' else a['kind']
        a['preview_session_id']=context.get('preview_session_id') if a['activity_type']=='preview' else None
        a['lecture_link_basis']='matching_submission_date' if a.get('lecture_id') else None
        a['lecture_date']=None;a['related_class_notes']=False
        a['before_lecture_confirmed']=bool(context.get('temporal_relation')=='before_lecture' and context.get('relation_source')=='explicit_user_chat')
        a['classroom_improvement']=False
        if a['activity_type']=='preview':
            if a['course_id'] not in available:available[a['course_id']]=lectures(c,a['course_id'])
            lesson,basis=resolve_preview(context,a['occurred_at'],available[a['course_id']])
            a['lecture_id']=lesson['id'] if lesson else None;a['lecture_link_basis']=basis
            if lesson:
                a['lecture_date']=lesson['lecture_date']
                title=c.execute('SELECT title FROM learning_topic WHERE id=?',(a['topic_id'],)).fetchone()
                a['related_class_notes']=bool(lesson['notes_available'] and title and related_topic(title[0],lesson))
        elif a['activity_type']=='classroom':
            if a['course_id'] not in available:available[a['course_id']]=lectures(c,a['course_id'])
            confirmed=context.get('lecture_id') if context.get('lecture_basis')=='user_confirmed_cycle' else None
            lesson=next((l for l in available[a['course_id']] if l['id']==(confirmed or a.get('lecture_id'))),None)
            if not lesson and context.get('capture_method')=='readonly_verified_capture' and context.get('native_course_name')=='高等数学Ⅰ1':
                published_day=day_of(context.get('activity_published_at'))
                ended_day=context.get('platform_activity_day')
                day=ended_day if ended_day and ended_day==published_day else published_day if not ended_day else None
                candidates=[l for l in available[a['course_id']] if day and l['lecture_date']==day]
                if len(candidates)==1:
                    lesson=candidates[0];a['lecture_link_basis']='unique_lecture_on_platform_activity_day'
            if lesson:
                a['lecture_id']=lesson['id'];a['lecture_date']=lesson['lecture_date']
                if confirmed:a['lecture_link_basis']='user_confirmed_cycle'
                title=c.execute('SELECT title FROM learning_topic WHERE id=?',(a['topic_id'],)).fetchone()
                a['related_class_notes']=bool(lesson['notes_available'] and title and related_topic(title[0],lesson))
        record=next((q for q in parsed.get('questions',[]) if normalized(q['question'])==normalized(a['question']) and q.get('my_answer')==a['answer']),None)
        a['platform_result_quote']=record.get('result_quote','') if record else ''
        a['platform_correct_answer']=record.get('correct_answer','') if record else ''
        a['platform_explanation']=record.get('explanation','') if record else ''
        a['platform_result_notice']=context.get('result_notice','')
        a['answer_key_status']=context.get('answer_key_status','displayed' if a['platform_correct_answer'] else 'not_shown')
        if a['answer_key_status']=='publication_conflict':
            a['displayed_answer_unconfirmed']=a['platform_correct_answer'];a['platform_correct_answer']=''
    for a in items:
        root=a;seen=set()
        while root.get('retest_of') in by_id and root['id'] not in seen:
            seen.add(root['id']);root=by_id[root['retest_of']]
        a['origin_activity_type']=root['activity_type']
        if root['activity_type']=='preview':
            a['preview_session_id']=root['preview_session_id'];a['lecture_id']=root['lecture_id']
            a['lecture_date']=root['lecture_date'];a['lecture_link_basis']=root['lecture_link_basis']
            a['related_class_notes']=root['related_class_notes']
            a['before_lecture_confirmed']=root['before_lecture_confirmed']
        before=day_of(root['occurred_at']);after=day_of(a['occurred_at']);lesson=a['lecture_date']
        a['before_after_progress']=bool(a['retest_success'] and a['independent'] and root['activity_type']=='preview'
            and a['related_class_notes'] and after and lesson and after>lesson
            and (root['before_lecture_confirmed'] or before and before<lesson))
    for a in items:
        if a['activity_type']!='classroom' or a['result']!='correct' or not a['independent'] or not a['related_class_notes']:continue
        # A broad topic match could confuse an already-correct easy item with a
        # prior weakness. Tentative classroom progress requires the same exercise.
        a['classroom_improvement']=any(p['activity_type']=='preview' and p['result']=='incorrect'
            and p['exercise_id']==a['exercise_id'] and p['lecture_id']==a['lecture_id']
            and (p['before_lecture_confirmed'] or day_of(p['occurred_at']) and p['lecture_date'] and day_of(p['occurred_at'])<p['lecture_date']) for p in items)
    return items


def counts(items):
    return {'total':len(items),'correct':sum(a['result']=='correct' for a in items),
            'incorrect':sum(a['result']=='incorrect' for a in items),'unknown':sum(a['result']=='unknown' for a in items)}


def weaknesses(c,items):
    grouped={}
    for a in items:
        if a['result']=='incorrect':grouped[a['topic_id']]=grouped.get(a['topic_id'],0)+1
    result=[]
    for tid,n in sorted(grouped.items(),key=lambda v:-v[1]):
        row=c.execute('SELECT title FROM learning_topic WHERE id=?',(tid,)).fetchone()
        result.append({'topic_id':tid,'title':row[0],'incorrect':n})
    return result[:3]


def preview_sessions(c,attempts,plans=()):
    sessions={};course_lessons={}
    for row in c.execute('SELECT * FROM learning_capture ORDER BY created_at DESC'):
        cap=dict(row);context=json.loads(cap['context_json'])
        if context.get('activity_type')!='preview':continue
        sid=context['preview_session_id'];course=context.get('course_id')
        session=sessions.setdefault(sid,{'id':sid,'title':context.get('preview_title','课前预习'),'course_id':course,
            'activity_date':context.get('activity_date'),'target_lecture_date':context.get('target_lecture_date'),
            'received_at':cap['created_at'],'capture_ids':[],'raw_item_ids':[],'materials':[],'lecture':None,'_lecture_candidates':{}})
        session['capture_ids'].append(cap['id']);session['raw_item_ids'].append(cap['raw_item_id'])
        if context.get('class_completed') is True and context.get('cycle_confirmation_source')=='explicit_user_chat':session['class_completed']=True
        for field in ['activity_date','target_lecture_date']:
            if not session[field] and context.get(field):session[field]=context[field]
        page=json.loads(cap['parsed_json'] or '{}')
        material=page.get('preview_materials',[])
        if not material and page.get('preview_completed_quote'):
            material=[{'title':session['title'],'kind':'other','completed':True,'evidence_quote':page['preview_completed_quote']}]
        for item in material:
            key=(item['title'],item['kind'])
            old=next((m for m in session['materials'] if (m['title'],m['kind'])==key),None)
            complete=bool(item.get('completed') and cap['status']=='completed')
            if old:old['completed']=old['completed'] or complete
            else:session['materials'].append({**item,'completed':complete,'evidence_type':'exposure','raw_item_id':cap['raw_item_id']})
        if course:
            if course not in course_lessons:course_lessons[course]=lectures(c,course)
            lesson,basis=resolve_preview(context,None,course_lessons[course])
            if lesson:session['_lecture_candidates'][lesson['id']]={k:lesson[k] for k in ['id','lecture_date','title','notes_available','accepted_artifact_id']}|{'link_basis':basis}
    for session in sessions.values():
        aa=[a for a in attempts if a['activity_type']=='preview' and a['preview_session_id']==session['id']]
        session['exercises']=counts(aa);session['weaknesses']=weaknesses(c,aa)
        session['weakness_coverage']=[{**w,'covered_in_notes':any(a['topic_id']==w['topic_id'] and a['related_class_notes'] for a in aa)} for w in session['weaknesses']]
        session['material_exposures']=len(session['materials'])
        session['material_completed']=sum(m['completed'] for m in session['materials'])
        # Resolve per-attempt dates too, including pages with no page-level submit date.
        linked={a['lecture_id'] for a in aa if a['lecture_id']}
        for lesson in course_lessons.get(session['course_id'],[]):
            if lesson['id'] in linked:
                a=next(a for a in aa if a['lecture_id']==lesson['id'])
                session['_lecture_candidates'][lesson['id']]={k:lesson[k] for k in ['id','lecture_date','title','notes_available','accepted_artifact_id']}|{'link_basis':a['lecture_link_basis']}
        candidates=session.pop('_lecture_candidates')
        session['lecture']=next(iter(candidates.values())) if len(candidates)==1 else None
        related=[a for a in attempts if a['preview_session_id']==session['id']]
        ids={a['id'] for a in related};pending=[p for p in plans if p['attempt_id'] in ids and p['status']!='resolved']
        after=[a for a in related if a['before_after_progress'] and session['lecture'] and a['lecture_id']==session['lecture']['id']]
        session['after_lecture']={'pending':len(pending),'independent_successes':len(after),'progress':bool(after),
            'next_due_at':min((p['due_at'] for p in pending),default=None)}
        classroom=[a for a in attempts if a['activity_type']=='classroom' and session['lecture'] and a['lecture_id']==session['lecture']['id']]
        session['classroom_exercises']=counts(classroom)
        session['after_lecture']['classroom_improvements']=sum(a['classroom_improvement'] for a in classroom)
        session['after_lecture']['notice']='课堂对应题独立正确只能作为改善线索，仍需不同题目的延迟复测。' if any(a['classroom_improvement'] for a in classroom) else '课堂已知正确题不能自动证明课前薄弱已经改善；未知结果和积分不作为掌握证据。'
    return list(sessions.values())


def before_lecture(personal,lecture_id):
    # Historical-schema readers remain supported; this hook never changes learner data.
    with personal.connection() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='learning_capture'").fetchone():return None
        from .store import LearningStore
        attempts=LearningStore.active_attempts(c)
        lesson=c.execute('SELECT * FROM lecture WHERE id=?',(lecture_id,)).fetchone()
        if not lesson:return None
        selected=[a for a in attempts if a['activity_type']=='preview' and a['lecture_id']==lecture_id
            and (not day_of(a['occurred_at']) or day_of(a['occurred_at'])<=lesson['lecture_date'])]
        sessions=[s for s in preview_sessions(c,attempts) if s['lecture'] and s['lecture']['id']==lecture_id]
        if not selected and not sessions:return None
        detail=[]
        for a in sorted(selected,key=lambda a:a['result']!='incorrect')[:5]:
            detail.append({'attempt_id':a['id'],'exercise_id':a['exercise_id'],'topic_id':a['topic_id'],
                'question':a['question'] if len(a['question'])<=500 else a['question'][:499]+'…','my_answer':a['answer'][:200],'result':a['result'],
                'result_quote':a['platform_result_quote'][:200],'submitted_at':a['occurred_at'],
                'raw_item_id':a['source_raw_item_id'],'snapshot_id':a['source_snapshot_id'],
                'lecture_link_basis':a['lecture_link_basis']})
        return {'lecture_id':lecture_id,'counts':counts(selected),'material_exposures':sum(s['material_exposures'] for s in sessions),
            'session_ids':[s['id'] for s in sessions[:10]],'weaknesses':weaknesses(c,selected),'attempts':detail,
            'timing_unknown':sum(not day_of(a['occurred_at']) or day_of(a['occurred_at'])==lesson['lecture_date'] for a in selected),
            'user_confirmed_before':sum(a['before_lecture_confirmed'] and (not day_of(a['occurred_at']) or day_of(a['occurred_at'])==lesson['lecture_date']) for a in selected),
            'notice':'预习材料只说明接触；题目结果是本人已提交记录。次日课次可能是自动推测，不证明教师逐项讲授，也不证明掌握。这里只列部分题目，完整记录可打开原页。'
                +(' 本次预习先于课堂的相对次序由用户确认；平台提交时刻仍以原页为准。' if any(a['before_lecture_confirmed'] for a in selected) else '')}


def lecture_cycle(personal,lecture_id):
    """A bounded read-only projection of the same ledger for review and export."""
    before=before_lecture(personal,lecture_id)
    with personal.connection() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='learning_capture'").fetchone():return None
        from .store import LearningStore
        lesson=c.execute('SELECT * FROM lecture WHERE id=?',(lecture_id,)).fetchone()
        if not lesson:return None
        available=lectures(c,lesson['course_id']);notes=next(l for l in available if l['id']==lecture_id)
        attempts=LearningStore.active_attempts(c)
        classroom=[a for a in attempts if a['activity_type']=='classroom' and a['lecture_id']==lecture_id]
        if not before and not classroom:return None
        preview=[a for a in attempts if a['activity_type']=='preview' and a['lecture_id']==lecture_id]
        weak=weaknesses(c,preview);weak_ids={w['topic_id'] for w in weak}
        coverage=[{**w,'covered_in_notes':related_topic(w['title'],notes),'basis':'reliable_current_note_body'} for w in weak]
        detail=[{'attempt_id':a['id'],'exercise_id':a['exercise_id'],'topic_id':a['topic_id'],'question':a['question'][:500],
            'my_answer':a['answer'][:200],'result':a['result'],'independent':bool(a['independent']),
            'submitted_at':a['occurred_at'],'notice':a['platform_result_notice'][:500],
            'raw_item_id':a['source_raw_item_id'],'snapshot_id':a['source_snapshot_id']}
            for a in sorted(classroom,key=lambda a:(a['topic_id'] not in weak_ids,a['result']=='correct'))[:5]]
        after=[a for a in attempts if a['lecture_id']==lecture_id and a['before_after_progress']]
        improvements=[a for a in classroom if a['classroom_improvement']]
        return {'schema':'eric-learning-cycle-v1','lecture_id':lecture_id,'lecture_date':lesson['lecture_date'],
            'before_lecture':before,'during_lecture':{'counts':counts(classroom),'notes_available':bool(notes['notes_available']),
                'weakness_coverage':coverage,'attempts':detail,'notice':'笔记覆盖不是掌握；课堂积分不代替对错，未公布或冲突结果保留未知。'},
            'after_lecture':{'classroom_improvements':len(improvements),'delayed_independent_successes':len(after),
                'needs_retest':[{'topic_id':w['topic_id'],'title':w['title']} for w in weak],
                'state':'improving' if after or improvements else 'needs_review' if weak else 'evidence_insufficient',
                'notice':'有一次对应的课堂独立正确，仍需延迟复测。' if improvements and not after else '当前没有足够证据认定课前薄弱已改善。' if weak and not after else '只描述已有证据，不认证整章掌握。'}}
