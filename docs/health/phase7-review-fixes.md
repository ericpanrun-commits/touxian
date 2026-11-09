# Phase 7 P2 review fixes — 2026-11-09

Incremental review against Eric OS PR #7 at
`86cf55015e34255299d6e8d531e97dc6d64a88ce`. Companion HealthData PR #1 starts at
`78d5775919f9643ea28e843d2424e8b2d42b26eb`. Both existing feature branches are
retained. No merge, deployment, configuration change or service restart occurred.

## P2-1: reliable completion evidence

HealthData preserves validated upload `day_status` in its existing KV history and
summary. Normalization, analysis, Dashboard and both Overview modes share that
evidence. Dates passing never promote partial data; historical values without
evidence stay partial. Explicit missing and authoritative null replacements do
not reuse older values. The Eric cross-project fixture now declares its known
complete/partial evidence explicitly; no Eric Health runtime change is needed.

Five new real-router tests cover upload → KV → Overview, zero/null/missing,
legacy data, later explicit completion, and no-upload Shanghai midnight. The same
regression fails against the old HealthData PR commit and passes after repair.
The real Dashboard browser scripts also retain 2026-11-08 as all six latest
complete dates across midnight and 7/30-day switching, without read-side writes.
Structured upload payload/fingerprint/queue scheduling and Qwen code are retained.

## P2-2: retain the actual v23 QR notice

The read-only Sites version listing still identifies PWA **v23**, source
`694f3e1fe9e2cf0e2af3c4a71a78898415ad0203`. The restored line in
`src/personal/Evidence.jsx` renders this fixed notice for a WeChat snapshot with
`qr_in_original_chat`:

> 需扫码，二维码请返回原微信群查看

After restoring the line, the entire component matches that v23 source after
normalizing line endings/trailing whitespace. Two regression tests render the
actual shared component: matching notices show the fixed hint once, while
ordinary/non-WeChat/unrelated warnings do not show it. Untrusted warning message
HTML is not rendered. Desktop and phone browser samples confirm both behaviors
without page errors. No v22 bundle or old application tree is promoted.

Fresh read-only retrieval confirms `eric-app-api` is still **v28**. All 13 live
source files match the preserved `29fe6a6` v28 baseline under the same whitespace
normalization. This incremental repair changes **zero Edge files**. Existing
Phase 7 additive Edge changes and all v28 official-source/Vision/guarded-WeChat
behavior are retained.

## Validation and scope

| Check | Result |
| --- | --- |
| Eric `npm run test:frontend` | 51 pass (49 existing + 2 QR tests) |
| Eric `npm run build` | Desktop and PWA bundles pass; existing PDF size advisory remains |
| Deno Edge + cross-project integration | 13 pass |
| HealthData `npm run check` / `npm test` | Pass; four existing script suites and 10 Node tests pass |
| HealthData Worker package | Wrangler dry-run passes; no deployment |
| Browser checks | QR desktop/phone and HealthData midnight checks pass with synthetic data |
| Original workspace integrity | 1,054 Windows + 43 HealthData source hashes, Git HEAD and status unchanged |

Browser plugin was unavailable; existing Playwright was used. Private local
evidence retains logs, screenshots and v23/v28 comparisons. No dependency or
lockfile changed. The prior Windows comparison found the same 29 failing IDs
before/after Phase 7. This revision changes no Python/backend files and does not
rerun or repair those unrelated baseline failures; the original installation is
unchanged.

The Eric runtime delta is one UI line. Its remaining changes are regression
tests/fixture and documentation. HealthData runtime changes are limited to
completion metadata persistence, normalization and its truthful tooltip. No new
database, raw upload copy, persistent health cache, polling, model analysis,
diagnosis or prediction is introduced. Auth, PWA lifecycle, Attention Desk,
Life Ledger, Engineering Portfolio, existing Qwen jobs and production services
retain their boundaries. Historical completion is not backfilled by inference.

This addendum supersedes the inherited day-status limitation in the initial
acceptance report. Retain conservative reads during any later rollback. Release
approval and Astra incremental re-review remain pending.
