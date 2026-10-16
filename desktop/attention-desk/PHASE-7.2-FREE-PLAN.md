# Phase 7.2-Free implementation checkpoint

The user's 2026-10-15 request supersedes the Fly worker proposal. New fixed monthly
cost must be $0. No Fly resource, Docker Desktop, WSL modification, paid runner or
paid Supabase feature is authorized. Existing Fly-specific files are inactive
historical preparation; do not execute their deployment helpers.

## Initial audit evidence (before approved installation/import)

- Repository `sssssjw11/touxian` already exists and is PUBLIC; do not change its
  visibility. Default branch is main. No existing Actions workflows were listed.
- Both gh CLI and GitHub connector have only READ access to this repository, not
  push/admin. Do not change credentials, push, or activate a workflow automatically.
- Standard GitHub-hosted Ubuntu runners on public repositories are free. Private
  GitHub Free includes 2,000 minutes/month; actual owner's private usage is not
  accessible through this current read-only identity. It does not limit this public
  repository's standard-runner minute allowance.
- Supabase organization `afnpaqrftzejqvikeakg` is confirmed Free. Two existing
  healthy projects consume its two active project slots. No project is created.
- Candidate reusable project `mantuehuzptactymjpqg` has existing IELTS/finance
  public tables; its database is 14,191,763 bytes. Isolate school state in the
  private `zqu_worker` schema; do not modify those tables or their policies.
  The other project's HealthData contents were not inspected.
- The school-only verified SQLite export is 4,214,839 bytes: 29 sources, 543 raw
  article identities (includes unprojected discoveries), 543 source references,
  543 aliases, 539 existing notification records, and 711 crawl runs. Database
  backup integrity is ok, activation `2026-10-15T02:23:03.077645+00:00` retained.
  This export is not a claim that the cloud import has already been executed.
- Supabase Free includes 500MB database and 5GB egress, no paid backup/PITR/IPv4.
  Never upgrade or add an addon. Monitor actual total database size after import.

## Implementation

Hourly workflow `17 * * * *` UTC, also hourly :17 in Asia/Shanghai, standard Ubuntu
24.04, one job, 12-minute timeout, fixed concurrency, no artifacts or package cache.
Manual runs default dry; scheduled execution stays gated by `ZQU_ACTIONS_ENABLED`
until the owner enables it. GitHub scheduling may be delayed/dropped, so status is
based on last successful persisted run, with stale detection after three hours.

`python -m server.actions.worker` runs once and exits. It uses the existing
OfficialSourceMonitor crawler through the new synchronous run_once entry and a
PostgreSQL repository, existing parser/classifier/relevance/policy/Bark client and
dispatcher. No FastAPI app, React, resident scheduler or local SQLite production
state is started. Each cycle shares all due sources; high/normal check at most
hourly and low priority remains four-hourly, with inherited failure backoff.

Production writes use a TLS-verified Supabase PostgreSQL pooler connection stored
only as GitHub Secret `SUPABASE_DB_URL` (safe equivalent to a service-role key).
It avoids paid IPv4 and a web API. Default desktop environment cannot automatically
send production Bark. GitHub production context, database identity and a verified
first dry run are required before live sending.

The native PostgreSQL importer is atomic. The SQL connector size fallback uses
INSERT-only staging batches with exact row checks, then activates meta only after
all counts match. Missing historical events are suppressed. In-flight migrated events are conservatively suppressed
with their original state retained in metadata, rather than replayed. Notifications
claim atomically in PostgreSQL; unknown outcomes are terminal, sent never replays.
Only detailed crawl/worker logs expire after 30 days; identities and dedup persist.

The singleton `zqu_worker.cloud_status` contains only timestamps/result flags and
remains private after the user's migration-safety review. A later desktop read
path needs separate review; no public table/function/policy is installed here.
All school tables have RLS and no anon/authenticated access.

## 2026-10-16 checkpoint after explicit approval

Applied exactly the audited revision (SHA256 in `SUPABASE-MIGRATION-SAFETY-AUDIT.md`),
with all six destructive counts zero and no warning. Migration history records
`20261016114712 zqu_actions_state`. All 10 private tables exist with RLS.
Original 17 public business tables and every row count are unchanged after both
installation and import. No protected-schema business object was modified.

Imported the verified school-only export: all six data counts match the table
above. All 539 notifications remain suppressed/historical, dedup intact,
replayable history zero, baseline unchanged. Independent article-text checksum
matches. Database size is now 18,508,947 bytes; school relations 4,120,576 bytes.
First real hosted dry run is deliberately NOT marked verified: worker_runs=0.
No Bark, Docker invocation, new Fly resource or paid resource was used.

Regression: backend 954, frontend 30, browser 24 passed; production build and
new Python static checks passed. Existing local status UI describes the local
backend; private cloud_status cannot yet be read by the browser. Do not expose
database credentials or restore the removed public table to change that.

Remaining acceptance: owner must push the reviewed commit to default main and
configure GitHub environment/Secrets. Current identity has READ only, remote
workflow list is empty. Run first hosted manual cycle with dry_run=true, inspect
its persisted result and historical count, then perform the bounded real Bark
acceptance before enabling schedule. Do not claim computer-off delivery until
real hosted runs and the schedule are verified. Detailed steps and 22-item status
report are in `PHASE-7.2-FREE-REPORT.md`. No paid workaround is authorized.

Monthly schedule: 720 runs/30 days or 744/31 days. Actual Actions elapsed time and
source growth are unmeasured until a real job is enabled; report measurements,
not local tests as hosted-run timings. Forecast private minutes conservatively
using setup plus a measured representative crawl and provider billing rounding.

Sources: [GitHub billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
[scheduled workflow behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[Supabase Free](https://supabase.com/pricing),
[Supabase shared pooler](https://supabase.com/docs/guides/database/connecting-to-postgres).
