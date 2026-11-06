"""Helpers shared by the Playwright suites (imported by each run_test.py)."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import Browser, Page, Playwright

# The repository, for the suites that sign tokens as the server does (chipova_forge.gateway.api).
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:3000").rstrip("/")
# The Reflex backend: the same origin behind re-ddns, another port in a local run.
BACKEND_URL = os.environ.get("BACKEND_URL", BASE_URL).rstrip("/")
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", Path(__file__).resolve().parent / "output"))
HEADLESS = os.environ.get("HEADLESS", "1").strip().lower() not in {"0", "false", "no", "off"}
SLOWMO = int(os.environ.get("PW_SLOWMO_MS", "0") or 0)
TIMEOUT = int(os.environ.get("PW_TIMEOUT_MS", "30000") or 30000)
SECRET = os.environ.get("DDNS_AUTH_SECRET", "")
_T0 = time.time()


def log(*args) -> None:
    print(f"[{time.time() - _T0:6.2f}]", *args, flush=True)


def launch(pw: Playwright) -> Browser:
    args = []
    if BASE_URL.startswith("https://") and ".reflex-ddns.com" in BASE_URL:
        # A Mac may resolve *.reflex-ddns.com to the Docker-internal IP.
        args.append("--host-resolver-rules=MAP *.reflex-ddns.com 127.0.0.1")
    return pw.chromium.launch(headless=HEADLESS, slow_mo=SLOWMO, args=args)


def sign_in_cookie(email: str, name: str) -> dict:
    """The platform's sign-in cookie for ``email`` (what Relack sets), signed with the test secret."""
    import jwt

    token = jwt.encode({"email": email, "name": name, "approved": True, "exp": int(time.time()) + 3600}, SECRET, algorithm="HS256")
    host = re.sub(r"^https?://", "", BASE_URL).split(":")[0].split("/")[0]
    return {"name": "ddns_auth", "value": token, "domain": host, "path": "/"}


def new_page(browser: Browser, errors: list[str], who: str, *, email: str = "", width: int = 1500, height: int = 920) -> Page:
    """A browser tab of its own (its own guest, or signed in as ``email``)."""
    ctx = browser.new_context(viewport={"width": width, "height": height}, ignore_https_errors=True, accept_downloads=True)
    if email:
        if not SECRET:
            raise SystemExit("signing in needs DDNS_AUTH_SECRET (the runner sets one)")
        ctx.add_cookies([sign_in_cookie(email, who)])
    page = ctx.new_page()
    page.set_default_timeout(TIMEOUT)

    def on_console(msg) -> None:  # noqa: ANN001
        if msg.type == "error" and "Outdated Optimize Dep" not in msg.text and "Failed to load resource" not in msg.text:
            errors.append(f"[{who}] console: {msg.text}")

    def on_error(err) -> None:  # noqa: ANN001
        # Reflex's own window.onerror trips on error events without an error object (a Monaco
        # worker stopped while loading): not the page's fault.
        if "at window.onerror" in (getattr(err, "stack", "") or ""):
            return
        if str(err) == "Canceled":
            return
        errors.append(f"[{who}] pageerror: {err}")

    page.on("console", on_console)
    page.on("pageerror", on_error)
    return page


def shot(page: Page, name: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUTPUT_DIR / f"{name}.png"))


def wait_for(fn, what: str, timeout: float = 30.0) -> None:
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            if fn():
                return
        except Exception as e:  # noqa: BLE001 - keep trying until the deadline
            last = e
        time.sleep(0.25)
    raise AssertionError(f"timed out: {what}" + (f" (last error: {last})" if last else ""))


def open_home(page: Page) -> None:
    page.goto(BASE_URL + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#new-project", timeout=120000)


def new_product(page: Page, title: str, template: str = "smart_sensor", chip: str = "") -> str:
    """Makes a product from the home page (its template, and an example chip); returns its id."""
    open_home(page)
    page.click("#new-project")
    page.wait_for_selector("#create-project")
    page.fill("#new-title", title)
    page.click(f"[data-option='{template}']")
    page.click(f"[data-option='{chip or template}'] >> nth=-1")
    page.click("#create-project")
    page.wait_for_url(re.compile(r".*/p/[0-9a-f]{10}"), timeout=60000)
    page.wait_for_selector("#steps [data-step]", timeout=60000)
    return page.url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]


def open_product(page: Page, project_id: str, view: str = "") -> None:
    page.goto(f"{BASE_URL}/p/{project_id}" + (f"?view={view}" if view else ""), wait_until="domcontentloaded")
    page.wait_for_selector("[data-view='overview']", timeout=90000)


def view(page: Page, name: str) -> None:
    page.click(f"[data-view='{name}']")
    page.wait_for_selector(f"#section[data-section='{name}']", timeout=15000)


def status_of(page: Page, selector: str) -> str:
    loc = page.locator(selector)
    return (loc.first.get_attribute("data-status") or "") if loc.count() else ""


def wait_status(page: Page, selector: str, want: tuple[str, ...], timeout: float = 180) -> str:
    end = time.time() + timeout
    st = ""
    while time.time() < end:
        st = status_of(page, selector)
        if st in want:
            return st
        time.sleep(0.4)
    raise AssertionError(f"{selector} didn't reach {want} (last {st!r})")


def tool(name: str, user: str, **args):
    """Calls one of Forge's AI tools as the AI Hub does for ``user`` (the person the AI works
    for), with a token signed with the test secret."""
    os.environ.setdefault("DDNS_AUTH_SECRET", SECRET)
    from reflex_ddns_auth.tools import call_token

    req = urllib.request.Request(
        f"{BACKEND_URL}/_ddns_tool/call/{name}",
        data=json.dumps({"args": args}).encode(),
        headers={"Authorization": "Bearer " + call_token(os.environ.get("SERVICE_SUBDOMAIN", "chipova-forge"), user), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.loads(r.read())
    if not out.get("ok"):
        raise ToolRefused(out.get("error", ""))
    return out.get("result")


class ToolRefused(RuntimeError):
    """A tool call the app refused or that failed (the AI Hub shows the AI this message)."""


def api(method: str, path: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    """The REST API with a personal token."""
    req = urllib.request.Request(BACKEND_URL + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def backend_get(path: str, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(BACKEND_URL + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def finish(errors: list[str]) -> None:
    if errors:
        print("Page errors:\n" + "\n".join(errors[:30]))
        sys.exit(1)
    print("OK")
