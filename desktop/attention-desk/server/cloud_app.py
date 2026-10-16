"""Minimal protected cloud worker. No desktop, mail, AI, files or Windows services."""
from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from .cloud_store import ProcessLock, validate_storage
from .notifications.api import router as notification_router
from .notifications.store import NotificationStore
from .official_api import router as official_router
from .official_service import OfficialSourceMonitor
from .personal_store import PersonalStore, SCHEMA_VERSION
from .runtime import RuntimeConfig

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logging.getLogger('httpx').setLevel(logging.WARNING)


class CloudAuth(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith('/api/') or request.method == 'OPTIONS':
            return await call_next(request)
        runtime = getattr(request.app.state,'runtime',None)
        if runtime is None:
            return JSONResponse({'detail':'后台尚未就绪。'},status_code=503)
        supplied = request.headers.get('Authorization','')
        if not runtime.admin_token or not hmac.compare_digest(supplied.encode(),('Bearer '+runtime.admin_token).encode()):
            return JSONResponse({'detail':'需要后台访问凭据。'},status_code=401,headers={'WWW-Authenticate':'Bearer'})
        origin = request.headers.get('origin')
        own = str(request.base_url).rstrip('/')
        if origin and origin not in runtime.allowed_origins and origin != own:
            return JSONResponse({'detail':'此访问来源未获允许。'},status_code=403)
        return await call_next(request)


@asynccontextmanager
async def lifespan(application):
    runtime = RuntimeConfig.from_env()
    if not runtime.cloud_production or len(runtime.admin_token)<32 or not runtime.expected_database_id:
        raise RuntimeError('Cloud worker 需要 production/cloud 模式和至少 32 字符的 CLOUD_ADMIN_TOKEN。')
    root,path = validate_storage(runtime)
    lock = ProcessLock(root)
    lock.acquire()
    monitor = None
    try:
        runtime = replace(runtime,storage_verified=True)
        application.state.runtime = runtime
        # Refuse to create a blank replacement DB when migration identity is expected.
        if not path.is_file():
            if not runtime.bootstrap or runtime.auto_sync or runtime.notification_authority:
                raise RuntimeError('缺少应迁移的生产数据库，拒绝建立空库。')
            # Keep the VM alive for authenticated Fly SSH/SFTP migration. No empty
            # replacement DB, crawler, dispatcher or public upload endpoint is created.
            application.state.awaiting_database = True
            LOG.info('worker_bootstrap instance=%s result=awaiting_database',runtime.instance_id)
            yield
            return
        personal = PersonalStore(path)
        with personal.connection() as db:
            try:
                identity=db.execute('SELECT database_id FROM production_identity WHERE id=1').fetchone()[0]
            except Exception:
                raise RuntimeError('请先对迁移副本执行 bootstrap 并验证 manifest。') from None
        if identity!=runtime.expected_database_id:
            raise RuntimeError('生产数据库身份不匹配，拒绝迁移或发送。')
        personal.initialize()
        NotificationStore(personal).establish_baseline(ignore_disabled=True)
        application.state.personal_store = personal
        monitor = OfficialSourceMonitor(personal,runtime=runtime)
        application.state.official_monitor = monitor
        if monitor.configuration_error:
            raise RuntimeError('生产官方来源配置未通过验证。')
        LOG.info('worker_start instance=%s environment=%s bootstrap=%s authority=%s',runtime.instance_id,runtime.environment,runtime.bootstrap,runtime.automatic_bark_allowed)
        yield
    finally:
        if monitor:
            await run_in_threadpool(monitor.close)
        lock.close()
        LOG.info('worker_shutdown')


def create_app():
    application = FastAPI(title='ZQU Cloud Worker',version='7.2',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    application.include_router(official_router)
    application.include_router(notification_router)
    application.add_middleware(CloudAuth)
    config = RuntimeConfig.from_env()
    application.add_middleware(CORSMiddleware,allow_origins=list(config.allowed_origins),allow_methods=['GET','POST','PUT','PATCH'],
                               allow_headers=['Authorization','Content-Type'],allow_credentials=False)

    @application.get('/health')
    def health():
        personal = getattr(application.state,'personal_store',None)
        if personal is None:
            if getattr(application.state,'awaiting_database',False):
                return {'status':'bootstrapping','database_accessible':False}
            raise HTTPException(503,detail='not_ready')
        try:
            with personal.connection() as db:
                db.execute('SELECT 1').fetchone()
        except Exception:
            raise HTTPException(503,detail='database_unavailable') from None
        return {'status':'ok'}

    @application.get('/health/ready')
    def ready():
        health()
        if getattr(application.state,'personal_store',None) is None:
            raise HTTPException(503,detail='awaiting_database')
        with application.state.personal_store.connection() as db:
            complete = db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == SCHEMA_VERSION
        monitor = getattr(application.state,'official_monitor',None)
        if not complete or monitor is None:
            raise HTTPException(503,detail='not_ready')
        state = monitor.cloud_store.status()
        if state['scheduler']=='stale':
            raise HTTPException(503,detail='scheduler_stale')
        return {'status':'ready','bootstrap':application.state.runtime.bootstrap,'scheduler':state['scheduler']}

    @application.get('/api/personal/system/status')
    def status():
        if getattr(application.state,'awaiting_database',False):
            return {**application.state.runtime.public(),'cloud_deployed':False,'status':'bootstrapping',
                    'scheduler':'disabled','migration_complete':False,'backup_status':'pending'}
        return application.state.official_monitor.cloud_store.status()

    return application


app = create_app()
