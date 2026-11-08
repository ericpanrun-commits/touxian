---
version: 1
slug: "src-cloud-todaypage-jsx"
primary_target: "src/cloud/TodayPage.jsx"
related_targets: ["src/cloud/CloudChrome.jsx","src/cloud/responsive.css","src/cloud/today.css","src/personal/CourseCard.jsx","src/personal/TaskRow.jsx"]
---

# Today surface brief

## Scope and mode

Primary target: `src/cloud/TodayPage.jsx`. Related targets: CloudChrome, responsive.css, today.css and the shared course/task presentation owners. Visitor mode: **Operate**.

Eric uses Today to orient a university study day on Windows, iPad and iPhone. The daily job is to see the next class/action, real timetable context, tasks and the most useful study continuation. Deeper reading and editing continue through the established routes.

## Direction contract

**THESIS:** A personal university study desk leads with the day and its next useful action. It avoids generic administrative counters and never invents learning evidence to fill a dashboard.

**OWN-WORLD:** Restrained gray canvas, white reading groups, sparse blue action, local system typography and coherent subject covers. Navigation adapts to each device class; no decorative operating-system window controls.

**STORY:** Eric understands today's timing and current context, opens the relevant course or action, then continues through existing study, Inbox and confirmation owners. Content comes from actual registered courses, saved learning evidence and confirmed planning facts.

**FIRST VIEWPORT:** Date/week and greeting lead, followed by a full-width next-class/action strip. Desktop gives schedule a broad left area and supporting context alongside. Phone puts schedule first, then tasks, progress and a horizontal course gallery; focus, upcoming and reviews follow. The next action stays above this stack.

**FORM:** Owner-authorized incremental Apple HIG-inspired Web/PWA adaptation; no direction-card roll was used for this specified redesign. Position/seed key: not applicable. User references and the generated concept are directional only; invented concept claims and rooms have no product authority.

**FINISH:** unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Implemented truth

- Today reuses existing GET dashboard/courses/learning/planning-upcoming projections, TaskCheck/progress/local outbox, ReadState, DeskSheet and Forms/Confirm owners.
- Desktop above 1100px uses a permanent 216px rail and multi-column Today. Tablet 640–1100px uses a collapsible 76/208px rail and two columns, with a separate 640–820px arrangement. Phone below 640px uses five bottom tabs, the single-column priority order and a horizontal course gallery.
- Phone tabs are Today, 课程, 学习, Inbox and 更多. More preserves secondary access, including Review, tasks, textbooks and settings. Route identity and dirty navigation guards remain canonical.
- Ctrl+K/Meta+K uses DeskSheet for course/page search only. Its query stays transient and clearable. Task left-swipe reveals completion/restore; there is no delete contract.
- Safe-area variables, 100dvh, coarse-pointer targets, hover separation and reduced-motion rules are implemented. These are browser behaviors, not proof of physical-device installation.
- CourseCard/CourseArtwork are shared by Today and cloud catalogue/detail. Subject mapping covers ten families plus study/general fallbacks, supports optional object visualTheme, and never uses record IDs to choose art. 体育 is a running track.
- The original personal EricInbox component was restored from the latest existing project backup; its established behavior is retained. The deeper Learning, Lecture, Review, Inbox, Capture, Textbooks and system-form layouts remain in place and consume shared tokens. The local TodayDesk composition is retained.

## Data and artwork authority

The current registered ten-course catalogue is displayed without modifying source rows. `courseReference.json` holds presentation-only teacher/location metadata from the owner's 139-UID iCalendar; no schedule times are duplicated, and current API fields win. `coursePresentation.js` uses the explicit owner late-study reference 一教420 without adding an evening time.

The coherent 12-cell course atlas is `src/assets/course-atlas.webp`. Its exact generation prompt and creation provenance are in `src/assets/course-atlas.webp.json`. References and concept images are not evidence of teacher, room, time, progress or a supported feature.

## Verification and limits

The main implementation task reports the full Chromium and WebKit regressions, 136 frontend tests and both production builds passing. The finish review identified primary-button contrast; the shared runtime now separates accent, readable text, action fill and foreground roles in light and dark appearance.

Physical hardware safe areas, installed-PWA behavior and real-device installation are not proven by those checks. No public deployment is claimed. The migration ledger in DESIGN.md defines the completed surface scope.
