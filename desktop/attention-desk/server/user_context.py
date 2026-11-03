"""Shared, versioned user facts. Unknown roles never mean confirmed false."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .personal_store import PersonalError, encode


class RoleAssertion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: Literal['true', 'false', 'unknown'] = 'unknown'
    confirmed: bool = False


class UserContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile_version: int = Field(default=1, ge=1)
    school: str = Field(default='', max_length=120)
    college: str = Field(default='', max_length=120)
    major: str = Field(default='', max_length=80)
    entry_year: int | None = Field(default=None, ge=1900, le=2200)
    class_name: str = Field(default='', max_length=120)
    role_assertions: dict[str, RoleAssertion] = Field(default_factory=dict, max_length=50)
    courses: list[str] = Field(default_factory=list, max_length=50)
    interests: list[str] = Field(default_factory=list, max_length=30)
    projects: list[str] = Field(default_factory=list, max_length=30)
    wechat_self_aliases: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default='', max_length=1000)
    confirmed_fields: list[str] = Field(default_factory=list, max_length=30)

    def role(self, name):
        assertion = self.role_assertions.get(name)
        return assertion.value if assertion and assertion.confirmed else 'unknown'

    def fingerprint(self):
        return hashlib.sha256(encode(self.model_dump(exclude={'profile_version'})).encode()).hexdigest()


class UserContextStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / 'user-context.json'

    def read(self):
        if self.path.exists():
            try:
                return UserContext.model_validate(json.loads(self.path.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                raise PersonalError('用户画像无法读取，未覆盖原文件。', 'profile_error', 503) from None
        # Initial suggestions, never inferred role assertions.
        values = {}
        official = Path(os.environ.get('ATTENTION_OFFICIAL_PROFILE') or Path(__file__).parent / 'config/official_profile.json')
        try:
            original = json.loads(official.read_text(encoding='utf-8'))
            values = {k: original[k] for k in ('school','college','major','entry_year') if original.get(k)}
        except (OSError, ValueError):
            pass
        try:
            legacy = json.loads((self.directory / 'profile.json').read_text(encoding='utf-8'))
            for key in ('college', 'major', 'interests', 'notes'):
                if legacy.get(key):
                    values[key] = legacy[key]
            if str(legacy.get('grade','')).isdigit():
                values['entry_year'] = int(legacy['grade'])
        except (OSError, ValueError, TypeError):
            pass
        return UserContext.model_validate(values)

    def save(self, value: UserContext, expected_version: int):
        if self.read().profile_version != expected_version:
            raise PersonalError('画像已改变，请重新读取。', 'conflict', 409)
        saved = value.model_copy(update={'profile_version': expected_version + 1})
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid4().hex + '.tmp')
        try:
            temporary.write_text(encode(saved.model_dump()), encoding='utf-8')
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return saved
