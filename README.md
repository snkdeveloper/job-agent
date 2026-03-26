# Navi: LinkedIn + CareerShift Automation

This project automates a sourcing workflow:

1. Search LinkedIn people with filters (company/location and optional Northeastern alumni filter).
2. Extract candidate profiles.
3. Optionally send LinkedIn connection requests (currently scoped to Northeastern alumni flow only).
4. Optionally search CareerShift for emails.
5. Save results to Excel.

---

## What This Repository Does

### Core capabilities

- **Engineering manager search flow**
  - Finds LinkedIn people matching engineering-manager intent.
  - Applies LinkedIn UI filters (`Locations`, `Current companies`).
  - Optionally searches CareerShift for emails.
  - Writes results to `results.xlsx`.

- **Northeastern alumni search flow**
  - Finds LinkedIn people using the Northeastern school filter (`schoolFilter`).
  - Applies company/location filters.
  - Can run in:
    - **data mode** (collect rows and optional emails),
    - **connector mode** (open each profile and run connect decision logic).
  - Writes results to `northeastern_alumni_results.xlsx` when enabled.

- **Technical recruiter search flow**
  - Finds technical recruiter profiles with company/location filters.
  - Optionally searches CareerShift for emails.
  - Writes results to `technical_recruiter_results.xlsx`.

- **Profile connector (aria-label driven)**
  - Implemented in `linkedin_connector.py`.
  - Opens a single profile URL and branches by profile action state.
  - Uses deterministic checks (Invite/Pending/Follow + `Remove connection` check in More menu).

---

## Project Structure

- `main.py`  
  Entry point. Orchestrates enabled flows, browser setup, resume logic, and output writing.

- `config.py`  
  Runtime configuration flags and constants.

- `linkedin_scraper.py`  
  LinkedIn search/filter logic and candidate extraction.

- `linkedin_connector.py`  
  Profile-level connection logic based on button `aria-label` and More menu checks.

- `careershift_scraper.py`  
  CareerShift email lookup logic.

- `excel_handler.py`  
  Excel read/write + processed-pair resume helpers.

- `start_chrome_debug.sh`  
  Starts Chrome/Chromium with remote debugging port enabled.

---

## Requirements

- Python 3.10+ recommended
- macOS/Linux shell (project currently configured for Chrome remote debugging workflow)
- Google Chrome or Chromium installed
- LinkedIn account session available in browser
- CareerShift account session (if email lookup is enabled)

Install dependencies:

```bash
pip install -r requirements.txt
```

`requirements.txt` includes:

- `selenium`
- `webdriver-manager`
- `pandas`
- `openpyxl`

---

## Setup

### 1) Create and activate virtual environment (recommended)

```bash
cd "/Users/sachetkanchugar/ai agent"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Start Chrome in remote-debug mode

Use the helper script:

```bash
./start_chrome_debug.sh
```

or manually:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-profile
```

### 3) Log in manually in that Chrome window

- Log into LinkedIn.
- Log into CareerShift if you want email lookup enabled.

### 4) Prepare input Excel

Default input file: `companies.xlsx`  
Required columns:

- `company`
- `location`

---

## Running the Script

```bash
cd "/Users/sachetkanchugar/ai agent"
source .venv/bin/activate
python3 main.py
```

---

## Configuration Guide (`config.py`)

### Browser / connection

- `REMOTE_DEBUGGING_ADDRESS = "127.0.0.1"`
- `REMOTE_DEBUGGING_PORT = 9222`
- `CHROME_USER_DATA_DIR = "/tmp/chrome-profile"`

### Input / output files

- `INPUT_EXCEL = "companies.xlsx"`
- `OUTPUT_EXCEL = "results.xlsx"`
- `ALUMNI_OUTPUT_EXCEL = "northeastern_alumni_results.xlsx"`
- `TECHNICAL_RECRUITER_OUTPUT_EXCEL = "technical_recruiter_results.xlsx"`

### Flow toggles

- `ENABLE_ENGINEERING_MANAGER_FLOW`
- `ENABLE_NORTHEASTERN_ALUMNI_FLOW`
- `ENABLE_TECHNICAL_RECRUITER_FLOW`

### Northeastern alumni behavior

- `SEARCH_EMAIL_IN_NORTHEASTERN_ALUMNI_FLOW`  
  If `False`, alumni flow skips CareerShift lookup.

- `SAVE_NORTHEASTERN_ALUMNI_RESULTS`  
  If `False`, alumni rows are not written to alumni output file.

- `NORTHEASTERN_SCHOOL_FILTER`  
  LinkedIn school filter snippet used in query URL.

### Connector behavior

- `ENABLE_CONNECT_IN_NORTHEASTERN_ALUMNI_FLOW`  
  Enables profile connection attempts **only in Northeastern alumni flow**.

- `PROFILE_CONNECTOR_NOTE`  
  The message used for connection requests.

### Other connector flags currently present

- `ENABLE_PROFILE_CONNECTOR_FLOW`
- `PROFILE_CONNECTOR_URL`
- `PROFILE_CONNECTOR_INPUT_EXCEL`
- `PROFILE_CONNECTOR_OUTPUT_EXCEL`

At present, standalone connector mode is intentionally skipped by policy in `main.py`:
connector execution is limited to the Northeastern alumni candidate loop.

---

## Current Connector Decision Logic

Implemented in `linkedin_connector.py` (`connect_to_profile()`):

1. Open profile URL.
2. Read primary action `aria-label`.
3. If **Invite** pattern -> click invite -> add note -> send -> `CONNECTED`.
4. If **Pending** pattern -> `PENDING`.
5. Check More menu for **Remove connection** -> `ALREADY_CONNECTED`.
6. If **Follow** pattern -> open More -> click Connect -> send note -> `FOLLOW_FLOW`.
7. Else -> fallback `ALREADY_CONNECTED`.
8. Any exception -> `ERROR`.

Notes:

- Uses `WebDriverWait`-based waits (no blind loop sleeps for critical interactions).
- Uses a robust More-menu-based connected check (`Remove connection`) to identify existing connections.

---

## Resume / Idempotency Behavior

The project avoids re-processing company/location pairs already present in output files:

- Engineering manager resume set: from `results.xlsx`
- Alumni resume set: from `northeastern_alumni_results.xlsx` (only when saving alumni output is enabled)
- Technical recruiter resume set: from `technical_recruiter_results.xlsx`

If a pair is already processed for all enabled outputs, it is skipped.

---

## Output Files

- `results.xlsx` (engineering manager flow)
- `northeastern_alumni_results.xlsx` (alumni flow, when saving is enabled)
- `technical_recruiter_results.xlsx` (technical recruiter flow)

Output schema used by these files:

- `company`
- `location`
- `first_name`
- `last_name`
- `email`

Possible email values include:

- Real email address
- `NOT_FOUND`
- `EMAIL_LOOKUP_SKIPPED`
- `NO_RESULTS_SAVED` / placeholders depending on branch

---

## Recommended Toggle Profiles

### A) Alumni-only with connect (current preferred mode)

```python
ENABLE_ENGINEERING_MANAGER_FLOW = False
ENABLE_NORTHEASTERN_ALUMNI_FLOW = True
ENABLE_TECHNICAL_RECRUITER_FLOW = False

ENABLE_CONNECT_IN_NORTHEASTERN_ALUMNI_FLOW = True
SEARCH_EMAIL_IN_NORTHEASTERN_ALUMNI_FLOW = False  # or True if needed
SAVE_NORTHEASTERN_ALUMNI_RESULTS = True           # optional
```

### B) Alumni-only without connect (data collection)

```python
ENABLE_NORTHEASTERN_ALUMNI_FLOW = True
ENABLE_CONNECT_IN_NORTHEASTERN_ALUMNI_FLOW = False
```

### C) Multi-flow sourcing, no connect

```python
ENABLE_ENGINEERING_MANAGER_FLOW = True
ENABLE_NORTHEASTERN_ALUMNI_FLOW = True
ENABLE_TECHNICAL_RECRUITER_FLOW = True
ENABLE_CONNECT_IN_NORTHEASTERN_ALUMNI_FLOW = False
```

---

## Troubleshooting

### `Could not connect to Chrome at 127.0.0.1:9222`

- Ensure Chrome is started with `--remote-debugging-port=9222`.
- Use `./start_chrome_debug.sh`.

### Script says input Excel missing

- Create `companies.xlsx` in repo root.
- Ensure columns are exactly `company` and `location`.

### No LinkedIn candidates found

- Confirm LinkedIn session is logged in and not at checkpoint/captcha.
- Enable `DEBUG_LINKEDIN_DUMPS = True` to save diagnostics under `debug/linkedin`.

### CareerShift email lookup fails

- Ensure CareerShift is logged in in the same browser session.
- Set `SEARCH_EMAIL_IN_NORTHEASTERN_ALUMNI_FLOW = False` to skip email lookup for alumni flow.

### Connector always returns connected/pending unexpectedly

- Review logs from `linkedin_connector`.
- LinkedIn UI can vary by account/AB-test; selectors may need adjustment if DOM changes.

---

## Safety and Operational Notes

- Keep request volume conservative to avoid account restrictions.
- Prefer small batches and monitor run behavior.
- LinkedIn UI changes frequently; expect periodic selector maintenance.

---

## Quick Start (Copy/Paste)

```bash
cd "/Users/sachetkanchugar/ai agent"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./start_chrome_debug.sh
# Log into LinkedIn (+ CareerShift if needed) in opened Chrome.
python3 main.py
```

