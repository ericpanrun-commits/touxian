"""Store-first intake shared by the LAN Collector and desktop Eric Inbox."""
from __future__ import annotations

import asyncio
import hashlib
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID
from ..extraction_models import RawInput
from ..personal_store import encode, now, PersonalError
from ..review_store import ReviewStore
from ..inbox_store import InboxStore
from .public_url import PublicURLFetcher
from .security import CollectorError
from .safe_logs import record


class SubmissionService:
    def __init__(self, personal, documents, extraction, *, fetcher=None, clock=time.monotonic):
        self.personal, self.documents, self.extraction = personal, documents, extraction
        self.reviews, self.inbox = ReviewStore(personal), InboxStore(personal)
        self.fetcher, self.clock = fetcher or PublicURLFetcher(), clock
        self.loop, self.lock = asyncio.get_running_loop(), asyncio.Lock()
        self.tasks, self.receipts, self.requests = {}, {}, {}
        self.inbox.recover()

    async def bridge(self, coroutine):
        if asyncio.get_running_loop() is self.loop:
            future = asyncio.create_task(coroutine)
        else:
            future = asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coroutine, self.loop))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Intake handlers own temporary uploads until this save has finished.
            # Disconnect may lose the response, never cancel the commit or delete
            # a temporary file underneath its saving thread.
            await asyncio.shield(future)
            raise

    @staticmethod
    def fingerprints(kind, payload, files, confirmed_course_id=None):
        manifest = []
        for name, mime, path in files:
            with path.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            manifest.append((name, mime, digest))
        content = {'kind': 'file' if files else kind, 'body': payload.get('text', ''),
                   'url': payload.get('url', ''), 'files': sorted(r[2] for r in manifest)}
        if confirmed_course_id is not None:content.update(owner_selected_course=confirmed_course_id,purpose=payload.get('purpose','capture'))
        content_hash = hashlib.sha256(encode(content).encode()).hexdigest()
        request = {'kind': kind, 'payload': {k: v for k, v in payload.items() if k not in {'submission_id', 'again', 'confirm_large'}}, 'files': manifest}
        if confirmed_course_id is not None:request['owner_selected_course']=confirmed_course_id
        return hashlib.sha256(encode(request).encode()).hexdigest(), content_hash

    @staticmethod
    def input_kind(kind, files):
        if not files: return kind
        if len(files) != 1: return 'file'
        ext = files[0][0].rsplit('.', 1)[-1].lower()
        return 'image' if ext in {'png', 'jpg', 'jpeg', 'heic', 'webp'} else 'pdf' if ext == 'pdf' else 'file'

    def _save(self, owner, request_id, kind, payload, files, signature, content_hash,confirmed_course_id=None):
        key = self.inbox.key(owner, request_id)
        with self.personal.connection(write=True) as c:
            old = c.execute('SELECT * FROM inbox_receipt WHERE owner=? AND request_id=?', (owner, request_id)).fetchone()
            if old:
                if old['signature'] != signature:
                    raise CollectorError('submission_changed', '本次发送内容已改变，请开始一次新提交。', 409)
                return dict(old), True, False
            existing = c.execute('SELECT id FROM raw_item WHERE inbox_fingerprint=?', (content_hash,)).fetchone()
            if confirmed_course_id is not None and not c.execute('SELECT 1 FROM course WHERE id=?',(confirmed_course_id,)).fetchone():
                raise PersonalError('所选课程尚未登记，原件保留，请核对课程。','invalid_course',409)
            if existing:
                raw_id, fresh = existing['id'], False
            else:
                mobile = owner != 'desktop'
                source = {'source_id': 'mobile' if mobile else 'eric_inbox', 'source_kind': 'mobile' if mobile else 'manual_inbox',
                          'source_name': '来自手机' if mobile else 'Eric Inbox', 'raw_kind': 'inbox_textbook' if payload.get('purpose') == 'textbook' else ('mobile_' if mobile else 'inbox_') + kind}
                if payload.get('purpose')=='lecture_note':source['raw_kind']='inbox_lecture_note'
                title = payload.get('source_name') or (payload.get('text', '').strip().splitlines()[0][:200] if payload.get('text', '').strip() else files[0][0] if files else payload.get('url', 'Eric Inbox')[:200])
                stamp = payload.get('source_time') or None
                if files:
                    from ..documents.policy import MAX_BYTES
                    from ..school.boundary import TEXTBOOK_MAX_BYTES
                    bundle = self.documents.files.collect(files, payload.get('text', ''), title, stamp, key, connection=c, max_bytes=TEXTBOOK_MAX_BYTES if payload.get('purpose') == 'textbook' else MAX_BYTES, **source)
                else:
                    body = payload['url'] if kind == 'url' else payload['text']
                    bundle = self.reviews.collect(RawInput(original_text=body, title_hint=title, source_time=stamp), key, connection=c, **source)
                raw_id, fresh = bundle['snapshot']['raw_item_id'], True
                self.inbox.attach(c, bundle, self.input_kind(kind, files), content_hash)
                if kind == 'url':
                    c.execute('UPDATE raw_item SET original_url=?,title=?,discovered_at=? WHERE id=?', (payload['url'], title, now(), raw_id))
            if confirmed_course_id is not None:
                current=c.execute('SELECT inbox_course_id FROM raw_item WHERE id=?',(raw_id,)).fetchone()[0]
                if current is not None and current!=confirmed_course_id:
                    raise PersonalError('同份原件的课程已有更改，请核对一次已有记录；本次云端原件仍保留。','course_link_conflict',409)
                c.execute('UPDATE raw_item SET inbox_course_id=? WHERE id=?',(confirmed_course_id,raw_id))
            stamp = now()
            c.execute('INSERT INTO inbox_receipt VALUES (?,?,?,?,?,?)', (key, owner, request_id, signature, raw_id, stamp))
            return {'id': key, 'owner': owner, 'raw_item_id': raw_id, 'created_at': stamp}, not fresh, fresh

    async def submit(self, owner, kind, payload, files=(),*,confirmed_course_id=None):
        if confirmed_course_id is not None and not owner.startswith('cloud:'):
            raise CollectorError('scope_denied','笔记分享连接不能替代账号中的课程选择。',403)
        try: request_id = str(UUID(payload['submission_id']))
        except (KeyError, ValueError, TypeError): raise CollectorError('invalid_submission', '发送标识无效，请重新打开收集页。')
        if kind not in {'text', 'url', 'file'}: raise CollectorError('invalid_input', '请选择文字、链接或文件。')
        if kind == 'url':
            try:
                u = urlsplit(payload['url'])
                if u.scheme not in {'http', 'https'} or not u.hostname or u.username or u.password or len(payload['url']) > 4096: raise ValueError()
            except ValueError: raise CollectorError('invalid_url', '请输入不含账号密码的完整 HTTP / HTTPS 链接。')
        signature, content_hash = await asyncio.to_thread(self.fingerprints, kind, payload, files,confirmed_course_id)
        async with self.lock:
            if len(self.tasks) >= 8: raise CollectorError('busy', '电脑正在整理多条输入，请稍后再发送。', 429)
            try:
                receipt, duplicate, fresh = await asyncio.to_thread(self._save, owner, request_id, kind, payload, files, signature, content_hash,confirmed_course_id)
            except (CollectorError, PersonalError): raise
            except Exception as error:
                record(self.personal, 'save', error)
                raise CollectorError('save_failed', '尚未确认保存到电脑，请保留输入并以同一发送标识重试。', 503)
            # Diagnostics cache only; persisted rows are the receipt authority.
            self.receipts[receipt['id']] = SimpleNamespace(owner=owner, created=self.clock())
            self.requests[(owner, request_id)] = receipt['id']
            if len(self.receipts) > 512:
                self.receipts.pop(next(iter(self.receipts)))
                self.requests = {k: v for k, v in self.requests.items() if v in self.receipts}
            result = await self.status(owner, receipt['id'])
            result['duplicate'] = duplicate
            if fresh: self._schedule(receipt['raw_item_id'])
            return result

    def _schedule(self, raw_id):
        if raw_id in self.tasks: return
        task = asyncio.create_task(self._finish(raw_id))
        self.tasks[raw_id] = task
        def finished(done):
            self.tasks.pop(raw_id, None)
            if not done.cancelled():
                error=done.exception()
                if error:record(self.personal,'process_status_write',error,job_id=raw_id,entity='raw_item')
        task.add_done_callback(finished)

    async def resume_notes(self):
        # Resume local work through the same pipeline after a committed receive.
        # Paid review calls retain the existing one-shot/reservation guards.
        def pending():
            with self.personal.connection() as c:
                return [r[0] for r in c.execute("SELECT id FROM raw_item WHERE kind='inbox_lecture_note' AND inbox_status IN ('received','extracting','classifying','recovery_needed') ORDER BY created_at LIMIT 8")]
        for raw_id in await asyncio.to_thread(pending):
            if len(self.tasks)>=8:break
            self._schedule(raw_id)

    def _save_page(self, snapshot_id, original_url, page):
        with self.personal.connection(write=True) as c:
            original = c.execute('SELECT * FROM raw_snapshot WHERE id=?', (snapshot_id,)).fetchone()
            stamp = now()
            new_id = ReviewStore._new_snapshot(c, original['raw_item_id'], page['text'], stamp, original['source_time'], page['title'])
            version_hash = hashlib.sha256(encode(page).encode()).hexdigest()
            source_id = c.execute('SELECT source_id FROM raw_item WHERE id=?', (original['raw_item_id'],)).fetchone()[0]
            c.execute('INSERT INTO raw_snapshot_web VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (new_id, source_id, page['title'], original_url, page['url'], page['published_at'], stamp, '[]', '[]', page['body_html'], 'text', version_hash, 1))
            c.execute('UPDATE raw_item SET canonical_url=?,title=?,published_at=?,last_fetch_attempt_at=?,fetch_error=? WHERE id=?',
                      (page['url'], page['title'], page['published_at'], stamp, '', original['raw_item_id']))
            return new_id

    async def _finish(self, raw_id):
        stage = 'local_processing'
        try:
            with self.personal.connection() as c: row = dict(c.execute('SELECT * FROM raw_item WHERE id=?', (raw_id,)).fetchone())
            sid = row['inbox_original_snapshot_id']
            bundle = await asyncio.to_thread(self.reviews.get, sid)
            if row['kind'] == 'inbox_textbook':
                self.inbox.state(raw_id, 'needs_review', 'textbook_saved', {'code': 'textbook_chapter_required', 'message': '教材原件已保存，请在教材页面登记这份资料并选择当前章节。', 'retryable': False})
                return
            if row['inbox_kind'] == 'url':
                stage = 'fetch'
                self.inbox.state(raw_id, 'extracting', stage)
                try:
                    page = await asyncio.to_thread(self.fetcher.fetch, row['original_url'])
                    sid = await asyncio.to_thread(self._save_page, sid, row['original_url'], page)
                except Exception as error:
                    code = error.code if isinstance(error, CollectorError) else 'fetch_failed'
                    with self.personal.connection(write=True) as c:
                        c.execute('UPDATE raw_item SET fetch_error=?,last_fetch_attempt_at=? WHERE id=?', (code, now(), raw_id))
                    self.inbox.state(raw_id, 'needs_manual_content', stage, {'code': code, 'message': '链接已保存，正文无法自动获取，可粘贴正文或上传截图。', 'retryable': True})
                    return
            elif bundle['snapshot'].get('attachments'):
                stage = 'extract'
                self.inbox.state(raw_id, 'extracting', stage)
                ids = [a['id'] for a in bundle['snapshot']['attachments']]
                job = await asyncio.to_thread(self.documents.start, sid, ids, True)
                deadline = self.clock() + 900
                while True:
                    result = await asyncio.to_thread(self.documents.job, job['id'])
                    if result['status'] not in {'waiting', 'running'}: break
                    if self.clock() > deadline:
                        self.inbox.state(raw_id, 'recovery_needed', stage, {'code': 'parse_wait_timeout', 'message': '本地解析等待超时，原件保留；请核对已有解析状态后重试。', 'retryable': True})
                        return
                    await asyncio.sleep(.25)
                errors=result.get('error') or []
                notes_without_text=row['kind']=='inbox_lecture_note' and bool(errors) and all(e.get('code')=='empty_text' for e in errors)
                if not result.get('result_snapshot_id') and not notes_without_text:
                    attachments = await asyncio.to_thread(lambda: self.reviews.get(sid)['snapshot']['attachments'])
                    if attachments and all(a['parse_status'] == 'unsupported' for a in attachments):
                        self.inbox.state(raw_id, 'needs_review', stage, {'code': 'unsupported', 'message': '原件已保存；暂不支持自动解析此格式。', 'retryable': False})
                        return
                    self.inbox.state(raw_id, 'failed', stage, {'code': 'parse_failed', 'message': '原件已保存，本地解析未完成，可重试。', 'retryable': True})
                    return
                sid = result.get('result_snapshot_id') or sid
            stage = 'classify'
            self.inbox.state(raw_id, 'classifying', stage, snapshot_id=sid)
            await asyncio.to_thread(self.inbox.classify, raw_id, sid)
            learning = getattr(self, 'learning', None)
            if learning:
                stage = 'learning_projection'
                await learning.ingest(raw_id, sid)
        except asyncio.CancelledError:
            self.inbox.state(raw_id, 'recovery_needed', stage, {'code': 'interrupted', 'message': '处理已中断，原件保留，可重试本地处理。', 'retryable': True})
            raise
        except Exception as error:
            record(self.personal, stage, error, job_id=raw_id, entity='raw_item')
            self.inbox.state(raw_id, 'failed', stage, {'code': 'learning_projection_failed' if stage == 'learning_projection' else 'processing_failed', 'message': '原件已保存，自动整理未完成，可重试本地处理。', 'retryable': True})

    async def retry(self, raw_id, version):
        async with self.lock:
            detail = await asyncio.to_thread(self.inbox.detail, raw_id)
            if detail['version'] != version: raise PersonalError('状态已更新，请重新读取。', 'conflict', 409)
            if not detail['managed'] or detail['processing_status'] == 'analyzing':
                raise PersonalError('请通过原有来源或分析页面处理。', 'source_pipeline_required', 409)
            if raw_id not in self.tasks:
                self.inbox.state(raw_id, 'received', 'retry')
                self._schedule(raw_id)
            return await asyncio.to_thread(self.inbox.detail, raw_id)

    async def status(self, owner, submission_id):
        with self.personal.connection() as c:
            row = c.execute('''SELECT r.id,r.raw_item_id,i.inbox_status FROM inbox_receipt r
                JOIN raw_item i ON i.id=r.raw_item_id WHERE r.id=? AND r.owner=?''', (submission_id, owner)).fetchone()
            if not row: raise CollectorError('not_found', '这次收集记录不可读取，请在电脑 Inbox 查看。', 404)
            state = row['inbox_status']
            phases = {'received': 'Windows 已接收；原件已保存。', 'extracting': '原件已保存，正在本地解析。',
                      'classifying': '正在整理关联建议。', 'completed': '已整理，可在电脑 Eric Inbox 查看。',
                      'needs_review': '原件已保存，请在电脑 Eric Inbox 核对。', 'needs_manual_content': '链接已保存，正文无法自动获取，可粘贴或截图。',
                      'failed': '原件已保存，但处理未完成。', 'recovery_needed': '原件已保存，处理已中断，可在电脑重试。'}
            return {'id': row['id'], 'submission_id': row['raw_item_id'], 'state': state, 'phase': phases.get(state, '原件已保存。'),
                    'saved': True, 'duplicate': False, 'warning': ''}

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
