"""Desktop-only information projection API over the existing official monitor."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, StrictBool
from .personal_api import perform, store_for
from .official_store import OfficialStore

router=APIRouter(prefix='/api/personal/official',tags=['official-information'])

def monitor_for(request:Request):
    monitor=getattr(request.app.state,'official_monitor',None)
    if monitor is None: raise HTTPException(503,detail='官网同步服务尚未就绪。')
    return monitor

def action(fn):
    try: return perform(fn)
    except ValueError as exc: raise HTTPException(400,detail=str(exc)) from exc
    except RuntimeError as exc: raise HTTPException(409,detail=str(exc)) from exc

@router.get('/feed')
def feed(view:Literal['latest','relevant','college','academic','competition','international','employment','all','saved','unread']='latest',
         offset:int=Query(0,ge=0,le=100000),limit:int=Query(30,ge=1,le=50),source_id:str|None=None,store=Depends(store_for)):
    return action(lambda:OfficialStore(store).feed(view,offset,limit,source_id))

@router.get('/items/{item_id}')
def detail(item_id:str,store=Depends(store_for)):
    return action(lambda:OfficialStore(store).detail(item_id))

class ItemState(BaseModel):
    model_config=ConfigDict(extra='forbid')
    is_read:StrictBool|None=None
    is_saved:StrictBool|None=None

@router.patch('/items/{item_id}')
def state(item_id:str,payload:ItemState,store=Depends(store_for)):
    return action(lambda:OfficialStore(store).set_item_state(item_id,**payload.model_dump()))

@router.get('/overview')
def overview(monitor=Depends(monitor_for)):
    return action(monitor.overview)

class ScanInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_ids:list[str]|None=None

@router.post('/scan')
def scan(payload:ScanInput,monitor=Depends(monitor_for)):
    return action(lambda:monitor.start_scan(payload.source_ids))

class SourceInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id:str
    enabled:StrictBool

@router.post('/source')
def source(payload:SourceInput,monitor=Depends(monitor_for)):
    return action(lambda:monitor.set_enabled(payload.source_id,payload.enabled))

@router.get('/runs')
def runs(source_id:str|None=None,store=Depends(store_for)):
    return action(lambda:{'runs':OfficialStore(store).recent_runs(source_id)})
