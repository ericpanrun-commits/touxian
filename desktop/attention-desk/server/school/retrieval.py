"""Small lexical retrieval after Course, document and section filters."""
import math
import re
import unicodedata
from collections import Counter
from ..personal_store import PersonalError
from .models import COURSE,require_course

ALIASES = [
    ('夹逼', '夹挤', 'squeeze'), ('重要极限', 'important limit'), ('极限', 'limit'),
    ('数列', 'sequence'), ('函数', 'function'), ('定义', 'definition'), ('条件', 'condition'),
    ('单调', 'monotone'), ('有界', 'bounded'), ('连续', 'continuous'), ('无穷小', 'infinitesimal'),
    ('无穷大', 'infinity'), ('等价', 'equivalent'), ('代换', 'substitution'), ('例题', 'example'),
    ('证明', 'proof'), ('弧度', 'radian'), ('左右', 'sided'), ('运算', 'arithmetic'),
]


def tokens(text,*,math_aliases=True):
    text = unicodedata.normalize('NFKC', text).casefold()
    values = [v for v in re.findall(r'[a-z]+|\d+(?:\.\d+)?', text) if v not in {'the','is','are','a','an','to','of','in','for','what','where','which','how','and','or'}]
    for part in re.findall(r'[\u4e00-\u9fff]+', text):
        values.extend(part[i:i+2] for i in range(max(1, len(part)-1)))
    for group in ALIASES if math_aliases else []:
        if any(word in text for word in group):
            for word in group: values.extend(re.findall(r'[a-z]+', word))
    if math_aliases and re.search(r'sin\s*[\(（]?\s*x', text): values.append('sine')
    if math_aliases and re.search(r'\(\s*1\s*\+|e\^|自然常数', text): values.append('exponential')
    return Counter(values)


def compact_math(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',text).casefold()).replace('−','-')


def math_fragments(text):
    value=compact_math(re.sub(r'例\s*\d+','',text))
    parts=re.findall(r'[a-z0-9_(){}\[\]+*/^.=\-]+',value)
    return [re.sub(r'^lim','',p).strip('{}') for p in parts if len(p)>=6 and any(op in p for op in ('/','^'))]


class Retrieval:
    def __init__(self, personal, documents): self.personal, self.documents = personal, documents

    def sections(self, lecture_id, *, confirmed=True):
        with self.personal.connection() as c:
            rows = [dict(r) for r in c.execute('''SELECT s.*,t.title AS textbook_title,t.course_id,t.is_fixture
                FROM lecture_section ls JOIN document_section s ON s.id=ls.section_id JOIN textbook t ON t.id=s.textbook_id
                JOIN lecture l ON l.id=ls.lecture_id WHERE ls.lecture_id=? AND l.course_id=t.course_id
                AND s.document_id=t.document_id AND ls.status=?''', (lecture_id, 'confirmed' if confirmed else 'suggested'))]
        return rows

    def search(self, query, *, lecture_id=None, section_ids=None, limit=6,course_id=None):
        if not query.strip() or len(query) > 2000: raise PersonalError('检索词请填写 1–2000 字。')
        if not 1 <= limit <= 12: raise PersonalError('检索结果最多 12 个。')
        if lecture_id:
            with self.personal.connection() as c:
                lecture=c.execute('SELECT course_id FROM lecture WHERE id=?',(lecture_id,)).fetchone()
                if not lecture:raise PersonalError('课堂记录不存在，请重新选择。','not_found',404)
                if course_id is not None and course_id!=lecture['course_id']:
                    raise PersonalError('课程与课堂不一致。','invalid_reference',400)
                course_id=lecture['course_id'];require_course(course_id,c)
            sections = self.sections(lecture_id)
        else:
            if not section_ids: return {'items': [], 'reason': '请先选择当前教材小节。'}
            marks = ','.join('?' for _ in section_ids)
            with self.personal.connection() as c:
                course_id=course_id or COURSE;require_course(course_id,c)
                sections = [dict(r) for r in c.execute('''SELECT s.*,t.title AS textbook_title,t.course_id,t.is_fixture
                    FROM document_section s JOIN textbook t ON t.id=s.textbook_id WHERE s.id IN (''' + marks + ''')
                    AND t.course_id=? AND t.document_id=s.document_id''', [*section_ids, course_id])]
        if section_ids: sections = [s for s in sections if s['id'] in section_ids]
        if not sections: return {'items': [], 'reason': '没有已确认的当前教材章节。'}
        blocks = self.documents.blocks(sorted({s['document_id'] for s in sections}))
        scoped = []
        for block in blocks:
            inside = [s for s in sections if s['document_id'] == block['document_id'] and s['start_page'] <= block['page_index'] <= s['end_page']]
            if inside: scoped.append({**block, 'section_id': inside[0]['id'], 'section_title': inside[0]['title'], 'textbook_title': inside[0]['textbook_title'], 'is_fixture': inside[0]['is_fixture']})
        wanted = tokens(query,math_aliases=course_id==COURSE)
        if not scoped or not wanted: return {'items': [], 'reason': '当前范围没有可检索证据，请先解析章节。'}
        encoded = [tokens(b['resolved_text'],math_aliases=course_id==COURSE) for b in scoped]
        frequency = Counter(t for terms in encoded for t in terms)
        n = len(scoped); average = sum(sum(t.values()) for t in encoded)/max(1, n)
        results = []
        for block, terms in zip(scoped, encoded):
            # Scanned math OCR often produces isolated symbols/half-lines. Keep
            # them as evidence, but do not rank these unverified fragments as
            # stand-alone answers ahead of a readable definition or theorem.
            if block['extraction_quality']=='ocr_unverified' and len(block['resolved_text'].strip())<12:
                continue
            length = sum(terms.values()); score = 0
            for term in wanted:
                tf = terms[term]
                if tf:
                    idf = math.log(1 + (n-frequency[term]+.5)/(frequency[term]+.5))
                    score += idf * tf * 2.2/(tf+1.2*(.25+.75*length/max(1, average)))
            if score > 0:
                if query.casefold() in block['resolved_text'].casefold(): score += 2
                # Retain exact expression structure: (1+1/x)^x and (1+5/x)^x
                # must not be treated as identical bags of x/1/5 symbols.
                compact=compact_math(block['resolved_text'])
                if course_id==COURSE:score += 8*sum(fragment in compact for fragment in math_fragments(query))
                if block['extraction_quality'] in {'text_extracted','visually_checked','vision_unverified'}:
                    score *= 3
                heading=block['resolved_text'][:100]
                if re.search(r'^(?:\d+[.、]\s*)?第[一二]个重要极限[：:]|^定义\s*\d|^.{0,20}定理\s*\d[（(：:]',heading):
                    score *= 1.2
                results.append({**block, 'score': round(score, 6)})
        results.sort(key=lambda b: (-b['score'], b['page_index'], b['block_order']))
        unique, seen = [], set()
        for block in results:
            if block['content_hash'] in seen: continue
            seen.add(block['content_hash']); unique.append(block)
            if len(unique) >= limit: break
        return {'items': unique, 'reason': '' if unique else '当前章节未找到匹配证据；没有扩大到其他课程或整本教材。',
                'scope': {'course_id': course_id, 'sections': [s['id'] for s in sections]}, 'strategy': 'section-filtered-lexical-v1'}

    def suggest(self, lecture):
        with self.personal.connection() as c:
            sections = [dict(r) for r in c.execute('''SELECT s.* FROM document_section s JOIN textbook t ON t.id=s.textbook_id
                WHERE t.course_id=? AND s.document_id=t.document_id''', (lecture['course_id'],))]
        wanted = tokens(lecture['title'],math_aliases=lecture['course_id']==COURSE)
        scored = [(sum(min(v, tokens(s['title'],math_aliases=lecture['course_id']==COURSE)[k]) for k, v in wanted.items()), s) for s in sections]
        return [{'section': s, 'origin': 'local_keyword', 'suggestion': True} for score, s in sorted(scored, key=lambda v: -v[0]) if score > 0][:4]
