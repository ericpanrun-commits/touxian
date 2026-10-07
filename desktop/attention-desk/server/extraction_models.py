"""Strict candidate schema and a separate, explicit user confirmation contract."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from .personal_models import EvidenceInput, normalize_time

PROMPT_VERSION = "notice-extraction-4"
OUTPUT_SCHEMA_VERSION = "2"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


CandidateId = str


class QuoteCandidate(StrictModel):
    block_id: str | None = Field(default=None, max_length=120)
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    quote: str = Field(min_length=1, max_length=4000)
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(ge=1)


class FactCandidate(StrictModel):
    text: str = Field(min_length=1, max_length=1500)
    evidence_ids: list[str] = Field(max_length=20)


class ActionCandidate(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=300)
    kind: Literal["explicit", "suggested"]
    evidence_ids: list[str] = Field(max_length=20)


class DeadlineCandidate(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    role: str = Field(min_length=1, max_length=180)
    original_text: str = Field(min_length=1, max_length=1000)
    normalized_date: str | None = Field(max_length=40)
    precision: Literal["date", "minute", "unknown"]
    related_action_ids: list[str] = Field(max_length=40)
    evidence_ids: list[str] = Field(max_length=20)
    uncertain: bool
    relation: Literal["current", "previous", "cancelled"]


class EventCandidate(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(max_length=4000)
    category: str = Field(min_length=1, max_length=80)
    audience: FactCandidate | None
    participation: Literal["optional", "required", "unclear"]
    notice_kind: Literal["new", "update", "cancellation", "information"]
    eligibility: list[FactCandidate] = Field(max_length=30)
    deadlines: list[DeadlineCandidate] = Field(max_length=40)
    actions: list[ActionCandidate] = Field(max_length=40)
    materials: list[FactCandidate] = Field(max_length=30)
    locations: list[FactCandidate] = Field(max_length=30)
    uncertainties: list[str] = Field(max_length=30)


class ExtractionOutput(StrictModel):
    events: list[EventCandidate] = Field(max_length=8)
    evidence: list[QuoteCandidate] = Field(max_length=100)


class RawInput(StrictModel):
    original_text: str = Field(min_length=1, max_length=100000)
    title_hint: str = Field(default="", max_length=200)
    source_time: str | None = Field(default=None, max_length=40)

    @field_validator("original_text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请粘贴完整通知")
        return value

    @field_validator("source_time")
    @classmethod
    def source_date(cls, value):
        return normalize_time(value, "date" if len(value.strip()) == 10 else "minute") if value and value.strip() else None


class AnalyzeInput(StrictModel):
    force: bool = False


class ReviewFact(StrictModel):
    text: str = Field(max_length=1500)
    evidence: list[EvidenceInput] = Field(default_factory=list, max_length=20)


class ReviewDeadline(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    selected: bool = True
    role: str = Field(min_length=1, max_length=180)
    value: str = Field(max_length=40)
    precision: Literal["date", "minute"]
    original_text: str = Field(default="", max_length=1000)
    evidence: list[EvidenceInput] = Field(default_factory=list, max_length=20)
    confirmed_by_user: bool = False
    relation: Literal["current", "previous", "cancelled"] = "current"

    @model_validator(mode="after")
    def date_valid(self):
        if self.selected:
            self.value = normalize_time(self.value, self.precision)
            if not self.confirmed_by_user:
                raise ValueError("请核对并确认选中的截止时间")
        return self


class ReviewAction(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    selected: bool = True
    title: str = Field(min_length=1, max_length=300)
    kind: Literal["explicit", "suggested", "manual"]
    deadline_id: str | None = Field(default=None, max_length=64)
    evidence: list[EvidenceInput] = Field(default_factory=list, max_length=20)


class ReviewEvent(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    selected: bool = True
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    category: str = Field(default="其他", min_length=1, max_length=80)
    relevance: Literal["yes", "no", "unclear"] = "unclear"
    participation: Literal["yes", "no", "later"] = "later"
    notice_kind: Literal["new", "update", "cancellation", "information"] = "new"
    audience: ReviewFact | None = None
    eligibility: list[ReviewFact] = Field(default_factory=list, max_length=30)
    deadlines: list[ReviewDeadline] = Field(default_factory=list, max_length=40)
    actions: list[ReviewAction] = Field(default_factory=list, max_length=40)
    materials: list[ReviewFact] = Field(default_factory=list, max_length=30)
    locations: list[ReviewFact] = Field(default_factory=list, max_length=30)
    uncertainties: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("title", "category")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请填写内容")
        return value.strip()


class ConfirmationInput(StrictModel):
    expected_version: StrictInt = Field(ge=1)
    extraction_id: str | None = Field(default=None, max_length=36)
    acknowledged_warnings: bool = False
    events: list[ReviewEvent] = Field(min_length=1, max_length=8)


class DraftInput(StrictModel):
    expected_version: StrictInt = Field(ge=1)
    draft: dict


class ParticipationInput(StrictModel):
    expected_version: StrictInt = Field(ge=1)
    participation: Literal["yes", "no", "later"]
