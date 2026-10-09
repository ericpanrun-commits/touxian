"""Restricted LAN application. Never mounts the desktop router or arbitrary files."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import secrets
import threading
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, ValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from ..documents.policy import DocumentError, MAX_BYTES, SOFT_BYTES, extension, filename, validate_file
from ..personal_models import CollectionInput
from .security import CollectorError, RateLimiter, SESSION_SECONDS
from .safe_logs import record
from .endpoint import public_base_url

COOKIE = 'ad_collector_session'
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff',
           'X-Frame-Options': 'DENY', 'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"}


class SubmissionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    submission_id: str = Field(min_length=36, max_length=36)
    source_name: str = Field(default='', max_length=200)
    source_time: str | None = Field(default=None, max_length=40)
    again: bool = False

    @field_validator('source_time')
    @classmethod
    def time_format(cls, value):
        return CollectionInput.valid_source_time(value)


class TextInput(SubmissionInput):
    text: str = Field(min_length=1, max_length=100000)

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        return CollectionInput.preserve_original(value)


class URLInput(SubmissionInput):
    url: str = Field(min_length=1, max_length=4096)


class FileInput(SubmissionInput):
    text: str = Field(default='', max_length=100000)
    confirm_large: bool = False


class PairInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    token: str = Field(min_length=1, max_length=128)


def rejection(error):
    return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=error.status, headers=HEADERS)


class CollectorBoundary:
    """Host/origin/auth before body; bounded rate, bytes, time and concurrency."""
    def __init__(self, app, *, origin, authority, personal):
        self.app, self.origin, self.authority = app, origin, authority
        self.host = origin.removeprefix('http://')
        self.personal = personal
        self.rates = RateLimiter()
        self.upload_slots = threading.BoundedSemaphore(2)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        path, method = scope.get('path', ''), scope['method']
        client = (scope.get('client') or ('unknown', 0))[0]
        response_started = False
        async def secured_send(message):
            nonlocal response_started
            if message['type'] == 'http.response.start':
                response_started = True
                existing = {key.lower() for key, _ in message.get('headers', [])}
                message['headers'] = list(message.get('headers', [])) + [(key.lower().encode(), value.encode()) for key, value in HEADERS.items() if key.lower().encode() not in existing]
            await send(message)
        acquired = False
        try:
            if headers.get(b'host', b'').decode('latin-1').lower() != self.host:
                raise CollectorError('request_rejected', '请求地址无法验证，请使用电脑显示的二维码。', 403)
            if method not in {'GET', 'POST'}:
                raise CollectorError('method_not_allowed', '此收集入口不支持该操作。', 405)
            if path.startswith('/api/'):
                origin = headers.get(b'origin', b'').decode('latin-1')
                if origin and origin != self.origin:
                    raise CollectorError('request_rejected', '请求来源无法验证。', 403)
                self.rates.check(('read', client), 100)
                if path not in {'/api/pair/validate', '/api/pair/confirm'}:
                    request = Request(scope)
                    session = self.authority.session(request.cookies.get(COOKIE), diagnose=method == 'GET' and path == '/api/session')
                    scope.setdefault('state', {})['collector_session'] = session
                    if method == 'POST':
                        self.authority.check_csrf(session, headers.get(b'x-collector-csrf', b'').decode('latin-1'))
                if method == 'POST':
                    if path != '/api/pair/validate':
                        self.rates.check(('write', client), 12)
                        self.rates.check(('global_write',), 24)
                    expected = 'multipart/form-data' if path == '/api/file' else 'application/json'
                    actual = headers.get(b'content-type', b'').decode('latin-1').split(';')[0].strip().lower()
                    if actual != expected:
                        raise CollectorError('content_type', '发送格式不支持，请使用收集页重新发送。', 415)
            if method == 'POST':
                limit = 60_000_000 if path == '/api/file' else 450_000
                length = headers.get(b'content-length', b'')
                if length.isdigit() and int(length) > limit:
                    raise CollectorError('file_too_large', '本次上传超过安全上限，请拆分文件。', 413)
                if not self.upload_slots.acquire(blocking=False):
                    raise CollectorError('busy', '电脑正在接收其他文件，请稍后再试。', 429)
                acquired = True
                messages, total = [], 0
                deadline = asyncio.get_running_loop().time() + 90
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise CollectorError('upload_timeout', '上传超时，请保留文件后重试。', 408)
                    try:
                        message = await asyncio.wait_for(receive(), remaining)
                    except asyncio.TimeoutError:
                        raise CollectorError('upload_timeout', '上传超时，请保留文件后重试。', 408)
                    if message['type'] == 'http.disconnect':
                        return
                    total += len(message.get('body', b''))
                    if total > limit:
                        raise CollectorError('file_too_large', '上传超过安全上限，已停止。', 413)
                    messages.append(message)
                    if not message.get('more_body'):
                        break
                index = 0
                async def replay():
                    nonlocal index
                    if index < len(messages):
                        message = messages[index]
                        index += 1
                        return message
                    return await receive()
                return await self.app(scope, replay, secured_send)
            return await self.app(scope, receive, secured_send)
        except CollectorError as error:
            return await rejection(error)(scope, receive, secured_send)
        except Exception as error:
            record(self.personal, 'request', error)
            if not response_started:
                return await rejection(CollectorError('unavailable', '电脑暂时无法处理，请保留输入并稍后重试。', 503))(scope, receive, secured_send)
        finally:
            if acquired:
                self.upload_slots.release()


def mobile_assets(dist):
    dist = Path(dist)
    entry = dist / 'mobile/index.html'
    try:
        manifest = json.loads((dist / '.vite/manifest.json').read_text(encoding='utf-8'))
        if not entry.is_file() or 'mobile/index.html' not in manifest:
            raise ValueError()
        allowed, visited = {}, set()
        def visit(key):
            if key in visited:
                return
            visited.add(key)
            record = manifest[key]
            for name in [record['file'], *record.get('css', []), *record.get('assets', [])]:
                target = (dist / name).resolve()
                if not target.is_relative_to(dist.resolve()) or not target.is_file():
                    raise ValueError()
                allowed['/' + name] = target
            for dependency in record.get('imports', []):
                visit(dependency)
        visit('mobile/index.html')
        return entry, allowed
    except (OSError, ValueError, KeyError, TypeError):
        raise CollectorError('collector_not_built', '手机页面尚未准备好，请重新启动工作台。', 503)


def create_app(service, authority, origin, app_root, *, allow_loopback_fixture=False):
    origin = public_base_url(origin, allow_loopback_fixture=allow_loopback_fixture)
    entry, assets = mobile_assets(Path(app_root) / 'dist')
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, debug=False)
    application.add_middleware(CollectorBoundary, origin=origin, authority=authority, personal=service.personal)

    @application.exception_handler(CollectorError)
    async def collector_error(_, error):
        return rejection(error)

    @application.exception_handler(RequestValidationError)
    async def input_error(_, error):
        return rejection(CollectorError('invalid_input', '请核对正文、来源名称和原消息时间；时间可留空。', 422))

    @application.exception_handler(HTTPException)
    async def http_error(_, error):
        return rejection(CollectorError('request_rejected', '此收集入口无法处理该请求。', error.status_code))

    @application.exception_handler(Exception)
    async def unexpected(_, error):
        return rejection(CollectorError('unavailable', '电脑暂时无法处理，请保留输入并稍后重试。', 503))

    @application.get('/')
    async def root():
        return RedirectResponse('/collect')

    @application.get('/collect')
    async def page():
        return FileResponse(entry, media_type='text/html')

    @application.get('/pair')
    async def landing(request: Request):
        token = request.query_params.get('token')
        try:
            authority.validate(token)
            result = 'valid'
        except CollectorError as error:
            result = error.code
        authority.trace('GET', '/pair', token, result)
        # Invalid invitations still get an explanatory page. A preview, reload
        # or prefetch cannot consume tokens or issue device cookies.
        return FileResponse(entry, media_type='text/html')

    @application.get('/collector-config')
    async def public_config():
        return {'public_base_url': origin, 'fixture': allow_loopback_fixture}

    @application.get('/assets/{name}')
    async def asset(name: str):
        target = assets.get('/assets/' + name)
        if not target:
            raise CollectorError('not_found', '资源不存在。', 404)
        mime = 'text/javascript' if target.suffix == '.js' else mimetypes.guess_type(target.name)[0]
        return FileResponse(target, media_type=mime or 'application/octet-stream')

    @application.get('/apple-touch-icon.png')
    async def icon():
        return FileResponse(Path(app_root) / 'public/touxian-icon.png', media_type='image/png')

    @application.get('/manifest.webmanifest')
    async def manifest():
        return JSONResponse({'name': 'Attention Desk 手机收集', 'short_name': '通知收集', 'start_url': '/collect', 'display': 'standalone',
                             'icons': [{'src': '/apple-touch-icon.png', 'sizes': 'any', 'type': 'image/png'}]}, media_type='application/manifest+json')

    def session_response(session):
        return {'paired': True, 'csrf': session.csrf, 'submission_id': str(uuid4())}

    @application.post('/api/pair/validate')
    async def validate_pairing(payload: PairInput):
        try:
            result = authority.validate(payload.token)
        except CollectorError as error:
            authority.trace('POST', '/api/pair/validate', payload.token, error.code)
            raise
        authority.trace('POST', '/api/pair/validate', payload.token, 'valid')
        return result

    @application.post('/api/pair/confirm')
    async def pair(payload: PairInput):
        try:
            value, session = authority.pair(payload.token)
        except CollectorError as error:
            authority.trace('POST', '/api/pair/confirm', payload.token, error.code)
            raise
        authority.trace('POST', '/api/pair/confirm', payload.token, 'confirmed')
        response = JSONResponse({**session_response(session), 'collector_path': '/collect'})
        response.set_cookie(COOKIE, value, max_age=SESSION_SECONDS, expires=SESSION_SECONDS,
                            httponly=True, samesite='strict', secure=False, path='/')
        return response

    @application.get('/api/session')
    async def session(request: Request):
        return session_response(request.state.collector_session)

    @application.post('/api/text', status_code=202)
    async def text(payload: TextInput, request: Request):
        return await service.bridge(service.submit(request.state.collector_session.owner, 'text', payload.model_dump()))

    @application.post('/api/url', status_code=202)
    async def url(payload: URLInput, request: Request):
        return await service.bridge(service.submit(request.state.collector_session.owner, 'url', payload.model_dump()))

    @application.post('/api/file', status_code=202)
    async def files(request: Request):
        temporary, inputs = [], []
        try:
            async with request.form(max_files=10, max_fields=6, max_part_size=450000) as form:
                allowed = {'files', 'submission_id', 'source_name', 'source_time', 'text', 'confirm_large', 'again'}
                if set(form) - allowed:
                    raise CollectorError('invalid_input', '发送字段不支持，请重新打开收集页。')
                payload = FileInput(submission_id=str(form.get('submission_id', '')), text=str(form.get('text', '')), source_name=str(form.get('source_name', '')),
                                    source_time=str(form.get('source_time', '')) or None, again=form.get('again') == 'true', confirm_large=form.get('confirm_large') == 'true')
                for upload in form.getlist('files'):
                    if not isinstance(upload, UploadFile):
                        raise CollectorError('invalid_file', '请选择有效文件。')
                    name = filename(upload.filename or '')
                    ext = extension(name)
                    if ext not in {'png', 'jpg', 'jpeg', 'pdf', 'docx', 'txt'}:
                        raise CollectorError('unsupported_file', '支持 PNG/JPG/JPEG、PDF/DOCX/TXT。HEIC 请先另存为 JPEG，或上传截图。')
                    if (upload.size or 0) > MAX_BYTES:
                        raise CollectorError('file_too_large', '单个文件超过 50 MB，请拆分。', 413)
                    if (upload.size or 0) > SOFT_BYTES and not payload.confirm_large:
                        raise CollectorError('large_file_confirmation', '文件超过 15 MB，需要确认后上传。', 413)
                    path = service.documents.files.work / (str(uuid4()) + '.mobile-upload')
                    temporary.append(path)
                    size = 0
                    with path.open('xb') as stream:
                        while chunk := await upload.read(65536):
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise CollectorError('file_too_large', '单个文件超过 50 MB，已停止。', 413)
                            stream.write(chunk)
                    await asyncio.to_thread(validate_file, path, ext, upload.content_type or '')
                    inputs.append((name, upload.content_type or '', path))
                if not inputs:
                    raise CollectorError('invalid_file', '请选择至少一个文件。')
                return await service.bridge(service.submit(request.state.collector_session.owner, 'file', payload.model_dump(), inputs))
        except DocumentError as error:
            message = '文件类型或内容无法安全处理，请核对文件格式。'
            raise CollectorError(error.code, message, 400)
        except ValidationError:
            raise CollectorError('invalid_input', '请核对来源名称和原消息时间，也可以留空。', 422)
        finally:
            for path in temporary:
                path.unlink(missing_ok=True)

    @application.get('/api/submissions/{submission_id}')
    async def status(submission_id: str, request: Request):
        return await service.bridge(service.status(request.state.collector_session.owner, submission_id))

    return application
