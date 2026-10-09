"""Precision-preserving civil dates and aware scheduler instants."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re

UTC = timezone.utc


def system_zone() -> ZoneInfo:
    # Reload instead of caching: a running notebook may move to another zone.
    from tzlocal import reload_localzone
    return reload_localzone()


def zone(name: str | None = None) -> ZoneInfo:
    try:
        return ZoneInfo(name) if name and name.strip() else system_zone()
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("请填写有效的 IANA 时区，例如 Asia/Shanghai") from exc


def utc_now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("需要含时区的时间")
    return value.astimezone(UTC)


def stamp(value: datetime) -> str:
    return aware(value).isoformat(timespec="microseconds")


def civil(value: str, tz: ZoneInfo, fold: int | None = None) -> datetime:
    """Reject nonexistent or unresolved ambiguous times, without inventing facts."""
    naive = datetime.fromisoformat(value)
    if naive.tzinfo is not None:
        raise ValueError("请分别填写本地时间与 IANA 时区")
    first = naive.replace(tzinfo=tz, fold=0)
    second = naive.replace(tzinfo=tz, fold=1)
    valid = [item for item in (first, second)
             if item.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == naive]
    if not valid:
        raise ValueError("该本地时间因夏令时切换不存在，请核对")
    if first.utcoffset() != second.utcoffset() and fold is None:
        raise ValueError("该本地时间出现两次，请明确选择夏令时折叠顺序")
    return naive.replace(tzinfo=tz, fold=fold or 0)


def reminder_civil(day: date, clock: str, tz: ZoneInfo) -> datetime:
    # Reminder policy is not a fact: move a gap forward to the first valid minute;
    # choose the first occurrence in a fold. Deadline values are never changed.
    naive = datetime.combine(day, time.fromisoformat(clock))
    for offset in range(181):
        try:
            return civil((naive + timedelta(minutes=offset)).isoformat(), tz, 0)
        except ValueError:
            continue
    raise ValueError("无法确定该日的本地提醒时刻")


@dataclass(frozen=True)
class DeadlineTime:
    start: date | datetime
    end: date | datetime
    all_day: bool
    timezone: str

    def days(self, display_zone: ZoneInfo) -> tuple[date, date]:
        if self.all_day:
            return self.start, self.end
        return self.start.astimezone(display_zone).date(), self.end.astimezone(display_zone).date()


def resolve(deadline: dict, *, require_confirmed: bool = True) -> DeadlineTime | None:
    if require_confirmed and (not deadline.get("confirmed_by_user") or deadline.get("uncertain")):
        return None
    precision = deadline.get("precision")
    if precision not in {"date", "minute", "range"}:
        return None
    unit = deadline.get("range_precision") if precision == "range" else precision
    if unit not in {"date", "minute"}:
        return None
    try:
        tz = zone(deadline.get("timezone"))
        end_value = deadline.get("end_value") if precision == "range" else deadline["value"]
        pattern = r'\d{4}-\d{2}-\d{2}' if unit=='date' else r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}'
        if not all(re.fullmatch(pattern,value or '') for value in (deadline['value'],end_value)):
            return None
        if unit == "date":
            start, end = date.fromisoformat(deadline["value"]), date.fromisoformat(end_value)
        else:
            start = civil(deadline["value"], tz, deadline.get("fold"))
            end = civil(end_value, tz, deadline.get("fold"))
        if (start > end) if unit == "date" else (aware(start) > aware(end)):
            return None
        return DeadlineTime(start, end, unit == "date", str(tz))
    except (ValueError, TypeError, KeyError):
        return None


def state(deadline: dict, *, at: datetime | None = None, display_zone: ZoneInfo | None = None) -> dict:
    at = aware(at or utc_now())
    tz = display_zone or system_zone()
    resolved = resolve(deadline)
    if not resolved:
        return {"state": "needs_confirmation", "days_remaining": None, "overdue_days": 0}
    first, last = resolved.days(tz)
    today = at.astimezone(tz).date()
    # Date-only remains valid throughout its civil date. No 23:59 stored anywhere.
    overdue = last < today if resolved.all_day else aware(resolved.end) < at
    days = (last - today).days
    if resolved.all_day:
        label = f'已逾期 {-days} 天' if overdue else '今天' if days==0 else f'还有 {days} 天'
    else:
        seconds = abs((aware(resolved.end)-at).total_seconds())
        distance = f'{int(seconds//86400)} 天' if seconds>=86400 else f'{int(seconds//3600)} 小时' if seconds>=3600 else f'{max(1,int(seconds//60))} 分钟'
        label = ('已逾期 ' if overdue else '还有 ')+distance
    return {"state": "overdue" if overdue else "today" if first <= today <= last else "upcoming", "overdue": overdue,
            "days_remaining": days, "overdue_days": max(0, -days) if overdue else 0,"relative_label":label,
            "all_day": resolved.all_day, "resolved_timezone": resolved.timezone}
