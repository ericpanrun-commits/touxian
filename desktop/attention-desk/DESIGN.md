---
name: Eric OS study workspace
description: A calm, platform-aware university study desk for Windows, iPad and iPhone.
colors:
  ink: "#1c1c1e"
  paper: "#f2f2f7"
  panel: "#ffffff"
  soft: "#eeeef3"
  muted: "#63636b"
  subtle: "#707079"
  line: "#c7c7cc"
  line-soft: "#e5e5ea"
  blue: "#007aff"
  blue-text: "#0063cf"
  blue-soft: "#e5f0ff"
  action-fill: "#0063cf"
  action-ink: "#ffffff"
  green: "#34c759"
  green-text: "#217a38"
  green-soft: "#edf8ef"
  red: "#ff3b30"
  red-text: "#cb241b"
  red-soft: "#fff0ee"
  yellow: "#8a5900"
  yellow-soft: "#fff5e6"
typography:
  sans:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif'
    fontSize: "14px"
  display:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif'
    fontSize: "32px"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-0.9px"
  title:
    fontSize: "17px"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-0.3px"
  body:
    fontSize: "14px"
  label:
    fontSize: "12px"
rounded:
  desk-radius: "18px"
  desk-control-radius: "11px"
  sheet: "20px"
  cover: "11px"
  status: "7px"
spacing:
  space-1: "4px"
  space-2: "8px"
  space-3: "12px"
  space-4: "16px"
  space-5: "20px"
  space-6: "24px"
  space-8: "32px"
components:
  button-primary:
    backgroundColor: "{colors.action-fill}"
    textColor: "{colors.action-ink}"
    rounded: "{rounded.desk-control-radius}"
    padding: "9px 15px"
    height: "44px"
  button-quiet:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.desk-control-radius}"
    padding: "9px 15px"
    height: "44px"
  field:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.desk-control-radius}"
    padding: "10px 12px"
    height: "43px"
  navigation-current:
    backgroundColor: "{colors.blue-soft}"
    textColor: "{colors.blue-text}"
    rounded: "{rounded.desk-control-radius}"
    padding: "11px 13px"
    height: "46px"
  status-complete:
    backgroundColor: "{colors.green-soft}"
    textColor: "{colors.green-text}"
    rounded: "{rounded.status}"
  study-group:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.desk-radius}"
    padding: "20px 22px"
  task-row:
    textColor: "{colors.ink}"
    height: "52px"
---

# Design System: Eric OS

## Overview

**Creative North Star: "The Daily Study Desk"**

A calm personal university study workspace, with the clarity of an Apple HIG-inspired tool and the familiar controls of the device in use. Restrained gray, white and blue keep coursework readable; coherent subject covers give courses a recognizable identity. The interface uses a real web application shell with platform-aware navigation, keyboard and touch behavior. Reference images and the generated concept set direction; they are not pixel-copy specifications or sources of product facts.

The owner's November 3, 2026 redesign authorization supersedes earlier task-scoped prohibitions on new colors, imagery, navigation and dashboard composition. It preserves business, evidence and confirmation rules in UX-CONTRACT.md. The canonical runtime token owner is `src/personal/design-tokens.css`, imported by `src/personal/desk.css`; this document mirrors that source. Update the runtime and this mirror together. The shared system is being applied incrementally, as recorded below.

**Key Characteristics:**

- Restrained neutral surfaces, sparse blue actions and explicit written states.
- Local system typography and a coherent subject-cover signature.
- Responsive navigation that supports mouse, keyboard and touch.
- Existing interaction owners and real records throughout the redesign.

## Colors

Cool gray canvas, white surfaces and near-black text form the neutral field. Blue signals navigation and action; green, red and amber communicate completion, error/urgency and review with accompanying text.

### Primary

- **System Blue** (`blue`): accent marks, focus outlines and decorative active indicators.
- **Readable Blue** (`blue-text`): small action text and selected navigation labels.
- **Action Blue / Action Foreground** (`action-fill` / `action-ink`): the primary-button pair. The foreground is chosen for its fill, independently of the decorative accent.
- **Quiet Blue** (`blue-soft`): selected rows, action icons and evidence context.

### Secondary

- **Completion Green** (`green`) and **Readable Green** (`green-text`): completion marks and completion text respectively, with `green-soft` behind quiet success states.
- **Error Red** (`red`) and **Readable Red** (`red-text`): error/urgent marks and error text respectively, with `red-soft` behind quiet warnings.
- **Review Amber** (`yellow` / `yellow-soft`): review and uncertainty, with a written reason.

### Neutral

- **Study Ink** (`ink`): primary content.
- **Cool Canvas / White Surface** (`paper` / `panel`): page ground and reading groups.
- **Soft Gray** (`soft`): subdued controls and pressed/hover states.
- **Secondary / Quiet Text** (`muted` / `subtle`): supporting text, kept readable on the intended surface.
- **Field / Surface Lines** (`line` / `line-soft`): control boundaries and subtle separators.

**The Separate Roles Rule.** Do not substitute the decorative accent for a text or primary-button token. In light mode the primary action uses Readable Blue with white; dark mode uses its own light-blue fill and dark foreground. The full light/dark values and scrollbar colors remain in the canonical runtime file.

Light, dark and system appearance share semantic roles; only the appearance preference is persisted by DeskChrome. Forced-colors modes retain visible system controls and focus. Status never depends on color alone.

## Typography

**Display Font / Body Font:** the local system stack in the frontmatter. Windows prefers Segoe UI Variable, then Segoe UI and Microsoft YaHei UI; Apple devices resolve their local system face and Chinese fallback. No font download is required.

**Character:** compact, clear and familiar. Chinese is the primary product language; existing Today/Inbox/Review labels and the time-aware English greeting are retained where implemented. Dense evidence remains readable text, separate from ordinary navigation.

### Hierarchy

- **Display:** Today's greeting uses the display role, reducing through tablet sizes to 27px on phones.
- **Headline:** existing page headings retain their established 30px scale and responsive variants; the legacy local Today heading remains 38px / 33px.
- **Title:** Today group headings use the title role; course/task titles use 13–15px and moderate weight.
- **Body:** the system body role, with generous line height for learning and source reading.
- **Label:** supporting labels use 11–13px; clocks and progress counts use tabular numbers.
- **Fields:** narrow form controls and the search input use 16px text to remain usable on mobile.

Full source text preserves whitespace and is not summarized away. Compact course names and inbox summaries may clamp; the existing detail/source route retains the complete value.

## Layout

The cloud Web/PWA shell is owned by CloudChrome, rendered by the cloud branch of DeskChrome. `src/cloud/responsive.css` owns shell adaptation; `src/cloud/today.css` owns Today's composition. The local legacy branch keeps its existing structure and breakpoints while consuming the shared tokens.

- **Desktop, above 1100px:** a permanent 216px rail, content width up to 1480px with 32px side padding, and a multi-column Today. Schedule and courses span the broad area; supporting focus, tasks, progress and upcoming groups sit alongside it.
- **Tablet, 640–1100px:** a 76px icon rail expands to 208px, with two-column Today. At 640–820px, the schedule spans both columns and the secondary groups rearrange for the narrower portrait workspace.
- **Phone, below 640px:** a single column and five labeled bottom tabs: Today, 课程, 学习, Inbox, 更多. Today orders schedule, tasks, progress, courses, focus, upcoming and reviews after its next-action strip. The course gallery scrolls horizontally with 145px cards; the full catalogue stays a two-column grid.
- **Safe areas:** the cloud shell uses 100dvh and `env(safe-area-inset-*)` for content, toolbar and bottom navigation. Browser emulation does not prove hardware notch, keyboard or installed-PWA behavior.
- **Input:** primary cloud actions and task hit areas are at least 44px; coarse-pointer rules enlarge smaller search, theme and segmented controls. Hover feedback is scoped separately from coarse-pointer behavior.
- **Sheets:** DeskSheet remains a 520px right panel with a 680px wide variant, viewport insets, internal body scrolling and sticky actions. Narrow sheets fill the width below a 12px top inset. Ordinary pages retain document scrolling.

### Incremental migration ledger

| Surface | Implemented state |
|---|---|
| Web/PWA Today | New responsive composition using existing dashboard, course, learning and upcoming projections |
| Cloud navigation | Shared CloudChrome rail, tablet expansion, phone tabs, More and course/page search |
| Cloud course catalogue/detail | Shared CourseCard/CourseArtwork covers and presentation metadata |
| Cloud task rows | Shared TaskRow with reversible completion/restore affordances |
| Shared styles and PWA presentation | Canonical light/dark tokens, manifest theme, Apple touch icon and public hashed-image shell caching |
| Learning, Lecture, Review, Inbox, Capture, Textbooks and system forms | Existing layouts retained; shared tokens and established interaction owners consumed |
| Local desktop TodayDesk and local legacy shell | Existing composition retained; shared tokens consumed |

This ledger records the shipped UI scope; it does not claim that every deeper screen has been redesigned. The generated concept and user references do not override it.

## Elevation & Depth

White reading groups and subtle separators provide most structure. The cloud navigation rail and phone tab bar use restrained translucent backgrounds with backdrop blur. Sheets and confirmations use the shared diffuse shadow (`--desk-shadow`); selected segmented controls use only a small structural shadow. Course artwork supplies visual richness without adding decorative shadows to every card.

**The Quiet Surface Rule.** Reading groups stay calm at rest. Use depth to identify an overlay or selected control, and keep ordinary content on tonal surfaces.

Press feedback on the next action and course cards uses the shared scale and spring easing. Reduced-motion preferences remove animation and transitions. Focus outlines remain visible independently of motion.

## Shapes

Gently rounded reading groups use the shared surface radius; controls use the shared control radius. Sheets use 20px corners, course covers 11px, and compact status labels 7px. Phone Today groups use the implemented 17px adaptation. Thin Lucide icons support text labels; the native checkbox remains the task control beneath its circular visual.

Course covers use one coherent generated atlas rather than unrelated photos. `src/personal/courseVisuals.js` maps ten subject families plus study/general fallbacks by subject name or an optional course object's `visualTheme`. Record IDs never select imagery. 体育 uses a running track, not basketball. The 12-cell atlas is `src/assets/course-atlas.webp`; its exact generation prompt and creation provenance are retained in `src/assets/course-atlas.webp.json`.

## Components

### Buttons and fields

Primary buttons use the action fill/foreground pair; quiet buttons use the white surface and ink. Hover changes are subtle, focus uses a visible 3px outline, and pending actions disable duplicate submissions. Existing Forms.Field/SubmitButton and useDraft remain the field, validation, retained-input and submit owners. Native selects and typed ISO dates retain platform ownership.

### Navigation and search

CloudChrome replaces only the cloud shell branch. Navigation still goes through the existing route and dirty-change guard. More preserves access to Review, tasks, textbooks, settings and other secondary tools. Ctrl+K or Meta+K opens the existing DeskSheet search. It searches saved course names and navigation pages only; its query is transient, clearable, and absent from URL or persistent storage.

### CourseCard / CourseArtwork

The same artwork owner is used by Today, the cloud catalogue and course detail. Covers are decorative and hidden from assistive technology; visible course names remain the meaningful labels. `coursePresentation.js` uses live location/teacher fields first, then presentation-only metadata keyed to the owner's iCalendar UIDs. No second set of schedule times is stored. The explicit late-study location is 一教420; a cover or concept never supplies a class time, teacher or room.

### TaskRow

TaskRow reuses the existing TaskCheck, cloud progress and durable local outbox owners. A labeled native checkbox keeps mouse/keyboard access. A left swipe reveals the same completion or restore action; the affordance does not delete a task. Pending, offline and failed changes retain their existing truthful state and recovery behavior.

### Reading, forms and overlays

TodayPage composes existing data and ReadState behavior. DeskSheet remains the shared native-dialog owner with Escape, focus return and scroll locking; ConfirmDialog remains the confirmation/discard owner. Existing evidence/source viewers, LearningFocus and form owners retain their semantics. Loading, empty, offline and error states describe the actual read or saved intention. Reading, generated content, adoption and demonstrated mastery remain distinct.

### PWA presentation

The public manifest uses the shared light canvas theme and existing standalone display. The Apple 180px icon and viewport-fit metadata are present. The service worker permits hashed public image assets for the shell; this does not add private-source caching or establish native app installation. No weather dependency is introduced.

## Do's and Don'ts

### Do:

- **Do** update canonical runtime tokens and this mirror together.
- **Do** use readable text and action pairs for their intended surfaces in light and dark appearance.
- **Do** reuse the shared course artwork, task row, sheets, forms, read states and navigation guard.
- **Do** preserve real course identity, immutable evidence and the existing confirmation boundary.
- **Do** keep keyboard focus visible, touch targets usable and reduced-motion behavior intact.
- **Do** report the migration scope and real-device verification limits accurately.

### Don't:

- **Don't** turn a reference image or generated concept into product facts, rooms, schedules or progress.
- **Don't** choose course imagery by record ID or treat decorative artwork as evidence.
- **Don't** add task deletion, weather, remote fonts or new confirmation owners through this visual change.
- **Don't** claim that token inheritance completes the redesign of a deeper screen.
- **Don't** claim physical-device installation, safe-area acceptance or public deployment from browser tests.

## Phase 7 Health Overview

The lightweight Health page consumes the existing typography, semantic colors, surfaces and native disclosure patterns. Six daily-metric cards use three/two/one columns on desktop/tablet/phone, with small SVG trends, hollow partial-data points and gaps for missing dates. No new tokens, fonts, illustration, chart dependency or Today composition is introduced. Behavior and privacy are owned by UX-CONTRACT.md and HEALTH-OVERVIEW.md.
