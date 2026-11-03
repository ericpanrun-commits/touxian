"""The larger textbook upload is still a desktop-loopback-only operation."""
import os
from fastapi import Request
from urllib.parse import urlsplit
from ..personal_store import PersonalError

TEXTBOOK_MAX_BYTES = 250_000_000


def local_upload(request: Request):
    if not request.client or request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
        raise PersonalError('教材导入仅允许在本机工作台操作。', 'local_only', 403)
    origin = request.headers.get('origin')
    if origin:
        value = urlsplit(origin)
        allowed = {urlsplit(str(request.base_url)).port, int(os.getenv('ATTENTION_WEB_PORT', '5173')), int(os.getenv('ATTENTION_API_PORT', '8765'))}
        if value.scheme not in {'http', 'https'} or value.hostname not in {'localhost', '127.0.0.1', '::1'} or value.port not in allowed:
            raise PersonalError('不允许的教材导入页面来源。', 'origin_denied', 403)
