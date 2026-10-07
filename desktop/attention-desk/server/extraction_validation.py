"""Deterministic validation of model claims, dates and exact source spans."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pydantic import ValidationError
from .extraction_models import ExtractionOutput
from .extraction_errors import ExtractionFailure
from .personal_models import normalize_time

# Contemporary school notices use China standard time (UTC+08:00), without DST.
CHINA = timezone(timedelta(hours=8), "Asia/Shanghai")


def deadline_state(value: str, precision: str, *, at=None) -> dict:
    current = at or datetime.now(CHINA)
    current = current.astimezone(CHINA)
    target = date.fromisoformat(value[:10])
    days = (target - current.date()).days
    overdue = days < 0 if precision == "date" else datetime.fromisoformat(value).replace(tzinfo=CHINA) < current
    return {"days_remaining": days, "overdue": overdue, "timezone": "Asia/Shanghai",
            "status": "overdue" if overdue else "today" if days == 0 else "upcoming", "precision": precision}


def parse_output(raw: str) -> dict:
    try:
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON key")
                result[key] = value
            return result
        parsed = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid JSON constant")))
    except ValueError:
        raise ExtractionFailure("invalid_json", "模型输出不是完整、严格的 JSON，原文已保留。", raw_output=raw)
    try:
        return ExtractionOutput.model_validate(parsed).model_dump()
    except ValidationError:
        raise ExtractionFailure("invalid_schema", "模型结果不符合提取 Schema，不能正式创建事项。可重新分析或手动整理。", raw_output=raw)


def validate_candidate(parsed: dict, snapshot: dict) -> dict:
    text = snapshot["original_text"]
    warnings, spans, fields = list(snapshot.get('document_warnings',[])), {}, {}
    from .documents.blocks import span_source
    from .personal_store import PersonalError
    def warn(path, code, message):
        warnings.append({"field_path": path, "code": code, "message": message})
        fields.setdefault(path, []).append(code)
    for evidence in parsed["evidence"]:
        span = dict(evidence)
        span["raw_snapshot_id"] = snapshot["id"]
        try:
            resolved=span_source(snapshot,evidence)
            span.update({k:resolved.get(k) for k in ('source_type','attachment_id','page','section','bounding_box','filename')})
            valid=True
        except PersonalError: valid=False
        if evidence["id"] in spans:
            valid = False
            warn("evidence." + evidence["id"], "duplicate_id", "依据编号重复，需要重新核对。")
        span["valid"] = valid
        spans[evidence["id"]] = span
        if not valid:
            warn("evidence." + evidence["id"], "invalid_evidence", "引用内容或起止位置与原文不一致。")
    def references(path, ids, *, optional=False):
        if not ids and not optional:
            warn(path, "missing_evidence", "没有准确原文依据，请人工核对。")
        for value in ids:
            if value not in spans or not spans[value]["valid"]:
                warn(path, "invalid_evidence", "此字段引用了无效依据，请更正或移除引用后人工确认。")
    event_ids = set()
    for event in parsed["events"]:
        prefix = "events." + event["id"]
        if event["id"] in event_ids:
            warn(prefix, "duplicate_id", "事项编号重复，需要人工核对。")
        event_ids.add(event["id"])
        for index, _ in enumerate(event["uncertainties"]):
            warn(f"{prefix}.uncertainties.{index}", "model_uncertain", "模型留下不确定项，请核对并编辑。")
        if event["audience"]:
            references(prefix + ".audience", event["audience"]["evidence_ids"])
        if event["participation"] != "required":
            warn(prefix + ".participation", "participation_decision", "请你决定是否参加；AI 不会替你报名。")
        if event["notice_kind"] == "cancellation":
            warn(prefix + ".notice_kind", "cancellation", "这是取消通知，请核对影响；不会自动取消已有事项。")
        if event["notice_kind"] == "update":
            warn(prefix + ".notice_kind", "update", "这是变更通知，请核对旧、新日期；不会自动覆盖已有事项。")
        for name in ("eligibility", "materials", "locations"):
            for index, fact in enumerate(event[name]):
                references(f"{prefix}.{name}.{index}", fact["evidence_ids"])
                if name == "materials" and not snapshot.get('blocks'):
                    message = "附件链接已记录，但文件未下载或解析，请核对原文件。" if snapshot.get('web', {}).get('attachments') else "原文只提及材料，本次输入没有提供附件文件。"
                    warn(f"{prefix}.{name}.{index}", "material_missing", message)
        action_ids = set()
        for action in event["actions"]:
            path = prefix + ".actions." + action["id"]
            if action["id"] in action_ids:
                warn(path, "duplicate_id", "行动编号重复。")
            action_ids.add(action["id"])
            references(path, action["evidence_ids"], optional=action["kind"] == "suggested")
        deadline_ids = set()
        for deadline in event["deadlines"]:
            path = prefix + ".deadlines." + deadline["id"]
            if deadline["id"] in deadline_ids:
                warn(path, "duplicate_id", "日期编号重复。")
            deadline_ids.add(deadline["id"])
            references(path, deadline["evidence_ids"])
            original = deadline["original_text"]
            if original not in text:
                warn(path, "invalid_original_phrase", "日期表述没有出现在原文中。")
            if snapshot.get('blocks'):
                linked=[spans.get(i) for i in deadline['evidence_ids']]
                if not any(s and s['valid'] and original in next((b['text'] for b in snapshot['blocks'] if b['block_id']==s.get('block_id')),'') for s in linked):
                    warn(path,'invalid_original_phrase','日期表述必须出现在它引用的文件或正文块中。')
            if set(deadline["related_action_ids"]) - action_ids:
                warn(path, "invalid_action_reference", "关联的行动不存在。")
            value = deadline["normalized_date"]
            source = snapshot.get("source_time")
            anchor = date.fromisoformat(source[:10]) if source else None
            relative = any(word in original for word in ("明天", "后天", "今天", "下周", "本周"))
            if relative and not anchor:
                warn(path, "relative_without_source_time", "相对日期缺少可靠原消息时间，不能用今天推算。")
            explicit_years = {int(year) for year in re.findall(r"(?<!\d)((?:19|20|21)\d{2})(?:年|[-/])", original)}
            if not explicit_years and not anchor:
                warn(path, "uncertain_year", "原文没有可可靠确定的年份，必须由你核对。")
            try:
                if not value or deadline["precision"] == "unknown":
                    raise ValueError("Unresolved date")
                normalize_time(value, deadline["precision"])
            except ValueError:
                warn(path, "invalid_date", "日期尚未确定或不合法，请填写有效日期后确认。")
            else:
                year = int(value[:4])
                expected_year = anchor.year + (1 if "明年" in original else -1 if "去年" in original else 0) if anchor else None
                if (explicit_years or anchor) and year not in explicit_years and (expected_year is None or year != expected_year):
                    warn(path, "uncertain_year", "原文没有可可靠确定的年份，必须由你核对。")
                month_day = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", original)
                if month_day and (int(value[5:7]), int(value[8:10])) != tuple(map(int, month_day.groups())):
                    warn(path, "date_mismatch", "规范日期的月日与原文不同，请核对。")
                if relative:
                    if anchor and "明天" in original and value[:10] != (anchor + timedelta(days=1)).isoformat():
                        warn(path, "date_mismatch", "明天的规范日期与原消息时间不一致。")
                if deadline["precision"] == "minute" and not re.search(r"(?:\d{1,2}\s*[:：]\s*\d{2}|\d{1,2}\s*[点时])", original):
                    warn(path, "invented_time", "原文没有明确时分，不应补出具体时刻。")
                deadline["date_state"] = deadline_state(value, deadline["precision"])
            if deadline["uncertain"]:
                warn(path, "model_uncertain", "模型对这个日期有疑问，请人工核对。")
            deadline["uncertain"] = deadline["uncertain"] or path in fields
    return {"status": "needs_review" if warnings else "valid", "warnings": warnings, "evidence": list(spans.values()), "fields": fields}
