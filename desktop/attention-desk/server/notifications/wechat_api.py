"""Local settings only; keys are accepted once and never returned."""
from fastapi import APIRouter, Body, Depends, Request
from ..personal_api import perform
from ..wechat_collect.api import LocalRoute, desktop_write

router = APIRouter(prefix='/api/personal/notifications/wechat-bark', tags=['notifications'], route_class=LocalRoute)

def dispatcher(request: Request):
    return request.app.state.wechat_bark

@router.get('/settings')
def settings(service=Depends(dispatcher)):
    return perform(service.settings)

@router.put('/settings', dependencies=[Depends(desktop_write)])
def save(payload: dict = Body(), service=Depends(dispatcher)):
    return perform(lambda: service.save(payload))

@router.post('/test', dependencies=[Depends(desktop_write)])
def test(service=Depends(dispatcher)):
    return perform(service.test)
