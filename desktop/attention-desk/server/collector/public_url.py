"""Public URL intake with DNS-pinned sockets, verified TLS and bounded HTML."""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import time
import queue
import threading
from urllib.parse import urljoin, urlsplit, urlunsplit
from bs4 import BeautifulSoup
import idna
from ..official_parsers import body_text, published_date
from .security import CollectorError

MAX_HTML = 2_000_000
DNS_SLOTS = threading.BoundedSemaphore(4)


def bounded_resolve(host, port, resolver, timeout):
    # OS DNS has no portable cancellation. Bound both wait time and stuck workers.
    if not DNS_SLOTS.acquire(blocking=False):
        raise CollectorError('url_unavailable', 'DNS 查询繁忙，可改为截图/粘贴。')
    results = queue.Queue(maxsize=1)
    def resolve():
        try:
            results.put((True, resolve_public(host, port, resolver)))
        except Exception as error:
            results.put((False, error))
        finally:
            DNS_SLOTS.release()
    threading.Thread(target=resolve, name='attention-public-dns', daemon=True).start()
    try:
        ok, value = results.get(timeout=max(.01, timeout))
    except queue.Empty:
        raise CollectorError('url_unavailable', 'DNS 查询超时，可改为截图/粘贴。')
    if not ok:
        if isinstance(value, CollectorError):
            raise value
        raise CollectorError('url_unavailable', '正文无法自动获取，可改为截图/粘贴。')
    return value


def public_ip(value):
    try:
        address = ipaddress.ip_address(value.split('%')[0])
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped:
                return public_ip(str(address.ipv4_mapped))
            if address.sixtofour or address.teredo or address in ipaddress.ip_network('64:ff9b::/96'):
                return False
        return address.is_global and not (address.is_multicast or address.is_reserved or address.is_unspecified)
    except ValueError:
        return False


def canonical_public_url(value):
    if not isinstance(value, str) or len(value) > 4096 or re.search(r'[\x00-\x20\x7f]', value):
        raise CollectorError('unsafe_url', '请输入公开 HTTP/HTTPS 网页链接。')
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        if parts.scheme not in {'http', 'https'} or not host or parts.username is not None or parts.password is not None or port not in {80, 443}:
            raise ValueError()
        try:
            ipaddress.ip_address(host)
            if not public_ip(host):
                raise ValueError()
        except ValueError:
            if ':' in host or re.fullmatch(r'[0-9.]+', host):
                raise ValueError()
            host = idna.encode(host.rstrip('.'), uts46=False, std3_rules=True).decode('ascii').lower()
            if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.lan', '.home')) or '.' not in host:
                raise ValueError()
        authority = f'[{host}]' if ':' in host else host
        if port != (443 if parts.scheme == 'https' else 80):
            authority += f':{port}'
        return urlunsplit((parts.scheme, authority, parts.path or '/', parts.query, ''))
    except (ValueError, idna.IDNAError):
        raise CollectorError('unsafe_url', '不允许访问本机、内网或非公开 HTTP/HTTPS 地址。')


def resolve_public(host, port, resolver=socket.getaddrinfo):
    try:
        addresses = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise CollectorError('url_unavailable', '正文无法自动获取，可改为截图/粘贴。')
    if not addresses or len(addresses) > 64 or any(not public_ip(row[4][0]) for row in addresses):
        raise CollectorError('unsafe_url', '链接解析到了非公开地址，已拒绝访问。')
    return addresses


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, addresses, *, https, timeout):
        super().__init__(host, port, timeout=timeout)
        self.addresses, self.https = addresses, https

    def connect(self):
        last_error = None
        deadline = time.monotonic() + self.timeout
        for family, kind, protocol, _, endpoint in self.addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('connection_timeout')
            connection = socket.socket(family, kind, protocol)
            connection.settimeout(remaining)
            try:
                # No second hostname lookup: connect to the exact validated address.
                connection.connect(endpoint)
                self.sock = ssl.create_default_context().wrap_socket(connection, server_hostname=self.host) if self.https else connection
                return
            except OSError as error:
                connection.close()
                last_error = error
        raise last_error or OSError('connection_failed')


class PublicURLFetcher:
    def __init__(self, resolver=socket.getaddrinfo, connection_factory=PinnedConnection, clock=time.monotonic):
        self.resolver, self.connection_factory, self.clock = resolver, connection_factory, clock

    def fetch(self, value):
        current = canonical_public_url(value)
        deadline = self.clock() + 30
        for _ in range(4):
            parts = urlsplit(current)
            port = parts.port or (443 if parts.scheme == 'https' else 80)
            addresses = bounded_resolve(parts.hostname, port, self.resolver, min(8, deadline - self.clock()))
            if self.clock() >= deadline:
                raise CollectorError('url_unavailable', '正文无法自动获取，可改为截图/粘贴。')
            connection = self.connection_factory(parts.hostname, port, addresses, https=parts.scheme == 'https', timeout=min(8, deadline - self.clock()))
            try:
                target = urlunsplit(('', '', parts.path or '/', parts.query, ''))
                connection.request('GET', target, headers={'User-Agent': 'AttentionDesk/0.1 MobileCollector', 'Accept': 'text/html,application/xhtml+xml,text/plain', 'Accept-Encoding': 'identity'})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader('Location')
                    if not location:
                        raise CollectorError('url_unavailable', '正文无法自动获取，可改为截图/粘贴。')
                    current = canonical_public_url(urljoin(current, location))
                    continue
                mime = (response.getheader('Content-Type') or '').split(';')[0].strip().lower()
                encoding = (response.getheader('Content-Encoding') or 'identity').lower()
                size = response.getheader('Content-Length') or ''
                if response.status != 200 or mime not in {'text/html', 'application/xhtml+xml', 'text/plain'} or encoding != 'identity' or (size.isdigit() and int(size) > MAX_HTML):
                    raise CollectorError('url_unavailable', '正文无法自动获取，可改为截图/粘贴。')
                content = bytearray()
                while True:
                    if self.clock() >= deadline:
                        raise CollectorError('url_unavailable', '正文无法自动获取，可改为截图/粘贴。')
                    if connection.sock:
                        connection.sock.settimeout(min(8, deadline - self.clock()))
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    content.extend(chunk)
                    if len(content) > MAX_HTML:
                        raise CollectorError('url_unavailable', '网页过大，链接已保存，可改为截图/粘贴。')
                return extract_page(bytes(content), current, mime)
            except (OSError, ssl.SSLError, http.client.HTTPException):
                raise CollectorError('url_unavailable', '正文无法自动获取，可改为截图/粘贴。')
            finally:
                connection.close()
        raise CollectorError('url_unavailable', '重定向过多，链接已保存，可改为截图/粘贴。')


def extract_page(content, url, mime):
    if mime == 'text/plain':
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                text = content.decode('gb18030')
            except UnicodeDecodeError:
                raise CollectorError('url_unavailable', '正文无法自动获取，可改为截图/粘贴。')
        title, publication, html = text.strip().splitlines()[0][:200] if text.strip() else '', None, ''
    else:
        soup = BeautifulSoup(content, 'lxml')
        if soup.select_one('input[type="password"], #captcha, .captcha'):
            raise CollectorError('url_unavailable', '页面需要登录或验证，链接已保存，可改为截图/粘贴。')
        node = soup.select_one('.v_news_content, #vsb_content, #js_content, article, main, [role="main"]') or soup.body
        if not node:
            raise CollectorError('url_unavailable', '正文无法自动获取，可改为截图/粘贴。')
        heading = soup.select_one('.arc-tit h1, #article-title, #activity-name, h1, title')
        title = heading.get_text(' ', strip=True)[:200] if heading else '手机提交的网页'
        date_node = soup.select_one('.arc-info, #article-info, time[datetime], meta[property="article:published_time"]')
        publication = published_date(date_node.get('datetime') or date_node.get('content') or date_node.get_text(' ', strip=True)) if date_node else None
        for element in node.select('script,style,noscript,iframe,object,embed,form,nav,header,footer,aside,[hidden],[aria-hidden="true"]'):
            element.decompose()
        text, html = body_text(node), str(node)
        if len(text) < 20 or re.search(r'^(?:请登录|扫码登录|访问验证|安全验证|Just a moment|Access Denied)', text, re.I):
            raise CollectorError('url_unavailable', '正文无法自动获取，可改为截图/粘贴。')
    if not text.strip() or len(text) > 100000:
        raise CollectorError('url_unavailable', '正文为空或过长，链接已保存，可改为截图/粘贴。')
    return {'url': url, 'text': text, 'title': title, 'published_at': publication, 'body_html': html}
