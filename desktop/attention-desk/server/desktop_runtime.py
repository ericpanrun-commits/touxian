"""Small, dependency-free identity/readiness contract for the desktop launcher."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
APP_ID = 'attention-desk'


def workspace_id(root=APP_ROOT):
    return hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode()).hexdigest()[:24]


def bundle_ready(root=APP_ROOT):
    dist = Path(root).resolve()/'dist'
    try:
        html = (dist/'index.html').read_text(encoding='utf-8')
        if 'id="root"' not in html:
            return False
        manifest = json.loads((dist/'.vite/manifest.json').read_text(encoding='utf-8'))
        seen = set()

        def available(key):
            if key in seen:
                return True
            seen.add(key)
            entry = manifest[key]
            for name in [entry['file'], *entry.get('css', [])]:
                path = (dist/name).resolve()
                if not path.is_relative_to(dist) or not path.is_file():
                    return False
            return all(available(key) for key in entry.get('imports', []))

        return available('index.html')
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return False


def health_identity(root=APP_ROOT):
    return {'application': APP_ID, 'workspace_id': workspace_id(root),
            'process_id': os.getpid(), 'background': os.environ.get('ATTENTION_DESKTOP_BACKGROUND')=='1',
            'frontend_ready': bundle_ready(root)}
