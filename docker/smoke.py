"""Synthetic Docker integration test. No real login or external requests."""

import http.client
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from naukri_assistant.browser import browser_session
from naukri_assistant.history import History


def check_viewer():
    deadline = time.monotonic() + 15
    while True:
        try:
            connection = http.client.HTTPConnection("127.0.0.1", 6080, timeout=2)
            connection.request("GET", "/vnc.html")
            assert connection.getresponse().status == 200
            connection.close()
            break
        except (OSError, AssertionError):
            if time.monotonic() >= deadline:
                raise RuntimeError("Local viewer did not become ready") from None
            time.sleep(0.25)
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True, executable_path="/usr/bin/chromium")
        try:
            page = browser.new_page()
            page.goto("http://127.0.0.1:6080/vnc.html")
            page.evaluate("""async password => {
                const { default: RFB } = await import('/core/rfb.js');
                const target = document.createElement('div');
                document.body.appendChild(target);
                const rfb = new RFB(target, 'ws://127.0.0.1:6080/websockify', {credentials: {password}});
                await new Promise((resolve, reject) => {
                    const timer = setTimeout(() => reject(new Error('Viewer connection timed out')), 12000);
                    rfb.addEventListener('connect', () => { clearTimeout(timer); resolve(); });
                    rfb.addEventListener('securityfailure', () => { clearTimeout(timer); reject(new Error('Viewer authentication failed')); });
                });
                rfb.disconnect();
            }""", os.environ["VNC_PASSWORD"])
        finally:
            browser.close()
    print("Password-authenticated viewer connection passed")


def check_persistence(phase):
    state = Path("/state/docker-smoke")
    state.mkdir(exist_ok=True)
    history = History(state / "history.sqlite")
    try:
        with browser_session(state, headless=False, executable="/usr/bin/chromium") as browser:
            # Intercept every request: the synthetic Naukri page is never fetched.
            browser.context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body='<a href="/mnjuser/profile">View profile</a><h1>Synthetic Docker test</h1>'))
            browser.authenticate()
            if phase == "seed":
                browser.context.add_cookies([{"name": "docker_fixture", "value": "synthetic", "domain": ".naukri.com", "path": "/", "expires": time.time() + 3600}])
                browser.page.evaluate("localStorage.setItem('docker_fixture', 'synthetic')")
                history.reserve("010126000001")
                history.finish("010126000001", "applied")
            else:
                assert any(cookie["name"] == "docker_fixture" for cookie in browser.context.cookies())
                assert browser.page.evaluate("localStorage.getItem('docker_fixture')") == "synthetic"
                assert history.seen("010126000001")
                assert history.rows()[0][2] == "applied"
        assert not os.path.lexists(state / "browser/SingletonLock")
    finally:
        history.close()
    print(f"Headed browser and persistent session/history phase {phase} passed")


if __name__ == "__main__":
    phase = sys.argv[1]
    if phase not in {"seed", "verify"}:
        raise SystemExit("Expected seed or verify")
    check_viewer()
    check_persistence(phase)
