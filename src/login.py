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
from time import sleep

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, BrowserContext, Page

from config import SLOW_MO, VIEWPORT, SETTLE_MS, TIMEOUT_NETWORKIDLE

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TARGET_URL        = "https://eclaims.waseel.com/"
USERNAME          = os.environ["WASEEL_USERNAME"]
PASSWORD          = os.environ["WASEEL_PASSWORD"]
WEBHOOK_API_KEY   = os.environ["WEBHOOK_API_KEY"]
WEBHOOK_SITE_BASE = os.environ["WEBHOOK_SITE_BASE"]
SESSION_FILE      = Path(__file__).parent / "session.json"

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

def wait_stable(page: Page, timeout: int = TIMEOUT_NETWORKIDLE) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass
    page.wait_for_timeout(SETTLE_MS)


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


_CCHI_URL_VALIDATE = "https://eclaims.waseel.com/nphies/beneficiary/add"

def check_session_valid(page: Page) -> bool:
    """
    Navigate directly to the CCHI beneficiary page and verify the Angular form
    actually loaded (national ID search input is present).

    A valid session loads the full Angular app (~200KB+) with the ID input.
    An expired session returns a redirect/stub page (~13KB) without the input,
    even if the URL does not contain 'iam.waseel.com' — this was the prior
    false-positive bug where sso.waseel.com was accepted as VALID.
    """
    try:
        page.goto(_CCHI_URL_VALIDATE, wait_until="load", timeout=30000)
        wait_stable(page)
        # Hard redirect to Keycloak login page
        if "iam.waseel.com" in page.url:
            print(f"  [SESSION] Redirected to Keycloak: {page.url}")
            return False
        # Confirm the Angular CCHI form actually rendered (national ID input present)
        has_input = page.locator(
            'input[placeholder*="national ID"], '
            'input[placeholder*="iqama"], '
            'input[placeholder*="National ID"], '
            'input[placeholder*="Iqama"]'
        ).count() > 0
        if not has_input:
            content_len = len(page.content())
            print(f"  [SESSION] CCHI form not found ({content_len:,} bytes) — session expired")
            return False
        return True
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
    browser = pw.chromium.launch(headless=False, slow_mo=SLOW_MO)

    if SESSION_FILE.exists():
        sep("RESTORING SAVED SESSION")
        mtime = datetime.fromtimestamp(SESSION_FILE.stat().st_mtime)
        print(f"  [SESSION] Found {SESSION_FILE}  (saved: {mtime:%Y-%m-%d %H:%M:%S})")

        context = browser.new_context(
            viewport=VIEWPORT,
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

    context = browser.new_context(viewport=VIEWPORT)
    page    = context.new_page()
    target_page = do_login(page, context)
    return browser, context, target_page


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"\n  Waseel eClaims — Login & Session Manager")
    print(f"  {'='*45}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=SLOW_MO)

        if SESSION_FILE.exists():
            # ── try to restore saved session ─────────────────────────────────
            sep("RESTORING SAVED SESSION")
            mtime = datetime.fromtimestamp(SESSION_FILE.stat().st_mtime)
            print(f"  [SESSION] Found {SESSION_FILE}  (saved: {mtime:%Y-%m-%d %H:%M:%S})")

            context = browser.new_context(
                viewport=VIEWPORT,
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

                context = browser.new_context(viewport=VIEWPORT)
                page    = context.new_page()
                target_page = do_login(page, context)

        else:
            # ── no session file: full login ───────────────────────────────────
            sep("NO SAVED SESSION — FULL LOGIN")
            context = browser.new_context(viewport=VIEWPORT)
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
