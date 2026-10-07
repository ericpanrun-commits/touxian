# UX Contract

## Product context and sources

Scope: personal Windows notice workflow with public ZQU sources, local file/OCR intake, DeepSeek candidate extraction and explicit human confirmation, zh-CN, Asia/Shanghai minute precision, Gregorian dates. Accessibility target is WCAG 2.2 AA for new controls. Files and original images stay local; explicit analysis sends extracted text. No cloud account, phone app, calendar or clipboard automation is introduced. A transient password field sends the provider key to the local backend for Windows DPAPI protection.

| Domain / scope | Authoritative source | Source type | Reviewed date |
|---|---|---|---|
| Phase boundaries and manual input | Approved user Phase 0/1 brief; README | Product brief | 2026-10-07 |
| Field validation and transitions | server/personal_models.py; server/personal_store.py | Executable domain contract | 2026-10-07 |
| Immutable text and relationships | server/migrations/001_personal.sql | Storage contract | 2026-10-07 |
| API failures and version checks | server/personal_api.py | API contract | 2026-10-07 |
| Candidate schema and validation | server/extraction_models.py; server/extraction_validation.py | Executable extraction contract | 2026-10-07 |
| Confirmation, participation and cache | server/review_store.py; server/extraction_service.py | Executable domain contract | 2026-10-07 |
| DeepSeek request and credential handling | server/extraction_provider.py; server/ai_settings.py | Provider contract; official API docs | 2026-10-07 |
| File retention, versions and block Evidence | server/documents; server/migrations/004_document_sources.sql | Executable file/storage contract | 2026-10-07 |

Visual context: DESIGN.md mirrors canonical runtime `src/styles.css` and `src/personal/personal.css`. Reconcile token changes in both. Preserve upstream light theme and existing screens; this contract governs new `src/personal` flows.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Select/Listbox | Forms.jsx Field with native select | Fixed precision, origin and deadline options | native | Keyboard and popup browser check |
| Date | formHelpers.js validTime with typed input | personal_models.normalize_time; extraction_validation.deadline_state | typed date / typed minute | Invalid date and precision tests; browser edit |
| Form | Forms.jsx Field/SubmitButton; formHelpers.js useDraft | personal_models.py; extraction_models.py; form validators | collect / settings / review / event edit / deadline / task | First-invalid focus; retained input on failure |
| Scrollbar | Global styles.css rules | root scrollbar variables | original text and textarea geometry | Computed standards properties and scrolling |
| CRUD | PersonalWorkspace.jsx and ExtractionWorkspace.jsx | personal_api.py; review_api.py; personal_store.py; review_store.py | raw collect / draft / review-confirm / participation / edit / complete / cancel / restore | Browser source-review-restart flow and pytest |

Inline feedback is owned by PersonalWorkspace and its ExtractionWorkspace flow; both use persistent inline feedback. No toast or table selection capability exists in this slice.

## Flow ledger

| Operation | Trigger | Pending | Success destination | Success feedback | Failure recovery | Focus outcome | Source ref |
|---|---|---|---|---|---|---|---|
| Collect | Save original and analyze / manual | Disable original write | Saved raw preview | Raw saved; candidates only | Preserve input; retry same request | Preview heading | RawCollectionForm; raw API |
| Analyze | DeepSeek analysis action | Persisted running job; no formal records | Same preview | Candidate result or retained-source error | Manual review or explicit new version | Review controls | ExtractionWorkspace; ExtractionService |
| Save draft | Save preview draft | Disable writes | Same preview | Draft saved, no formal event | Keep edits and show error | Current control | ReviewEditor; draft API |
| Confirm | Confirm selected candidates | Validate dates/spans; disable writes | First formal event detail | Number of events created | Transaction rollback; keep edits | Detail heading | ReviewEditor; confirmation API |
| Participate later | Attend and create tasks / decline | Disable writes | Same formal event | Created tasks or ignored status | Keep stored plan | Detail heading | EventContext; participation API |
| Settings | Save extraction settings | Disable form/actions | Same settings page | Settings saved; password cleared | Keep transient input; show sanitized error | Settings heading | AISettingsForm; AISettings |
| Edit or add child | Form submit | Disable current writes | Same event, form closes | Saved message | Keep fields and show error | Detail heading | DeadlineForm/TaskForm; event API |
| Complete | Complete event | Confirm unfinished tasks, then disable | Same event | Completed status | Keep tasks and event, show error | Restore control | status API |
| Cancel / restore | Explicit action | Disable writes | Same event | Updated status | Keep saved data | Current action area | restore API |
| Navigate with changes | Back, sibling or refresh | App confirmation | Requested destination | Saved records preserved | Escape keeps draft | Opener restored | requestLeave / ConfirmDialog |

No delete, search or bulk lifecycle operation is added. Unified collection accepts text, files and screenshots. Dates are explicitly typed and confirmed. AI evidence is verified against immutable text or a selected immutable source block using Unicode codepoint offsets and can be edited or removed before confirmation. Terminal edits are disabled with a nearby explanation and restore control. A cancelled raw review can be resumed without losing the source. Cancellation and postponement notices never automatically mutate existing events.

## Navigation and async behavior

Phase 4 extends RawCollectionForm with a native labeled file picker and a drop area for text/files/images. Forms.jsx and DocumentSources.SourceBlockPicker retain Field/native-select ownership. OriginalNotice remains the shared source viewer for preview and formal events: text/web blocks, PDF page rasters, DOCX paragraph/table positions, and original screenshots with exact OCR regions. SourceImage uses the existing yellow highlight tokens. Downloaded originals are offered as downloads, never executable previews. Preview evidence editors and formal deadline/task forms use the same block selection contract.

File intake first saves originals and starts a persisted background parser job. The preview disables analysis/confirmation while manual files lack extracted content. Successful parsing navigates to a new immutable snapshot of the same RawItem; a combined save-and-analyze action then uses the existing ExtractionService. An explicit local-only save starts no model call. Failed parsing keeps originals and shows a retry action; failed DeepSeek retains all local blocks. Reads/polls abort on navigation, server jobs continue, and interrupted jobs become retryable failures after restart. Files over 15 MB require the shared ConfirmDialog; 50 MB per file and 60 MB per upload request are hard bounds. Body/attachment date differences, OCR uncertainty and omitted long-document blocks appear in the preview and require acknowledgment plus confirmation of chosen dates. Re-download creates a version when SHA-256 changes; formal Evidence retains its exact original file version.

Phase 3 adds five configured public ZQU sources to the same SQLite workflow. OfficialMonitor owns only source status, enable/disable and bounded checks; it has no event editor or second review database. `official_sources=1` opens sources; `review_source` opens the shared RawInbox with a persistent native-select filter. Source actions use labeled native buttons/checkboxes, serialized pessimistic writes, bounded abortable reads and persistent failure messages. A check continues on the server when leaving the page. Source reads never call AI. Newly saved snapshots require explicit DeepSeek analysis or manual review, and human confirmation to create formal records. Shared OriginalNotice owns publication/version/link and unparsed attachment display for both previews and formal events. Publication dates are never treated as deadlines. Source failures back off; partial results remain reviewable.

- Event and raw lists read 50 records per page and offer load more. Detail identities are reflected in `personal_event` / `personal_raw` URLs. Preview drafts are saved only on the explicit save action and survive refresh. The root screen selection retains the existing local preference. Titles identify collection, review, settings, list or selected event.
- `personal_settings=1` opens extraction settings directly. Explicit personal event/raw/settings links override the remembered legacy screen. Settings opened from a raw review retain that raw identity for the return action; navigating away clears the settings marker.
- New pages size naturally. Narrow layouts stack detail/forms and retain labeled controls. Source text wraps and scrolls; exact quotes remain available. Legacy screens keep their original desktop layout.
- GET requests abort on navigation; refresh discards drafts only after confirmation. A missing event or storage read failure shows an explicit error and a route back to the list.
- Writes are pessimistic and serialized. Collection reuses an idempotency key for identical retries and starts a new key for changed input. Version conflicts preserve the current form and ask the user to reload saved content. Other writes require an explicit retry, rather than unattended retry.
- Local requests timeout after 15 seconds. Analysis POST returns a persisted job immediately; the backend provider timeout is configured separately (45 seconds by default). Preview polling aborts on navigation; the server job continues. If connection fails, explain that save status is uncertain and allow re-reading saved content. No silent fallback to browser-only storage. SQLite transactions own rollback.
- App navigation with dirty inputs uses the authored native dialog. Browser closing/reload uses the standard beforeunload safeguard. Drafts are not automatically persisted; explicitly saved preview drafts, source and event records are. Force analysis warns that current preview edits will be replaced and that a new API call may cost money.
- Forms use noValidate, label-associated fields, inline errors and first-invalid focus. IME Enter does not submit during composition. Server errors remain visible; inputs are retained until a successful save or explicit cancel.
- Confirmation focuses the safe action, Escape cancels and focus returns to a connected opener. Busy labels stay stable. Evidence selection focuses the original region and reveals an exact quote and highlighted span.

## Verification and legacy boundary

Run pytest with ATTENTION_INBOX_POLL=0, npm run build, DESIGN.md lint and the scoped strict UI audit. Acceptance uses installed Edge and the existing browser runtime, without adding Playwright to project dependencies. Check raw-first/no-event-before-confirm, provider failures, saved preview refresh, participation, date precision, exact evidence highlighting, keyboard/dialog behavior, desktop-width layout, old chat sample and unconfigured official screen.

Actual acceptance must stop both servers and reopen a fresh browser session against the same database: two deadlines, three tasks, one completed task, edited date and evidence remain. Further complete/cancel/restore checks retain data. Domain tests cover transactions, newer-schema refusal, immutable snapshots, exact Unicode offsets and concurrent versions.

DeepSeek acceptance additionally requires a real API call using a user-configured key, explicit participation and confirmation, and a process restart preserving event/deadline/task/evidence plus model provenance. This was completed on 2026-10-07 with prompt notice-extraction-2: two explicit tasks, one date-only deadline, corrected exact evidence, and unchanged complete API responses after restarting both processes and using a fresh browser session. The source did not provide a year; 2026 was chosen explicitly for the acceptance example. PHASE-2-REPORT.md records the real output and manual corrections. Mock transports remain separate evidence for failures and contracts.

The scope audit scans the new personal module. A separate whole-src audit identifies upstream form/select conventions without forcing a legacy refactor. Existing JSON workflows, desktop geometry and original provider behavior are maintained; existing tests and sibling-screen browser checks cover regression. Future UI slices must resolve their owners before adding controls.

The whole-src audit reports four inherited differences in App.jsx: the manual-review toggle has no handler, two older textareas do not declare resize-none inline, and the old judgment-day input uses a native date picker rather than the new typed-date owner. Auditing the original commit reproduces the same four findings. These are outside the new workflow; preserve them in this slice and assess them during a separately approved legacy-UI cleanup. The new personal-module strict audit and DESIGN.md lint both pass with zero errors and warnings.
