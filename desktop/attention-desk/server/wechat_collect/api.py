"""Desktop loopback controls; no route mounts on phone/cloud collectors."""
from __future__ import annotations

import json
from datetime import date
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Query, Request, HTTPException
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..personal_api import perform
from ..personal_store import PersonalError
from ..user_context import UserContext
from .config import SettingsDocument
from .scheduler import SHANGHAI
from .inbox import list_candidates,set_ignored,briefing
from ..extraction_models import ConfirmationInput
from typing import Literal

class LocalRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handle(request):
            try:
                return await original(request)
            except PersonalError as exc:
                raise HTTPException(exc.status,detail={'code':exc.code,'message':str(exc)}) from None
            except __import__('sqlite3').DatabaseError:
                raise HTTPException(503,detail={'code':'storage_error','message':'本地存储暂时不可用，已有内容保留。'}) from None
        return handle


router=APIRouter(prefix='/api/personal/wechat',tags=['wechat'],route_class=LocalRoute)


def service_for(request:Request):
    service=getattr(request.app.state,'wechat_service',None)
    if service is None:
        raise PersonalError('本地微信服务未就绪。','not_ready',503)
    return service


def desktop_write(request:Request):
    origin=request.headers.get('origin')
    host=request.client.host if request.client else ''
    if host not in {'127.0.0.1','::1','testclient'}:
        raise PersonalError('微信设置仅允许本机操作。','local_only',403)
    if origin:
        parts=urlsplit(origin)
        if parts.scheme not in {'http','https'} or parts.hostname not in {'localhost','127.0.0.1','::1'}:
            raise PersonalError('不允许的页面来源。','origin_denied',403)
        expected=urlsplit(str(request.base_url)).port
        from os import environ
        allowed_ports={expected,int(environ.get('ATTENTION_WEB_PORT','5173')),int(environ.get('ATTENTION_API_PORT','8765'))}
        if parts.port not in allowed_ports:
            raise PersonalError('不允许的页面来源。','origin_denied',403)
    if 'application/json' not in request.headers.get('content-type',''):
        raise PersonalError('请使用 JSON 请求。','json_required',415)


class ScanInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    override_quiet: bool=False


@router.get('/status')
def status(service=Depends(service_for)):
    return perform(service.status)


@router.get('/settings')
def settings(service=Depends(service_for)):
    return perform(lambda:service.settings.read().model_dump())


@router.put('/settings',dependencies=[Depends(desktop_write)])
async def save_settings(payload:SettingsDocument,service=Depends(service_for)):
    return (await service.save_settings(payload,payload.revision)).model_dump()


@router.post('/scan',status_code=202,dependencies=[Depends(desktop_write)])
async def scan(payload:ScanInput,service=Depends(service_for),idempotency_key:str=Header()):
    try:
        key=str(UUID(idempotency_key))
    except ValueError:
        raise PersonalError('请求标识无效。','invalid_key',400) from None
    return await service.request_scan(manual=True,override_quiet=payload.override_quiet,request_key=key)


@router.get('/scans')
def scans(limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),service=Depends(service_for)):
    return perform(lambda:service.repository.scans(limit,offset))


@router.get('/stats')
def stats(date_value:date|None=Query(None,alias='date'),service=Depends(service_for)):
    return perform(lambda:service.repository.stats((date_value or service.clock().astimezone(SHANGHAI).date()).isoformat()))


@router.get('/chats')
def chats(service=Depends(service_for)):
    return perform(lambda:{'items':service.repository.group_cards(service.document),'revision':service.document.revision})

class GroupGrantInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    chatroom_id:str=Field(pattern=r'^[A-Za-z0-9_-]+@chatroom$')
    display_name:str=Field(min_length=1,max_length=120)
    priority:Literal['high','medium','low']='medium'
    expected_revision:int=Field(ge=1,strict=True)

class GroupChangeInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=1,strict=True)
    enabled:StrictBool|None=None
    priority:Literal['high','medium','low']|None=None

@router.get('/chats/search')
async def search_groups(q:str=Query(min_length=1,max_length=120),offset:int=Query(0,ge=0,le=3000),service=Depends(service_for)):
    return await service.search_groups(q,offset)

@router.post('/chats',dependencies=[Depends(desktop_write)])
async def add_group(payload:GroupGrantInput,service=Depends(service_for)):
    return (await service.add_group(payload)).model_dump()

@router.patch('/chats/{chat_id}',dependencies=[Depends(desktop_write)])
async def update_group(chat_id:str,payload:GroupChangeInput,service=Depends(service_for)):
    return (await service.update_group(chat_id,payload)).model_dump()

@router.post('/chats/{chat_id}/remove',dependencies=[Depends(desktop_write)])
async def remove_group(chat_id:str,payload:GroupChangeInput,service=Depends(service_for)):
    return (await service.update_group(chat_id,payload,remove=True)).model_dump()


@router.post('/chats/{chat_id}/bind',dependencies=[Depends(desktop_write)])
async def bind(chat_id:str,payload:dict=Body(),service=Depends(service_for)):
    if not getattr(service.adapter,'is_database',False):
        raise PersonalError('当前读取器不支持数据库绑定。','uia_not_verified',409)
    if service.lock.locked():
        raise PersonalError('扫描进行中，请稍后绑定。','busy',409)
    target=next((ch for ch in service.document.wechat.priority_chats if ch.id==chat_id),None)
    if target is None:
        raise PersonalError('群未配置。','chat_not_configured',404)
    async with service.lock:
        import asyncio
        binding=await asyncio.to_thread(service.adapter.resolve,target,policy=service.policy,cancelled=service.stop.is_set)
        service.repository.bind_database(chat_id,binding)
    return {'bound':True,'capability':'GO','internal_chat_id':binding['internal_chat_id']}


@router.get('/candidates')
def candidates(limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0),status:Literal['needs_review','ignored']='needs_review',service=Depends(service_for)):
    return perform(lambda:list_candidates(service.personal,limit=limit,offset=offset,status=status))


@router.get('/briefing')
def knowledge(limit:int=Query(3,ge=1,le=20),service=Depends(service_for)):
    return perform(lambda:briefing(service.personal,limit=limit,at=service.clock()))


@router.get('/media')
def media_summary(service=Depends(service_for)):
    from .media_pipeline import media_status
    return {**media_status(service.personal),'quiet':service.policy.quiet()}


class FYIActionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['archive','ignore','restore','pin','unpin']
    expected_version:int=Field(ge=1,strict=True)


class FYIConversionInput(ConfirmationInput):
    fyi_version:int=Field(ge=1,strict=True)


@router.get('/fyi')
def fyi_feed(limit:int=Query(3,ge=1,le=20),offset:int=Query(0,ge=0),view:Literal['recent','archived']='recent',service=Depends(service_for)):
    from .fyi import feed
    return perform(lambda:feed(service.personal,limit=limit,offset=offset,view=view,at=service.clock()))


@router.post('/fyi/{candidate_id}/state',dependencies=[Depends(desktop_write)])
def fyi_state(candidate_id:str,payload:FYIActionInput,service=Depends(service_for)):
    from .fyi import mutate
    return perform(lambda:mutate(service.personal,candidate_id,payload.action,payload.expected_version,at=service.clock()))


@router.get('/fyi/{candidate_id}')
def fyi_item(candidate_id:str,service=Depends(service_for)):
    from .fyi import refresh,get
    refresh(service.personal,service.clock())
    with service.personal.connection() as c:
        return get(c,candidate_id)


@router.get('/fyi/{candidate_id}/conversion')
def fyi_preview(candidate_id:str,service=Depends(service_for)):
    from .fyi import conversion_preview
    return perform(lambda:conversion_preview(service.personal,candidate_id,at=service.clock()))


@router.post('/fyi/{candidate_id}/convert',dependencies=[Depends(desktop_write)])
def fyi_convert(candidate_id:str,payload:FYIConversionInput,service=Depends(service_for)):
    from .fyi import refresh
    from ..review_store import ReviewStore
    refresh(service.personal,service.clock())
    with service.personal.connection() as c:
        row=c.execute("SELECT b.snapshot_id FROM wechat_candidate ca JOIN wechat_batch b ON b.id=ca.batch_id WHERE ca.id=? AND ca.attention_level='fyi'",(candidate_id,)).fetchone()
        if not row:raise PersonalError('参考信息不存在。','not_found',404)
        snapshot_id=row[0]
    request=ConfirmationInput.model_validate(payload.model_dump(exclude={'fyi_version'}))
    return perform(lambda:ReviewStore(service.personal).confirm(snapshot_id,request,fyi_candidate_id=candidate_id,fyi_version=payload.fyi_version))


@router.post('/candidates/{candidate_id}/ignore',dependencies=[Depends(desktop_write)])
def ignore(candidate_id:str,payload:dict=Body(),service=Depends(service_for)):
    if set(payload)-{'ignored','expected_version'} or not isinstance(payload.get('ignored'),bool):
        raise PersonalError('请明确选择忽略或恢复。')
    return perform(lambda:set_ignored(service.personal,candidate_id,payload['ignored'],expected_version=payload.get('expected_version')))


@router.get('/user-context')
def context(service=Depends(service_for)):
    return perform(lambda:service.context.read().model_dump())


@router.put('/user-context',dependencies=[Depends(desktop_write)])
async def save_context(payload:UserContext,service=Depends(service_for)):
    async with service.settings_lock:
        return perform(lambda:service.context.save(payload,payload.profile_version).model_dump())
