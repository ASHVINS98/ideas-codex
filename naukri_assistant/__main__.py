"""Usage: python -m naukri_assistant --help."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .browser import AccountActionRequired, browser_session
from .history import History
from .github_history import GitHubHistory
from .matching import Job, Profile, rank, score


def save_report(path: Path, profile: Profile, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
               "profile_fingerprint": profile.fingerprint, "jobs": rows}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    for row in rows:
        status = "eligible" if row["eligible"] else "; ".join(row["excluded_reasons"] + row["manual_review_reasons"]) or "below score threshold"
        print(f"{row['score']:6.2f}  {row['job_id']}  {row['job']['title']} / {row['job']['company']} — {status}")
    print(f"Report: {path} ({len(rows)} jobs)", flush=True)


def read_report(path: Path, profile: Profile) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("profile_fingerprint") != profile.fingerprint:
        raise ValueError("Report does not match the current profile and resume. Scan again")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(data["created_at"])).total_seconds()
    if not 0 <= age <= 86400:
        raise ValueError("Report is older than 24 hours or has an invalid timestamp. Scan again")
    # Never trust saved eligibility flags or scores as authorization to submit.
    return rank([Job.from_dict(row["job"]) for row in data["jobs"]], profile)


def scan(browser, profile: Profile, args) -> list[dict]:
    cfg = profile.config
    links = browser.search(cfg["target_roles"], cfg["experience_years"], cfg["preferred_locations"], args.pages)
    if len(links) > args.max_jobs:
        print(f"Inspecting the first {args.max_jobs} of {len(links)} discovered jobs; increase --max-jobs to inspect more.")
    jobs = []
    for link in links[:args.max_jobs]:
        jobs.append(browser.read_job(link))
        time.sleep(1)
    return rank(jobs, profile)


def apply_rows(browser, rows: list[dict], profile: Profile, history: History, limit: int, daily_limit: int):
    attempts = 0
    for row in rows:
        if attempts >= limit or history.today() >= daily_limit:
            print("Application attempt limit reached.", flush=True)
            break
        if not row["eligible"] or history.seen(row["job_id"]):
            continue
        # The listing may have changed since the scan, so re-read and re-score it.
        fresh = browser.read_job(row["job"]["url"])
        verified = score(fresh, profile)
        if fresh.id != row["job_id"] or not verified["eligible"]:
            print(f"Skip {row['job_id']}: fresh listing no longer qualifies.")
            continue
        if browser.already_applied():
            history.reserve(fresh.id)
            history.finish(fresh.id, "previously_applied")
            continue
        button = browser.application_button()
        if button is None:
            print(f"Manual application needed: {fresh.url} (no unambiguous direct Apply button)")
            continue
        # Commit BEFORE clicking. A crash or uncertain response must never trigger a blind retry.
        history.reserve(fresh.id)
        attempts += 1
        try:
            button.click()
            confirmed = browser.confirm_application()
        except Exception:
            history.finish(fresh.id, "uncertain", "Interrupted after Apply; inspect Naukri application history before any manual retry")
            raise
        if confirmed:
            history.finish(fresh.id, "applied")
            print(f"Applied: {fresh.title} / {fresh.company} ({verified['score']})", flush=True)
        else:
            history.finish(fresh.id, "manual_review", "No success confirmation; inspect Naukri application history and complete any questionnaire manually")
            print(f"Application not confirmed; manual review required: {fresh.url}", flush=True)
            # An unexpected application flow should not be repeated across the remaining jobs.
            break
        time.sleep(2)


def run_watch(args, history: History):
    print(f"Checking every {args.interval_minutes} minutes; auto-apply {'ON' if args.apply else 'OFF (reports only)'}. Ctrl+C stops.", flush=True)
    while True:
        started = time.monotonic()
        profile = Profile.load(args.profile)
        with browser_session(args.state_dir, args.headless, args.browser, getattr(args, "storage_state", None)) as browser:
            browser.authenticate()
            rows = scan(browser, profile, args)
            save_report(args.output, profile, rows)
            if args.apply:
                apply_rows(browser, rows, profile, history, args.limit, args.daily_limit)
        if args.once:
            return
        # Minimum 15 minutes between cycle starts; do not run overlapping catch-up jobs.
        remaining = max(0, args.interval_minutes * 60 - (time.monotonic() - started))
        print(f"Next check in {remaining / 60:.1f} minutes. Keep this process and computer running.", flush=True)
        time.sleep(remaining)


def bounded_int(low: int, high: int):
    def parse(value):
        result = int(value)
        if not low <= result <= high:
            raise argparse.ArgumentTypeError(f"must be between {low} and {high}")
        return result
    return parse


def parser():
    root = argparse.ArgumentParser(description="Resume-based Naukri matching and optional scheduled direct applications")
    commands = root.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", type=Path, default=Path("profile.json"))
    common.add_argument("--state-dir", type=Path, default=Path(os.environ.get("NAUKRI_STATE_DIR", ".naukri")), help="Use a different state directory for each account")
    common.add_argument("--browser", default=os.environ.get("NAUKRI_BROWSER"), help="Optional Chromium executable path")
    common.add_argument("--storage-state", type=Path, help="Import a private Playwright session JSON, e.g. on GitHub Actions")
    common.add_argument("--github-history", action="store_true", help="Use durable history via GITHUB_REPOSITORY and GITHUB_TOKEN")
    for name in ("login", "export-session", "init-github-history", "rank", "scan", "apply", "watch", "history"):
        command = commands.add_parser(name, parents=[common])
        if name == "rank":
            command.add_argument("--jobs", type=Path, required=True, help="JSON array of job objects; no browser required")
        if name in {"rank", "scan", "watch"}:
            command.add_argument("--output", type=Path, default=Path("reports/latest.json"))
        if name == "export-session":
            command.add_argument("--output", type=Path, default=Path("private/naukri-session.json"))
        if name in {"scan", "apply", "watch", "export-session"}:
            command.add_argument("--headless", action="store_true")
        if name == "scan":
            command.add_argument("--interactive", action="store_true", help="Keep a visible browser open for manual login/security checks; scan only")
        if name in {"scan", "watch"}:
            command.add_argument("--pages", type=bounded_int(1, 3), default=1, help="Search pages per role")
            command.add_argument("--max-jobs", type=bounded_int(1, 100), default=30)
        if name in {"apply", "watch"}:
            command.add_argument("--limit", type=bounded_int(1, 20), default=3, help="Maximum attempts per cycle")
            command.add_argument("--daily-limit", type=bounded_int(1, 100), default=20, help="Maximum recorded attempts per local calendar day")
        if name == "apply":
            command.add_argument("--report", type=Path, default=Path("reports/latest.json"))
        if name == "watch":
            command.add_argument("--apply", action="store_true", help="Submit qualifying direct applications; omit for reports only")
            command.add_argument("--interval-minutes", type=bounded_int(15, 1440), default=15)
            command.add_argument("--once", action="store_true", help="Run one cycle and exit")
    return root


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    history = None
    try:
        if getattr(args, "interactive", False) and (args.headless or os.environ.get("GITHUB_ACTIONS") == "true"):
            raise ValueError("Interactive scans require a visible local browser; omit --headless and run outside GitHub Actions")
        if args.command == "rank":
            profile = Profile.load(args.profile)
            jobs = [Job.from_dict(item) for item in json.loads(args.jobs.read_text(encoding="utf-8"))]
            save_report(args.output, profile, rank(jobs, profile))
            return 0
        if args.command == "init-github-history":
            # Seed from local history when this command is run on an existing local installation.
            with_existing = History(args.state_dir / "history.sqlite")
            try:
                with with_existing.exclusive():
                    GitHubHistory.initialize(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", ""), with_existing.rows())
            finally:
                with_existing.close()
            print("GitHub history initialized on its dedicated branch; existing history was preserved.")
            return 0
        submitting = args.command == "apply" or args.command == "watch" and args.apply
        if os.environ.get("GITHUB_ACTIONS") == "true" and submitting and not args.github_history:
            raise ValueError("GitHub Actions applications require --github-history; temporary runner storage is not durable")
        history = (GitHubHistory(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", ""))
                   if args.github_history else History(args.state_dir / "history.sqlite"))
        if args.command == "history":
            for row in history.rows():
                print(" | ".join(row))
            return 0
        with history.exclusive():
            if args.command == "watch":
                run_watch(args, history)
            elif args.command == "login":
                with browser_session(args.state_dir, executable=args.browser) as browser:
                    browser.login()
                    print("Logged-in session saved locally. Do not share the session directory.")
            elif args.command == "export-session":
                with browser_session(args.state_dir, args.headless, args.browser) as browser:
                    browser.export_session(args.output)
                    print(f"Private session exported to {args.output}. Store it as a GitHub Actions secret, never as a repository file.")
            else:
                profile = Profile.load(args.profile)
                with browser_session(args.state_dir, args.headless, args.browser, args.storage_state, interactive=getattr(args, "interactive", False)) as browser:
                    browser.authenticate()
                    if args.command == "scan":
                        save_report(args.output, profile, scan(browser, profile, args))
                    else:
                        rows = read_report(args.report, profile)
                        apply_rows(browser, rows, profile, history, args.limit, args.daily_limit)
        return 0
    except KeyboardInterrupt:
        print("\nStopped. Existing submission attempts remain in history.")
        return 130
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, AccountActionRequired, RuntimeError) as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Playwright failures must stop the scheduler, not silently repeat submissions.
        print(f"Browser operation failed ({type(error).__name__}). Check your browser installation, network access, and saved login; no automatic retry was scheduled.", file=sys.stderr)
        return 1
    finally:
        if history:
            history.close()


if __name__ == "__main__":
    def stop_on_signal(_signum, _frame):
        # Docker stop sends SIGTERM: unwind browser contexts and release profile
        # locks before the container exits, preserving the reusable session.
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_on_signal)
    raise SystemExit(main())
