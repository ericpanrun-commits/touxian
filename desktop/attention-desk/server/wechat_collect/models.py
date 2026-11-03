"""Immutable observations and strict, separate WeChat analysis contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator, AfterValidator


@dataclass(frozen=True)
class ObservedMessage:
    text: str
    sender: str = ''
    message_type: str = 'text'
    source_time: str | None = None
    time_reliability: str = 'unknown'
    attachment_identity: str = ''
    media_path: str | None = None
    sender_reliability: str = 'unknown'
    database_identity: dict | None = None


@dataclass(frozen=True)
class Observation:
    chat_id: str
    account_scope: str
    messages: tuple[ObservedMessage, ...]
    covers_tail: bool = False
    bootstrap_complete: bool = False
    pages: int = 1
    capability: str = 'not_tested'
    warnings: tuple[str, ...] = ()
    database_cursor: dict | None = None


@dataclass
class Reconciliation:
    matched: dict[int, str] = field(default_factory=dict)
    new_indices: list[int] = field(default_factory=list)
    complete: bool = False
    reason: str = 'missing_anchor'


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


def unique_refs(value):
    if len(value)!=len(set(value)):
        raise ValueError('Duplicate references')
    return value


Refs = Annotated[list[Annotated[str,Field(min_length=1,max_length=120)]],AfterValidator(unique_refs),Field(max_length=40,json_schema_extra={'uniqueItems':True})]


class TimeValue(StrictModel):
    value: str | None
    end_value: str | None
    precision: Literal['date', 'minute', 'range', 'unknown']
    range_precision: Literal['date', 'minute'] | None
    timezone: Literal['Asia/Shanghai']
    original_text: str = Field(max_length=500)
    anchor_message_id: str | None
    uncertain: bool
    evidence_ids: Refs = Field(max_length=40)

    @model_validator(mode='after')
    def valid_shape(self):
        from ..personal_models import normalize_time
        if self.precision == 'unknown':
            if self.value is not None or self.end_value is not None or not self.uncertain:
                raise ValueError('Unknown dates must stay unknown')
        else:
            unit = self.range_precision if self.precision == 'range' else self.precision
            if unit is None or self.value is None:
                raise ValueError('Missing date precision')
            normalize_time(self.value, unit)
            if self.precision == 'range':
                if not self.end_value or self.value > self.end_value:
                    raise ValueError('Invalid date range')
                normalize_time(self.end_value, unit)
            elif self.end_value is not None or self.range_precision is not None:
                raise ValueError('Unexpected range endpoint')
        return self


class WeChatDeadline(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=180)
    relation: Literal['current', 'previous', 'cancelled']
    time: TimeValue


class WeChatAction(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    kind: Literal['explicit', 'suggested']
    deadline_ids: Refs = Field(max_length=40)
    evidence_ids: Refs = Field(max_length=40)


class EvidenceMapping(StrictModel):
    field: str = Field(min_length=1, max_length=160)
    evidence_ids: Refs = Field(max_length=40)


class WeChatEvidence(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    message_id: str = Field(min_length=1)
    block_id: str = Field(min_length=1, max_length=120)
    quote: str = Field(min_length=1, max_length=4000)
    start: int = Field(ge=0)
    end: int = Field(ge=1)


class WeChatItem(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    important: bool
    relevant: bool | None
    action_required: bool
    attention_level: Literal['action','fyi','ignore']
    information_complete: bool
    await_more_context: bool
    category: Literal['deadline', 'event', 'task', 'notice', 'course_change', 'exam', 'registration', 'competition', 'meeting', 'administrative', 'other']
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(max_length=2000)
    start_time: TimeValue | None
    end_time: TimeValue | None
    deadline: TimeValue | None
    location: str | None = Field(max_length=500)
    action: str | None = Field(max_length=500)
    target_people: list[str] = Field(max_length=20)
    priority: Literal['P0', 'P1', 'P2', 'P3']
    confidence: float = Field(ge=0, le=1)
    evidence_message_ids: Refs = Field(max_length=40)
    reason: str = Field(max_length=1000)
    time_text: str = Field(max_length=1000)
    needs_review: bool
    participation: Literal['required', 'optional', 'unclear']
    notice_kind: Literal['new', 'update', 'cancellation', 'information']
    related_candidate_id: str | None
    deadlines: list[WeChatDeadline] = Field(max_length=20)
    actions: list[WeChatAction] = Field(max_length=20)
    evidence_mapping: list[EvidenceMapping] = Field(max_length=60)
    uncertainties: list[str] = Field(max_length=20)


class WeChatOutput(StrictModel):
    schema_version: Literal['wechat.v1']
    batch_id: str = Field(min_length=1)
    items: list[WeChatItem] = Field(max_length=8)
    evidence: list[WeChatEvidence] = Field(max_length=100)
