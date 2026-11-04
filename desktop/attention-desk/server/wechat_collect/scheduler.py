"""Pure schedule policy. Every external operation must obtain a fresh permit."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import WeChatConfig
from ..recovery_controls import paid_ai_paused

SHANGHAI = ZoneInfo('Asia/Shanghai')


def utc_now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ManualOverride:
    scan_id: str
    expires_at: datetime


@dataclass(frozen=True)
class Permission:
    allowed: bool
    reason: str
    expires_at: datetime


class SchedulerPolicy:
    def __init__(self, config: WeChatConfig, clock=utc_now):
        self.config, self.clock = config, clock

    def quiet(self, at=None):
        local = (at or self.clock()).astimezone(SHANGHAI)
        hhmm = local.strftime('%H:%M')
        return self.config.quiet_hours.start <= hhmm < self.config.quiet_hours.end

    def authorize(self, *, operation='scan', maximum_seconds=0, override=None, require_enabled=True):
        at = self.clock()
        local = at.astimezone(SHANGHAI)
        midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if operation in {'llm','vision'} and paid_ai_paused():
            return Permission(False, 'paid_analysis_paused', at)
        if require_enabled and not self.config.enabled:
            return Permission(False, 'disabled', at)
        if override and override.expires_at > at:
            if override.expires_at - at > timedelta(minutes=5):
                return Permission(False, 'invalid_override', at)
            until = override.expires_at
        elif self.quiet(at):
            return Permission(False, 'quiet', local.replace(hour=6, minute=0, second=0, microsecond=0))
        else:
            until = midnight
        guard = max(maximum_seconds, 60 if operation in {'llm', 'vision'} else 0)
        if at + timedelta(seconds=guard) >= until:
            return Permission(False, 'quiet_boundary', until)
        return Permission(True, 'manual' if override else 'allowed', until)

    def require(self, **kwargs):
        result = self.authorize(**kwargs)
        if not result.allowed:
            from ..personal_store import PersonalError
            raise PersonalError('微信操作已暂停：' + result.reason, result.reason, 409)
        return result

    def next_scan(self, at=None):
        local = (at or self.clock()).astimezone(SHANGHAI)
        if self.quiet(local):
            return local.replace(hour=6, minute=0, second=0, microsecond=0)
        result = local.replace(second=0, microsecond=0) + timedelta(minutes=15 - local.minute % 15)
        if self.quiet(result):
            result = result.replace(hour=6, minute=0)
        return result


class ScheduleState:
    """Coalesce all due reasons; no burst replay after sleep or clock changes."""
    def __init__(self, policy: SchedulerPolicy):
        self.policy = policy
        self.next_due = policy.next_scan()
        self.pending = {'startup'}
        self.resume_after = None
        self.last_slot = None

    def resumed(self):
        self.pending.add('resume')
        self.resume_after = self.policy.clock() + timedelta(seconds=self.policy.config.resume_delay_seconds)

    def recovered(self):
        self.pending.add('recovery')

    def due(self):
        at = self.policy.clock()
        if not self.policy.authorize().allowed or self.resume_after and at < self.resume_after:
            return set()
        local = at.astimezone(SHANGHAI)
        if at >= self.next_due:
            self.pending.add('periodic')
        if not self.pending:
            return set()
        reasons = set(self.pending)
        if local.hour == 6 and local.minute < 15:
            reasons.add('catch_up')
        if local.hour == 23 and local.minute < 15:
            reasons.add('core_reconcile')
        self.pending.clear()
        self.resume_after = None
        self.last_slot = local.isoformat()
        self.next_due = self.policy.next_scan(at)
        return reasons
