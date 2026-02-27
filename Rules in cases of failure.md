# Rules in cases of failure

This file documents failure patterns encountered during RequestEligibility automation runs.
Each rule describes a specific failure condition, how to detect it, and what action the script takes.

<!-- Add new rules below using the template. Increment rule number each time. -->

---

## Rule 1 — Invalid ID Number Format

**Trigger:** The ID number provided in `.env` (or `testing/ids.txt`) is not a valid Saudi National ID or Iqama number.

**Detected by:** Pre-flight validation before any browser action:
- Length ≠ 10 digits
- Contains non-numeric characters
- Does not start with `1` (Saudi citizen) or `2` (Iqama / resident)
- For National IDs (starts with `1`): checksum mismatch (see algorithm below)

**Action:** Skip the ID entirely — do not navigate to the CCHI page. Log a clear error and continue to the next ID. Records `"INVALID_ID"` in all CSV data fields and the failure reason in the `error` column.

**Implemented in:** `src/CheckCCHI.validate_id()` — called before the `try:` block in both `src/RequestEligibility.main()` and `testing/testing.py run_one_id()`.

### Saudi National ID Checksum Algorithm

1. Take the first 9 digits.
2. For each **odd-position** digit (positions 1, 3, 5, 7, 9 — 1-indexed): multiply by 2; if result ≥ 10, sum its two digits (e.g. 16 → 7, 18 → 9).
3. For each **even-position** digit (positions 2, 4, 6, 8): keep as-is.
4. Sum all 9 processed values → `total`.
5. `expected_check_digit = (10 − (total % 10)) % 10`
6. The 10th digit must equal `expected_check_digit`.

Iqama numbers (start with `2`) pass on format only — no checksum applied.

**Notes:**
- Validation runs **before** the `try:` block so no browser time is consumed for bad IDs.
- In multi-ID runs, invalid IDs are skipped with a `[SKIP]` prefix; valid IDs continue normally.
- The CSV row for a skipped ID records `"INVALID_ID"` in all result fields for traceability.

---

## Rule 2 — Valid ID but No Insurance / No Record Found

**Trigger:** The ID passes format and checksum validation, CCHI is queried successfully, but the patient has no active insurance plan registered in NPHIES.

**Detected by:** After `run_cchi_inquiry()` returns, check for the error dialog on the page:

```python
no_record = page.locator("mat-dialog-container").filter(
    has_text=re.compile(r"No record found", re.IGNORECASE)
).count() > 0
```

The dialog is the **sole authoritative signal** — the empty payer field in the CCHI table is a side-effect, not the trigger.

**Visual state when triggered (confirmed 2026-02-27):**
- **Error dialog** — `cdk-overlay-pane.error-dialog` → `mat-dialog-container` → `<p style="color: rgb(204, 47, 47);">No record found</p>`
- **Insurance Plans section** — `<app-empty-state><span>No insurance plans found!</span></app-empty-state>`
- Patient table: all fields blank except `ID Type = "Nationals and Residents (HIDP)"`

**Action:** Skip `AddEligibility` and the eligibility request entirely. Record `"NO_INSURANCE"` in all downstream fields:

```python
if no_record:
    record.update({
        "insurance_payer":  "NO_INSURANCE",
        "policy_holder":    "NO_INSURANCE",
        "expiry_date":      "NO_INSURANCE",
        "site_eligibility": "NO_INSURANCE",
        "outcome":          "NO_INSURANCE",
        "disposition":      "NO_INSURANCE",
        "error":            "No insurance plans found",
    })
```

**Notes:**
- The check runs **after** CCHI inquiry and **before** `AddEligibility` — saves ~14s of browser time per skipped ID.
- `full_name` from CCHI is preserved as-is (may be blank for this error state).
- No dialog dismissal needed — `page.goto(CCHI_URL)` at the start of the next ID reloads the page and clears all overlays.

---

## Rule 3 — Valid ID, Valid Insurance, but Payer Cannot Be Auto-Selected

**Trigger:** The ID is valid, CCHI returns insurance data, but the Insurance Plans section on `/nphies/beneficiary/add` cannot automatically resolve the correct payer — requiring intervention before "Add & Apply Eligibility" can succeed.

**Parent rule** — covers multiple sub-cases, each with a distinct detection method and fix.

---

## Rule 3-1 — Multiple TPA Payer: Index-Based Selection

**Trigger:** The CCHI returned a valid payer, but that payer belongs to **Multiple TPAs**. The app cannot auto-select it and leaves the `app-nphies-payers-selector` dropdown as "Select Payer" (empty / validation-failing), showing an `error_outline` icon.

**Detected by:** `mat-icon.error-icon` present after CCHI inquiry:

```python
page.locator("mat-icon.error-icon").count() > 0
```

**HTML structure (confirmed 2026-02-27):**
```html
<app-nphies-payers-selector>
  <mat-form-field>
    <mat-select id="mat-select-15"><!-- "Select Payer" placeholder --></mat-select>
    <!-- cdk-describedby-message-18 = "Payer must be specified" -->
  </mat-form-field>
  <mat-icon class="error-icon ml-2"
            aria-describedby="cdk-describedby-message-14"
            aria-hidden="true">error_outline</mat-icon>
  <!-- cdk-describedby-message-14 = "<TPA company name> is part of
       Multiple TPAs. Please select the appropriate Payer under the TPA." -->
</app-nphies-payers-selector>
```

**Known TPA companies and their option positions in mat-select-15:**

| Keyword in `cdk-describedby-message-14` | Option # | Option text (English + Arabic) |
|---|---|---|
| `MedGulf` | 6 | The Mediterranean and Gulf Cooperative Insurance and Reinsurance Company (MedGulf) شركة المتوسط والخليج للتأمين وإعادة التأمين التعاوني ( ميدغلف) |
| `Al-Etihad` | 2 | Al-Etihad Cooperative Insurance Company شركة الاتحاد للتأمين التعاوني * |

> Option 1 is always the `"Select Payer"` placeholder. Payers start at option 2.

**Fix — Phase 1: CCHI page (`src/CheckCCHI.fix_multiple_tpa_payer(page)`):**

1. Read the ARIA message from `mat-icon.error-icon[aria-describedby]`.
2. Match the message against `TPA_OPTION_MAP` (keyword → option index).
3. Open `app-nphies-payers-selector mat-select` **once**.
4. Select by known index (0-based = option_num − 1), scoped to the CDK overlay panel.
5. Falls back to name-based CONTAINS match for TPAs not in the map (and logs `"add to TPA_OPTION_MAP"`).

```python
TPA_OPTION_MAP = {
    "MedGulf":   6,   # The Mediterranean and Gulf Cooperative Insurance...
    "Al-Etihad": 2,   # Al-Etihad Cooperative Insurance Company
}
# → opens dropdown once, clicks opts[option_num - 1] in CDK overlay panel
```

**Phase 2: Read confirmed index (`src/CheckCCHI.get_payer_option_index(page)`):**

Called **after** `fix_multiple_tpa_payer()` (and after data re-capture). Works for both pre-filled payers and Rule 3-1 fixed payers:

1. Opens `app-nphies-payers-selector mat-select`.
2. Finds the option with `aria-selected="true"` or `mat-selected` class in the CDK overlay.
3. Returns its 1-based index.
4. Closes the dropdown with Escape.

```python
payer_option_idx = CheckCCHI.get_payer_option_index(page)
# Returns e.g. 6 (MedGulf), 2 (Al-Etihad), or None if no payer selected
```

**Phase 3: Eligibility page (`src/RequestEligibility.select_insurance_plan(page, payer_en, payer_option_idx)`):**

After `click_discovery()`, `select_insurance_plan()` uses the index from Phase 2 to select the same option in the eligibility page's "Select Payer" dropdown — avoiding the text-match entirely for TPA patients:

```python
# Primary: index-based (reliable for TPA payers)
opts[payer_option_idx - 1].click()
# Fallback: CONTAINS text match on payer_en (only if index fails or is None)
# Last resort: first non-empty option if payer_en is also empty
```

**Call chain:**
- `src/CheckCCHI.fix_multiple_tpa_payer(page)` — called from `run_cchi_inquiry()` after first data capture (production), and from `run_one_id()` step 6 (testing harness)
- `src/CheckCCHI.get_payer_option_index(page)` — called after Rule 3-1 + data re-capture in both production `main()` and testing `run_one_id()` step 7c
- `src/RequestEligibility.select_insurance_plan(page, payer_en, payer_option_idx)` — called after Discovery click

**Notes:**
- `cdk-describedby-message-*` IDs are Angular CDK-generated and shift across sessions — always locate via `aria-describedby` attribute, never hard-code the numeric ID.
- `mat-select-15` is Angular's session-assigned ID for the payer dropdown — always locate via `app-nphies-payers-selector mat-select` container (stable across sessions).
- To add a new TPA: identify the keyword in the error message and the 1-based option number, then add one line to `TPA_OPTION_MAP` in `fix_multiple_tpa_payer()`.
- If `fix_multiple_tpa_payer()` returns `False` (no icon present), no action is taken — the normal flow continues unchanged.

---

## Rule 3-2 — Empty Payer: JS Regex Guard

**Trigger:** After all CCHI processing (including Rule 3-1 attempt), `payer_en` is still an empty string. This can happen when:
- The TPA fix failed (unknown TPA, no matching option) — payer dropdown still shows placeholder
- The CCHI response had no recognisable payer field (mat-select ID shifted, label mismatch)

**Why it fails without this rule:** `re.escape("")` produces `""`, so the JS regex literal becomes `//i` — a JavaScript **line comment** — which causes the `if (t)` statement on the next line to raise `SyntaxError: Unexpected token 'if'`.

**Confirmed symptom (29-ID run, 2026-02-27):** `Error: Page.evaluate: SyntaxError: Unexpected token 'if'` on 11/29 IDs, all with empty Insurance Payer field.

**Fix — in `src/RequestEligibility.select_insurance_plan()`:**

Before building the JS regex for the name-match fallback, guard against empty `payer_en`:

```python
if payer_en:
    esc = re.escape(payer_en)
    # ... CONTAINS regex match in mat-option ...
else:
    print("  [!] payer_en is empty — skipping name match")

if not ok:
    # Last resort: select first non-empty option
    fb = page.evaluate(r"""() => {
        const opts = [...document.querySelectorAll('mat-option')]
                         .filter(o => o.textContent.trim().length > 0);
        if (opts.length > 0) { opts[0].click(); return opts[0].textContent.trim(); }
        return null;
    }""")
```

**Notes:**
- This guard is in the **fallback path only**. The primary path (index-based selection via `payer_option_idx`) does not use a regex and is not affected by empty `payer_en`.
- In practice, Rule 3-1 + `get_payer_option_index()` prevents reaching this guard for known TPA patients — but the guard protects against future unknown cases.

---

## Rule 3-3 — Navigation Stuck After Add & Apply Eligibility

**Trigger:** Clicking "Add & Apply Eligibility" does not cause the page to leave `/nphies/beneficiary/add`. This occurs when:
- The TPA payer was not resolved (Rule 3-1 failed) — form validation rejects submission silently
- The patient's beneficiary record already exists in the system
- Any other form error that prevents navigation

**Confirmed symptom (29-ID run, 2026-02-27):** `TimeoutError: Locator.wait_for: Timeout 10000ms exceeded` on `mat-radio-button` (Discovery radio) for 8/29 IDs — all were MedGulf TPA patients where the payer remained unresected.

**Two-layer fix:**

**Layer 1 — Proactive early exit (both `src/RequestEligibility.main()` and `testing/testing.py run_one_id()`):**
After `fix_multiple_tpa_payer()` and `get_payer_option_index()`, if `payer_en` is still empty AND the `error-icon` is still present, skip Add & Apply entirely:

```python
if not payer_en and page.locator("mat-icon.error-icon").count() > 0:
    record["error"] = "TPA payer unresolved — Add & Apply skipped (error-icon still present)"
    # production: results.append(record); continue
    # testing:    return record
```

**Layer 2 — Safety-net URL check (both `src/RequestEligibility.main()` and `testing/testing.py run_one_id()`):**
After `wait_navigation_after_add` (whether it timed out or succeeded), confirm the page reached `/eligibility`:

```python
current_url = page.url
if "eligibility" not in current_url:
    record["error"] = f"Navigation stuck — expected /eligibility, got: {current_url[:100]}"
    # production: results.append(record); continue
    # testing:    return record
```

**Notes:**
- Layer 1 (proactive) handles the known TPA case — avoids the unnecessary `wait_navigation_after_add` 15s wait.
- Layer 2 (safety net) handles any other unforeseen case where navigation fails (patient already exists, form validation error, etc.).
- Both layers exist in **both** `src/RequestEligibility.py` (production) and `testing/testing.py` — added to production 2026-02-27 to achieve full parity.

---

## Dev Rule — JS String Literals Inside `page.evaluate()` Must Use Explicit `+` Concatenation

**This is a coding pitfall, not a runtime eligibility rule.** Document here to prevent regression.

**Trigger:** Any multi-line `document.querySelector()` or similar JS call inside `page.evaluate()` where adjacent Python string literals are used across lines — e.g.:

```python
# BROKEN — Python silently concatenates 'a' 'b' at parse time,
# but the resulting JS string is syntactically invalid:
page.evaluate(r"""() => {
    const p = document.querySelector(
        '.mat-select-panel, '
        '.mat-mdc-select-panel, '
        '[role="listbox"]'
    );
}""")
```

**Detected by:** Runtime exception from `page.evaluate()`:
```
Error: Page.evaluate: SyntaxError: missing ) after argument list
```

The error location usually points to the **first string literal** in the adjacent pair, not the second. Appears in the Playwright error log, often immediately after a timing line in the profiler output.

**Root cause:** Python's parser sees `'a' 'b'` as two adjacent string literals and implicitly concatenates them into one string at compile time. When this happens inside a raw string passed to JavaScript via `page.evaluate()`, the resulting JS contains:

```js
document.querySelector('.mat-select-panel, .mat-mdc-select-panel, [role="listbox"]')
// Python already merged them — BUT only if they are truly adjacent (no + between them).
// If there is any variable interpolation or surrounding JS context, the merge does NOT work as intended.
```

In practice the issue occurred because the strings were inside a triple-quoted raw string `r"""..."""` that was passed whole to JS — Python did NOT merge them inside the raw string. The resulting JS contained:

```js
'.cdk-overlay-container .mat-select-panel, '  '.cdk-overlay-container .mat-mdc-select-panel, '
```

i.e. two **separate string expressions** in JS source, which is a syntax error.

**Fix:** Always add explicit `+` between JS string fragments:

```python
# CORRECT:
page.evaluate(r"""() => {
    const p = document.querySelector(
        '.cdk-overlay-container .mat-select-panel, ' +
        '.cdk-overlay-container .mat-mdc-select-panel, ' +
        '.cdk-overlay-container [role="listbox"]'
    );
}""")
```

**Confirmed instances fixed (2026-02-27):**
- `src/CheckCCHI.py` — `fix_multiple_tpa_payer()` — CDK overlay panel selector (2 `+` added)
- `src/CheckCCHI.py` — `get_payer_option_index()` — CDK overlay panel selector (2 `+` added)
- `src/RequestEligibility.py` — `select_insurance_plan()` — CDK overlay panel selector (2 `+` added)

**Impact:** Bug C caused all 22 insured IDs in the first post-refactor run to fail at `get_payer_option_index`. Only the 5 NO_INSURANCE + 2 INVALID_ID IDs "succeeded" (via early-exit rules). Fixed in the same session.

**Rule for new `page.evaluate()` code:** Before committing any multi-line JS selector, check every string continuation. If two string literals appear on adjacent lines without a `+`, add one.

---

<!--
## Rule N — [Rule Title]

**Trigger:** What condition causes this failure

**Detected by:** How to identify it (HTML element, log message, exception, etc.)

**Action:** What the script should do when this is detected

**Notes:** Any extra context, code hints, or related selectors

---
-->
