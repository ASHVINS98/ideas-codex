# Run on GitHub Actions every 15 minutes

The workflow is `.github/workflows/naukri.yml`. It uses a fresh Ubuntu runner for
one cycle, then exits. GitHub schedules the next cycle; no laptop needs to stay on.
Scheduled cycles scan only unless you explicitly enable applications.

## 1. Push the code

The workflows must be committed and pushed to the repository's **default branch**
before scheduled runs work. Publishing a Codex environment does not push Git code.
Use a private repository if you want matching reports and application history to
stay private. This implementation supports one Naukri account per repository.

## 2. Create your profile and export a login session locally

Follow the README installation instructions on a computer with an interactive
browser. Create `profile.json` using your actual details. Then:

```bash
python -m naukri_assistant login
python -m naukri_assistant export-session --output private/naukri-session.json
```

The second command verifies your login and exports Naukri cookies, local storage
and IndexedDB when available. It excludes unrelated third-party domains. It will
not overwrite an existing export; remove the previous export or use a new private
filename when refreshing it. The file grants access to your account: do not commit
it, paste it into chat, or upload it as a workflow artifact.

This is a storage-state export, not a full browser-profile transfer. Session
storage and device-bound authentication are not portable. If Naukri rejects the
session on a hosted runner, or requires an OTP/CAPTCHA, the run fails. Complete the
check normally on your computer and refresh the session; the script does not bypass
site restrictions. A self-hosted runner on a trusted always-on machine is an
alternative if Naukri does not support using the exported session on hosted runners.

## 3. Add repository Actions secrets

In GitHub, open **Settings → Secrets and variables → Actions → Secrets**. Add:

| Secret | Contents |
| --- | --- |
| `NAUKRI_PROFILE_JSON` | Your entire actual `profile.json` file |
| `NAUKRI_RESUME_TEXT` | Your resume as plain text, including projects and experience |
| `NAUKRI_STORAGE_STATE` | The entire exported `private/naukri-session.json` file |

These are secret values, not filenames. The profile's `resume_path` is replaced
with a private runtime text file. No password or personal GitHub access token is
required. The workflow uses GitHub's own short-lived `GITHUB_TOKEN` for its ledger.
GitHub limits each Actions secret's size; a large export might not fit. Do not
work around that by committing the session or including it in artifacts.

## 4. Verify a scan-only run

Open **Actions → Naukri job assistant → Run workflow**, select the default branch,
choose mode **scan**, and run it.

Check the logs and download its `naukri-report-…` artifact to inspect
`reports/latest.json`. Its `jobs` list contains only eligible matches; summary counts
cover all scanned jobs, and rejection reasons remain in the workflow log. Verify
jobs and scores against your real profile before enabling applications. Artifacts contain job matches and skill
keywords, but no raw resume or browser session. Reports expire after seven days.

Once secrets are configured, scheduled scan-only runs occur at minutes
**7, 22, 37 and 52** each hour. GitHub may delay or skip scheduled runs; exact timing
is not guaranteed. Missing or expired secrets cause a clear failure, not a success.
Disable the workflow in GitHub Actions when you want to stop all scheduled checks.
Four runs per hour means up to 96 runs per day and consumes your plan's Actions
minutes and artifact storage.

## 5. Initialize durable application history once

For a new account with **no previous local script applications**, run this workflow
manually in mode **initialize-history**. It creates a separate branch named
`naukri-application-history` with `application-history.json`. Existing history is
never reset by initialization. If branch protection or organization policy blocks
the write, resolve the repository permission issue before attempting applications.

If you already used this script to apply locally, seed the ledger from that
computer instead, so previous pending and confirmed attempts are preserved:

```bash
# Set GITHUB_REPOSITORY to owner/repository and configure GITHUB_TOKEN securely
# with repository Contents read/write access in your local process. Never commit it.
python -m naukri_assistant init-github-history
```

This reads the existing local `.naukri/history.sqlite`. Stop any local scheduler
first, and switch to the GitHub ledger for future runs; local and remote SQLite
histories are not automatically synchronized. Do not initialize an empty cloud
ledger if you have previous local attempts. No token needs to be provided to this
chat, and the hosted workflow does not need a personal token.

History contains numeric job IDs, dates, statuses and fixed diagnostic messages.
It contains no resume or session credentials, but is visible to anyone who can read
the repository. Never delete/reset this branch, edit its ledger, or bypass a failed
history write to restart applications. If initialization fails after creating its
branch, inspect and recover that state rather than deleting possible past attempts.

## 6. Enable applications after scan validation

First, dispatch mode **apply** manually. It attempts up to three qualifying direct
applications, with a total limit of 20 recorded attempts per day in **Asia/Kolkata**.
It fails if the initialized remote history is missing or cannot be read/written.
Check both the workflow log and your Naukri application history.

To enable applications on subsequent scheduled runs, open
**Settings → Secrets and variables → Actions → Variables** and create the repository
variable **`NAUKRI_AUTO_APPLY`** with the exact value **`true`**. Set it to `false` or
remove it to return to scan-only mode. Manual dispatch mode overrides this variable.
This setting does not change the local foreground scheduler.

Before each Apply click, a `pending` attempt must be successfully committed to the
history branch. A runner crash after that commit leaves a pending job that future
runs will not retry. Writes use GitHub file SHAs to reject conflicting runners;
workflow concurrency also prevents normal overlap. If status confirmation fails,
inspect Naukri manually. Do not assume an unconfirmed attempt failed to submit.

The workflow uploads only the matching report. It does not cache or upload the
browser profile, session export, resume or private runtime inputs. Secret files are
removed when the run finishes. Session refreshes cannot be written back to a GitHub
secret by this workflow, so re-export and update `NAUKRI_STORAGE_STATE` when login
expires. The default branch's source files remain unchanged by ledger commits.

## Validation status

The unit tests simulate the GitHub Contents API, including stale-write rejection
and restart recovery. Real Chromium tests exercise private session export/import
on intercepted synthetic pages. These tests do not establish that Naukri currently
accepts a hosted runner's login or that the current live page selectors work.
Only a successful real scan followed by a verified application establishes that.
