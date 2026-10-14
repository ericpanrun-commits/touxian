"""Local explainable classification. Dates are hints, never confirmed Deadline facts."""
from __future__ import annotations
import hashlib
import json
import os
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path


def load_profile():
    path = Path(os.environ.get('ATTENTION_OFFICIAL_PROFILE') or Path(__file__).with_name('config') / 'official_profile.json')
    return json.loads(path.read_text(encoding='utf-8'))


def text_hash(text):
    normalized = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


DATE = r'(?<!\d)(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})(?:日|号)?(?:\s*(\d{1,2})\s*[:：时]\s*(\d{1,2})(?:分)?)?'


def extract_times(text):
    result = {'deadline_at': None, 'event_start_at': None, 'event_end_at': None, 'time_evidence': []}
    # Only explicit complete dates in a clause with an unambiguous time-role.
    # Multiple competing dates for a role are left null rather than guessed.
    candidates = {k: [] for k in ('deadline_at','event_start_at','event_end_at')}
    for clause in re.split(r'[。；;\n]', text):
        if not clause or len(clause)>800:
            continue
        matches = list(re.finditer(DATE, clause))
        values = []
        for match in matches:
            try:
                y,m,d,h,minute = match.groups()
                value = date(int(y),int(m),int(d)).isoformat()
                if h is not None:
                    value = datetime(int(y),int(m),int(d),int(h),int(minute)).isoformat(timespec='minutes') + '+08:00'
                values.append(value)
            except ValueError:
                continue
        if not values or len(values)!=len(matches):
            continue
        role = 'deadline_at' if re.search(r'截止|截至|最迟', clause) else 'event_start_at' if re.search(r'考试时间|活动时间|选课时间|开始时间|举办时间|报名时间|申请时间|提交时间', clause) else None
        if role:
            if len(values)==1:
                candidates[role].append((values[0],clause.strip()[:240]))
            elif len(values)==2 and re.search(r'至|到|—|~|～', clause[matches[0].end():matches[1].start()]):
                if values[0] <= values[1]:
                    candidates['event_start_at'].append((values[0],clause.strip()[:240]))
                    candidates['deadline_at' if role=='deadline_at' else 'event_end_at'].append((values[1],clause.strip()[:240]))
    for key, entries in candidates.items():
        if len({v for v,_ in entries})==1:
            result[key]=entries[0][0]
            result['time_evidence'].append({'field':key,'quote':entries[0][1],'value':entries[0][0]})
    return result


def classify(article, source, profile=None):
    p = profile or load_profile()
    title, body = article['title'], article['original_text']
    category = None
    # Prefer the headline, then content, then the column's configured purpose.
    for text in (title,):
        for label, words in p['category_keywords'].items():
            if any(word in text for word in words):
                category=label
                break
        if category:
            break
    if not category and source.category in p['category_scores']:
        category=source.category
    if not category:
        for label,words in p['category_keywords'].items():
            if any(word in body[:20000] for word in words):
                category=label;break
    category = category or ('学院通知' if source.source_type=='official_college' else '其他')
    score = p['category_scores'].get(category,25)
    reasons = [category]
    full = title+'\n'+body
    if p['major'] in full:
        score=max(score,98); reasons.append('车辆工程专业')
    if source.department in p['college_aliases'] or any(name in full for name in p['college_aliases']):
        score=max(score,90); reasons.append(p['college'])
    hits=[word for word in p['grade_keywords'] if word in full]
    if hits:
        score=min(100,score+5); reasons.extend(hits[:2])
    if any(word in title for word in p.get('graduate_keywords',[])) and '本科' not in title:
        score=min(score,30); reasons.append('面向其他培养阶段或教育类型')
    if any(word in title for word in p.get('staff_keywords',[])) and '学生' not in title:
        score=min(score,30); reasons.append('主要面向教师')
    if any(word in title for word in p.get('graduating_keywords',[])):
        score=min(score,45); reasons.append('主要面向毕业年级')
    years=re.findall(r'(?<!\d)(20\d{2})\s*级',title)
    if years and str(p.get('entry_year')) not in years:
        score=min(score,45);reasons.append('面向其他入学年级')
    is_news = source.category == '校园新闻' or ('通知' not in title and any(word in title for word in p['news_keywords']))
    if is_news and score<80:
        score=min(score,20)
    importance = 'critical' if any(word in title for word in p['critical_keywords']) else 'high' if not is_news and (score>=90 or any(word in title for word in p['high_keywords'])) else 'low' if is_news else 'normal'
    times=extract_times(body)
    if importance!='critical' and times['deadline_at'] and not is_news:
        importance='high'
        remaining=(date.fromisoformat(times['deadline_at'][:10])-datetime.now().date()).days
        if score>=65 and 0<=remaining<=p.get('critical_deadline_days',2):
            importance='critical'
    summary=' '.join(body.split())[:220]
    return {'category':category,'summary':summary,'importance':importance,'relevance':score,'reasons':list(dict.fromkeys(reasons)),
            'content_hash':text_hash(body),'title_hash':text_hash(title),'rule_version':p['version'],**times}
