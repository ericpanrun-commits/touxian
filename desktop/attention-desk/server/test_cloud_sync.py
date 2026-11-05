import hashlib
from pathlib import Path

import httpx
import pytest

from server.cloud_sync.service import CHUNK_BYTES, FULL_DOWNLOAD_LIMIT, CloudSyncError, CloudSyncService


SHA = 'ab' * 32
OTHER = 'cd' * 32
RESOURCE = 'textbook:demo'
BASE = 'https://example.test/functions/v1/eric-app-api'
KEY = 'ework_' + 'k' * 43


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def service(tmp_path: Path, handler, **kwargs) -> CloudSyncService:
    options = {'range_limit': 8, 'chunk_bytes': 4, **kwargs}
    return CloudSyncService(BASE, KEY, tmp_path, transport=httpx.MockTransport(handler), **options)


def content_url(resource_id=RESOURCE):
    return BASE + '/worker/files/' + resource_id + '/content'


def test_limits_match_the_edge_function_contract():
    assert FULL_DOWNLOAD_LIMIT == 50 * 1024 * 1024
    assert CHUNK_BYTES == 4 * 1024 * 1024


def test_matching_cache_is_reused_and_kept_when_ack_fails(tmp_path):
    payload = b'cached-bytes'
    sha = digest(payload)
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError('cache hit must not download')

    sync = service(tmp_path, handler)
    cached = sync.cache_file(RESOURCE, sha)
    cached.parent.mkdir(parents=True)
    cached.write_bytes(payload)
    leftover = cached.with_name(cached.name + '.partial')
    leftover.write_bytes(b'stale-partial')

    def fail(_path):
        raise CloudSyncError('ack_failed', 'ack failed')

    with pytest.raises(CloudSyncError):
        sync.download_capture(RESOURCE, sha, size=len(payload), ack=fail)
    assert cached.read_bytes() == payload
    assert not leftover.exists()
    assert calls == []
    sync.close()


def test_large_download_uses_ranges_and_keeps_cache_on_later_failure(tmp_path):
    payload = b'0123456789'  # 10 bytes, limit 8, chunks of 4 -> 0-3, 4-7, 8-9
    sha = digest(payload)
    calls = []

    def handler(request):
        calls.append((request.headers.get('range'), request.headers.get('x-eric-worker-key')))
        assert request.headers['range']
        begin, end = request.headers['range'].removeprefix('bytes=').split('-')
        begin, end = int(begin), int(end)
        body = payload[begin:end + 1]
        return httpx.Response(206, content=body, headers={'Content-Range': f'bytes {begin}-{end}/{len(payload)}'})

    sync = service(tmp_path, handler)
    path = sync.download_capture(RESOURCE, sha, size=len(payload))
    assert path.read_bytes() == payload
    assert [item[0] for item in calls] == ['bytes=0-3', 'bytes=4-7', 'bytes=8-9']
    assert all(item[1] == KEY for item in calls)

    def fail(_path):
        raise RuntimeError('ack failed after a good file')

    with pytest.raises(RuntimeError):
        sync.download_capture(RESOURCE, sha, size=len(payload), ack=fail)
    assert path.read_bytes() == payload
    assert len(calls) == 3
    sync.close()


def test_http_416_falls_back_to_ranges_and_a_failed_chunk_keeps_other_cache(tmp_path):
    payload = b'abcdefghij'
    sha = digest(payload)
    neighbor = sync_neighbor(tmp_path)
    calls = []

    def handler(request):
        calls.append(request.headers.get('range'))
        if request.headers.get('range') is None:
            return httpx.Response(416, json={'detail': {'code': 'range_required', 'message': 'range'}})
        begin, end = request.headers['range'].removeprefix('bytes=').split('-')
        begin, end = int(begin), int(end)
        if begin >= 4:
            return httpx.Response(503)
        body = payload[begin:end + 1]
        return httpx.Response(206, content=body, headers={'Content-Range': f'bytes {begin}-{end}/{len(payload)}'})

    sync = service(tmp_path, handler, range_limit=50)
    with pytest.raises(CloudSyncError) as error:
        sync.download_capture(RESOURCE, sha, size=len(payload))
    assert error.value.code == 'download_unavailable'
    cached = sync.cache_file(RESOURCE, sha)
    partial = cached.with_name(cached.name + '.partial')
    assert not cached.exists()
    assert not partial.exists()
    assert neighbor.read_bytes() == b'keep-me'
    assert calls[0] is None
    assert calls[1] == 'bytes=0-3'
    sync.close()


def test_small_file_still_uses_one_get(tmp_path):
    payload = b'12345'
    sha = digest(payload)
    calls = []

    def handler(request):
        calls.append(request.headers.get('range'))
        return httpx.Response(200, content=payload, headers={'Content-Length': str(len(payload))})

    sync = service(tmp_path, handler, range_limit=50)
    path = sync.download_capture(RESOURCE, sha, size=len(payload))
    assert path.read_bytes() == payload
    assert calls == [None]
    sync.close()


def test_unknown_size_reads_manifest_then_ranges(tmp_path):
    payload = b'0123456789'
    sha = digest(payload)

    def handler(request):
        if request.url.path.endswith('/manifest'):
            return httpx.Response(200, json={'bytes': len(payload), 'sha256': sha})
        assert request.headers.get('range')
        begin, end = request.headers['range'].removeprefix('bytes=').split('-')
        begin, end = int(begin), int(end)
        body = payload[begin:end + 1]
        return httpx.Response(206, content=body, headers={'Content-Range': f'bytes {begin}-{end}/{len(payload)}'})

    sync = service(tmp_path, handler)
    assert sync.download_capture(RESOURCE, sha).read_bytes() == payload
    sync.close()


def test_replay_script_samples_with_range_and_assembles_without_a_full_get(tmp_path):
    import importlib.util
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'verify-shortcuts-cloud-replay.py'
    spec = importlib.util.spec_from_file_location('verify_shortcuts_cloud_replay', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    payload = b'0123456789abcdef'
    sha = digest(payload)
    calls = []

    def handler(request):
        calls.append(request.headers.get('range'))
        assert request.headers.get('range')
        begin, end = request.headers['range'].removeprefix('bytes=').split('-')
        begin, end = int(begin), int(end)
        body = payload[begin:end + 1]
        return httpx.Response(206, content=body, headers={'Content-Range': f'bytes {begin}-{end}/{len(payload)}'})

    sync = service(tmp_path, handler, chunk_bytes=4, range_limit=8)
    sampled = module.replay(sync, RESOURCE, len(payload), window=4)
    assert sampled['mode'] == 'sample'
    assert sampled['windows'] == [(0, 3), (8, 11)]
    assert b''.join(sampled['parts']) == b'0123' + b'89ab'
    assembled = module.replay(sync, RESOURCE, len(payload), sha256=sha, assemble=True)
    assert assembled['mode'] == 'assemble'
    assert Path(assembled['path']).read_bytes() == payload
    assert None not in calls
    sync.close()


def sync_neighbor(tmp_path: Path) -> Path:
    placeholder = CloudSyncService(BASE, KEY, tmp_path, transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    path = placeholder.cache_file('textbook:other', OTHER)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'keep-me')
    placeholder.close()
    return path
