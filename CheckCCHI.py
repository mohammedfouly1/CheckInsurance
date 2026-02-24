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

def sep(label: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")


def show(title: str, data) -> None:
    sep(title)
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(data)

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
    payer_raw = sel_label("insurance") or sel_label("payer") or sel("mat-select-14") or ""
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

    sep("NAVIGATING TO CCHI PAGE")
    page.goto(CCHI_URL, wait_until="load", timeout=30000)
    login.wait_stable(page)
    print(f"  URL   : {page.url}")
    print(f"  Title : {page.title()}")

    if verbose:
        detect_page_elements(page, "CCHI PAGE — initial")

    sep("LOCATING SEARCH INPUT & BUTTON")
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

    sep("SUBMITTING INQUIRY")
    search_input.clear()
    search_input.fill(id_number)
    print(f"  [+] ID filled: {id_number}")

    inquire_btn.click()
    print("  [+] Inquire CCHI clicked — waiting for response ...")
    login.wait_stable(page)
    # Wait for the full-name field to be populated (CCHI data loaded) or fallback 800ms
    try:
        page.wait_for_function(
            """() => {
                const inp = document.querySelector(
                    'input[placeholder*="full name"], input[placeholder*="Full Name"]'
                );
                return inp && inp.value && inp.value.trim().length > 0;
            }""",
            timeout=5000,
        )
    except Exception:
        page.wait_for_timeout(800)

    if verbose:
        detect_page_elements(page, "CCHI PAGE — after inquiry")

    sep("CAPTURING PATIENT DATA")
    return capture_patient_data(page)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"\n  CheckCCHI — Beneficiary Inquiry")
    print(f"  {'='*45}")
    print(f"  ID Number : {ID_NUMBER}")

    with sync_playwright() as pw:
        browser, context, page = login.get_logged_in_page(pw)

        raw = run_cchi_inquiry(page)

        sep("PATIENT SUMMARY")
        print_patient_table(raw)

        print(f"\n  {'='*45}")
        print("  Inquiry complete.")
        print(f"  {'='*45}")
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
