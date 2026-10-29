"""GitHub Actions entry point. Secrets are consumed through environment variables."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .__main__ import main
from .browser import validate_storage_state
from .matching import Profile


def prepare_inputs(directory: Path) -> tuple[Path, Path]:
    required = ("NAUKRI_PROFILE_JSON", "NAUKRI_RESUME_TEXT", "NAUKRI_STORAGE_STATE")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError("Configure these repository Actions secrets: " + ", ".join(missing))
    # Never include malformed secret contents or decoder errors in logs.
    try:
        profile = json.loads(os.environ["NAUKRI_PROFILE_JSON"])
        storage = validate_storage_state(json.loads(os.environ["NAUKRI_STORAGE_STATE"]))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Profile or session secret is invalid; re-export the session and check profile JSON") from None
    if not isinstance(profile, dict):
        raise ValueError("NAUKRI_PROFILE_JSON must contain a JSON object")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The configured resume path must never redirect writes outside the private directory.
    profile["resume_path"] = "resume.txt"
    paths = (directory / "profile.json", directory / "resume.txt", directory / "session.json")
    for path, text in zip(paths, (json.dumps(profile), os.environ["NAUKRI_RESUME_TEXT"], json.dumps(storage))):
        with open(path, "x", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
            stream.write(text)
    Profile.load(paths[0])
    return paths[0], paths[2]


def run():
    os.umask(0o077)
    mode = os.environ.get("NAUKRI_RUN_MODE", "scan")
    if mode not in {"scan", "apply", "initialize-history"}:
        raise ValueError("Unsupported workflow mode")
    state = Path(".naukri/actions")
    if mode == "initialize-history":
        # Only an explicit manual dispatch may initialize an empty cloud ledger.
        if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
            raise ValueError("History initialization requires a manual workflow dispatch")
        return main(["init-github-history", "--state-dir", str(state)])
    inputs = state / "inputs"
    try:
        profile, session = prepare_inputs(inputs)
        command = ["watch", "--once", "--headless", "--state-dir", str(state),
                   "--profile", str(profile), "--storage-state", str(session),
                   "--output", "reports/latest.json", "--max-jobs", "30"]
        if mode == "apply":
            command += ["--apply", "--github-history", "--limit", "3", "--daily-limit", "20"]
        return main(command)
    finally:
        # Only these known secret files are removed; do not upload the input directory.
        for name in ("profile.json", "resume.txt", "session.json"):
            (inputs / name).unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (ValueError, OSError) as error:
        print(f"Workflow setup stopped: {error}")
        raise SystemExit(1)
