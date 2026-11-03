# Vision worker contract (first image)

Eric OS Bot calls `eric-app-api` with `Authorization: Bearer <ERIC_VISION_BOT_SECRET>`.
The secret is an Edge Function secret of at least 16 ASCII characters. Set it in the Supabase Dashboard and give the same value to the bot out of band. It is not the Windows `ework_` publisher key and it is not `service_role`.

`verify_jwt` stays off. The function checks the bearer itself. A Windows `X-Eric-Worker-Key` is optional and is not required for the bot.

Base URL:

`https://mantuehuzptactymjpqg.supabase.co/functions/v1/eric-app-api`

Anchor for IMG_1080 only:

- capture `88584ff1-867f-47e1-8276-a8412fb96830`
- resource `capture:88584ff1-867f-47e1-8276-a8412fb96830:506180e3adc4d31a83746d89c202e3188ab7f0b7f728c9b53ac5239c5fbd2ce9`

Do not send a Storage `object_path`. The bucket `eric-app-private` stays private.

## Fetch

`GET /worker/vision/content?capture_id=<uuid>`

or `GET /worker/vision/content?resource_id=<capture:uuid:sha256>`

Exactly one query parameter. The server loads the capture, picks that image (or the capture's first image), and streams the private parts. Response headers match the existing file route, including `Cache-Control: private, no-store`.

`GET /worker/vision/manifest?capture_id=<uuid>` or `?resource_id=...` returns only `filename`, `mime`, `bytes`, `sha256`, and `part_count`.

## Write

`POST /worker/vision/results`

Each call appends a new `eric_app_records` row, kind `vision_result`, id `vision:{capture_id}:{sha256 of the canonical payload}`. A byte-for-byte retry returns the same id and does not overwrite that row. A different `analyzed_at` or result appends another row.

The same call updates the latest card text on `inbox_item` and `inbox` (`e32765f5-43c9-4a20-8e50-d7afc9e7c773` for this capture) and mirrors `vision_*` onto `eric_app_captures`. It does not change image bytes, `course_id`, `lecture_id`, or `learning_capture_id`. A `preview_source=human` preview is left in place.

```json
{
  "schema": "eric-os-vision-result.v1",
  "capture_id": "88584ff1-867f-47e1-8276-a8412fb96830",
  "resource_id": "capture:88584ff1-867f-47e1-8276-a8412fb96830:506180e3adc4d31a83746d89c202e3188ab7f0b7f728c9b53ac5239c5fbd2ce9",
  "sha256": "506180e3adc4d31a83746d89c202e3188ab7f0b7f728c9b53ac5239c5fbd2ce9",
  "status": "completed",
  "analyzed_at": "2026-11-02T08:30:00Z",
  "analyzer": "eric-os-bot",
  "provider": "grok",
  "source": "original_image",
  "confidence": "high",
  "result": {
    "title": "极限复习",
    "probable_course": "高等数学 II",
    "topic": "导数",
    "summary": "导数定义",
    "readable_content": "",
    "equations": [],
    "key_points": [],
    "uncertain_regions": [],
    "confidence": "high"
  }
}
```

`status` is `pending`, `completed`, or `failed`. `confidence` may be `high`, `medium`, `low`, or a number from 0 to 1.

Response: `{"ok":true,"kind":"vision_result","id":"vision:…","revision":N}`

## Read

`GET /worker/vision/results?capture_id=<uuid>`

`GET /worker/vision/results?capture_id=<uuid>&latest=1` returns the newest history row by `revision` plus the capture mirror and the inbox preview.

## Where Inbox reads

1. Vision is written to `inbox_item` / `inbox` payload (`preview`, `preview_source`, `vision_status`, `vision_analysis`, and the other `vision_*` keys), mirrored on `eric_app_captures.vision_*`, and appended as `vision_result`.
2. The inbox list reads `item.preview` from kind `inbox_item`. After projection that preview is human-confirmed text, then the vision summary, then `等待 AI 分析…`. Failure uses `AI 分析失败，可重新处理`. Low confidence uses `部分内容需要确认`.
3. Windows OCR stays in `ocr_preview` and in `review.snapshot.original_text`. The default inbox API omits `original_text` and replaces an image card's OCR preview. `?advanced=1` returns it under `debug` with `collapsed: true`. `EricInbox.jsx` shows that block only in the debug details. `InboxStore.project_snapshot` in `server/inbox_store.py` is what the publisher should use so a later sync does not copy OCR back into `preview`. `InboxStore(personal).project(c, row)` remains the SQLite list projection. `eric_app_publish_records` also keeps an existing vision/waiting/human preview when an older publisher still sends OCR.

IMG_1080 acceptance: fetch the content URL with the bearer secret and confirm PNG bytes; POST one completed result; GET `latest=1` and the inbox item `e32765f5-43c9-4a20-8e50-d7afc9e7c773` and confirm `preview` is the summary, `preview_source` is `vision`, and `ocr_preview` still holds the old OCR. No batch, cron, public bucket, or client `service_role`.
