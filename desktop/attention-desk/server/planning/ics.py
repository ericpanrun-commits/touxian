"""RFC 5545 export, stable Deadline identity, no source payload or local paths."""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from icalendar import Calendar, Event

from .query import PlanningQuery
from .time import UTC, aware, resolve, utc_now


def safe_text(value: str) -> str:
    value = str(value or '')
    value = re.sub(r'(?i)\b(?:sk-[\w-]{8,}|(?:api[_ -]?key|pairing[_ -]?token|session[_ -]?(?:secret|token)|token)\s*[:=]\s*[^\s,;]+)', '[已隐藏]', value)
    value = re.sub(r'(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n,;]+', '[本地路径已隐藏]',value)
    value = re.sub(r'(?<!\w)/(?:Users|home|tmp|var|mnt|private|Volumes)/[^\r\n,;]+', '[本地路径已隐藏]',value)
    return ''.join(char for char in value if ord(char) >= 32 or char in '\n\t')[:3000]


def public_url(value: str | None) -> str | None:
    if not value or len(value) > 2048:
        return None
    try:
        url = urlsplit(value)
        host = (url.hostname or '').lower().rstrip('.')
        if url.scheme not in {'http','https'} or not host or url.username or url.password:
            return None
        if host in {'localhost','localhost.localdomain'} or host.endswith(('.localhost','.local','.internal')) or '.' not in host:
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            # Numeric alternate loopback forms (127.1, 2130706433) are not public.
            if re.fullmatch(r'[\d.xXa-fA-F]+',host):
                return None
        if any(re.search(r'(?i)(token|key|secret|password|auth|session)',key) for key,_ in parse_qsl(url.query)):
            return None
        if re.search(r'(?i)(sk-[\w-]{8,}|token=|secret=)',value) or any(ord(c)<33 for c in value):
            return None
        return value
    except ValueError:
        return None


def export(store, *, event_id=None, deadline_id=None, future=False, include_terminal=False, at=None) -> bytes:
    at = aware(at or utc_now())
    calendar = Calendar()
    calendar.add('prodid','-//Attention Desk//Confirmed Deadlines//ZH-CN')
    calendar.add('version','2.0')
    calendar.add('calscale','GREGORIAN')
    rows = PlanningQuery(store).deadlines(include_terminal=include_terminal,event_id=event_id,deadline_id=deadline_id,at=at)
    for item in rows:
        fact = resolve(item)
        if not fact or (future and item['state'] == 'overdue'):
            continue
        entry = Event()
        # Formal Deadline UUID is stable across user edits and source updates.
        entry.add('uid',f"{UUID(item['id'])}@attention-desk.local")
        entry.add('dtstamp',at)
        entry.add('dtstart',fact.start if fact.all_day else aware(fact.start))
        if fact.all_day:
            entry.add('dtend',fact.end+timedelta(days=1))
        elif fact.end != fact.start:
            entry.add('dtend',aware(fact.end))
        entry.add('summary',safe_text(f"【{item['label']}】{item['event_title']}"))
        description = [f"时间节点：{item['label']}",f"来源：{item['source_name']}"]
        if item['tasks']:
            description.append('未完成任务：'+'；'.join(task['title'] for task in item['tasks'][:8]))
        if item['evidence']:
            locations = [f"PDF 第 {row['page']} 页" if row['page'] else row['section'] or row['source_type'] for row in item['evidence'][:4]]
            description.append('证据位置：'+'；'.join(locations))
        entry.add('description',safe_text('\n'.join(description)))
        url = public_url(item['source_url'])
        if url:
            entry.add('url',url)
        modified = max(datetime.fromisoformat(item['updated_at']),datetime.fromisoformat(item['event_updated_at']))
        entry.add('last-modified',aware(modified))
        entry.add('sequence',item['event_version'])
        entry.add('status','CANCELLED' if item['event_status']=='cancelled' else 'CONFIRMED')
        calendar.add_component(entry)
    return calendar.to_ical()
