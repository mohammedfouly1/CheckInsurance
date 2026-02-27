# Waseel Automation — Claude Memory

This file is read by Claude Code at session start to restore project context.

---

## Project Purpose
Automate NPHIES eligibility checking on eclaims.waseel.com for Saudi health insurance.
Provider: MAKKAH PARK CLINIC · Platform: NPHIES

## Import Chain
```
src/login.py ← src/CheckCCHI.py ← src/AddEligibility.py ← src/RequestEligibility.py
```

## Run Command
```
cd "D:\Waseel Automation"
PYTHONIOENCODING=utf-8 venv/Scripts/python src/RequestEligibility.py
```

## Input
`.env` → `ID_NUMBER` — single or comma-separated (brackets optional):
```
ID_NUMBER=2309901342
ID_NUMBER=1086242508,2347902641,1090270735,2463423430,2102931983
```

## Output (13 fields per ID)
| Source | Fields |
|--------|--------|
| CCHI page (main) | Full Name · Insurance Payer · Policy Holder · Expiry Date |
| CCHI page (extended, hidden panel) | Patient Share % · Max Limit Amount · Issue Date · Network ID · Sponsor Number · Policy Class Name |
| Result card | Site Eligibility · Outcome · Disposition |

Extended fields are retrieved by clicking the `add_circle_outline` mat-icon on the CCHI page, which expands a hidden supplementary info panel. Timing cost: ~640ms per ID.

Saved to `testing/eligibility_results.csv` (appended) and printed as ASCII table.

---

## CONFIRMED HTML Selectors — DO NOT CHANGE WITHOUT RE-VALIDATING

Validated on **2026-02-24** against eclaims.waseel.com.

### Element 1 — Discovery radio button
```python
page.locator("mat-radio-button").filter(
    has_text=re.compile(r"Discovery", re.IGNORECASE)
).first.click()
```
- Underlying input: `id="isDiscovery-input"`, `name="mat-radio-group-0"`, `value="2"`
- **Critical side-effect**: Angular **removes** `mat-select#insurancePlan` from DOM
  and replaces it with a new last `mat-select` labelled "Select Payer"

### Element 2 — Insurance Plan dropdown
**Must be called AFTER Discovery.** `mat-select#insurancePlan` no longer exists.
```python
# select_insurance_plan(page, payer_en, payer_option_idx=None)
# Primary: index-based — same option position as mat-select-15 on CCHI page
opts[payer_option_idx - 1].click()   # scoped to CDK overlay panel
# Fallback: CONTAINS match on payer English name (only if index absent/fails)
# Last resort: first non-empty option (guards against empty payer_en → JS //i SyntaxError)
```
- Post-Discovery: last `mat-select` on page, label = "Select Payer"
- `payer_option_idx` is read from CCHI page by `CheckCCHI.get_payer_option_index()` before navigation
- Option text format: `"(Primary) Member ID: 002309901342001 (Gulf Union...) (Active)"`

### Element 3 — Request Eligibility button
```python
page.locator("#requestEligibilty")   # APP TYPO: single 'i' — confirmed, do not fix
```
- Post-click: result renders **in-place on same URL** (Angular SPA, no navigation event — expected)

### Other confirmed selectors
```python
# Add & Apply Eligibility
page.locator("button").filter(has_text=re.compile(r"Add.*Apply.*Eligibility", re.IGNORECASE)).first

# Marital Status / Occupation dropdowns
page.locator("mat-form-field").filter(has_text="Marital Status").first.locator("mat-select").first.click()
# then JS scroll+click option by exact text

# Date of birth
page.locator('input[placeholder="Select date of birth"]').first
# method: click → Ctrl+A → type with delay=50 → Tab

# Set Primary (first unchecked radio)
page.locator("mat-radio-button:not(.mat-radio-checked)").first.click()
```

---

## Page Navigation Flow
```
1. GET /nphies/beneficiary/add
   → Enter ID → Inquire CCHI → form pre-filled with patient data

1a. Rule 3-1 (if error-icon present): open mat-select-15 → select by TPA_OPTION_MAP index
    → get_payer_option_index() reads confirmed index from mat-select-15 → stored as payer_option_idx

2. Fill: Marital Status=Unknown · Occupation=Unknown · DOB=01/01/2000 · Set Primary

2b. Rule 3-3 L1: if payer_en still empty AND error-icon still present after Rule 3-1
    → skip Add & Apply entirely (TPA unresolved — prevents form rejection loop)

3. Click "Add & Apply Eligibility"
   → Snackbar (2s timeout) → dismiss → navigate to:
   → Rule 3-3 L2: URL safety-check — if "eligibility" not in url, record error and skip

4. GET /nphies/eligibility?beneficiary=<dynamic-id>
   → Pre-filled Insurance Plan: "(Primary) Member ID: ... (Active)"

5. Click Discovery radio → Angular clears Insurance Plan

6. Select payer: primary = opts[payer_option_idx - 1] in CDK overlay (index from step 1a)
                 fallback = CONTAINS match on payer_en if index fails
                 last resort = first non-empty option (guards empty payer_en)

7. Click #requestEligibilty → NPHIES processes (~17s) → result in-place

8. Parse result card → Site Eligibility · Outcome · Disposition
```

---

## Key Behaviors & Gotchas

| Behavior | Detail |
|----------|--------|
| Session file | `src/session.json` — auto-restored; path resolves relative to `login.py` (`__file__`), so it always lives in `src/` regardless of working directory. Expired → fresh login |
| OTP | Fetched from webhook.site; **auto-deleted from inbox** after retrieval (no accumulation) |
| Snackbar | 2s timeout (no initial wait); mouse.click(700,300) to dismiss; 300ms settle |
| URL after Request Eligibility | **SAME URL** — in-place render. URL change timeout logs `[!]` but this is normal |
| verbose=False | Passed to `run_cchi_inquiry()` in multi-ID runs — suppresses heavy element dumps |
| mat-select IDs | `mat-select-13` (Relation), `mat-select-15` (TPA Payer selector) in CCHI page — Angular-generated, may shift; always locate via container component |
| TPA payer dropdown | `app-nphies-payers-selector mat-select` — stable selector regardless of Angular-assigned ID |
| `get_payer_option_index()` | Opens payer dropdown, reads `aria-selected` index, closes — call after Rule 3-1 on CCHI page |
| **JS string concat in page.evaluate()** | Python adjacent literals (`'str1' 'str2'`) auto-concat in Python but NOT in JS → `SyntaxError: missing ) after argument list`. Always add explicit `+` between JS string fragments inside `page.evaluate()` calls. |
| **Session validator false-positive** | Old check: `"iam.waseel.com" not in url` — expired sessions redirect to `sso.waseel.com`, which passes. Fixed: `check_session_valid` now navigates to CCHI_URL and checks the national ID input is present in DOM. |
| **CCHI dual-condition wait (Improvement 1)** | Single `wait_for_function` resolves on name filled OR `mat-dialog-container` present (8s budget). Payer dropdown wait then guarded by `check_no_record_dialog()` — skipped entirely for NO_INSURANCE IDs. Saves ~5s per NO_INSURANCE ID vs old two-timeout approach. Both in `src/CheckCCHI.run_cchi_inquiry()` and `testing/testing.py`. |
| **`mat-option` listing (Improvement 2)** | `select_mat_dropdown()` uses `page.evaluate(r"""...""")` single JS call to read all option texts — NEVER `[o.inner_text() for o in .all()]`. The old IPC loop caused a 31s `TimeoutError` on ID 1091123552; now impossible. |
| **`insurance_payer` empty for fast/TPA** | Backfill from `select_insurance_plan()` return value; Arabic stripped with `arabic_idx` same as `_extract_table_rows`. Applies in both `testing/testing.py` and `src/RequestEligibility.py`. |

---

## Timing Benchmarks (2026-02-27, 49-ID batch · improvements 1+2+4 applied)

Per-step mean times from `testing/analyze.py` across 35 successful full-pipeline IDs:

| Step | Mean | P95 | Note |
|------|------|-----|------|
| login (full, with OTP) | ~20s | — | Fresh login; restore ~10s |
| cchi_page_goto | 3.76s | 6.26s | Navigation from previous eligibility page |
| cchi_page_wait_stable | 0.89s | 1.04s | Angular settle |
| cchi_form_fill | 0.58s | 0.78s | Fill ID + click Inquire |
| cchi_wait_response | 4.31s | 8.64s | **Improvement 1 applied**: NO_INS now ~2-3s (was ~8.5s); insured path unchanged |
| fix_multiple_tpa_payer | 0.22s | 1.17s | 8/35 IDs triggered Rule 3-1 (MedGulf TPA) |
| capture_extended_cchi_fields | 0.58s | 0.86s | Clicks add_circle_outline, reads 6 hidden fields |
| get_payer_option_index | 0.95s | 1.45s | Opens dropdown, reads aria-selected, closes |
| select_marital_status | 2.03s | 2.04s | **Improvement 2 applied**: 31s crash risk eliminated (JS eval) |
| select_occupation | 1.43s | 2.33s | |
| fill_date_picker_dob | 2.23s | 5.14s | **Improvement 4 applied**: delay=20ms (was 30ms); saves ~100ms/ID |
| click_set_primary | 0.52s | 1.06s | |
| wait_snackbar_dismiss | 2.54s | 3.08s | Server snackbar consistently ~2.5s |
| wait_navigation_after_add | 4.32s | 6.75s | Server-side save; high variance normal |
| click_discovery | 1.09s | 1.64s | |
| select_insurance_plan | 0.63s | 0.90s | |
| **click_request_eligibility** | **15.97s** | **16.23s** | **Dominant bottleneck — NPHIES server, unavoidable** |
| extract_eligibility_result | 0.01s | 0.01s | |
| **Total per ID (full pipeline)** | **~32s** | | Happy path |

For granular per-step stats across a batch run, see `testing/output/analysis/report_{ts}.xlsx`
(run `testing/analyze.py` after a `testing/testing.py` batch run).

---

## CCHI Data Extraction
```python
rows = dict(CheckCCHI._extract_table_rows(raw))
# Keys available: Full Name, Document ID, Insurance Payer, Payer (Arabic),
#                 Member Card ID, Policy Number, Policy Holder, Expiry Date,
#                 Relation, ID Type
payer_en = rows["Insurance Payer"]  # English only, before first Arabic char
```

---

## Output Files
| File | Notes |
|------|-------|
| `testing/eligibility_results.csv` | Appended; header written once; open in Excel |
| `testing/run_log.txt` | Last run stdout — redirect with `> testing/run_log.txt 2>&1` |
| `testing/output/html/{id}_{ts}/` | HTML snapshots from testing harness (8 per ID) |
| `testing/output/id_runs/{id}_{ts}.json` | Per-ID JSON run record (timing + bottlenecks + warnings) |
| `testing/output/analysis/report_{ts}.xlsx` | 6-sheet Excel from `analyze.py` — Summary, Results, Timing, Bottlenecks, Errors, Warnings |
| `testing/output/analysis/timing_chart_{ts}.png` | Horizontal bar chart from `analyze.py` — mean time per step |
| `src/session.json` | Auto-managed; do not edit manually |
| `CLAUDE.md` | This file — Claude session memory |
| `src/config.py` | Named constants (SLOW_MO, VIEWPORT, timeouts, defaults) |

### Run Commands
```bash
# Production (one or many IDs via .env ID_NUMBER):
PYTHONIOENCODING=utf-8 venv/Scripts/python src/RequestEligibility.py

# Testing harness (reads testing/ids.txt first, falls back to .env ID_NUMBER):
PYTHONIOENCODING=utf-8 venv/Scripts/python testing/testing.py > testing/run_log.txt 2>&1

# Post-run deep analysis (Excel + chart):
PYTHONIOENCODING=utf-8 venv/Scripts/python testing/analyze.py
```

---

## Dependencies
```
playwright==1.58.0   requests==2.32.5   python-dotenv==1.2.1
pandas==3.0.1        openpyxl==3.1.5    matplotlib==3.10.8   numpy==2.4.2
Python 3.13 · Windows 10 · venv at D:\Waseel Automation\venv\
```
