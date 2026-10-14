# Phase 7 architecture audit and implementation contract

Audit: 2026-10-14, before implementation. Baseline commit `a1fb8a4`.

- Frontend: React 18, Vite 6, Lucide, existing paper/ink CSS. `App.jsx` selects personal/chat/official screens. Personal Today is the default; official currently opens `OfficialSources` management only.
- Backend: FastAPI/Uvicorn, Python, httpx, BeautifulSoup/lxml. Lifespan owns the personal store, extraction, document worker, collector, official scheduler and ReminderService. Desktop binds loopback. No Supabase or cloud database.
- SQLite numbered transactional migrations 001–006. Existing tables: source, raw_item, raw_snapshot, event, deadline, task, evidence, user_decision, ai_extraction, extraction_request, raw_review, event_context, event_task_plan, official_source, official_item_source, official_url_alias, raw_snapshot_web, official_fetch_state, attachment, snapshot_attachment, raw_source_block, snapshot_document, attachment_job, ai_chunk_extraction, extraction_chunk_cache, reminder_settings, reminder, reminder_attempt, schema_migrations.
- Phase 3 already implements five configured ZQU HTML sources, three VSB selector adapters, bounded HTTP fetching, source associations, canonical URL aliases, immutable snapshots, ETag/Last-Modified, changed-body versions, enable/disable and a 30-second scheduler tick (12-hour per-column frequency). Official data enters shared RawInbox/review, not a readable news feed. `official_monitor.py` and desktop scraper are older/offline mechanisms; do not reactivate them as parallel ingestion.
- AI extraction/classification and profile code serve earlier manual/chat workflows. Official ingestion must remain local, deterministic and free of model calls. Phase 6 only schedules human-confirmed Deadline facts.
- Gaps: request retry/backoff and persistent cross-process lease; shorter priority intervals; bounded first backfill/incremental article checks; broad verified source coverage; readable feed/read/save metadata; deterministic category/relevance/importance and conservative time hints; run history/health; suppressed backfill notifications.

## Chosen extension

Retain Source → PublicHTMLClient → configured VSB adapter → OfficialStore → RawItem/immutable RawSnapshot → AttachmentStore. Migration 007 adds a one-to-one `official_item` projection keyed by existing RawItem, run/lease/notification ledgers and source operational fields. It does not create a second article body or event system. Content fingerprint groups reprints for display while retaining every source, URL and immutable version. Original text is rendered as escaped text; retained HTML is sanitized before storage.

Extend the existing scheduler with one SQLite lease for all sources, heartbeat and interrupted-run recovery. First backfill is bounded by pages/items and 90 days; subsequent scans stop after the front page when it contains no new URLs, and revalidate a bounded rotation of older articles. Per-host pacing is shared across sources. Failures remain isolated with durable run counts and backoff.

Keep Today, Reminder, Calendar and explicit review unchanged. Add a visible official-information entry in Today and turn the existing official screen into a feed with its source-management subview. Use the shared request/read owners, semantic buttons/native controls, inline feedback, document scrolling and existing CSS tokens. Feed detail links into the existing review flow for an explicitly confirmed reminder; extracted times are hints, never Deadline facts.

Vehicle engineering affiliation is confirmed from the official college introduction and undergraduate major page: https://jxqc.zqu.edu.cn/xyjj/xyjj.htm and https://jxqc.zqu.edu.cn/bksjy/zyjs/clgc.htm. Seed only URLs reached from existing configuration or official navigation; record evidence in the Phase 7 operational documentation.

## Safety and acceptance

Back up the live SQLite using its backup API; prove migration on a copy before live upgrade. Preserve existing rows, original files, immutable snapshots and confirmed facts. No DB reset/drop, network/system configuration, WeChat work, push or automatic AI calls. Run the full Python/frontend/build suites, bounded real-site verification, incremental reruns, cross-process scheduler tests and desktop/narrow browser interaction/error/keyboard checks. Commit locally and create a new stable tag after acceptance.
