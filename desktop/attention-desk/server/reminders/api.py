"""Settings and Reminder Center actions, confined to the desktop API."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field

from ..personal_api import perform
from ..personal_models import InputModel
from .policy import ReminderSettingsInput
from ..planning.time import system_zone

router = APIRouter(prefix='/api/personal/reminders',tags=['reminders'])


def service_for(request:Request):
    service = getattr(request.app.state,'reminder_service',None)
    if service is None:
        raise HTTPException(503,detail={'message':'提醒服务尚未就绪，请重启工作台。'})
    return service


class ReminderAction(InputModel):
    action: Literal['dismiss','10m','1h','tomorrow']
    expected_generation: int = Field(ge=0)


class TestNotificationInput(InputModel):
    pass


@router.get('/settings')
def settings(service=Depends(service_for)):
    return perform(lambda:{**service.repository.settings(),'system_timezone':system_zone().key,
        'backend_required':True,'startup_supported':False,
        'startup_note':'当前为本地网页工作台，尚未提供随 Windows 启动集成；关闭网页后可保留后端，完全退出后不保证提醒。'})


@router.put('/settings')
def save_settings(payload:ReminderSettingsInput,service=Depends(service_for)):
    return perform(lambda:service.repository.save_settings(payload))


@router.post('/test')
def test_notification(payload:TestNotificationInput,service=Depends(service_for)):
    return service.test_notification()


@router.get('')
def center(limit:int=Query(100,ge=1,le=100),offset:int=Query(0,ge=0),service=Depends(service_for)):
    def read():
        service.repository.reconcile(at=service.clock())
        return {**service.repository.center(limit=limit,offset=offset),'last_result':service.last_result}
    return perform(read)


@router.post('/{reminder_id}/action')
def act(reminder_id:UUID,payload:ReminderAction,service=Depends(service_for)):
    def apply():
        service.repository.reconcile(at=service.clock())
        return service.repository.act(str(reminder_id),payload.action,at=service.clock(),expected_generation=payload.expected_generation)
    return perform(apply)
