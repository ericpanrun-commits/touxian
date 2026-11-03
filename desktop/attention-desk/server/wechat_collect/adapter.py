"""Read-only adapter interface. Unverified selectors never read chat contents."""
from __future__ import annotations

from typing import Protocol

from ..personal_store import PersonalError
from .models import Observation

# These areas are never operated on, even when a selector happens to match.
DENIED_CONTROL_ROLES = frozenset({'EditControl', 'DocumentControl', 'ButtonControl', 'MenuItemControl'})
DENIED_AUTOMATION_IDS = frozenset({'chat_input', 'send', 'voice', 'emoji', 'send_file', 'send_image'})


class ReadAdapter(Protocol):
    is_real: bool
    def read(self, chat, binding, cursor, *, policy, override=None, cancelled=lambda: False) -> Observation: ...


class UnverifiedUIAAdapter:
    """Fail closed until the actual WeChat version has a certified selector profile.

    There are deliberately no write/input methods or guessed chat selectors.
    A structural spike is not a successful message-reading certification.
    """
    is_real = True
    def read(self, chat, binding, cursor, *, policy, override=None, cancelled=lambda: False):
        policy.require(operation='uia', maximum_seconds=8, override=override)
        if cancelled():
            raise PersonalError('用户活动，已停止后续操作。', 'cancelled', 409)
        if not binding or binding.get('capability') not in {'GO', 'IDLE_ONLY'}:
            raise PersonalError('真实微信读取尚未通过 Phase 0；未读取任何聊天。', 'uia_not_verified', 409)
        raise PersonalError('本机版本尚无经过实测的消息读取适配器。', 'adapter_not_certified', 409)


class FakeAdapter:
    """Explicit test injection only; never selectable through configuration/API."""
    is_real = False
    def __init__(self, observations):
        self.observations = list(observations)
        self.calls = 0

    def read(self, chat, binding, cursor, *, policy, override=None, cancelled=lambda: False):
        policy.require(operation='uia', override=override)
        if cancelled():
            raise PersonalError('用户活动，已停止。', 'cancelled', 409)
        self.calls += 1
        if not self.observations:
            raise PersonalError('测试适配器没有观测。', 'unavailable', 503)
        value = self.observations.pop(0) if len(self.observations) > 1 else self.observations[0]
        if isinstance(value, Exception):
            raise value
        return value
