"""Desktop-only Inbox projection. Existing raw/file routes own intake."""
import asyncio
from typing import Literal
from fastapi import APIRouter, Request, Query
from pydantic import BaseModel, Field, ConfigDict
from .personal_api import perform
from .personal_store import PersonalError
from .inbox_store import InboxStore

router = APIRouter(prefix='/api/personal/inbox', tags=['inbox'])


def service_for(request):
    service = getattr(request.app.state, 'inbox_service', None)
    if service is None: raise PersonalError('收件服务尚未就绪。', 'not_ready', 503)
    return service


class LinkInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1)
    course_id: str | None = None
    project_ref: str = Field(default='', max_length=120)


class RetryInput(BaseModel):
    expected_version: int = Field(ge=1)


@router.get('')
def list_inbox(request: Request, status: Literal['all', 'processing', 'needs_review', 'completed'] = 'all',
               scope: Literal['all', 'submitted'] = 'all', limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    return perform(lambda: InboxStore(request.app.state.personal_store).list(status, limit, offset, scope))


@router.get('/{raw_id}')
def detail(raw_id: str, request: Request):
    return perform(lambda: InboxStore(request.app.state.personal_store).detail(raw_id))


@router.put('/{raw_id}/links')
async def links(raw_id: str, payload: LinkInput, request: Request):
    result=await asyncio.to_thread(perform,lambda: InboxStore(request.app.state.personal_store).link(raw_id, payload.expected_version, payload.course_id, payload.project_ref))
    learning=getattr(request.app.state,'learning_service',None)
    if learning is not None:learning.resume_pending_note_links()
    return result


@router.post('/{raw_id}/retry', status_code=202)
async def retry(raw_id: str, payload: RetryInput, request: Request):
    try: return await service_for(request).retry(raw_id, payload.expected_version)
    except PersonalError as error: return perform(lambda: (_ for _ in ()).throw(error))
