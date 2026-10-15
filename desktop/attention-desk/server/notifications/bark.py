"""Bark V2 JSON transport. Never retain provider bodies, request URLs or exceptions."""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


MESSAGES = {
    'not_configured': 'Bark 未配置，请在后端 .env 中填写 Device Key 后重启。',
    'disabled': 'Bark 已关闭，请启用推送后再测试。',
    'invalid_config': 'Bark 配置无效，请检查后端环境变量。',
    'unauthorized': 'Bark unauthorized / invalid key，请核对 Device Key。',
    'rejected': 'Bark 拒绝请求，请检查服务配置。',
    'rate_limited': 'Bark 请求过于频繁，将有限重试。',
    'server_error': 'Bark 服务器暂时不可用，将有限重试。',
    'connect_timeout': '连接 Bark 超时，将有限重试。',
    'unreachable': 'Bark 服务器不可达，将有限重试。',
    'delivery_unknown': '无法确认 Bark 是否接受通知；为避免重复，已停止自动重发。',
    'test_cooldown': '测试通知刚刚发送过，请稍后再试。',
    'client_error': 'Bark 发送组件暂时不可用。',
}


@dataclass(frozen=True)
class DeliveryResult:
    accepted: bool = False
    error: str | None = None
    retryable: bool = False
    status_code: int | None = None
    provider_message_id: str | None = None

    def public(self):
        return {'accepted': self.accepted, 'result': 'sent' if self.accepted else self.error,
                'message': 'Bark 已接受通知，请在 iPhone 查看。' if self.accepted else MESSAGES.get(self.error, MESSAGES['client_error'])}


class BarkClient:
    def __init__(self, config, *, transport=None, sleep=time.sleep):
        self.config, self.transport, self.sleep = config, transport, sleep

    def send(self, payload, *, max_attempts=3):
        if self.config.error:
            return DeliveryResult(error='invalid_config')
        if not self.config.configured:
            return DeliveryResult(error='not_configured')
        if not self.config.enabled:
            return DeliveryResult(error='disabled')
        # Queue callers use ONE HTTP request per persisted attempt, never nested retries.
        for attempt in range(max(1, min(max_attempts, 3))):
            result = self._once(payload)
            if not result.retryable or attempt + 1 >= max_attempts:
                return result
            self.sleep(2 ** attempt)
        return result

    def _once(self, payload):
        try:
            # No redirects: never forward credentials to a redirected destination.
            with httpx.Client(transport=self.transport, timeout=httpx.Timeout(8, connect=3, read=8, write=5, pool=3),
                              follow_redirects=False, trust_env=False,
                              headers={'User-Agent': 'ZQU-Workspace/7.1', 'Content-Type': 'application/json; charset=utf-8'}) as client:
                response = client.post(self.config.endpoint, json={**payload, 'device_key': self.config.device_key})
            code = response.status_code
            if code == 429:
                return DeliveryResult(error='rate_limited', retryable=True, status_code=code)
            if code >= 500:
                return DeliveryResult(error='server_error', retryable=True, status_code=code)
            if code in {400, 401, 403, 404, 410}:
                return DeliveryResult(error='unauthorized', status_code=code)
            if not 200 <= code < 300:
                return DeliveryResult(error='rejected', status_code=code)
            try:
                data = response.json()
            except ValueError:
                return DeliveryResult(error='delivery_unknown', status_code=code)
            if not isinstance(data, dict) or data.get('code') != 200:
                provider_code = data.get('code') if isinstance(data, dict) else None
                return DeliveryResult(error='unauthorized' if provider_code in {400, 401, 403, 404, 410} else 'delivery_unknown', status_code=code)
            # Stable local id is also sent to Bark for update semantics, not exactly-once claims.
            return DeliveryResult(accepted=True, status_code=code, provider_message_id=payload.get('id'))
        except httpx.ConnectTimeout:
            return DeliveryResult(error='connect_timeout', retryable=True)
        except (httpx.ConnectError, httpx.PoolTimeout):
            return DeliveryResult(error='unreachable', retryable=True)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            # Read/write failure can occur AFTER the provider accepted the push.
            return DeliveryResult(error='delivery_unknown')
        except Exception:
            return DeliveryResult(error='client_error')

    def test(self, *, notification_id, body='Bark 手机通知连接成功 🎉 [test]'):
        return self.send({'title': 'ZQU Workspace', 'body': body, 'group': 'ZQU Workspace',
                          'level': 'active', 'id': notification_id}, max_attempts=1)

    def health_check(self):
        # Passive configuration check only; delivery health comes from the persisted ledger.
        return self.config.public()
