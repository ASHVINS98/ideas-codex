"""Visible-browser automation. Selectors need validation against a real account."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from html import unescape
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from .matching import Job, job_url


class AccountActionRequired(RuntimeError):
    pass


def validate_storage_state(state: dict) -> dict:
    """Restrict imported/exported browser credentials to Naukri origins."""
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list) or not isinstance(state.get("origins"), list):
        raise ValueError("Invalid browser session: expected Playwright cookies and origins")
    def allowed(host):
        return isinstance(host, str) and (host == "naukri.com" or host.endswith(".naukri.com"))
    for cookie in state["cookies"]:
        if not isinstance(cookie, dict) or not allowed(str(cookie.get("domain", "")).lstrip(".")):
            raise ValueError("Browser session contains cookies outside Naukri")
    for origin in state["origins"]:
        if not isinstance(origin, dict):
            raise ValueError("Invalid browser session origin")
        parsed = urlsplit(origin.get("origin", ""))
        if parsed.scheme != "https" or not allowed(parsed.hostname) or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("Browser session contains an origin outside HTTPS Naukri")
    if not state["cookies"] and not state["origins"]:
        raise ValueError("Browser session is empty; log in before exporting")
    return state


def strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", " ", value))).strip()


def experience_range(value: str) -> tuple[float | None, float | None]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:yrs?|years?)\b", value, re.I)
    if match:
        return float(match[1]), float(match[2])
    return None, None


def parse_job(data: dict, url: str) -> Job:
    posting = {}
    def visit(value):
        nonlocal posting
        if isinstance(value, dict):
            kind = value.get("@type", [])
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                posting = value
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for script in data.get("scripts", []):
        try:
            visit(json.loads(script))
        except (ValueError, TypeError):
            continue
    organization = posting.get("hiringOrganization") or {}
    company = organization.get("name", "") if isinstance(organization, dict) else ""
    locations = posting.get("jobLocation") or []
    if isinstance(locations, dict):
        locations = [locations]
    labels = []
    for location in locations:
        if isinstance(location, dict):
            address = location.get("address", {})
            if isinstance(address, dict):
                labels.extend(str(address.get(key) or "") for key in ("addressLocality", "addressRegion"))
    location_text = data.get("location") or ", ".join(filter(None, labels))
    if posting.get("jobLocationType") == "TELECOMMUTE":
        location_text = "Remote " + location_text
    exp = posting.get("experienceRequirements", "")
    exp_text = exp if isinstance(exp, str) else ""
    low, high = experience_range(data.get("experience", "") or exp_text)
    return Job.from_dict({"url": url, "title": data.get("title") or posting.get("title", ""),
                          "company": data.get("company") or company, "location": location_text,
                          "description": strip_html(data.get("description") or posting.get("description", "")),
                          "min_experience": low, "max_experience": high})


@contextmanager
def browser_session(state_dir: Path, headless: bool = False, executable: str | None = None, storage_state: Path | None = None, interactive: bool = False):
    if interactive and headless:
        raise ValueError("Interactive verification requires a visible browser; omit --headless")
    from playwright.sync_api import sync_playwright
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sync_playwright() as driver:
        options = {"headless": headless}
        if executable:
            options["executable_path"] = executable
        proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if proxy_url:
            parsed = urlsplit(proxy_url)
            options["proxy"] = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 8080}"}
            if parsed.username:
                options["proxy"].update(username=parsed.username, password=parsed.password or "")
        browser = None
        if storage_state is not None:
            state = validate_storage_state(json.loads(storage_state.read_text(encoding="utf-8")))
            browser = driver.chromium.launch(**options)
            context = browser.new_context(storage_state=state, accept_downloads=False)
        else:
            context = driver.chromium.launch_persistent_context(str((state_dir / "browser").resolve()), accept_downloads=False, **options)
        context.set_default_timeout(15000)
        try:
            yield NaukriBrowser(context, interactive=interactive)
        finally:
            context.close()
            if browser:
                browser.close()


class NaukriBrowser:
    def __init__(self, context, interactive: bool = False):
        self.context = context
        self.page = context.pages[0] if context.pages else context.new_page()
        self.interactive = interactive

    def blocking_reason(self):
        host = urlsplit(self.page.url).hostname
        if host not in {"naukri.com", "www.naukri.com"}:
            raise AccountActionRequired("Navigation left Naukri; complete this job manually")
        text = self.page.locator("body").inner_text()[:6000]
        if re.search(r"access denied", text, re.I):
            return "Naukri denied access to this browser. An access denial may not offer a manual verification option"
        if re.search(r"verify (?:that )?you are human|unusual traffic|complete the captcha|security verification", text, re.I):
            return "Naukri requires a security check"
        for iframe in self.page.locator('iframe[src*="captcha"]').all():
            if iframe.is_visible():
                return "A CAPTCHA is visible"
        if "/nlogin/" in self.page.url or self.page.locator('input[type="password"]:visible').count():
            return "Naukri login expired"
        return None

    def guard(self, resume_url: str | None = None) -> bool:
        reason = self.blocking_reason()
        if reason is None:
            return False
        if not self.interactive:
            raise AccountActionRequired(reason + ". Stop and resolve it manually; a local scan --interactive keeps the visible browser open")
        print(reason + ". The browser is staying open for your manual inspection.", flush=True)
        input("Complete any available verification or login in the browser viewer. If access is denied with no verification option, press Ctrl+C to stop. After completing the check, press Enter here: ")
        remaining = self.blocking_reason()
        if remaining is not None:
            raise AccountActionRequired(remaining + ". Verification is still required; the scan has stopped")
        if resume_url is not None and self.page.url != resume_url:
            # Manual login can redirect to the homepage. Return to the requested
            # read-only listing/search once after the user completes verification.
            self.page.goto(resume_url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(1200)
            remaining = self.blocking_reason()
            if remaining is not None:
                raise AccountActionRequired(remaining + ". The requested page is still unavailable; the scan has stopped")
        return True

    def authenticate(self):
        homepage = "https://www.naukri.com/mnjuser/homepage"
        self.page.goto(homepage, wait_until="domcontentloaded")
        self.guard(resume_url=homepage)
        # A profile link is positive evidence of a logged-in session; the URL alone is not.
        self.page.locator('a[href*="/mnjuser/profile"], a[href*="/mnjuser/homepage"]').first.wait_for(state="attached")
        self.guard(resume_url=homepage)

    def login(self):
        self.page.goto("https://www.naukri.com/nlogin/login", wait_until="domcontentloaded")
        input("Sign into Naukri in the browser, then press Enter here. Your password is not read by this script: ")
        self.authenticate()

    def export_session(self, output: Path):
        self.authenticate()
        raw = self.context.storage_state(indexed_db=True)
        def is_naukri(host):
            return host == "naukri.com" or host.endswith(".naukri.com")
        # OAuth/CAPTCHA providers can leave unrelated cookies in this dedicated
        # browser. Only Naukri credentials belong in its exported session.
        state = validate_storage_state({
            "cookies": [cookie for cookie in raw["cookies"] if is_naukri(cookie["domain"].lstrip("."))],
            "origins": [origin for origin in raw["origins"] if is_naukri(urlsplit(origin["origin"]).hostname or "")],
        })
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Exclusive creation prevents accidentally overwriting an unrelated file.
        with open(output, "x", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)) as file:
            json.dump(state, file)

    def search(self, roles: list[str], experience: float, locations: list[str], pages: int):
        links = {}
        for role in roles:
            slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")
            for number in range(1, pages + 1):
                query = {"k": role, "experience": str(int(experience))}
                if locations:
                    query["l"] = ", ".join(locations)
                suffix = f"-{number}" if number > 1 else ""
                target = f"https://www.naukri.com/{quote(slug)}-jobs{suffix}?{urlencode(query)}"
                self.page.goto(target, wait_until="domcontentloaded")
                self.page.wait_for_timeout(1200)
                self.guard(resume_url=target)
                locator = self.page.locator('a[href*="/job-listings-"]')
                if not locator.count():
                    try:
                        locator.first.wait_for(state="attached", timeout=10000)
                    except Exception as error:
                        if self.guard(resume_url=target):
                            locator.first.wait_for(state="attached", timeout=10000)
                        elif re.search(r"no (?:jobs|results)|couldn't find", self.page.locator("body").inner_text(), re.I):
                            break
                        else:
                            raise RuntimeError("No job links found; Naukri may have changed its page layout") from error
                for href in locator.evaluate_all("elements => elements.map(el => el.href)"):
                    try:
                        clean = job_url(href)
                        links[clean] = None
                    except ValueError:
                        continue
        return list(links)

    def read_job(self, url: str) -> Job:
        clean = job_url(url)
        self.page.goto(clean, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1200)
        self.guard(resume_url=clean)
        data = self.page.evaluate("""() => {
            const first = selectors => {
                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (el && el.innerText.trim()) return el.innerText.trim();
                }
                return '';
            };
            return {
                scripts: [...document.querySelectorAll('script[type="application/ld+json"]')].map(el => el.textContent),
                title: first(['h1']),
                company: first(['.styles_jd-header-comp-name__MvqAI a', '.jd-header-comp-name a', '[class*="jd-header-comp-name"] a']),
                location: first(['.styles_jhc__location__W_pVs', '.loc', '[class*="jhc__location"]']),
                experience: first(['.styles_jhc__exp__k_giM', '.exp', '[class*="jhc__exp"]']),
                description: first(['.styles_JDC__dang-inner-html__h0K4t', '.dang-inner-html', '[class*="dang-inner-html"]', '[class*="job-description"]'])
            };
        }""")
        return parse_job(data, job_url(self.page.url))

    def already_applied(self) -> bool:
        return self.page.get_by_text(re.compile(r"^(?:already applied|applied)$", re.I), exact=True).count() > 0

    def application_button(self):
        buttons = self.page.get_by_role("button", name=re.compile(r"^apply(?: now)?$", re.I))
        visible = [button for button in buttons.all() if button.is_visible() and button.is_enabled()]
        if len(visible) != 1:
            return None
        return visible[0]

    def confirm_application(self) -> bool:
        confirmation = self.page.get_by_text(re.compile(r"^(?:you have (?:been )?)?successfully applied(?:\b.*)?$|^application (?:has been )?submitted(?: successfully)?[.!]?$", re.I))
        for attempt in range(2):
            try:
                confirmation.first.wait_for(state="visible", timeout=12000)
            except Exception:
                completed = self.guard()
                if self.interactive and attempt == 0 and not completed:
                    input("No success confirmation yet. Complete any recruiter questions yourself in the browser, then press Enter here to check the result. Press Ctrl+C to stop: ")
                    self.guard()
                    completed = True
                if completed and attempt == 0:
                    # A manual check after Apply may reveal the confirmation.
                    # Recheck the result without clicking Apply a second time.
                    continue
                return False
            self.guard()
            return confirmation.first.is_visible()
        return False
