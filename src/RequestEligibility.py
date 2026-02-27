"""
RequestEligibility.py — Phase 3 Eligibility Request (production version)

Confirmed selectors (validated 2026-02-24):
  Element 1 — Discovery radio   : mat-radio-button CONTAINS text "Discovery"
  Element 2 — Insurance Plan    : JS last mat-select on page  ← becomes "Select Payer"
                                  after Discovery click; mat-select#insurancePlan removed
  Element 3 — Request button    : id="requestEligibilty"  (app typo: single 'i')

Input  : ID_NUMBER in .env — single value OR comma-separated list (brackets optional)
           e.g.  ID_NUMBER=2309901342
           e.g.  ID_NUMBER=1086242508,2347902641,1090270735
           e.g.  ID_NUMBER=[1086242508, 2347902641, 1090270735]

Output : Per-ID record saved to eligibility_results.csv
         Fields: Full Name · Insurance Payer · Policy Holder · Expiry Date
                 Site Eligibility · Outcome · Disposition

Run: python src/RequestEligibility.py
"""

import csv
import io
import os
import re
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page

import login
import CheckCCHI
import AddEligibility

from config import TIMEOUT_ELEMENT

load_dotenv()

# ---------------------------------------------------------------------------
# Input: ID list parsing
# ---------------------------------------------------------------------------

def parse_id_list() -> list:
    """
    Parse ID_NUMBER from .env.
    Supports: "2309901342"
              "1086242508,2347902641,1090270735"
              "[1086242508, 2347902641, 1090270735]"
    """
    raw = os.environ.get("ID_NUMBER", "").strip()
    ids = [x.strip() for x in raw.strip("[]").split(",") if x.strip()]
    if not ids:
        raise ValueError("ID_NUMBER is not set or empty in .env")
    return ids


# ---------------------------------------------------------------------------
# Rule 1: ID validation — single source of truth in CheckCCHI.validate_id()
# ---------------------------------------------------------------------------

validate_id = CheckCCHI.validate_id


# ---------------------------------------------------------------------------
# Element 1: Discovery radio
#
# Confirmed selector  : mat-radio-button whose visible text CONTAINS "Discovery"
# Effect after click  : Angular reactively removes mat-select#insurancePlan
#                       and replaces it with a new mat-select ("Select Payer")
# ---------------------------------------------------------------------------

def click_discovery(page: Page) -> None:
    login.sep("DISCOVERY RADIO")
    loc = page.locator("mat-radio-button").filter(
        has_text=re.compile(r"Discovery", re.IGNORECASE)
    ).first
    loc.wait_for(state="visible", timeout=TIMEOUT_ELEMENT)   # wait for Angular to render it
    loc.scroll_into_view_if_needed(timeout=5000)
    loc.click(timeout=4000)
    page.wait_for_timeout(500)                      # reduced from wait_stable+800ms
    print("  [+] Discovery clicked — Insurance Plan dropdown cleared by Angular")


# ---------------------------------------------------------------------------
# Element 2: Insurance Plan dropdown
#
# Confirmed selector  : JS click on LAST mat-select on page
# Note                : Must be called AFTER click_discovery().
#                       mat-select#insurancePlan is removed post-Discovery;
#                       the replacement is the last mat-select ("Select Payer").
# Option matching     : CONTAINS payer_en  (option text includes member ID + status)
# Fallback            : first non-empty option if payer not found
# ---------------------------------------------------------------------------

def select_insurance_plan(page: Page, payer_en: str,
                          payer_option_idx: int = None) -> str:
    """Select the insurance payer from 'Select Payer' dropdown (post-Discovery).
    Returns the selected option text (trimmed), or empty string if none selected."""
    login.sep(f"INSURANCE PLAN — {payer_en!r}")

    # Open the last mat-select ("Select Payer" after Discovery click)
    page.evaluate(r"""() => {
        const all = [...document.querySelectorAll('mat-select')];
        if (all.length > 0) all[all.length - 1].click();
    }""")
    page.wait_for_selector("mat-option", timeout=5000)
    page.wait_for_timeout(200)

    selected_payer_text = ""
    ok = False

    if payer_option_idx is not None:
        # Primary: use the same option index as the CCHI page mat-select-15.
        # Scoped to the CDK overlay panel to avoid matching options from other dropdowns.
        txt = page.evaluate(rf"""() => {{
            const panel = document.querySelector(
                '.cdk-overlay-container .mat-select-panel, ' +
                '.cdk-overlay-container .mat-mdc-select-panel, ' +
                '.cdk-overlay-container [role="listbox"]'
            );
            const opts = panel
                ? [...panel.querySelectorAll('mat-option')]
                : [...document.querySelectorAll('mat-option')].filter(o => o.offsetParent);
            const t = opts[{payer_option_idx - 1}];
            if (t) {{ t.scrollIntoView({{block: 'center'}}); t.click(); return t.textContent.trim(); }}
            return null;
        }}""")
        page.wait_for_timeout(150)
        if txt:
            selected_payer_text = txt
            print(f"  [+] Option {payer_option_idx} selected: {(txt or '')[:80]!r}")
            ok = True
        else:
            print(f"  [!] Option index {payer_option_idx} not found — falling back to name match")

    if not ok:
        # Fallback: CONTAINS text match on English payer name.
        # Guard: if payer_en is empty, re.escape("") produces "" which makes
        # the JS regex literal //i — a line comment — causing a SyntaxError.
        # Skip name match entirely and jump straight to first-option fallback.
        if payer_en:
            esc = re.escape(payer_en)
            page.evaluate(r"""() => {
                const p = document.querySelector(
                    '.mat-select-panel, .mat-mdc-select-panel, [class*="select-panel"]'
                );
                if (p) p.scrollTop = p.scrollHeight;
            }""")
            page.wait_for_timeout(150)
            txt = page.evaluate(rf"""() => {{
                const p = document.querySelector(
                    '.mat-select-panel, .mat-mdc-select-panel, [class*="select-panel"]'
                );
                if (p) p.scrollTop = p.scrollHeight;
                const t = [...document.querySelectorAll('mat-option')]
                              .find(o => /{esc}/i.test(o.textContent.trim()));
                if (t) {{ t.scrollIntoView({{block:'center'}}); t.click(); return t.textContent.trim(); }}
                return null;
            }}""")
            page.wait_for_timeout(150)
            if txt:
                selected_payer_text = txt
                ok = True
                print(f"  [+] Selected by name: {payer_en!r}")
        else:
            print("  [!] payer_en is empty — skipping name match")

        if not ok:
            fb = page.evaluate(r"""() => {
                const opts = [...document.querySelectorAll('mat-option')]
                                 .filter(o => o.textContent.trim().length > 0);
                if (opts.length > 0) { opts[0].click(); return opts[0].textContent.trim(); }
                return null;
            }""")
            page.wait_for_timeout(150)
            if fb:
                selected_payer_text = fb
            print(f"  [!] Payer not found — fallback selected: {fb!r}")

    return selected_payer_text


# ---------------------------------------------------------------------------
# Element 3: Request Eligibility button
#
# Confirmed selector  : id="requestEligibilty"  (app typo: single 'i')
# Post-click behavior : Result renders in-place on same URL (Angular SPA);
#                       URL change may NOT occur — that is expected.
# ---------------------------------------------------------------------------

def click_request_eligibility(page: Page) -> None:
    login.sep("REQUEST ELIGIBILITY")
    url_before = page.url
    loc = page.locator("#requestEligibilty")
    loc.wait_for(timeout=TIMEOUT_ELEMENT)
    print(f"  [+] Button: {loc.inner_text().strip()!r}")
    loc.click(timeout=5000)
    print("  [+] Clicked — waiting for result ...")
    try:
        page.wait_for_url(lambda url: url != url_before, timeout=15000)
        print("  [+] Navigation detected")
    except Exception:
        print("  [!] URL unchanged — result rendered in-place (expected for this app)")
    login.wait_stable(page)
    page.wait_for_timeout(300)


# ---------------------------------------------------------------------------
# Output extraction
# ---------------------------------------------------------------------------

_RESULT_LABEL_MAP = {
    "Site Eligibility": "site_eligibility",
    "Outcome":          "outcome",
    "Disposition":      "disposition",
}

_CCHI_FIELD_MAP = {
    "Full Name":       "full_name",
    "Insurance Payer": "insurance_payer",
    "Policy Holder":   "policy_holder",
    "Expiry Date":     "expiry_date",
}


def extract_eligibility_result(page: Page) -> dict:
    """
    Parse Site Eligibility, Outcome, Disposition from the result card.
    Card text structure: label<LF><LF>value<LF><LF>next-label ...
    """
    result = {v: "" for v in _RESULT_LABEL_MAP.values()}
    try:
        card_texts = page.evaluate(r"""() =>
            [...document.querySelectorAll(
                'mat-card, mat-expansion-panel, [class*="card"], [class*="patient"]'
            )].map(c => (c.innerText || '').trim())
              .filter(t => t.includes('Site Eligibility'))
        """)
        if not card_texts:
            print("  [!] No result card found — eligibility data not extracted")
            return result
        parts = [p.strip() for p in card_texts[0].split("\n\n") if p.strip()]
        for i, part in enumerate(parts):
            key = _RESULT_LABEL_MAP.get(part)
            if key and i + 1 < len(parts):
                result[key] = parts[i + 1]
        print(f"  [+] Eligibility: {result}")
    except Exception as e:
        print(f"  [!] extract_eligibility_result error: {e}")
    return result


def extract_cchi_fields(raw: dict) -> dict:
    """Extract CCHI output fields (basic 4 + 6 extended) from the capture dict."""
    row_dict = dict(CheckCCHI._extract_table_rows(raw))
    result = {dest: row_dict.get(src, "") for src, dest in _CCHI_FIELD_MAP.items()}
    # Extended fields stored by capture_extended_cchi_fields under raw["extended"]
    result.update(raw.get("extended", {}))
    return result


# ---------------------------------------------------------------------------
# Output: display + CSV
# ---------------------------------------------------------------------------

_OUTPUT_COLS = [
    ("id",                "ID"),
    ("full_name",         "Full Name"),
    ("insurance_payer",   "Insurance Payer"),
    ("policy_holder",     "Policy Holder"),
    ("expiry_date",       "Expiry Date"),
    # Extended CCHI fields (from hidden add_circle_outline panel)
    ("patient_share_pct", "Patient Share %"),
    ("max_limit_amount",  "Max Limit Amount"),
    ("issue_date",        "Issue Date"),
    ("network_id",        "Network ID"),
    ("sponsor_number",    "Sponsor Number"),
    ("policy_class_name", "Policy Class Name"),
    # Eligibility result
    ("site_eligibility",  "Site Eligibility"),
    ("outcome",           "Outcome"),
    ("disposition",       "Disposition"),
    ("error",             "Error"),
]


def _blank_record(id_number: str) -> dict:
    rec = {k: "" for k, _ in _OUTPUT_COLS}
    rec["id"] = id_number
    return rec


def print_id_card(record: dict) -> None:
    """Print one ID result as a vertical labeled card."""
    login.sep(f"RESULT — ID {record['id']}")
    lw = max(len(lbl) for _, lbl in _OUTPUT_COLS) + 2
    for key, lbl in _OUTPUT_COLS:
        val = record.get(key, "")
        if val:
            print(f"  {lbl:<{lw}} {val}")


def print_summary_table(results: list) -> None:
    """Print all results as a compact ASCII table (values capped at 32 chars)."""
    if not results:
        return
    CAP = 32

    def t(s):
        s = str(s or "")
        return s if len(s) <= CAP else s[:CAP - 1] + "…"

    keys   = [k for k, _ in _OUTPUT_COLS]
    labels = [l for _, l in _OUTPUT_COLS]
    col_w  = [
        max(len(lbl), max(len(t(r.get(k, ""))) for r in results)) + 2
        for k, lbl in _OUTPUT_COLS
    ]

    def hline(lc, mc, rc):
        return "  " + lc + mc.join("─" * w for w in col_w) + rc

    login.sep("SUMMARY TABLE")
    print(hline("┌", "┬", "┐"))
    print("  │" + "│".join(f" {l:^{w-2}} " for l, w in zip(labels, col_w)) + "│")
    print(hline("├", "┼", "┤"))
    for row in results:
        print("  │" + "│".join(
            f" {t(row.get(k, '')):^{w-2}} " for k, w in zip(keys, col_w)
        ) + "│")
    print(hline("└", "┴", "┘"))


def save_results_csv(results: list) -> None:
    """Append results to eligibility_results.csv; creates with header on first run."""
    filename = "testing/eligibility_results.csv"
    keys   = [k for k, _ in _OUTPUT_COLS]
    labels = [l for _, l in _OUTPUT_COLS]
    exists = Path(filename).exists()
    with open(filename, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["run_at"] + keys, extrasaction="ignore")
        if not exists:
            writer.writerow(dict(zip(["run_at"] + keys, ["Run At"] + labels)))
        run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for row in results:
            writer.writerow({"run_at": run_at, **{k: row.get(k, "") for k in keys}})
    print(f"  [+] Results appended → {filename}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    id_list = parse_id_list()

    print(f"\n  RequestEligibility — {len(id_list)} ID(s)")
    print(f"  IDs : {id_list}")
    print(f"  {'='*55}")

    results = []

    with sync_playwright() as pw:

        browser, context, page = login.get_logged_in_page(pw)

        for idx, id_number in enumerate(id_list, 1):
            print(f"\n  {'─'*55}")
            print(f"  Processing {idx}/{len(id_list)}: {id_number}")
            print(f"  {'─'*55}")

            # --- Rule 1: ID format + checksum validation ---
            valid, reason = validate_id(id_number)
            if not valid:
                print(f"\n[SKIP] '{id_number}' — {reason}")
                record = {
                    "id": id_number,
                    "full_name": "INVALID_ID",
                    "insurance_payer": "INVALID_ID",
                    "policy_holder": "INVALID_ID",
                    "expiry_date": "INVALID_ID",
                    "site_eligibility": "INVALID_ID",
                    "outcome": "INVALID_ID",
                    "disposition": "INVALID_ID",
                    "error": f"Invalid ID: {reason}",
                }
                results.append(record)
                continue

            record = _blank_record(id_number)

            try:
                # ── CCHI inquiry ──────────────────────────────────────────────
                raw = CheckCCHI.run_cchi_inquiry(page, id_number, verbose=False)

                CheckCCHI.print_patient_table(raw)
                cchi = extract_cchi_fields(raw)
                record.update(cchi)
                payer_en = cchi["insurance_payer"]

                # ── Rule 2: "No record found" dialog → no active insurance ───
                no_record = CheckCCHI.check_no_record_dialog(page)
                if no_record:
                    print(f"  [SKIP] ID {id_number} — 'No record found' dialog detected (no active insurance plan)")
                    record.update({
                        "insurance_payer":  "NO_INSURANCE",
                        "policy_holder":    "NO_INSURANCE",
                        "expiry_date":      "NO_INSURANCE",
                        "site_eligibility": "NO_INSURANCE",
                        "outcome":          "NO_INSURANCE",
                        "disposition":      "NO_INSURANCE",
                        "error":            "No insurance plans found",
                    })

                else:
                    print(f"  [INFO] Payer cached for re-selection: {payer_en!r}")

                    # ── Read payer option index from CCHI page ────────────────
                    # Works for pre-filled payers and for Rule 3-1 fixed payers.
                    # Used for index-based selection on the eligibility page.
                    payer_option_idx = CheckCCHI.get_payer_option_index(page)

                    # ── Rule 3-3 L1: TPA payer still unresolved → skip ────────
                    # If the error-icon is still present after Rule 3-1 attempt,
                    # the payer is unknown — proceeding would reject the form and
                    # leave the URL stuck on /beneficiary/add indefinitely.
                    if not payer_en and page.locator("mat-icon.error-icon").count() > 0:
                        print(f"  [SKIP] ID {id_number} — TPA payer unresolved after Rule 3-1 attempt")
                        record["error"] = "TPA payer unresolved — Add & Apply skipped (error-icon still present)"
                        results.append(record)
                        continue

                    # ── Add & Apply Eligibility ───────────────────────────────
                    AddEligibility.run_add_eligibility(page)

                    # ── Rule 3-3 L2: Verify navigation reached eligibility URL ─
                    # If the form was rejected, URL stays on /beneficiary/add.
                    # Calling click_discovery on the wrong page causes cascade errors.
                    current_url = page.url
                    if "eligibility" not in current_url:
                        print(f"  [SKIP] ID {id_number} — navigation stuck after Add & Apply: {current_url[:100]}")
                        record["error"] = f"Navigation stuck — expected /eligibility, got: {current_url[:100]}"
                        results.append(record)
                        continue

                    # ── Discovery radio ───────────────────────────────────────
                    click_discovery(page)

                    # ── Insurance Plan dropdown ───────────────────────────────
                    selected_text = select_insurance_plan(page, payer_en, payer_option_idx)
                    # Backfill insurance_payer when CCHI table left it empty (TPA patients /
                    # fast responses where payer dropdown hadn't loaded during capture).
                    # Strip Arabic portion so only English name is stored.
                    if selected_text and not record.get("insurance_payer"):
                        ar_idx = next(
                            (i for i, c in enumerate(selected_text) if "\u0600" <= c <= "\u06FF"),
                            len(selected_text)
                        )
                        backfilled = selected_text[:ar_idx].strip()
                        if backfilled:
                            record["insurance_payer"] = backfilled
                            print(f"  [INFO] insurance_payer backfilled: {backfilled!r}")

                    # ── Request Eligibility button ────────────────────────────
                    click_request_eligibility(page)

                    # ── Extra settle before extract ───────────────────────────
                    # Angular result card animation takes ~500ms to fully populate
                    # all fields. Without this wait, disposition was empty on 2/22 IDs.
                    page.wait_for_timeout(500)

                    # ── Extract result ────────────────────────────────────────
                    elig = extract_eligibility_result(page)
                    record.update(elig)

            except Exception as exc:
                record["error"] = f"{exc.__class__.__name__}: {str(exc)[:100]}"
                print(f"  [ERROR] ID {id_number}: {record['error']}")

            print_id_card(record)
            results.append(record)

        # ── Final output ──────────────────────────────────────────────────────
        print_summary_table(results)
        save_results_csv(results)

        print(f"\n  {'='*55}")
        print("  RequestEligibility complete.")
        print(f"  {'='*55}")
        print("\n  Close the browser window to exit.")
        try:
            page.wait_for_event("close", timeout=120000)
        except Exception:
            pass
        browser.close()


if __name__ == "__main__":
    main()
