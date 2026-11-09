# Phase 7 Health Overview — Implementation and acceptance

Date: 2026-11-09, Asia/Shanghai. Status: implemented for review; no production
merge, deployment, credential configuration or shared-worker restart performed.
All health values in tests and screenshots are synthetic.

## Delivered behavior

Windows and PWA now have a Health page with six latest complete metric values,
their source dates, seven-calendar-day mini trends ending today in UTC+8, upload/
generation/read timestamps, existing automatic-analysis state, explicit Refresh
and a protected original-site link. Complete reports and 30-day views remain in
HealthData. Phone navigation retains its existing tabs and exposes Health in More.

The HealthData prerequisite commit preserves its existing uncommitted upload,
queue and Qwen integration source. The Eric OS prerequisite commit preserves
the **deployed Edge API v28** before adding the Overview reader. These are
baseline preservation commits, not newly invented features or migrations.
HealthData's companion feature must be reviewed/deployed separately before
enabling the Eric OS connection.

The feature adds no health database, raw-data copy, persistent browser health
cache, synchronization, background health poll, model inference or new package
dependency. Current source/timestamps and freshness are separate from page reads.

## Baselines and protection

| Authority | Verified baseline / protection |
| --- | --- |
| GitHub Eric OS main | `b2f41505e5f1e729acb93846dc052e59d53c2881` |
| Actual Windows HEAD | `a65875e`, with existing dirty files and Phase 6 additions preserved |
| Current Site | v23, source `694f3e1fe9e2cf0e2af3c4a71a78898415ad0203`; cloud navigation/page/SW sources match main after normalizing line endings |
| Current Edge API | v28 source downloaded read-only and retained in its own commit |
| Actual HealthData HEAD | `64b80f3`, plus its existing automation working-tree source |
| Original workspace audit | SHA-256/HEAD/status verification covers 1,054 Windows source files and 43 HealthData source files; private manifests retained outside Git |

Development uses separate clones. A second private Windows validation tree was
created from the actual working source, with only Health files/router wiring
overlaid. No original `.git`, source file, DB, runtime config or raw health data
was modified. Life Ledger and PersonalAIWorker were neither edited nor restarted.
Pushes target feature branches; existing workflows have no feature-push production
deployment trigger. Site source was opened read-only and never pushed or saved.

## Verification results

| Check | Result |
| --- | --- |
| HealthData existing suites | Rolling dashboard, refactor, preview data and health automation all pass |
| New HealthData Overview tests | **5 passed**: existing calculation parity; zero/missing/partial semantics; stale dates/midnight; token isolation; failure behavior/no writes |
| HealthData syntax / Worker package | `npm run check` and Wrangler **dry-run only** passed |
| Eric OS final full backend | **1,059 passed, 25 skipped**, no failures |
| Desktop Overview against actual Windows source | **12 passed**, including DPAPI, wrong origins/peers/runtime, whitelist, streamed size limit and overall timeout |
| Repository frontend tests | **49 passed**, including new contract/privacy checks |
| Actual Windows frontend regression | **204 passed** using its existing ignored synthetic dependencies and Node TypeScript-transform flag |
| Edge regression and security | **12 passed** across file, Vision and Health tests; auth/device checks precede Overview; fixed upstream; no privileged credential forwarding; eight-second abort |
| Cross-project integration | **1 passed**: real HealthData router/projection → real Edge reader → real authenticated Eric route; only network/storage replaced |
| Production frontend bundles | Both desktop and PWA builds pass; actual Windows source overlay desktop build also passes |
| Health browser acceptance | Desktop + PWA pass: six values/charts; gaps/zero/partial; daily disclosure; keyboard/touch; manual refresh/no 15s poll; offline/reconnect; midnight; summary/error fallback; late-response/identity invalidation; second-account denial |
| Storage inspection | Health payload/token absent from localStorage, sessionStorage, IndexedDB and Cache Storage; no browser calls directly to HealthData |
| Existing PWA browser regression | Pass: three devices, task completion/restore, reading progress, course routing, file recovery, textbook reading, offline durable capture and public SW offline shell |
| Existing Phase 6 browser regression | Pass: folder and standalone cards, original file integrity, filters, previews, keyboard/error states, responsive layout and no external requests |

The standard existing PDF chunk-size advisory and Starlette/httpx deprecation
warning remain. HealthData's locked development tooling reports existing npm
audit findings; dependencies were not upgraded as part of Health integration.

### Actual Windows baseline failures

The broader actual Windows source snapshot has more tests than GitHub main. Its
full run produced **2,208 passed, 76 skipped, 29 failed**. Every one of those
29 failing test IDs was rerun against the unchanged pre-Health source snapshot:
**the same 29 failed**. The comparison manifest is retained in private evidence.
No Health test was among them and no introduced failure was found.

- Eight network tests fail under the machine's legacy GBK default; their complete
  15-test module passes with `PYTHONUTF8=1` and unchanged code.
- One existing extraction migration test expects a table absent from its old
  synthetic schema.
- Twenty existing WeChat tests have stale pipeline/snapshot expectations. This
  task does not rewrite them or alter current notice/analysis behavior to fit them.
- Initial snapshot frontend runs lacked ignored DeepSeek synthetic fixtures and
  Node's required TS transform mode. After restoring only those known source/
  synthetic dependencies in the private copy, all 204 frontend tests pass.

Skipped tests are not counted as passed. Full production auth, actual iPhone
installation/cellular behavior, live uploads and Qwen inference are **not** claimed
by this pre-deployment acceptance. They remain controlled release checks after
separate production approval; no real data or paid analysis was used to obtain
a passing test.

## Security, rollout and rollback

Two separately revocable Overview-only tokens protect HealthData. The Edge reader
also pins an Eric owner/workspace after existing account/device verification.
Desktop access is loopback/origin restricted. GET responses are bounded to 32 KiB,
timeouts to eight seconds, redirect/cookie forwarding disabled, and the output is
whitelisted. The public service-health probe has no health data.

Data exists only in page memory and is cleared on navigation, pagehide, offline,
identity changes or unmount. Pending responses cannot restore an old identity's
view. Reconnect and cross-day resume require manual Refresh. Neither the global
offline cache nor existing SW policy is broadened.

Configuration defaults to disabled. After a separate release approval, deploy
the HealthData protected route and Edge adapter, configure scoped secrets and
verified owner/workspace, then enable the frontend. Apply only the additive Health
router wiring to the current Windows `main.py`; do not replace its complete file
from an older repository baseline. Preserve all concurrent v28/Windows fixes.

For reduced scope, choose `summary` on the Edge and Windows configuration. For
disconnect, choose `off` and revoke Overview tokens. Authentication problems never
fall back to an unprotected endpoint. Rollback needs no database recovery or
HealthData deletion. Existing KV-vs-Supabase consistency is documented in
[the implementation contract](../../desktop/attention-desk/HEALTH-OVERVIEW.md).
The [P2 review-fix addendum](phase7-review-fixes.md) supersedes the inherited
day-status limitation and records upload/KV/midnight and v23 QR compatibility checks.

## Review artifacts

All images show **synthetic data**, including deliberately artificial metric
values used to test zero/missing/partial behavior:

- [PWA desktop capture](evidence/pwa-desktop.png)
- [PWA phone capture](evidence/pwa-phone.png)

Private local evidence additionally includes tablet/dark captures, per-command
logs, original-source integrity hashes, the baseline-failure comparison, existing
PWA/portfolio reports and the dry-run Worker bundle. No private production data
or credentials are included in this report or PR.
