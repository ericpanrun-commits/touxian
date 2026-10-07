"""Small, verified VSB source configurations; no runtime source discovery."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceFailure(Exception):
    def __init__(self, code: str, message: str, *, status='error'):
        super().__init__(message)
        self.code, self.status = code, status


def canonical_url(url: str, allowed_hosts: list[str]) -> str:
    try:
        if not isinstance(url, str) or len(url) > 4096 or re.search(r'[\x00-\x20\x7f\\]', url):
            raise ValueError()
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        if (parsed.scheme not in {'https', 'http'} or host not in allowed_hosts or parsed.username or parsed.password
                or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}', host)):
            raise ValueError()
        if parsed.port not in {None, 80 if parsed.scheme == 'http' else 443}:
            raise ValueError()
    except ValueError as exc:
        raise SourceFailure('unsafe_url', '地址不在该来源允许的公开 HTTP/HTTPS 范围内。') from exc
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'fbclid', 'gclid'}]
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or '/', urlencode(sorted(query)), ''))


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{1,70}$')
    name: str = Field(min_length=1, max_length=120)
    school: str = Field(min_length=1, max_length=80)
    department: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    list_url: str
    allowed_hosts: list[str] = Field(min_length=1, max_length=5)
    parser_type: Literal['vsb_school', 'vsb_div', 'vsb_table']
    enabled: bool = True
    polling_interval: int = Field(default=43200, ge=3600, le=604800)
    max_items: int = Field(default=20, ge=1, le=50)
    max_pages: int = Field(default=3, ge=1, le=5)

    @field_validator('allowed_hosts')
    @classmethod
    def hosts(cls, value):
        if any(not re.fullmatch(r'(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,}', host) or host.endswith('.localhost') for host in value):
            raise ValueError('allowed_hosts must contain exact public DNS names')
        return list(dict.fromkeys(value))

    @model_validator(mode='after')
    def address(self):
        canonical_url(self.list_url, self.allowed_hosts)
        return self


def configuration_path():
    return Path(os.environ.get('ATTENTION_OFFICIAL_SOURCES') or Path(__file__).with_name('config') / 'official_sources.json')


def load_sources(path=None):
    payload = json.loads(Path(path or configuration_path()).read_text(encoding='utf-8'))
    if payload.get('version') != 1 or not isinstance(payload.get('sources'), list) or not 1 <= len(payload['sources']) <= 5:
        raise ValueError('来源配置需包含 1—5 个经过验证的来源。')
    sources = [SourceConfig.model_validate(value) for value in payload['sources']]
    if len({s.id for s in sources}) != len(sources) or len({s.school for s in sources}) != 1:
        raise ValueError('当前仅支持一个学校，来源编号不能重复。')
    return sources
