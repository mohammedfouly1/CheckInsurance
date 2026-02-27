"""
CheckCCHI.py — CCHI Beneficiary Inquiry
Logs in via login.py session, navigates to the beneficiary add page,
searches by national ID / iqama, captures all returned patient data,
and displays it in a formatted table.
Run standalone: python CheckCCHI.py
Importable:     import CheckCCHI; raw = CheckCCHI.run_cchi_inquiry(page)
"""

import io
import json
import os
import re
import sys

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page

import login

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CCHI_URL  = "https://eclaims.waseel.com/nphies/beneficiary/add"
ID_NUMBER = os.environ["ID_NUMBER"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def show(title: str, data) -> None:
    login.sep(title)
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(data)


def validate_id(id_number: str) -> tuple:
    """
    Validate a Saudi National ID or Iqama number.
    Returns (True, "") on success, or (False, reason_string) on failure.

    Checks (in order):
      1. All digits
      2. Exactly 10 characters
      3. Starts with 1 (National ID) or 2 (Iqama)
      4. National ID only: Luhn-like checksum
         - Odd positions (1,3,5,7,9): multiply digit by 2; if ≥10, sum its two digits
         - Even positions (2,4,6,8): keep as-is
         - Sum all 9 values → total
         - 10th digit must equal (10 − total % 10) % 10
    """
    s = id_number.strip()
    if not s.isdigit():
        return False, "non-numeric characters"
    if len(s) != 10:
        return False, f"length {len(s)} (must be 10)"
    if s[0] not in ("1", "2"):
        return False, f"starts with '{s[0]}' (must be 1 for National ID or 2 for Iqama)"
    if s[0] == "1":
        digits = [int(c) for c in s]
        total = 0
        for i, d in enumerate(digits[:9]):    # first 9 digits, 0-indexed
            if (i + 1) % 2 == 1:             # odd positions: 1,3,5,7,9
                v = d * 2
                if v >= 10:
                    v = (v // 10) + (v % 10) # sum the two digits
                total += v
            else:
                total += d
        if digits[9] != (10 - (total % 10)) % 10:
            return False, "checksum mismatch (invalid National ID)"
    return True, ""

# ---------------------------------------------------------------------------
# Page detection
# ---------------------------------------------------------------------------

def detect_page_elements(page: Page, label: str) -> None:
    """Dump all inputs, buttons, labels and forms on the current page."""

    elements = page.evaluate(r"""() => {
        const q = sel => [...document.querySelectorAll(sel)];

        const inputs = q('input, select, textarea').map(el => ({
            tag:         el.tagName.toLowerCase(),
            type:        el.type   || null,
            name:        el.name   || null,
            id:          el.id     || null,
            placeholder: el.placeholder || null,
            value:       el.value  || null,
            required:    el.required || false,
            label:       (() => {
                if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
                if (el.id) {
                    const lbl = document.querySelector('label[for="' + el.id + '"]');
                    if (lbl) return lbl.textContent.trim();
                }
                const matField = el.closest('mat-form-field');
                if (matField) {
                    const ml = matField.querySelector('mat-label, label');
                    if (ml) return ml.textContent.trim();
                }
                return null;
            })(),
        }));

        const buttons = q('button, input[type=submit], input[type=button], a[role=button]').map(b => ({
            tag:  b.tagName.toLowerCase(),
            type: b.type || null,
            text: (b.innerText || b.value || b.getAttribute('aria-label') || '').trim().slice(0, 120) || null,
            id:   b.id   || null,
            cls:  (b.className || '').trim().slice(0, 100) || null,
            disabled: b.disabled || false,
        })).filter(b => b.text).slice(0, 60);

        const headings = q('h1,h2,h3,h4,h5,mat-card-title,[class*="title"],[class*="header"]')
            .map(h => h.innerText?.trim())
            .filter(t => t && t.length < 200)
            .slice(0, 20);

        const labels = [...new Set(
            q('label, mat-label, mat-card-subtitle, [class*="label"], [class*="field-label"]')
                .map(l => l.innerText?.trim())
                .filter(t => t && t.length < 200)
        )].slice(0, 60);

        return { inputs, buttons, headings, labels };
    }""")

    show(f"[{label}] INPUTS",   elements["inputs"])
    show(f"[{label}] BUTTONS",  elements["buttons"])
    show(f"[{label}] HEADINGS", elements["headings"])
    show(f"[{label}] LABELS",   elements["labels"])

# ---------------------------------------------------------------------------
# Patient data capture (after inquiry)
# ---------------------------------------------------------------------------

def capture_patient_data(page: Page) -> dict:
    """
    Scrape all visible patient data from the page after CCHI inquiry.
    Captures: mat-form-field pairs, input values, dropdowns, data rows, cards.
    """
    data = page.evaluate(r"""() => {
        const q = sel => [...document.querySelectorAll(sel)];

        const fields = q('mat-form-field').map(field => {
            const label = field.querySelector('mat-label, label')?.innerText?.trim() || null;
            const input = field.querySelector('input, select, textarea, mat-select');
            let value = null;
            if (input) {
                if (input.tagName === 'MAT-SELECT') {
                    value = input.querySelector('.mat-select-value-text')?.innerText?.trim() || null;
                } else {
                    value = input.value?.trim() || null;
                }
            }
            const display = field.querySelector('[class*="value"], [class*="display"], span:not(mat-label)');
            if (!value && display) value = display.innerText?.trim() || null;
            return { label, value };
        }).filter(f => f.label || f.value);

        const dataRows = q('[class*="detail"], [class*="info-row"], [class*="data-row"], mat-list-item')
            .map(row => row.innerText?.trim())
            .filter(t => t && t.length < 400)
            .slice(0, 60);

        const inputValues = q('input, textarea').map(el => ({
            id:          el.id || null,
            name:        el.name || null,
            placeholder: el.placeholder || null,
            value:       el.value?.trim() || null,
        })).filter(el => el.value);

        const selects = q('mat-select').map(sel => ({
            id:    sel.id || null,
            value: sel.querySelector('.mat-select-value-text')?.innerText?.trim() || null,
            label: sel.closest('mat-form-field')?.querySelector('mat-label, label')?.innerText?.trim() || null,
        })).filter(s => s.value);

        const cards = q('mat-card, mat-expansion-panel, [class*="card"], [class*="patient"]')
            .map(c => c.innerText?.trim())
            .filter(t => t && t.length > 10 && t.length < 1000)
            .slice(0, 20);

        return { fields, dataRows, inputValues, selects, cards };
    }""")

    show("PATIENT DATA — mat-form-field pairs", data["fields"])
    show("PATIENT DATA — input values",         data["inputValues"])
    show("PATIENT DATA — dropdowns",            data["selects"])
    show("PATIENT DATA — data rows",            data["dataRows"])
    show("PATIENT DATA — cards / panels",       data["cards"])

    return data

# ---------------------------------------------------------------------------
# Extended CCHI fields — hidden panel (add_circle_outline expand icon)
# ---------------------------------------------------------------------------

def capture_extended_cchi_fields(page: Page) -> dict:
    """
    Click the add_circle_outline mat-icon on the CCHI beneficiary page to expand
    the hidden supplementary info panel, then read 6 additional fields:
      patient_share_pct, max_limit_amount, issue_date,
      network_id, sponsor_number, policy_class_name.

    The icon element is: <mat-icon ...>add_circle_outline</mat-icon>
    Clicking it (or its closest clickable parent) reveals new input textboxes.

    Returns a dict with the 6 keys, empty string for any field not found.
    Safe to call even when the icon is absent (returns empty dict gracefully).
    """
    empty = {
        "patient_share_pct":  "",
        "max_limit_amount":   "",
        "issue_date":         "",
        "network_id":         "",
        "sponsor_number":     "",
        "policy_class_name":  "",
    }

    # Locate and JS-click the expand icon (aria-hidden="true" makes direct Playwright
    # click unreliable — use JS to find the closest clickable ancestor)
    clicked = page.evaluate(r"""() => {
        const icon = [...document.querySelectorAll('mat-icon')]
                         .find(i => i.textContent.trim() === 'add_circle_outline');
        if (!icon) return false;
        const target = icon.closest('button, a, [role="button"], [tabindex]')
                    || icon.parentElement
                    || icon;
        target.click();
        return true;
    }""")

    if not clicked:
        print("  [INFO] add_circle_outline icon not found — extended fields skipped")
        return empty

    page.wait_for_timeout(600)   # Angular expand animation
    print("  [+] Extended info panel expanded")

    # Scrape label → value from mat-form-fields and input placeholders
    raw_labels = page.evaluate(r"""() => {
        const out = {};
        for (const field of document.querySelectorAll('mat-form-field')) {
            const labelEl = field.querySelector('mat-label, label');
            const inputEl = field.querySelector('input');
            if (!labelEl || !inputEl) continue;
            const label = labelEl.innerText.trim();
            const value = inputEl.value.trim();
            if (label) out[label] = value;
        }
        // Fallback: placeholder → value for inputs not inside mat-form-field
        for (const inp of document.querySelectorAll('input')) {
            const ph = (inp.placeholder || '').trim();
            const val = (inp.value || '').trim();
            if (ph && !(ph in out)) out[ph] = val;
        }
        return out;
    }""")

    print(f"  [DEBUG] Extended raw labels: {raw_labels}")

    def pick(keys: list) -> str:
        """Return the first value whose label contains any of the key substrings (case-insensitive)."""
        for label, value in raw_labels.items():
            ll = label.lower()
            for k in keys:
                if k.lower() in ll:
                    return value
        return ""

    result = {
        "patient_share_pct":  pick(["patient share", "share %", "share("]),
        "max_limit_amount":   pick(["max limit", "maximum limit", "limit amount"]),
        "issue_date":         pick(["issue date"]),
        "network_id":         pick(["network id", "network"]),
        "sponsor_number":     pick(["sponsor number", "sponsor no", "sponsor"]),
        "policy_class_name":  pick(["policy class", "class name"]),
    }

    print(f"  [+] Extended CCHI fields: {result}")
    return result


# ---------------------------------------------------------------------------
# Patient summary table
# ---------------------------------------------------------------------------

def _extract_table_rows(raw: dict) -> list:
    """Map known placeholders / mat-select IDs to labelled display rows."""

    def inp(placeholder_sub: str) -> str:
        for x in raw.get("inputValues", []):
            ph = (x.get("placeholder") or "").lower()
            if placeholder_sub.lower() in ph:
                return x.get("value") or ""
        return ""

    def sel(id_str: str) -> str:
        for x in raw.get("selects", []):
            if x.get("id") == id_str:
                return x.get("value") or ""
        return ""

    def sel_label(label_sub: str) -> str:
        """Find a mat-select by its form-field label text (robust vs Angular ID shifts)."""
        for x in raw.get("selects", []):
            lbl = (x.get("label") or "").lower()
            if label_sub.lower() in lbl:
                return x.get("value") or ""
        return ""

    # Split payer into English (before first Arabic char) + Arabic
    # Primary: label-based (stable across sessions); fallback: mat-select-14 (may shift)
    # mat-select-15 fallback covers TPA patients where mat-select-14 stays empty after Rule 3-1
    payer_raw = sel_label("insurance") or sel_label("payer") or sel("mat-select-14") or sel("mat-select-15") or ""
    arabic_idx = next(
        (i for i, c in enumerate(payer_raw) if "\u0600" <= c <= "\u06FF"),
        len(payer_raw),
    )
    payer_en = payer_raw[:arabic_idx].strip()
    payer_ar = payer_raw[arabic_idx:].strip()

    return [
        ("Full Name",       inp("full name")),
        ("Document ID",     inp("document id")),
        ("Insurance Payer", payer_en),
        ("Payer (Arabic)",  payer_ar),
        ("Member Card ID",  inp("member card id")),
        ("Policy Number",   inp("policy number")),
        ("Policy Holder",   inp("policy holder")),
        ("Expiry Date",     inp("expiry date")),
        ("Relation",        sel("mat-select-13")),
        ("ID Type",         sel("mat-select-0")),
    ]


def print_patient_table(raw: dict) -> None:
    """Print key patient fields in a bordered ASCII table."""
    rows = _extract_table_rows(raw)
    c1 = max([len("Field")] + [len(f) for f, _ in rows]) + 2
    c2 = max([len("Value")] + [len(v) for _, v in rows]) + 2

    def hline(lc, mc, rc):
        return f"  {lc}{'─'*c1}{mc}{'─'*c2}{rc}"

    print(hline("┌", "┬", "┐"))
    print(f"  │{'Field':^{c1}}│{'Value':^{c2}}│")
    print(hline("├", "┼", "┤"))
    for i, (f, v) in enumerate(rows):
        print(f"  │ {f:<{c1-2}} │ {v:<{c2-2}} │")
        if i < len(rows) - 1:
            print(hline("├", "┼", "┤"))
    print(hline("└", "┴", "┘"))

# ---------------------------------------------------------------------------
# Rule 2: "No record found" dialog detection
# ---------------------------------------------------------------------------

def check_no_record_dialog(page: Page) -> bool:
    """Rule 2: Returns True if 'No record found' dialog is present on page."""
    return page.locator("mat-dialog-container").filter(
        has_text=re.compile(r"No record found", re.IGNORECASE)
    ).count() > 0


# ---------------------------------------------------------------------------
# Rule 3-1: Multiple TPA payer auto-fix (runs on CCHI page before AddEligibility)
# ---------------------------------------------------------------------------

def fix_multiple_tpa_payer(page: Page) -> bool:
    """
    Rule 3-1: The Insurance Plans section shows an error_outline icon because
    the payer belongs to Multiple TPAs and cannot be auto-selected by CCHI.

    Detection : mat-icon.error-icon present inside app-nphies-payers-selector
    Fix       : Read the ARIA message attached to the error icon, identify the
                TPA from the known map, open the dropdown ONCE, select the option
                at the known 1-based index (option 1 = "Select Payer" placeholder).

    Known TPA map (extend as new TPAs are discovered):
      "MedGulf"   → option 6  (The Mediterranean and Gulf Cooperative...)
      "Al-Etihad" → option 2  (Al-Etihad Cooperative Insurance Company)

    Falls back to name-based text match for TPAs not in the map.

    Called from run_cchi_inquiry() after first capture.
    Returns True if an option was successfully selected, False otherwise.
    """
    if page.locator("mat-icon.error-icon").count() == 0:
        return False

    login.sep("RULE 3-1 — MULTIPLE TPA PAYER FIX")

    # Read the ARIA description attached to the error icon
    aria_msg = page.evaluate(r"""() => {
        const icon = document.querySelector('mat-icon.error-icon[aria-describedby]');
        if (!icon) return null;
        const el = document.getElementById(icon.getAttribute('aria-describedby'));
        return el ? el.textContent.trim() : null;
    }""")

    if not aria_msg:
        print("  [!] error-icon found but ARIA message empty — cannot auto-fix")
        return False

    print(f"  [INFO] ARIA message: {aria_msg!r}")

    # ── Known TPA → option number mapping ────────────────────────────────────
    # Option 1 is the "Select Payer" placeholder; payers start at option 2.
    # Add new entries here as new TPAs are encountered.
    TPA_OPTION_MAP = {
        "MedGulf":   6,   # The Mediterranean and Gulf Cooperative Insurance... (MedGulf)
        "Al-Etihad": 2,   # Al-Etihad Cooperative Insurance Company
    }

    option_num = None   # 1-based
    matched_keyword = None
    for keyword, num in TPA_OPTION_MAP.items():
        if keyword.lower() in aria_msg.lower():
            option_num = num
            matched_keyword = keyword
            print(f"  [INFO] TPA identified as {keyword!r} → option {num}")
            break

    # ── Open the dropdown (once) ──────────────────────────────────────────────
    page.locator("app-nphies-payers-selector mat-select").first.click()
    page.wait_for_selector("mat-option", timeout=5000)
    page.wait_for_timeout(200)

    if option_num is not None:
        # Select by known index (0-based = option_num - 1); scoped to visible overlay
        selected_text = page.evaluate(rf"""() => {{
            const panel = document.querySelector(
                '.cdk-overlay-container .mat-select-panel, ' +
                '.cdk-overlay-container .mat-mdc-select-panel, ' +
                '.cdk-overlay-container [role="listbox"]'
            );
            const opts = panel
                ? [...panel.querySelectorAll('mat-option')]
                : [...document.querySelectorAll('mat-option')].filter(o => o.offsetParent);
            const t = opts[{option_num - 1}];
            if (t) {{ t.scrollIntoView({{block: 'center'}}); t.click(); return t.textContent.trim(); }}
            return null;
        }}""")
        ok = bool(selected_text)
        if ok:
            print(f"  [+] Option {option_num} selected: {(selected_text or '')[:80]!r}")
        else:
            print(f"  [!] Option index {option_num} not found in dropdown")
    else:
        # ── Fallback: name-based CONTAINS match for unknown TPAs ──────────────
        m = re.match(r'^(.+?)\s+is part of Multiple TPAs', aria_msg, re.IGNORECASE)
        if not m:
            print("  [!] Cannot parse TPA name from ARIA message — skipping fix")
            page.keyboard.press("Escape")
            return False
        payer_name = m.group(1).strip()
        print(f"  [INFO] Unknown TPA — attempting name match for {payer_name!r}")
        esc = re.escape(payer_name)
        ok = page.evaluate(rf"""() => {{
            const p = document.querySelector(
                '.mat-select-panel, .mat-mdc-select-panel, [class*="select-panel"]'
            );
            if (p) p.scrollTop = p.scrollHeight;
            const t = [...document.querySelectorAll('mat-option')]
                          .find(o => /{esc}/i.test(o.textContent.trim()));
            if (t) {{ t.scrollIntoView({{block: 'center'}}); t.click(); return true; }}
            return false;
        }}""")
        if ok:
            print(f"  [+] Payer matched by name: {payer_name!r}")
        else:
            print(f"  [!] No option matched {payer_name!r} — add to TPA_OPTION_MAP")

    page.wait_for_timeout(300)
    return ok


# ---------------------------------------------------------------------------
# Rule 3-1 helper: read the currently selected option index from the payer
# dropdown (mat-select-15 / app-nphies-payers-selector mat-select).
# Call AFTER fix_multiple_tpa_payer() so the value reflects any Rule 3-1 fix.
# Also works when the payer was pre-filled by CCHI (no error icon).
# ---------------------------------------------------------------------------

def get_payer_option_index(page: Page) -> int | None:
    """
    Opens app-nphies-payers-selector mat-select, finds the currently selected
    option's 1-based index (option 1 = "Select Payer" placeholder), then closes
    the dropdown.

    Returns the index (int >= 2) if a payer is selected, None if the dropdown
    still shows the placeholder or the element is not found on the page.
    """
    sel_loc = page.locator("app-nphies-payers-selector mat-select").first
    if sel_loc.count() == 0:
        print("  [INFO] app-nphies-payers-selector not found — skipping index read")
        return None

    sel_loc.click()
    try:
        page.wait_for_selector("mat-option", timeout=3000)
    except Exception:
        page.keyboard.press("Escape")
        print("  [INFO] Payer dropdown did not open — skipping index read")
        return None
    page.wait_for_timeout(150)

    idx = page.evaluate(r"""() => {
        const panel = document.querySelector(
            '.cdk-overlay-container .mat-select-panel, ' +
            '.cdk-overlay-container .mat-mdc-select-panel, ' +
            '.cdk-overlay-container [role="listbox"]'
        );
        const opts = panel
            ? [...panel.querySelectorAll('mat-option')]
            : [...document.querySelectorAll('mat-option')].filter(o => o.offsetParent);
        const i = opts.findIndex(
            o => o.classList.contains('mat-selected') ||
                 o.classList.contains('mat-mdc-option-active') ||
                 o.getAttribute('aria-selected') === 'true'
        );
        return i >= 0 ? i + 1 : null;   // 1-based; null = placeholder still shown
    }""")

    page.keyboard.press("Escape")
    page.wait_for_timeout(150)

    if idx:
        print(f"  [INFO] Payer option index (mat-select-15): {idx}")
    else:
        print("  [INFO] Payer option index: none selected (placeholder)")
    return idx


# ---------------------------------------------------------------------------
# Core inquiry flow — importable entry point
# ---------------------------------------------------------------------------

def run_cchi_inquiry(page: Page, id_number: str = None, verbose: bool = True) -> dict:
    """
    Navigate to CCHI beneficiary page, enter id_number, click Inquire CCHI,
    wait for results, and return capture_patient_data() dict.
    Importable by AddEligibility.py and other scripts.
    verbose=False suppresses the heavy detect_page_elements dumps (used for multi-ID runs).
    """
    if id_number is None:
        id_number = ID_NUMBER

    login.sep("NAVIGATING TO CCHI PAGE")
    page.goto(CCHI_URL, wait_until="load", timeout=30000)
    login.wait_stable(page)
    print(f"  URL   : {page.url}")
    print(f"  Title : {page.title()}")

    if verbose:
        detect_page_elements(page, "CCHI PAGE — initial")

    login.sep("LOCATING SEARCH INPUT & BUTTON")
    search_input = page.locator(
        'input[placeholder*="national ID"], '
        'input[placeholder*="iqama"], '
        'input[placeholder*="National ID"], '
        'input[placeholder*="Iqama"]'
    ).first
    search_input.wait_for(timeout=15000)
    print("  [+] Search input found")

    inquire_btn = page.locator("button").filter(
        has_text=re.compile(r"Inquire\s+CCHI", re.IGNORECASE)
    ).first
    print("  [+] Inquire CCHI button found")

    login.sep("SUBMITTING INQUIRY")
    search_input.clear()
    search_input.fill(id_number)
    print(f"  [+] ID filled: {id_number}")

    inquire_btn.click()
    print("  [+] Inquire CCHI clicked — waiting for response ...")
    login.wait_stable(page)
    # Dual-condition wait: resolves as soon as EITHER the full-name fills (insured)
    # OR the 'No record found' dialog appears (uninsured — Rule 2).
    # Replaces two sequential timeouts that always expired for NO_INSURANCE IDs:
    #   old: 5s name timeout + 800ms fallback + 2s payer timeout + 300ms fallback = ~8.1s wasted
    #   new: resolves at dialog appearance (~2-3s); saves ~5s per NO_INSURANCE ID.
    try:
        page.wait_for_function(
            """() => {
                const nameOk = (() => {
                    const inp = document.querySelector(
                        'input[placeholder*="full name"], input[placeholder*="Full Name"]'
                    );
                    return inp && inp.value && inp.value.trim().length > 0;
                })();
                const dialogOk = document.querySelector('mat-dialog-container') !== null;
                return nameOk || dialogOk;
            }""",
            timeout=8000,
        )
    except Exception:
        page.wait_for_timeout(300)
    # Only wait for payer dropdown when the patient has insurance.
    # Skips the 2s payer timeout for NO_INSURANCE IDs (dialog already confirmed above).
    if not check_no_record_dialog(page):
        try:
            page.wait_for_function(
                """() => {
                    const sels = [...document.querySelectorAll('mat-select')];
                    return sels.some(s => {
                        const lbl = (s.closest('mat-form-field')
                            ?.querySelector('mat-label,label')
                            ?.innerText || '').toLowerCase();
                        return (lbl.includes('insurance') || lbl.includes('payer'))
                            && s.querySelector('.mat-select-value-text')
                                   ?.innerText?.trim().length > 0;
                    });
                }""",
                timeout=2000,
            )
        except Exception:
            page.wait_for_timeout(300)

    if verbose:
        detect_page_elements(page, "CCHI PAGE — after inquiry")

    login.sep("CAPTURING PATIENT DATA")
    raw = capture_patient_data(page)

    # Rule 3-1: if error icon present, fix payer then re-capture with corrected data
    if fix_multiple_tpa_payer(page):
        login.sep("RE-CAPTURING PATIENT DATA (after Rule 3-1 payer fix)")
        raw = capture_patient_data(page)

    # Extended fields: click add_circle_outline to expand hidden panel
    login.sep("CAPTURING EXTENDED CCHI FIELDS")
    raw["extended"] = capture_extended_cchi_fields(page)

    return raw

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"\n  CheckCCHI — Beneficiary Inquiry")
    print(f"  {'='*45}")
    print(f"  ID Number : {ID_NUMBER}")

    valid, reason = validate_id(ID_NUMBER)
    if not valid:
        print(f"\n[SKIP] '{ID_NUMBER}' — {reason}")
        return

    with sync_playwright() as pw:
        browser, context, page = login.get_logged_in_page(pw)

        raw = run_cchi_inquiry(page)

        login.sep("PATIENT SUMMARY")
        print_patient_table(raw)

        print(f"\n  {'='*45}")
        print("  Inquiry complete.")
        print(f"  {'='*45}")
        if not login.is_remote():
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
