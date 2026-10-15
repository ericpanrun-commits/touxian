"""Delivery policy over Phase 7 facts. Does not reclassify or mutate importance."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from ..official_rules import load_profile
from ..official_sources import canonical_url, SourceFailure
from ..planning.time import aware, stamp


class NotificationPolicyConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    critical: StrictBool = True
    high_relevant: StrictBool = True
    vehicle_direct: StrictBool = True
    college_grade: StrictBool = True
    academic_core: StrictBool = True
    minimum_importance: str = 'high'
    minimum_relevance: int = Field(default=80, ge=0, le=100, strict=True)
    categories: list[str] = Field(default_factory=list, max_length=30)
    source_priorities: list[str] = Field(default_factory=lambda: ['high', 'normal', 'low'], max_length=3)
    quiet_enabled: StrictBool = True
    quiet_start: str = '23:30'
    quiet_end: str = '07:30'
    timezone: str = 'Asia/Shanghai'
    fresh_days: int = Field(default=2, ge=0, le=7, strict=True)
    max_per_hour: int = Field(default=3, ge=1, le=10, strict=True)
    digest_enabled: StrictBool = False

    @field_validator('minimum_importance')
    @classmethod
    def importance(cls, value):
        if value not in {'normal', 'high', 'critical'}:
            raise ValueError('minimum importance is invalid')
        return value

    @field_validator('quiet_start', 'quiet_end')
    @classmethod
    def clock(cls, value):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('expected HH:mm')
        return value

    @field_validator('timezone')
    @classmethod
    def timezone_name(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, KeyError) as exc:
            raise ValueError('invalid timezone') from exc
        return value

    @field_validator('source_priorities')
    @classmethod
    def priorities(cls, value):
        if any(v not in {'high', 'normal', 'low'} for v in value):
            raise ValueError('invalid priority')
        return value

    @field_validator('digest_enabled')
    @classmethod
    def digest(cls, value):
        if value:
            raise ValueError('digest is not available in this phase')
        return value


def click_url(value, sources):
    hosts = {h for s in sources if s.get('source_type') in {'official_school', 'official_college', 'official_department'}
             for h in s.get('allowed_hosts', []) if h == 'zqu.edu.cn' or h.endswith('.zqu.edu.cn')}
    try:
        return canonical_url(value, list(hosts))
    except (SourceFailure, TypeError):
        return None


def quiet_due(at, config):
    if not config.quiet_enabled or config.quiet_start == config.quiet_end:
        return at
    tz = ZoneInfo(config.timezone)
    local = aware(at).astimezone(tz)
    start, end = time.fromisoformat(config.quiet_start), time.fromisoformat(config.quiet_end)
    clock = local.time().replace(tzinfo=None)
    overnight = start > end
    quiet = clock >= start or clock < end if overnight else start <= clock < end
    if not quiet:
        return at
    day = local.date() + timedelta(days=int(overnight and clock >= start))
    from ..planning.time import reminder_civil
    return aware(reminder_civil(day, config.quiet_end, tz))


def expired(item, at, config):
    tz = ZoneInfo(config.timezone)
    for field in ('deadline_at', 'event_end_at'):
        value = item.get(field)
        if not value:
            continue
        try:
            if len(value) == 10:
                if date.fromisoformat(value) < aware(at).astimezone(tz).date():
                    return True
            else:
                end = datetime.fromisoformat(value)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=tz)
                if aware(end) < aware(at):
                    return True
        except ValueError:
            continue
    return False


def evaluate(item, sources, config, *, activation_at, at):
    """Return (reason, scheduled_at). None scheduled means permanently suppressed."""
    profile = load_profile()
    if (not activation_at or item.get('official_backfill', 1)
            or not item.get('discovered_at') or item['discovered_at'] <= activation_at):
        return 'historical', None
    if not click_url(item.get('canonical_url'), sources) or not any(
            s.get('school') == profile['school'] and s.get('source_type') in
            {'official_school', 'official_college', 'official_department'} for s in sources):
        return 'untrusted_source', None
    tz = ZoneInfo(config.timezone)
    try:
        age = (aware(at).astimezone(tz).date() - date.fromisoformat(item['published_at'][:10])).days
        if age < 0 or age > config.fresh_days:
            return 'historical', None
    except (TypeError, ValueError, KeyError):
        return 'unknown_publication', None
    if expired(item, at, config):
        return 'expired', None
    importance, relevance = item['importance'], item['relevance']
    if importance == 'critical':
        return ('critical', stamp(at)) if config.critical else ('policy_disabled', None)
    if importance == 'low':
        return 'low_importance', None
    if relevance < config.minimum_relevance:
        return 'low_relevance', None
    if config.categories and item['category'] not in config.categories:
        return 'category_filtered', None
    if not any(s.get('priority') in config.source_priorities for s in sources):
        return 'source_priority_filtered', None
    reasons = item.get('reasons', [])
    direct = config.vehicle_direct and relevance >= 98 and '车辆工程专业' in reasons
    grade = config.college_grade and profile['college'] in reasons and any(
        v in reasons for v in profile['grade_keywords'] if v != '本科生')
    academic = config.academic_core and item['category'] in {'教务', '考试', '选课', '课程', '成绩', '学籍'}
    # Phase 7 reasons carry audience exclusions; the relevance floor honors those.
    standard = config.high_relevant and {'normal': 1, 'high': 2, 'critical': 3}[importance] >= {
        'normal': 1, 'high': 2, 'critical': 3}[config.minimum_importance]
    reason = 'vehicle_direct' if direct else 'college_grade' if grade else 'academic_core' if academic else 'high_relevant' if standard else None
    return (reason, stamp(quiet_due(at, config))) if reason else ('policy_filtered', None)


def truncate(value, maximum):
    value = str(value or '').strip()
    return value if len(value) <= maximum else value[:maximum - 1].rstrip() + '…'


def presentation(item, sources, config, bark_config, *, at, notification_id):
    status = {'critical': '🔴', 'high': '🟠'}.get(item['importance'], '🔵')
    # Strip source-supplied emoji so the title retains at most one status symbol.
    title = re.sub('[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F]', '', item['title'])
    title = truncate(f"{status} {item['category']}｜{title}", 120)
    organization = next((s['department'] for s in sources if s.get('source_type') == 'official_college'),
                        sources[0].get('department', '') if sources else '')
    parts = [organization]
    deadline = item.get('deadline_at')
    if deadline:
        display = deadline[:16].replace('T', ' ')
        urgent = False
        if len(deadline) > 10:
            try:
                instant = datetime.fromisoformat(deadline)
                if instant.tzinfo is None:
                    instant = instant.replace(tzinfo=ZoneInfo(config.timezone))
                urgent = timedelta(0) <= aware(instant) - aware(at) < timedelta(hours=24)
            except ValueError:
                pass
        parts.append(('即将截止：' if urgent else '截止：') + display)
    parts.extend([truncate(item.get('summary'), 250), '点击查看官方原文。'])
    payload = {'title': title, 'body': truncate('\n'.join(v for v in parts if v), 400),
               'group': bark_config.group, 'url': click_url(item['canonical_url'], sources),
               'level': 'timeSensitive' if item['importance'] == 'critical' else 'active', 'id': notification_id}
    if bark_config.sound:
        payload['sound'] = bark_config.sound
    return payload
