"""Block-level quote validation, bounded model context and conflict warnings."""
import json
import re
from ..personal_store import PersonalError

AI_CHAR_BUDGET = 24000
AI_BLOCK_BUDGET = 80


def read_blocks(c, snapshot_id):
    values = []
    for row in c.execute('SELECT b.*,a.filename FROM raw_source_block b LEFT JOIN attachment a ON a.id=b.attachment_id WHERE b.raw_snapshot_id=? ORDER BY b.ordinal', (snapshot_id,)):
        value = dict(row)
        value['bounding_box'] = json.loads(value.pop('bounding_box_json') or 'null')
        value['words'] = json.loads(value.pop('words_json'))
        value['needs_review'] = bool(value['needs_review'])
        values.append(value)
    return values


def span_source(snapshot, evidence):
    block_id = evidence.get('block_id')
    blocks = snapshot.get('blocks', [])
    if blocks:
        source = next((b for b in blocks if b['block_id'] == block_id), None)
        if source is None:
            raise PersonalError('依据必须选择这份原文中存在的来源块。', 'invalid_evidence', 400)
    else:
        if block_id:
            raise PersonalError('原文没有这个附件或来源块。', 'invalid_evidence', 400)
        source = {'text': snapshot['original_text'], 'source_type': 'web' if snapshot.get('web') else 'text', 'attachment_id': None, 'page': None, 'section': None, 'bounding_box': None, 'words': []}
    quote = evidence['quote']
    start = evidence.get('start_offset', evidence.get('start'))
    end = evidence.get('end_offset', evidence.get('end'))
    start = source['text'].find(quote) if start is None else start
    end = start + len(quote) if end is None else end
    if start < 0 or end <= start or end > len(source['text']) or source['text'][start:end] != quote:
        raise PersonalError('引用必须精确对应所选页、段落或 OCR 文本块。', 'invalid_evidence', 400)
    box = source['bounding_box']
    words = [w['box'] for w in source.get('words', []) if w['start'] < end and w['end'] > start]
    if words:
        x, y = min(w['x'] for w in words), min(w['y'] for w in words)
        box = {**words[0], 'x': x, 'y': y, 'width': max(w['x'] + w['width'] for w in words) - x, 'height': max(w['y'] + w['height'] for w in words) - y}
    return {**source, 'block_id': block_id, 'start_offset': start, 'end_offset': end, 'bounding_box': box}


def select_model_blocks(blocks):
    def rank(block):
        text = block['text']
        score = 8 * len(re.findall(r'截止|申报|报名|提交|报送|资格|申请|材料|deadline|submit|eligib', text, re.I))
        score += 12 * len(re.findall(r'\d{1,2}\s*月\s*\d{1,2}\s*[日号]|20\d{2}[-/]\d{1,2}[-/]\d{1,2}', text))
        return score + (100 if block['attachment_id'] is None else 0)
    selected, used = [], 0
    if sum(len(b['text']) for b in blocks) <= AI_CHAR_BUDGET and len(blocks) <= AI_BLOCK_BUDGET:
        chosen = blocks
    else:
        chosen = sorted(blocks, key=lambda b: (rank(b), -b['ordinal']), reverse=True)
    for b in chosen:
        if len(selected) >= AI_BLOCK_BUDGET: break
        if used + len(b['text']) > AI_CHAR_BUDGET: continue
        selected.append({k: b.get(k) for k in ('block_id', 'text', 'source_type', 'page', 'section', 'filename', 'needs_review')})
        used += len(b['text'])
    order = {b['block_id']: b['ordinal'] for b in blocks}
    selected.sort(key=lambda b: order[b['block_id']])
    return {'blocks': selected, 'selected_count': len(selected), 'total_count': len(blocks), 'omitted_count': len(blocks) - len(selected), 'characters': used, 'char_budget': AI_CHAR_BUDGET}


def document_warnings(blocks):
    warnings = []
    ocr = [b for b in blocks if b['needs_review']]
    if ocr:
        warnings.append({'field_path': 'sources', 'code': 'ocr_unverified', 'message': 'OCR 文字可能有误，Windows OCR 未提供置信度。请对照原图逐一确认日期和引用。'})
    budget = select_model_blocks(blocks)
    if budget['omitted_count']:
        warnings.append({'field_path': 'sources', 'code': 'limited_document_analysis', 'message': f'长文按日期、行动和资格线索选择 {budget["selected_count"]}/{budget["total_count"]} 个文本块分析；其余内容仍完整保存在本机，请核对遗漏范围。'})
    dates = []
    for b in blocks:
        for match in re.finditer(r'(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]', b['text']):
            window = b['text'][max(0, match.start() - 45):match.end() + 45]
            if not re.search(r'截止|截至|报送|提交|报名|申报|填报', window): continue
            role = '报名/申报' if re.search(r'报名|申报|申请', window) else '提交材料'
            dates.append((role, match.group(0), (match.group(1), int(match.group(2)), int(match.group(3))), b))
    seen = set()
    for role, phrase, value, body in dates:
        if body['attachment_id'] is not None: continue
        for other_role, other_phrase, other_value, file in dates:
            if file['attachment_id'] is None or role != other_role: continue
            if value[1:] == other_value[1:] and (not value[0] or not other_value[0] or value[0]==other_value[0]): continue
            key = (body['block_id'], file['block_id'], phrase, other_phrase)
            if key in seen or len(seen) >= 20: continue
            seen.add(key)
            warnings.append({'field_path': 'sources', 'code': 'date_conflict', 'message': f'日期存在冲突，需确认：正文 {phrase}；{file.get("filename") or "附件"} {other_phrase}（可能为不同阶段）。',
                             'sources': [{'block_id': body['block_id'], 'date_text': phrase}, {'block_id': file['block_id'], 'date_text': other_phrase}]})
    return warnings
