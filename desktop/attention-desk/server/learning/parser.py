"""Read submitted result text; never solve or submit a platform question."""
import re
from .models import CapturedPage, CapturedQuestion, PreviewMaterial
from .rules import result_from_quote
from .activities import header


def is_learning(text):
    return bool(re.search(r'学习通|超星|我的答案|本人答案|课堂题|预习|高等数学|高数|ERIC_LEARNING_CAPTURE', text))


def parse(text):
    # Read labels conservatively. A bare correct answer is never a student attempt.
    chunks = re.split(r'(?m)(?=^\s*(?:第[0-9一二三四五六七八九十]+题|\d+[.、．]\s*))', text)
    questions = []
    submitted=set(re.findall(r'提交时间[：:]\s*(\d{4}-\d\d-\d\d[ T]\d\d:\d\d(?::\d\d)?)',text))
    page_time=next(iter(submitted)) if len(submitted)==1 else None
    for chunk in chunks[:31]:
        mine = re.search(r'(?:我的答案|本人答案|已提交答案)[：:][ \t]*([^\n]+)', chunk)
        if not mine: continue
        title = re.search(r'(?:题目|题干)[：:]\s*([^\n]+)', chunk)
        if not title:
            title = re.search(r'(?:第[0-9一二三四五六七八九十]+题|\d+[.、．])\s*(?:[（(][^）)]+[）)])?\s*([^\n]{2,})', chunk)
        if not title: continue
        options = {m[0]: m[1].strip() for m in re.findall(r'(?m)^\s*([A-D])[.、．：:]\s*([^\n]+)', chunk)}
        correct = re.search(r'正确答案[：:][ \t]*([^\n]+)', chunk)
        result_line = next((s for s in chunk.splitlines() if re.match(r'^\s*(?:回答|答题|结果|答对|答错|[✓✔✅✕×❌]\s*$)',s) and result_from_quote(s) != 'unknown'), '')
        explanation = re.search(r'(?:答案解析|解析)[：:][ \t]*([^\n]+)', chunk)
        time = re.search(r'提交时间[：:]\s*(\d{4}-\d\d-\d\d[ T]\d\d:\d\d(?::\d\d)?)', chunk)
        questions.append(CapturedQuestion(question=title[1].strip(), options=options, my_answer=mine[1].strip(),
            correct_answer=correct[1].strip() if correct else '', result=result_from_quote(result_line), result_quote=result_line,
            explanation=explanation[1].strip() if explanation else '', submitted_at=time[1] if time else page_time))
    preview = re.search(r'(?:预习[^\n]{0,80}(?:已完成|完成[：:]\s*是)|(?:已完成|完成[：:]\s*是)[^\n]{0,80}预习)', header(text))
    materials=[]
    for line in header(text).splitlines():
        match=re.match(r'\s*(阅读材料|阅读|视频|课件|预习材料|学习内容|讲义)[：:]\s*(.{1,350})',line)
        if not match:continue
        completed=bool(re.search(r'已完成|已阅读|已学习|已观看|学习完成',match[2])) and not re.search(r'未完成|未阅读|未学习|未观看',match[2])
        title=re.split(r'\s*[·|｜]\s*|\s+(?:已完成|已阅读|已学习|已观看|未完成)',match[2],maxsplit=1)[0].strip()
        kind='video' if match[1]=='视频' else 'slides' if match[1]=='课件' else 'reading' if match[1] in {'阅读材料','阅读','讲义'} else 'other'
        materials.append(PreviewMaterial(title=title or match[1],kind=kind,completed=completed,evidence_quote=line.strip()))
    return CapturedPage(questions=questions, preview_completed_quote=preview[0] if preview else '',preview_materials=materials[:20])
