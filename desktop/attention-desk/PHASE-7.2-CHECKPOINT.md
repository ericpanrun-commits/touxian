# Phase 7.2 — paused at user request

Historical Docker/Fly checkpoint only. The user subsequently resumed and replaced
this architecture with Phase 7.2-Free. Current status is in
`PHASE-7.2-FREE-REPORT.md`: approved private Supabase migration/import complete,
GitHub owner push/configuration and real cloud acceptance pending. Do not run the
Fly deployment preparation retained below or interpret this old pause as current.

Checkpoint: 2026-10-15 (Asia/Shanghai). Status: **PAUSED**, not fully complete.

The user requested an immediate stop after Docker Desktop repeatedly failed with
`initializing Ingest server ... sailor-ingest.sock ... The file cannot be accessed by the system`.
Do not start or repeatedly query Docker Desktop. Do not reset Docker, delete its
data, or change WSL/system networking. Wait for an explicit instruction to resume.
Keep all current code, database copies, and test results. No rollback was performed.

## Verified stop

Windows process inspection (without invoking Docker) found no Docker CLI,
Docker Desktop/backend, Compose, buildkit, Fly CLI, or matching shell retry helper
running at this checkpoint. Earlier diagnostic calls and read-only Fly audits
have finished. No Docker build or Fly deployment was started. No automated Docker
retry helper was installed.

## Preserved work

- Development/production runtime identity and a fail-closed automatic Bark guard.
- Separate protected cloud FastAPI entry point, one process, persistent disk
  validation, SQLite scheduler lease, existing atomic notification claim.
- Migration 009: production database identity and passive worker status.
- Safe SQLite backup with bounded retention, offline preparation/manifest tools,
  and preservation of existing notification baseline/history/deduplication.
- Dockerfile, deny-by-default .dockerignore and validated fly.toml template.
- Configurable worker API routing; desktop Reminder/Calendar stay local.
- Worker status UI and additional browser fixtures.
- Read-only deployment preflight and staged-secret import helper drafts.

The real `server/.env` has not been changed. The existing live desktop backend has
not yet been restarted onto these changes; runtime behavior of that old process
must not be represented as the new development guard being deployed.

## Evidence

- `artifacts/phase72-full.log`: **927 passed**, one dependency deprecation warning,
  in 94.75 seconds. Later tooling/UI edits still need targeted verification.
- `artifacts/cloud72-retest.log`: **194 passed**, one dependency warning; includes
  authority gates, authentication, bootstrap, backup retention, lease concurrency
  and migration safety. These overlap with the full suite, not 194 extra tests.
- Frontend tests: **30 passed**. Vite production build succeeded before the most
  recent UI copy/browser fixture edits; rerun the relevant checks after resuming.
- Strict UI audit: **0 errors / 0 warnings**.
- `artifacts/phase72-migration/report.json`: integrity check `ok`, all original
  business rows preserved. Raw items **559 → 559**, official items **542 → 542**,
  notification records **539 → 539**, previous Bark activation preserved.
- Immutable pre-migration backup:
  `../data/attention-desk/personal.pre-phase72-20261015T030442Z.sqlite3`.
- Prepared migration copy:
  `artifacts/phase72-migration/import/production-20261015T033810972122Z.sqlite3`.

## Cloud and cost state

Read-only Fly audit found only the unrelated `ericpanphoto` application (photo
service), a stopped shared CPU/512MB machine in Singapore and its attached 3GB
`atlas_data` volume. None were changed or reused. The school worker app/volume
do not exist. New paid resources created: **0**. No cloud secret was uploaded.

The proposed school worker uses a separate single 512MB machine and 1GB persistent
volume. Creating these requires the user's explicit cost approval. Docker
recovery does not grant that approval. The earlier public pricing estimate was
approximately $3.84 per 30 days for IAD compute plus 1GB volume, excluding egress
and other usage; recheck pricing before requesting approval.

## Remaining work after explicit resume

1. Review the latest helper drafts and UI fixtures; complete lint/type/design
   checks, targeted tests and browser acceptance. In particular, account for the
   Fly apps JSON field casing in the Python secret-import helper and use the
   actual shared ReadStatus error text in its new browser assertion.
2. Validate the production image in an available container build environment.
   Local Windows Docker Desktop is not required for the cloud worker to run;
   another Linux/remote build environment can produce the image. Do not start
   such a remote builder if it creates unapproved charges.
3. Verify local Linux container restart/persistence and unattended scheduling.
   This is preparation evidence only, not actual Fly cloud acceptance.
4. Finish deployment/backup/rollback/security documentation and secret scan.
5. After all readiness work is reviewable, request approval for any required new
   paid machine/volume. Do not alter the photo service to avoid this gate.
6. Only after approval: create/import/deploy in silent bootstrap, validate actual
   cloud fetch/network/storage/restart/duplicates, then activate the sole cloud
   authority. Maximum real phone tests: one connectivity plus one controlled
   automatic notification, clearly marked test. No new real Bark push occurred
   during this preparation.
7. Commit and tag truthfully; do not issue a stable/fully-complete cloud claim
   until real cloud operation with the local computer off is verified. No push.

At this checkpoint, cloud 24/7 operation, real cloud Bark delivery, actual Fly
restart persistence, and the latest browser changes remain **unverified**.
The working changes remain uncommitted and preserved.

## Resumed audit — 2026-10-15

The user explicitly resumed Phase 7.2 while prohibiting any use or repair of local
Docker Desktop. A Docker-free Fly CLI/documentation audit is now recorded in
`PHASE-7.2-REMOTE-AUDIT.md`. Remote builds are technically supported, but there is
no demonstrated free end-to-end deployment path: the independent worker machine
and persistent volume are missing, and managed-builder billing/allowance is not
verified. Stop before provisioning/build/deploy at **BLOCKED BY USER APPROVAL**.

No previous code or data was rolled back. New authority/bootstrap/authentication/
backup/migration regression: **153 passed** (overlaps existing tests), stored in
`artifacts/cloud72-remote-audit-tests.log`. Existing photo resources are unchanged;
no local Docker calls, cloud secrets, new paid resources, or real Bark pushes
occurred during the resumed audit. The original pause section above is historical;
the current blocker is cost authorization, not Windows Docker availability.
