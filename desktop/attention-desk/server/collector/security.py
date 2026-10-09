"""Ephemeral, capability-limited pairing. No credential or notice logging."""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PAIRING_SECONDS = 5 * 60
SESSION_SECONDS = 7 * 24 * 3600
MAX_DEVICES = 32


class CollectorError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Session:
    owner: str
    csrf: str
    expires: datetime


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


class PairingAuthority:
    """Five-minute invitation; explicit confirmation grants a seven-day session.

    Rotating an invitation preserves approved devices. Stop/restart/network
    changes and the separate desktop revocation action revoke all capabilities.
    """
    def __init__(self, clock=utc_now, *, diagnostics_path=None):
        self.clock, self.lock = clock, threading.RLock()
        self.sessions = {}
        self.pair_hash = None
        self.pair_expires = None
        self.current = None
        self.epoch = secrets.token_hex(12)
        self.records, self.requests = deque(maxlen=64), deque(maxlen=128)
        self.device_records, self.session_requests = deque(maxlen=64), deque(maxlen=128)
        self.diagnostics_path = Path(diagnostics_path) if diagnostics_path else None
        self.diagnostics_ok = True
        # Restore diagnostics only. No invitation/session credential survives
        # a process restart; the local UI starts disabled with a new epoch.
        if self.diagnostics_path:
            try:
                saved = json.loads(self.diagnostics_path.read_text(encoding='utf-8'))
                fields = {'id', 'created_at', 'expires_at', 'state', 'validated_at', 'confirmed_at',
                          'consumed_at', 'revoked_at', 'consume_reason', 'history'}
                for old in saved.get('tokens', [])[-64:]:
                    if not isinstance(old, dict) or set(old) != fields:
                        continue
                    if old['state'] in {'created', 'validated', 'confirmed'}:
                        old.update(state='revoked', revoked_at=timestamp(self._now()), consume_reason='collector_restart')
                        old['history'].append('revoked')
                    self.records.append(old)
                device_fields = {'id', 'created_at', 'expires_at', 'state', 'revoked_at', 'revoke_reason'}
                for old in saved.get('devices', [])[-64:]:
                    if not isinstance(old, dict) or set(old) != device_fields:
                        continue
                    if old['state'] == 'approved':
                        old.update(state='revoked', revoked_at=timestamp(self._now()), revoke_reason='collector_restart')
                    self.device_records.append(old)
            except (OSError, ValueError, TypeError, KeyError):
                pass
            self._save()

    def _now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('pairing_clock_requires_aware_datetime')
        return value.astimezone(timezone.utc)

    def _save(self):
        if not self.diagnostics_path:
            return
        try:
            self.diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.diagnostics_path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'epoch': self.epoch, 'tokens': list(self.records),
                                              'requests': list(self.requests), 'devices': list(self.device_records),
                                              'session_requests': list(self.session_requests)}, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.diagnostics_path)
            self.diagnostics_ok = True
        except OSError:
            self.diagnostics_ok = False  # Never log an exception containing credentials.

    def _expire(self):
        if self.pair_hash and self._now() >= self.pair_expires:
            self.current.update(state='expired', consume_reason='ttl_elapsed')
            self.current['history'].append('expired')
            self.pair_hash = None
            self._save()

    def _revoke_invitation(self, reason):
        self._expire()
        if self.pair_hash:
            self.current.update(state='revoked', revoked_at=timestamp(self._now()), consume_reason=reason)
            self.current['history'].append('revoked')
        self.pair_hash = None
        self._save()

    def trace(self, method, path, token, result):
        """Only fixed pairing paths/results and a 12-character SHA256 id.

        Never pass a query string, session secret, payload or exception here.
        """
        if method not in {'GET', 'POST'} or path not in {'/pair', '/collect', '/api/pair/validate', '/api/pair/confirm'}:
            return
        if result not in {'valid', 'confirmed', 'pairing_invalid', 'device_limit'}:
            return
        with self.lock:
            self.requests.append({'at': timestamp(self._now()), 'method': method, 'path': path,
                                  'token_id': digest(token)[:12] if isinstance(token, str) else None, 'result': result})
            self._save()

    def regenerate(self):
        with self.lock:
            self._revoke_invitation('regenerated')
            token = secrets.token_urlsafe(32)
            now = self._now()
            self.pair_hash, self.pair_expires = digest(token), now + timedelta(seconds=PAIRING_SECONDS)
            self.current = {'id': self.pair_hash[:12], 'created_at': timestamp(now), 'expires_at': timestamp(self.pair_expires),
                            'state': 'created', 'validated_at': None, 'confirmed_at': None, 'consumed_at': None,
                            'revoked_at': None, 'consume_reason': None, 'history': ['created']}
            self.records.append(self.current)
            self._save()
            return token

    def revoke(self, reason='devices_revoked'):
        with self.lock:
            for owner in self.sessions:
                self._device_ended(owner, 'revoked', reason)
            self.sessions.clear()
            self._revoke_invitation(reason)

    def _device_ended(self, owner, state, reason):
        for record in reversed(self.device_records):
            if record['id'] == owner[:12] and record['state'] == 'approved':
                record.update(state=state, revoked_at=timestamp(self._now()), revoke_reason=reason)
                break

    def _prune_sessions(self, now):
        changed = False
        for owner, session in list(self.sessions.items()):
            if now >= session.expires:
                self._device_ended(owner, 'expired', 'ttl_elapsed')
                del self.sessions[owner]
                changed = True
        if changed:
            self._save()

    def _trace_session(self, value, result):
        # Fixed endpoint, coarse result and a short hash only. Never save the
        # Cookie header, secret, CSRF value, query, user agent or content.
        self.session_requests.append({'at': timestamp(self._now()), 'method': 'GET', 'path': '/api/session',
                                      'cookie_present': bool(value),
                                      'device_id': digest(value)[:12] if isinstance(value, str) and 32 <= len(value) <= 128 else None,
                                      'result': result})
        self._save()

    def _validate_token(self, token):
        self._expire()
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise CollectorError('pairing_invalid', '配对已失效，请在电脑重新生成二维码。', 401)
        if not self.pair_hash or not secrets.compare_digest(digest(token), self.pair_hash):
            raise CollectorError('pairing_invalid', '配对已失效，请在电脑重新生成二维码。', 401)

    def validate(self, token):
        """Non-consuming check: safe to repeat in independent browser cookie jars."""
        with self.lock:
            self._validate_token(token)
            if self.current['state'] == 'created':
                self.current['state'] = 'validated'
                self.current['history'].append('validated')
            self.current['validated_at'] = timestamp(self._now())
            self._save()
            return {'pairing_valid': True, 'expires_in': max(0, int((self.pair_expires - self._now()).total_seconds()))}

    def pair(self, token):
        """Confirm and consume atomically, never invoked by a page GET."""
        with self.lock:
            self._validate_token(token)
            now = self._now()
            self._prune_sessions(now)
            if len(self.sessions) >= MAX_DEVICES:
                raise CollectorError('device_limit', '已配对设备较多，请先在电脑撤销已配对设备。', 409)
            value = secrets.token_urlsafe(32)
            session = Session(digest(value), secrets.token_urlsafe(24), now + timedelta(seconds=SESSION_SECONDS))
            self.sessions[session.owner] = session
            self.device_records.append({'id': session.owner[:12], 'created_at': timestamp(now),
                                        'expires_at': timestamp(session.expires), 'state': 'approved',
                                        'revoked_at': None, 'revoke_reason': None})
            self.current.update(state='consumed', confirmed_at=timestamp(now), consumed_at=timestamp(now), consume_reason='explicit_post_confirm')
            self.current['history'].extend(['confirmed', 'consumed'])
            self.pair_hash = None
            self._save()
            return value, session

    def session(self, value, *, diagnose=False):
        with self.lock:
            if not isinstance(value, str) or not 32 <= len(value) <= 128:
                if diagnose:
                    self._trace_session(value, 'cookie_missing' if not value else 'invalid_cookie')
                raise CollectorError('not_paired', '手机尚未配对或会话已失效，请重新扫码。', 401)
            session = self.sessions.get(digest(value))
            if not session or self._now() >= session.expires:
                if session:
                    self._device_ended(session.owner, 'expired', 'ttl_elapsed')
                self.sessions.pop(digest(value), None)
                if diagnose:
                    self._trace_session(value, 'session_expired' if session else 'unknown_session')
                elif session:
                    self._save()
                raise CollectorError('not_paired', '手机尚未配对或会话已失效，请重新扫码。', 401)
            if diagnose:
                self._trace_session(value, 'authenticated')
            return session

    def check_csrf(self, session, value):
        if not isinstance(value, str) or not secrets.compare_digest(session.csrf, value):
            raise CollectorError('request_rejected', '请求无法验证，请重新打开收集页。', 403)

    def public(self):
        with self.lock:
            self._expire()
            self._prune_sessions(self._now())
            return {'paired_devices': len(self.sessions), 'pairing_available': bool(self.pair_hash),
                    'pairing_expires_in': max(0, int((self.pair_expires - self._now()).total_seconds())) if self.pair_hash else 0,
                    'server_epoch': self.epoch, 'server_time': timestamp(self._now()), 'diagnostics_ok': self.diagnostics_ok,
                    'pairing_id': self.current['id'] if self.pair_hash else None,
                    'pairing': {k: v for k, v in self.current.items() if k != 'history'} if self.current else None}


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock, self.buckets = clock, threading.Lock(), {}

    def check(self, key, limit, window=60):
        with self.lock:
            stamp = self.clock()
            if len(self.buckets) >= 256:
                self.buckets = {k: v for k, v in self.buckets.items() if v and v[-1] > stamp - window}
                if len(self.buckets) >= 256 and key not in self.buckets:
                    raise CollectorError('rate_limited', '请求较多，请稍后再试。', 429)
            values = self.buckets.setdefault(key, deque())
            while values and values[0] <= stamp - window:
                values.popleft()
            if len(values) >= limit:
                raise CollectorError('rate_limited', '发送过于频繁，请稍后再试。', 429)
            values.append(stamp)
