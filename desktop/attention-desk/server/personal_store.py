"""Transactional SQLite storage for the unified personal event workflow.

Legacy JSON stores stay independent. No network, model, crawler or WeChat access
is performed here. Evidence offsets count Unicode code points, with exclusive ends.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .personal_models import CollectionInput, DeadlineInput, EvidenceInput, TaskInput

MIGRATIONS = Path(__file__).with_name("migrations")
SCHEMA_VERSION = 5
TERMINAL = {"done", "cancelled"}


def default_database_path() -> Path:
    override = os.environ.get("ATTENTION_PERSONAL_DB", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "data" / "attention-desk" / "personal.sqlite3"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def identifier() -> str:
    return str(uuid4())


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class PersonalError(Exception):
    def __init__(self, message: str, code: str = "invalid_input", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class PersonalStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connection(self, *, write: bool = False):
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # WAL is persistent; transactions themselves use fresh, thread-owned connections.
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        finally:
            connection.close()
        with self.connection(write=True) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            current = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
            if current > SCHEMA_VERSION:
                raise PersonalError("事项数据库版本比当前应用更新，请使用匹配的应用；数据库未覆盖。", "schema_newer", 503)
            for version in range(current + 1, SCHEMA_VERSION + 1):
                files = list(MIGRATIONS.glob(f"{version:03d}_*.sql"))
                if len(files) != 1:
                    raise RuntimeError(f"Expected exactly one migration for version {version}")
                statement = ""
                for line in files[0].read_text(encoding="utf-8").splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        connection.execute(statement)
                        statement = ""
                if statement.strip():
                    raise RuntimeError("Incomplete migration statement")
                connection.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, now()))

    @staticmethod
    def _event(connection, event_id: str, expected_version: int | None = None, *, editable: bool = False):
        row = connection.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise PersonalError("事项不存在，请返回列表刷新。", "not_found", 404)
        if expected_version is not None and row["version"] != expected_version:
            raise PersonalError("事项已在其他页面更新。请先刷新并核对；本次修改尚未保存。", "conflict", 409)
        if editable and row["status"] in TERMINAL:
            raise PersonalError("请先恢复事项，再修改内容或任务。", "restore_required", 409)
        return row

    @staticmethod
    def _child(connection, table: str, child_id: str, event_id: str):
        if table not in {"deadline", "task"}:
            raise ValueError("Unsupported child table")
        row = connection.execute(f"SELECT * FROM {table} WHERE id = ? AND event_id = ?", (child_id, event_id)).fetchone()
        if row is None:
            raise PersonalError("所选截止时间或任务不属于当前事项。", "invalid_reference", 400)
        return row

    @staticmethod
    def _audit(connection, event_id: str, action: str, entity_id: str, before, after):
        connection.execute("INSERT INTO user_decision VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (identifier(), event_id, action, entity_id, encode(before) if before is not None else None, encode(after), now()))

    @staticmethod
    def _touch(connection, event_id: str):
        connection.execute("UPDATE event SET version = version + 1, updated_at = ? WHERE id = ?", (now(), event_id))

    def _evidence(self, connection, event, field_path: str, evidence: EvidenceInput):
        from .source_metadata import snapshot_with_metadata
        from .documents.blocks import span_source
        snapshot = snapshot_with_metadata(connection, connection.execute("SELECT * FROM raw_snapshot WHERE id=?", (event['raw_snapshot_id'],)).fetchone())
        resolved = span_source(snapshot, evidence.model_dump())
        evidence_id = identifier()
        connection.execute("""INSERT OR IGNORE INTO evidence
            (id,event_id,raw_snapshot_id,field_path,quote,start_offset,end_offset,created_at,source_type,block_id,attachment_id,page,section,bounding_box_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (evidence_id,event['id'],event['raw_snapshot_id'],field_path,evidence.quote,resolved['start_offset'],resolved['end_offset'],now(),
             resolved['source_type'],resolved.get('block_id'),resolved.get('attachment_id'),resolved.get('page'),resolved.get('section'),
             encode(resolved['bounding_box']) if resolved.get('bounding_box') else None))

    @staticmethod
    def _original_phrase(connection, event, phrase: str):
        if phrase:
            original = connection.execute("SELECT original_text FROM raw_snapshot WHERE id = ?", (event["raw_snapshot_id"],)).fetchone()[0]
            if phrase not in original:
                raise PersonalError("原文日期表述必须是原始通知中的准确片段，也可以留空。", "invalid_original_phrase", 400)

    @staticmethod
    def _detail(connection, event_id: str) -> dict:
        event = dict(PersonalStore._event(connection, event_id))
        from .source_metadata import snapshot_with_metadata
        event["snapshot"] = snapshot_with_metadata(connection, connection.execute("SELECT * FROM raw_snapshot WHERE id = ?", (event["raw_snapshot_id"],)).fetchone())
        event["source"] = dict(connection.execute("SELECT s.* FROM source s JOIN raw_item r ON r.source_id = s.id WHERE r.id = ?", (event["raw_item_id"],)).fetchone())
        event["deadlines"] = [dict(row) for row in connection.execute("SELECT * FROM deadline WHERE event_id = ? ORDER BY value, created_at, id", (event_id,))]
        for deadline in event["deadlines"]:
            deadline["confirmed_by_user"] = bool(deadline["confirmed_by_user"])
        event["tasks"] = [dict(row) for row in connection.execute("SELECT * FROM task WHERE event_id = ? ORDER BY sort_order, created_at, id", (event_id,))]
        for task in event["tasks"]:
            task["order"] = task.pop("sort_order")
        event["evidence"] = [dict(row) for row in connection.execute("SELECT * FROM evidence WHERE event_id = ? ORDER BY created_at, id", (event_id,))]
        for evidence in event['evidence']:
            evidence['bounding_box'] = json.loads(evidence.pop('bounding_box_json') or 'null')
        decisions = connection.execute("SELECT * FROM user_decision WHERE event_id = ? ORDER BY created_at DESC, id LIMIT 50", (event_id,))
        event["decisions"] = [{"id": row["id"], "action": row["action"], "entity_id": row["entity_id"],
                               "before": json.loads(row["before_json"]) if row["before_json"] else None,
                               "after": json.loads(row["after_json"]), "created_at": row["created_at"]} for row in decisions]
        context = connection.execute("SELECT * FROM event_context WHERE event_id=?", (event_id,)).fetchone()
        event["context"] = dict(context) if context else None
        if event["context"]:
            for name in ("audience", "eligibility", "materials", "locations", "uncertainties", "review"):
                event["context"][name] = json.loads(event["context"].pop(name + "_json"))
            plan = connection.execute("SELECT * FROM event_task_plan WHERE event_id=?", (event_id,)).fetchone()
            event["context"]["tasks_activated"] = bool(plan and plan["activated_at"])
            event["context"]["planned_actions"] = json.loads(plan["actions_json"]) if plan and not plan["activated_at"] else []
            provenance = connection.execute("SELECT provider,model,prompt_version,schema_version,thinking,created_at FROM ai_extraction WHERE id=?", (context["ai_extraction_id"],)).fetchone()
            event["context"]["extraction"] = dict(provenance) if provenance else None
        from .personal_priority import personal_priority
        event["priority"] = personal_priority(event)
        return event

    def get_event(self, event_id: str) -> dict:
        with self.connection() as connection:
            return self._detail(connection, event_id)

    def list_events(self, *, limit: int = 50, offset: int = 0) -> dict:
        with self.connection() as connection:
            total = connection.execute("SELECT COUNT(*) FROM event").fetchone()[0]
            rows = connection.execute("""SELECT e.*,
                (SELECT COUNT(*) FROM task WHERE event_id = e.id) AS task_count,
                (SELECT COUNT(*) FROM task WHERE event_id = e.id AND status = 'done') AS completed_task_count,
                (SELECT COUNT(*) FROM deadline WHERE event_id = e.id) AS deadline_count
                FROM event e ORDER BY e.updated_at DESC, e.id LIMIT ? OFFSET ?""", (limit, offset))
            return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def create_collection(self, payload: CollectionInput, submission_id: str | None = None) -> dict:
        fingerprint = hashlib.sha256(encode(payload.model_dump()).encode("utf-8")).hexdigest()
        with self.connection(write=True) as connection:
            if submission_id:
                existing = connection.execute("SELECT id, submission_hash FROM raw_item WHERE submission_id = ?", (submission_id,)).fetchone()
                if existing:
                    if existing["submission_hash"] != fingerprint:
                        raise PersonalError("这次保存请求的内容已改变，请重新开始一次收集。", "idempotency_conflict", 409)
                    event = connection.execute("SELECT id FROM event WHERE raw_item_id = ?", (existing["id"],)).fetchone()
                    return self._detail(connection, event["id"])
            stamp, raw_id, snapshot_id, event_id = now(), identifier(), identifier(), identifier()
            connection.execute("INSERT OR IGNORE INTO source VALUES ('manual_text', 'manual_text', '手动粘贴', ?)", (stamp,))
            connection.execute("INSERT INTO raw_item (id,source_id,kind,submission_id,submission_hash,created_at) VALUES (?, 'manual_text', 'manual_text', ?, ?, ?)", (raw_id, submission_id, fingerprint, stamp))
            content_hash = hashlib.sha256(payload.original_text.encode("utf-8")).hexdigest()
            connection.execute("INSERT INTO raw_snapshot (id, raw_item_id, original_text, created_at, source_time, hash) VALUES (?, ?, ?, ?, ?, ?)",
                               (snapshot_id, raw_id, payload.original_text, stamp, payload.source_time, content_hash))
            connection.execute("INSERT INTO event (id, raw_item_id, raw_snapshot_id, title, summary, category, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (event_id, raw_id, snapshot_id, payload.title, payload.summary, payload.category, stamp, stamp))
            self._audit(connection, event_id, "event_created", event_id, None, {"title": payload.title, "status": "needs_review", "snapshot_hash": content_hash})
            return self._detail(connection, event_id)

    def edit_event(self, event_id: str, expected_version: int, fields: dict) -> dict:
        with self.connection(write=True) as connection:
            event = self._event(connection, event_id, expected_version, editable=True)
            connection.execute("UPDATE event SET title = ?, summary = ?, category = ? WHERE id = ?",
                               (fields["title"], fields["summary"], fields["category"], event_id))
            self._audit(connection, event_id, "event_edited", event_id, {name: event[name] for name in fields}, fields)
            self._touch(connection, event_id)
            return self._detail(connection, event_id)

    def change_status(self, event_id: str, expected_version: int, status: str, confirm_incomplete: bool = False) -> dict:
        with self.connection(write=True) as connection:
            event = self._event(connection, event_id, expected_version)
            if event["status"] == status:
                return self._detail(connection, event_id)
            if event["status"] in TERMINAL:
                raise PersonalError("请使用恢复操作，保留之前的处理状态。", "restore_required", 409)
            if status in {"needs_action", "in_progress"}:
                self._require_participation(connection, event_id)
            if status == "done" and not confirm_incomplete:
                remaining = connection.execute("SELECT COUNT(*) FROM task WHERE event_id = ? AND status != 'done'", (event_id,)).fetchone()[0]
                if remaining:
                    raise PersonalError("还有未完成任务，请确认是否仍将整个事项标记完成。", "incomplete_tasks", 409)
            previous = event["status"] if status in TERMINAL else event["previous_status"]
            connection.execute("UPDATE event SET status = ?, previous_status = ? WHERE id = ?", (status, previous, event_id))
            self._audit(connection, event_id, "event_status_changed", event_id, {"status": event["status"]}, {"status": status, "confirmed_incomplete": confirm_incomplete})
            self._touch(connection, event_id)
            return self._detail(connection, event_id)

    def restore_event(self, event_id: str, expected_version: int) -> dict:
        with self.connection(write=True) as connection:
            event = self._event(connection, event_id, expected_version)
            if event["status"] not in TERMINAL:
                return self._detail(connection, event_id)
            status = event["previous_status"] if event["previous_status"] not in TERMINAL and event["previous_status"] else "needs_review"
            connection.execute("UPDATE event SET status = ?, previous_status = NULL WHERE id = ?", (status, event_id))
            self._audit(connection, event_id, "event_restored", event_id, {"status": event["status"]}, {"status": status})
            self._touch(connection, event_id)
            return self._detail(connection, event_id)

    def add_deadline(self, event_id: str, payload: DeadlineInput) -> dict:
        with self.connection(write=True) as connection:
            event = self._event(connection, event_id, payload.expected_version, editable=True)
            self._original_phrase(connection, event, payload.original_text)
            deadline_id, stamp = identifier(), now()
            fields = payload.model_dump(exclude={"expected_version", "evidence"})
            connection.execute("INSERT INTO deadline (id, event_id, label, value, precision, original_text, confirmed_by_user, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (deadline_id, event_id, payload.label, payload.value, payload.precision, payload.original_text, int(payload.confirmed_by_user), stamp, stamp))
            if payload.evidence:
                self._evidence(connection, event, f"deadlines.{deadline_id}.value", payload.evidence)
            self._audit(connection, event_id, "deadline_added", deadline_id, None, fields)
            self._touch(connection, event_id)
            return self._detail(connection, event_id)

    def edit_deadline(self, event_id: str, deadline_id: str, payload: DeadlineInput) -> dict:
        with self.connection(write=True) as connection:
            event = self._event(connection, event_id, payload.expected_version, editable=True)
            deadline = self._child(connection, "deadline", deadline_id, event_id)
            self._original_phrase(connection, event, payload.original_text)
            connection.execute("UPDATE deadline SET label = ?, value = ?, precision = ?, original_text = ?, confirmed_by_user = ?, updated_at = ? WHERE id = ?",
                               (payload.label, payload.value, payload.precision, payload.original_text, int(payload.confirmed_by_user), now(), deadline_id))
            field_path = f"deadlines.{deadline_id}.value"
            before_evidence = [dict(row) for row in connection.execute("SELECT * FROM evidence WHERE event_id = ? AND field_path = ?", (event_id, field_path))]
            if "evidence" in payload.model_fields_set:
                connection.execute("DELETE FROM evidence WHERE event_id = ? AND field_path = ?", (event_id, field_path))
                if payload.evidence:
                    self._evidence(connection, event, field_path, payload.evidence)
            self._audit(connection, event_id, "deadline_edited", deadline_id, {**dict(deadline), "evidence": before_evidence}, payload.model_dump(exclude={"expected_version"}))
            self._touch(connection, event_id)
            return self._detail(connection, event_id)

    @staticmethod
    def _require_participation(connection, event_id):
        context = connection.execute("SELECT participation,relevance,notice_kind FROM event_context WHERE event_id=?", (event_id,)).fetchone()
        if context and (context["participation"] != "yes" or context["relevance"] == "no" or context["notice_kind"] == "cancellation"):
            raise PersonalError("请先确认参加 / 执行；未参加、无关或取消通知不能创建任务。", "participation_required", 409)

    def add_task(self, event_id: str, payload: TaskInput) -> dict:
        with self.connection(write=True) as connection:
            event = self._event(connection, event_id, payload.expected_version, editable=True)
            self._require_participation(connection, event_id)
            if payload.deadline_id:
                self._child(connection, "deadline", payload.deadline_id, event_id)
            task_id, stamp = identifier(), now()
            order = connection.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM task WHERE event_id = ?", (event_id,)).fetchone()[0]
            connection.execute("INSERT INTO task (id, event_id, title, sort_order, deadline_id, origin, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (task_id, event_id, payload.title, order, payload.deadline_id, payload.origin, stamp, stamp))
            if payload.evidence:
                self._evidence(connection, event, f"tasks.{task_id}.title", payload.evidence)
            self._audit(connection, event_id, "task_added", task_id, None, payload.model_dump(exclude={"expected_version"}))
            self._touch(connection, event_id)
            return self._detail(connection, event_id)

    def edit_task(self, event_id: str, task_id: str, expected_version: int, fields: dict) -> dict:
        with self.connection(write=True) as connection:
            event = self._event(connection, event_id, expected_version, editable=True)
            task = self._child(connection, "task", task_id, event_id)
            if not fields:
                return self._detail(connection, event_id)
            if fields.get("deadline_id"):
                self._child(connection, "deadline", fields["deadline_id"], event_id)
            updated = dict(task)
            for name in ("title", "status", "origin", "deadline_id"):
                if name in fields:
                    updated[name] = fields[name]
            if "order" in fields:
                updated["sort_order"] = fields["order"]
            completed_at = (task["completed_at"] or now()) if updated["status"] == "done" else None
            connection.execute("UPDATE task SET title = ?, status = ?, origin = ?, deadline_id = ?, sort_order = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                               (updated["title"], updated["status"], updated["origin"], updated["deadline_id"], updated["sort_order"], completed_at, now(), task_id))
            self._audit(connection, event_id, "task_edited", task_id, dict(task), {**fields, "completed_at": completed_at})
            self._touch(connection, event_id)
            return self._detail(connection, event_id)

    def bind_evidence(self, event_id: str, expected_version: int, field_path: str, evidence: EvidenceInput) -> dict:
        with self.connection(write=True) as connection:
            event = self._event(connection, event_id, expected_version, editable=True)
            if field_path not in {"event.title", "event.summary"}:
                match = field_path.split(".")
                table = {"deadlines": "deadline", "tasks": "task"}.get(match[0]) if len(match) == 3 else None
                if not table or match[2] != ("value" if table == "deadline" else "title"):
                    raise PersonalError("不支持的依据字段。", "invalid_field", 400)
                self._child(connection, table, match[1], event_id)
            self._evidence(connection, event, field_path, evidence)
            self._audit(connection, event_id, "evidence_bound", event_id, None, {"field_path": field_path, **evidence.model_dump()})
            self._touch(connection, event_id)
            return self._detail(connection, event_id)
