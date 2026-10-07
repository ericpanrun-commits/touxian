"""Bounded public HTML requests. Every redirect is checked before requesting it."""
from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
import httpx
from .official_sources import SourceFailure, canonical_url


def public_dns(host: str):
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        raise SourceFailure('dns_error', '域名暂时无法解析，请稍后检查。', status='unreachable')
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise SourceFailure('private_address', '来源解析到了非公开网络地址，已停止访问。')


@dataclass
class HTMLResult:
    url: str
    body: bytes
    status: int
    etag: str | None = None
    last_modified: str | None = None


class PublicHTMLClient:
    def __init__(self, source, *, transport=None, resolver=public_dns, delay=.25, stop=None):
        self.source, self.resolver, self.delay, self.stop = source, resolver, delay, stop
        self.client = httpx.Client(timeout=httpx.Timeout(12, connect=8), follow_redirects=False, trust_env=False,
                                   transport=transport, headers={'User-Agent': 'AttentionDesk/0.1 public-notices (+local personal use)',
                                                                 'Accept': 'text/html,application/xhtml+xml'})
        self.request_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.client.close()

    def get(self, url, *, etag=None, last_modified=None):
        headers = {}
        if etag:
            headers['If-None-Match'] = etag
        if last_modified:
            headers['If-Modified-Since'] = last_modified
        for _ in range(4):
            url = canonical_url(url, self.source.allowed_hosts)
            self.resolver(urlsplit(url).hostname)
            if self.stop and self.stop.is_set():
                raise SourceFailure('interrupted', '检查因应用退出而中断。', status='unreachable')
            if self.request_count:
                if self.stop:
                    self.stop.wait(self.delay)
                else:
                    time.sleep(self.delay)
            self.request_count += 1
            try:
                with self.client.stream('GET', url, headers=headers) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get('location')
                        if not location:
                            raise SourceFailure('redirect_error', '页面重定向缺少目标地址。')
                        url = canonical_url(urljoin(url, location), self.source.allowed_hosts)
                        headers = {}  # Validators belong to the original URL.
                        continue
                    if response.status_code == 304:
                        return HTMLResult(url, b'', 304, response.headers.get('etag'), response.headers.get('last-modified'))
                    if response.status_code in {401, 403}:
                        raise SourceFailure('access_denied', '公开页面拒绝访问或需要认证，已跳过。', status='unreachable')
                    if response.status_code != 200:
                        raise SourceFailure('http_error', f'HTTP {response.status_code}，原有通知保留。', status='unreachable' if response.status_code >= 500 or response.status_code == 429 else 'error')
                    size = response.headers.get('content-length', '')
                    if size.isdigit() and int(size) > 2_000_000:
                        raise SourceFailure('response_too_large', '页面超过 2 MB 限制，已跳过。')
                    mime = response.headers.get('content-type', '').lower().split(';')[0]
                    if mime not in {'text/html', 'application/xhtml+xml'}:
                        raise SourceFailure('not_html', '来源没有返回 HTML 正文，未下载附件或其他文件。')
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 2_000_000:
                            raise SourceFailure('response_too_large', '页面超过 2 MB 限制，已跳过。')
                    return HTMLResult(url, bytes(data), 200, response.headers.get('etag'), response.headers.get('last-modified'))
            except httpx.TimeoutException:
                raise SourceFailure('timeout', '网站请求超时，请稍后检查。', status='unreachable')
            except httpx.HTTPError:
                raise SourceFailure('network_error', '网站暂时无法连接，已保留原有通知。', status='unreachable')
        raise SourceFailure('redirect_limit', '重定向超过 3 次限制，已停止访问。')
