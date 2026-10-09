"""Lifecycle and QR access exists only on the localhost desktop API."""
import os
from fastapi import APIRouter, Depends, Request, HTTPException, Response as APIResponse
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from .security import CollectorError

router = APIRouter(prefix='/api/personal/collector', tags=['mobile collector control'])


def controller_for(request: Request, response: APIResponse):
    response.headers['Cache-Control'] = 'no-store'
    web_port, api_port = os.getenv('ATTENTION_WEB_PORT', '5173'), os.getenv('ATTENTION_API_PORT', '8765')
    allowed_origins = {f'http://127.0.0.1:{port}' for port in (web_port, api_port)} | {f'http://localhost:{port}' for port in (web_port, api_port)}
    origin = request.headers.get('origin')
    allowed_hosts = {value.removeprefix('http://') for value in allowed_origins}
    if request.client.host == 'testclient':
        allowed_hosts.add('testserver')
    if request.client.host not in {'127.0.0.1', '::1', 'testclient'} or request.headers.get('host', '').lower() not in allowed_hosts or (origin and origin not in allowed_origins):
        raise HTTPException(403, detail={'code': 'local_only', 'message': '手机收集设置只能在本机工作台操作。'})
    controller = getattr(request.app.state, 'collector', None)
    if not controller:
        raise HTTPException(503, detail={'code': 'not_ready', 'message': '手机收集服务尚未准备好，请重新启动工作台。'})
    return controller


class EnableInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    interface_id: str | None = Field(default=None, max_length=40)
    port: int = Field(default=8473, ge=1024, le=65535)


def perform(action):
    try:
        return action()
    except CollectorError as error:
        return JSONResponse({'detail': {'code': error.code, 'message': error.message}}, status_code=error.status, headers={'Cache-Control': 'no-store'})


@router.get('')
def status(controller=Depends(controller_for)):
    return controller.status()


@router.post('/enable')
def enable(payload: EnableInput, controller=Depends(controller_for)):
    return perform(lambda: controller.enable(payload.interface_id, payload.port))


@router.post('/stop')
def stop(controller=Depends(controller_for)):
    return controller.stop()


@router.post('/regenerate')
def regenerate(controller=Depends(controller_for)):
    return perform(controller.regenerate)


@router.post('/revoke-devices')
def revoke_devices(controller=Depends(controller_for)):
    return perform(controller.revoke_devices)


@router.get('/qr')
def qr(pairing_id: str = '', controller=Depends(controller_for)):
    return perform(lambda: Response(controller.qr(pairing_id), media_type='image/svg+xml', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}))
