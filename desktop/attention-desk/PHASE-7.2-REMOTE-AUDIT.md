# Phase 7.2 — remote build and cost audit

Audited 2026-10-15, Asia/Shanghai. **BLOCKED BY USER APPROVAL** for new billable
production resources; Phase 7.2 is not fully complete.

No local Docker executable, Docker Desktop, repair/reset operation, WSL command,
or Windows network change was used during this resumed audit. All earlier code,
migration copies and test evidence remain intact. No build/deploy was executed.

## Account and project evidence

The installed CLI is Fly v0.4.86. Authenticated read-only commands inspected all
apps in the accessible personal organization, organization details, the existing
app's machines, volumes, secret names, deployed configuration and snapshots.

| Resource | Observed state | Reuse assessment |
| --- | --- | --- |
| `ericpanphoto` | Only app listed, suspended; Node/R2 photo service | Separate application; do not replace its image or configuration |
| `874975c0201298` / `patient-shape-4234` | Stopped, `sin`, shared 1 CPU / 512MB | Occupied photo-service machine, not an idle school worker |
| `vol_vjyx1gn1wo7we5xv` / `atlas_data` | Encrypted 3GB, `sin`, attached to that machine at `/data` | Contains photo DB `/data/atlas.db`; no spare unattached volume found |
| Existing secrets | 13 names for R2/Cloudflare/session/TOTP | No Bark or school-worker admin/database identity secrets; values not read |
| Existing snapshots | 30 listed, latest 2026-10-14 20:48:28 UTC, retention 30 days | Photo backups; no school production DB |
| Organization builder | `RemoteBuilderApp=null`; `RemoteBuilderImage` names the rchab image | Image reference is not an existing builder machine |
| Historical builder metadata | Existing photo release records `fly_builder_id=18577ee3c94628` | A previous build ID does not prove a currently reusable builder or free allowance |
| `zqu-workspace-worker` | Not listed / not created | Proposed independent school app |
| `school_data` | Not created | Proposed 1GB school persistent volume |
| Repository deployment pipeline | No existing workflow found; current Dockerfile/fly.toml are Phase 7.2 working changes | No existing Linux CI build job to reuse was demonstrated |

The photo app uses autostop=true and min_machines_running=0. It cannot provide
unattended school polling in that configuration. Co-hosting both apps on its one
512MB machine would require changing the unrelated photo image/startup behavior
and proving combined resource usage and data safety. That has not been established
as a safe reuse path and was not attempted.

A Fly volume belongs to one app and can attach to one machine. The occupied photo
volume therefore cannot be shared with a separate school worker app.
[Fly volume ownership and attachment rules](https://docs.fly.io/volumes/overview)

## Remote build conclusion

**Technical feasibility: YES. End-to-end deployment with no newly billable
resources established by this audit: NO.**

The installed CLI and current official command reference support `--remote-only`,
`--depot=true`, `--build-only`, and `--ha=false`. Remote-only selects a remote
builder instead of the local Docker daemon. An existing registry image can also
be deployed with `--image`, skipping a source build.
[Fly deploy reference](https://docs.fly.io/flyctl/cmd/fly_deploy)

Fly's Depot integration does not require opening a separate Depot account. Managed
Depot builders run outside the user's app list, so RemoteBuilderApp=null does not
prove there is no provider-managed Depot builder/cache. The read-only CLI evidence
does not establish its current size, region, available minutes or billing terms.
Do not infer free access from the absence of a builder app.
[Depot's Fly integration](https://depot.dev/docs/container-builds/integrations/fly)

Fly's September 2024 announcement described initially free builds and a future
300-minute allowance with $0.05/minute overage. This historical announcement is
not verification of this account's October 2026 terms. Depot's standalone paid
plans are also not evidence of Fly-integrated pricing. No build was initiated to
discover whether it would provision or bill resources.
[Historical Fly announcement](https://community.fly.io/t/depot-remote-builders-becoming-the-default/21756)

Legacy native remote builder creation can provision a builder app/machine/cache
volume (`fly-builder-*` name chosen by the provider). Do not invoke that fallback
or `--recreate-builder` without separately confirming cost authorization.

Even if a managed remote build were verified free, the independent always-on
production machine and persistent volume are still missing. This is sufficient
to stop deployment at the user's cost boundary.

## Concrete resource proposal, not executed

- Application: `zqu-workspace-worker` (subject to name availability).
- One shared CPU, 512MB machine in `iad`, no HA spare; do not create a second writer.
- One encrypted 1GB `school_data` volume, mounted at `/data`.
- SQLite `/data/production.sqlite3`; preserved identity, baseline and dedup ledger.
- Managed remote builder via explicit Depot selection, with its account billing
  terms checked/approved first. Do not create a standalone paid Depot subscription.
- No managed database, Redis, paid IP, domain or plan upgrade.

Published IAD prices: 512MB compute $3.69 per 30 continuously running days plus
1GB volume $0.15/month, approximately **$3.84 per 30 days**, before network/build
usage and other overages. Compute bills by runtime; volume capacity bills even
when the machine is stopped. A 31-day always-on month costs slightly more.
North American internet egress is $0.02/GB. Snapshots are $0.08/GB/month with the
first 10GB free. These are list estimates, not a verified account invoice.
[Current Fly resource pricing](https://docs.fly.io/about/pricing)

Existing potentially billable resources: the photo machine's stopped rootfs,
its 3GB volume (list capacity charge $0.45/month), its snapshots, and any actual
runtime/network usage. Snapshot listing sizes sum to 2,233,132,476 bytes; this is
not an invoice calculation because snapshots are incremental. Account credits,
actual invoice and current integrated-builder allowance were not established.

Cheaper/alternative paths:

- An already available Linux machine or CI runner with confirmed included minutes
  can build and push the image, then Fly deploys its digest with `--image`. This
  can avoid a Fly builder, but does not remove worker/volume running costs. No such
  existing pipeline was demonstrated in this project; do not push private work or
  trigger potentially paid CI automatically.
- Fly 256MB compute is listed at $2.19/30 days; with 1GB volume, approximately
  $2.34 before usage. Current parser/runtime memory safety at that size has not
  been verified, so the readiness template remains 512MB.
- No existing resource was confirmed both free and suitable for persistent,
  always-on production. A sleeping free web service is not equivalent to 24/7
  request-independent polling.

## Reviewed remote-only sequence after authorization

These commands are documentation, **not executed**. Before use, complete pending
readiness review/browser checks and obtain explicit machine/storage/build cost
approval. Create the approved independent app and single volume separately; never
substitute `ericpanphoto` to bypass the approval boundary.

Build only, with explicit remote selection and no local Docker fallback:

```powershell
& "$env:USERPROFILE/.fly/bin/fly.exe" deploy --app zqu-workspace-worker --config fly.toml --remote-only --depot=true --build-only --push --ha=false --deploy-retries 0
```

Record the immutable registry digest, then deploy that exact image without a
source build, preserving the silent bootstrap defaults in fly.toml:

```powershell
& "$env:USERPROFILE/.fly/bin/fly.exe" deploy --app zqu-workspace-worker --config fly.toml --image 'registry.fly.io/zqu-workspace-worker@sha256:VERIFIED_DIGEST' --ha=false --strategy immediate --deploy-retries 0
```

Before activating authority: stage secrets through stdin, import the verified DB
copy over authenticated SSH/SFTP, compare the manifest/activation/history, verify
health and protected API behavior, fetch real sources in dry run, then test actual
cloud restart persistence and duplicates. Enable the sole cloud scheduler/sender
only after those gates pass. Verify request-independent ticks without the local
servers. Maximum real Bark pushes: one connectivity and one marked controlled
automatic test. None of those cloud acceptance gates has been claimed passed.

## Preserved validation and remaining gate

The resumed Docker-free authority/bootstrap/auth/backup/migration regression
passed **153 tests**; log `artifacts/cloud72-remote-audit-tests.log`. Earlier full
backend evidence remains **927 passed**, frontend **30 passed**, and the earlier
production build succeeded. These sets overlap and are not summed.

Prepared migration preserves 559 raw items, 542 official items, 539 notification
records and the previous Bark activation; integrity check is `ok`. The source
default development runtime cannot automatically send production Bark; cloud
send requires all authority gates. The old desktop process still needs a planned
restart before its live behavior can be claimed to use the new guard.

New paid resources: **0**. Billing changes: **0**. Cloud secret uploads: **0**.
New real Bark pushes: **0**. Pending: approval of independent machine/volume and
any chargeable remote build, followed by actual deployment and cloud acceptance.
