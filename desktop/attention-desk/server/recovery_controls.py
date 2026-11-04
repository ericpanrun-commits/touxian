"""Persistent, independent holds for local recovery; no network or domain writes."""
from __future__ import annotations

import json
import os
from pathlib import Path


def controls(database_path=None):
    override = os.environ.get('ATTENTION_RECOVERY_CONTROL_PATH')
    if override:
        path = Path(override)
    else:
        if database_path is None:
            from .personal_store import default_database_path
            database_path = default_database_path()
        path = Path(database_path).with_name('recovery-controls.json')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('version') != 1
                or any(type(value.get(key)) is not bool for key in ('hold_cloud_uploads', 'pause_paid_ai'))):
            raise ValueError('invalid recovery controls')
        return {key: value[key] for key in ('hold_cloud_uploads', 'pause_paid_ai')} | {'state': 'configured'}
    except FileNotFoundError:
        return {'hold_cloud_uploads': False, 'pause_paid_ai': False, 'state': 'not_configured'}
    except (OSError, ValueError, UnicodeError):
        # A damaged control file must never silently authorize a transmission.
        return {'hold_cloud_uploads': True, 'pause_paid_ai': True, 'state': 'unreadable_held'}


def cloud_uploads_held(database_path=None):
    return controls(database_path)['hold_cloud_uploads']


def paid_ai_paused(database_path=None):
    return controls(database_path)['pause_paid_ai']
