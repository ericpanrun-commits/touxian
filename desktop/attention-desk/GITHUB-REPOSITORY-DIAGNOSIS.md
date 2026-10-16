# GitHub repository diagnosis

2026-10-17 (Asia/Shanghai). Authenticated account: `ericpanrun-commits`.

## Verified original state

- Local repository: `C:\Users\ericp\Desktop\reference\touxian`.
- Branch: `main`; clean at diagnosis; original HEAD:
  `1e31cd6a90615e6beab63aefc6ccd6aba6c5a6fa`.
- Original remote output:

  ```text
  origin  https://github.com/sssssjw11/touxian.git (fetch)
  origin  https://github.com/sssssjw11/touxian.git (push)
  ```

- Origin owner was `sssssjw11`, repository `touxian`.
- Local CLI authentication, independent of the ChatGPT connector, was verified
  as `ericpanrun-commits`, using the Windows secure credential store.
- GitHub API permissions on that remote: `pull=true`, `push=false`,
  `maintain=false`, `admin=false`.
- The old owner's username came from the earlier user-provided deployment target
  and the existing origin URL. Treating that owner as the user's account was
  incorrect. No third-party account credentials are required or requested.
- The pending login process was cancelled at the user's request. No further
  login flow was started.

## Owned repository discovery

The account's four repositories were listed and their root directories checked:
`LifeLedger`, `IELTS-practice`, `health_analyzer`, and `photo_atlas`.
None contained this project's desktop/Android workspace. The requested own
repository `ericpanrun-commits/touxian` returned HTTP 404 before creation.
The account's existing OAuth authorization includes `repo` and `workflow`.

## Authorized correction

After reporting the diagnosis and intended changes, a new empty **public**
repository was created under the user's account:
<https://github.com/ericpanrun-commits/touxian>.
The GitHub API confirmed `push=true`, `maintain=true`, and `admin=true`.
No repository initialization commit, new license, or replacement history was
created. All existing local commits and upstream attribution are preserved.

The old origin was renamed `upstream`; the new repository is `origin`:

```text
origin    https://github.com/ericpanrun-commits/touxian.git (fetch)
origin    https://github.com/ericpanrun-commits/touxian.git (push)
upstream  https://github.com/sssssjw11/touxian.git (fetch)
upstream  https://github.com/sssssjw11/touxian.git (push)
```

The authenticated account has read-only access to upstream. Production pushes
target only the user's origin. No force push, reset, rebase, or commit deletion
is part of the correction.

## Production safeguards

- Workflow and runtime repository allowlists now name only
  `ericpanrun-commits/touxian`, on `main`.
- The old repository is explicitly covered by the rejected-context regression
  test. Storage identity verification remains mandatory.
- Repository variables: `ZQU_ACTIONS_ENABLED=false`,
  `ZQU_NOTIFICATION_DRY_RUN=true`, and the existing production database ID.
- Environment: `zqu-production`, restricted to the `main` branch.
- `BARK_DEVICE_KEY`: configured YES. `BARK_SERVER_URL`: configured YES.
  Values were read from the ignored local configuration and passed to GitHub's
  secret command through standard input. They were not printed or committed.
- `SUPABASE_DB_URL`: configured NO. No hosted run or Bark acceptance is allowed
  until database configuration is complete.
- No Docker, Fly, paid runner, database resource, or billing modification.
  Public standard GitHub-hosted runners are free:
  <https://docs.github.com/en/billing/concepts/product-billing/github-actions>.

The approved Supabase migration, imported data, notification baseline, dedup
history, local SQLite backups, and earlier test results are unchanged.

Validation after the allowlist correction: full backend suite **962 passed**,
including isolated native PostgreSQL integration, migration, notification and
authority tests. The test server was stopped normally after completion. Secret
audit checked **11,005** files/objects, with **zero findings**. No live Bark was
sent. The two pytest warnings concern an existing dependency deprecation and
an unavailable cache directory; no test failed.
