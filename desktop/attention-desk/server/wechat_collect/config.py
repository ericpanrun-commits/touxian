"""One versioned configuration owner. Secrets never belong in this file."""
from __future__ import annotations

import os
import hashlib
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..personal_store import PersonalError


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ChatConfig(StrictConfig):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    name: str = Field(min_length=1, max_length=120)
    chatroom_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]+@chatroom$')
    priority: Literal['high','medium','low'] = 'high'
    aliases: list[str] = Field(default_factory=list, max_length=10)
    core: bool = True
    enabled: bool = True
    trusted_senders: list[str] = Field(default_factory=list, max_length=30)

    @field_validator('name', 'aliases', 'trusted_senders')
    @classmethod
    def clean_names(cls, value):
        values = value if isinstance(value, list) else [value]
        if any(not v.strip() or len(v) > 120 or '\n' in v for v in values):
            raise ValueError('群名和别名必须是非空单行文字')
        return [v.strip() for v in values] if isinstance(value, list) else value.strip()


class QuietHours(StrictConfig):
    start: str = '00:00'
    end: str = '06:00'

    @field_validator('start', 'end')
    @classmethod
    def clock_time(cls, value):
        from datetime import time
        if len(value) != 5:
            raise ValueError('请使用 HH:mm')
        time.fromisoformat(value)
        return value


class ContextFollowup(StrictConfig):
    enabled: bool = True
    delay_seconds: int = Field(default=120, ge=30, le=120)
    max_per_batch: Literal[1] = 1


class BudgetConfig(StrictConfig):
    http_daily: int = Field(default=30, ge=1, le=1000)
    tokens_daily: int = Field(default=100_000, ge=4096, le=10_000_000)
    vision_daily: int = Field(default=5, ge=0, le=100)
    concurrency: Literal[1] = 1
    batches_per_cycle: int = Field(default=4, ge=1, le=10)


class WeChatConfig(StrictConfig):
    enabled: bool = False
    allowed_chats: list[str] = Field(default_factory=list, max_length=30)
    allowed_chatrooms: list[str] = Field(default_factory=list, max_length=30)
    timezone: Literal['Asia/Shanghai'] = 'Asia/Shanghai'
    scan_interval_minutes: Literal[15] = 15
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    catch_up_at: Literal['06:00'] = '06:00'
    core_reconcile_at: Literal['23:00'] = '23:00'
    resume_delay_seconds: int = Field(default=20, ge=10, le=30)
    idle_seconds: int = Field(default=120, ge=120, le=3600)
    focus_mode: Literal['idle_only', 'background_only'] = 'idle_only'
    bootstrap_hours: Literal[24] = 24
    context_followup: ContextFollowup = Field(default_factory=ContextFollowup)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    priority_chats: list[ChatConfig] = Field(default_factory=list, max_length=30)

    def allows(self, chat):
        return chat.name in self.allowed_chats and (not chat.chatroom_id or chat.chatroom_id in self.allowed_chatrooms)

    @model_validator(mode='after')
    def invariants(self):
        if self.quiet_hours.start != '00:00' or self.quiet_hours.end != '06:00':
            raise ValueError('本版本静默时段固定为 00:00–06:00')
        if len({chat.id for chat in self.priority_chats}) != len(self.priority_chats):
            raise ValueError('群逻辑 ID 不能重复')
        ids=[chat.chatroom_id for chat in self.priority_chats if chat.chatroom_id]
        if len(set(ids))!=len(ids) or len(set(self.allowed_chatrooms))!=len(self.allowed_chatrooms):
            raise ValueError('授权群 ID 不能重复')
        if any(not __import__('re').fullmatch(r'[A-Za-z0-9_-]+@chatroom',value) for value in self.allowed_chatrooms):
            raise ValueError('授权群 ID 格式无效')
        if any(not name.strip() or name != name.strip() or '\n' in name or len(name) > 120 for name in self.allowed_chats):
            raise ValueError('allowed_chats 必须是精确的非空群名')
        if len(set(self.allowed_chats)) != len(self.allowed_chats):
            raise ValueError('allowed_chats 不能重复')
        configured = {chat.name for chat in self.priority_chats}
        for name in self.allowed_chats:
            if name not in configured:
                self.priority_chats.append(ChatConfig(id='db_' + hashlib.sha256(name.encode()).hexdigest()[:16], name=name))
        return self


class SettingsDocument(StrictConfig):
    version: Literal[1] = 1
    revision: int = Field(default=1, ge=1)
    wechat: WeChatConfig = Field(default_factory=WeChatConfig)


class ConfigStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self) -> SettingsDocument:
        if not self.path.exists():
            return SettingsDocument()
        try:
            return SettingsDocument.model_validate(yaml.safe_load(self.path.read_text(encoding='utf-8')))
        except (OSError, ValueError, yaml.YAMLError):
            raise PersonalError('微信配置无法读取；采集已暂停，原配置没有覆盖。', 'wechat_config', 503) from None

    def save(self, document: SettingsDocument, expected_revision: int):
        # Serialised by the service; revision prevents stale browser overwrites.
        if self.read().revision != expected_revision:
            raise PersonalError('微信设置已更新，请重新读取。', 'conflict', 409)
        saved = document.model_copy(update={'revision': expected_revision + 1})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid4().hex + '.tmp')
        try:
            temporary.write_text(yaml.safe_dump(saved.model_dump(), allow_unicode=True, sort_keys=False), encoding='utf-8')
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return saved
