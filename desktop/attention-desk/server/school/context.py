"""Finite evidence frames, with every page and source label assigned here."""
import json
from ..personal_store import PersonalError, encode, now
from .documents import digest
from .models import COURSE
from .profiles import profile_for, source_scope

MAX_SOURCES = 14
MAX_CHARS = 18000


def caption(title, page, printed=None, origin='unknown', section=None, *, image=False):
    parts = [title]
    if section: parts.append(section)
    if printed is not None:
        parts.append(('候选印刷页 ' if origin == 'detected_unconfirmed' else '印刷页 ') + str(printed))
    parts.append('原图' if image else f'PDF 第 {page} 页')
    return ' · '.join(parts)


class ContextBuilder:
    def __init__(self, personal, store, documents, retrieval, mistake_reader=None, *, persist_sources=True):
        self.personal, self.store, self.documents, self.retrieval = personal, store, documents, retrieval
        self.mistake_reader = mistake_reader
        self.persist_sources = persist_sources

    def block_source(self, block, role, *, title=None, section=None):
        return {'source_id': 'src-' + block['id'].removeprefix('block-')[:24], 'document_id': block['document_id'],
                'page': block['page_index'], 'block_id': block['id'], 'source_kind': role, 'kind': 'text',
                'text': block['resolved_text'][:2500], 'content_hash': block['content_hash'],
                'page_hash': block['page_hash'], 'extraction_key': block['extraction_key'],
                'extraction_method': block['extraction_method'], 'quality': 'vision_uncertain' if block['extraction_method'] == 'deepseek_vision' and block.get('vision_status') in {'uncertain','unreadable','failed','unknown'} else block['extraction_quality'],
                'printed_page_label': block['printed_page_label'], 'label_origin': block['label_origin'],
                'uncertainties':json.loads(block['metadata_json']).get('uncertainties',[]),
                'caption': caption(title or block['document_title'], block['page_index'], block['printed_page_label'], block['label_origin'], section,
                    image=(block.get('mime_type') or '').startswith('image/')),
                'section': section, 'image_url': f'/api/personal/school/documents/{block["document_id"]}/pages/{block["page_index"]}/image',
                'original_url': f'/api/personal/attachments/{block["attachment_id"]}/original'}

    def image_source(self, doc, index, role='notes', *, title=None, section=None):
        page = self.documents.page(doc['id'], index)
        bid = 'block-' + digest(page['id'] + ':original-image')[:40]
        if self.persist_sources:
            with self.personal.connection(write=True) as c:
                c.execute('''INSERT OR IGNORE INTO document_block(id,document_id,page_id,page_index,block_order,text,
                    extraction_key,extraction_method,extraction_quality,content_hash,metadata_json,created_at)
                    VALUES (?,?,?,?,0,?,'original-image','page_image','unverified',?,'{}',?)''',
                    (bid, doc['id'], page['id'], index, '原始页面图像；这段标签不是笔记内容。', page['page_hash'], now()))
        att = self.documents.attachment(doc['id'])
        return {'source_id': 'src-' + bid.removeprefix('block-')[:24], 'document_id': doc['id'], 'page': index,
                'block_id': bid, 'source_kind': role, 'kind': 'page_image', 'text': '', 'quality': 'unverified',
                'page_hash': page['page_hash'], 'content_hash': page['page_hash'], 'extraction_key': 'original-image',
                'extraction_method': 'page_image', 'printed_page_label': page['printed_page_label'], 'label_origin': page['label_origin'],
                'caption': caption(title or doc['title'], index, page['printed_page_label'], page['label_origin'],section,
                    image=(att.get('mime_type') or '').startswith('image/')),
                'image_url': f'/api/personal/school/documents/{doc["id"]}/pages/{index}/image',
                'original_url': f'/api/personal/attachments/{att["id"]}/original'}

    def build(self, lecture_id, *, include_recent=False, allow_incomplete=False):
        lecture = self.store.lecture(lecture_id)
        profile = profile_for({'lecture': lecture})
        book = lecture['textbook']
        metadata = {k: lecture[k] for k in ('id', 'course_id', 'lecture_date', 'title', 'sequence_number', 'learning_unit_id', 'version')}
        if not lecture['notes']: raise PersonalError('课次尚未关联笔记。', 'notes_required', 409)
        note_ids = [d['id'] for d in lecture['notes']]
        note_blocks = self.documents.blocks(note_ids)
        # Prefer coherent page transcriptions; then select a small spread across pages.
        selected, seen_pages = [], set()
        for block in sorted(note_blocks, key=lambda b: (-int(b['extraction_method'] == 'deepseek_vision'), -len(b['resolved_text']))):
            page_key = (block['document_id'], block['page_index'])
            if page_key in seen_pages: continue
            seen_pages.add(page_key); selected.append(block)
            if len(selected) >= 4: break
        selected_ids = {b['id'] for b in selected}
        for block in sorted(note_blocks, key=lambda b: -len(b['resolved_text'])):
            if len(selected) >= 4: break
            if block['id'] not in selected_ids: selected.append(block); selected_ids.add(block['id'])
        sources = [self.block_source(b, 'notes') for b in selected]
        needs_vision = []
        for doc in lecture['notes']:
            for page in self.documents.pages(doc['id'], limit=2000):
                page_blocks = [b for b in note_blocks if b['document_id'] == doc['id'] and b['page_index'] == page['page_index']]
                current_vision = any(b['extraction_method'] == 'deepseek_vision' for b in page_blocks)
                if not current_vision and not page.get('vision_status') and (not page_blocks or any(b['extraction_quality'] != 'text_extracted' for b in page_blocks)):
                    needs_vision.append({'document_id': doc['id'], 'page': page['page_index']})
            # At most two original note pages accompany a frame or vision request.
            for page in self.documents.pages(doc['id'], limit=2):
                if len([s for s in sources if s['kind'] == 'page_image']) < 2:
                    sources.append(self.image_source(doc, page['page_index']))
        queries=[lecture['title']]
        # This bounded Math II pilot needs both named limits, their existence
        # criterion and a definition, rather than six mentions of the chapter
        # title. These are textbook evidence, never inferred teaching history.
        if lecture['course_id']==COURSE and '重要极限' in lecture['title']:
            queries+=['夹逼定理','柯西收敛准则','第一个重要极限 sin x / x','第二个重要极限 (1+1/x)^x','函数极限的 ε-δ 定义']
        searches=[self.retrieval.search(q,lecture_id=lecture_id,limit=6) for q in queries]
        found={**searches[0]};picked=[];seen=set()
        for candidates in [[r['items'][0] for r in searches if r['items']],*[r['items'] for r in searches]]:
            for block in candidates:
                if block['id'] in seen:continue
                seen.add(block['id']);picked.append(block)
                if len(picked)==6:break
            if len(picked)==6:break
        found['items']=picked
        textbook_sources = [self.block_source(b, 'textbook', title=b['textbook_title'], section=b['section_title']) for b in found['items']]
        if not profile.requires_textbook:
            sections_by_id = {section['id']: section for section in lecture['sections']}
            for source, block in zip(textbook_sources, found['items']):
                section = sections_by_id[block['section_id']]
                source.update(section_id=section['id'], textbook_id=section['textbook_id'])
        sources.extend(textbook_sources)
        for source in textbook_sources:
            page = self.documents.page(source['document_id'], source['page'])
            item = {'document_id': source['document_id'], 'page': source['page']}
            if source['quality'] == 'ocr_unverified' and not page.get('vision_status') and item not in needs_vision and len(needs_vision) < 4:
                needs_vision.append(item)
        missing = []
        if not book:missing.append('这门课尚未登记教材，课堂笔记和原件已保留。')
        elif not book['document_id']: missing.append('等待指定版本的真实教材 PDF 或章节扫描件。')
        elif not self.retrieval.sections(lecture_id): missing.append('尚未确认教材小节关联。')
        elif not textbook_sources: missing.append(found['reason'])
        if profile.requires_textbook and not allow_incomplete and missing:
            raise PersonalError('；'.join(missing), 'textbook_evidence_required', 409)
        if include_recent:
            with self.personal.connection() as c:
                recent = c.execute('''SELECT n.document_id FROM lecture l JOIN lecture_note n ON n.lecture_id=l.id
                    WHERE l.course_id=? AND l.learning_unit_id IS ? AND l.id!=? AND l.accepted_artifact_id IS NOT NULL
                    AND (l.lecture_date<=? OR l.lecture_date IS NULL) ORDER BY l.lecture_date DESC LIMIT 2''',
                    (lecture['course_id'], lecture['learning_unit_id'], lecture_id, lecture['lecture_date'] or '9999-12-31')).fetchall()
            recent_blocks = self.documents.blocks([r[0] for r in recent])
            sources.extend(self.block_source(b, 'recent_notes') for b in recent_blocks[:2])
        # The hook is read-only; no fabricated Mistake table or learner history.
        mistakes = self.mistake_reader(lecture) if self.mistake_reader else []
        from ..learning.activities import before_lecture,lecture_cycle
        preview=before_lecture(self.personal,lecture_id)
        cycle=lecture_cycle(self.personal,lecture_id)
        bounded, chars, seen = [], 0, set()
        for source in sources:
            if source['source_id'] in seen: continue
            if len(bounded) >= MAX_SOURCES: break
            remaining = MAX_CHARS - chars
            if source['kind'] != 'page_image' and remaining < 120: continue
            value = {**source, 'text': source['text'][:remaining]}
            bounded.append(value); chars += len(value['text']); seen.add(value['source_id'])
        if profile.requires_textbook and not any(s['source_kind'] == 'textbook' for s in bounded) and not allow_incomplete:
            raise PersonalError('本次上下文缺少教材证据，请先调整检索或章节。', 'no_evidence', 409)
        versions = {'lecture': metadata, 'documents': [{'id': d['id'], 'sha256': d['sha256'], 'version': d['version']} for d in lecture['notes']],
                    'textbook': {'id': book['id'], 'version': book['version'], 'document_id': book['document_id'], 'document_version': book['document']['version'] if book['document'] else None, 'is_fixture': bool(book['is_fixture'])} if book else None,
                    'sections': [{'id': s['id'], 'version': s['version']} for s in lecture['sections'] if s['link_status'] == 'confirmed'],
                    'blocks': [{'id': s['block_id'], 'hash': s['content_hash'], 'page_hash': s['page_hash'], 'extraction_key': s['extraction_key'], 'quality': s['quality']} for s in bounded]}
        if not profile.requires_textbook:
            # Available catalogue metadata is not evidence. Bind only books and
            # sections which supplied the bounded frame, including companions.
            used_books = sorted({s['textbook_id'] for s in bounded if s['source_kind'] == 'textbook'})
            used_sections = {s['section_id'] for s in bounded if s['source_kind'] == 'textbook'}
            books = [self.store.textbook(identity) for identity in used_books]
            versions['textbooks'] = [{'id': b['id'], 'version': b['version'], 'document_id': b['document_id'],
                'document_version': b['document']['version'] if b['document'] else None,
                'is_fixture': bool(b['is_fixture'])} for b in books]
            versions['textbook'] = versions['textbooks'][0] if books else None
            versions['sections'] = [s for s in versions['sections'] if s['id'] in used_sections]
            versions['review_profile'] = profile.key
        versions['before_lecture']=preview
        if cycle:versions['learning_cycle']=cycle
        fingerprint = digest(encode({**versions, 'lecture': {k: v for k, v in metadata.items() if k != 'version'}}))
        result = {'context_id': fingerprint, 'lecture': metadata, 'course':lecture['course'], 'sources': bounded, 'input_versions': versions,
                'before_lecture':preview,'learning_cycle':cycle,
                'mistakes': {'available': bool(mistakes), 'items': mistakes[:3], 'message': '' if mistakes else '当前没有足够历史错误数据'},
                'needs_vision': needs_vision[:4], 'unprocessed_note_page_count': len(needs_vision),
                'missing': missing, 'is_fixture': bool(book and book['is_fixture']), 'real_acceptance': book['real_acceptance'] if book else 'pending',
                'budget': {'max_sources': MAX_SOURCES, 'actual_sources': len(bounded), 'max_characters': MAX_CHARS, 'actual_characters': chars,
                           'omitted_note_blocks': max(0, len(note_blocks)-len(selected)), 'max_vision_images': 4},
                'retrieval': {'strategy': found.get('strategy'), 'sections': found.get('scope'), 'result_count': len(textbook_sources)}}
        if not profile.requires_textbook:
            result.update(review_profile=profile.key, source_scope=source_scope(bounded),
                is_fixture=any(b['is_fixture'] for b in versions['textbooks']),
                real_acceptance='not_used' if not books else 'accepted' if all(b['real_acceptance'] == 'accepted' for b in books) else 'pending')
        return result
