"""Range and cache behavior of the Omen11 CloudSync downloader.

Origin does not yet contain every module the full processor imports. Missing
modules are stubbed here so these tests can call the real ``download_capture``
without replacing that class.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import sys
import types
from pathlib import Path

import httpx
import pytest


def _ensure(name, **attrs):
    """Import ``name``, or install an in-memory stub when this checkout lacks it."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        module = sys.modules.get(name)
        if module is None:
            module = types.ModuleType(name)
            module.__package__ = name.rpartition('.')[0]
            module.__path__ = []
            sys.modules[name] = module
            parent_name, _, child = name.rpartition('.')
            if parent_name:
                parent = _ensure(parent_name)
                setattr(parent, child, module)
        for key, value in attrs.items():
            setattr(module, key, value)
        return module


def _install_origin_gaps():
    _ensure('server.notes_anywhere.settings', BASE='https://example.test')
    _ensure('server.recovery_controls', cloud_uploads_held=lambda path: False)
    _ensure('server.course_api', CourseInput=object, CourseAssociationInput=object)
    _ensure('server.courses', CourseStore=object)
    _ensure('server.inbox_api', LinkInput=object)
    school = {
        'server.school.artifacts': ['ReviewArtifacts'],
        'server.school.models': [
            'FeedbackInput', 'LectureInput', 'SectionLinks', 'BookMetadataInput', 'BookFileInput', 'SectionInput',
        ],
        'server.school.store': ['SchoolStore'],
        'server.school.documents': ['ReadDocuments'],
        'server.school.context': ['ContextBuilder'],
        'server.school.retrieval': ['Retrieval'],
        'server.school.service': ['SchoolService'],
    }
    for module_name, class_names in school.items():
        _ensure(module_name, **{class_name: type(class_name, (), {}) for class_name in class_names})
    _ensure(
        'server.learning.store',
        LearningStore=object,
        unpack=lambda *args, **kwargs: None,
        capture_projection=lambda *args, **kwargs: None,
    )


_install_origin_gaps()

import server.cloud_sync.service as cloud_sync
from server.cloud_sync.service import FULL_DOWNLOAD_LIMIT, PART_BYTES, CloudSync, SyncError


KEY = 'ework_' + 'k' * 43
RESOURCE = 'textbook:demo'


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def capture_for(payload: bytes, *, resource_id=RESOURCE, capture_id='cap-1', filename='notes.bin', sha=None):
    return {
        'id': capture_id,
        'files': [{
            'resource_id': resource_id,
            'sha256': sha or digest(payload),
            'bytes': len(payload),
            'filename': filename,
            'mime': 'application/octet-stream',
        }],
    }


def cache_file(root: Path, capture_id: str, sha: str) -> Path:
    return root / f'{capture_id}-{sha}.download'


def open_sync(tmp_path: Path, handler) -> CloudSync:
    sync = CloudSync.__new__(CloudSync)
    sync.cache = tmp_path
    sync.config = {'worker_key': KEY}
    sync.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(60, connect=10),
        follow_redirects=False,
    )
    return sync


def run_download(sync: CloudSync, capture):
    async def go():
        try:
            return await sync.download_capture(capture)
        finally:
            await sync.client.aclose()

    return asyncio.run(go())


def slice_range(payload: bytes, header: str) -> httpx.Response:
    begin, end = header.removeprefix('bytes=').split('-')
    begin, end = int(begin), int(end)
    body = payload[begin:end + 1]
    return httpx.Response(206, content=body, headers={'Content-Range': f'bytes {begin}-{end}/{len(payload)}'})


def test_limits_match_the_downloader_contract():
    assert FULL_DOWNLOAD_LIMIT == 50 * 1024 * 1024
    assert PART_BYTES == 4_000_000


def test_over_limit_assembles_with_ranges(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_sync, 'FULL_DOWNLOAD_LIMIT', 8)
    monkeypatch.setattr(cloud_sync, 'PART_BYTES', 4)
    payload = b'0123456789'
    calls = []

    def handler(request):
        calls.append((request.headers.get('range'), request.headers.get('x-eric-worker-key')))
        assert request.headers['range']
        return slice_range(payload, request.headers['range'])

    sync = open_sync(tmp_path, handler)
    files = run_download(sync, capture_for(payload))
    filename, mime, path = files[0]
    assert filename == 'notes.bin'
    assert mime == 'application/octet-stream'
    assert path.read_bytes() == payload
    assert [item[0] for item in calls] == ['bytes=0-3', 'bytes=4-7', 'bytes=8-9']
    assert all(item[1] == KEY for item in calls)
    assert not path.with_name(path.name + '.partial').exists()


def test_http_416_assembles_with_ranges(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_sync, 'FULL_DOWNLOAD_LIMIT', 50)
    monkeypatch.setattr(cloud_sync, 'PART_BYTES', 4)
    payload = b'abcdefghij'
    calls = []

    def handler(request):
        calls.append(request.headers.get('range'))
        if request.headers.get('range') is None:
            return httpx.Response(416)
        return slice_range(payload, request.headers['range'])

    sync = open_sync(tmp_path, handler)
    path = run_download(sync, capture_for(payload))[0][2]
    assert path.read_bytes() == payload
    assert calls == [None, 'bytes=0-3', 'bytes=4-7', 'bytes=8-9']


def test_small_file_uses_one_get(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_sync, 'FULL_DOWNLOAD_LIMIT', 50)
    payload = b'12345'
    calls = []

    def handler(request):
        calls.append(request.headers.get('range'))
        return httpx.Response(200, content=payload)

    sync = open_sync(tmp_path, handler)
    path = run_download(sync, capture_for(payload))[0][2]
    assert path.read_bytes() == payload
    assert calls == [None]


def test_matching_cache_is_reused(tmp_path):
    payload = b'cached-bytes'
    sha = digest(payload)
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError('cache hit must not download')

    sync = open_sync(tmp_path, handler)
    cached = cache_file(tmp_path, 'cap-1', sha)
    cached.write_bytes(payload)
    partial = cached.with_name(cached.name + '.partial')
    partial.write_bytes(b'stale-partial')
    path = run_download(sync, capture_for(payload, sha=sha))[0][2]
    assert path == cached
    assert cached.read_bytes() == payload
    assert not partial.exists()
    assert calls == []


def test_failed_download_deletes_only_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_sync, 'FULL_DOWNLOAD_LIMIT', 8)
    monkeypatch.setattr(cloud_sync, 'PART_BYTES', 4)
    payload = b'0123456789'
    sha = digest(payload)
    neighbor = cache_file(tmp_path, 'cap-keep', 'ab' * 32)
    neighbor.write_bytes(b'keep-me')
    neighbor_partial = neighbor.with_name(neighbor.name + '.partial')
    neighbor_partial.write_bytes(b'other-partial')

    def handler(request):
        begin = int(request.headers['range'].removeprefix('bytes=').split('-')[0])
        if begin >= 4:
            return httpx.Response(503)
        return slice_range(payload, request.headers['range'])

    sync = open_sync(tmp_path, handler)
    with pytest.raises(SyncError) as error:
        run_download(sync, capture_for(payload, sha=sha))
    assert error.value.code == 'download_unavailable'
    target = cache_file(tmp_path, 'cap-1', sha)
    assert not target.exists()
    assert not target.with_name(target.name + '.partial').exists()
    assert neighbor.read_bytes() == b'keep-me'
    assert neighbor_partial.read_bytes() == b'other-partial'


def test_process_finally_keeps_verified_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_sync, 'record', lambda *args, **kwargs: None)
    payload = b'0123456789'
    sha = digest(payload)
    calls = []

    def handler(request):
        calls.append(request.headers.get('range'))
        return httpx.Response(200, content=payload)

    sync = open_sync(tmp_path, handler)
    sync.epoch = 'epoch-1'
    sync.generation = 3
    sync.workspace = 'ws-1'
    sync.personal = types.SimpleNamespace(path=tmp_path / 'personal.db')

    async def boom(*args, **kwargs):
        raise RuntimeError('after a verified download')

    async def finish(*args, **kwargs):
        calls.append('finish')
        return {}

    sync.intake = types.SimpleNamespace(submit=lambda *args, **kwargs: {'queued': True}, bridge=boom)
    sync.request = finish
    item = capture_for(payload, sha=sha)
    item.update(text_content='', title='t', shared_at='2026-11-05T00:00:00Z', purpose='study', lease_token='lease-1')

    async def go():
        try:
            await sync._process('captures', item)
        finally:
            await sync.client.aclose()

    asyncio.run(go())
    cached = cache_file(tmp_path, 'cap-1', sha)
    assert cached.read_bytes() == payload
    assert not cached.with_name(cached.name + '.partial').exists()
    assert calls == [None, 'finish']


def test_replay_script_assembles_with_ranges_and_reuses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_sync, 'FULL_DOWNLOAD_LIMIT', 8)
    monkeypatch.setattr(cloud_sync, 'PART_BYTES', 4)
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
        return slice_range(payload, request.headers['range'])

    sync = open_sync(tmp_path, handler)
    assembled = module.replay(sync, capture_for(payload, sha=sha))
    assert assembled['mode'] == 'assemble'
    assert assembled['sha256'] == sha
    assert Path(assembled['path']).read_bytes() == payload
    assert None not in calls

    def fail(request):
        raise AssertionError('replay must reuse the verified cache')

    sync.client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    again = module.replay(sync, capture_for(payload, sha=sha))
    assert Path(again['path']).read_bytes() == payload
    asyncio.run(sync.client.aclose())
