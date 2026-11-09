# Phase 7 — Health Overview

HealthData remains the owner of uploads, health history, reports and Qwen jobs.
Eric OS reads only six latest complete daily aggregates and a seven-calendar-day
projection. Both frontends share `src/health/HealthOverview.jsx`. No dependency,
database migration, sync record, background poll or model request is added.

## Read contract and boundaries

`GET /api/integrations/eric-os/overview?view=trend` on HealthData returns
`health-overview.v1`. `view=summary` omits all daily/chart fields. The fixed metric
order is sleep hours, steps, resting heart rate, HRV, active energy and exercise
minutes. Labels/units are maintained in the shared `health_contract.js` contract.
Each metric has `latest_value`/`latest_date`; trend mode also has seven `days`
(`date`, nullable numeric `value`, `complete|partial|missing`) and bounded numeric
chart coordinates. No HTML, report text, raw export, snapshot ID or fingerprint
is returned.

The window ends today in Asia/Shanghai. Latest complete values come from the
same rolling 30-day calculation as HealthData. Missing values are never zero;
valid zero remains zero. An isolated point remains a point; missing dates break
lines. Partial values are plotted with hollow dots but excluded from latest
complete values. Existing KV conversion does not retain every incoming explicit
day status; this feature follows HealthData's existing normalization rather than
reinterpreting the source or changing ingestion.

`source_generated_at`, `received_at`, `latest_health_date` and `fetched_at` have
different meanings. Refreshing never advances source timestamps. Analysis status
is current only when KV's receipt time and latest date match the existing status
RPC. Otherwise it is `unknown`; missing KV is `empty` even if old analysis rows
remain. A receipt match is not a new atomicity/fingerprint guarantee.

The cloud endpoint is `/functions/v1/eric-app-api/health/overview`. It runs after
the existing account/workspace/device checks and also pins one owner/workspace
pair. `/health` remains the public service probe and contains no health data.
The desktop endpoint is `/api/personal/health/overview`, registered only in
`server.main`. It checks runtime, loopback peer, Host, exact origin ports and
Fetch Metadata; it is absent from Collector/cloud applications.

Adapters fix the upstream HTTPS origin, allow GET only, reject redirects, bound
response bodies to 32 KiB and abort after eight seconds. All data responses use
`private, no-store`. Upstream auth errors become Health-unavailable errors, not
Eric OS account-expiry errors. Unknown fields are stripped after validation.

## Private configuration (release preparation only)

Do not configure production or deploy as part of this PR. After a separate
deployment approval, create two independent random base64url secrets (32–256
characters) and store them in existing secret-management surfaces:

| Location | Names |
| --- | --- |
| HealthData Cloudflare Secrets | `HEALTH_OVERVIEW_CLOUD_TOKEN`, `HEALTH_OVERVIEW_DESKTOP_TOKEN` |
| Eric OS Edge Secrets | `ERIC_HEALTH_OVERVIEW_TOKEN` (cloud token), `ERIC_HEALTH_OWNER_ID`, `ERIC_HEALTH_WORKSPACE_ID`, `HEALTH_OVERVIEW_MODE=trend` |
| Windows | Run `scripts/configure-health-overview.py` with the existing desktop Python in an interactive Windows terminal; enter the desktop token and `trend` |

Verify owner/workspace identifiers from the existing trusted Eric OS account
configuration. Never use a query parameter or browser-supplied identifier as the
authorization mapping. Missing configuration is disabled by default.

The Windows helper encrypts the token with current-user DPAPI before writing
`health-overview.json` beside the existing personal database. That file contains
connection configuration only, never health data. It is Git-ignored. No settings
page, shared-worker secret, service-role key or health data is copied.

Do not reuse report/upload/worker credentials. HealthData rejects privilege-secret
collisions and identical cloud/desktop tokens. Rotate either Overview token
independently. Keep all secrets out of shell arguments, URLs, logs and client
build variables.

## Frontend privacy and lifecycle

Health fetches on entry and explicit Refresh only. PWA uses low-level authenticated
`request()` with owner/device scope, bypassing `read()`, cloud transport caches,
Dexie, outboxes and changes feeds. No health values enter localStorage,
sessionStorage, IndexedDB, Cache Storage or a module-global data cache. Public
hashed JavaScript/CSS can still be shell-cached; private JSON is not.

Leaving the route, pagehide, offline, auth expiry/sign-out/account change or
unmount clears rendered health data and cancels/invalidates pending responses.
After an offline event, reconnecting requires explicit Refresh. Focus/pageshow/
visibility restoration clears a previous-day window without automatic fetching.
The original site opens without credentials or referrer and keeps its own login.
Phone Health remains under More; existing bottom tabs and Today are unchanged.

## Repeatable local checks

Run in the app directory with existing locked dependencies:

```powershell
npm.cmd run test:frontend
npm.cmd run build
python -m pytest server/test_health_overview.py server/test_engineering_portfolio.py -q
deno test --allow-read --allow-env supabase/functions/eric-app-api/
$env:ERIC_HEALTHDATA_SOURCE = '<separate HealthData checkout>'
deno test --allow-read --allow-env tests/health-integration.ts
$env:ERIC_TEST_PYTHON = '<existing desktop Python executable>'
node tests/health-browser.mjs
```

The browser fixture starts two owned loopback test servers and imports no
production main application. It uses synthetic data, fake auth/network and the
real production builds. Browser plugin was unavailable; Playwright is the recorded
fallback. Reports/screenshots are under `artifacts/health-overview` unless
`ERIC_HEALTH_EVIDENCE` overrides it. Real installed iPhone PWA and production
credentials are release gates, not claims made by these tests.

## Rollback and production-source drift

Set Edge `HEALTH_OVERVIEW_MODE=summary` and Windows mode `summary` to remove
curves while retaining the same authentication. Set mode `off` and revoke tokens
to stop data access; the original-site link stays available. Authentication
failure must never fall back to an unprotected data endpoint.

This PR separately preserves deployed `eric-app-api` v28 source before adding
Health. Keep its official-source, Vision and guarded WeChat code. For a future
Windows promotion, apply only the new module plus the two router-registration
lines to the current installation; never copy the repository's whole `main.py`
over the installation's later local repairs. Preserve Site v23's existing PWA
lifecycle and all concurrent edits. No merge/deployment is included here.

No health DB rollback is needed. No original, KV key, snapshot, Qwen result or
Life Ledger data is deleted. Existing HealthData deletion clears KV but does not
purge Supabase history; Phase 7 neither changes that behavior nor advertises
full-history deletion.
