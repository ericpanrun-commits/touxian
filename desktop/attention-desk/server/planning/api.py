"""Desktop-only local planning API. Never mounted on the LAN Collector."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ..personal_api import perform, store_for
from ..personal_models import DeadlineInput
from ..personal_store import PersonalError
from .ics import export
from .query import PlanningQuery

router = APIRouter(prefix='/api/personal',tags=['planning'])


@router.get('/planning/today')
def today(include_terminal: bool=False,store=Depends(store_for)):
    return perform(lambda:PlanningQuery(store).today(include_terminal=include_terminal))


@router.get('/planning/upcoming')
def upcoming(days:int=Query(7),include_terminal:bool=False,store=Depends(store_for)):
    if days not in {7,14,30}:
        raise HTTPException(422,detail={'message':'请选择 7、14 或 30 天。'})
    return perform(lambda:PlanningQuery(store).upcoming(days,include_terminal=include_terminal))


@router.get('/planning/calendar')
def month(year:int=Query(ge=1970,le=9998),month:int=Query(ge=1,le=12),include_terminal:bool=False,store=Depends(store_for)):
    return perform(lambda:PlanningQuery(store).month(year,month,include_terminal=include_terminal))


@router.get('/planning/export.ics')
def calendar_export(event_id:UUID|None=None,deadline_id:UUID|None=None,future:bool=True,include_terminal:bool=False,store=Depends(store_for)):
    def generate():
        if event_id:
            store.get_event(str(event_id))
        if deadline_id:
            with store.connection() as db:
                if not db.execute('SELECT id FROM deadline WHERE id=?',(str(deadline_id),)).fetchone():
                    raise PersonalError('日期不存在。','not_found',404)
        try:
            payload = export(store,event_id=str(event_id) if event_id else None,deadline_id=str(deadline_id) if deadline_id else None,
                             future=future,include_terminal=include_terminal)
        except (ValueError,OverflowError,TypeError):
            raise PersonalError('日历文件暂时无法生成，事项数据未修改。','ics_failed',503)
        return Response(payload,media_type='text/calendar; charset=utf-8',headers={
            'Content-Disposition':'attachment; filename="attention-deadlines.ics"',
            'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    return perform(generate)


@router.post('/events/{event_id}/deadlines/{deadline_id}/source-update')
def apply_source_update(event_id:UUID,deadline_id:UUID,payload:DeadlineInput,store=Depends(store_for)):
    if not payload.source_snapshot_id or not payload.acknowledged_source_change or not payload.evidence or not payload.confirmed_by_user:
        raise HTTPException(422,detail={'message':'需要新来源、准确引用和明确确认。'})
    return perform(lambda:store.edit_deadline(str(event_id),str(deadline_id),payload))
