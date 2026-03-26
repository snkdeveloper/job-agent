from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import List, Tuple

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from careershift_scraper import find_email
from config import (
    ALUMNI_OUTPUT_EXCEL,
    ENABLE_CONNECT_IN_NORTHEASTERN_ALUMNI_FLOW,
    ENABLE_ENGINEERING_MANAGER_FLOW,
    ENABLE_NORTHEASTERN_ALUMNI_FLOW,
    ENABLE_PROFILE_CONNECTOR_FLOW,
    ENABLE_TECHNICAL_RECRUITER_FLOW,
    INPUT_EXCEL,
    NORTHEASTERN_SCHOOL_NAME,
    OUTPUT_EXCEL,
    PROFILE_CONNECTOR_INPUT_EXCEL,
    PROFILE_CONNECTOR_NOTE,
    PROFILE_CONNECTOR_OUTPUT_EXCEL,
    PROFILE_CONNECTOR_URL,
    REMOTE_DEBUGGING_ADDRESS,
    REMOTE_DEBUGGING_PORT,
    SAVE_NORTHEASTERN_ALUMNI_RESULTS,
    SEARCH_EMAIL_IN_NORTHEASTERN_ALUMNI_FLOW,
    TECHNICAL_RECRUITER_OUTPUT_EXCEL,
    random_delay,
)
from excel_handler import (
    get_processed_alumni_company_location_pairs,
    get_processed_company_location_pairs,
    get_processed_profile_urls,
    get_processed_technical_recruiter_company_location_pairs,
    load_companies,
    load_profile_targets,
    save_alumni_results,
    save_profile_connector_results,
    save_results,
    save_technical_recruiter_results,
)
from linkedin_scraper import (
    search_engineering_managers,
    search_neu_alumni_by_company,
    search_technical_recruiters,
)
from linkedin_connector import connect_to_profile


def _ensure_site_tab(
    driver: webdriver.Chrome,
    attr_name: str,
    domain: str,
    landing_url: str,
) -> None:
    """Ensure a dedicated tab exists for a given site and switch to it."""

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
        driver.switch_to.window(driver.window_handles[-1])

    setattr(driver, attr_name, driver.current_window_handle)
    try:
        driver.get(landing_url)
    except Exception:
        pass


def _init_driver() -> webdriver.Chrome:
    """Attach to an existing Chrome session started with remote debugging."""

    chrome_options = Options()
    debugger_address = f"{REMOTE_DEBUGGING_ADDRESS}:{REMOTE_DEBUGGING_PORT}"
    chrome_options.add_experimental_option("debuggerAddress", debugger_address)

    print(f"[Driver] Attaching to existing Chrome at {debugger_address}...")

    driver_path = ChromeDriverManager().install()
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.maximize_window()
    return driver


def _process_company(
    driver: webdriver.Chrome,
    company: str,
    location: str,
    *,
    run_engineering_manager_flow: bool,
    run_northeastern_alumni_flow: bool,
    run_technical_recruiter_flow: bool,
) -> List[Tuple[str, str, str, str, str]]:
    print(f"\n=== Processing company: {company} | Location: {location} ===")

    rows: List[Tuple[str, str, str, str, str]] = []

    def _process_candidates(
        *,
        candidates: List[Tuple[str, str, str]],
        flow_name: str,
        lookup_email: bool = True,
        save_enabled: bool = True,
        connect_enabled: bool = True,
        output_path: str,
        save_row,
    ) -> None:
        if not candidates:
            print(f"[Info] No LinkedIn {flow_name} found for {company} in {location}.")
            if save_enabled:
                placeholder = (company, location, "", "", "NOT_FOUND")
                save_row(rows=[placeholder])
                print(f"[Saved] {company} | {location} | NOT_FOUND -> {output_path}")
                rows.append(placeholder)
            return

        saved_any_row = False

        for idx, (first_name, last_name, profile_url_raw) in enumerate(candidates, start=1):
            profile_url = str(profile_url_raw or "").strip()

            should_connect = bool(
                ENABLE_CONNECT_IN_NORTHEASTERN_ALUMNI_FLOW
                and ENABLE_NORTHEASTERN_ALUMNI_FLOW
                and connect_enabled
                and profile_url
            )
            if should_connect:
                print(
                    f"[Info] ({idx}/{len(candidates)}) Connecting via filtered profile: {profile_url}"
                )
                try:
                    _ensure_site_tab(
                        driver,
                        attr_name="_linkedin_tab_handle",
                        domain="linkedin.com",
                        landing_url="https://www.linkedin.com/feed/",
                    )
                    connect_outcome = connect_to_profile(
                        driver,
                        profile_url,
                        PROFILE_CONNECTOR_NOTE,
                    )
                    print(
                        f"[Info] Connect outcome for {first_name} {last_name}: {connect_outcome.name}"
                    )
                except Exception:
                    print(
                        f"[Error] Failed connect flow for {first_name} {last_name} ({profile_url}).\n{traceback.format_exc()}"
                    )

            if lookup_email:
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
            else:
                print(
                    f"[Info] ({idx}/{len(candidates)}) Skipping email lookup for {first_name} {last_name} — flow='{flow_name}'."
                )
                email_str = "EMAIL_LOOKUP_SKIPPED"

            if save_enabled:
                saved_any_row = True
                row = (company, location, first_name, last_name, email_str)
                rows.append(row)
                save_row(rows=[row])
                print(
                    f"[Saved] {company} | {location} | {first_name} {last_name} | {email_str} -> {output_path}"
                )
                random_delay("After saving result")

        if save_enabled and candidates and not saved_any_row:
            placeholder = (company, location, "", "", "NO_RESULTS_SAVED")
            save_row(rows=[placeholder])
            print(f"[Saved] {company} | {location} | NO_RESULTS_SAVED -> {output_path}")
            rows.append(placeholder)

    if run_engineering_manager_flow:
        try:
            _ensure_site_tab(
                driver,
                attr_name="_linkedin_tab_handle",
                domain="linkedin.com",
                landing_url="https://www.linkedin.com/feed/",
            )
            mgr_candidates = search_engineering_managers(driver, company, location)
        except Exception:
            print(
                f"[Error] Failed to search LinkedIn for engineering managers at {company} in {location}.\n{traceback.format_exc()}"
            )
            mgr_candidates = []

        _process_candidates(
            candidates=mgr_candidates,
            flow_name="engineering manager candidates",
            lookup_email=True,
            save_enabled=True,
            connect_enabled=False,
            output_path=OUTPUT_EXCEL,
            save_row=save_results,
        )

    if run_northeastern_alumni_flow:
        try:
            _ensure_site_tab(
                driver,
                attr_name="_linkedin_tab_handle",
                domain="linkedin.com",
                landing_url="https://www.linkedin.com/feed/",
            )
            alumni_candidates = search_neu_alumni_by_company(driver, company=company)
        except Exception:
            print(
                f"[Error] Failed to search LinkedIn alumni (school={NORTHEASTERN_SCHOOL_NAME}) for {company} in {location}.\n{traceback.format_exc()}"
            )
            alumni_candidates = []

        _process_candidates(
            candidates=alumni_candidates,
            flow_name=f"alumni candidates (school={NORTHEASTERN_SCHOOL_NAME})",
            lookup_email=bool(SEARCH_EMAIL_IN_NORTHEASTERN_ALUMNI_FLOW),
            save_enabled=bool(SAVE_NORTHEASTERN_ALUMNI_RESULTS),
            connect_enabled=True,
            output_path=ALUMNI_OUTPUT_EXCEL,
            save_row=save_alumni_results,
        )

    if run_technical_recruiter_flow:
        try:
            _ensure_site_tab(
                driver,
                attr_name="_linkedin_tab_handle",
                domain="linkedin.com",
                landing_url="https://www.linkedin.com/feed/",
            )
            recruiter_candidates = search_technical_recruiters(driver, company, location)
        except Exception:
            print(
                f"[Error] Failed to search LinkedIn for technical recruiters at {company} in {location}.\n{traceback.format_exc()}"
            )
            recruiter_candidates = []

        _process_candidates(
            candidates=recruiter_candidates,
            flow_name="technical recruiter candidates",
            lookup_email=True,
            save_enabled=True,
            connect_enabled=False,
            output_path=TECHNICAL_RECRUITER_OUTPUT_EXCEL,
            save_row=save_technical_recruiter_results,
        )

    return rows


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    print("=== LinkedIn → CareerShift Email Finder Automation ===")
    print(f"Input Excel: {INPUT_EXCEL}")

    if ENABLE_ENGINEERING_MANAGER_FLOW:
        print(f"Output Excel: {OUTPUT_EXCEL}")
    else:
        print("Output Excel: (engineering-manager flow disabled)")

    if ENABLE_NORTHEASTERN_ALUMNI_FLOW:
        if SAVE_NORTHEASTERN_ALUMNI_RESULTS:
            print(
                f"Alumni Flow: ENABLED (school={NORTHEASTERN_SCHOOL_NAME}) -> {ALUMNI_OUTPUT_EXCEL}"
            )
        else:
            print(
                f"Alumni Flow: ENABLED (school={NORTHEASTERN_SCHOOL_NAME}) -> (no Excel output)"
            )

    if ENABLE_TECHNICAL_RECRUITER_FLOW:
        print(f"Recruiter Flow: ENABLED -> {TECHNICAL_RECRUITER_OUTPUT_EXCEL}")
    if ENABLE_PROFILE_CONNECTOR_FLOW:
        print(
            "Profile Connector Flow: ENABLED "
            f"(single={PROFILE_CONNECTOR_URL}, input={PROFILE_CONNECTOR_INPUT_EXCEL}, output={PROFILE_CONNECTOR_OUTPUT_EXCEL})"
        )
    if ENABLE_CONNECT_IN_NORTHEASTERN_ALUMNI_FLOW:
        print("Connector in Northeastern alumni flow: ENABLED")

    run_company_flows = bool(
        ENABLE_ENGINEERING_MANAGER_FLOW
        or ENABLE_NORTHEASTERN_ALUMNI_FLOW
        or ENABLE_TECHNICAL_RECRUITER_FLOW
    )

    if run_company_flows and not Path(INPUT_EXCEL).exists():
        print(
            f"[Error] Input file '{INPUT_EXCEL}' not found. "
            f"Please create it with columns: company, location."
        )
        return

    companies_df = load_companies() if run_company_flows else None

    processed_mgr_pairs = (
        get_processed_company_location_pairs() if ENABLE_ENGINEERING_MANAGER_FLOW else set()
    )
    processed_alumni_pairs = (
        get_processed_alumni_company_location_pairs()
        if (ENABLE_NORTHEASTERN_ALUMNI_FLOW and SAVE_NORTHEASTERN_ALUMNI_RESULTS)
        else set()
    )
    processed_recruiter_pairs = (
        get_processed_technical_recruiter_company_location_pairs()
        if ENABLE_TECHNICAL_RECRUITER_FLOW
        else set()
    )

    print(
        f"[Resume] Engineering-manager pairs: {len(processed_mgr_pairs)} | Alumni pairs: {len(processed_alumni_pairs)} | Recruiter pairs: {len(processed_recruiter_pairs)}"
    )

    driver = _init_driver()

    try:
        if ENABLE_PROFILE_CONNECTOR_FLOW:
            print(
                "[Skip] Standalone profile connector is disabled by alumni-only policy. "
                "Connector runs only inside Northeastern alumni flow."
            )

        if not run_company_flows:
            print("[Info] Company/location flows are disabled. Nothing else to process.")
            return

        for _, row in companies_df.iterrows():
            company = str(row["company"]).strip()
            location = str(row["location"]).strip()
            if not company or not location:
                continue

            pair = (company, location)
            skip_mgr = (not ENABLE_ENGINEERING_MANAGER_FLOW) or (pair in processed_mgr_pairs)
            skip_alumni = (pair in processed_alumni_pairs) if ENABLE_NORTHEASTERN_ALUMNI_FLOW else True
            skip_recruiter = (
                (pair in processed_recruiter_pairs) if ENABLE_TECHNICAL_RECRUITER_FLOW else True
            )

            if skip_mgr and skip_alumni and skip_recruiter:
                print(
                    f"[Skip] Already processed {company} in {location} according to enabled outputs."
                )
                continue

            try:
                _process_company(
                    driver,
                    company,
                    location,
                    run_engineering_manager_flow=not skip_mgr,
                    run_northeastern_alumni_flow=(
                        ENABLE_NORTHEASTERN_ALUMNI_FLOW and (not skip_alumni)
                    ),
                    run_technical_recruiter_flow=(
                        ENABLE_TECHNICAL_RECRUITER_FLOW and (not skip_recruiter)
                    ),
                )
            except Exception:
                print(
                    f"[Error] Unexpected error while processing {company} in {location}.\n{traceback.format_exc()}"
                )
                continue

    finally:
        print("[Driver] Closing browser session handle (not the actual Chrome window).")
        try:
            driver.quit()
        except Exception:
            pass

    print("=== Done. ===")


if __name__ == "__main__":
    main()
