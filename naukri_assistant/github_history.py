"""Durable attempt ledger on a dedicated GitHub branch, committed before Apply.

Uses the short-lived workflow GITHUB_TOKEN. No browser session or resume is stored
in the ledger. GitHub file SHAs provide optimistic locking across fresh runners.
"""

from __future__ import annotations

import base64
import json
import re
from contextlib import contextmanager
from datetime import date, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


BRANCH = "naukri-application-history"
FILE = "application-history.json"
STATUSES = {"pending", "applied", "previously_applied", "manual_review", "uncertain"}


class GitHubAPI:
    def __init__(self, repository: str, token: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not token:
            raise ValueError("GitHub history needs GITHUB_REPOSITORY and GITHUB_TOKEN")
        self.repository = repository
        self.token = token

    def request(self, method: str, path: str, data=None, missing_ok=False):
        request = Request("https://api.github.com/repos/" + self.repository + path,
                          data=json.dumps(data).encode() if data is not None else None,
                          headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                                   "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28",
                                   "User-Agent": "naukri-assistant"}, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code == 404 and missing_ok:
                return None
            raise RuntimeError(f"GitHub history request failed (HTTP {error.code}); no automatic retry. Check repository permissions or a conflicting runner") from None
        except (URLError, TimeoutError):
            raise RuntimeError("GitHub history is unavailable; applications stopped to preserve duplicate protection") from None


class GitHubHistory:
    def __init__(self, repository: str, token: str):
        self.api = GitHubAPI(repository, token)
        result = self.api.request("GET", f"/contents/{FILE}?ref={BRANCH}", missing_ok=True)
        if result is None:
            raise RuntimeError("GitHub application history is missing. Initialize it once before enabling applications; never reset existing history")
        payload = json.loads(base64.b64decode(result["content"]))
        self.validate(payload)
        self.payload = payload
        self.sha = result["sha"]

    @staticmethod
    def validate(payload):
        if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("attempts"), dict):
            raise ValueError("Invalid GitHub application history; refusing to replace it")
        for job_id, record in payload["attempts"].items():
            if not re.fullmatch(r"\d{10,20}", job_id) or not isinstance(record, dict) or record.get("status") not in STATUSES or not isinstance(record.get("detail"), str):
                raise ValueError("Invalid GitHub application history record")
            date.fromisoformat(record["day"])

    @classmethod
    def initialize(cls, repository: str, token: str, seed_rows=()):
        api = GitHubAPI(repository, token)
        if api.request("GET", f"/git/ref/heads/{BRANCH}", missing_ok=True) is not None:
            raise RuntimeError("History branch already exists; initialization will not overwrite it")
        payload = {"version": 1, "attempts": {job_id: {"day": day, "status": status, "detail": detail} for job_id, day, status, detail in seed_rows}}
        cls.validate(payload)
        default_branch = api.request("GET", "")["default_branch"]
        head = api.request("GET", "/git/ref/heads/" + quote(default_branch, safe="/"))["object"]["sha"]
        api.request("POST", "/git/refs", {"ref": "refs/heads/" + BRANCH, "sha": head})
        api.request("PUT", "/contents/" + FILE,
                    {"message": "Initialize Naukri application history", "branch": BRANCH,
                     "content": base64.b64encode(json.dumps(payload).encode()).decode()})

    @contextmanager
    def exclusive(self):
        # Every update includes the previous SHA. A concurrent stale writer fails
        # before it can click Apply; workflow concurrency prevents normal overlap.
        yield

    def save(self):
        self.validate(self.payload)
        result = self.api.request("PUT", "/contents/" + FILE,
                                  {"message": "Update Naukri application history", "branch": BRANCH,
                                   "sha": self.sha, "content": base64.b64encode(json.dumps(self.payload).encode()).decode()})
        self.sha = result["content"]["sha"]

    def seen(self, job_id: str) -> bool:
        return job_id in self.payload["attempts"]

    def today(self) -> int:
        today = datetime.now().astimezone().date().isoformat()
        return sum(record["day"] == today for record in self.payload["attempts"].values())

    def reserve(self, job_id: str):
        if self.seen(job_id):
            raise ValueError("Job already recorded in GitHub history")
        self.payload["attempts"][job_id] = {"day": datetime.now().astimezone().date().isoformat(), "status": "pending", "detail": ""}
        self.save()  # A successful remote commit is required BEFORE the Apply click.

    def finish(self, job_id: str, status: str, detail: str = ""):
        self.payload["attempts"][job_id].update(status=status, detail=detail)
        self.save()

    def rows(self):
        return [(job_id, row["day"], row["status"], row["detail"]) for job_id, row in self.payload["attempts"].items()]

    def close(self):
        pass
