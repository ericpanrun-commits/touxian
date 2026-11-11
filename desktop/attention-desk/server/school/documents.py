"""Document/Page/Block metadata over the existing original file library."""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
import threading
from types import SimpleNamespace
from pathlib import Path
from ..personal_store import PersonalError, encode, now
from ..documents.parsers import split_text
from ..documents.policy import IMAGE_EXTENSIONS, validate_file
from .models import PROCESS_VERSION


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def content_hash(text):
    return digest(re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text)).strip())


class LearningDocuments:
    def __init__(self, personal, documents):
        self.personal, self.documents, self.files = personal, documents, documents.files
        self.cache = self.files.work / 'school-pages'
        self.cache.mkdir(exist_ok=True)
        self.image_lock = threading.Lock()

    def register(self, attachment_id):
        att = self.files.get(attachment_id)
        path = self.files.path(att)
        if att['extension'] not in {'pdf', *IMAGE_EXTENSIONS}:
            raise PersonalError('课堂笔记请关联 PDF 或图片原件；其他输入仍保留在 Inbox。', 'pdf_required', 400)
        if att['extension'] in IMAGE_EXTENSIONS:
            validate_file(path, att['extension'], att.get('mime_type'))
        if not att['sha256']:
            raise PersonalError('请先完成原件保存。', 'original_required', 409)
        doc_id = 'doc-' + att['sha256'][:32]
        with self.personal.connection(write=True) as c:
            old = c.execute('SELECT * FROM document WHERE sha256=?', (att['sha256'],)).fetchone()
            if old: return dict(old)
            stamp = now()
            c.execute('INSERT INTO document(id,attachment_id,sha256,title,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                      (doc_id, att['id'], att['sha256'], att['filename'], stamp, stamp))
        manifest = json.loads(att['parse_manifest_json'] or 'null')
        if att['extension'] in IMAGE_EXTENSIONS:
            self.catalog(doc_id, {'page_count': 1, 'labels': {}})
        elif manifest and manifest['metadata'].get('page_count'):
            count = manifest['metadata']['page_count']
            self.catalog(doc_id, {'page_count': count, 'labels': {}})
        return self.get(doc_id)

    def get(self, doc_id):
        with self.personal.connection() as c:
            row = c.execute('''SELECT d.*,a.mime_type,a.extension FROM document d
                JOIN attachment a ON a.id=d.attachment_id WHERE d.id=?''', (doc_id,)).fetchone()
            if not row: raise PersonalError('文档不存在。', 'not_found', 404)
            return dict(row)

    def attachment(self, doc_id):
        return self.files.get(self.get(doc_id)['attachment_id'])

    def blank_pages(self, doc_id):
        """Reuse the saved PDF parser's rendered-blank proof, never an OCR miss."""
        doc = self.get(doc_id)
        att = self.files.get(doc['attachment_id'])
        if att['extension'] != 'pdf' or att['sha256'] != doc['sha256']:
            return set()
        manifest = json.loads(att.get('parse_manifest_json') or '{}')
        meta = manifest.get('metadata') or {}
        if meta.get('page_count') != doc['page_count']:
            return set()
        return {p for p in meta.get('blank_pages', [])
                if type(p) is int and 1 <= p <= doc['page_count']}

    @staticmethod
    def page_id(doc_id, index):
        return 'page-' + digest(doc_id + ':' + str(index))[:32]

    def catalog(self, doc_id, result):
        count = result['page_count']
        if not 1 <= count <= 2000: raise PersonalError('教材页数需在 1–2000 页内。', 'page_limit', 400)
        with self.personal.connection(write=True) as c:
            doc = c.execute('SELECT * FROM document WHERE id=?', (doc_id,)).fetchone()
            if not doc: raise PersonalError('文档不存在。', 'not_found', 404)
            stamp = now()
            for index in range(1, count + 1):
                label = result.get('labels', {}).get(str(index))
                # File hash plus physical PDF index identifies an immutable page;
                # render_sha256 separately records actual rendered pixel bytes.
                c.execute('''INSERT OR IGNORE INTO document_page
                    (id,document_id,page_index,printed_page_label,label_origin,page_hash,updated_at)
                    VALUES (?,?,?,?,?,?,?)''',
                    (self.page_id(doc_id, index), doc_id, index, label, 'pdf_label' if label is not None else 'unknown',
                     digest(doc['sha256'] + ':' + str(index)), stamp))
            c.execute("UPDATE document SET page_count=?,status='ready',error_json=NULL,updated_at=? WHERE id=?", (count, stamp, doc_id))
            self._reuse_original_blocks(c, doc)

    @staticmethod
    def _reuse_original_blocks(c, doc):
        latest = c.execute('''SELECT b.raw_snapshot_id FROM raw_source_block b JOIN raw_snapshot s ON s.id=b.raw_snapshot_id
            WHERE b.attachment_id=? ORDER BY s.created_at DESC,s.id DESC LIMIT 1''', (doc['attachment_id'],)).fetchone()
        if not latest: return
        rows = c.execute('SELECT * FROM raw_source_block WHERE raw_snapshot_id=? AND attachment_id=? ORDER BY ordinal', (latest[0], doc['attachment_id'])).fetchall()
        grouped = {}
        for row in rows: grouped.setdefault(row['page'] or 1, []).append(row)
        for index, values in grouped.items():
            page = c.execute('SELECT * FROM document_page WHERE document_id=? AND page_index=?', (doc['id'], index)).fetchone()
            if not page or page['current_extraction_key']: continue
            key = 'existing-' + latest[0]
            quality = 'ocr_unverified' if any(v['needs_review'] for v in values) else 'text_extracted'
            for order, value in enumerate(values):
                block_id = 'block-' + digest(doc['id'] + ':' + str(index) + ':' + key + ':' + value['block_id'])[:40]
                c.execute('''INSERT OR IGNORE INTO document_block
                    (id,document_id,page_id,page_index,block_order,raw_snapshot_id,raw_block_id,extraction_key,
                     extraction_method,extraction_quality,content_hash,metadata_json,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (block_id, doc['id'], page['id'], index, order, latest[0], value['block_id'], key,
                     value['source_type'], quality, content_hash(value['text']),
                     encode({'processing_version': PROCESS_VERSION, 'reused_existing_extraction': True}), now()))
            c.execute('UPDATE document_page SET current_extraction_key=?,status=?,updated_at=? WHERE id=?',
                      (key, 'needs_review' if quality == 'ocr_unverified' else 'indexed', now(), page['id']))

    def page(self, doc_id, index):
        with self.personal.connection() as c:
            row = c.execute('SELECT * FROM document_page WHERE document_id=? AND page_index=?', (doc_id, index)).fetchone()
            if not row: raise PersonalError('文档没有这一页。', 'invalid_page', 404)
            return dict(row)

    def pages(self, doc_id, start=1, limit=40):
        with self.personal.connection() as c:
            return [dict(r) for r in c.execute('SELECT * FROM document_page WHERE document_id=? AND page_index>=? ORDER BY page_index LIMIT ?', (doc_id, start, limit))]

    def store_page(self, doc_id, index, result, method=None, quality=None, metadata=None):
        page = self.page(doc_id, index)
        text = result.get('extracted_text', '')
        meta = {**result.get('metadata', {}), **(metadata or {})}
        method = method or meta.get('extractor', 'unknown')
        quality = quality or meta.get('quality', 'ocr_unverified' if meta.get('needs_review') else 'text_extracted')
        if not text.strip():
            raise PersonalError('本页没有可靠的提取文字，原页保留。', 'empty_page_text', 409)
        values = result.get('blocks') or split_text(text, 'pdf', page=index)
        key = digest(encode({'method': method, 'version': PROCESS_VERSION, 'text': text, 'page_hash': page['page_hash'], 'attempt': meta.get('attempt_id'),
                             'quality':quality,'block_quality':[b.get('extraction_quality',quality) for b in values]}))
        with self.personal.connection(write=True) as c:
            for order, block in enumerate(values):
                if not block['text'].strip(): continue
                bid = 'block-' + digest(page['id'] + ':' + key + ':' + str(order))[:40]
                c.execute('''INSERT OR IGNORE INTO document_block
                    (id,document_id,page_id,page_index,block_order,text,extraction_key,extraction_method,
                     extraction_quality,content_hash,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (bid, doc_id, page['id'], index, order, block['text'], key, method, block.get('extraction_quality',quality),
                     content_hash(block['text']), encode({**meta, 'processing_version': PROCESS_VERSION}), now()))
            label = meta.get('printed_page_label')
            if label and page['label_origin'] == 'unknown':
                c.execute("UPDATE document_page SET printed_page_label=?,label_origin='detected_unconfirmed' WHERE id=?", (str(label)[:20], page['id']))
            changed = c.execute('''UPDATE document_page SET current_extraction_key=?,status=?,error_json=NULL,version=version+1,
                updated_at=? WHERE id=? AND (current_extraction_key IS NULL OR current_extraction_key!=?)''',
                (key, 'indexed' if quality == 'text_extracted' else 'needs_review', now(), page['id'], key)).rowcount
            if changed:
                c.execute('UPDATE document SET version=version+1,updated_at=? WHERE id=?', (now(), doc_id))
        return self.page(doc_id, index)

    def page_failed(self, doc_id, index, code, message):
        with self.personal.connection(write=True) as c:
            c.execute("UPDATE document_page SET status='failed',error_json=?,updated_at=? WHERE document_id=? AND page_index=?",
                      (encode({'code': code, 'message': message}), now(), doc_id, index))

    def blocks(self, doc_ids, *, current=True):
        if not doc_ids: return []
        marks = ','.join('?' for _ in doc_ids)
        with self.personal.connection() as c:
            return [dict(r) for r in c.execute('''SELECT b.*,COALESCE(b.text,r.text) AS resolved_text,
                p.printed_page_label,p.label_origin,p.page_hash,p.render_sha256,p.vision_status,d.title AS document_title,
                d.attachment_id,d.sha256 AS document_sha256,a.mime_type,a.extension
                FROM document_block b JOIN document_page p ON p.id=b.page_id JOIN document d ON d.id=b.document_id
                JOIN attachment a ON a.id=d.attachment_id
                LEFT JOIN raw_source_block r ON r.raw_snapshot_id=b.raw_snapshot_id AND r.block_id=b.raw_block_id
                WHERE b.document_id IN (''' + marks + ')' + (' AND p.current_extraction_key=b.extraction_key' if current else '') + ' ORDER BY b.document_id,b.page_index,b.block_order', doc_ids)]

    def image_path(self, doc_id, index):
        with self.image_lock:
            return self._image_path(doc_id, index)

    def _image_path(self, doc_id, index):
        doc, page = self.get(doc_id), self.page(doc_id, index)
        target = self.cache / f'{doc["sha256"]}-page-{index}.png'
        if not target.exists():
            original = self.files.path(self.attachment(doc_id))
            pending = self.cache / (self.page_id(doc_id, index) + '.pending.png')
            self.documents.worker(original, f'school-render:{index}', pending, 45)
            from PIL import Image
            with Image.open(pending) as im: im.verify()
            if not target.exists(): pending.replace(target)
            else: pending.unlink(missing_ok=True)
        rendered = hashlib.sha256(target.read_bytes()).hexdigest()
        if page['render_sha256'] != rendered:
            with self.personal.connection(write=True) as c:
                c.execute('UPDATE document_page SET render_sha256=? WHERE id=?', (rendered, page['id']))
        return target


class ReadDocuments(LearningDocuments):
    """Metadata-only adapter; never opens originals, creates caches or recovers jobs."""
    def __init__(self, personal):
        self.personal=personal
        def attachment(identity):
            with personal.connection() as c:
                row=c.execute('SELECT * FROM attachment WHERE id=?',(identity,)).fetchone()
                return dict(row) if row else None
        self.files=SimpleNamespace(get=attachment)
