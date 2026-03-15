from __future__ import annotations

import traceback
from pathlib import Path
from typing import List, Tuple

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from careershift_scraper import find_email
from config import (
    INPUT_EXCEL,
    LINKEDIN_CONNECTION_NOTE,
    OUTPUT_EXCEL,
    REMOTE_DEBUGGING_ADDRESS,
    REMOTE_DEBUGGING_PORT,
    SEND_LINKEDIN_CONNECTION_REQUESTS,
    random_delay,
)
from excel_handler import (
    get_processed_company_location_pairs,
    load_companies,
    save_results,
)
from linkedin_connector import connect_if_not_pending
from linkedin_scraper import search_engineering_managers


def _ensure_site_tab(
    driver: webdriver.Chrome,
    attr_name: str,
    domain: str,
    landing_url: str,
) -> None:
    """Ensure a dedicated tab exists for a given site and switch to it.

    We attach to an existing Chrome profile via remote debugging, so there may
    already be tabs open. This function:
      1) Reuses a previously remembered handle if still valid.
      2) Otherwise searches existing tabs for one whose URL contains `domain`.
      3) Otherwise opens a new tab and navigates to `landing_url`.
    """

    handles = list(driver.window_handles)
    remembered = getattr(driver, attr_name, None)

    def _url_matches() -> bool:
        try:
            return domain.lower() in (driver.current_url or "").lower()
        except Exception:
            return False

    # 1) Switch to remembered tab.
    if remembered and remembered in handles:
        try:
            driver.switch_to.window(remembered)
            if _url_matches():
                return
        except Exception:
            pass

    # 2) Search existing tabs.
    for handle in handles:
        try:
            driver.switch_to.window(handle)
            if _url_matches():
                setattr(driver, attr_name, handle)
                return
        except Exception:
            continue

    # 3) Open new tab.
    try:
        driver.switch_to.new_window("tab")
    except Exception:
        driver.execute_script("window.open('about:blank','_blank');")
        # Most browsers append the new handle at the end.
        driver.switch_to.window(driver.window_handles[-1])

    setattr(driver, attr_name, driver.current_window_handle)
    try:
        driver.get(landing_url)
    except Exception:
        pass


def _init_driver() -> webdriver.Chrome:
    """
    Attach to an existing Chrome session that was started with:

        chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-profile

    and where the user has already logged into LinkedIn and CareerShift.
    """
    chrome_options = Options()
    debugger_address = f"{REMOTE_DEBUGGING_ADDRESS}:{REMOTE_DEBUGGING_PORT}"
    chrome_options.add_experimental_option("debuggerAddress", debugger_address)

    print(f"[Driver] Attaching to existing Chrome at {debugger_address}...")

    driver_path = ChromeDriverManager().install()
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.maximize_window()
    return driver


def process_company(
    driver: webdriver.Chrome, company: str, location: str
) -> List[Tuple[str, str, str, str, str]]:
    """
    Process a single company/location:
      - Search LinkedIn for engineering managers
      - For each name, search CareerShift for an email
      - Return a list of rows for Excel output
    """
    print(f"\n=== Processing company: {company} | Location: {location} ===")

    rows: List[Tuple[str, str, str, str, str]] = []

    try:
        _ensure_site_tab(
            driver,
            attr_name="_linkedin_tab_handle",
            domain="linkedin.com",
            landing_url="https://www.linkedin.com/feed/",
        )
        candidates = search_engineering_managers(driver, company, location)
    except Exception:
        print(
            f"[Error] Failed to search LinkedIn for {company} in {location}.\n{traceback.format_exc()}"
        )
        candidates = []

    if not candidates:
        print(
            f"[Info] No LinkedIn engineering managers found for {company} in {location}."
        )
        # Still write a placeholder row so we know the company was processed
        rows.append((company, location, "", "", "NOT_FOUND"))
        return rows

    for idx, (first_name, last_name, profile_url) in enumerate(candidates, start=1):
        if SEND_LINKEDIN_CONNECTION_REQUESTS:
            print(
                f"[LinkedIn] ({idx}/{len(candidates)}) Connect attempt: {first_name} {last_name} -> {profile_url}"
            )
            try:
                _ensure_site_tab(
                    driver,
                    attr_name="_linkedin_tab_handle",
                    domain="linkedin.com",
                    landing_url="https://www.linkedin.com/feed/",
                )
                outcome = connect_if_not_pending(
                    driver, profile_url, note=LINKEDIN_CONNECTION_NOTE
                )
                print(f"[LinkedIn] {outcome.result}: {outcome.detail}")
            except Exception as exc:
                print(f"[LinkedIn] ERROR: connect attempt failed: {exc}")
            random_delay("After LinkedIn connect attempt")

        print(
            f"[Info] ({idx}/{len(candidates)}) Looking up email for {first_name} {last_name} at {company}..."
        )
        random_delay("Before CareerShift search")
        try:
            _ensure_site_tab(
                driver,
                attr_name="_careershift_tab_handle",
                domain="careershift.com",
                landing_url="https://careershift.com/App/Contacts/Search",
            )
            email = find_email(first_name, last_name, company, driver)
        except Exception:
            print(
                f"[Error] Failed to search CareerShift for {first_name} {last_name} at {company}.\n{traceback.format_exc()}"
            )
            email = "NOT_FOUND"

        email_str = str(email or "").strip()
        email_upper = email_str.upper()
        has_email = (
            bool(email_str)
            and email_upper not in {"NOT_FOUND", "NOT FOUND", "NONE", "N/A"}
            and ("@" in email_str)
        )

        if not has_email:
            print(
                f"[Info] Skipping save: no email found for {first_name} {last_name} at {company}."
            )
            continue

        rows.append((company, location, first_name, last_name, email_str))

        # Save after each FOUND email to support resuming
        save_results(rows=[rows[-1]])
        print(
            f"[Saved] {company} | {location} | {first_name} {last_name} | {email_str} -> {OUTPUT_EXCEL}"
        )

        random_delay("After saving result")

    # If we processed candidates but found zero emails, write a single placeholder row
    # so the run can be resumed without reprocessing this company/location.
    if candidates and not rows:
        placeholder = (company, location, "", "", "NO_EMAILS_FOUND")
        save_results(rows=[placeholder])
        print(f"[Saved] {company} | {location} | NO_EMAILS_FOUND -> {OUTPUT_EXCEL}")
        rows.append(placeholder)

    return rows


def main() -> None:
    print("=== LinkedIn → CareerShift Email Finder Automation ===")
    print(f"Input Excel: {INPUT_EXCEL}")
    print(f"Output Excel: {OUTPUT_EXCEL}")

    if not Path(INPUT_EXCEL).exists():
        print(
            f"[Error] Input file '{INPUT_EXCEL}' not found. "
            f"Please create it with columns: company, location."
        )
        return

    companies_df = load_companies()
    processed_pairs = get_processed_company_location_pairs()
    print(f"[Resume] Already processed {len(processed_pairs)} company/location pairs.")

    driver = _init_driver()

    try:
        for _, row in companies_df.iterrows():
            company = str(row["company"]).strip()
            location = str(row["location"]).strip()
            if not company or not location:
                continue

            pair = (company, location)
            if pair in processed_pairs:
                print(
                    f"[Skip] Already processed {company} in {location} according to {OUTPUT_EXCEL}."
                )
                continue

            try:
                process_company(driver, company, location)
            except Exception:
                print(
                    f"[Error] Unexpected error while processing {company} in {location}.\n{traceback.format_exc()}"
                )
                # Continue with next company
                continue

    finally:
        print("[Driver] Closing browser session handle (not the actual Chrome window).")
        try:
            driver.quit()
        except Exception:
            pass

    print("=== Done. Check results in results.xlsx ===")


if __name__ == "__main__":
    main()

