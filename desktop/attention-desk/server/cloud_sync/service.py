"""Download worker file bytes in ranges and reuse a verified local cache.

Objects above 50 MiB, and any object the server answers with 416, are read in
about 4 MiB ranges. A matching id + sha256 cache file is reused. Failures
delete only the incomplete ``*.partial`` file, including when a later ack fails.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import quote

import httpx


# Keep in step with files.ts FULL_DOWNLOAD_LIMIT (50 MiB) and the ~4 MiB client chunk.
FULL_DOWNLOAD_LIMIT = 50 * 1024 * 1024
CHUNK_BYTES = 4 * 1024 * 1024
_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,256}$')
_SHA = re.compile(r'^[a-f0-9]{64}$')


class CloudSyncError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def sample_windows(size: int, window: int = 65536) -> list[tuple[int, int]]:
    """Small inclusive ranges used to check a cloud original without reading it all."""
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise CloudSyncError('download_unavailable', '文件大小无效，已停止下载。')
    if not isinstance(window, int) or isinstance(window, bool) or window < 1:
        raise CloudSyncError('download_unavailable', '抽样窗口无效。')
    last = min(window, size) - 1
    first = (0, last)
    if size <= window:
        return [first]
    begin = min(size // 2, size - window)
    return [first, (begin, begin + window - 1)]


class CloudSyncService:
    def __init__(self, base_url: str, worker_key: str, cache_dir, *, transport=None, range_limit=FULL_DOWNLOAD_LIMIT, chunk_bytes=CHUNK_BYTES):
        if not isinstance(base_url, str) or not base_url.startswith(('https://', 'http://')):
            raise CloudSyncError('download_unavailable', '同步地址无效。')
        if not isinstance(worker_key, str) or not worker_key or len(worker_key) > 512:
            raise CloudSyncError('download_unavailable', '同步连接不可用。')
        if range_limit < 1 or chunk_bytes < 1:
            raise CloudSyncError('download_unavailable', '分段大小无效。')
        self.base_url = base_url.rstrip('/')
        self.worker_key = worker_key
        self.cache_dir = Path(cache_dir)
        self.range_limit = range_limit
        self.chunk_bytes = chunk_bytes
        self._client = httpx.Client(transport=transport, timeout=httpx.Timeout(60, connect=10), follow_redirects=False, trust_env=False)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def cache_file(self, resource_id: str, sha256: str) -> Path:
        self._check(resource_id, sha256)
        name = hashlib.sha256(resource_id.encode()).hexdigest()
        return self.cache_dir / sha256[:2] / f'{name}.{sha256}'

    def download_capture(self, resource_id: str, sha256: str, *, size: int | None = None, ack=None) -> Path:
        """Return the verified cache file for this id and sha256.

        A matching cache is reused and is not downloaded again. ``ack`` runs
        only after that file is in place; an ack error leaves it on disk.
        """
        cached = self.cache_file(resource_id, sha256)
        partial = cached.with_name(cached.name + '.partial')
        if self._matches(cached, sha256):
            partial.unlink(missing_ok=True)
        else:
            cached.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._download_to(resource_id, partial, size)
                if not self._matches(partial, sha256):
                    raise CloudSyncError('download_unavailable', '文件校验没有通过，请重试。')
                os.replace(partial, cached)
            except Exception:
                partial.unlink(missing_ok=True)
                raise
        try:
            if ack is not None:
                ack(cached)
        except Exception:
            raise
        return cached

    def sample_file(self, resource_id: str, size: int, ranges: list[tuple[int, int]]) -> list[bytes]:
        """Read only the given ranges. This never issues an unranged GET."""
        self._check(resource_id, 'a' * 64)
        if not ranges:
            raise CloudSyncError('download_unavailable', '没有要抽样的范围。')
        return [self._read_range(resource_id, begin, end, size) for begin, end in ranges]

    def _download_to(self, resource_id: str, partial: Path, size: int | None):
        total = self._known_size(size) if size is not None else self._manifest_size(resource_id)
        if total > self.range_limit:
            self._download_ranged(resource_id, partial, total)
            return
        with self._client.stream('GET', self._content_url(resource_id), headers=self._headers()) as response:
            if response.status_code == 416:
                ranged = True
            elif response.status_code != 200:
                raise CloudSyncError('download_unavailable', f'文件下载失败 HTTP {response.status_code}。')
            else:
                declared = response.headers.get('content-length')
                if declared and declared.isdigit() and int(declared) > self.range_limit:
                    total = int(declared)
                    ranged = True
                else:
                    ranged = False
                    self._write_all(response, partial, total)
        if ranged:
            self._download_ranged(resource_id, partial, total)

    def _download_ranged(self, resource_id: str, partial: Path, total: int):
        if total < 1 or total > 512_000_000:
            raise CloudSyncError('download_unavailable', '文件大小无效，已停止下载。')
        with partial.open('wb') as out:
            begin = 0
            while begin < total:
                end = min(begin + self.chunk_bytes, total) - 1
                out.write(self._read_range(resource_id, begin, end, total))
                begin = end + 1
            out.flush()
            os.fsync(out.fileno())

    def _read_range(self, resource_id: str, begin: int, end: int, total: int) -> bytes:
        headers = self._headers()
        headers['Range'] = f'bytes={begin}-{end}'
        with self._client.stream('GET', self._content_url(resource_id), headers=headers) as response:
            if response.status_code != 206:
                raise CloudSyncError('download_unavailable', f'分段下载失败 HTTP {response.status_code}。')
            if response.headers.get('content-range') != f'bytes {begin}-{end}/{total}':
                raise CloudSyncError('download_unavailable', '分段范围与文件不符，已停止下载。')
            body = response.read()
        if len(body) != end - begin + 1:
            raise CloudSyncError('download_unavailable', '分段长度不符，已停止下载。')
        return body

    def _write_all(self, response: httpx.Response, partial: Path, total: int):
        remaining = total
        with partial.open('wb') as out:
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                if len(chunk) > remaining or total - remaining + len(chunk) > self.range_limit:
                    raise CloudSyncError('download_unavailable', '文件超过分段阈值，已停止整文件下载。')
                out.write(chunk)
                remaining -= len(chunk)
            out.flush()
            os.fsync(out.fileno())
        if remaining != 0:
            raise CloudSyncError('download_unavailable', '文件下载不完整，已停止。')

    def _manifest_size(self, resource_id: str) -> int:
        response = self._client.get(self._content_url(resource_id).rsplit('/', 1)[0] + '/manifest', headers=self._headers())
        if response.status_code != 200:
            raise CloudSyncError('download_unavailable', '无法确认文件大小，已停止下载。')
        try:
            body = response.json()
            size = body['bytes']
        except (ValueError, KeyError, TypeError):
            raise CloudSyncError('download_unavailable', '无法确认文件大小，已停止下载。') from None
        return self._known_size(size)

    def _known_size(self, size) -> int:
        if not isinstance(size, int) or isinstance(size, bool) or size < 1 or size > 512_000_000:
            raise CloudSyncError('download_unavailable', '文件大小无效，已停止下载。')
        return size

    def _content_url(self, resource_id: str) -> str:
        return self.base_url + '/worker/files/' + quote(resource_id, safe='') + '/content'

    def _headers(self) -> dict[str, str]:
        return {'X-Eric-Worker-Key': self.worker_key, 'Accept-Encoding': 'identity'}

    def _check(self, resource_id: str, sha256: str):
        if not isinstance(resource_id, str) or not _ID.fullmatch(resource_id) or not isinstance(sha256, str) or not _SHA.fullmatch(sha256):
            raise CloudSyncError('download_unavailable', '文件标识无效。')

    @staticmethod
    def _matches(path: Path, sha256: str) -> bool:
        if not path.is_file():
            return False
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest() == sha256
