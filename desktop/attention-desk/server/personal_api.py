"""Small API boundary for personal events, separate from legacy triage routes."""
from __future__ import annotations

import sqlite3
from typing import Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from .personal_models import (CollectionInput, DeadlineInput, EventEditInput,
                              EvidenceBindInput, MutationInput, StatusInput,
                              TaskEditInput, TaskInput)
from .personal_store import PersonalError, PersonalStore

router = APIRouter(prefix="/api/personal", tags=["personal"])


def store_for(request: Request) -> PersonalStore:
    store = getattr(request.app.state, "personal_store", None)
    if store is None:
        raise HTTPException(503, detail={"code": "not_ready", "message": "事项存储尚未就绪，请重启应用。"})
    return store


def perform(action: Callable):
    try:
        return action()
    except PersonalError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": str(exc)}) from exc
    except sqlite3.DatabaseError as exc:
        raise HTTPException(503, detail={"code": "storage_error", "message": "本地事项暂时无法读写，请重试。原有数据未覆盖。"}) from exc


@router.get("/events")
def list_events(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0), store: PersonalStore = Depends(store_for)):
    return perform(lambda: store.list_events(limit=limit, offset=offset))


@router.post("/collect", status_code=201)
def collect(payload: CollectionInput, idempotency_key: str | None = Header(default=None), store: PersonalStore = Depends(store_for)):
    if idempotency_key:
        try:
            idempotency_key = str(UUID(idempotency_key))
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "invalid_key", "message": "保存请求标识无效，请重新打开收集入口。"}) from exc
    return perform(lambda: store.create_collection(payload, idempotency_key))


@router.get("/events/{event_id}")
def event_detail(event_id: str, store: PersonalStore = Depends(store_for)):
    return perform(lambda: store.get_event(event_id))


@router.patch("/events/{event_id}")
def edit_event(event_id: str, payload: EventEditInput, store: PersonalStore = Depends(store_for)):
    return perform(lambda: store.edit_event(event_id, payload.expected_version, payload.model_dump(exclude={"expected_version"})))


@router.post("/events/{event_id}/status")
def status(event_id: str, payload: StatusInput, store: PersonalStore = Depends(store_for)):
    return perform(lambda: store.change_status(event_id, payload.expected_version, payload.status, payload.confirm_incomplete))


@router.post("/events/{event_id}/restore")
def restore(event_id: str, payload: MutationInput, store: PersonalStore = Depends(store_for)):
    return perform(lambda: store.restore_event(event_id, payload.expected_version))


@router.post("/events/{event_id}/deadlines", status_code=201)
def add_deadline(event_id: str, payload: DeadlineInput, store: PersonalStore = Depends(store_for)):
    return perform(lambda: store.add_deadline(event_id, payload))


@router.put("/events/{event_id}/deadlines/{deadline_id}")
def edit_deadline(event_id: str, deadline_id: str, payload: DeadlineInput, store: PersonalStore = Depends(store_for)):
    return perform(lambda: store.edit_deadline(event_id, deadline_id, payload))


@router.post("/events/{event_id}/tasks", status_code=201)
def add_task(event_id: str, payload: TaskInput, store: PersonalStore = Depends(store_for)):
    return perform(lambda: store.add_task(event_id, payload))


@router.patch("/events/{event_id}/tasks/{task_id}")
def edit_task(event_id: str, task_id: str, payload: TaskEditInput, store: PersonalStore = Depends(store_for)):
    return perform(lambda: store.edit_task(event_id, task_id, payload.expected_version, payload.model_dump(exclude={"expected_version"}, exclude_unset=True)))


@router.post("/events/{event_id}/evidence", status_code=201)
def bind_evidence(event_id: str, payload: EvidenceBindInput, store: PersonalStore = Depends(store_for)):
    return perform(lambda: store.bind_evidence(event_id, payload.expected_version, payload.field_path, payload.evidence))
