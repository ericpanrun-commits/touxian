"""Parse displayed, already-submitted records; never solve a platform item."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

KINDS=r'[【\[]\s*(?:单选题|多选题|判断题)'

def question_blocks(lines):
    lines=[str(s).strip() for s in lines if str(s).strip()]
    starts=[i for i,s in enumerate(lines[:-2]) if re.fullmatch(r'\d+[.、．]?',s) and re.search(KINDS,lines[i+1])]
    questions=[]
    for n,start in enumerate(starts):
        block=lines[start:starts[n+1] if n+1<len(starts) else len(lines)]
        def field(label):
            for i,value in enumerate(block):
                if value.rstrip('：:')==label:
                    if i+1<len(block) and block[i+1].rstrip('：:') not in {'我的答案','正确答案','答案解析'}:return block[i+1],i
                    return '',i
            return '',None
        mine,mi=field('我的答案');answer,ai=field('正确答案');explanation,_=field('答案解析')
        if not re.fullmatch(r'[A-D]+|对|错',mine):mine=''
        if not re.fullmatch(r'[A-D]+|对|错',answer):answer=''
        origin='displayed_answer'
        if not answer:
            explicit=set(re.findall(r'故选\s*([A-D]+)',explanation))
            if len(explicit)==1:answer=next(iter(explicit));origin='platform_explanation_explicit_conclusion'
        options={}
        for i,value in enumerate(block[:-1]):
            match=re.fullmatch(r'([A-D])[.、．]',value)
            if match and block[i+1] not in {'img','image','图像','图片'} and not re.fullmatch(r'[A-D][.、．]',block[i+1]):options[match[1]]=block[i+1]
        quote=''
        if answer and origin=='platform_explanation_explicit_conclusion':quote=explanation
        elif answer and mi is not None and ai is not None:quote='\n'.join(block[mi:ai+2])
        q={'number':int(re.match(r'\d+',block[0])[0]),'kind':block[1],'question':block[2],'options':options,'my_answer':mine,
           'correct_answer':answer,'explanation':explanation,'result':'unknown','result_quote':quote,'submitted_at':None,
           'key_origin':origin,'raw_lines':block,'graded_score':None}
        for i,value in enumerate(block[:-2]):
            if value in {'\ue902','\ue901'} and block[i+2]=='分':
                try:q['graded_score']=float(block[i+1]);q['result_quote']='\n'.join(block[i:i+3])
                except ValueError:pass
                break
        questions.append(q)
    return questions

def grade_page(lines,kind):
    clean=[str(s).strip() for s in lines if str(s).strip()]
    joined='\n'.join(clean);questions=question_blocks(clean)
    pending=bool(re.search(r'待教师公布|待公布正确答案|尚未公布|未批阅|待批阅',joined))
    scores=[q['graded_score'] for q in questions]
    score=max((s for s in scores if s is not None),default=None)
    count=re.search(r'题量\s*[:：]\s*(\d+)',joined)
    maximum=re.search(r'满分\s*[:：]\s*(\d+(?:\.\d+)?)',joined)
    grade=None
    if '本次成绩' in clean:
        try:grade=float(clean[clean.index('本次成绩')+1])
        except (ValueError,IndexError):pass
    uniform=bool(kind=='preview' and questions and count and int(count[1])==len(questions) and maximum and score
        and abs(float(maximum[1])/len(questions)-score)<.0001 and all(s in {0,score} for s in scores)
        and grade is not None and abs(sum(scores)-grade)<.0001)
    for q in questions:
        if pending:q['result']='unknown'
        elif uniform:q['result']='correct' if q['graded_score']==score else 'incorrect'
        elif q['correct_answer'] and q['my_answer']:q['result']='correct' if q['my_answer']==q['correct_answer'] else 'incorrect'
        else:
            # Integral points and cohort correctness percentages are never grades.
            explicit=next((line for line in q['raw_lines'] if re.fullmatch(r'(?:回答|答题结果)[：:]?\s*(?:正确|错误)',line)),None)
            if explicit:q['result']='incorrect' if '错误' in explicit else 'correct';q['result_quote']=explicit
    return {'questions':questions,'publication_conflict':pending,'result_origin':'platform_graded_score' if uniform else 'published_answer_comparison',
            'grade':grade,'maximum':float(maximum[1]) if maximum else None}

def is_ended_exercise(labels,day):
    text='\n'.join(labels)
    if any(term in text for term in ['签到','选人','投票','问卷','直播','视频','播放','开始答题','继续答题','提交答案','重新答题']):return False
    ends=re.findall(r'结束时间[：:]\s*(\d\d-\d\d\s+\d\d:\d\d)',text)
    if len(set(ends))!=1 or not ends[0].startswith(day[5:]) or not re.search(r'随堂练习|课堂练习|测验|测试题',text):return False
    try:return datetime.fromisoformat(day[:5]+ends[0]).replace(tzinfo=ZoneInfo('Asia/Shanghai'))<=datetime.now(ZoneInfo('Asia/Shanghai'))
    except ValueError:return False

def submitted_result(labels):
    return any(s.strip().startswith('已提交') for s in labels) and any(s.rstrip('：:')=='我的答案' for s in labels)
