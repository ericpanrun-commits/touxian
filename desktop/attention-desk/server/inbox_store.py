"""Project a raw snapshot into the cloud inbox payload.

Image and PDF cards do not copy unverified OCR into the default preview.
The Windows publisher should call ``InboxStore.project`` instead of truncating
``raw_snapshot.original_text`` into ``preview``.
"""

from __future__ import annotations

WAITING = "等待 AI 分析…"
FAILED = "AI 分析失败，可重新处理"
LOW = "部分内容需要确认"
PREVIEW_LIMIT = 240
_IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "heic", "heif", "pdf"}


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _is_image_or_pdf(snapshot: dict) -> bool:
    if snapshot.get("input_kind") in {"image", "pdf"}:
        return True
    files = list(snapshot.get("attachments") or []) + list(snapshot.get("files") or [])
    for item in files:
        if not isinstance(item, dict):
            continue
        mime = item.get("mime_type") or item.get("mime") or ""
        if mime.startswith("image/") or mime == "application/pdf":
            return True
        if str(item.get("extension") or "").lower() in _IMAGE_EXT:
            return True
    return False


def _band(vision: dict | None) -> str | None:
    if not isinstance(vision, dict):
        return None
    raw = vision.get("confidence")
    result = vision.get("result") if isinstance(vision.get("result"), dict) else vision.get("vision_analysis")
    if raw in (None, "") and isinstance(result, dict):
        raw = result.get("confidence")
    if raw in {"high", "medium", "low"}:
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and 0 <= raw <= 1:
        if raw >= 0.8:
            return "high"
        if raw >= 0.5:
            return "medium"
        return "low"
    return None


def _analysis(vision: dict) -> dict:
    result = vision.get("result")
    if isinstance(result, dict):
        return result
    analysis = vision.get("vision_analysis")
    return analysis if isinstance(analysis, dict) else {}


class InboxStore:
    """Cloud inbox projection. Distinct from the WeChat zip watcher in inbox.py."""

    def project(self, snapshot: dict | None, vision: dict | None = None, human_text: str | None = None) -> dict:
        snapshot = snapshot or {}
        image = _is_image_or_pdf(snapshot)
        original = _text(snapshot.get("original_text")).replace("\r\n", "\n").strip()
        ocr_preview = original[:PREVIEW_LIMIT] if image and original else None
        analysis = _analysis(vision) if isinstance(vision, dict) else {}
        status = vision.get("status") or vision.get("vision_status") if isinstance(vision, dict) else None
        band = _band(vision)
        human = _text(human_text).strip()
        notice = None
        if human:
            preview, source = human[:PREVIEW_LIMIT], "human"
        elif status == "failed":
            preview, source, notice = FAILED, "failed", FAILED
        elif status == "completed" and band == "low":
            preview, source, notice = LOW, "low_confidence", LOW
        elif status == "completed":
            preview = _text(analysis.get("summary") or analysis.get("readable_content") or analysis.get("title") or WAITING)[:PREVIEW_LIMIT]
            source = "vision"
        elif image:
            preview, source, notice = WAITING, "waiting", WAITING
        else:
            preview, source = (original[:PREVIEW_LIMIT] if original else ""), "text"
        projected = {
            "preview": preview,
            "preview_source": source,
            "vision_notice": notice,
            "ocr_preview": ocr_preview,
        }
        if isinstance(vision, dict) and status in {"pending", "completed", "failed"}:
            projected.update({
                "vision_status": status,
                "vision_provider": vision.get("provider") or vision.get("analyzer"),
                "vision_analysis": analysis,
                "vision_confidence": band,
                "vision_processed_at": vision.get("analyzed_at") or vision.get("vision_processed_at"),
                "vision_error": vision.get("error") if status == "failed" else None,
                "vision_resource_id": vision.get("resource_id"),
                "vision_sha256": vision.get("sha256"),
            })
        return projected
