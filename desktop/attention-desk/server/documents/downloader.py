"""Download only saved official links; redirects, hosts and size are bounded."""
import time
from urllib.parse import urljoin, urlsplit
import httpx
from ..official_http import public_dns
from ..official_sources import canonical_url, SourceFailure
from .policy import DocumentError, SOFT_BYTES, MAX_BYTES


def download(url, allowed_hosts, destination, *, confirm_large=False, transport=None, resolver=public_dns):
    limit = MAX_BYTES if confirm_large else SOFT_BYTES
    started = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(20,connect=8),follow_redirects=False,trust_env=False,transport=transport,
                          headers={'User-Agent':'AttentionDesk/0.1 local attachment parser','Accept-Encoding':'identity'}) as client:
            for _ in range(4):
                url = canonical_url(url,allowed_hosts)
                resolver(urlsplit(url).hostname)
                with client.stream('GET',url) as r:
                    if r.status_code in (301,302,303,307,308):
                        if not r.headers.get('location'): raise DocumentError('redirect_error','附件重定向缺少目标。')
                        url = canonical_url(urljoin(url,r.headers['location']),allowed_hosts)
                        continue
                    if r.status_code!=200: raise DocumentError('download_failed',f'附件下载 HTTP {r.status_code}，原通知保留。')
                    if r.headers.get('content-encoding','identity').lower() not in ('','identity'):
                        raise DocumentError('compressed_response','附件服务器返回压缩响应，已停止下载。')
                    declared = r.headers.get('content-length','')
                    if declared.isdigit() and int(declared)>limit:
                        raise DocumentError('file_too_large' if int(declared)>MAX_BYTES else 'large_file_confirmation','文件过大，需要确认（15 MB 自动上限；50 MB 硬上限）。')
                    mime = r.headers.get('content-type','').split(';')[0].lower()
                    if mime in ('text/html','application/xhtml+xml'):
                        raise DocumentError('mime_mismatch','链接返回网页或登录页，未作为附件解析。')
                    size = 0
                    with open(destination,'wb') as out:
                        for chunk in r.iter_raw():
                            size+=len(chunk)
                            if size>limit: raise DocumentError('file_too_large' if size>MAX_BYTES else 'large_file_confirmation','文件过大，需要确认；已停止下载。')
                            if time.monotonic()-started>60: raise DocumentError('download_timeout','附件下载超过 60 秒，已停止。')
                            out.write(chunk)
                    return mime
    except SourceFailure as e: raise DocumentError(e.code,str(e))
    except httpx.HTTPError: raise DocumentError('download_failed','附件下载超时或网络不可用，原通知保留。')
    raise DocumentError('redirect_limit','附件重定向超过 3 次，已停止。')
