# Continuous personal notification actions: release contract

This draft extends the batch/queue repair with a future-only, explicitly enabled
personal-action pipeline. It does not enable or deploy any production service.

## Behavior

- The existing desktop entrypoint supervises the analysis and scoped publication
  workers. Workers tick every 60 seconds and recover actual process exits with
  persisted backoff. Existing collection scheduling remains unchanged.
- A separately reviewed activation pins the implementation, API scope, identity,
  five source bindings and fresh message sequence/time floors. Existing history
  is excluded, including the previously disclosed unsuccessful batches.
- All runtime AI, automatic confirmation and publication checks honor the actual
  global pause file. Dedicated worker controls cannot override that file.
- Confirmed class/year/course membership or an exact self mention is necessary
  for personal applicability. Explicit action, complete source evidence and
  certain dates are required. Unknown roles, participation intent, eligibility,
  attachment-dependent conditions and negated actions remain pending.
- Formal creation reuses the existing transactional confirmation and audit path.
  Titles come from literal action evidence, not arbitrary model wording. Stable
  source identities prevent duplicate formal events for identical same-day
  reposts; different batches retain separate analysis identities.
- Automatic date verification is attached to hashes of specific committed rows.
  It does not claim human confirmation or carry over to later user edits.
- Scoped publication sends only the existing minimized text source plus its
  committed event/task/deadline records. It reuses receipt, fence, revocation,
  epoch, insert-only conflict handling and protected transport. The old text-only
  protocol still cannot publish formal tasks. Dynamic countdowns do not affect
  persistent publication identity.
- Definite temporary AI failures have at most two total attempts; unknown
  delivery is retained without another paid request. Publication has at most
  twelve combined publish/reconciliation requests per bundle, exponential
  backoff and existing circuit/quota protection. Default scoped HTTP allowance
  is 1,000/day with no artificial token cap. Pending/held/failing workers feed the
  existing health observer; there is no separate monitoring service.

## Incremental validation

66 distinct new Python regression cases have passing evidence across incremental
runs. Thirteen API cases have passing evidence: twelve passed in the main batch;
the router fixture was corrected and that single case passed separately. These
are deduplicated case results, not a claim of a single complete final-revision
suite run. The prior 32-case suite and unrelated project tests were not repeated.

An isolated production-preserving runtime merge passed five affected integration
checks. The mock pipeline handled eight new notices in two four-item analysis
and publication cycles. Thirty-six synthetic old batches remained untouched.
Real network, external process creation and databases outside each test's
temporary directory were blocked. Node uses fake transactions and rejects real
fetches. No real DeepSeek request, production write or deployment was performed.

## Version matching and deployment

There are two deployment targets: the Windows runtime and `eric-app-api`.
The existing Eric OS site needs no frontend deployment for the added records.
The current remote API baseline is version 33, bundle
`3866c970f970ad8c71e5a40036b0d0bbe7701aede9d10b948a6b1b61e00fb823`.
`scripts/prepare-notification-release.py` requires all seventeen reviewed
baseline file hashes and assembles an eighteen-file candidate with narrow route,
validator and date-read changes. It preserves the live Health/Notes modules.
Never replace the live function with the older repository API directory.

The runtime baseline branch differs from online main; the production worktree
also contains unrelated local changes. Deploy a reviewed overlay against exact
production original hashes, preserving those changes. Do not merge the draft or
copy the entire checkout over production. The isolated runtime includes the
existing compatibility preflight/status changes in service.py and health.py.

Deployment and activation require separate explicit approval. With both global
pauses still true, code can be installed inert; this does not restore service.
Activation must not change either pause. It rejects paused, stale or mismatched
proofs and writes the enable file last after all local operational metadata.

If global resumption is separately approved, this order is mandatory:

1. Back up current changed-file bytes, original API v33, control files and scoped
   operational receipts. Preserve the business database; never restore an older
   database over new messages or task history.
2. Stop the desktop parent, all legacy/current AI and cloud consumers. Verify
   process exit and resolve or retain all in-flight/unknown requests. Do not
   release a global pause while any old consumer is still running.
3. Verify installed runtime/API hashes and existing credentials/session epoch.
   Make only the explicitly approved global configuration change. While every
   consumer remains stopped, create a fresh five-source checkpoint and matching
   API activation scope/proof, less than fifteen minutes old. Checkpoint only
   then, so prior backlog remains excluded.
4. Activate the reviewed identity/profile and scoped runtime. Verify all hashes,
   all permissions and the complete enable file. Verify the generic full-sync
   and historical AI consumers are still held by the scoped-release fence.
5. Start the updated existing desktop service, which automatically supervises
   the two workers. Do not start a legacy worker or manually trigger a batch.

If step 2 cannot be guaranteed, activation is No-Go. A true-to-false global
transition before the scoped enable file exists can release legacy work; the
last-file activation rule alone does not close that operational window.

## Real acceptance after approval

Trace fresh real source IDs through collection, batch, job, attempt, saved
provider receipt, candidate, automatic decision, event/task and cloud receipt.
Verify the existing active-events API and UI display that same event without a
duplicate. Observe at least two subsequent established collection/processing
periods (the current collection period is fifteen minutes), with no manual
analysis, worker restart or synchronization trigger. Do not fabricate a personal
task if recent content is uncertain or irrelevant; retain real processing proof
and the isolated formal-creation proof instead. Backlog recovery is separately
approved and is limited to previously unsuccessful messages, in bounded batches.

## Rollback

Stop the affected consumers before changing code or authorization. Revoke/disable
the scoped AI/publication authorization and keep legacy consumers fenced. Restore
the saved exact runtime bytes and API v33 bundle, preserving receipts, original
messages and newly created task/history records. Restore or hold global controls
only as explicitly approved; do not let restored legacy launchers inherit an
active old grant. Restart collection alone until the revised scope is approved.
Never reset dedupe IDs, publication receipts or unknown-outcome attempts.

## Residual limits

Production recovery is unproven until the real acceptance above. Both global
pauses currently remain in force. Windows must be awake in the signed-in user
session with the authorized WeChat source available; prolonged sleep or a signout
cannot be repaired by a worker restart. Missing reliable timestamps, unresolved
attachment qualifications and uncertain personal applicability remain pending.
Unknown AI outcomes or exhausted delivery attempts require a recorded recovery
decision; they do not cause unlimited paid retries. Future deployments must
preserve the scoped routes and recompute matching implementation approvals.
