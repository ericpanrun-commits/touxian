"""Local, conservative note routing from independent source/time/context evidence.

The timetable is owner-provided input. It never proves attendance or mastery.
"""
import json,re
from datetime import date,datetime,timedelta
from pathlib import Path
from ..courses import CourseStore, normalized_name
from .rules import SHANGHAI

MATH='course-math-ii'
FAMILIES=[r'极限|趋于|夹逼',r'无穷小|同阶|等价',r'连续|间断点',r'导数|微分|求导',r'积分|原函数']

def stamp(value):
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        return result.astimezone(SHANGHAI) if result.tzinfo else None
    except (ValueError,TypeError,AttributeError):return None

def timetable(path):
    try:
        path=Path(path)
        if path.stat().st_size>1_000_000:return None
        value=json.loads(path.read_text(encoding='utf-8'))
        first=date.fromisoformat(value['term_week_one_monday'])
        if first.weekday()!=0 or value['timezone']!='Asia/Shanghai' or value['term_start_confirmed_by']!='explicit_user_chat':return None
        if not isinstance(value['sessions'],list) or len(value['sessions'])>1000:return None
        return value
    except (OSError,ValueError,KeyError,TypeError):return None

def source_times(personal,raw_id):
    """Creation is currently a pending-PDF proxy, distinct from actual cloud share."""
    with personal.connection() as c:
        rows=c.execute('''SELECT a.payload_json FROM inbox_receipt r JOIN learning_artifact a
            ON a.kind='note_transport_context' AND json_extract(a.payload_json,'$.submission_id')=r.request_id
            WHERE r.raw_item_id=? ORDER BY r.created_at LIMIT 1''',(raw_id,)).fetchall()
    return json.loads(rows[0][0]) if rows else {}


def reconcile_timetable(personal):
    """Startup-only registration from the owner's validated source timetable."""
    settings=timetable(personal.path.with_name('notes-timetable.json'))
    return CourseStore(personal).register_timetable(settings) if settings else {'created':[],'conflicts':[],'course_mappings':{}}


def infer(personal,raw_id,title,text,context,*,known=(),settings=None):
    with personal.connection() as c:
        courses=[dict(r) for r in c.execute('SELECT * FROM course')]
        aliases=[dict(r) for r in c.execute('SELECT * FROM course_alias')]
        sections=[dict(r) for r in c.execute('''SELECT s.title,t.course_id FROM document_section s JOIN textbook t ON t.id=s.textbook_id
            WHERE t.document_id=s.document_id AND t.is_fixture=0''')]
        lessons=[dict(r) for r in c.execute('SELECT id,course_id,lecture_date,title FROM lecture ORDER BY lecture_date DESC LIMIT 40')]
    names={c['id']:c['name'] for c in courses}
    def decision(cid,level,reasons,**extra):
        return {'confidence':level,'course_id':cid if level=='high' else None,
                'recommended_course_id':cid if level=='medium' else None,
                'course_name':names.get(cid),'reasons':reasons,**extra}
    config=settings if settings is not None else timetable(personal.path.with_name('notes-timetable.json'))
    if config:config=CourseStore(personal).timetable(config)
    times=source_times(personal,raw_id)
    shared=stamp(times.get('shared_at'))
    # A local receive after an offline PC resumes is never substituted for share time.
    proxy=stamp(times.get('pending_created_at') or context.get('source_time'))
    original=stamp(context.get('note_created_at')) if context.get('note_created_origin')=='original_note_metadata' else None
    # Cloud receipt is an upload clock. A cached PDF retried much later must
    # retain its earlier (weaker) share proxy, rather than becoming today's class.
    delayed=bool(shared and proxy and abs(shared-proxy)>timedelta(minutes=15))
    at=original or (proxy if delayed else shared or proxy)
    text_key=normalized_name(title+'\n'+text);title_key=normalized_name(title)
    native_titles=[normalized_name(name) for name,cid in (config or {}).get('course_mappings',{}).items() if cid==MATH]
    conflicting_number=re.search(r'(?:高等数学|高数)(iii|iv|i(?!i)|[13一三])',text_key)
    explicit_days=set(re.findall(r'20\d\d-\d\d-\d\d',title+'\n'+text[:160]))
    temporal_allowed=bool(at) and (not explicit_days or explicit_days=={at.date().isoformat()})
    sessions=[];week=None
    if config and at and temporal_allowed:
        first=date.fromisoformat(config['term_week_one_monday']);week=(at.date()-first).days//7+1
        if 1<=week<=24:
            for row in config['sessions']:
                start,end=stamp(row.get('start')),stamp(row.get('end'))
                if not start or not end or start.date()!=end.date() or not timedelta(0)<end-start<=timedelta(hours=6):continue
                if row.get('week')!=(start.date()-first).days//7+1 or row.get('week')!=week:continue
                if start<=at<=end:sessions.append(row)
    mapped=lambda row:(config or {}).get('course_mappings',{}).get(row['title'])
    unique=next(iter(sessions)) if len(sessions)==1 else None
    existing=context.get('course_id')
    if existing and context.get('course_basis') not in {'explicit_course_name','unresolved'}:
        if existing not in names:return decision(None,'low',['课程尚未登记，保留待确认。'],basis='unregistered_course')
        # The trusted course alone is not a class date. Only a unique matching
        # slot and the original/actual share clock may supply that date here.
        day=at.date().isoformat() if unique and mapped(unique)==existing and (original or shared and not delayed) else None
        return decision(existing,'high',['沿用已确认的课程关联。'],basis='existing_link',
            lecture_day=day,source_timestamp=at.isoformat() if at else None)
    owners={l['course_id'] for l in known}
    if len(owners)==1 and owners<=names.keys():
        return decision(next(iter(owners)),'high',['同一原件已关联现有课堂。'],basis='existing_note_document')
    if owners:return decision(None,'low',['同一原件存在冲突课程，保留待确认。'],basis='conflicting_document')
    if conflicting_number and not any(name in text_key for name in native_titles):
        return decision(None,'low',['课程编号与现有高数课程不一致，保留待确认。'],basis='conflicting_course_number')
    foreign_title=bool(config and any(normalized_name(row['title']) in text_key and mapped(row) not in names
        for row in config['sessions'] if len(row.get('title',''))>=4))
    if foreign_title:return decision(None,'low',['笔记标题指向尚未登记的课程，保留待确认。'],basis='foreign_course_title')
    candidates=[]
    for course in courses:
        cid=course['id'];reasons=[];groups=[];content=0;score=0
        course_aliases=[a['normalized_alias'] for a in aliases if a['course_id']==cid]
        hits=[a for a in course_aliases if len(a)>=2 and a in text_key]
        full_title=any(len(a)>=(5 if cid==MATH else 4) and a in title_key for a in hits)
        if hits:
            content=.48 if full_title else .28;reasons.append('笔记标题或内容含课程名称/别名。')
        families=sum(bool(re.search(pattern,text_key)) for pattern in FAMILIES) if cid==MATH else 0
        if families:
            content=max(content,.12 if families==1 else min(.5,.22+.08*families))
            reasons.append(f'内容出现{families}组相互关联的数学主题。')
        labels=[re.sub(r'^第[^章]+章\s*第[^节]+节\s*|^第[^节]+节\s*','',s['title']).replace('的','') for s in sections if s['course_id']==cid]
        book_hits=[label for label in labels if len(label)>=4 and normalized_name(label) in text_key.replace('的','')]
        if book_hits:content=min(.65,content+.16);reasons.append('与已登记教材小节对应。')
        if content:score+=content;groups.append('content')
        temporal_conflict=bool(unique and mapped(unique)!=cid)
        if unique and mapped(unique)==cid:
            weight=.48 if (original or shared and not delayed) else .24
            score+=weight;groups.append('timetable');reasons.append(f'第{week}周该时段只有这门课。')
            if not (original or shared and not delayed):reasons.append('仅有待发送PDF创建时间，时间权重已降低。')
        elif len(sessions)>1:reasons.append('该时段存在课程重叠，未当作唯一时间证据。')
        relevant=[]
        if at:
            for lesson in lessons:
                try:day=date.fromisoformat(lesson['lecture_date'] or '')
                except ValueError:continue
                if lesson['course_id']==cid and timedelta(0)<=at.date()-day<=timedelta(days=14):relevant.append(lesson)
        matching_recent=any(any(re.search(p,text_key) and re.search(p,normalized_name(l['title'])) for p in FAMILIES) for l in relevant) if cid==MATH else False
        if matching_recent and content:
            score+=.18;groups.append('recent_lecture');reasons.append('与近期已有课堂主题连续。')
        if temporal_conflict:
            score=max(0,score-.2);reasons.append('分享时段属于其他课程，已降低置信度。')
        high=score>=.82 and len(groups)>=2 and not temporal_conflict and (families>=2 or full_title)
        candidates.append({'cid':cid,'score':round(score,3),'high':high,'groups':groups,'reasons':reasons})
    candidates.sort(key=lambda row:(-row['score'],row['cid']))
    if not candidates or candidates[0]['score']<.42:
        return decision(None,'low',['课程证据不足，保留原件并待确认。'],basis='insufficient_evidence',teaching_week=week)
    top=candidates[0];margin=top['score']-(candidates[1]['score'] if len(candidates)>1 else 0)
    level='high' if top['high'] and margin>=.2 else 'medium'
    return decision(top['cid'],level,top['reasons'],basis='combined_note_evidence',score=top['score'],
                    evidence_groups=top['groups'],teaching_week=week,source_timestamp=at.isoformat() if at else None,
                    lecture_day=at.date().isoformat() if at and level=='high' and unique and mapped(unique)==top['cid']
                    and (original or shared and not delayed) else None)
