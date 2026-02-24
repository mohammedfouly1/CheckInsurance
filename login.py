"""
login.py — Waseel eClaims session manager
Handles login (credentials + OTP), session persistence, and session restoration.
Run: python login.py
"""

import io
import json
import os
import re
import sys

from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep, perf_counter

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, BrowserContext, Page

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TARGET_URL        = "https://eclaims.waseel.com/"
USERNAME          = os.environ["WASEEL_USERNAME"]
PASSWORD          = os.environ["WASEEL_PASSWORD"]
WEBHOOK_API_KEY   = os.environ["WEBHOOK_API_KEY"]
WEBHOOK_SITE_BASE = os.environ["WEBHOOK_SITE_BASE"]
SESSION_FILE      = Path("session.json")

# ---------------------------------------------------------------------------
# OTP helpers (webhook.site + SendGrid)
# ---------------------------------------------------------------------------

def get_token_uuid(api_key: str) -> str:
    """Return the UUID of the first token belonging to this API key."""
    url = f"{WEBHOOK_SITE_BASE}/token"
    resp = requests.get(url, headers={"Api-Key": api_key, "Accept": "application/json"}, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    if not data:
        raise RuntimeError("No webhook.site tokens found for the provided API key.")
    return data[0]["uuid"]


def fetch_latest_request(token_uuid: str, api_key: str) -> dict | None:
    """Return the most recent request object for the given token, or None."""
    url = f"{WEBHOOK_SITE_BASE}/token/{token_uuid}/requests?sorting=newest"
    resp = requests.get(url, headers={"Api-Key": api_key, "Accept": "application/json"}, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return data[0] if data else None


def _parse_ts(ts_str: str) -> datetime:
    """Parse a webhook.site timestamp into a UTC-aware datetime."""
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _get_email_body(raw: dict) -> str:
    """
    Combine all text sources from a webhook.site request object.
    SendGrid sends multipart/form-data so 'content' is empty;
    the parsed email fields live in raw['request'] (text, html, subject, ...).
    """
    parts = [raw.get("content", "") or ""]
    req = raw.get("request", {})
    if isinstance(req, dict):
        parts.append(req.get("text", "") or "")
        parts.append(req.get("html", "") or "")
        parts.append(req.get("subject", "") or "")
    return " ".join(parts)


def extract_otp(content: str) -> str | None:
    """Extract the first 6-digit code from email body (plain text or HTML)."""
    match = re.search(r'\b(\d{6})\b', content)
    return match.group(1) if match else None


def fetch_latest_otp(
    token_uuid: str,
    api_key: str,
    after_time: datetime,
    max_wait: int = 30,
) -> str:
    """
    Poll webhook.site until a fresh OTP (< 3 min old, newer than after_time)
    arrives. Returns the 6-digit code string.
    Raises TimeoutError if no OTP arrives within max_wait seconds.
    """
    poll_interval = 3
    max_attempts  = max_wait // poll_interval
    staleness     = timedelta(minutes=3)

    for attempt in range(1, max_attempts + 1):
        print(f"  [OTP] Polling attempt {attempt}/{max_attempts} ...")
        raw = fetch_latest_request(token_uuid, api_key)

        if raw:
            created_at_str = raw.get("created_at", "")
            msg_time = _parse_ts(created_at_str)
            now_utc  = datetime.now(timezone.utc)

            if msg_time > after_time:
                age = now_utc - msg_time
                if age <= staleness:
                    otp = extract_otp(_get_email_body(raw))
                    if otp:
                        print(f"  [OTP] Code found: {otp}  (age: {int(age.total_seconds())}s)")
                        req_uuid = raw.get("uuid")
                        if req_uuid:
                            delete_webhook_request(token_uuid, req_uuid, api_key)
                        return otp
                    else:
                        raise ValueError("Fresh message found but no 6-digit OTP code in body.")
                else:
                    print(f"  [OTP] Message too old ({int(age.total_seconds())}s), waiting ...")
            else:
                print(f"  [OTP] No new message yet ...")
        else:
            print("  [OTP] No messages in inbox yet ...")

        if attempt < max_attempts:
            sleep(poll_interval)

    raise TimeoutError(f"No OTP received within {max_wait}s.")


def delete_webhook_request(token_uuid: str, request_uuid: str, api_key: str) -> None:
    """Delete a specific request from the webhook.site inbox after OTP retrieval."""
    url = f"{WEBHOOK_SITE_BASE}/token/{token_uuid}/request/{request_uuid}"
    try:
        resp = requests.delete(
            url,
            headers={"Api-Key": api_key, "Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code in (200, 204):
            print(f"  [OTP] Deleted inbox message {request_uuid!r}")
        else:
            print(f"  [OTP] Delete returned {resp.status_code}: {resp.text[:60]}")
    except Exception as exc:
        print(f"  [OTP] Delete failed (non-critical): {exc}")

# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------

def wait_stable(page: Page, timeout: int = 15000) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass
    page.wait_for_timeout(800)


def sep(label: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")

# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def save_session(context: BrowserContext) -> None:
    """Persist all browser cookies + localStorage to SESSION_FILE."""
    state = context.storage_state()
    SESSION_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    size = SESSION_FILE.stat().st_size
    print(f"  [SESSION] Saved -> {SESSION_FILE}  ({size:,} bytes)")


def check_session_valid(page: Page) -> bool:
    """
    Navigate to TARGET_URL with the restored session.
    Returns True  if the Angular eClaims app loads (session active).
    Returns False if Keycloak redirects us to the login page.
    """
    try:
        page.goto(TARGET_URL, wait_until="load", timeout=30000)
        wait_stable(page)
        # Valid session stays on eclaims.waseel.com
        # Expired session gets redirected to iam.waseel.com (Keycloak)
        return "iam.waseel.com" not in page.url
    except Exception as exc:
        print(f"  [SESSION] Validity check error: {exc}")
        return False

# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

def do_login(page: Page, context: BrowserContext) -> Page:
    """
    Full login: navigate → fill credentials → handle OTP → click NPHIES card.
    Saves session after the NPHIES tab fully loads.
    Returns the active Page on eclaims.waseel.com.
    """
    sep("FULL LOGIN")
    page.goto(TARGET_URL, wait_until="load", timeout=30000)
    wait_stable(page)
    print(f"  Redirected to: {page.url}")

    # ── credentials ─────────────────────────────────────────────────────────
    page.wait_for_selector("#username", timeout=10000)
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    if not page.is_checked("#rememberMe"):
        page.check("#rememberMe")
    print("  [+] Credentials filled")

    login_submitted_at = datetime.now(timezone.utc)
    print("  [+] Submitting credentials ...")
    with page.expect_navigation(wait_until="load", timeout=30000):
        page.click("#kc-login")
    wait_stable(page)
    print(f"  [+] Post-submit URL: {page.url}")

    # ── OTP ─────────────────────────────────────────────────────────────────
    if page.query_selector("#kc-email-totp-code-login-form"):
        sep("OTP")
        token_uuid = get_token_uuid(WEBHOOK_API_KEY)
        print(f"  [OTP] Token UUID: {token_uuid}")
        otp_code = fetch_latest_otp(
            token_uuid, WEBHOOK_API_KEY,
            after_time=login_submitted_at, max_wait=30,
        )
        page.wait_for_selector("#code", timeout=10000)
        page.fill("#code", otp_code)
        print(f"  [OTP] Code filled: {otp_code}")
        with page.expect_navigation(wait_until="load", timeout=30000):
            page.click("input[type='submit']")
        wait_stable(page)
        print(f"  [OTP] Complete -> {page.url}")

    # ── click Waseel Connect - NPHIES card (opens new tab) ──────────────────
    sep("NAVIGATING -> WASEEL CONNECT - NPHIES")
    nphies_btn = page.locator("button").filter(
        has_text=re.compile(r"^Waseel Connect - NPHIES$")
    )
    try:
        with context.expect_page(timeout=15000) as new_page_info:
            nphies_btn.click()
        target_page = new_page_info.value
        wait_stable(target_page)
        print(f"  [+] New tab opened -> {target_page.url}")
    except Exception:
        wait_stable(page)
        target_page = page
        print(f"  [+] Same-page navigation -> {target_page.url}")

    # ── save session after eclaims.waseel.com fully loads ───────────────────
    save_session(context)
    return target_page

# ---------------------------------------------------------------------------
# Public API — imported by other scripts
# ---------------------------------------------------------------------------

def get_logged_in_page(pw) -> tuple:
    """
    Importable entry point for other scripts.
    Returns (browser, context, page) with an active session on eclaims.waseel.com.
    Handles session restore / full login / session expiry automatically.
    """
    browser = pw.chromium.launch(headless=False, slow_mo=80)

    if SESSION_FILE.exists():
        sep("RESTORING SAVED SESSION")
        mtime = datetime.fromtimestamp(SESSION_FILE.stat().st_mtime)
        print(f"  [SESSION] Found {SESSION_FILE}  (saved: {mtime:%Y-%m-%d %H:%M:%S})")

        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            storage_state=str(SESSION_FILE),
        )
        page = context.new_page()

        print("  [SESSION] Checking validity ...")
        if check_session_valid(page):
            print(f"  [SESSION] VALID — skipping login -> {page.url}")
            return browser, context, page

        print("  [SESSION] EXPIRED — removing old session, logging in fresh ...")
        SESSION_FILE.unlink()
        context.close()

    else:
        sep("NO SAVED SESSION — FULL LOGIN")

    context = browser.new_context(viewport={"width": 1400, "height": 900})
    page    = context.new_page()
    target_page = do_login(page, context)
    return browser, context, target_page


# ---------------------------------------------------------------------------
# Timer — per-step wall-clock timing with cross-run JSONL log
# ---------------------------------------------------------------------------

class Timer:
    """
    Track wall-clock time for named steps.
    start(label) / stop(label) pairs.
    summary()  → prints a bar-chart breakdown.
    save()     → appends one JSON line to timing_log.jsonl for cross-run analysis.
    """

    def __init__(self, run_label: str = ""):
        self._starts:  dict = {}   # label -> perf_counter at start
        self._elapsed: dict = {}   # label -> elapsed seconds
        self._order:   list = []   # insertion order
        self.run_at    = datetime.now()
        self.run_label = run_label

    def start(self, label: str) -> None:
        self._starts[label] = perf_counter()
        if label not in self._order:
            self._order.append(label)

    def stop(self, label: str) -> float:
        if label not in self._starts:
            return 0.0
        elapsed = perf_counter() - self._starts.pop(label)
        self._elapsed[label] = elapsed
        print(f"  [timer] {label}: {elapsed:.2f}s")
        return elapsed

    def summary(self) -> None:
        if not self._elapsed:
            return
        total = sum(self._elapsed.values())
        max_t = max(self._elapsed.values()) or 1.0
        print(f"\n  {'─'*60}")
        print(f"  TIMING SUMMARY  [{self.run_label}]")
        print(f"  {'─'*60}")
        for lbl in self._order:
            t   = self._elapsed.get(lbl, 0.0)
            bar = "█" * max(1, round(t / max_t * 20))
            pct = t / total * 100 if total else 0.0
            print(f"  {lbl:<35} {t:6.2f}s  {pct:4.1f}%  {bar}")
        print(f"  {'─'*60}")
        print(f"  {'TOTAL':<35} {total:6.2f}s")
        print(f"  {'─'*60}")

    def save(self, filename: str = "timing_log.jsonl") -> None:
        entry = {
            "run_at": self.run_at.isoformat(timespec="seconds"),
            "label":  self.run_label,
            "steps":  {k: round(self._elapsed.get(k, 0.0), 3) for k in self._order},
        }
        with open(filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"  [timer] Log appended → {filename}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"\n  Waseel eClaims — Login & Session Manager")
    print(f"  {'='*45}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=80)

        if SESSION_FILE.exists():
            # ── try to restore saved session ─────────────────────────────────
            sep("RESTORING SAVED SESSION")
            mtime = datetime.fromtimestamp(SESSION_FILE.stat().st_mtime)
            print(f"  [SESSION] Found {SESSION_FILE}  (saved: {mtime:%Y-%m-%d %H:%M:%S})")

            context = browser.new_context(
                viewport={"width": 1400, "height": 900},
                storage_state=str(SESSION_FILE),
            )
            page = context.new_page()

            print("  [SESSION] Checking validity ...")
            if check_session_valid(page):
                print(f"  [SESSION] VALID — skipping login -> {page.url}")
                target_page = page
            else:
                # ── session expired: wipe + fresh login ───────────────────────
                print("  [SESSION] EXPIRED — removing old session file ...")
                SESSION_FILE.unlink()
                context.close()

                context = browser.new_context(viewport={"width": 1400, "height": 900})
                page    = context.new_page()
                target_page = do_login(page, context)

        else:
            # ── no session file: full login ───────────────────────────────────
            sep("NO SAVED SESSION — FULL LOGIN")
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            page    = context.new_page()
            target_page = do_login(page, context)

        # ── ready ─────────────────────────────────────────────────────────────
        print(f"\n  {'='*45}")
        print(f"  Ready  |  {target_page.url}")
        print(f"  {'='*45}")
        print("\n  Close the browser window to exit.")
        try:
            target_page.wait_for_event("close", timeout=120000)
        except Exception:
            pass
        browser.close()


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
