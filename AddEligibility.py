"""
AddEligibility.py — Add Beneficiary & Apply Eligibility
Full automated flow:
  1. Login (session restore or fresh login via login.py)
  2. CCHI inquiry via CheckCCHI.run_cchi_inquiry()
  3. Display patient summary table
  4. Select Marital Status → Unknown
  5. Select Occupation     → Unknown
  6. Set Date of Birth     → 01/01/2000
  7. Check Set Primary radio
  8. Click "Add & Apply Eligibility"
  9. Dismiss success message, wait for navigation
 10. Capture and display result page elements
Run: python AddEligibility.py
"""

import io
import re
import sys

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page

import login
import CheckCCHI

load_dotenv()

# ---------------------------------------------------------------------------
# Angular Material helpers — confirmed winning methods
# ---------------------------------------------------------------------------

def select_mat_dropdown(page: Page, label_text: str, option_text: str) -> None:
    """
    Open a mat-select by its mat-form-field label, list all options (scrolling
    panel to bottom to reveal hidden items), then select via JS click.
    Winning method: JS scroll + JS click (M3 from discovery run).
    """
    CheckCCHI.sep(f"DROPDOWN — {label_text}")
    page.locator("mat-form-field").filter(has_text=label_text).first \
        .locator("mat-select").first.click()
    page.wait_for_selector("mat-option", timeout=5000)
    page.wait_for_timeout(200)

    # Scroll panel to bottom so all items (including last) are reachable
    page.evaluate(r"""() => {
        const p = document.querySelector(
            '.mat-select-panel, .mat-mdc-select-panel, [class*="select-panel"]'
        );
        if (p) p.scrollTop = p.scrollHeight;
    }""")
    page.wait_for_timeout(150)

    texts = [o.inner_text().strip() for o in page.locator("mat-option").all()]
    print(f"  Options ({len(texts)}): {texts}")

    esc = re.escape(option_text)
    ok = page.evaluate(rf"""() => {{
        const p = document.querySelector(
            '.mat-select-panel, .mat-mdc-select-panel, [class*="select-panel"]'
        );
        if (p) p.scrollTop = p.scrollHeight;
        const t = [...document.querySelectorAll('mat-option')]
                      .find(o => /^{esc}$/i.test(o.textContent.trim()));
        if (t) {{ t.scrollIntoView({{block: 'center'}}); t.click(); return true; }}
        return false;
    }}""")
    page.wait_for_timeout(150)
    print(f"  [+] Selected: {option_text!r}" if ok else f"  [!] Option '{option_text}' not found")


def fill_date_picker(page: Page, placeholder: str, date_value: str) -> None:
    """
    Fill a date-picker input: click → Ctrl+A → type char-by-char → Tab.
    Winning method: type with delay (M2 from discovery run).
    """
    CheckCCHI.sep(f"DATE PICKER — {placeholder}")
    inp = page.locator(f'input[placeholder="{placeholder}"]').first
    inp.click(timeout=3000)
    page.keyboard.press("Control+a")
    inp.type(date_value, delay=30)
    inp.press("Tab")
    page.wait_for_timeout(200)
    print(f"  [+] Stored: {inp.input_value()!r}")


def click_set_primary(page: Page) -> None:
    """
    Click the Set Primary radio button.
    Winning method: first unchecked mat-radio-button (M4 from discovery run).
    Note: 'Set Primary' is a column header — it is NOT the radio element's text.
    """
    CheckCCHI.sep("SET PRIMARY")
    target = page.locator(
        "mat-radio-button:not(.mat-radio-checked), input[type='radio']:not(:checked)"
    ).first
    if target.count() > 0:
        target.click(timeout=3000)
        page.wait_for_timeout(150)
        print("  [+] Set Primary clicked")
    else:
        print("  [!] No unchecked radio button found")

# ---------------------------------------------------------------------------
# Exportable Phase 2 Part 2 entry point
# ---------------------------------------------------------------------------

def run_add_eligibility(page: Page, raw: dict = None) -> str:
    """
    Phase 2 Part 2: Fill Marital Status, Occupation, DOB, Set Primary,
    click 'Add & Apply Eligibility', dismiss success message, wait for navigation.
    Returns the new URL (eligibility page URL).
    Importable by RequestEligibility.py and future scripts.
    """
    select_mat_dropdown(page, "Marital Status", "Unknown")
    select_mat_dropdown(page, "Occupation",     "Unknown")
    fill_date_picker(page, "Select date of birth", "01/01/2000")
    click_set_primary(page)

    # Click Add & Apply Eligibility
    CheckCCHI.sep("CLICKING ADD & APPLY ELIGIBILITY")
    add_btn = page.locator("button").filter(
        has_text=re.compile(r"Add.*Apply.*Eligibility", re.IGNORECASE)
    ).first
    add_btn.wait_for(timeout=10000)
    print(f"  [+] Button: {add_btn.inner_text().strip()!r}")
    add_btn.click()
    print("  [+] Clicked — watching for success message ...")

    _snack = "mat-snack-bar-container, [class*='snack-bar'], [class*='toast'], .alert-success"
    try:
        page.wait_for_selector(_snack, timeout=2000, state="visible")
        msg = page.locator(_snack).first.inner_text()
        print(f"  [+] Success: {msg.strip()!r}")
    except Exception:
        print("  [!] No snackbar detected")
    page.mouse.click(700, 300)
    print("  [+] Dismissed")
    page.wait_for_timeout(300)

    try:
        page.wait_for_url(
            lambda url: "beneficiary/add" not in url,
            timeout=15000,
        )
        print("  [+] Navigation detected")
    except Exception:
        print("  [!] URL unchanged after 15 s")

    login.wait_stable(page)
    page.wait_for_timeout(500)
    return page.url


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"\n  AddEligibility — Beneficiary Add & Eligibility Request")
    print(f"  {'='*55}")

    with sync_playwright() as pw:
        browser, context, page = login.get_logged_in_page(pw)

        # ── Step 1: CCHI inquiry ─────────────────────────────────────────────
        raw = CheckCCHI.run_cchi_inquiry(page)

        # ── Step 2: Patient summary table ────────────────────────────────────
        CheckCCHI.sep("PATIENT SUMMARY")
        CheckCCHI.print_patient_table(raw)

        # ── Steps 3–6: Fill form + submit + navigate ─────────────────────────
        new_url = run_add_eligibility(page, raw)

        # ── Step 7: Capture result page ──────────────────────────────────────
        CheckCCHI.sep("RESULT PAGE")
        print(f"  New URL : {new_url}")

        CheckCCHI.detect_page_elements(page, "RESULT PAGE")
        CheckCCHI.capture_patient_data(page)

        print(f"\n  {'='*55}")
        print("  AddEligibility complete.")
        print(f"  {'='*55}")
        print("\n  Close the browser window to exit.")
        try:
            page.wait_for_event("close", timeout=120000)
        except Exception:
            pass
        browser.close()


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
