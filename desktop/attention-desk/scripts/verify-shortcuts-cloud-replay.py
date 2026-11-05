"""Replay a cloud shortcut by Range, not by one whole-file GET.

Samples two small windows by default. ``--assemble`` checks the sha256, but a
file above 50 MiB is built from about 4 MiB ranges and a verified id+sha cache
is reused. A failure does not delete that cache.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.cloud_sync.service import CloudSyncError, CloudSyncService, sample_windows


def replay(service: CloudSyncService, resource_id: str, size: int, *, sha256: str | None = None, assemble: bool = False, window: int = 65536):
    if assemble:
        if not sha256:
            raise CloudSyncError('download_unavailable', '组装核对需要 sha256。')
        path = service.download_capture(resource_id, sha256, size=size)
        return {'mode': 'assemble', 'path': str(path), 'sha256': sha256}
    windows = sample_windows(size, window)
    parts = service.sample_file(resource_id, size, windows)
    return {'mode': 'sample', 'windows': windows, 'lengths': [len(part) for part in parts], 'parts': parts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Range-sample a worker cloud file. Assemble only when asked.')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--worker-key', required=True)
    parser.add_argument('--cache-dir', required=True)
    parser.add_argument('--resource-id', required=True)
    parser.add_argument('--bytes', required=True, type=int)
    parser.add_argument('--sha256')
    parser.add_argument('--window', type=int, default=65536)
    parser.add_argument('--assemble', action='store_true')
    args = parser.parse_args(argv)
    try:
        with CloudSyncService(args.base_url, args.worker_key, args.cache_dir) as service:
            result = replay(service, args.resource_id, args.bytes, sha256=args.sha256, assemble=args.assemble, window=args.window)
    except CloudSyncError as error:
        print(f'{error.code}: {error}', file=sys.stderr)
        return 1
    if result['mode'] == 'sample':
        print('sample', ' '.join(f'{begin}-{end}:{length}' for (begin, end), length in zip(result['windows'], result['lengths'])))
    else:
        print('assemble', result['sha256'], result['path'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
