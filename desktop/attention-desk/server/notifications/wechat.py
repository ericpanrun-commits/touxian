"""Candidate-only Bark outbox. Does not collect messages or create formal items."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from ..ai_settings import protect
from ..personal_store import PersonalError, now
from .bark import BarkClient, DeliveryResult
from .config import BarkConfig
from ..wechat_collect.scheduler import SHANGHAI


def candidate_level(item, at, group_priority="high"):
    if (item.get('attention_level')!='action' or item.get('information_complete') is not True
            or item.get('await_more_context') is not False
            or item.get('analysis_origin') == 'local_pending' or not item.get('important')
            or item.get('relevant') is not True):
        return None
    deadline = item.get('deadline') or {}
    today = (deadline.get('value') or '')[:10] == at.astimezone(SHANGHAI).date().isoformat()
    urgent = (item.get('priority') == 'P0' or item.get('category') == 'course_change'
              or item.get('notice_kind') in {'update', 'cancellation'}
              or bool(re.search(r'紧急|临时变动', item.get('title', ''))))
    if urgent or today:
        return 'timeSensitive'
    if group_priority=='low' and item.get('priority') not in {'P0','P1'}:
        return None
    explicit_action=any(action.get('kind')=='explicit' and action.get('evidence_ids') for action in item.get('actions',[]))
    if ((item.get('category') in {'task', 'deadline'} or explicit_action) and item.get('action_required') is True
            and item.get('participation') == 'required'):
        return 'active'
    return None


class WeChatBarkDispatcher:
    def __init__(self, personal, *, config=None, transport=None, clock=None):
        self.personal = personal
        self.environment = config or BarkConfig.from_env()
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        self.stop = asyncio.Event()
        self.task = None

    def _read(self):
        with self.personal.connection() as c:
            return dict(c.execute('SELECT * FROM wechat_bark_settings WHERE id=1').fetchone())

    def _config(self, row):
        key = protect(row['protected_key'], decrypt=True) if row['protected_key'] else self.environment.device_key
        return replace(self.environment, enabled=bool(row['enabled']),
                       server_url=row['server_url'] or self.environment.server_url,
                       device_key=key, error='')

    def settings(self):
        row = self._read()
        # Never decrypt credentials just to display settings.
        with self.personal.connection() as c:
            last = c.execute('SELECT state,sent_at,last_error FROM wechat_bark_notification ORDER BY COALESCE(sent_at,created_at) DESC LIMIT 1').fetchone()
        return {'enabled': bool(row['enabled']), 'server': row['server_url'] or self.environment.server_url,
                'device_key_configured': bool(row['protected_key'] or self.environment.configured),
                'key_source': 'windows' if row['protected_key'] else 'environment' if self.environment.configured else 'missing',
                'version': row['version'], 'activated_at': row['activated_at'],
                'last_notification': dict(last) if last else None}

    def save(self, payload):
        allowed = {'enabled', 'server', 'device_key', 'expected_version'}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise PersonalError('手机通知设置包含不支持的字段。')
        server, key = payload.get('server'), payload.get('device_key', '')
        if not isinstance(server, str) or not isinstance(key, str) or type(payload.get('enabled')) is not bool:
            raise PersonalError('请核对 Bark 地址、开关和 Device Key。')
        try:
            parts = urlsplit(server.strip())
            # Server field is an origin, never a device-specific push URL.
            if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                    or parts.query or parts.fragment or parts.path not in {'', '/'}
                    or re.search(r'[\x00-\x20\x7f\\]', server)):
                raise ValueError()
            parts.port
            server = urlunsplit((parts.scheme, parts.netloc, '', '', ''))
        except ValueError:
            raise PersonalError('请填写 HTTPS Bark Server 根地址，例如 https://api.day.app；Device Key 请填在单独的密钥栏。') from None
        if key and not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', key):
            raise PersonalError('Device Key 格式无效，请从 Bark 复制单独的密钥。')
        protected = protect(key) if key else None
        with self.personal.connection(write=True) as c:
            row = c.execute('SELECT * FROM wechat_bark_settings WHERE id=1').fetchone()
            if type(payload.get('expected_version')) is not int or payload['expected_version'] != row['version']:
                raise PersonalError('设置已更新，请重新读取后保存。', 'conflict', 409)
            if payload['enabled'] and not (protected or row['protected_key'] or self.environment.configured):
                raise PersonalError('请先填写 Device Key，再开启手机通知。')
            activation = self.clock().isoformat() if payload['enabled'] and not row['enabled'] else row['activated_at']
            c.execute('UPDATE wechat_bark_settings SET enabled=?,server_url=?,protected_key=COALESCE(?,protected_key),activated_at=?,version=version+1 WHERE id=1',
                      (int(payload['enabled']), server, protected, activation))
        return self.settings()

    def test(self):
        if not self.lock.acquire(blocking=False):
            raise PersonalError('手机通知正在发送，请稍后再试。', 'busy', 409)
        try:
            row = self._read()
            at = self.clock()
            if row['last_test_at'] and (at - datetime.fromisoformat(row['last_test_at'])).total_seconds() < 60:
                return DeliveryResult(error='test_cooldown').public()
            config = self._config(row)
            if not config.configured:
                return DeliveryResult(error='not_configured').public()
            # Explicit connection tests work even when automatic candidate pushes are off.
            with self.personal.connection(write=True) as c:
                c.execute('UPDATE wechat_bark_settings SET last_test_at=? WHERE id=1', (at.isoformat(),))
            return BarkClient(replace(config, enabled=True), transport=self.transport).send({
                'title': 'Attention Desk · 微信', 'subtitle': '连接测试',
                'body': '手机通知已连接。重要微信通知会在这里提醒你，请打开 Attention Desk 确认。',
                'group': 'Attention Desk', 'level': 'active'}, max_attempts=1).public()
        finally:
            self.lock.release()

    def tick(self):
        from ..recovery_controls import cloud_uploads_held
        if cloud_uploads_held(self.personal.path):
            return
        if not self.lock.acquire(blocking=False):
            return
        try:
            at = self.clock()
            row = self._read()
            if not row['enabled'] or at.astimezone(SHANGHAI).hour < 6:
                return
            config = self._config(row)
            if not config.configured:
                return
            stamp = at.isoformat()
            with self.personal.connection(write=True) as c:
                # A crash after claim has an unknown outcome. Never resend it on restart.
                c.execute("UPDATE wechat_bark_notification SET state='failed',retryable=0,last_error='delivery_unknown' WHERE state='sending' AND retry_at<=?", (stamp,))
                rows = c.execute("SELECT ca.*,ch.name AS chat_name,ch.config_json AS group_config FROM wechat_candidate ca JOIN wechat_batch b ON b.id=ca.batch_id JOIN wechat_chat ch ON ch.id=b.chat_id JOIN raw_review r ON r.raw_snapshot_id=b.snapshot_id WHERE ch.enabled=1 AND ca.attention_level='action' AND ca.information_complete=1 AND ca.await_more_context=0 AND ca.status='needs_review' AND r.status='pending' AND b.superseded_by IS NULL AND ca.created_at>=? ORDER BY ca.created_at LIMIT 200", (row['activated_at'] or stamp,)).fetchall()
                for candidate in rows:
                    item = json.loads(candidate['data_json'])
                    level = candidate_level(item, at,json.loads(candidate['group_config']).get('priority','high'))
                    if level:
                        c.execute("INSERT OR IGNORE INTO wechat_bark_notification(candidate_id,state,level,created_at) VALUES (?,'not_sent',?,?)", (candidate['id'],level,stamp))
                work = c.execute("SELECT n.*,ca.data_json,ch.name AS chat_name,ch.config_json AS group_config FROM wechat_bark_notification n JOIN wechat_candidate ca ON ca.id=n.candidate_id JOIN wechat_batch b ON b.id=ca.batch_id JOIN raw_review r ON r.raw_snapshot_id=b.snapshot_id JOIN wechat_chat ch ON ch.id=b.chat_id WHERE ch.enabled=1 AND ca.attention_level='action' AND ca.information_complete=1 AND ca.await_more_context=0 AND ca.status='needs_review' AND r.status='pending' AND b.superseded_by IS NULL AND n.attempt_count<3 AND (n.state='not_sent' OR (n.state='failed' AND n.retryable=1 AND n.retry_at<=?)) ORDER BY n.created_at LIMIT 3", (stamp,)).fetchall()
            for notification in work:
                item = json.loads(notification['data_json'])
                level = candidate_level(item, at,json.loads(notification['group_config']).get('priority','high'))
                if not level:
                    continue
                with self.personal.connection(write=True) as c:
                    claimed = c.execute("UPDATE wechat_bark_notification SET state='sending',attempt_count=attempt_count+1,retry_at=? WHERE candidate_id=? AND state=? AND attempt_count=?", ((at+timedelta(minutes=2)).isoformat(),notification['candidate_id'],notification['state'],notification['attempt_count'])).rowcount
                if not claimed:
                    continue
                body = re.sub(r'\s+', ' ', item['title']).strip()
                due=item.get('deadline') or next((d.get('time',{}) for d in item.get('deadlines',[]) if d.get('relation')=='current'),{})
                if due.get('value') and due['value'] not in body:
                    body+=' · '+due['value'].replace('T',' ')+' 前'
                body=body[:100]
                result = BarkClient(config, transport=self.transport).send({
                    'title':'Attention Desk · 微信', 'subtitle':notification['chat_name'][:80],
                    'body':body, 'level':level, 'group':'Attention Desk',
                    'id':'ad-wx-'+hashlib.sha256(notification['candidate_id'].encode()).hexdigest()[:24]}, max_attempts=1)
                attempts = notification['attempt_count'] + 1
                with self.personal.connection(write=True) as c:
                    c.execute('UPDATE wechat_bark_notification SET state=?,sent_at=?,last_error=?,retryable=?,retry_at=? WHERE candidate_id=?',
                              ('sent' if result.accepted else 'failed',stamp if result.accepted else None,result.error,
                               int(result.retryable and attempts<3),(at+timedelta(minutes=2**attempts)).isoformat(),notification['candidate_id']))
        finally:
            self.lock.release()

    async def start(self):
        self.task = asyncio.create_task(self._run(), name='wechat-bark-outbox')

    async def close(self):
        self.stop.set()
        if self.task:
            await self.task

    async def _run(self):
        while not self.stop.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                # No raw exception, request body or credential ever enters ordinary logs.
                pass
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=15)
            except TimeoutError:
                pass
