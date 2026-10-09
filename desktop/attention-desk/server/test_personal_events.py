"""Manual workflow acceptance and failure-path tests; no external services."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.personal_api import router
from server.personal_models import CollectionInput
from server.personal_store import PersonalError, PersonalStore

ORIGINAL = "  📌 大创报名通知\n请有意报名的同学于10月12日前填写附件1，并发送到指定邮箱。\n10月10日前阅读要求。  "


@pytest.fixture
def store(tmp_path):
    value = PersonalStore(tmp_path / "personal.sqlite3")
    value.initialize()
    return value


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(router)
    app.state.personal_store = store
    with TestClient(app) as value:
        yield value


def create(client, text=ORIGINAL, **kwargs):
    response = client.post("/api/personal/collect", json={"original_text": text, "title": "大学生创新创业训练计划报名", **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def add_deadline(client, event, value, label, quote):
    response = client.post(f"/api/personal/events/{event['id']}/deadlines", json={
        "expected_version": event["version"], "label": label, "value": value, "precision": "date",
        "original_text": quote, "evidence": {"quote": quote},
    })
    assert response.status_code == 201, response.text
    return response.json()


def add_task(client, event, title, deadline_id=None, quote=None, origin="manual"):
    response = client.post(f"/api/personal/events/{event['id']}/tasks", json={
        "expected_version": event["version"], "title": title, "deadline_id": deadline_id, "origin": origin,
        "evidence": {"quote": quote} if quote else None,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_full_two_deadline_three_task_restart_acceptance(client, store):
    event = create(client, source_time="2026-10-07 09:30")
    event = add_deadline(client, event, "2026-10-12", "报名材料提交", "10月12日前填写附件1")
    event = add_deadline(client, event, "2026-10-10", "阅读要求", "10月10日前阅读要求")
    submission = next(value for value in event["deadlines"] if value["label"] == "报名材料提交")
    reading = next(value for value in event["deadlines"] if value["label"] == "阅读要求")
    event = add_task(client, event, "阅读要求", reading["id"], "阅读要求", "explicit")
    event = add_task(client, event, "填写附件1", submission["id"], "填写附件1", "explicit")
    event = add_task(client, event, "发送至指定邮箱", submission["id"], "发送到指定邮箱", "explicit")
    response = client.patch(f"/api/personal/events/{event['id']}/tasks/{event['tasks'][0]['id']}", json={"expected_version": event["version"], "status": "done"})
    assert response.status_code == 200
    event = response.json()
    response = client.put(f"/api/personal/events/{event['id']}/deadlines/{submission['id']}", json={
        "expected_version": event["version"], "label": submission["label"], "value": "2026-10-11", "precision": "date",
        "original_text": submission["original_text"],
    })
    assert response.status_code == 200, response.text
    event = response.json()
    reopened = PersonalStore(store.path)
    reopened.initialize()
    saved = reopened.get_event(event["id"])
    assert saved["snapshot"]["original_text"] == ORIGINAL
    assert saved["snapshot"]["source_time"] == "2026-10-07T09:30"
    assert len(saved["deadlines"]) == 2
    assert len(saved["tasks"]) == 3
    assert [task["status"] for task in saved["tasks"]] == ["done", "todo", "todo"]
    assert [task["order"] for task in saved["tasks"]] == [0, 1, 2]
    assert next(value for value in saved["deadlines"] if value["id"] == submission["id"])["value"] == "2026-10-11"
    assert saved["status"] == "needs_review"
    assert saved["evidence"]
    for evidence in saved["evidence"]:
        assert evidence["raw_snapshot_id"] == saved["snapshot"]["id"]
        assert ORIGINAL[evidence["start_offset"]:evidence["end_offset"]] == evidence["quote"]
    assert any(decision["action"] == "deadline_edited" for decision in saved["decisions"])


def test_snapshot_is_immutable_and_initialization_is_repeatable(client, store):
    event = create(client)
    for statement in ("UPDATE raw_snapshot SET original_text = 'changed'", "DELETE FROM raw_snapshot"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with store.connection(write=True) as connection:
                connection.execute(statement)
    store.initialize()
    assert store.get_event(event["id"])["snapshot"]["original_text"] == ORIGINAL
    with store.connection() as connection:
        assert [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3, 4, 5, 6]


def test_invalid_evidence_rolls_back_child_and_version(client, store):
    event = create(client)
    response = client.post(f"/api/personal/events/{event['id']}/deadlines", json={
        "expected_version": event["version"], "label": "提交", "value": "2026-10-12", "evidence": {"quote": "原文没有这句话"},
    })
    assert response.status_code == 400
    saved = store.get_event(event["id"])
    assert saved["version"] == event["version"]
    assert saved["deadlines"] == []
    assert len(saved["decisions"]) == 1


def test_failed_creation_is_atomic_and_retryable(store, monkeypatch):
    submission_id = str(uuid4())
    audit = store._audit
    with monkeypatch.context() as context:
        context.setattr(store, "_audit", lambda *args: (_ for _ in ()).throw(PersonalError("injected write failure")))
        with pytest.raises(PersonalError):
            store.create_collection(CollectionInput(original_text=ORIGINAL, title="报名"), submission_id)
    with store.connection() as connection:
        for table in ("source", "raw_item", "raw_snapshot", "event", "user_decision"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert store._audit == audit
    assert store.create_collection(CollectionInput(original_text=ORIGINAL, title="报名"), submission_id)["snapshot"]["original_text"] == ORIGINAL


def test_deadline_original_phrase_cannot_claim_text_missing_from_snapshot(client, store):
    event = create(client)
    payload = {"expected_version": event["version"], "label": "提交", "value": "2026-10-12", "original_text": "10月11日截止"}
    response = client.post(f"/api/personal/events/{event['id']}/deadlines", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_original_phrase"
    assert store.get_event(event["id"])["deadlines"] == []
    event = add_deadline(client, event, "2026-10-12", "提交", "10月12日")
    response = client.put(f"/api/personal/events/{event['id']}/deadlines/{event['deadlines'][0]['id']}", json={**payload, "expected_version": event["version"]})
    assert response.status_code == 400
    saved = store.get_event(event["id"])
    assert saved["version"] == event["version"]
    assert saved["deadlines"] == event["deadlines"]
    assert saved["evidence"] == event["evidence"]


def test_create_retries_are_idempotent_but_changed_payload_is_not(client):
    key = {"Idempotency-Key": str(uuid4())}
    payload = {"title": "报名", "original_text": ORIGINAL}
    first = client.post("/api/personal/collect", json=payload, headers=key)
    second = client.post("/api/personal/collect", json=payload, headers=key)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert client.get("/api/personal/events").json()["total"] == 1
    conflict = client.post("/api/personal/collect", json={**payload, "title": "不同内容"}, headers=key)
    assert conflict.status_code == 409


@pytest.mark.parametrize("value,precision", [
    ("2026-02-30", "date"), ("2026-10-12T23:59", "date"), ("10月12日", "date"),
    ("2026-10-12", "minute"), ("2026-10-12T25:00", "minute"), ("2026-10-12T12:00Z", "minute"),
])
def test_invalid_or_invented_datetime_is_rejected(client, value, precision):
    event = create(client)
    response = client.post(f"/api/personal/events/{event['id']}/deadlines", json={
        "expected_version": event["version"], "label": "提交", "value": value, "precision": precision,
    })
    assert response.status_code == 422


def test_date_precision_never_adds_2359_and_minute_is_explicit(client):
    event = add_deadline(client, create(client), "2026-10-12", "提交", "10月12日")
    assert event["deadlines"][0]["value"] == "2026-10-12"
    assert event["deadlines"][0]["precision"] == "date"
    response = client.post(f"/api/personal/events/{event['id']}/deadlines", json={
        "expected_version": event["version"], "label": "明确的时间", "value": "2026-10-12 16:30", "precision": "minute",
    })
    assert response.status_code == 201
    minute = next(value for value in response.json()["deadlines"] if value["precision"] == "minute")
    assert minute["value"] == "2026-10-12T16:30"
    assert minute["timezone"] is None  # Empty timezone explicitly follows system IANA zone.


def test_all_tasks_complete_does_not_complete_event(client):
    event = add_task(client, create(client), "阅读要求")
    event = client.patch(f"/api/personal/events/{event['id']}/tasks/{event['tasks'][0]['id']}", json={"expected_version": event["version"], "status": "done"}).json()
    assert event["status"] == "needs_review"
    done = client.post(f"/api/personal/events/{event['id']}/status", json={"expected_version": event["version"], "status": "done"})
    assert done.status_code == 200
    assert done.json()["status"] == "done"


def test_event_complete_cancel_restore_preserves_tasks_and_previous_status(client):
    event = add_task(client, create(client), "填写材料")
    event = client.post(f"/api/personal/events/{event['id']}/status", json={"expected_version": event["version"], "status": "in_progress"}).json()
    blocked = client.post(f"/api/personal/events/{event['id']}/status", json={"expected_version": event["version"], "status": "done"})
    assert blocked.status_code == 409
    event = client.post(f"/api/personal/events/{event['id']}/status", json={"expected_version": event["version"], "status": "done", "confirm_incomplete": True}).json()
    assert event["status"] == "done" and event["tasks"][0]["status"] == "todo"
    assert client.post(f"/api/personal/events/{event['id']}/tasks", json={"expected_version": event["version"], "title": "不可编辑"}).status_code == 409
    event = client.post(f"/api/personal/events/{event['id']}/restore", json={"expected_version": event["version"]}).json()
    assert event["status"] == "in_progress"
    event = client.post(f"/api/personal/events/{event['id']}/status", json={"expected_version": event["version"], "status": "cancelled"}).json()
    assert event["status"] == "cancelled"
    event = client.post(f"/api/personal/events/{event['id']}/restore", json={"expected_version": event["version"]}).json()
    assert event["status"] == "in_progress" and event["tasks"][0]["status"] == "todo"


def test_concurrent_changes_do_not_silently_overwrite(store):
    event = store.create_collection(CollectionInput(original_text=ORIGINAL, title="报名"))
    def edit(title):
        try:
            return store.edit_event(event["id"], event["version"], {"title": title, "summary": "", "category": "其他"})["title"]
        except PersonalError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(edit, ("第一个修改", "第二个修改")))
    assert results.count("conflict") == 1
    assert store.get_event(event["id"])["title"] in {"第一个修改", "第二个修改"}


def test_cross_event_task_deadline_and_evidence_reference_rejected(client):
    first = add_deadline(client, create(client), "2026-10-12", "提交", "10月12日")
    first = add_task(client, first, "填写材料")
    second = create(client)
    foreign = first["deadlines"][0]["id"]
    response = client.post(f"/api/personal/events/{second['id']}/tasks", json={"expected_version": second["version"], "title": "不合法关联", "deadline_id": foreign})
    assert response.status_code == 400
    response = client.post(f"/api/personal/events/{second['id']}/evidence", json={
        "expected_version": second["version"], "field_path": f"tasks.{first['tasks'][0]['id']}.title", "evidence": {"quote": "填写附件1"},
    })
    assert response.status_code == 400
    assert client.get(f"/api/personal/events/{second['id']}").json()["tasks"] == []


def test_unicode_offsets_are_code_points_and_quote_must_match(client):
    event = create(client)
    quote = "📌 大创报名通知"
    start = ORIGINAL.index(quote)
    response = client.post(f"/api/personal/events/{event['id']}/evidence", json={
        "expected_version": event["version"], "field_path": "event.title",
        "evidence": {"quote": quote, "start_offset": start, "end_offset": start + len(quote)},
    })
    assert response.status_code == 201
    event = response.json()
    assert event["evidence"][0]["quote"] == quote
    response = client.post(f"/api/personal/events/{event['id']}/evidence", json={
        "expected_version": event["version"], "field_path": "event.title",
        "evidence": {"quote": quote, "start_offset": start, "end_offset": start + len(quote) + 1},
    })
    assert response.status_code == 400


def test_database_failure_is_reported_and_does_not_replace_data(client, store, monkeypatch):
    event = create(client)
    with monkeypatch.context() as context:
        context.setattr(store, "get_event", lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError("injected busy database")))
        response = client.get(f"/api/personal/events/{event['id']}")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "storage_error"
    assert store.get_event(event["id"])["snapshot"]["original_text"] == ORIGINAL


def test_newer_schema_is_rejected_without_overwrite(store):
    with store.connection(write=True) as connection:
        connection.execute("INSERT INTO schema_migrations VALUES (999, 'future')")
    with pytest.raises(PersonalError, match="版本"):
        store.initialize()
    with store.connection() as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 999


def test_list_empty_paging_and_input_contract(client):
    assert client.get("/api/personal/events").json()["total"] == 0
    for index in range(3):
        create(client, title=f"事项 {index}")
    page = client.get("/api/personal/events?limit=2&offset=2").json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert client.get("/api/personal/events?limit=1000").status_code == 422
    assert client.post("/api/personal/collect", json={"title": " ", "original_text": ORIGINAL}).status_code == 422
    assert client.post("/api/personal/collect", json={"title": "事项", "original_text": " \n "}).status_code == 422
    assert client.post("/api/personal/collect", json={"title": "事项", "original_text": ORIGINAL, "provider": "deepseek"}).status_code == 422


def test_launcher_readiness_does_not_probe_optional_integrations(monkeypatch):
    from server import main
    def unavailable(*args, **kwargs):
        raise AssertionError("Readiness must not invoke external integration checks")
    monkeypatch.setattr(main.mail_digest, "mail_status", unavailable)
    monkeypatch.setattr(main.WECHAT, "status", unavailable)
    assert main.health(check_integrations=False)["status"] == "ok"
