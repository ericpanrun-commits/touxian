import unittest

from server.inbox_store import FAILED, LOW, WAITING, InboxStore


OCR = "1： 35PM\n\n1\n\nWed Oct OCR 未核对公式"


class InboxProjectionTests(unittest.TestCase):
    def setUp(self):
        self.store = InboxStore()
        self.image = {
            "input_kind": "image",
            "original_text": OCR,
            "attachments": [{"filename": "IMG_1080.png", "mime_type": "image/png", "extension": "png"}],
        }

    def test_image_without_vision_does_not_preview_ocr(self):
        projected = self.store.project(self.image)
        self.assertEqual(projected["preview"], WAITING)
        self.assertEqual(projected["preview_source"], "waiting")
        self.assertEqual(projected["ocr_preview"], OCR[:240])
        self.assertNotIn("35PM", projected["preview"])

    def test_vision_summary_becomes_preview_and_ocr_stays_debug(self):
        projected = self.store.project(self.image, {
            "status": "completed",
            "provider": "grok",
            "analyzer": "eric-os-bot",
            "analyzed_at": "2026-11-02T08:30:00Z",
            "confidence": "high",
            "resource_id": "capture:88584ff1-867f-47e1-8276-a8412fb96830:506180e3adc4d31a83746d89c202e3188ab7f0b7f728c9b53ac5239c5fbd2ce9",
            "sha256": "506180e3adc4d31a83746d89c202e3188ab7f0b7f728c9b53ac5239c5fbd2ce9",
            "result": {"title": "极限复习", "summary": "导数定义", "probable_course": "高等数学 II", "key_points": ["极限"], "equations": ["f'(x)"]},
        })
        self.assertEqual(projected["preview"], "导数定义")
        self.assertEqual(projected["preview_source"], "vision")
        self.assertEqual(projected["vision_analysis"]["summary"], "导数定义")
        self.assertEqual(projected["ocr_preview"][:4], "1： 3")
        self.assertNotEqual(projected["preview"], projected["ocr_preview"])

    def test_failure_and_low_confidence_copy(self):
        failed = self.store.project(self.image, {"status": "failed", "error": {"message": "no"}})
        low = self.store.project(self.image, {"status": "completed", "confidence": "low", "result": {"summary": "不确定"}})
        self.assertEqual(failed["preview"], FAILED)
        self.assertEqual(low["preview"], LOW)

    def test_human_text_wins_over_vision_and_ocr(self):
        projected = self.store.project(self.image, {"status": "completed", "result": {"summary": "导数定义"}}, human_text="我确认的笔记")
        self.assertEqual(projected["preview"], "我确认的笔记")
        self.assertEqual(projected["preview_source"], "human")

    def test_plain_text_snapshot_keeps_original(self):
        projected = self.store.project({"input_kind": "text", "original_text": "明天下午交作业"})
        self.assertEqual(projected["preview"], "明天下午交作业")
        self.assertIsNone(projected["ocr_preview"])


if __name__ == "__main__":
    unittest.main()
