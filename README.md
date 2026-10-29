# Naukri resume matching and application assistant

A local Python script that searches Naukri, ranks jobs against your resume and
explicit profile, and optionally attempts direct applications every **15 minutes**.
This is an initial implementation: **Naukri's current UI and real submissions have
not been validated with a logged-in account.** Matching and application-control
tests use synthetic data; they do not prove compatibility with the live website.

**Windows with Docker Desktop:** follow [Docker setup](docs/docker.md) for a local
browser viewer, saved sessions and background automation, without installing Python.

For scheduled cloud runs without keeping your computer on, follow
[GitHub Actions setup](docs/github-actions.md). It starts in scan-only mode and
supports a durable remote application ledger before automatic applications.

## Install

Requires Python 3.10+ and a computer where you can open a browser for initial login.
From this repository:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

On Windows, activate with `.venv\Scripts\activate` instead. Linux machines may need
`python -m playwright install --with-deps chromium`. Alternatively pass
`--browser /path/to/chromium` to browser commands to use an installed Chromium.

## Set up your real profile

Copy `profile.example.json` to **`profile.json`** and replace all example details.
Put your actual resume under **`private/`** and set `resume_path` relative to the
profile file. PDF with selectable text, DOCX, TXT, and Markdown are supported.
Scanned-image PDFs need OCR outside this script. No AI API key is needed.

- `target_roles`: titles to search and match.
- `skills`: your actual, explicitly declared skills used as matching keywords.
- `experience_years`: your real total relevant experience, including fractions.
- `preferred_locations`: accepted locations; `[]` accepts any known location.
- `allow_remote`: accept remote listings when true.
- `exclude_companies` / `exclude_keywords`: hard exclusions.
- `minimum_score`: minimum keyword relevance score, default 75/100.

The script reads this local profile and resume. It does **not** import or modify
your whole Naukri profile. Applications use the details and active resume already
saved in your Naukri account, so update those yourself to match your local profile.
Do not use the synthetic example candidate to apply to real jobs.

## Log in yourself

```bash
python -m naukri_assistant login
```

Sign in normally in the opened browser, complete any OTP/CAPTCHA yourself, then
press Enter in the terminal. The script never asks for or reads your password.
Your reusable session is stored under `.naukri/browser`; treat that directory as a
credential. Session, personal profile, resume, reports and history are Git-ignored.
Use a separate `--state-dir` for each account and keep it unchanged between runs.

## Run once and inspect the match report

```bash
python -m naukri_assistant scan
```

This does not apply. It writes `reports/latest.json` with **only `eligible: true`
jobs**, their scores and matched keywords. The summary counts all scanned jobs;
exclusion and manual-review reasons remain in terminal output. If nothing qualifies,
the JSON contains `"jobs": []`. By default it inspects up to 30
listings across the first search page for each target role. `--pages 2` and
`--max-jobs 50` expand that scope. Results are not an exhaustive survey of Naukri.

For local scans that need manual verification, use `scan --interactive`. Chromium
stays visible and the terminal waits while you complete any available Naukri check
yourself. Press Enter after completion, or Ctrl+C if the page denies access without
offering a check. This mode cannot be combined with `--headless` or used in GitHub
Actions. Background runs still stop when verification is required.

To submit qualifying jobs from a recent report:

```bash
python -m naukri_assistant apply --interactive --limit 3 --daily-limit 20
```

This submits applications; it rechecks each listing before applying and retains
duplicate protection. Keep the visible browser open for any manual security
verification. `--interactive` also works for `apply` and cannot be combined with
`--headless` or used in GitHub Actions. Reports must be less than 24 hours old and
match your current profile/resume. Re-scan after changing those files. Extra
recruiter questionnaires and external application flows still need manual completion.

## Automate every 15 minutes

Start with reports only if you want to evaluate your filters:

```bash
python -m naukri_assistant watch --interval-minutes 15 --headless
```

To automatically attempt applications to qualifying jobs:

```bash
python -m naukri_assistant watch --apply --interval-minutes 15 --limit 3 --daily-limit 20 --headless
```

This is a foreground scheduler. Leave the process and computer running, disable
sleep, and stop with Ctrl+C. No cron task or background service is installed.
Cycles start no more often than every 15 minutes and do not overlap. A slow cycle
delays the next one. `--once` runs a single cycle for initial validation. An expired
login, CAPTCHA, unexpected layout or browser error stops the scheduler with an
error rather than silently claiming success. Rerun `login` when needed.

## Application behavior

- Only above-threshold jobs with a known title, company, location, readable
  description and compatible explicit experience range qualify automatically.
- Listings are fetched and scored again immediately before clicking Apply.
- Only a single unambiguous direct **Apply / Apply Now** button is clicked. External
  application flows and recruiter questionnaires need manual completion. The script
  never invents screening answers or clicks through an unknown questionnaire.
- Only a visible success message counts as a confirmed application. Without it,
  the attempt is marked for manual review and the rest of that cycle is stopped.
- Every attempt is recorded **before** the click. Confirmed, interrupted, uncertain
  and manual-review attempts are not automatically retried, even after a restart.
  Inspect Naukri's own application history before manually retrying an uncertain job.
- The daily cap uses the computer's local calendar day and includes uncertain
  attempts. Do not delete history to restart a scheduler; that removes deduplication.
- A database lock prevents two runners using the same state directory from applying
  simultaneously. Do not run the same account with multiple state directories.

Inspect history with:

```bash
python -m naukri_assistant history
```

Use automation only where Naukri permits it. This tool does not evade bot detection,
CAPTCHAs, rate limits, or site restrictions. Availability of login does not establish
that automated applications are permitted by the site's current terms.

## How the score works

The score is explainable keyword relevance, **not** an employer ATS score or a
guarantee of eligibility:

| Component | Maximum points |
| --- | ---: |
| Fraction of your configured skill keywords present in the listing | 40 |
| Target job title token overlap | 25 |
| Your experience falls inside the advertised range | 20 |
| Meaningful description words also present in your resume | 15 |

Location, excluded terms, excluded employers and incompatible experience are hard
filters. Missing fields require manual review rather than optimistic scoring.
This first version does not interpret salary, visa eligibility, notice periods,
semantic skill equivalence or complex mandatory requirements. Review initial
reports and configure keywords carefully before enabling automatic applications.

## Offline demo and tests

These commands do not sign in, contact Naukri or submit applications:

```bash
python -m naukri_assistant rank --profile examples/profile.json --jobs examples/jobs.json --output reports/demo.json
python -m unittest discover -s tests -v
```

`examples/` contains synthetic jobs and a fake resume solely for testing. Live
automation uses browser DOM and JSON-LD data, not an unofficial application API;
Naukri UI changes can require updates to `naukri_assistant/browser.py`.
