"""Validated input contracts for the manual-only personal workbench."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

EventStatus = Literal["needs_review", "needs_action", "in_progress", "done", "cancelled"]
TaskOrigin = Literal["explicit", "suggested", "manual"]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def normalize_time(value: str, precision: str) -> str:
    value = value.strip().replace(" ", "T")
    if precision == "date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("日期请填写 YYYY-MM-DD；不会自动补上时间")
        date.fromisoformat(value)
    else:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", value):
            raise ValueError("时间请填写 YYYY-MM-DD HH:mm")
        datetime.fromisoformat(value)
    return value


class EvidenceInput(InputModel):
    block_id: str | None = Field(default=None, max_length=120)
    quote: str = Field(min_length=1, max_length=4000)
    start_offset: StrictInt | None = Field(default=None, ge=0)
    end_offset: StrictInt | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def paired_offsets(self):
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("引用起止位置需要同时提供")
        return self


class CollectionInput(InputModel):
    original_text: str = Field(min_length=1, max_length=100000)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    category: str = Field(default="其他", min_length=1, max_length=80)
    source_time: str | None = Field(default=None, max_length=40)

    @field_validator("title", "category")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写内容")
        return value.strip()

    @field_validator("original_text")
    @classmethod
    def preserve_original(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请粘贴原始通知")
        return value  # Whitespace and line breaks are part of the immutable original.

    @field_validator("source_time")
    @classmethod
    def valid_source_time(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        return normalize_time(value, "date" if len(value.strip()) == 10 else "minute")


class MutationInput(InputModel):
    expected_version: StrictInt = Field(ge=1)


class EventEditInput(MutationInput):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    category: str = Field(min_length=1, max_length=80)

    @field_validator("title", "category")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return CollectionInput.nonblank(value)


class StatusInput(MutationInput):
    status: EventStatus
    confirm_incomplete: bool = False


class DeadlineInput(MutationInput):
    label: str = Field(min_length=1, max_length=180)
    value: str = Field(min_length=1, max_length=40)
    precision: Literal["date", "minute"] = "date"
    original_text: str = Field(default="", max_length=1000)
    confirmed_by_user: bool = True
    evidence: EvidenceInput | None = None

    @field_validator("label")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return CollectionInput.nonblank(value)

    @model_validator(mode="after")
    def validate_value(self):
        self.value = normalize_time(self.value, self.precision)
        return self


class TaskInput(MutationInput):
    title: str = Field(min_length=1, max_length=300)
    origin: TaskOrigin = "manual"
    deadline_id: str | None = Field(default=None, max_length=36)
    evidence: EvidenceInput | None = None

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return CollectionInput.nonblank(value)


class TaskEditInput(MutationInput):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    status: Literal["todo", "done"] | None = None
    origin: TaskOrigin | None = None
    deadline_id: str | None = Field(default=None, max_length=36)
    order: StrictInt | None = Field(default=None, ge=0)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        return CollectionInput.nonblank(value) if value is not None else None

    @model_validator(mode="after")
    def disallow_null_values(self):
        for name in ("title", "status", "origin", "order"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} 不能为空")
        return self


class EvidenceBindInput(MutationInput):
    field_path: str = Field(min_length=1, max_length=100)
    evidence: EvidenceInput
