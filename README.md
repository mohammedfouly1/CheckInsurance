# Waseel Automation — NPHIES Eligibility Checker

Automated eligibility verification for Saudi health insurance claims on
**eclaims.waseel.com** (NPHIES platform).
Supports single-patient or batch runs; outputs a structured CSV report.

---

## Architecture

```
login.py  ←  CheckCCHI.py  ←  AddEligibility.py  ←  RequestEligibility.py
```

| File | Responsibility |
|------|---------------|
| `login.py` | Session restore / fresh login / OTP via webhook.site · `Timer` class |
| `CheckCCHI.py` | CCHI beneficiary inquiry · patient data capture · ASCII table |
| `AddEligibility.py` | Fill beneficiary form · click Add & Apply Eligibility · navigate |
| `RequestEligibility.py` | **Main entry point** · multi-ID loop · Discovery → Plan → Request · CSV output |

---

## Setup

### 1 — Install dependencies
```
cd "D:\Waseel Automation"
venv\Scripts\pip install -r requirements.txt
venv\Scripts\playwright install chromium
```

### 2 — Configure `.env`
```
WASEEL_USERNAME=autoinstance
WASEEL_PASSWORD=bousia@@11BB
WEBHOOK_API_KEY=d4a40704-0cfb-4f37-b666-a949000063a4
WEBHOOK_SITE_BASE=https://webhook.site
ID_NUMBER=2309901342
```

---

## Input

Set `ID_NUMBER` in `.env` — any of these formats work:

```
# Single patient
ID_NUMBER=2309901342

# Multiple patients — comma-separated
ID_NUMBER=1086242508,2347902641,1090270735,2463423430,2102931983

# Multiple patients — bracket list (spaces ignored)
ID_NUMBER=[1086242508, 2347902641, 1090270735]
```

---

## Run

```
PYTHONIOENCODING=utf-8 venv\Scripts\python RequestEligibility.py
```

---

## Output

### Console — per-ID result card
```
────────────────────────────────────────────────────────────
  RESULT — ID 2309901342
────────────────────────────────────────────────────────────
  ID                 2309901342
  Full Name          MOHMED SALAH FOULY MOHME
  Insurance Payer    Gulf Union Cooperative Insurance Company
  Policy Holder      مجمع مشعل هليل هلال الفهمى الطبي
  Expiry Date        28/04/2026
  Site Eligibility   Eligible ( Eligible )
  Outcome            Processing Complete
  Disposition        Coverage is in-force
```

### Console — summary table (all IDs)
ASCII box table with all 7 output fields per ID, printed at end of run.

### `eligibility_results.csv`
Appended after every run (header written once on first creation).

| Column | Source |
|--------|--------|
| Run At | Timestamp of run |
| ID | Patient ID (National ID / Iqama) |
| Full Name | CCHI page — input `placeholder="Enter full name"` |
| Insurance Payer | CCHI page — mat-select-14 (English text only) |
| Policy Holder | CCHI page — input `placeholder="Enter policy holder"` |
| Expiry Date | CCHI page — input `placeholder="Select expiry date"` |
| Site Eligibility | Result card — label `"Site Eligibility"` |
| Outcome | Result card — label `"Outcome"` |
| Disposition | Result card — label `"Disposition"` |
| Error | Python exception if step failed (empty on success) |

### `timing_log.jsonl`
One JSON line per timer per run. Two entries per run:

```jsonc
// Per-ID step breakdown
{"run_at":"2026-02-24T18:28:42","label":"2309901342","steps":{
  "cchi_inquiry":7.939,"add_eligibility":13.709,
  "click_discovery":2.184,"select_plan":1.155,
  "request_eligibility":16.869,"extract_result":0.008}}

// Run-level summary
{"run_at":"2026-02-24T18:28:26","label":"run:1_ids","steps":{
  "total":57.305,"login":12.317,"id_2309901342":41.865}}
```

---

## Timing Benchmarks (single ID · 2026-02-24)

| Step | Time | % of ID |
|------|------|---------|
| cchi_inquiry | 7.94 s | 19 % |
| add_eligibility | 13.71 s | 33 % |
| click_discovery | 2.18 s | 5 % |
| select_plan | 1.16 s | 3 % |
| **request_eligibility** | **16.87 s** | **40 %** ← NPHIES server wait |
| extract_result | 0.01 s | 0 % |
| **Total per ID** | **41.87 s** | |
| Login (session restore) | 12.32 s | — |

**Bottleneck**: `request_eligibility` — server-side NPHIES processing,
not reducible from client. Second: `add_eligibility` — form fill + page navigation.

---

## Confirmed HTML Selectors

All selectors validated on **2026-02-24**.  Do not modify without re-validating.

### Element 1 — Discovery radio button
```python
page.locator("mat-radio-button").filter(
    has_text=re.compile(r"Discovery", re.IGNORECASE)
).first.click()
```
- Underlying input: `id="isDiscovery-input"`, `name="mat-radio-group-0"`, `value="2"`
- **Side-effect**: Angular reactively **removes** `mat-select#insurancePlan` from DOM
  and replaces it with a new unnamed `mat-select` labelled "Select Payer"

### Element 2 — Insurance Plan dropdown
```python
# Must be called AFTER click_discovery()
# mat-select#insurancePlan NO LONGER EXISTS at this point
page.evaluate("() => { const all=[...document.querySelectorAll('mat-select')]; if(all.length>0) all[all.length-1].click(); }")
# then CONTAINS match on payer_en inside mat-option
```
- Post-Discovery DOM: `mat-select-2` with placeholder "Select Payer"
- Option text format: `"(Primary) Member ID: 002309901342001 (Gulf Union...) (Active)"`
  → matched with `/payer_en/i` CONTAINS (not exact)

### Element 3 — Request Eligibility button
```python
page.locator("#requestEligibilty")   # single 'i' — app typo, confirmed
```
- After click: result renders **in-place** on the same URL (Angular SPA — no navigation)

---

## Page Flow

```
1.  GET /nphies/beneficiary/add
        ↓ Enter ID → Inquire CCHI → form pre-filled with patient data

2.  Fill Marital Status = "Unknown"
    Fill Occupation     = "Unknown"
    Fill Date of Birth  = 01/01/2000
    Click Set Primary radio

3.  Click "Add & Apply Eligibility"
        ↓ Success snackbar (max 2 s wait) → dismiss → navigate to:

4.  /nphies/eligibility?beneficiary=<dynamic-id>
        ↓ Pre-filled with Insurance Plan = "(Primary) Member ID: ... (Active)"

5.  Click Discovery radio
        ↓ Angular clears Insurance Plan dropdown

6.  Select payer from "Select Payer" dropdown (CONTAINS match)

7.  Click "Request Eligibility"  [id="requestEligibilty"]
        ↓ NPHIES processes (~17 s) → result renders in-place

8.  Parse result card → extract Site Eligibility · Outcome · Disposition
```

---

## Session Management

- Session saved to `session.json` (cookies + localStorage)
- On startup: session validity checked by loading TARGET_URL
  - If `iam.waseel.com` in URL → session expired → full login
  - If `eclaims.waseel.com` in URL → session valid → skip login
- OTP: fetched from webhook.site; **automatically deleted from inbox** after use

---

## Other Scripts (standalone)

```
# Just login / test session
venv\Scripts\python login.py

# CCHI inquiry only — prints patient table
venv\Scripts\python CheckCCHI.py

# Add & Apply Eligibility only
venv\Scripts\python AddEligibility.py
```

---

## Output Files Reference

| File | Description |
|------|-------------|
| `eligibility_results.csv` | Accumulated results; open in Excel |
| `timing_log.jsonl` | Cross-run timing data (one JSON line per timer) |
| `session.json` | Browser session — do not edit manually |
| `run_log.txt` | Last run stdout (overwritten each run when redirected) |

---

## Dependencies

```
playwright==1.58.0      # browser automation
requests==2.32.3        # webhook.site OTP polling + deletion
python-dotenv==1.2.1    # .env loading
```

Python 3.13 · Windows 10
