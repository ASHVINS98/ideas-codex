"""Real Chromium against intercepted synthetic pages; never contacts Naukri."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from naukri_assistant.browser import AccountActionRequired, browser_session


URL = "https://www.naukri.com/job-listings-python-developer-example-010126000001"
POSTING = {
    "@type": "JobPosting", "title": "Python Developer",
    "hiringOrganization": {"name": "Synthetic Company"},
    "jobLocation": {"address": {"addressLocality": "Bengaluru"}},
    "description": "Python and Django backend development with SQL, REST API and Docker. Build reliable production services and automated tests.",
    "experienceRequirements": "2-5 years",
}
HTML = f'''<!doctype html><html><body>
<a href="https://www.naukri.com/mnjuser/profile">View profile</a>
<a href="{URL}">Python Developer</a>
<h1>Python Developer</h1>
<script type="application/ld+json">{json.dumps(POSTING)}</script>
<button onclick="this.remove(); document.getElementById('result').textContent='You have successfully applied to this job'">Apply</button>
<p id="result"></p></body></html>'''


class BrowserSmokeTests(unittest.TestCase):
    def test_login_detection_search_extraction_and_apply_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary:
            with browser_session(Path(temporary), headless=True, executable=shutil.which("chromium")) as browser:
                browser.context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=HTML))
                browser.authenticate()
                links = browser.search(["Python Developer"], 3, ["Bengaluru"], 1)
                self.assertEqual(links, [URL])
                job = browser.read_job(URL)
                self.assertEqual(job.company, "Synthetic Company")
                self.assertEqual((job.min_experience, job.max_experience), (2, 5))
                browser.application_button().click()
                self.assertTrue(browser.confirm_application())
                browser.page.set_content("<body>Verify you are human</body>")
                with patch("builtins.input") as prompt:
                    with self.assertRaises(AccountActionRequired):
                        browser.guard()
                    prompt.assert_not_called()

    def test_interactive_verification_keeps_page_open_and_resumes_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            with browser_session(Path(temporary), headless=True, executable=shutil.which("chromium")) as browser:
                # Test a manual action against intercepted pages; no real CAPTCHA
                # is solved and no external page is requested.
                browser.interactive = True
                completed = False
                def respond(route):
                    challenged = "python-developer-jobs" in route.request.url and not completed
                    route.fulfill(status=200, content_type="text/html", body="<body>Verify you are human</body>" if challenged else HTML)
                browser.context.route("**/*", respond)
                browser.authenticate()
                def complete_check(_message):
                    nonlocal completed
                    self.assertFalse(browser.page.is_closed())
                    self.assertIn("Verify you are human", browser.page.locator("body").inner_text())
                    completed = True
                    # A real login can redirect away from the requested search.
                    browser.page.goto("https://www.naukri.com/mnjuser/homepage")
                    return ""
                with patch("builtins.input", side_effect=complete_check) as prompt:
                    links = browser.search(["Python Developer"], 3, ["Bengaluru"], 1)
                    self.assertEqual(links, [URL])
                    prompt.assert_called_once()
                    self.assertIn("python-developer-jobs", browser.page.url)

    def test_interactive_check_must_clear_before_scan_can_continue(self):
        with tempfile.TemporaryDirectory() as temporary:
            with browser_session(Path(temporary), headless=True, executable=shutil.which("chromium")) as browser:
                browser.context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=HTML))
                browser.authenticate()
                browser.interactive = True
                for text in ("Verify you are human", "Access Denied"):
                    with self.subTest(text=text):
                        browser.page.set_content(f"<body>{text}</body>")
                        with patch("builtins.input", return_value="") as prompt:
                            with self.assertRaisesRegex(AccountActionRequired, "still required"):
                                browser.guard()
                            prompt.assert_called_once()

    def test_interactive_launch_rejects_hidden_browser(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "visible browser"):
                with browser_session(Path(temporary), headless=True, interactive=True):
                    self.fail("An invisible browser cannot be used for manual checks")

    def test_session_export_and_import_on_fresh_browser(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = directory / "private/session.json"
            executable = shutil.which("chromium")
            with browser_session(directory / "first", headless=True, executable=executable) as browser:
                browser.context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=HTML))
                browser.authenticate()
                browser.context.add_cookies([
                    {"name": "fixture", "value": "synthetic-only", "domain": ".naukri.com", "path": "/"},
                    {"name": "unrelated", "value": "not-exported", "domain": ".example.org", "path": "/"},
                ])
                browser.page.evaluate("localStorage.setItem('fixture', 'synthetic-local-storage')")
                browser.export_session(output)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertNotIn("not-exported", output.read_text())
            with browser_session(directory / "fresh", headless=True, executable=executable, storage_state=output) as browser:
                browser.context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=HTML))
                browser.authenticate()
                self.assertEqual(browser.context.cookies()[0]["value"], "synthetic-only")
                self.assertEqual(browser.page.evaluate("localStorage.getItem('fixture')"), "synthetic-local-storage")


if __name__ == "__main__":
    unittest.main()
