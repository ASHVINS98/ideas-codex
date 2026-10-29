import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch, MagicMock
from argparse import Namespace

from naukri_assistant.__main__ import apply_rows, main, read_report, run_watch
from naukri_assistant.browser import experience_range, parse_job
from naukri_assistant.history import History
from naukri_assistant.matching import Job, Profile, contains, job_url, rank, score


ROOT = Path(__file__).resolve().parents[1]


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.profile = Profile.load(ROOT / "examples/profile.json")
        self.job = Job.from_dict(json.loads((ROOT / "examples/jobs.json").read_text())[0])
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_matching_and_experience_filter(self):
        jobs = [Job.from_dict(item) for item in json.loads((ROOT / "examples/jobs.json").read_text())]
        rows = rank(jobs, self.profile)
        self.assertTrue(rows[0]["eligible"])
        self.assertGreaterEqual(rows[0]["score"], 75)
        self.assertFalse(rows[1]["eligible"])
        self.assertIn("Experience outside advertised range", rows[1]["excluded_reasons"])
        self.assertEqual(len(rank(jobs + jobs, self.profile)), 2)

    def test_missing_experience_requires_review(self):
        row = score(replace(self.job, min_experience=None), self.profile)
        self.assertFalse(row["eligible"])
        self.assertIn("Experience range unavailable", row["manual_review_reasons"])

    def test_location_alias_and_rejection(self):
        self.assertTrue(score(replace(self.job, location="Bangalore"), self.profile)["eligible"])
        self.assertFalse(score(replace(self.job, location="Pune"), self.profile)["eligible"])
        self.assertTrue(score(replace(self.job, location="Remote"), self.profile)["eligible"])

    def test_skill_boundaries(self):
        self.assertFalse(contains("JavaScript and C++", "Java"))
        self.assertFalse(contains("C++", "C"))
        self.assertTrue(contains("C++ and PostgreSQL", "C++"))
        self.assertTrue(contains("PostgreSQL", "Postgres"))

    def test_url_validation(self):
        for url in ["https://evil.test/job-listings-python-010126000001", "http://www.naukri.com/job-listings-python-010126000001", "https://www.naukri.com.evil.test/job-listings-python-010126000001", "https://user:pass@www.naukri.com/job-listings-python-010126000001", "https://www.naukri.com/nlogin/login"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                job_url(url)
        self.assertEqual(job_url(self.job.url + "?tracking=example"), self.job.url)

    def test_jsonld_and_dom_fallback(self):
        posting = {"@graph": [{"@type": "JobPosting", "title": "Python Developer", "description": "<p>Python &amp; Django</p>", "hiringOrganization": {"name": "Example"}, "jobLocation": {"address": {"addressLocality": "Bengaluru"}}, "experienceRequirements": "2 to 5 years"}]}
        job = parse_job({"scripts": ["bad json", json.dumps(posting)]}, self.job.url)
        self.assertEqual(job.company, "Example")
        self.assertEqual(job.description, "Python & Django")
        self.assertEqual((job.min_experience, job.max_experience), (2, 5))
        self.assertEqual(experience_range("2–5 Yrs"), (2, 5))
        self.assertEqual(experience_range("3 years minimum"), (None, None))

    def test_real_command_offline_report(self):
        path = Path(self.temp.name) / "report.json"
        result = main(["rank", "--profile", str(ROOT / "examples/profile.json"), "--jobs", str(ROOT / "examples/jobs.json"), "--output", str(path)])
        self.assertEqual(result, 0)
        self.assertEqual(len(read_report(path, self.profile)), 2)
        payload = json.loads(path.read_text())
        payload["profile_fingerprint"] = "changed"
        path.write_text(json.dumps(payload))
        with self.assertRaises(ValueError):
            read_report(path, self.profile)

    def history(self):
        history = History(Path(self.temp.name) / "history.sqlite")
        self.addCleanup(history.close)
        return history

    def fake_browser(self, confirmed=True, job=None):
        class Button:
            clicks = 0
            def click(inner):
                inner.clicks += 1
        class Browser:
            button = Button()
            def read_job(inner, url):
                return job or self.job
            def already_applied(inner):
                return False
            def application_button(inner):
                return inner.button
            def confirm_application(inner):
                return confirmed
        return Browser()

    @patch("naukri_assistant.__main__.time.sleep")
    def test_confirmed_apply_is_not_repeated(self, _sleep):
        history, browser = self.history(), self.fake_browser()
        rows = rank([self.job], self.profile)
        with history.exclusive():
            apply_rows(browser, rows, self.profile, history, 3, 20)
            apply_rows(browser, rows, self.profile, history, 3, 20)
        self.assertEqual(browser.button.clicks, 1)
        self.assertEqual(history.rows()[0][2], "applied")

    @patch("naukri_assistant.__main__.time.sleep")
    def test_unconfirmed_attempt_is_not_repeated(self, _sleep):
        history, browser = self.history(), self.fake_browser(confirmed=False)
        rows = rank([self.job], self.profile)
        apply_rows(browser, rows, self.profile, history, 3, 20)
        apply_rows(browser, rows, self.profile, history, 3, 20)
        self.assertEqual(browser.button.clicks, 1)
        self.assertEqual(history.rows()[0][2], "manual_review")

    @patch("naukri_assistant.__main__.time.sleep")
    def test_changed_listing_is_not_submitted(self, _sleep):
        history = self.history()
        browser = self.fake_browser(job=replace(self.job, min_experience=8, max_experience=12))
        apply_rows(browser, rank([self.job], self.profile), self.profile, history, 3, 20)
        self.assertEqual(browser.button.clicks, 0)
        self.assertEqual(history.today(), 0)

    def test_daily_limit_includes_uncertain_attempts(self):
        history, browser = self.history(), self.fake_browser()
        history.reserve("different-job")
        apply_rows(browser, rank([self.job], self.profile), self.profile, history, 3, 1)
        self.assertEqual(browser.button.clicks, 0)

    def test_exclusive_lock_blocks_parallel_runner(self):
        history = self.history()
        other = History(Path(self.temp.name) / "history.sqlite")
        self.addCleanup(other.close)
        import sqlite3
        with history.exclusive():
            with self.assertRaises(sqlite3.OperationalError):
                with other.exclusive():
                    self.fail("second scheduler acquired the lock")

    @patch("naukri_assistant.__main__.time.sleep", side_effect=KeyboardInterrupt)
    @patch("naukri_assistant.__main__.time.monotonic", side_effect=[0, 30])
    @patch("naukri_assistant.__main__.save_report")
    @patch("naukri_assistant.__main__.scan", return_value=[])
    @patch("naukri_assistant.__main__.browser_session")
    def test_scheduler_waits_remainder_of_15_minutes(self, session, scan, save, clock, sleep):
        args = Namespace(interval_minutes=15, apply=False, profile=ROOT / "examples/profile.json", state_dir=Path(self.temp.name), headless=True, browser=None, output=Path(self.temp.name) / "report.json", once=False)
        with self.assertRaises(KeyboardInterrupt):
            run_watch(args, self.history())
        sleep.assert_called_once_with(870)
        session.return_value.__enter__.return_value.authenticate.assert_called_once()

    @patch("naukri_assistant.__main__.time.sleep")
    def test_submission_exception_leaves_non_retryable_record(self, _sleep):
        history, browser = self.history(), self.fake_browser()
        browser.button.click = MagicMock(side_effect=RuntimeError("interrupted"))
        with self.assertRaises(RuntimeError):
            apply_rows(browser, rank([self.job], self.profile), self.profile, history, 3, 20)
        self.assertTrue(history.seen(self.job.id))
        self.assertEqual(history.rows()[0][2], "uncertain")


if __name__ == "__main__":
    unittest.main()
