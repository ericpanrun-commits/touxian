"""Small, explicit policy; notification times never change Deadline precision."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from typing import Literal

from pydantic import Field, field_validator

from ..personal_models import InputModel
from ..planning.time import aware, reminder_civil, resolve, stamp, zone


class ReminderSettingsInput(InputModel):
    expected_version: int = Field(ge=1)
    enabled: bool = True
    date_offsets: list[int] = Field(default_factory=lambda:[3,1,0],max_length=3)
    minute_offsets: list[int] = Field(default_factory=lambda:[24,2],max_length=2)
    daily_time: str = '09:00'
    privacy: Literal['full','title','hidden'] = 'title'
    grace_hours: int = Field(default=6,ge=0,le=24)

    @field_validator('date_offsets','minute_offsets')
    @classmethod
    def supported_offsets(cls,value,info):
        allowed = {0,1,3} if info.field_name == 'date_offsets' else {2,24}
        if len(set(value)) != len(value) or any(item not in allowed for item in value):
            raise ValueError('请选择支持的提醒间隔，不能重复')
        return sorted(value,reverse=True)

    @field_validator('daily_time')
    @classmethod
    def clock(cls,value):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):
            raise ValueError('提醒时刻请填写 HH:mm')
        return value


def fact_key(deadline):
    value = {key:deadline.get(key) for key in ('value','end_value','precision','range_precision','timezone','fold')}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def schedules(deadline,settings):
    fact = resolve(deadline)
    if not fact or not settings['enabled']:
        return []
    fingerprint = fact_key(deadline)
    result = []
    offsets = settings['date_offsets'] if fact.all_day else settings['minute_offsets']
    for offset in offsets:
        if fact.all_day:
            instant = reminder_civil(fact.end-timedelta(days=offset),settings['daily_time'],zone(fact.timezone))
            kind = f"date:{offset}:{settings['daily_time']}"
        else:
            # Duration offsets are elapsed hours across DST, not wall clock hours.
            instant = aware(fact.end)-timedelta(hours=offset)
            kind = f'minute:{offset}'
        key = hashlib.sha256(f"{deadline['id']}:{fingerprint}:{kind}".encode()).hexdigest()
        result.append({'dedupe_key':key,'fact_key':fingerprint,'reminder_type':kind,'scheduled_at':stamp(instant)})
    return result
