---
version: alpha
name: Attention Desk daily workspace
description: A calm Chinese personal inbox, calendar and action desk with explicit human review.
colors:
  primary: "#1d1d1f"
  background: "#f5f5f7"
  panel: "#ffffff"
  soft: "#eeeef2"
  muted: "#6e6e73"
  subtle: "#77777e"
  border: "#e5e5ea"
  fieldBorder: "#d2d2d7"
  focus: "#0066d6"
  focusSoft: "#eaf3ff"
  action: "#c93434"
  actionSoft: "#fff0ee"
  success: "#237347"
  successSoft: "#edf7f0"
  highlight: "#915800"
  highlightSoft: "#fff5e6"
typography:
  sans:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif'
  mono:
    fontFamily: '"Cascadia Mono", "SFMono-Regular", Consolas, monospace'
rounded:
  DEFAULT: "18px"
spacing:
  section-gap: "22px"
  section-padding: "27px"
  content-max: "1208px"
components:
  button:
    backgroundColor: "{colors.focus}"
    textColor: "{colors.panel}"
  field:
    backgroundColor: "{colors.panel}"
  helpText:
    textColor: "{colors.muted}"
  focusIndicator:
    backgroundColor: "{colors.focus}"
  dialog:
    backgroundColor: "{colors.panel}"
  pressedRow:
    backgroundColor: "{colors.soft}"
  subduedIcon:
    textColor: "{colors.subtle}"
  separator:
    backgroundColor: "{colors.border}"
  fieldBorder:
    backgroundColor: "{colors.fieldBorder}"
  evidenceQuote:
    backgroundColor: "{colors.focusSoft}"
  urgent:
    textColor: "{colors.action}"
    backgroundColor: "{colors.actionSoft}"
  done:
    textColor: "{colors.success}"
    backgroundColor: "{colors.successSoft}"
  review:
    textColor: "{colors.highlight}"
    backgroundColor: "{colors.highlightSoft}"
---

# Attention Desk design context

## Daily friction reduction (Phase 4.1 Lite)

Today and Learning share LearningFocus, a read-only projection of existing evidence. Show the actual latest learning day, preview/classroom known and pending results, at most three priorities and retest groups, and at most two verified delayed successes. A newly received note must not borrow yesterday's preview score. Keep provenance, technical fields and the full cycle under the existing disclosure. Today keeps its agenda and deadlines directly below the learning brief. No charts, new palette, new navigation or new notification is introduced.

The single Learning sync button starts one bounded read of the owner's existing Edge course window. Native focus restrictions are explained once in the inline progress. Errors retain originals and leave the button usable; reading never fills an answer, submits, opens media or saves credentials. Notes conflicts reuse DeskSheet and Field, native selects, typed ISO dates, existing mutation feedback and source links. Ordinary successful note sharing has no extra confirmation. The temporary notes connection only uploads notes and cannot act as a Collector session.

## Learning daily brief (Phase 4)

Ordinary mistake details reuse Sources for the actual attempt's Inbox original and related textbook pages. Repeated questions across classes retain separate attempt-source links. Source links remain inside a closed disclosure; no homepage field or new navigation destination. Local conservative suggestions are labeled as local rules, never as a model call.

Learning extends the incumbent cool gray, white, system-sans daily desk. Reuse the existing colors, typography, 18px reading surface, 10px controls, document scroll, DeskSheet, Field/SubmitButton/useDraft, ConfirmDialog, SourceImage and usePersonalResource. No new visual tokens, remote fonts or image assets. The October 28 usage correction makes preview results the first plain score line, followed by pre-class weaknesses, related notes and after-class retest status, one next step, recent progress and a quiet historical count. Classroom exercises are optional and collapsed. No metric tiles, charts, IDs, percentages or evidence counters in the first viewport.

PreviewEvidence and ExerciseScore are shared read-only business variants for Learning and Lecture Review. Material counts say exposure; exercise counts retain every known correct, incorrect and unknown result. “Class notes received” is distinct from claiming the teacher covered every item. A pre-class error to post-class independent-success line appears only with chronological and related-note evidence. Session/record sources reuse the existing disclosure and original Inbox links, without another form or page.

Today adds a quiet learning entry only when a real weakness or due followup exists. Learning is the sixth rail destination; narrow navigation shares seven equal destinations including Settings, each retaining a minimum 44px target at supported 320px width. Course opens the existing School workspace. Batch historical confirmation is an ordinary local metadata action; it does not open another confirmation dialog. Diagnostic and retest answers are initially blank and reference answers remain server-side until a real submission. Uncertainty, missing submission date and missing sources remain explicit. An ordinary question with no reasoning does not justify a confident careless/concept-error label.

Learning sources and external review stay in disclosures/sheets. Generated summaries are prominently labeled as retrospective textbook material, with exact PDF page links. External review only stores a second opinion. Bookmark intake is user invoked, reads visible/selected text, omits query strings and credentials, consumes transient URL fragment data immediately, and sends no platform answer. The user explicitly authorized the stated official DeepSeek learning-text scope on October 28. A compact explanation and pause/resume action live inside the existing closed collection disclosure, never a new settings page or daily confirmation. Source originals, passwords and browser sessions are excluded. Each analysis is one-shot; missing or partial usage retains the model-specific maximum-cost reservation. A source-verified local retest can survive a failed cloud cause suggestion; an unknown skill never earns an unrelated replacement question.

## School pilot variant (Phase 3)

Phase 3.2 keeps the same reading surface and canonical owners. JobStatus names evidence verification, composition and at most one targeted repair without fake percentages. The existing confirmation shows the real bounded request count and estimate. ReviewIssues is the School variant of the existing inline quality block: localized item labels and persistent reasons, never raw schema diagnostics. Automatically validated versions remain Needs Review with source uncertainty visible; adoption still requires the existing explicit action.

The Math II classroom is a nested workspace reached from Eric Inbox. Keep the six existing narrow navigation destinations. Course → paginated Lectures → Lecture → versioned Review reuses DeskChrome, document scrolling, Field/SubmitButton/useDraft, typed ISO class dates, native selects/checkboxes, ConfirmDialog, DeskSheet and SourceImage. No new visual tokens or dialog owner. Canonical SourceImage adds document_id/page for learning PDFs (up to 2000 physical pages), retaining the attachment variant. Source labels and page links are assigned by the backend. A quiet textbook metadata panel names the exact user-confirmed edition; missing real PDF and fixture-only acceptance remain explicit.

Ten review sections are reading surfaces, with sparse source links, native answer disclosures and explicit adoption/feedback. Suggested actions enter the existing draft/batch-confirm flow only on user request. Generation and selected-page Vision require the existing confirmation dialog with scope/cost; local import, mapping, export and feedback remain ordinary saves. Preserve original pages on parsing or AI failure. Do not equate saved PDF, extracted text and understood math. Search inside a Lecture is transient exploratory evidence (business override: no URL query persistence); explicit submit, immediate clear and stale-safe usePersonalResource reads.

## Overview

The October 23 user brief explicitly replaces the former paper-and-ink personal workbench with an Apple/iOS-inspired daily tool. Its job is to reveal today's commitments, approaching dates, unconfirmed WeChat notices and required actions within seconds. No charts, fake metrics, enterprise-dashboard density or always-open analysis forms. The visual signature is a quiet daily agenda above a compact review inbox, with focused reading/edit sheets.

Audience: one Chinese-speaking student on Windows and iPhone, zh-CN, Asia/Shanghai for WeChat and push policy. No Japan-market scope. Preserve IANA zones in formal data, civil date precision, exact immutable sources and explicit human participation. Existing chat archive, official feed and paired phone intake keep their domain behavior.

`src/personal/desk.css` is the runtime owner for the daily workspace, scoped by `.desk-shell`. Its first root block maps the frontmatter to existing CSS variables: primary→ink, background→paper, border→line-soft, fieldBorder→line, focus→blue, focusSoft→blue-soft, action→red, success→green, highlight→yellow and corresponding soft variants. Shared shape/geometry tokens are desk-radius, desk-control-radius and desk-nav-width. Existing `styles.css` owns the baseline outside this scope. No generated token adapter or remote font dependency.

## Colors

Cool gray canvas, true white reading surfaces, near-black content and sparse blue actions. Red means urgent/overdue; green means success; orange means review. Status always includes text. Dark mode uses ink #f5f5f7, paper #141416, panel #202023, soft #2b2b30, muted #adadb5, subtle #9898a2, line #46464c, line-soft #35353b, blue #72b4ff, blue-soft #1e3550, green #8bd4a3, green-soft #23382b, red #ff9c95, red-soft #462825, yellow #f8c773 and yellow-soft #3b3021. `DeskChrome` supports light, dark and system, persisting only the appearance preference. High-contrast/forced-colors keeps system controls operable.

## Typography

Local system sans throughout; display 38px desktop / 33px narrow, page titles 30px / 26px, groups 18px, candidate titles 17px, body 14px, metadata 12–13px with generous line-height. Native forms become 16px inside narrow sheets. No decorative type, all-caps section labels or font downloads. Source text preserves whitespace and is never truncated; only the compact inbox summary clamps to two lines, with the full value in details.

## Layout

216px navigation rail (184px mid-size), content max 1208px including 44px desktop side padding; 18px narrow padding. At 760px the rail becomes five labeled bottom actions. Today has two agenda groups, a three-candidate inbox preview and a bounded action list. Historical overdue dates remain accessible without dominating today. All-items pages load 50; inbox pages 20; filters/page remain URL state.

Sheets are 520px right panels (680px wide variant), 16px viewport inset, 20px radius and internally scrollable bodies with sticky footer actions. Narrow sheets occupy the viewport below a 12px top inset and respect safe areas. The document owns scrolling elsewhere. Native select popups and typed ISO dates retain existing platform ownership rather than adding custom date widgets.

Saved media uses the existing evidence disclosure and sheet. Today shows one quiet text entry only while attachments need processing; it disappears when resolved. Inbox cards add only “包含 N 份附件”. Inside evidence, original files precede verbose message metadata, with inline image preview and download. Cache thumbnails are labeled, and OCR, literal Vision transcription and nonliteral visual descriptions stay distinct. Failed or unavailable media remains outside the action inbox with a plain-language reason and automatic retry timing; no manual reanalysis is required.

## Elevation & Depth

Fine 1px separators and surface contrast supply structure. No decorative card shadows. Modal sheets alone have a quiet shadow and dim/blur backdrop, using the browser top layer and inert background. A confirmation can appear over a dirty sheet. Global scrollbar styles remain inherited from styles.css, with theme token overrides in desk.css.

## Shapes

18px panels, 13px inbox cards, 10px controls, 7px status labels. Consistent thin Lucide icons, normally 15–20px. No imagery is needed in the product; the generated concept is a design reference, not a bitmap UI asset.

## Components

Canonical shell/appearance: DeskChrome. Canonical overlay: DeskSheet, an app-owned native dialog, with focus return, Escape and scroll lock. Serious/discard prompts retain ConfirmDialog. Canonical field/submit/error owner remains Forms.Field/SubmitButton + useDraft; typed dates use validTime, evidence uses exactSpan. PersonalRequest/usePersonalResource own bounded abortable reads and persistent errors. Success remains inline, not a transient toast.

WeChatInbox displays title, one-sentence summary, source group/time, written priority/state and an actual deadline only when present. Ignore/edit/confirm are the primary actions. CandidateSheet is the focused WeChat variant of the existing raw-review contract; it uses the existing draft/confirm APIs and preserves sibling drafts. Five core fields are title, type, dates, place, action. Summary/relevance and additional details are disclosures; participation remains visible when it changes task creation. Unknown years are never invented. Sources, sender, AI reasons and quotes use WeChatEvidence/OriginalNotice in a closed disclosure.

Formal items emphasize title, summary, time, action and source. Date precision, evidence, analysis, context, ICS and history occupy secondary disclosures. Formal edits reuse existing forms inside DeskSheet, never a second event store. Participation and original evidence semantics are unchanged.

Bark phone settings show the candidate switch, HTTPS server, masked Device Key and explicit connection test. Shared Field labels remain associated with actual inputs. Show/hide is keyboard accessible; saving clears transient input. An empty key means keep the existing encrypted/environment key; no key is returned or stored in browser storage. Official/cloud Bark settings remain a separate disclosure and keep their existing policy.

Paired phone intake, documents/OCR, long-document extraction, source updates, ICS and reminder settings retain their original contracts. New body-level theme tokens do not migrate these domain rules. Collection status and operational counters belong in settings or a closed status disclosure, not the default home.

## Do's and Don'ts

- Do keep attention on what, when, next action and source.
- Do require explicit confirmation before formal records, exact evidence spans and deliberate participation.
- Do preserve entered values after failures, show actionable errors, prevent duplicate saves and restore focus.
- Do respect reduced motion, native keyboard controls, narrow layouts, safe areas and visible scrollbars.
- Do preserve the verified read-only DB increment, dedupe, evidence and human confirmation boundaries. Metadata discovery, per-room grants and scoped queue orchestration extend these boundaries under the explicit multigroup brief.
- Do not show model confidence/schema/diagnostics by default or send them to the phone.
- Do not infer a withdrawn message's content, fabricate missing years or treat a push as formal confirmation.

## Authorized WeChat groups · October 23 extension

Collection settings uses a compact health summary above independent group cards. A 400px minimum card column gives a balanced two-column desktop view and a single narrow column. Cards show the exact display name, written priority/enabled state, latest scan, new/important counts and pending reviews; room IDs, per-shard cursor, errors and AI counts remain in a disclosure. Enable/disable, priority and removal save immediately with revision checks. A separate schedule/budget form keeps its explicit save/discard ownership; dirty budget input blocks conflicting group mutations.

Adding a group uses DeskSheet + Field/native search/select and a concrete ConfirmDialog. Search debounces 350ms, respects IME, aborts stale reads and only queries metadata. The selected room ID is verified again before granting; same names and later pages cannot select another room. New grants read only the last 24 hours. Escape from the search field dismisses the sheet unless IME is active. Search failure keeps the query and offers retry. No ungranted body, preview or message shard is read during discovery.

High/medium/low priority adjusts conservative local triage and low-priority phone suppression. Bark uses meaningful explicit actions with validated evidence as well as task/deadline category labels; a required exam submission must not be lost because the model chose “exam”. Ordinary information remains in the desk. Exact push title is `Attention Desk · 微信`; the body is a concise title with a known deadline when available and the source is a subtitle. Model diagnostics stay off the phone.

## Attention quality · October 23 extension

Only complete, explicitly relevant Action items occupy the inbox and its home preview. FYI appears in a quiet “值得知道” section after actionable work. Its default feed shows the last seven days, ordered by source time, with a compact recent/archive switch. Up to three rows appear initially; more information is server-paged. Pinning keeps an item for thirty days. Archive and ignore are reversible, with written feedback and Undo. FYI never requires routine confirmation and never triggers candidate Bark. Chatter, duplicate participation lists, unrelated awards and unfollowed optional events stay out of these surfaces. Empty inbox copy describes the absence of actionable items without claiming unresolved information has been confirmed.

FYI details keep sources in the existing disclosure. “转为事项” explicitly opens the canonical CandidateSheet conversion variant; it starts as a Notice without invented dates or actions, preserves typed input on failure, guards dirty closure and only creates a formal item after “保存为事项”. The classification stays FYI, so this manual action cannot cause a candidate push. Converted items link to the existing formal detail. Unresolved/media sheets remain read-only. Scope source identity by account, chat and shard: a display name is never proof of identity. Unknown senders are shown with their raw ID; system records are labeled as system messages.

Questions and incomplete notices wait in a collapsed context area. An explicit confirmation can combine the held question and new fact from the same group/topic; both immutable sources remain available. Unreadable continuous images occupy one attachment group in a separate collapsed “未解析附件” area. Its sheet contains explanation and source disclosure, never an actionable candidate placeholder or confirmation button. A readable derived source must pass the same quality gate before becoming Action/FYI. Partial collection health says “部分群暂未更新”; it must not look healthy while a group is blocked.

## Phase 1 course association

Reuse CourseField (Forms.Field + native select) in review and formal course editing. Suggestions remain visibly unconfirmed until chosen and confirmed. Course is optional; errors do not discard inputs or block an empty association. Today adds the confirmed course name only when present, using existing metadata typography. No new layout, tokens or domain lifecycle changes.

## Anywhere Notes Ingest

The owner's 2026-10-29 scope explicitly permits private Supabase transit for shared classroom notes while the Windows desk is offline. This is a narrow evolution of the prior LAN-only note entry; other sources and model-send permissions remain unchanged. MobileCollectorSettings leads with cloud note sharing and keeps LAN controls in the existing closed disclosure. No visual tokens or navigation destinations change. Notes enter the same local Inbox and course/lecture pipeline after durable hash-verified receipt.

The cloud setup page offers one generic signed Shortcut download plus one upload-only connection to paste during import. The signed artifact is decoded and its actions and permissions compared with the reviewed unsigned source; it contains no owner credential or note. Its device-local pending folder is saved before upload and only its queued copy is deleted after `saved: true`. Installation and true cellular sharing require owner device verification before claiming acceptance. A manual guide stays closed as fallback; iOS background retry is described without promising execution after an arbitrary network failure.

First-setup correction: cloud QR codes carry a 15-minute, random setup nonce in a retained query, not the device upload capability. A same-origin, non-cacheable LAN exchange returns the capability to memory. Repeated preview, reload and Code Scanner → Safari handoff work without shared cookies; invalid, replaced, expired and revoked setups fail closed. The installer has one clearly labeled empty connection question. Its raw-PDF request uses typed text headers and ISO 8601 including time; only queued copies are eligible for deletion after saved confirmation.

The one-time mobile setup page owns the fixed HTTPS endpoint, masked upload credential with copy/reveal, and pending-file retry instructions. It must not claim an installable signed Shortcut when only Windows and iPhone/iPad are available. It distinguishes cloud “已接收” from later local processing. iOS background retry is not guaranteed: a preserved file is retried on the next share/run, with optional Wi-Fi personal automation. Device management reuses DeskSheet, usePersonalResource and ConfirmDialog; revocation names the loss of future upload access and preserves already received notes. No new completion notifications are added to the desktop.

## Phase 2 Eric Inbox

EricInbox uses the existing desk shell, event rows, native selects, Forms, useDraft, persistent inline feedback and DeskSheet. It is a product surface for receiving material, without a visual rebrand. Original, Extracted, Linked, Analysis, Actions and Evidence are sections in the existing wide sheet. Original bytes and immutable source text retain their existing viewing/download owners. Dense processing metadata is kept in a disclosure; user-facing states stay in Chinese.

The list has four status filters, a source-scope select and bounded pages of 20. Source scope defaults to active submissions; all authorized Attention sources remain accessible through the same underlying RawItem projection. On phones, five navigation destinations and Settings share six equal touch targets; each remains at least 44 px. Existing Today, Candidate and formal-item behavior is retained.

Receipt success, PDF readability and content quality are distinct. OCR output never earns a complete-understanding label. Unverified/low-quality OCR shows a visible reason and Needs Review while keeping original preview/download usable. Image decoding and PDF preview do not require successful OCR. Local classification is labeled as a suggestion. CourseField owns manual association, including change/clear; the Project field is a plain optional local filing label. No new tokens, icon family or elevation rules are introduced.


Phase 3.1: the existing textbook surface also lists the registered companion lower volume. Canonical-library import verifies the fixed Eric OS manifest and keeps original files in that directory without a second upload/copy. Upper volume remains primary for this Lecture. Acceptance is a persisted result, not a hardcoded Pending label. Reuse existing controls, tokens, inline errors and source preview; no primary-navigation or aesthetic changes.

Phase 4.1 connection scope confirmed by Eric: a 90-day notes-only verifier may survive restart, and the existing Collector may resume only on the previously selected exact private Wi-Fi/interface/address. Store only the verifier digest; browser-pairing sessions remain temporary. Manual stop, device revocation or network changes stop reception and revoke the notes connection. No public listener, firewall change, processing-complete notification or new service is introduced.


## Notes intake experience · 2026-10-29

Owner policy: preserve the physically proven raw-PDF upload endpoint and device capability. The owner confirmed week one Monday as 2026-08-31. The 139-session timetable is local evidence, including unregistered competing courses, and creates neither course records nor attendance/mastery. Existing same-document and human course links take priority. High-confidence course routing requires independent content/context evidence; medium confidence recommends a course in the existing CaptureSheet without preselecting it; low confidence keeps the existing confirmation path. Timetable, textbook and recent-lecture content are correlated conservatively; a lone word or timetable slot cannot auto-assign. Delayed phone retries and offline desktop downloads retain their source clocks.

The existing native pending folder remains. A saved=true response and valid receipt permit a hash/name-bound local acknowledgement marker, never a per-share delete. Repeated runs skip acknowledged unchanged files. Optional explicit batch maintenance requires both a marker proving the cache was generated by this workflow and a matching acknowledged content hash; iOS owns the batch deletion confirmation. Legacy unowned files and all unacknowledged or changed files remain. No system deletion setting is bypassed. Mobile success is the truthful immediate cloud receipt, “Eric OS 已接收”; course resolution remains asynchronous on the existing Inbox/Learning screen. No cloud course database, new analysis, notification stream, overlay or runtime style token is introduced.

Verification: server/test_note_course.py, server/test_notes_shortcut_cache.py, server/test_notes_anywhere.py, server/test_phase41_notes.py and the current isolated Chromium/WebKit captures. Native cache execution tests and cryptographically verified signing are technical evidence; installing/running the new workflow on an actual iPhone remains a separate acceptance step.
