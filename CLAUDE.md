# Waseel Automation — Claude Memory

This file is read by Claude Code at session start to restore project context.

---

## Project Purpose
Automate NPHIES eligibility checking on eclaims.waseel.com for Saudi health insurance.
Provider: MAKKAH PARK CLINIC · Platform: NPHIES

## Import Chain
```
login.py ← CheckCCHI.py ← AddEligibility.py ← RequestEligibility.py
```

## Run Command
```
cd "D:\Waseel Automation"
PYTHONIOENCODING=utf-8 venv/Scripts/python RequestEligibility.py
```

## Input
`.env` → `ID_NUMBER` — single or comma-separated (brackets optional):
```
ID_NUMBER=2309901342
ID_NUMBER=1086242508,2347902641,1090270735,2463423430,2102931983
```

## Output (7 fields per ID)
| Source | Fields |
|--------|--------|
| CCHI page | Full Name · Insurance Payer · Policy Holder · Expiry Date |
| Result card | Site Eligibility · Outcome · Disposition |

Saved to `eligibility_results.csv` (appended) and printed as ASCII table.

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
# Open the last mat-select on page ("Select Payer" after Discovery)
page.evaluate("() => { const a=[...document.querySelectorAll('mat-select')]; if(a.length) a[a.length-1].click(); }")
# CONTAINS match on payer English name (options include member ID + status text)
page.evaluate(rf"() => {{ const t=[...document.querySelectorAll('mat-option')].find(o=>/{esc}/i.test(o.textContent)); if(t){{t.scrollIntoView({{block:'center'}});t.click();return true;}} return false; }}")
```
- Post-Discovery: `id="mat-select-2"`, label = "Select Payer"
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

2. Fill: Marital Status=Unknown · Occupation=Unknown · DOB=01/01/2000 · Set Primary

3. Click "Add & Apply Eligibility"
   → Snackbar (2s timeout) → dismiss → navigate to:

4. GET /nphies/eligibility?beneficiary=<dynamic-id>
   → Pre-filled Insurance Plan: "(Primary) Member ID: ... (Active)"

5. Click Discovery radio → Angular clears Insurance Plan

6. Select payer from last mat-select ("Select Payer") with CONTAINS match

7. Click #requestEligibilty → NPHIES processes (~17s) → result in-place

8. Parse result card → Site Eligibility · Outcome · Disposition
```

---

## Key Behaviors & Gotchas

| Behavior | Detail |
|----------|--------|
| Session file | `session.json` — auto-restored; if `iam.waseel.com` in URL → expired → fresh login |
| OTP | Fetched from webhook.site; **auto-deleted from inbox** after retrieval (no accumulation) |
| Snackbar | 2s timeout (no initial wait); mouse.click(700,300) to dismiss; 300ms settle |
| URL after Request Eligibility | **SAME URL** — in-place render. URL change timeout logs `[!]` but this is normal |
| verbose=False | Passed to `run_cchi_inquiry()` in multi-ID runs — suppresses heavy element dumps |
| mat-select IDs | `mat-select-13` (Relation), `mat-select-14` (Payer) in CCHI page — Angular-generated, may shift |

---

## Timing Benchmarks (2026-02-24, single ID 2309901342)

| Step | Time | % |
|------|------|---|
| cchi_inquiry | 7.94s | 19% |
| add_eligibility | 13.71s | 33% |
| click_discovery | 2.18s | 5% |
| select_plan | 1.16s | 3% |
| **request_eligibility** | **16.87s** | **40% ← bottleneck** |
| extract_result | 0.01s | 0% |
| **Total per ID** | **41.87s** | |
| Login (restore) | 12.32s | — |

---

## Timer Class (login.Timer)
```python
t = login.Timer(run_label="my_run")
t.start("step_name")
# ... do work ...
t.stop("step_name")   # prints elapsed
t.summary()           # bar chart with %
t.save()              # appends to timing_log.jsonl
```

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
| `eligibility_results.csv` | Appended; header written once; open in Excel |
| `timing_log.jsonl` | One JSON line per `Timer.save()` call |
| `session.json` | Do not edit manually |
| `run_log.txt` | Last run stdout (overwritten when piped with `> run_log.txt`) |
| `README.md` | Full project documentation |
| `CLAUDE.md` | This file — Claude session memory |

---

## Dependencies
```
playwright==1.58.0   requests==2.32.3   python-dotenv==1.2.1
Python 3.13 · Windows 10 · venv at D:\Waseel Automation\venv\
```
