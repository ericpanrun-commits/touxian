from __future__ import annotations

from uuid import UUID
import sqlite3
from fastapi import APIRouter, Body, Depends, Header, Query, Request
from .extraction_models import AnalyzeInput, ConfirmationInput, DraftInput, ExtractionOutput, ParticipationInput, RawInput, OUTPUT_SCHEMA_VERSION
from .personal_models import MutationInput
from .personal_api import perform, store_for
from .personal_store import PersonalError
from .review_store import ReviewStore

router = APIRouter(prefix="/api/personal", tags=["review"])


def analysis_for(request: Request):
    service = getattr(request.app.state, "extraction_service", None)
    if service is None:
        raise PersonalError("分析服务尚未就绪，请重启应用。", "not_ready", 503)
    return service


def request_key(value):
    if value:
        try:
            return str(UUID(value))
        except ValueError:
            raise PersonalError("请求标识无效，请重新发起。", "invalid_key", 400)
    return None


@router.get("/ai-settings")
def settings(request: Request):
    return perform(lambda: analysis_for(request).settings.public())


@router.get("/extraction-schema")
def extraction_schema():
    return {"version": OUTPUT_SCHEMA_VERSION, "schema": ExtractionOutput.model_json_schema()}


@router.put("/ai-settings")
def save_settings(request: Request, payload: dict = Body()):
    return perform(lambda: analysis_for(request).settings.save(payload))


@router.get("/raw")
def list_raw(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), source_id: str | None = None, store=Depends(store_for)):
    return perform(lambda: ReviewStore(store).list(limit, offset, source_id))


@router.post("/raw", status_code=201)
def collect_raw(payload: RawInput, idempotency_key: str | None = Header(None), store=Depends(store_for)):
    return perform(lambda: ReviewStore(store).collect(payload, request_key(idempotency_key)))


@router.get("/raw/{snapshot_id}")
def raw_detail(snapshot_id: str, store=Depends(store_for)):
    return perform(lambda: ReviewStore(store).get(snapshot_id))


@router.post("/raw/{snapshot_id}/analyze", status_code=202)
async def analyze(snapshot_id: str, payload: AnalyzeInput, request: Request, idempotency_key: str | None = Header(None)):
    try:
        return await analysis_for(request).start(snapshot_id, payload.force, request_key(idempotency_key), confirm_budget=payload.confirm_budget, scope=payload.scope)
    except (PersonalError, sqlite3.DatabaseError) as exc:
        return perform(lambda: (_ for _ in ()).throw(exc))


@router.post('/raw/{snapshot_id}/analysis-cancel')
async def cancel_analysis(snapshot_id: str, request: Request):
    try:
        return await analysis_for(request).long.cancel(snapshot_id)
    except (PersonalError, sqlite3.DatabaseError) as exc:
        return perform(lambda: (_ for _ in ()).throw(exc))


@router.get('/raw/{snapshot_id}/analysis-chunks/{chunk_record_id}')
def chunk_record(snapshot_id: str, chunk_record_id: str, store=Depends(store_for)):
    def read():
        import json
        with store.connection() as c:
            row = c.execute('SELECT ch.* FROM ai_chunk_extraction ch JOIN ai_extraction a ON a.id=ch.extraction_id WHERE a.raw_snapshot_id=? AND ch.id=?',(snapshot_id,chunk_record_id)).fetchone()
            if not row:
                raise PersonalError('区域分析记录不存在。','not_found',404)
            return {**dict(row),'parsed_output':json.loads(row['parsed_output'] or 'null')}
    return perform(read)


@router.put("/raw/{snapshot_id}/draft")
def save_draft(snapshot_id: str, payload: DraftInput, store=Depends(store_for)):
    return perform(lambda: ReviewStore(store).save_draft(snapshot_id, payload.expected_version, payload.draft))


@router.post("/raw/{snapshot_id}/cancel")
def cancel_raw(snapshot_id: str, payload: MutationInput, store=Depends(store_for)):
    return perform(lambda: ReviewStore(store).set_status(snapshot_id, payload.expected_version, "cancelled"))


@router.post("/raw/{snapshot_id}/resume")
def resume_raw(snapshot_id: str, payload: MutationInput, store=Depends(store_for)):
    return perform(lambda: ReviewStore(store).set_status(snapshot_id, payload.expected_version, "pending"))


@router.post("/raw/{snapshot_id}/confirm", status_code=201)
def confirm_raw(snapshot_id: str, payload: ConfirmationInput, store=Depends(store_for)):
    return perform(lambda: ReviewStore(store).confirm(snapshot_id, payload))


@router.post("/events/{event_id}/participation")
def participation(event_id: str, payload: ParticipationInput, store=Depends(store_for)):
    return perform(lambda: ReviewStore(store).participation(event_id, payload.expected_version, payload.participation))
