"""Fail-closed runtime identity and production authority. Never exposes secrets."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


def environment():
    root = Path(__file__).resolve().parent
    try:
        return {**dotenv_values(root.parent.parent / '.env', interpolate=False),
                **dotenv_values(root.parent / '.env', interpolate=False),
                **dotenv_values(root / '.env', interpolate=False), **os.environ}
    except (OSError, UnicodeError):
        raise ValueError('运行配置无法读取，请检查环境文件权限和编码。') from None


def flag(values, name, default=False):
    value = str(values.get(name, str(default))).strip().lower()
    if value not in {'true', 'false', '1', '0', 'yes', 'no', 'on', 'off'}:
        raise ValueError('运行配置中的布尔选项无效。')
    return value in {'true', '1', 'yes', 'on'}


@dataclass(frozen=True)
class RuntimeConfig:
    environment: str = 'development'
    role: str = 'desktop'
    instance_id: str = 'local-desktop'
    auto_sync: bool = False
    notification_authority: bool = False
    bootstrap: bool = False
    dry_run: bool = False
    data_root: str = '/data'
    database_path: str = '/data/production.sqlite3'
    expected_database_id: str = ''
    admin_token: str = field(default='', repr=False)
    allowed_origins: tuple[str, ...] = ()
    storage_verified: bool = False
    poll_seconds: float = 30
    backup_keep: int = 7

    @classmethod
    def from_env(cls, values=None):
        values = environment() if values is None else values
        mode = str(values.get('APP_ENV') or 'development').strip()
        role = str(values.get('APP_RUNTIME') or 'desktop').strip()
        if mode not in {'development', 'production'} or role not in {'desktop', 'cloud'}:
            raise ValueError('APP_ENV 或 APP_RUNTIME 无效。')
        instance = str(values.get('INSTANCE_ID') or ('production-fly' if role == 'cloud' else 'local-desktop'))
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', instance):
            raise ValueError('INSTANCE_ID 格式无效。')
        origins = tuple(v.strip() for v in str(values.get('CORS_ALLOWED_ORIGINS') or '').split(',') if v.strip())
        for origin in origins:
            p = urlsplit(origin)
            if p.scheme not in {'http', 'https'} or not p.hostname or p.username or p.password or p.path or p.query or p.fragment or '*' in origin:
                raise ValueError('CORS 只能使用明确的 HTTP/HTTPS origin。')
        try:
            poll = float(values.get('OFFICIAL_TICK_SECONDS') or 30)
            keep = int(values.get('BACKUP_KEEP') or 7)
            if not 1 <= poll <= 60 or not 1 <= keep <= 14:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('调度周期或备份保留数量无效。') from None
        return cls(mode, role, instance, flag(values, 'AUTO_SYNC_ENABLED'),
                   flag(values, 'PRODUCTION_NOTIFICATION_AUTHORITY'), flag(values, 'CLOUD_BOOTSTRAP_MODE', role == 'cloud'),
                   flag(values, 'NOTIFICATION_DRY_RUN', role == 'cloud'), str(values.get('APP_DATA_DIR') or '/data'),
                   str(values.get('ATTENTION_PERSONAL_DB') or '/data/production.sqlite3'), str(values.get('PRODUCTION_DATABASE_ID') or ''),
                   str(values.get('CLOUD_ADMIN_TOKEN') or ''), origins, False, poll, keep)

    @property
    def cloud_production(self):
        return self.environment == 'production' and self.role == 'cloud'

    @property
    def automatic_sync_allowed(self):
        return self.auto_sync and not self.bootstrap and (self.role == 'desktop' and self.environment == 'development'
                                                       or self.cloud_production and self.storage_verified and (self.notification_authority or self.dry_run))

    @property
    def automatic_bark_allowed(self):
        return (self.cloud_production and self.storage_verified and self.auto_sync and self.notification_authority
                and not self.bootstrap and not self.dry_run)

    @property
    def preview_allowed(self):
        return self.cloud_production and self.storage_verified and (self.bootstrap or self.dry_run)

    @property
    def windows_allowed(self):
        return self.role == 'desktop' and self.environment == 'development' and os.name == 'nt'

    def public(self):
        return {'environment': self.environment, 'runtime': self.role, 'instance': self.instance_id,
                'automatic_sync': self.automatic_sync_allowed, 'automatic_bark': self.automatic_bark_allowed,
                'production_authority': self.automatic_bark_allowed, 'bootstrap': self.bootstrap,
                'dry_run': self.dry_run, 'database_persistent': self.storage_verified,
                'cloud_deployed': self.cloud_production and self.storage_verified}
