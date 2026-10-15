"""Environment-only credentials; public status never exposes a key or push URL."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from dotenv import dotenv_values


@dataclass(frozen=True)
class BarkConfig:
    enabled: bool = False
    server_url: str = field(default='https://api.day.app', repr=False)
    device_key: str = field(default='', repr=False)
    group: str = 'ZQU'
    sound: str = ''
    error: str = ''

    @classmethod
    def from_env(cls, env=None):
        load_error = False
        if env is None:
            root = Path(__file__).resolve().parents[1]
            # Process environment wins; server/.env is the documented backend file.
            try:
                env = {**dotenv_values(root.parent.parent / '.env', interpolate=False),
                       **dotenv_values(root.parent / '.env', interpolate=False),
                       **dotenv_values(root / '.env', interpolate=False), **os.environ}
            except (OSError, UnicodeError):
                env, load_error = dict(os.environ), True
        env = {k: str(v or '').strip() for k, v in env.items() if k.startswith('BARK_')}
        server = env.get('BARK_SERVER_URL') or 'https://api.day.app'
        key = env.get('BARK_DEVICE_KEY', '')
        error = 'invalid_config' if load_error else ''
        try:
            parsed = urlsplit(server)
            cls._validate_server(parsed)
            if parsed.query or parsed.fragment:
                raise ValueError()
            prefix = parsed.path.rstrip('/')
            override = env.get('BARK_PUSH_URL', '')
            if override:
                push = urlsplit(override)
                cls._validate_server(push)
                path = push.path
                if push.netloc == parsed.netloc and prefix and path.startswith(prefix + '/'):
                    path = path[len(prefix):]
                else:
                    prefix = ''
                parts = path.strip('/').split('/')
                key = unquote(parts[0])
                parsed = push
            if key in {'your_bark_device_key_here', '在这里填写', '...', 'push'}:
                key = ''
            if key and not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', key):
                raise ValueError()
            server = urlunsplit((parsed.scheme, parsed.netloc, prefix, '', ''))
        except (ValueError, TypeError):
            server, key, error = 'https://api.day.app', '', 'invalid_config'
        flag = env.get('BARK_ENABLED', '').lower()
        if flag and flag not in {'1', '0', 'true', 'false', 'yes', 'no', 'on', 'off'}:
            error = 'invalid_config'
        enabled = flag in {'1', 'true', 'yes', 'on'} if flag else bool(key)
        return cls(enabled, server, key, env.get('BARK_GROUP') or 'ZQU', env.get('BARK_DEFAULT_SOUND', ''), error)

    @staticmethod
    def _validate_server(parsed):
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                or re.search(r'[\x00-\x20\x7f\\]', parsed.geturl())):
            raise ValueError()
        parsed.port  # Validate malformed ports without echoing the input.

    @property
    def configured(self):
        return bool(self.device_key) and not self.error

    @property
    def endpoint(self):
        return self.server_url.rstrip('/') + '/push'

    def public(self):
        return {'configured': self.configured, 'environment_enabled': self.enabled,
                'server': urlsplit(self.server_url).hostname, 'device_key_configured': self.configured,
                'configuration_error': self.error or None}
