---
version: alpha
name: Attention Desk personal workflow
description: A local personal notice and action workbench with reviewed AI candidates, preserving the established paper and ink interface.
colors:
  primary: "#111111"
  background: "#f3f2ec"
  panel: "#ffffff"
  soft: "#e8e7e0"
  muted: "#62625d"
  subtle: "#8a8982"
  border: "#cfcec6"
  action: "#e3232d"
  actionSoft: "#fbe7e7"
  focus: "#2359d7"
  focusSoft: "#e8efff"
  success: "#18744c"
  successSoft: "#e3f2e9"
  highlight: "#f2c84b"
  highlightSoft: "#fff6cc"
typography:
  sans:
    fontFamily: 'Arial, "Microsoft YaHei", "Noto Sans CJK SC", sans-serif'
  mono:
    fontFamily: '"Cascadia Mono", "SFMono-Regular", Consolas, monospace'
rounded:
  DEFAULT: "0px"
spacing:
  section-gap: "20px"
  section-padding: "22px"
  content-max: "1240px"
components:
  button:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.panel}"
  buttonHover:
    backgroundColor: "{colors.action}"
  field:
    backgroundColor: "{colors.panel}"
  fieldBorder:
    backgroundColor: "{colors.border}"
  helpText:
    textColor: "{colors.muted}"
  legacyMutedIcon:
    textColor: "{colors.subtle}"
  focusIndicator:
    backgroundColor: "{colors.focus}"
  statusDone:
    textColor: "{colors.success}"
    backgroundColor: "{colors.successSoft}"
  statusReview:
    backgroundColor: "{colors.highlightSoft}"
  evidenceQuote:
    backgroundColor: "{colors.focusSoft}"
  evidenceHighlight:
    backgroundColor: "{colors.highlightSoft}"
  evidenceHighlightBorder:
    backgroundColor: "{colors.highlight}"
  legacyPriorityP0:
    backgroundColor: "{colors.actionSoft}"
  eventRowPressed:
    backgroundColor: "{colors.soft}"
  dialog:
    backgroundColor: "{colors.panel}"
---

# Attention Desk design context

## Overview

The existing paper and ink workbench is the visual reference: off-white canvas, white working surfaces, black navigation, square edges and restrained red actions. The personal workflow now preserves raw text first, offers DeepSeek candidates, and creates formal records only after explicit review. The original chat and official-source screens retain their design.

The audience is one Chinese-speaking student working on Windows. The authoritative brief is the accepted Phase 0/1 workflow and the subsequent DeepSeek extraction and human-confirmation request. There is no public market launch or Japan-facing requirement. Chinese labels and familiar verbs explain actions; dates remain unambiguous ISO values and stored minute values use Asia/Shanghai.

Runtime CSS is canonical. This document mirrors `src/styles.css` variables and `src/personal/personal.css`; it does not generate tokens. Changes should reconcile those files and this frontmatter. New styles reuse existing palette variables rather than introducing a new theme.

## Colors

Ink establishes hierarchy and primary buttons; muted text supports descriptions on paper and white panels. Red is the established brand and button-hover accent. Blue denotes links and keyboard focus. Green indicates completion; pale yellow denotes review and exact evidence highlights. Status always includes words. This scope supports the existing light theme and system forced-color behavior; no new dark theme.

## Typography

Use the established local font stack; no font downloads. The root is 14px. New section titles are 28px (23px on narrow layouts), secondary titles 18px, tasks 16px, help text 13px. Text uses generous 1.6–1.9 line heights and wraps long Chinese or Latin text. Dates use the existing mono stack at 20px. Preserve source whitespace; never truncate the immutable notice.

## Layout

The existing sidebar remains 72px. New content has a 1240px maximum and 28px horizontal padding. White sections use 22px padding and a 20px gap. Detail has two columns above 950px; below that it stacks. Below 600px, the new route alone uses a 52px sidebar, 16px sections and one-column forms. The old screens retain their desktop geometry. Lists page by 50; original text has a bounded 440px scroll area. Feedback reserves space and buttons retain their text while busy.

## Elevation & Depth

Borders and surface contrast provide hierarchy; sections do not add decorative shadows. Confirmation uses an app-authored native dialog with a dim backdrop, constrained height and internal scrolling. It occupies the browser top layer.

## Shapes

Square sections and fields follow the existing visual identity. Controls have clear borders, consistent padding and recognizable checkbox/native-select affordances. No decorative icon containers are added.

## Components

Buttons reuse `.button` styles. Primary actions have text plus a Lucide icon; minor edits are blue text buttons. Busy submits swap the icon for the existing spinner, keep their label and disable duplicate actions. Disabled terminal edits have explanatory text and a restore action. Focus uses a blue 2px outline with a 3px offset.

Forms share `src/personal/Forms.jsx` Field/SubmitButton and `formHelpers.js`: labels, help, validation and first-invalid focus; `noValidate` avoids native error bubbles. Collection, settings and review extend these owners. Typed dates explicitly choose date or minute precision, with a separate confirmation for each retained date. Native select is intentional for small fixed option sets; keyboard operation and its popup must be checked. Textareas cannot resize and have usable bounded heights. Immutable source text is read-only content, not an editable textarea.

Feedback stays inline: persistent errors and polite success messages, no transient toast. Evidence opens the original region, focuses it and highlights the exact saved span. Status badges include text. Original text and long values wrap at narrow widths. Native dialog controls restore opener focus when it still exists.

File collection extends the same form with a dashed drop boundary and a native file picker. OriginalNotice owns document source selection, filename/page/paragraph labels, PDF page rasters, and original screenshot overlays. The overlay uses the established yellow highlight and scales from stored image coordinates. Document errors and OCR uncertainty use the existing persistent inline states; no new color, font, modal owner or separate file-analysis page is introduced.

Candidates use simple bordered subsections within the established form. Explicit actions and suggestions have written origin labels. Participation defaults to “decide later”; that state preserves an action plan without creating tasks. Warnings remain beside affected fields. Persisted drafts reopen after refresh. Analysis progress describes durable background work; unavailable credentials and provider failures retain the source and expose manual review. A deliberate new paid analysis uses the shared confirmation dialog because it replaces current preview edits. Settings explain when source text is sent and keep password entry transient.

Lucide remains the icon family, generally 14–19px stroke icons. Every new action has a text label or an accessible task-specific label. Existing loading animation communicates pending work; no new decorative motion. The established reduced-motion stylesheet continues to apply.

## Do's and Don'ts

Phase 3's source management uses the same natural-height sections, ink buttons, paper palette, native checkboxes and status labels as the shared workbench. Five source sections display last check/success, new and changed counts, failures and entry links to the existing RawInbox. The shared notice region displays immutable web versions and links to unparsed attachments. This adds source provenance without changing tokens, typography or the established event/AI workflow.

- Do preserve the exact notice and make each saved quote traceable.
- Do use established controls and colors across the new list, collection and detail views.
- Do keep failure inputs available for correction or retry.
- Do not introduce gradients, new brand typography or a broad redesign in this phase.
- Do not silently invent a deadline time or equate task completion with event completion.
