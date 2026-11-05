"""Assemble a cloud capture with the real Windows ``CloudSync`` downloader.

Files above 50 MiB, and any file the server answers with 416, are read in
``PART_BYTES`` ranges. A verified ``{capture}-{sha256}.download`` cache is
reused. A failure removes only the incomplete ``*.partial`` file.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def replay(sync, capture):
    """Return verified files from ``CloudSync.download_capture``."""
    files = asyncio.run(sync.download_capture(capture))
    stored = [(filename, mime, str(path)) for filename, mime, path in files]
    filename, mime, path = stored[0]
    return {
        'mode': 'assemble',
        'path': path,
        'filename': filename,
        'mime': mime,
        'sha256': capture['files'][0]['sha256'],
        'files': stored,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Assemble a worker cloud file with CloudSync.download_capture.')
    parser.add_argument('--worker-key', required=True)
    parser.add_argument('--cache-dir', required=True)
    parser.add_argument('--capture-id', required=True)
    parser.add_argument('--resource-id', required=True)
    parser.add_argument('--bytes', required=True, type=int)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--filename', default='capture.bin')
    parser.add_argument('--mime', default='application/octet-stream')
    parser.add_argument('--base-url', help='Override the eric-app-api root. Defaults to the CloudSync endpoint.')
    args = parser.parse_args(argv)
    import httpx

    import server.cloud_sync.service as cloud_sync
    from server.cloud_sync.service import CloudSync, SyncError

    if args.base_url:
        cloud_sync.ENDPOINT = args.base_url.rstrip('/')
    sync = CloudSync.__new__(CloudSync)
    sync.cache = Path(args.cache_dir)
    sync.cache.mkdir(parents=True, exist_ok=True)
    sync.config = {'worker_key': args.worker_key}
    sync.client = httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10), follow_redirects=False)
    capture = {
        'id': args.capture_id,
        'files': [{
            'resource_id': args.resource_id,
            'sha256': args.sha256,
            'bytes': args.bytes,
            'filename': args.filename,
            'mime': args.mime,
        }],
    }
    try:
        result = replay(sync, capture)
    except SyncError as error:
        print(error.code, file=sys.stderr)
        return 1
    finally:
        asyncio.run(sync.client.aclose())
    print('assemble', result['sha256'], result['path'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
