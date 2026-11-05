"""Eric Inbox is a projection of RawItem, not a parallel capture/review system.

Image and PDF previews follow human text, then a vision summary, then the
waiting, failed, or low-confidence copy. Unverified OCR stays in ``ocr_preview``.

``InboxStore(personal).project(c, row)`` is the SQLite row projection used by
``list``, ``detail``, and ``link``. The cloud publisher calls
``InboxStore.project_snapshot(snapshot, vision=None, human_text=None)``.
"""
from __future__ import annotations

import hashlib
import json
import re
from uuid import NAMESPACE_URL, uuid5
from .personal_store import PersonalError, encode, now
from .review_store import ReviewStore
from .source_metadata import snapshot_with_metadata

WAITING = '等待 AI 分析…'
FAILED = 'AI 分析失败，可重新处理'
LOW = '部分内容需要确认'
PREVIEW_LIMIT = 240
# SQLite list cards keep the shorter width the local inbox already used.
ROW_PREVIEW_LIMIT = 160
_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'webp', 'heic', 'heif', 'pdf'}


def _text(value) -> str:
    return value if isinstance(value, str) else ''


def _is_image_or_pdf(snapshot: dict) -> bool:
    if snapshot.get('input_kind') in {'image', 'pdf'}:
        return True
    files = list(snapshot.get('attachments') or []) + list(snapshot.get('files') or [])
    for item in files:
        if not isinstance(item, dict):
            continue
        mime = item.get('mime_type') or item.get('mime') or ''
        if mime.startswith('image/') or mime == 'application/pdf':
            return True
        if str(item.get('extension') or '').lower() in _IMAGE_EXT:
            return True
    return False


def _band(vision: dict | None) -> str | None:
    if not isinstance(vision, dict):
        return None
    raw = vision.get('confidence')
    result = vision.get('result') if isinstance(vision.get('result'), dict) else vision.get('vision_analysis')
    if raw in (None, '') and isinstance(result, dict):
        raw = result.get('confidence')
    if raw in {'high', 'medium', 'low'}:
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and 0 <= raw <= 1:
        if raw >= 0.8:
            return 'high'
        if raw >= 0.5:
            return 'medium'
        return 'low'
    return None


def _analysis(vision: dict) -> dict:
    result = vision.get('result')
    if isinstance(result, dict):
        return result
    analysis = vision.get('vision_analysis')
    return analysis if isinstance(analysis, dict) else {}


VERSION = 'inbox-local-v1'
ACTIVE = {'received', 'extracting', 'classifying'}
REVIEW = {'needs_review', 'failed', 'recovery_needed', 'needs_manual_content'}


def classification(bundle):
    """Bounded local suggestions with exact immutable evidence; no cloud request."""
    snapshot = bundle['snapshot']
    text = snapshot['original_text']
    lowered = text.casefold()
    domain, hit = 'general', ''
    for name, words in [('school', ['高数', '高等数学', '课程', '作业', '老师', '考试']),
                        ('ielts', ['ielts', '雅思']), ('engineering', ['cad', '工程', 'solidworks']),
                        ('travel', ['行程', '机票', '酒店'])]:
        hit = next((w for w in words if w in lowered), '')
        if hit:
            domain = name
            break
    kind = 'document' if snapshot.get('attachments') else 'note'
    for name, words in [('assignment', ['作业', 'assignment']), ('exercise', ['习题', '练习题']),
                        ('notice', ['通知', '公告']), ('reference', ['参考', '资料', 'reference'])]:
        if any(w in lowered for w in words):
            kind = name
            break
    action = next((w for w in ['提交', '截止', '请完成', '报名', '考试', 'need to', 'deadline'] if w in lowered), '')
    quotes = list(dict.fromkeys(w for w in (hit, action) if w))
    evidence = []
    for word in quotes:
        match = re.search(re.escape(word), text, re.IGNORECASE)
        if match:
            evidence.append({'snapshot_id': snapshot['id'], 'quote': match[0],
                             'start_offset': match.start(), 'end_offset': match.end()})
    return {'domain': domain, 'kind': kind, 'possible_course': bundle.get('course_suggestions'),
            'possible_action': bool(action), 'suggestion': True, 'provider': 'local', 'model': 'bounded-rules',
            'prompt_version': 'none-local-rules-v1', 'processing_version': VERSION, 'evidence': evidence,
            'usage': {'input_tokens': 0, 'output_tokens': 0, 'images': 0}, 'estimated_cost_cny': 0}


class InboxStore:
    def __init__(self, personal):
        self.personal = personal

    @staticmethod
    def key(owner, request_id):
        return str(uuid5(NAMESPACE_URL, 'eric-inbox:' + owner + ':' + request_id))

    def recover(self):
        # Unknown processing outcomes are visible. No automatic paid replay.
        with self.personal.connection(write=True) as c:
            c.execute("""UPDATE raw_item SET inbox_status='recovery_needed',inbox_error_json=?,
                inbox_updated_at=?,inbox_version=inbox_version+1 WHERE inbox_status IN ('received','extracting','classifying')""",
                (encode({'code': 'interrupted', 'message': '电脑处理已中断；原件保留，可重试本地处理。', 'retryable': True}), now()))

    @staticmethod
    def attach(c, bundle, kind, signature):
        snap = bundle['snapshot']
        c.execute("""UPDATE raw_item SET inbox_kind=?,inbox_status='received',inbox_stage='receive',
            inbox_original_snapshot_id=?,inbox_snapshot_id=?,inbox_fingerprint=?,inbox_updated_at=? WHERE id=?""",
            (kind, snap['id'], snap['id'], signature, now(), snap['raw_item_id']))

    def state(self, raw_id, status, stage, error=None, snapshot_id=None):
        with self.personal.connection(write=True) as c:
            if snapshot_id and not c.execute('SELECT 1 FROM raw_snapshot WHERE id=? AND raw_item_id=?', (snapshot_id, raw_id)).fetchone():
                raise PersonalError('处理结果不属于原始输入。', 'invalid_reference', 409)
            c.execute('''UPDATE raw_item SET inbox_status=?,inbox_stage=?,inbox_error_json=?,
                inbox_snapshot_id=COALESCE(?,inbox_snapshot_id),inbox_updated_at=?,inbox_version=inbox_version+1 WHERE id=?''',
                (status, stage, encode(error) if error else None, snapshot_id, now(), raw_id))

    def classify(self, raw_id, snapshot_id):
        bundle = ReviewStore(self.personal).get(snapshot_id)
        result = classification(bundle)
        files = bundle['snapshot'].get('attachments', [])
        problems = [a for a in files if a['parse_status'] != 'parsed']
        quality = extraction_quality(bundle['snapshot'])
        result['extraction_quality'] = quality
        status = 'needs_review' if result['possible_action'] or problems or result['possible_course'] or quality['needs_review'] else 'completed'
        if problems:
            message = '暂不支持自动解析，原文件已保存。' if all(a['parse_status'] == 'unsupported' for a in problems) else '本地解析未完整完成，原文件保留。'
            error = {'code': 'unsupported' if all(a['parse_status'] == 'unsupported' for a in problems) else 'parse_incomplete',
                     'message': message, 'retryable': any(a['parse_status'] == 'failed' for a in problems)}
        elif quality['needs_review']:
            error = {'code': 'extraction_warning', 'message': '；'.join(quality['reasons']), 'retryable': False}
        else:
            error = None
        with self.personal.connection(write=True) as c:
            c.execute('UPDATE raw_item SET inbox_classification_json=? WHERE id=?', (encode(result), raw_id))
        self.state(raw_id, status, 'classified', error, snapshot_id)

    @staticmethod
    def _rows(c):
        return c.execute('''SELECT i.*,s.id AS current_snapshot_id,s.original_text,r.title_hint,r.status AS review_status,
            src.name AS source_name,src.kind AS source_kind,a.status AS ai_status,a.parsed_output,a.error_json AS ai_error
            FROM raw_item i JOIN source src ON src.id=i.source_id
            JOIN raw_snapshot s ON s.id=COALESCE(i.inbox_snapshot_id,
                (SELECT id FROM raw_snapshot WHERE raw_item_id=i.id ORDER BY created_at DESC,id DESC LIMIT 1))
            LEFT JOIN raw_review r ON r.raw_snapshot_id=s.id LEFT JOIN ai_extraction a ON a.id=r.extraction_id
            ORDER BY i.created_at DESC,i.id''').fetchall()

    @staticmethod
    def project_snapshot(snapshot: dict | None, vision: dict | None = None, human_text: str | None = None, limit: int = PREVIEW_LIMIT) -> dict:
        """Cloud inbox payload. OCR from an image or PDF stays in ``ocr_preview``."""
        snapshot = snapshot or {}
        image = _is_image_or_pdf(snapshot)
        original = _text(snapshot.get('original_text')).replace('\r\n', '\n').strip()
        ocr_preview = original[:limit] if image and original else None
        analysis = _analysis(vision) if isinstance(vision, dict) else {}
        status = vision.get('status') or vision.get('vision_status') if isinstance(vision, dict) else None
        band = _band(vision)
        human = _text(human_text).strip()
        notice = None
        if human:
            preview, source = human[:limit], 'human'
        elif status == 'failed':
            preview, source, notice = FAILED, 'failed', FAILED
        elif status == 'completed' and band == 'low':
            preview, source, notice = LOW, 'low_confidence', LOW
        elif status == 'completed':
            preview = _text(analysis.get('summary') or analysis.get('readable_content') or analysis.get('title') or WAITING)[:limit]
            source = 'vision'
        elif image:
            preview, source, notice = WAITING, 'waiting', WAITING
        else:
            preview, source = (original[:limit] if original else ''), 'text'
        projected = {
            'preview': preview,
            'preview_source': source,
            'vision_notice': notice,
            'ocr_preview': ocr_preview,
        }
        if isinstance(vision, dict) and status in {'pending', 'completed', 'failed'}:
            projected.update({
                'vision_status': status,
                'vision_provider': vision.get('provider') or vision.get('analyzer'),
                'vision_analysis': analysis,
                'vision_confidence': band,
                'vision_processed_at': vision.get('analyzed_at') or vision.get('vision_processed_at'),
                'vision_error': vision.get('error') if status == 'failed' else None,
                'vision_resource_id': vision.get('resource_id'),
                'vision_sha256': vision.get('sha256'),
            })
        return projected

    @staticmethod
    def project(c, row, vision=None, human_text=None):
        r = dict(row)
        sid = r['current_snapshot_id']
        attachments = [dict(a) for a in c.execute('''SELECT a.id,a.filename,a.extension,a.mime_type,a.size,a.parse_status,a.parse_error,a.sha256,a.needs_review
            FROM attachment a JOIN snapshot_attachment sa ON sa.attachment_id=a.id WHERE sa.raw_snapshot_id=?''', (sid,))]
        kind = r['inbox_kind']
        if not kind:
            kind = 'file' if attachments else 'url' if r.get('original_url') else 'text'
            if len(attachments) == 1:
                ext = attachments[0]['extension']
                kind = 'image' if ext in {'jpg', 'jpeg', 'png', 'heic', 'webp'} else 'pdf' if ext == 'pdf' else 'file'
        events = json.loads(r.get('parsed_output') or '{}').get('events', [])
        status = r['inbox_status'] or 'completed'
        stage = r['inbox_stage'] or 'legacy_saved'
        error = json.loads(r['inbox_error_json'] or 'null')
        if r['ai_status'] in {'waiting', 'running'}:
            status, stage = 'analyzing', 'analysis'
        elif r['review_status'] in {'confirmed', 'cancelled'}:
            status, stage = 'completed', 'confirmed' if r['review_status'] == 'confirmed' else 'rejected'
        elif r['ai_status'] == 'failed':
            status, stage, error = 'failed', 'analysis', json.loads(r['ai_error'] or 'null')
        elif events and r['review_status'] == 'pending':
            status = 'needs_review'
        if status == 'completed' and r['review_status'] not in {'confirmed', 'cancelled'} and any(a['needs_review'] for a in attachments):
            status = 'needs_review'
            error = error or {'code': 'extraction_warning', 'message': 'OCR 提取尚未核对，可能有缺失或错误；请对照原件检查。', 'retryable': False}
        if r['kind'] == 'wechat_batch':
            media = c.execute('''SELECT w.status,w.last_error FROM wechat_media_work w JOIN wechat_batch b ON b.id=w.batch_id
                WHERE b.snapshot_id=? AND w.status='recovery_needed' LIMIT 1''', (sid,)).fetchone()
            if media:
                status, stage, error = 'recovery_needed', 'wechat_media', {'code': media['last_error'], 'message': '微信媒体需要人工核对；原件保留。', 'retryable': False}
            elif c.execute('''SELECT 1 FROM wechat_candidate ca JOIN wechat_batch b ON b.id=ca.batch_id
                WHERE b.snapshot_id=? AND ca.status='needs_review' LIMIT 1''', (sid,)).fetchone():
                status = 'needs_review'
            elif r['review_status'] == 'pending':
                # The WeChat queue owns its execution state. A saved batch may
                # not have an extraction yet, and generic startup recovery may
                # mark an unstarted extraction interrupted. Neither is a
                # completed analysis or proof of a failed model request.
                work = c.execute('''SELECT b.status AS batch_status,j.status AS job_status,j.error_code,
                    (SELECT COUNT(*) FROM wechat_llm_attempt a WHERE a.job_id=j.id) AS attempts,
                    EXISTS(SELECT 1 FROM wechat_llm_attempt a WHERE a.job_id=j.id AND
                        (a.outcome_unknown=1 OR a.status IN ('outcome_unknown','timeout','connection_error','storage_or_response_error'))) AS unknown
                    FROM wechat_batch b JOIN raw_review rr ON rr.raw_snapshot_id=b.snapshot_id
                    LEFT JOIN wechat_analysis_job j ON j.batch_id=b.id AND j.extraction_id=rr.extraction_id
                    WHERE b.snapshot_id=? AND b.superseded_by IS NULL LIMIT 1''', (sid,)).fetchone()
                if work:
                    job, batch = work['job_status'], work['batch_status']
                    if job == 'outcome_unknown' or (work['unknown'] and job in {'pending','retry','budget_deferred'}):
                        status, stage, error = 'recovery_needed', 'analysis_result_unknown', {
                            'code': 'outcome_unknown', 'message': '微信分析结果尚未确认；原文保留，请先核对记录。', 'retryable': False}
                    elif job == 'running':
                        status, stage, error = 'analyzing', 'analysis', None
                    elif job in {'pending','retry','budget_deferred'} or (job is None and batch in {'pending','waiting_context'}):
                        status = 'needs_review' if any(a['needs_review'] for a in attachments) else 'received'
                        stage = 'analysis_budget_deferred' if job == 'budget_deferred' else 'analysis_pending'
                        if job == 'budget_deferred':
                            error = {'code': 'budget_deferred', 'message': '微信分析等待预算；原文已保存。', 'retryable': False}
                        elif job == 'pending' and not work['attempts'] and isinstance(error, dict) and error.get('code') == 'interrupted':
                            error = None
        course = c.execute('SELECT id,name FROM course WHERE id=?', (r['inbox_course_id'],)).fetchone()
        if kind in {'image', 'pdf'}:
            card = InboxStore.project_snapshot({'input_kind': kind, 'original_text': r.get('original_text') or ''}, vision, human_text, ROW_PREVIEW_LIMIT)
            preview, preview_source, vision_notice, ocr_preview = card['preview'], card['preview_source'], card['vision_notice'], card['ocr_preview']
        else:
            card = None
            preview, preview_source, vision_notice, ocr_preview = (r['original_text'] or '')[:ROW_PREVIEW_LIMIT], 'text', None, None
        result = {'id': r['id'], 'created_at': r['created_at'], 'source': r['source_name'], 'source_kind': r['source_kind'],
                'input_kind': kind, 'title': r['title_hint'] or (attachments[0]['filename'] if attachments else r['original_text'].splitlines()[0][:200]),
                'preview': preview, 'preview_source': preview_source, 'vision_notice': vision_notice, 'ocr_preview': ocr_preview,
                'processing_status': status, 'processing_stage': stage,
                'original_snapshot_id': r['inbox_original_snapshot_id'] or sid, 'snapshot_id': sid,
                'course': dict(course) if course else None, 'project_ref': r['inbox_project_ref'],
                'classification': json.loads(r['inbox_classification_json'] or 'null'), 'error': error,
                'proposal_count': len(events), 'review_status': r['review_status'], 'version': r['inbox_version'],
                'attachments': attachments, 'managed': r['inbox_kind'] is not None, 'needs_manual': status in REVIEW}
        if card:
            for key, value in card.items():
                if key.startswith('vision_') and key != 'vision_notice':
                    result[key] = value
        return result

    def list(self, status='all', limit=20, offset=0, scope='all'):
        # The existing local collection is small; projection joins all state before filtering.
        # Only the requested bounded page and counts cross the API boundary.
        with self.personal.connection() as c:
            rows = [self.project(c, r) for r in self._rows(c) if scope != 'submitted' or r['inbox_kind']]
            if status == 'processing': rows = [r for r in rows if r['processing_status'] in ACTIVE | {'analyzing'}]
            elif status == 'needs_review': rows = [r for r in rows if r['needs_manual']]
            elif status == 'completed': rows = [r for r in rows if r['processing_status'] == 'completed']
            return {'items': rows[offset:offset+limit], 'total': len(rows), 'offset': offset, 'limit': limit}

    def detail(self, raw_id):
        with self.personal.connection() as c:
            row = next((r for r in self._rows(c) if r['id'] == raw_id), None)
            if not row: raise PersonalError('输入不存在。', 'not_found', 404)
            return self.project_detail(c, row)

    @classmethod
    def project_detail(cls, c, row):
        # The same detail owner serves a single view and a consistent cloud export.
        # Exporting N rows must not repeat the complete N-row source join N times.
        result = cls.project(c, row)
        raw_id = result['id']
        original = c.execute('SELECT * FROM raw_snapshot WHERE id=?', (result['original_snapshot_id'],)).fetchone()
        result['original'] = snapshot_with_metadata(c, original)
        result['review_available'] = c.execute('SELECT 1 FROM raw_review WHERE raw_snapshot_id=?', (result['snapshot_id'],)).fetchone() is not None
        if result['review_available']:
            result['review'] = ReviewStore._bundle(c, result['snapshot_id'])
        else:
            current = c.execute('SELECT * FROM raw_snapshot WHERE id=?', (result['snapshot_id'],)).fetchone()
            # A legacy source may have an original but no candidate-review row.
            # Preserve its readable source without inventing a pending review.
            result['review'] = {'snapshot': snapshot_with_metadata(c, current), 'extraction': None, 'course_suggestions': {}}
        result['extraction_quality'] = extraction_quality(result['review']['snapshot'])
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_capture'").fetchone():
            note = c.execute("""SELECT * FROM learning_capture WHERE raw_item_id=?
                AND json_extract(context_json,'$.note_share')=1 ORDER BY created_at DESC,id DESC LIMIT 1""", (raw_id,)).fetchone()
            if note:
                from .learning.store import capture_projection
                capture = capture_projection(c, note, include_lecture_choices=False)
                result['learning_capture'] = {key: capture[key] for key in ('id', 'status')}
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='document'").fetchone():
            result['school_documents'] = [dict(d) for d in c.execute('SELECT d.* FROM document d JOIN attachment a ON a.id=d.attachment_id WHERE a.raw_item_id=?',(raw_id,))]
        result['receipts'] = [dict(r) for r in c.execute('SELECT id,created_at FROM inbox_receipt WHERE raw_item_id=? ORDER BY created_at', (raw_id,))]
        return result

    def link(self, raw_id, version, course_id, project_ref):
        with self.personal.connection(write=True) as c:
            row = c.execute('SELECT * FROM raw_item WHERE id=?', (raw_id,)).fetchone()
            if not row: raise PersonalError('输入不存在。', 'not_found', 404)
            if row['inbox_version'] != version: raise PersonalError('内容已更新，请重新读取后保存关联。', 'conflict', 409)
            if course_id and not c.execute('SELECT 1 FROM course WHERE id=?', (course_id,)).fetchone():
                raise PersonalError('课程不存在。', 'invalid_reference', 400)
            c.execute('''UPDATE raw_item SET inbox_course_id=?,inbox_project_ref=?,inbox_updated_at=?,
                inbox_version=inbox_version+1 WHERE id=?''', (course_id, project_ref.strip() or None, now(), raw_id))
            if course_id!=row['inbox_course_id']:
                from .learning.note_links import inbox_course_changed
                inbox_course_changed(c,raw_id,course_id,version+1)
        return self.detail(raw_id)


def extraction_quality(snapshot):
    """Reuse parser flags. An OCR result is unverified, never semantic success.

    Windows OCR does not publish numeric confidence. In particular handwriting
    and mathematical formulae must not acquire a made-up quality score.
    """
    files, reasons, ocr_warning_added = [], [], False
    for attachment in snapshot.get('attachments', []):
        metadata = (attachment.get('parse_manifest') or {}).get('metadata', {})
        ocr = metadata.get('ocr') or {}
        parsed = attachment['parse_status'] == 'parsed'
        needs_check = bool(attachment.get('needs_review') or metadata.get('needs_review') or metadata.get('needs_ocr') or ocr)
        if needs_check:
            ocr_warning_added = True
            reason = ('OCR 提取质量偏低，内容可能缺失或有误。' if ocr.get('quality') == 'low' else
                      'OCR 文字尚未核对；Windows OCR 不提供置信度，手写、公式和日期可能有误。')
            if reason not in reasons: reasons.append(reason)
        if attachment['parse_status'] == 'failed':
            reasons.append('部分内容未提取成功；原件保存不受影响。')
        if attachment['parse_status'] == 'unsupported':
            reasons.append('暂不支持自动解析此格式；原件保存成功。')
        files.append({'attachment_id': attachment['id'], 'filename': attachment['filename'], 'saved': bool(attachment.get('downloaded_at')),
                      'pdf_structure': 'readable' if attachment['extension'] == 'pdf' and metadata.get('page_count') else 'unverified' if attachment['extension'] == 'pdf' else 'not_applicable',
                      'page_count': metadata.get('page_count'), 'parse_status': attachment['parse_status'],
                      'content_quality': 'needs_review' if needs_check or not parsed else 'text_extracted',
                      'ocr_quality': ocr.get('quality'), 'confidence': ocr.get('confidence'), 'needs_review': needs_check or not parsed})
    for warning in snapshot.get('document_warnings', []):
        if warning['code'] == 'ocr_unverified' and ocr_warning_added:
            continue
        if warning['code'] not in {'files_pending'} and warning['message'] not in reasons:
            reasons.append(warning['message'])
    return {'files': files, 'needs_review': bool(reasons) or any(f['needs_review'] for f in files), 'reasons': reasons,
            'meaning': '结构与文字提取状态；不代表已理解笔记或验证数学内容。'}
