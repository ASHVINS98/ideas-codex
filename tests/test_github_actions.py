import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from naukri_assistant import actions
from naukri_assistant.__main__ import apply_rows, main
from naukri_assistant.browser import validate_storage_state
from naukri_assistant.github_history import BRANCH, FILE, GitHubHistory
from naukri_assistant.matching import Job, Profile, rank


ROOT = Path(__file__).resolve().parents[1]
STATE = {"cookies": [{"name": "fixture", "value": "synthetic-session", "domain": ".naukri.com", "path": "/", "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax"}], "origins": []}


class LedgerServer:
    """In-memory GitHub Contents API with real SHA compare-and-swap semantics."""
    def __init__(self):
        self.exists = False
        self.payload = None
        self.sha = "0"

    def request(self, method, path, data=None, missing_ok=False):
        if method == "GET" and path == "":
            return {"default_branch": "main"}
        if method == "GET" and path == "/git/ref/heads/main":
            return {"object": {"sha": "source-head"}}
        if method == "GET" and path == "/git/ref/heads/" + BRANCH:
            return {"object": {"sha": "history-head"}} if self.exists else None
        if method == "POST" and path == "/git/refs":
            self.exists = True
            return {}
        if method == "GET" and path == f"/contents/{FILE}?ref={BRANCH}":
            return {"sha": self.sha, "content": base64.b64encode(json.dumps(self.payload).encode()).decode()} if self.payload is not None else None
        if method == "PUT" and path == "/contents/" + FILE:
            if data["branch"] != BRANCH or (self.payload is not None and data.get("sha") != self.sha):
                raise RuntimeError("Conflicting history SHA")
            self.payload = json.loads(base64.b64decode(data["content"]))
            self.sha = str(int(self.sha) + 1)
            return {"content": {"sha": self.sha}}
        raise AssertionError((method, path))


class GitHubHistoryTests(unittest.TestCase):
    def setUp(self):
        self.server = LedgerServer()
        patcher = patch("naukri_assistant.github_history.GitHubAPI.request", side_effect=self.server.request)
        self.api = patcher.start()
        self.addCleanup(patcher.stop)

    def new(self):
        return GitHubHistory("example/repository", "synthetic-token")

    def test_history_must_be_initialized_explicitly(self):
        with self.assertRaises(RuntimeError):
            self.new()
        self.assertIsNone(self.server.payload)

    def test_pending_attempt_survives_fresh_runner_and_daily_cap(self):
        GitHubHistory.initialize("example/repository", "synthetic-token")
        first = self.new()
        first.reserve("010126000001")
        second = self.new()  # Simulates a crash before confirmation, then a fresh runner.
        self.assertTrue(second.seen("010126000001"))
        self.assertEqual(second.today(), 1)
        self.assertEqual(second.rows()[0][2], "pending")
        second.finish("010126000001", "applied")
        self.assertEqual(self.new().rows()[0][2], "applied")

    def test_initialization_preserves_local_attempts_and_cannot_reset(self):
        GitHubHistory.initialize("example/repository", "synthetic-token", [("010126000001", "2026-01-01", "uncertain", "")])
        self.assertTrue(self.new().seen("010126000001"))
        with self.assertRaises(RuntimeError):
            GitHubHistory.initialize("example/repository", "synthetic-token")
        self.assertTrue(self.new().seen("010126000001"))

    def test_stale_remote_writer_never_clicks_apply(self):
        GitHubHistory.initialize("example/repository", "synthetic-token")
        first, stale = self.new(), self.new()
        first.reserve("010126000002")
        job = Job.from_dict(json.loads((ROOT / "examples/jobs.json").read_text())[0])
        profile = Profile.load(ROOT / "examples/profile.json")
        browser = MagicMock()
        browser.read_job.return_value = job
        browser.already_applied.return_value = False
        with self.assertRaises(RuntimeError):
            apply_rows(browser, rank([job], profile), profile, stale, 3, 20)
        browser.application_button.return_value.click.assert_not_called()
        self.assertNotIn(job.id, self.server.payload["attempts"])

    def test_ci_rejects_ephemeral_history_for_submissions(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with patch("naukri_assistant.__main__.browser_session") as session:
                self.assertEqual(main(["watch", "--once", "--apply"]), 1)
                session.assert_not_called()


class ActionsInputTests(unittest.TestCase):
    def secrets(self):
        return {"NAUKRI_PROFILE_JSON": (ROOT / "examples/profile.json").read_text(),
                "NAUKRI_RESUME_TEXT": (ROOT / "examples/resume.txt").read_text(),
                "NAUKRI_STORAGE_STATE": json.dumps(STATE)}

    def test_private_input_materialization_and_path_override(self):
        secrets = self.secrets()
        profile = json.loads(secrets["NAUKRI_PROFILE_JSON"])
        profile["resume_path"] = "../../outside.txt"
        secrets["NAUKRI_PROFILE_JSON"] = json.dumps(profile)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, secrets):
            path, session = actions.prepare_inputs(Path(directory) / "inputs")
            self.assertEqual(json.loads(path.read_text())["resume_path"], "resume.txt")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(session.stat().st_mode & 0o777, 0o600)
            self.assertEqual(Profile.load(path).config["experience_years"], 3)

    def test_missing_and_malformed_secrets_do_not_expose_values(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "NAUKRI_PROFILE_JSON"):
                actions.prepare_inputs(Path(directory))
        secrets = self.secrets()
        secrets["NAUKRI_STORAGE_STATE"] = "do-not-log-this-secret"
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, secrets):
            with self.assertRaises(ValueError) as error:
                actions.prepare_inputs(Path(directory))
            self.assertNotIn("do-not-log-this-secret", str(error.exception))

    def test_storage_import_rejects_foreign_origins(self):
        for state in [{"cookies": [{"domain": ".evil.test"}], "origins": []},
                      {"cookies": [], "origins": [{"origin": "https://naukri.com.evil.test"}]},
                      {"cookies": [], "origins": [{"origin": "http://www.naukri.com"}]},
                      {"cookies": [], "origins": []}]:
            with self.subTest(state=state), self.assertRaises(ValueError):
                validate_storage_state(state)

    def test_scheduled_run_cannot_initialize_history(self):
        with patch.dict(os.environ, {"NAUKRI_RUN_MODE": "initialize-history", "GITHUB_EVENT_NAME": "schedule"}):
            with self.assertRaises(ValueError):
                actions.run()

    def test_ci_scan_default_and_cleanup_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                with patch.dict(os.environ, self.secrets() | {"NAUKRI_RUN_MODE": "scan"}):
                    with patch("naukri_assistant.actions.main", return_value=1) as command:
                        self.assertEqual(actions.run(), 1)
                        self.assertNotIn("--apply", command.call_args.args[0])
                        self.assertIn("--once", command.call_args.args[0])
                        self.assertFalse(list(Path(".naukri/actions/inputs").glob("*.json")))
                        self.assertFalse(Path(".naukri/actions/inputs/resume.txt").exists())
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
