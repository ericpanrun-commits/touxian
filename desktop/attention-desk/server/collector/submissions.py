"""Ephemeral receipts; all permanent content uses existing RawItem and services."""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from uuid import UUID, uuid4
from ..extraction_models import RawInput
from ..personal_store import encode, now
from ..review_store import ReviewStore
from .public_url import PublicURLFetcher, canonical_public_url
from .security import CollectorError
from .safe_logs import record


@dataclass
class Receipt:
    id: str
    owner: str
    fingerprint: str
    created: float
    state: str = 'receiving'
    phase: str = '正在保存到电脑'
    saved: bool = False
    snapshot_id: str | None = None
    warning: str = ''

    def public(self, duplicate=False):
        return {'id': self.id, 'state': self.state, 'phase': self.phase, 'saved': self.saved,
                'duplicate': duplicate, 'warning': self.warning}


class SubmissionService:
    def __init__(self, personal, documents, extraction, *, fetcher=None, clock=time.monotonic):
        self.personal, self.documents, self.extraction = personal, documents, extraction
        self.reviews, self.fetcher, self.clock = ReviewStore(personal), fetcher or PublicURLFetcher(), clock
        self.loop = asyncio.get_running_loop()
        self.receipts, self.requests, self.tasks = {}, {}, {}

    async def bridge(self, coroutine):
        if asyncio.get_running_loop() is self.loop:
            return await coroutine
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        # A lost phone connection must not cancel a save that reached Windows.
        return await asyncio.shield(asyncio.wrap_future(future))

    @staticmethod
    def fingerprint(kind, payload, files):
        manifest = []
        for name, mime, path in files:
            with path.open('rb') as stream:
                manifest.append((name, mime, hashlib.file_digest(stream, 'sha256').hexdigest()))
        return hashlib.sha256(encode({'kind': kind, 'payload': {k: v for k, v in payload.items() if k not in {'submission_id', 'again', 'confirm_large'}}, 'files': manifest}).encode()).hexdigest()

    async def submit(self, owner, kind, payload, files=()):
        try:
            request_id = str(UUID(payload['submission_id']))
        except (KeyError, ValueError, TypeError):
            raise CollectorError('invalid_submission', '发送标识无效，请重新打开收集页。')
        if kind == 'url':
            canonical_public_url(payload['url'])
        signature = await asyncio.to_thread(self.fingerprint, kind, payload, files)
        stale = {key for key, value in self.receipts.items() if key not in self.tasks and self.clock() - value.created > 3600}
        for key_stale in stale:
            self.receipts.pop(key_stale)
        self.requests = {key_request: value for key_request, value in self.requests.items() if value in self.receipts}
        key = (owner, request_id)
        if key in self.requests:
            receipt = self.receipts[self.requests[key]]
            if receipt.fingerprint != signature:
                raise CollectorError('submission_changed', '本次发送内容已改变，请开始一次新提交。', 409)
            return receipt.public(duplicate=True)
        if len(self.requests) >= 2048:
            raise CollectorError('busy', '收集请求较多，请稍后再发送。', 429)
        if not payload.get('again'):
            repeated = next((r for r in reversed(list(self.receipts.values())) if r.owner == owner and r.fingerprint == signature and self.clock() - r.created < 120), None)
            if repeated:
                self.requests[key] = repeated.id
                return repeated.public(duplicate=True)
        if len(self.tasks) + sum(not r.saved for r in self.receipts.values()) >= 8:
            raise CollectorError('busy', '电脑正在整理多条通知，请稍后再发送。', 429)
        if len(self.receipts) >= 512:
            raise CollectorError('busy', '收集任务较多，请稍后再发送。', 429)
        receipt = Receipt(str(uuid4()), owner, signature, self.clock())
        self.receipts[receipt.id], self.requests[key] = receipt, receipt.id
        source = {'source_id': 'mobile', 'source_kind': 'mobile', 'source_name': '来自手机', 'raw_kind': 'mobile_' + kind}
        title = payload.get('source_name') or (payload.get('text', '').strip().splitlines()[0][:200] if payload.get('text', '').strip() else '来自手机的通知')
        stamp = payload.get('source_time') or None
        try:
            if files:
                bundle = await asyncio.to_thread(self.documents.files.collect, files, payload.get('text', ''), title, stamp, receipt.id, **source)
            else:
                body = payload['url'] if kind == 'url' else payload['text']
                bundle = await asyncio.to_thread(self.reviews.collect, RawInput(original_text=body, title_hint=title, source_time=stamp), receipt.id, **source)
            receipt.snapshot_id, receipt.saved = bundle['snapshot']['id'], True
            receipt.state, receipt.phase = 'received', 'Windows 已接收'
            if kind == 'url':
                with self.personal.connection(write=True) as connection:
                    connection.execute('UPDATE raw_item SET original_url=?,canonical_url=?,title=?,discovered_at=? WHERE id=?',
                                       (payload['url'], canonical_public_url(payload['url']), title, now(), bundle['snapshot']['raw_item_id']))
            task = asyncio.create_task(self._finish(receipt, kind, payload, bundle))
            self.tasks[receipt.id] = task
            task.add_done_callback(lambda _: self.tasks.pop(receipt.id, None))
            return receipt.public()
        except Exception as error:
            record(self.personal, 'save', error)
            if receipt.saved:
                receipt.state, receipt.phase = 'failed', '内容已保存，但自动整理未完成。请在电脑核对。'
                return receipt.public()
            self.receipts.pop(receipt.id, None)
            self.requests = {k: v for k, v in self.requests.items() if v != receipt.id}
            raise CollectorError('save_failed', '内容尚未保存到电脑，请保留输入后重试。', 503)

    def _save_page(self, snapshot_id, original_url, page):
        with self.personal.connection(write=True) as connection:
            original = connection.execute('SELECT * FROM raw_snapshot WHERE id=?', (snapshot_id,)).fetchone()
            stamp = now()
            new_id = ReviewStore._new_snapshot(connection, original['raw_item_id'], page['text'], stamp, original['source_time'], page['title'])
            version_hash = hashlib.sha256(encode(page).encode()).hexdigest()
            connection.execute('''INSERT INTO raw_snapshot_web VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                               (new_id, 'mobile', page['title'], original_url, page['url'], page['published_at'], stamp, '[]', '[]', page['body_html'], 'text', version_hash, 1))
            connection.execute('UPDATE raw_item SET canonical_url=?,title=?,published_at=?,last_fetch_attempt_at=?,fetch_error=? WHERE id=?',
                               (page['url'], page['title'], page['published_at'], stamp, '', original['raw_item_id']))
            return new_id

    async def _finish(self, receipt, kind, payload, bundle):
        try:
            if kind == 'url':
                receipt.state, receipt.phase = 'fetching', '正在读取公开网页'
                try:
                    page = await asyncio.to_thread(self.fetcher.fetch, payload['url'])
                    receipt.snapshot_id = await asyncio.to_thread(self._save_page, receipt.snapshot_id, payload['url'], page)
                except CollectorError as error:
                    receipt.state, receipt.phase = 'failed', '链接已保存，正文无法自动获取，可改为截图/粘贴。'
                    with self.personal.connection(write=True) as connection:
                        connection.execute('UPDATE raw_item SET fetch_error=?,last_fetch_attempt_at=? WHERE id=?', (error.code, now(), bundle['snapshot']['raw_item_id']))
                    return
            if kind == 'file':
                ids = [value['id'] for value in bundle['snapshot']['attachments']]
                job = await asyncio.to_thread(self.documents.start, receipt.snapshot_id, ids, payload.get('confirm_large', False))
                receipt.state, receipt.phase = 'parsing', '正在解析 / 本地识别图片'
                while True:
                    result = await asyncio.to_thread(self.documents.job, job['id'])
                    if result['status'] not in {'waiting', 'running'}:
                        break
                    await asyncio.sleep(.4)
                if not result['result_snapshot_id']:
                    receipt.state, receipt.phase = 'failed', '原文件已保存，但解析未完成。请在电脑核对或重试。'
                    return
                receipt.snapshot_id = result['result_snapshot_id']
                if result.get('error'):
                    receipt.warning = '部分文件未能解析，请在电脑核对。'
            receipt.state, receipt.phase = 'waiting_ai', '等待电脑 AI 分析'
            result = await self.extraction.start(receipt.snapshot_id)
            while result['extraction']['status'] in {'waiting', 'running'}:
                receipt.state, receipt.phase = 'analyzing', '电脑正在分析通知'
                await asyncio.sleep(.4)
                result = await asyncio.to_thread(self.reviews.get, receipt.snapshot_id)
            if result['extraction']['status'] == 'failed':
                receipt.state, receipt.phase = 'failed', '内容已保存，但自动分析失败。请在电脑查看或手动整理。'
            else:
                receipt.state, receipt.phase = 'needs_review', '已发送到工作台，请在电脑上确认。'
        except asyncio.CancelledError:
            receipt.state, receipt.phase = 'failed', '内容已保存，工作台重新开启后可继续整理。'
            raise
        except Exception as error:
            record(self.personal, 'process', error)
            receipt.state, receipt.phase = 'failed', '内容已保存，但自动整理未完成。请在电脑核对。'

    async def status(self, owner, submission_id):
        receipt = self.receipts.get(submission_id)
        if receipt and self.clock() - receipt.created > 3600 and submission_id not in self.tasks:
            self.receipts.pop(submission_id)
            self.requests = {key: value for key, value in self.requests.items() if value != submission_id}
            receipt = None
        if not receipt or receipt.owner != owner:
            raise CollectorError('not_found', '这次收集记录不可读取，请在电脑查看已保存的通知。', 404)
        return receipt.public()

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
