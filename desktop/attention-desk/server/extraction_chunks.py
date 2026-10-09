"""Deterministic, lossless chunk plans. Stored source blocks remain authoritative."""
import hashlib
import re
from .personal_store import encode
from .documents.text_boundaries import semantic_ranges

LONG_PROMPT = 'notice-chunks-1'
LONG_SCHEMA = 'consolidated-1'
SHORT_CHARS = 6000
CHUNK_CHARS = 1800
MAX_CHUNKS = 32
AUTO_CHUNKS = 8
AUTO_CALLS = 12
MAX_CALLS = 48
AUTO_TOKENS = 240000
MAX_TOKENS = 1000000
SIGNALS = re.compile(r'截止|截至|报名|提交|报送|材料|时间|地点|要求|资格|附件|联系人|延期|延长|调整|取消|申请|考核|deadline|submit|eligib', re.I)


def hash_value(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def is_long(snapshot):
    blocks = snapshot.get('blocks') or []
    return len(blocks) > 80 or (sum(len(b['text']) for b in blocks) if blocks else len(snapshot['original_text'])) > SHORT_CHARS or len({(b.get('attachment_id'), b.get('page')) for b in blocks if b.get('page')}) > 3


def source_blocks(snapshot):
    return snapshot.get('blocks') or [{'block_id': 'WEB_BODY_001' if snapshot.get('web') else 'TEXT_BODY_001', 'text': snapshot['original_text'],
        'source_type': 'web' if snapshot.get('web') else 'text', 'page': None, 'section': '正文', 'attachment_id': None,
        'filename': None, 'ordinal': 0, 'bounding_box': None, 'words': [], 'needs_review': False}]


def identity(block):
    # Attachment UUIDs differ across versions; page/section/content determine reuse.
    return {k: block.get(k) for k in ('source_type', 'page', 'section', 'filename', 'needs_review')}


def make_chunk(blocks, refs, *, parent=None, depth=0, context=True):
    views = []
    by_id = {b['block_id']: b for b in blocks}
    ranges = {r['block_id']: (r['start'], r['end']) for r in refs}
    # Adjacent source blocks carry limited context, without crossing attachments.
    if context:
        for ref in (refs[0], refs[-1]):
            b = by_id[ref['block_id']]
            i = next(i for i, value in enumerate(blocks) if value['block_id'] == b['block_id'])
            for neighbour in (i-1, i+1):
                if 0 <= neighbour < len(blocks):
                    n = blocks[neighbour]
                    if n.get('attachment_id') != b.get('attachment_id') or n['block_id'] in ranges:
                        continue
                    pieces = list(semantic_ranges(n['text'], 350))
                    ranges[n['block_id']] = pieces[-1] if neighbour < i else pieces[0]
        for key, (start, end) in list(ranges.items()):
            b = by_id[key]
            primary = next((r for r in refs if r['block_id'] == key), None)
            if primary:
                # Overlap inside a split block also remains in original coordinates.
                pieces = list(semantic_ranges(b['text'], 350))
                left = [a for a, z in pieces if z <= start]
                right = [z for a, z in pieces if a >= end]
                ranges[key] = (left[-1] if left else start, right[0] if right else end)
    for b in blocks:
        if b['block_id'] in ranges:
            start, end = ranges[b['block_id']]
            views.append({'block': b, 'start': start, 'end': end})
    canonical = [{**identity(v['block']), 'text': v['block']['text'][v['start']:v['end']], 'start': v['start']} for v in views]
    digest = hash_value(canonical)
    first = by_id[refs[0]['block_id']]
    prefix = first['source_type'].upper() + (f'_PAGE_{first["page"]:03d}' if first.get('page') else '_BODY')
    return {'id': prefix + '_' + digest[:16], 'hash': digest, 'refs': refs, 'views': views,
            'parent': parent, 'depth': depth, 'characters': sum(v['end']-v['start'] for v in views)}


def plan_chunks(snapshot, scope='auto'):
    blocks = source_blocks(snapshot)
    chunks = []
    # A page or DOCX paragraph/table row owns its chunks, so unrelated page changes
    # do not shift every subsequent chunk. Small adjacent rows share a bounded group.
    group, used, owner = [], 0, None
    def flush():
        nonlocal group, used
        if group:
            chunks.append(make_chunk(blocks, group)); group, used = [], 0
    for b in blocks:
        parts = list(semantic_ranges(b['text'], CHUNK_CHARS))
        current = (b.get('attachment_id'), b.get('page'), b.get('section') if b['source_type'] == 'docx' else None)
        for start, end in parts:
            if group and (owner != current or used + end-start > CHUNK_CHARS or len(parts) > 1):
                flush()
            group.append({'block_id': b['block_id'], 'start': start, 'end': end})
            used += end-start; owner = current
            if len(parts) > 1:
                flush()
    flush()
    chosen = set(range(len(chunks)))
    if scope == 'auto' and sum(len(b['text']) for b in blocks) > 24000:
        hits = [i for i, ch in enumerate(chunks) if any(SIGNALS.search(next(b['text'] for b in blocks if b['block_id']==r['block_id'])[r['start']:r['end']]) for r in ch['refs'])]
        if hits:
            chosen = {0} | {j for i in hits for j in (i-1, i, i+1) if 0 <= j < len(chunks)}
    return {'blocks': blocks, 'all': chunks, 'selected': [ch for i, ch in enumerate(chunks) if i in chosen],
            'omitted': [ch['id'] for i, ch in enumerate(chunks) if i not in chosen], 'scope': scope}


def chunk_snapshot(snapshot, chunk):
    blocks = [{**v['block'], 'text': v['block']['text'][v['start']:v['end']]} for v in chunk['views']]
    return {**snapshot, 'blocks': blocks, 'original_text': '\n'.join(b['text'] for b in blocks), '_chunk': True,
            '_chunk_context': {'chunk_id': chunk['id'], 'partial_document': True}}


def split_chunk(blocks, chunk):
    if chunk['depth'] >= 2:
        return []
    total = sum(r['end']-r['start'] for r in chunk['refs'])
    if total <= 450:
        return []
    refs = []
    by_id = {b['block_id']: b for b in blocks}
    for r in chunk['refs']:
        text = by_id[r['block_id']]['text'][r['start']:r['end']]
        for a, z in semantic_ranges(text, max(225, total//2)):
            refs.append({'block_id': r['block_id'], 'start': r['start']+a, 'end': r['start']+z})
    if len(refs) == 1:
        return []
    return [make_chunk(blocks, [r], parent=chunk['id'], depth=chunk['depth']+1, context=False) for r in refs]
