"""Durable raw intake and review; formal entities are written only by confirmation."""
from __future__ import annotations

import hashlib
import json
from .extraction_models import ConfirmationInput, RawInput
from .personal_store import PersonalError, PersonalStore, encode, identifier, now
from .source_metadata import snapshot_with_metadata


class ReviewStore:
    def __init__(self, personal: PersonalStore):
        self.personal = personal

    @staticmethod
    def _review(connection, snapshot_id, expected_version=None, *, editable=False):
        row = connection.execute("SELECT * FROM raw_review WHERE raw_snapshot_id = ?", (snapshot_id,)).fetchone()
        if row is None:
            raise PersonalError("待确认原文不存在，请返回列表。", "not_found", 404)
        if expected_version is not None and row["version"] != expected_version:
            raise PersonalError("提取或草稿已更新，请重新读取后核对；本次输入没有覆盖已保存内容。", "conflict", 409)
        if editable and row["status"] != "pending":
            raise PersonalError("这条原文已确认或已取消，请重新读取状态。", "review_closed", 409)
        return row

    @staticmethod
    def _bundle(connection, snapshot_id):
        review = dict(ReviewStore._review(connection, snapshot_id))
        review["snapshot"] = snapshot_with_metadata(connection, connection.execute("SELECT * FROM raw_snapshot WHERE id = ?", (snapshot_id,)).fetchone())
        review['item'] = dict(connection.execute('SELECT * FROM raw_item WHERE id=?', (review['snapshot']['raw_item_id'],)).fetchone())
        review['item']['source_type'] = review['item']['kind']
        review['source'] = dict(connection.execute('SELECT * FROM source WHERE id=?', (review['item']['source_id'],)).fetchone())
        for name in ("draft_json", "confirmed_event_ids"):
            review[name.removesuffix("_json")] = json.loads(review[name]) if review[name] else None
            if name.endswith("_json"):
                del review[name]
        review["extraction"] = ReviewStore._extraction(connection, review["extraction_id"]) if review["extraction_id"] else None
        from .planning.source_update import comparison
        review['source_update'] = comparison(connection,review['snapshot']['raw_item_id'],snapshot_id,review['extraction'])
        return review

    @staticmethod
    def _extraction(connection, extraction_id):
        row = connection.execute("SELECT * FROM ai_extraction WHERE id = ?", (extraction_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for stored, public in (("parsed_output", "parsed_output"), ("validation_json", "validation"), ("error_json", "error"),
                               ("token_usage_json", "token_usage"), ("config_json", "configuration"), ("progress_json", "progress"), ("plan_json", "plan")):
            value = result.pop(stored)
            result[public] = json.loads(value) if value else None
        result["thinking"] = bool(result["thinking"])
        result['chunks'] = []
        for chunk in connection.execute('SELECT * FROM ai_chunk_extraction WHERE extraction_id=? ORDER BY created_at,id',(extraction_id,)):
            value = dict(chunk)
            for stored, public in [('scope_json','sources'),('config_json','configuration'),('error_json','error'),('token_usage_json','token_usage'),('validation_json','validation')]:
                value[public] = json.loads(value.pop(stored) or 'null')
            value.pop('raw_output',None)
            value.pop('parsed_output',None)
            result['chunks'].append(value)
        return result

    def get(self, snapshot_id):
        with self.personal.connection() as connection:
            return self._bundle(connection, snapshot_id)

    def list(self, limit=50, offset=0, source_id=None):
        with self.personal.connection() as connection:
            where = "r.status != 'confirmed'" + (' AND (i.source_id=? OR EXISTS (SELECT 1 FROM official_item_source os WHERE os.raw_item_id=i.id AND os.source_id=?))' if source_id else '')
            # A phone intake may gain a parsed snapshot; queue only its newest version.
            where += " AND (i.source_id!='mobile' OR NOT EXISTS (SELECT 1 FROM raw_snapshot newer WHERE newer.raw_item_id=s.raw_item_id AND (newer.created_at>s.created_at OR (newer.created_at=s.created_at AND newer.id>s.id))))"
            parameters = [source_id, source_id] if source_id else []
            total = connection.execute('SELECT COUNT(*) FROM raw_review r JOIN raw_snapshot s ON s.id=r.raw_snapshot_id JOIN raw_item i ON i.id=s.raw_item_id WHERE ' + where, parameters).fetchone()[0]
            rows = connection.execute("""SELECT r.raw_snapshot_id, r.title_hint, r.status, r.updated_at, r.version,
                substr(s.original_text, 1, 160) AS preview, a.status AS extraction_status,
                src.name AS source_name,src.kind AS source_kind,w.published_at,w.version AS snapshot_version,w.attachments_json
                FROM raw_review r JOIN raw_snapshot s ON s.id=r.raw_snapshot_id JOIN raw_item i ON i.id=s.raw_item_id
                JOIN source src ON src.id=i.source_id LEFT JOIN raw_snapshot_web w ON w.raw_snapshot_id=s.id
                LEFT JOIN ai_extraction a ON a.id=r.extraction_id WHERE """ + where + ' ORDER BY r.updated_at DESC, r.raw_snapshot_id LIMIT ? OFFSET ?', [*parameters, limit, offset])
            items = [dict(row) for row in rows]
            for item in items:
                item.pop('attachments_json')
                item['attachment_count'] = connection.execute('SELECT COUNT(*) FROM snapshot_attachment WHERE raw_snapshot_id=?',(item['raw_snapshot_id'],)).fetchone()[0]
                item['parsed_attachment_count'] = connection.execute("SELECT COUNT(*) FROM snapshot_attachment sa JOIN attachment a ON a.id=sa.attachment_id WHERE sa.raw_snapshot_id=? AND a.parse_status='parsed'",(item['raw_snapshot_id'],)).fetchone()[0]
            sources = [dict(row) for row in connection.execute('SELECT id,name,kind FROM source ORDER BY created_at,id')]
            return {"items": items, "sources": sources, "total": total, "limit": limit, "offset": offset}

    @staticmethod
    def _new_snapshot(connection, raw_id, text, stamp, source_time, title_hint):
        snapshot_id = identifier()
        connection.execute("INSERT INTO raw_snapshot (id,raw_item_id,original_text,created_at,source_time,hash) VALUES (?,?,?,?,?,?)",
                           (snapshot_id, raw_id, text, stamp, source_time, hashlib.sha256(text.encode('utf-8')).hexdigest()))
        connection.execute("INSERT INTO raw_review (raw_snapshot_id,title_hint,created_at,updated_at) VALUES (?,?,?,?)", (snapshot_id, title_hint, stamp, stamp))
        return snapshot_id

    def collect(self, payload: RawInput, submission_id=None, *, source_id='manual_text', source_kind='manual_text', source_name='手动粘贴', raw_kind=None):
        fingerprint = hashlib.sha256(encode(payload.model_dump()).encode("utf-8")).hexdigest()
        with self.personal.connection(write=True) as connection:
            if submission_id:
                existing = connection.execute("SELECT id, submission_hash FROM raw_item WHERE submission_id=?", (submission_id,)).fetchone()
                if existing:
                    if existing["submission_hash"] != fingerprint:
                        raise PersonalError("保存请求内容已改变，请开始新的收集请求。", "idempotency_conflict", 409)
                    snapshot = connection.execute("SELECT id FROM raw_snapshot WHERE raw_item_id=?", (existing["id"],)).fetchone()
                    return self._bundle(connection, snapshot["id"])
            stamp, raw_id = now(), identifier()
            connection.execute("INSERT OR IGNORE INTO source VALUES (?, ?, ?, ?)", (source_id, source_kind, source_name, stamp))
            connection.execute("INSERT INTO raw_item (id,source_id,kind,submission_id,submission_hash,created_at) VALUES (?, ?, ?, ?, ?, ?)", (raw_id, source_id, raw_kind or source_kind, submission_id, fingerprint, stamp))
            snapshot_id = self._new_snapshot(connection, raw_id, payload.original_text, stamp, payload.source_time, payload.title_hint)
            return self._bundle(connection, snapshot_id)

    def save_draft(self, snapshot_id, expected_version, draft):
        if len(encode(draft)) > 250000:
            raise PersonalError("草稿内容过大，请缩小后再保存。")
        with self.personal.connection(write=True) as connection:
            self._review(connection, snapshot_id, expected_version, editable=True)
            connection.execute("UPDATE raw_review SET draft_json=?, version=version+1, updated_at=? WHERE raw_snapshot_id=?", (encode(draft), now(), snapshot_id))
            return self._bundle(connection, snapshot_id)

    def set_status(self, snapshot_id, expected_version, status):
        with self.personal.connection(write=True) as connection:
            review = self._review(connection, snapshot_id, expected_version)
            if review["status"] == "confirmed":
                raise PersonalError("原文已经确认，正式事项仍保留。", "review_closed", 409)
            connection.execute("UPDATE raw_review SET status=?,version=version+1,updated_at=? WHERE raw_snapshot_id=?", (status, now(), snapshot_id))
            return self._bundle(connection, snapshot_id)

    def confirm(self, snapshot_id: str, payload: ConfirmationInput):
        data = payload.model_dump(exclude={"expected_version"})
        fingerprint = hashlib.sha256(encode(data).encode("utf-8")).hexdigest()
        with self.personal.connection(write=True) as connection:
            review = self._review(connection, snapshot_id)
            if review["status"] == "confirmed":
                if review["confirmation_hash"] != fingerprint:
                    raise PersonalError("这条原文已用其他内容确认，不会重复创建事项。", "confirmation_conflict", 409)
                ids = json.loads(review["confirmed_event_ids"])
                return {"events": [self.personal._detail(connection, value) for value in ids], "review": self._bundle(connection, snapshot_id)}
            self._review(connection, snapshot_id, payload.expected_version, editable=True)
            if review["extraction_id"] != payload.extraction_id:
                raise PersonalError("分析版本已改变，请重新读取预览。", "conflict", 409)
            extraction = self._extraction(connection, payload.extraction_id) if payload.extraction_id else None
            if extraction and extraction["status"] in {"running", "waiting"}:
                raise PersonalError("分析仍在进行，请等待完成后确认。", "analysis_running", 409)
            snapshot_meta = snapshot_with_metadata(connection,connection.execute('SELECT * FROM raw_snapshot WHERE id=?',(snapshot_id,)).fetchone())
            if snapshot_meta.get('attachments') and not snapshot_meta.get('blocks') and not snapshot_meta.get('web'):
                raise PersonalError('请先解析文件，再确认其中的内容。','files_pending',409)
            if (snapshot_meta.get('document_warnings') or extraction and (extraction.get("validation") or {}).get("warnings")) and not payload.acknowledged_warnings:
                raise PersonalError("请核对年份、引用和其他不确定项，再明确确认。", "review_required", 400)
            selected = [event for event in payload.events if event.selected]
            if not selected:
                raise PersonalError("请至少选择一个事项，或取消本次整理。")
            if len({event.id for event in payload.events}) != len(payload.events):
                raise PersonalError("事项编号重复，请核对。")
            snapshot = dict(connection.execute("SELECT * FROM raw_snapshot WHERE id=?", (snapshot_id,)).fetchone())
            event_ids = []
            for candidate in selected:
                event_id, stamp = identifier(), now()
                declined = candidate.participation == "no" or candidate.relevance == "no"
                status = "cancelled" if declined else "needs_review"
                connection.execute("INSERT INTO event (id,raw_item_id,raw_snapshot_id,title,summary,category,status,previous_status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                   (event_id, snapshot["raw_item_id"], snapshot_id, candidate.title, candidate.summary, candidate.category, status, "needs_review" if declined else None, stamp, stamp))
                event = self.personal._event(connection, event_id)
                deadline_map = {}
                if len({value.id for value in candidate.deadlines}) != len(candidate.deadlines) or len({value.id for value in candidate.actions}) != len(candidate.actions):
                    raise PersonalError("日期或行动编号重复，请核对。")
                for deadline in candidate.deadlines:
                    if not deadline.selected:
                        continue
                    self.personal._original_phrase(connection, event, deadline.original_text)
                    deadline_id = identifier()
                    deadline_map[deadline.id] = deadline_id
                    connection.execute("INSERT INTO deadline (id,event_id,label,value,precision,original_text,confirmed_by_user,created_at,updated_at) VALUES (?,?,?,?,?,?,1,?,?)",
                                       (deadline_id, event_id, deadline.role, deadline.value, deadline.precision, deadline.original_text, stamp, stamp))
                    for evidence in deadline.evidence:
                        self.personal._evidence(connection, event, f"deadlines.{deadline_id}.value", evidence)
                for name in ("audience", "eligibility", "materials", "locations"):
                    facts = [candidate.audience] if name == "audience" and candidate.audience else [] if name == "audience" else getattr(candidate, name)
                    for index, fact in enumerate(facts):
                        for evidence in fact.evidence:
                            self.personal._evidence(connection, event, f"{name}.{index}.text", evidence)
                actions = [action.model_dump() for action in candidate.actions if action.selected]
                for action in candidate.actions:
                    if not action.selected:
                        continue
                    if action.deadline_id and action.deadline_id not in deadline_map:
                        raise PersonalError("行动关联了未选中或不存在的日期，请重新选择。", "invalid_reference", 400)
                    # Validate even deferred actions: bad quotes cannot enter confirmed plans.
                    for evidence in action.evidence:
                        self.personal._evidence(connection, event, f"planned_actions.{action.id}.title", evidence)
                record = candidate.model_dump()
                connection.execute("INSERT INTO event_context VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                   (event_id, payload.extraction_id, candidate.id, candidate.relevance, candidate.participation, candidate.notice_kind,
                                    encode(record["audience"]), encode(record["eligibility"]), encode(record["materials"]), encode(record["locations"]),
                                    encode(record["uncertainties"]), encode(record), stamp))
                connection.execute("INSERT INTO event_task_plan (event_id,actions_json,deadline_ids_json) VALUES (?,?,?)", (event_id, encode(actions), encode(deadline_map)))
                if candidate.participation == "yes" and candidate.relevance != "no" and candidate.notice_kind != "cancellation":
                    self._activate_tasks(connection, event, actions, deadline_map)
                self.personal._audit(connection, event_id, "extraction_confirmed", event_id, None,
                                     {"ai_extraction_id": payload.extraction_id, "review": record, "acknowledged_warnings": payload.acknowledged_warnings})
                event_ids.append(event_id)
            connection.execute("UPDATE raw_review SET status='confirmed',version=version+1,draft_json=?,confirmation_hash=?,confirmed_event_ids=?,updated_at=? WHERE raw_snapshot_id=?",
                               (encode(data), fingerprint, encode(event_ids), now(), snapshot_id))
            return {"events": [self.personal._detail(connection, value) for value in event_ids], "review": self._bundle(connection, snapshot_id)}

    def _activate_tasks(self, connection, event, actions, deadline_map):
        first_order = connection.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM task WHERE event_id=?", (event["id"],)).fetchone()[0]
        for order, action in enumerate(actions, start=first_order):
            task_id, stamp = identifier(), now()
            connection.execute("INSERT INTO task (id,event_id,title,sort_order,deadline_id,origin,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                               (task_id, event["id"], action["title"], order, deadline_map.get(action["deadline_id"]), action["kind"], stamp, stamp))
            from .personal_models import EvidenceInput
            for evidence in action["evidence"]:
                self.personal._evidence(connection, event, f"tasks.{task_id}.title", EvidenceInput.model_validate(evidence))
        connection.execute("UPDATE event_task_plan SET activated_at=? WHERE event_id=?", (now(), event["id"]))

    def participation(self, event_id, expected_version, choice):
        with self.personal.connection(write=True) as connection:
            event = self.personal._event(connection, event_id, expected_version, editable=True)
            context = connection.execute("SELECT * FROM event_context WHERE event_id=?", (event_id,)).fetchone()
            plan = connection.execute("SELECT * FROM event_task_plan WHERE event_id=?", (event_id,)).fetchone()
            if context is None or plan is None:
                raise PersonalError("这条事项没有待决定的行动方案。")
            if plan["activated_at"]:
                raise PersonalError("任务已按你的确认创建，可使用事项取消或任务完成操作。", "already_participating", 409)
            if choice == "yes" and context["relevance"] == "no":
                raise PersonalError("你此前确认此事项与你无关，不能直接创建任务。")
            if choice == "yes" and context["notice_kind"] != "cancellation":
                self._activate_tasks(connection, event, json.loads(plan["actions_json"]), json.loads(plan["deadline_ids_json"]))
            if choice == "no":
                connection.execute("UPDATE event SET status='cancelled',previous_status=? WHERE id=?", (event["status"], event_id))
            connection.execute("UPDATE event_context SET participation=? WHERE event_id=?", (choice, event_id))
            self.personal._audit(connection, event_id, "participation_decided", event_id, {"participation": context["participation"]}, {"participation": choice})
            self.personal._touch(connection, event_id)
            return self.personal._detail(connection, event_id)
