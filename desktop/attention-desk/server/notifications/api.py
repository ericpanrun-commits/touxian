"""Desktop-only notification settings; no credential input or output."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..personal_api import perform
from .policy import NotificationPolicyConfig

router = APIRouter(prefix='/api/personal/notifications/bark', tags=['notifications'])


def dispatcher_for(request: Request):
    monitor = getattr(request.app.state, 'official_monitor', None)
    if monitor is None:
        raise HTTPException(503, detail='手机通知服务尚未就绪，请重试。')
    return monitor.notifications


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool
    expected_version: int = Field(ge=1, strict=True)
    policy: NotificationPolicyConfig


class TestInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


@router.get('/settings')
def settings(dispatcher=Depends(dispatcher_for)):
    return perform(dispatcher.settings)


@router.put('/settings')
def save_settings(payload: SettingsInput, dispatcher=Depends(dispatcher_for)):
    return perform(lambda: dispatcher.save_settings(payload))


@router.post('/test')
def test(payload: TestInput, dispatcher=Depends(dispatcher_for)):
    return perform(dispatcher.test_notification)


@router.get('/history')
def history(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=100000), dispatcher=Depends(dispatcher_for)):
    return perform(lambda: dispatcher.repository.history(limit=limit, offset=offset))
