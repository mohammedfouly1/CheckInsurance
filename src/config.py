"""
config.py — Centralised constants for Waseel Automation.
Import what you need:  from config import SLOW_MO, VIEWPORT, DEFAULT_DOB
"""

# ---------------------------------------------------------------------------
# Browser settings
# ---------------------------------------------------------------------------
SLOW_MO   = 30               # ms injected after every Playwright action
VIEWPORT  = {"width": 1400, "height": 900}

# ---------------------------------------------------------------------------
# Timing (ms)
# ---------------------------------------------------------------------------
SETTLE_MS          = 300     # fixed settle after networkidle in wait_stable()
TIMEOUT_NETWORKIDLE = 15000  # wait_for_load_state("networkidle") timeout
TIMEOUT_ELEMENT    = 10000   # generic element-visible / element-ready timeout

# ---------------------------------------------------------------------------
# Form defaults (beneficiary add page)
# ---------------------------------------------------------------------------
DEFAULT_DOB            = "01/01/2000"
DEFAULT_MARITAL_STATUS = "Unknown"
DEFAULT_OCCUPATION     = "Unknown"

# ---------------------------------------------------------------------------
# UI interaction
# ---------------------------------------------------------------------------
DISMISS_X = 700    # mouse.click X to dismiss snackbar / overlays
DISMISS_Y = 300    # mouse.click Y to dismiss snackbar / overlays
