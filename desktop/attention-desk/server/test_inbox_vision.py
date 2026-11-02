import sqlite3
import unittest

from server.inbox_store import FAILED, LOW, WAITING, InboxStore


OCR = "1： 35PM\n\n1\n\nWed Oct OCR 未核对公式"
VISION = {
    "status": "completed",
    "provider": "grok",
    "analyzer": "eric-os-bot",
    "analyzed_at": "2026-11-02T08:30:00Z",
    "confidence": "high",
    "resource_id": "capture:88584ff1-867f-47e1-8276-a8412fb96830:506180e3adc4d31a83746d89c202e3188ab7f0b7f728c9b53ac5239c5fbd2ce9",
    "sha256": "506180e3adc4d31a83746d89c202e3188ab7f0b7f728c9b53ac5239c5fbd2ce9",
    "result": {"title": "极限复习", "summary": "导数定义", "probable_course": "高等数学 II", "key_points": ["极限"], "equations": ["f'(x)"]},
}


class InboxProjectionTests(unittest.TestCase):
    def setUp(self):
        self.store = InboxStore(object())
        self.image = {
            "input_kind": "image",
            "original_text": OCR,
            "attachments": [{"filename": "IMG_1080.png", "mime_type": "image/png", "extension": "png"}],
        }

    def test_constructor_requires_personal_store(self):
        personal = object()
        store = InboxStore(personal)
        self.assertIs(store.personal, personal)
        with self.assertRaises(TypeError):
            InboxStore()
        self.assertTrue(callable(store.list))
        self.assertTrue(callable(store.detail))
        self.assertTrue(callable(store.link))

    def test_image_without_vision_does_not_preview_ocr(self):
        projected = self.store.project_snapshot(self.image)
        self.assertEqual(projected["preview"], WAITING)
        self.assertEqual(projected["preview_source"], "waiting")
        self.assertEqual(projected["ocr_preview"], OCR[:240])
        self.assertNotIn("35PM", projected["preview"])

    def test_vision_summary_becomes_preview_and_ocr_stays_debug(self):
        projected = self.store.project_snapshot(self.image, VISION)
        self.assertEqual(projected["preview"], "导数定义")
        self.assertEqual(projected["preview_source"], "vision")
        self.assertEqual(projected["vision_analysis"]["summary"], "导数定义")
        self.assertEqual(projected["ocr_preview"][:4], "1： 3")
        self.assertNotEqual(projected["preview"], projected["ocr_preview"])

    def test_failure_and_low_confidence_copy(self):
        failed = self.store.project_snapshot(self.image, {"status": "failed", "error": {"message": "no"}})
        low = self.store.project_snapshot(self.image, {"status": "completed", "confidence": "low", "result": {"summary": "不确定"}})
        self.assertEqual(failed["preview"], FAILED)
        self.assertEqual(low["preview"], LOW)
        self.assertEqual(failed["ocr_preview"], OCR[:240])
        self.assertEqual(low["ocr_preview"], OCR[:240])
        self.assertNotIn("35PM", failed["preview"])
        self.assertNotIn("35PM", low["preview"])

    def test_human_text_wins_over_vision_and_ocr(self):
        projected = self.store.project_snapshot(self.image, {"status": "completed", "result": {"summary": "导数定义"}}, human_text="我确认的笔记")
        self.assertEqual(projected["preview"], "我确认的笔记")
        self.assertEqual(projected["preview_source"], "human")

    def test_plain_text_snapshot_keeps_original(self):
        projected = self.store.project_snapshot({"input_kind": "text", "original_text": "明天下午交作业"})
        self.assertEqual(projected["preview"], "明天下午交作业")
        self.assertIsNone(projected["ocr_preview"])

    def test_pdf_extension_without_input_kind_hides_ocr(self):
        projected = self.store.project_snapshot({"original_text": OCR, "files": [{"extension": "pdf", "filename": "notes.pdf"}]})
        self.assertEqual(projected["preview"], WAITING)
        self.assertEqual(projected["preview_source"], "waiting")
        self.assertEqual(projected["ocr_preview"], OCR[:240])

    def test_numeric_confidence_bands(self):
        low = self.store.project_snapshot(self.image, {"status": "completed", "confidence": 0.2, "result": {"summary": "不确定"}})
        high = self.store.project_snapshot(self.image, {"status": "completed", "confidence": 0.9, "result": {"summary": "导数定义"}})
        self.assertEqual(low["preview"], LOW)
        self.assertEqual(low["preview_source"], "low_confidence")
        self.assertEqual(high["preview"], "导数定义")
        self.assertEqual(high["vision_confidence"], "high")

    def test_pending_vision_keeps_waiting_copy(self):
        projected = self.store.project_snapshot(self.image, {"status": "pending", "provider": "grok"})
        self.assertEqual(projected["preview"], WAITING)
        self.assertEqual(projected["vision_status"], "pending")
        self.assertEqual(projected["ocr_preview"], OCR[:240])


def _row(text, inbox_kind):
    return {
        "id": "raw-1",
        "created_at": "2026-11-02T00:00:00Z",
        "source_name": "桌面",
        "source_kind": "desktop",
        "inbox_kind": inbox_kind,
        "kind": inbox_kind,
        "original_url": None,
        "original_text": text,
        "parsed_output": None,
        "inbox_status": "completed",
        "inbox_stage": "classified",
        "inbox_error_json": None,
        "ai_status": None,
        "review_status": None,
        "ai_error": None,
        "title_hint": None,
        "current_snapshot_id": "snap-1",
        "inbox_original_snapshot_id": "snap-1",
        "inbox_course_id": None,
        "inbox_project_ref": None,
        "inbox_classification_json": None,
        "inbox_version": 1,
    }


def _connection(needs_review=0):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE attachment (
            id TEXT, filename TEXT, extension TEXT, mime_type TEXT, size INTEGER,
            parse_status TEXT, parse_error TEXT, sha256 TEXT, needs_review INTEGER
        );
        CREATE TABLE snapshot_attachment (raw_snapshot_id TEXT, attachment_id TEXT);
        CREATE TABLE course (id TEXT, name TEXT);
        """
    )
    connection.execute(
        "INSERT INTO attachment VALUES (?,?,?,?,?,?,?,?,?)",
        ("a1", "IMG_1080.png", "png", "image/png", 10, "parsed", None, "abc", needs_review),
    )
    connection.execute("INSERT INTO snapshot_attachment VALUES (?,?)", ("snap-1", "a1"))
    return connection


class InboxRowProjectionTests(unittest.TestCase):
    def test_image_row_preview_is_waiting_and_ocr_is_separate(self):
        item = InboxStore.project(_connection(needs_review=1), _row(OCR, "image"))
        self.assertEqual(item["preview"], WAITING)
        self.assertEqual(item["preview_source"], "waiting")
        self.assertEqual(item["vision_notice"], WAITING)
        self.assertEqual(item["ocr_preview"], OCR[:160])
        self.assertNotIn("35PM", item["preview"])
        self.assertEqual(item["processing_status"], "needs_review")
        self.assertEqual(item["input_kind"], "image")

    def test_image_row_uses_vision_then_human_without_leaking_ocr(self):
        connection = _connection()
        seen = InboxStore.project(connection, _row(OCR, "image"), vision=VISION)
        self.assertEqual(seen["preview"], "导数定义")
        self.assertEqual(seen["preview_source"], "vision")
        self.assertEqual(seen["vision_status"], "completed")
        self.assertEqual(seen["vision_analysis"]["summary"], "导数定义")
        self.assertEqual(seen["ocr_preview"], OCR[:160])
        self.assertNotIn("35PM", seen["preview"])
        confirmed = InboxStore.project(connection, _row(OCR, "image"), vision=VISION, human_text="我确认的笔记")
        self.assertEqual(confirmed["preview"], "我确认的笔记")
        self.assertEqual(confirmed["preview_source"], "human")
        self.assertEqual(confirmed["ocr_preview"], OCR[:160])
        failed = InboxStore.project(connection, _row(OCR, "pdf"), vision={"status": "failed", "error": {"message": "no"}})
        self.assertEqual(failed["preview"], FAILED)
        self.assertEqual(failed["input_kind"], "pdf")
        self.assertEqual(failed["ocr_preview"], OCR[:160])

    def test_single_image_attachment_infers_waiting_preview(self):
        row = _row(OCR, None)
        row["kind"] = "file"
        item = InboxStore.project(_connection(), row)
        self.assertIsNone(row["inbox_kind"])
        self.assertEqual(item["input_kind"], "image")
        self.assertEqual(item["preview"], WAITING)
        self.assertEqual(item["ocr_preview"], OCR[:160])
        self.assertNotIn("35PM", item["preview"])

    def test_text_row_keeps_original_preview(self):
        item = InboxStore.project(_connection(), _row("明天下午交作业", "text"))
        self.assertEqual(item["preview"], "明天下午交作业")
        self.assertEqual(item["preview_source"], "text")
        self.assertIsNone(item["ocr_preview"])
        self.assertIsNone(item["vision_notice"])


if __name__ == "__main__":
    unittest.main()
