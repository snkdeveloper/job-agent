from __future__ import annotations

import time
from pathlib import Path
from typing import List, Tuple
from urllib.parse import quote_plus

from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import (
    DEBUG_LINKEDIN_DUMPS,
    MAX_LINKEDIN_RESULTS,
    NORTHEASTERN_SCHOOL_FILTER,
    random_delay,
)


def _wait(driver: WebDriver, timeout: int = 20) -> WebDriverWait:
    return WebDriverWait(driver, timeout)


def _click_linkedin_filter_label(driver: WebDriver, *, label_text: str, timeout: int = 20) -> bool:
    """Click a LinkedIn search filter pill/label by its visible text.

    This is intentionally defensive because LinkedIn's DOM varies by account/AB test.
    """

    normalized = " ".join(label_text.split())

    # Prefer the top filter bar / pill list when present.
    xpaths = [
        "//div[contains(@class,'search-reusables__filter') or contains(@class,'search-reusables__filters')]"
        "//button[.//*[normalize-space()=$t] or normalize-space()=$t]",
        "//div[contains(@class,'search-reusables__filter') or contains(@class,'search-reusables__filters')]"
        "//*[@role='button'][.//*[normalize-space()=$t] or normalize-space()=$t]",
        "//div[contains(@class,'search-reusables__filter') or contains(@class,'search-reusables__filters')]"
        "//label[.//*[normalize-space()=$t] or normalize-space()=$t]",
        "//button[.//*[normalize-space()=$t] or normalize-space()=$t]",
        "//*[@role='button'][.//*[normalize-space()=$t] or normalize-space()=$t]",
        "//label[.//*[normalize-space()=$t] or normalize-space()=$t]",
        "//span[normalize-space()=$t]/ancestor::button[1]",
        "//span[normalize-space()=$t]/ancestor::*[@role='button'][1]",
        "//span[normalize-space()=$t]/ancestor::label[1]",
    ]

    # Selenium's XPath doesn't support variable binding directly; substitute safely.
    # We only allow an exact-text match, so quoting is straightforward.
    if "'" in normalized and '"' in normalized:
        # Extremely unlikely for filter labels; bail out rather than risk malformed XPath.
        return False
    if "'" in normalized:
        quoted = f'"{normalized}"'
    else:
        quoted = f"'{normalized}'"

    def _try_click(el) -> bool:
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                el,
            )
        except Exception:
            pass

        # If this is a <label for="...">, clicking the associated control can be more reliable.
        try:
            if (el.tag_name or "").lower() == "label":
                target_id = (el.get_attribute("for") or "").strip()
                if target_id:
                    try:
                        target = driver.find_element(By.ID, target_id)
                        try:
                            driver.execute_script(
                                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                                target,
                            )
                        except Exception:
                            pass
                        try:
                            target.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", target)
                        return True
                    except NoSuchElementException:
                        pass
        except Exception:
            pass

        try:
            el.click()
        except Exception:
            driver.execute_script("arguments[0].click();", el)
        return True

    deadline = time.monotonic() + max(0.0, float(timeout))
    last_exc: Exception | None = None

    # Single overall timeout; poll quickly to avoid multi-minute hangs.
    while time.monotonic() < deadline:
        for xp in xpaths:
            xp_resolved = xp.replace("$t", quoted)
            try:
                elements = driver.find_elements(By.XPATH, xp_resolved)
                if not elements:
                    continue
                for el in elements:
                    try:
                        if not el.is_displayed():
                            continue
                    except StaleElementReferenceException:
                        continue
                    try:
                        if _try_click(el):
                            return True
                    except (StaleElementReferenceException, TimeoutException) as exc:
                        last_exc = exc
                        continue
                    except Exception as exc:
                        last_exc = exc
                        continue
            except Exception as exc:
                last_exc = exc
                continue

        time.sleep(0.25)

    if last_exc is not None:
        print(f"[LinkedIn] Could not click filter label '{label_text}': {last_exc}")
    else:
        print(f"[LinkedIn] Could not find filter label '{label_text}' within {timeout}s")
    _dump_linkedin_debug(driver, tag=f"filter_label_not_found_{label_text}")
    return False


def _apply_linkedin_locations_filter(driver: WebDriver, *, location: str, timeout: int = 20) -> bool:
    """In the open Locations popover, type a location and click 'Show results'."""

    location = (location or "").strip()
    if not location:
        return False

    input_xpaths = [
        "//input[@data-testid='typeahead-input' and normalize-space(@placeholder)='Add a location']",
        "//input[normalize-space(@placeholder)='Add a location']",
        "//input[contains(@placeholder,'Add a location')]",
    ]

    typeahead = None
    last_exc: Exception | None = None
    for xp in input_xpaths:
        try:
            typeahead = _wait(driver, timeout=timeout).until(
                EC.presence_of_element_located((By.XPATH, xp))
            )
            if typeahead and typeahead.is_displayed():
                break
        except Exception as exc:
            last_exc = exc
            typeahead = None

    if typeahead is None:
        print(f"[LinkedIn] Locations typeahead not found for '{location}': {last_exc}")
        _dump_linkedin_debug(driver, tag=f"locations_typeahead_missing_{location}")
        return False

    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
            typeahead,
        )
    except Exception:
        pass

    # Focus the input (sometimes it has tabindex=-1).
    try:
        typeahead.click()
    except Exception:
        try:
            driver.execute_script("arguments[0].focus();", typeahead)
        except Exception:
            pass
        try:
            driver.execute_script("arguments[0].click();", typeahead)
        except Exception:
            pass

    # Clear any existing value.
    try:
        typeahead.send_keys(Keys.COMMAND, "a")
        typeahead.send_keys(Keys.BACKSPACE)
    except Exception:
        try:
            typeahead.clear()
        except Exception:
            pass

    print(f"[LinkedIn] Typing location into filter: {location}")
    typeahead.send_keys(location)

    # Give the typeahead a moment to populate suggestions.
    time.sleep(0.5)

    # Accept the top suggestion to ensure the filter is applied.
    try:
        typeahead.send_keys(Keys.ARROW_DOWN)
        typeahead.send_keys(Keys.ENTER)
    except Exception:
        pass

    # Click "Show results".
    show_xpaths = [
        "//a[normalize-space()='Show results' or .//*[normalize-space()='Show results']]",
        "//button[normalize-space()='Show results' or .//*[normalize-space()='Show results']]",
    ]
    for xp in show_xpaths:
        try:
            show = _wait(driver, timeout=timeout).until(
                EC.presence_of_element_located((By.XPATH, xp))
            )
            if not show.is_displayed():
                continue
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                    show,
                )
            except Exception:
                pass
            try:
                show.click()
            except Exception:
                driver.execute_script("arguments[0].click();", show)
            return True
        except Exception as exc:
            last_exc = exc
            continue

    print(f"[LinkedIn] Could not click 'Show results' after typing location '{location}': {last_exc}")
    _dump_linkedin_debug(driver, tag=f"locations_show_results_missing_{location}")
    return False


def _popover_reset_enabled(popover_root) -> bool:
    try:
        reset_btn = popover_root.find_element(
            By.XPATH, ".//button[.//*[normalize-space()='Reset'] or normalize-space()='Reset']"
        )
        disabled = (reset_btn.get_attribute("disabled") or "").strip()
        return not bool(disabled)
    except Exception:
        # If we can't locate the Reset control, don't treat it as a failure.
        return True


def _select_first_typeahead_suggestion(driver: WebDriver, *, typeahead, timeout: int = 10) -> bool:
    """Select the first suggestion for a LinkedIn typeahead input.

    Prefers clicking the first visible option element in the open popover.
    Falls back to ARROW_DOWN + ENTER.
    """

    try:
        popover_root = typeahead.find_element(By.XPATH, "ancestor::*[@popover='manual'][1]")
    except Exception:
        popover_root = driver

    deadline = time.monotonic() + max(0.0, float(timeout))
    last_exc: Exception | None = None

    # Wait for suggestions to render, then click the first option.
    while time.monotonic() < deadline:
        try:
            options = []
            try:
                options = popover_root.find_elements(By.CSS_SELECTOR, "[role='option']")
            except Exception:
                options = []
            if not options:
                # Fallback: some builds don't use role=option. Try a loose-but-contained heuristic.
                try:
                    options = popover_root.find_elements(
                        By.XPATH,
                        ".//*[self::li or self::button or self::div][@data-testid and contains(@data-testid,'typeahead') or @role='option']",
                    )
                except Exception:
                    options = []

            for opt in options:
                try:
                    if not opt.is_displayed():
                        continue
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'nearest', inline: 'nearest'});",
                        opt,
                    )
                    try:
                        opt.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", opt)

                    # If Reset exists, it should become enabled after a selection.
                    try:
                        _wait(driver, timeout=3).until(lambda d: _popover_reset_enabled(popover_root))
                    except Exception:
                        pass
                    return True
                except (StaleElementReferenceException, TimeoutException) as exc:
                    last_exc = exc
                    continue
                except Exception as exc:
                    last_exc = exc
                    continue
        except Exception as exc:
            last_exc = exc

        time.sleep(0.25)

    # Fallback to keyboard selection.
    try:
        typeahead.send_keys(Keys.ARROW_DOWN)
        typeahead.send_keys(Keys.ENTER)
        return True
    except Exception as exc:
        last_exc = exc
        print(f"[LinkedIn] Failed to select first typeahead suggestion: {last_exc}")
        return False


def _apply_linkedin_current_companies_filter(driver: WebDriver, *, company: str, timeout: int = 20) -> bool:
    """In the open Current companies popover, type a company and click 'Show results'."""

    company = (company or "").strip()
    if not company:
        return False

    input_xpaths = [
        "//input[@data-testid='typeahead-input' and normalize-space(@placeholder)='Add a company']",
        "//input[normalize-space(@placeholder)='Add a company']",
        "//input[contains(@placeholder,'Add a company')]",
    ]

    typeahead = None
    last_exc: Exception | None = None
    for xp in input_xpaths:
        try:
            typeahead = _wait(driver, timeout=timeout).until(
                EC.presence_of_element_located((By.XPATH, xp))
            )
            if typeahead and typeahead.is_displayed():
                break
        except Exception as exc:
            last_exc = exc
            typeahead = None

    if typeahead is None:
        print(f"[LinkedIn] Company typeahead not found for '{company}': {last_exc}")
        _dump_linkedin_debug(driver, tag=f"company_typeahead_missing_{company}")
        return False

    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
            typeahead,
        )
    except Exception:
        pass

    # Focus the input (sometimes it has tabindex=-1).
    try:
        typeahead.click()
    except Exception:
        try:
            driver.execute_script("arguments[0].focus();", typeahead)
        except Exception:
            pass
        try:
            driver.execute_script("arguments[0].click();", typeahead)
        except Exception:
            pass

    # Clear any existing value.
    try:
        typeahead.send_keys(Keys.COMMAND, "a")
        typeahead.send_keys(Keys.BACKSPACE)
    except Exception:
        try:
            typeahead.clear()
        except Exception:
            pass

    print(f"[LinkedIn] Typing company into filter: {company}")
    typeahead.send_keys(company)

    selected = _select_first_typeahead_suggestion(driver, typeahead=typeahead, timeout=10)
    if not selected:
        _dump_linkedin_debug(driver, tag=f"company_typeahead_select_failed_{company}")
        return False

    # Prefer clicking "Show results" within the same open popover.
    search_root = driver
    try:
        search_root = typeahead.find_element(By.XPATH, "ancestor::*[@popover='manual'][1]")
    except Exception:
        search_root = driver

    show_xpaths = [
        ".//a[normalize-space()='Show results' or .//*[normalize-space()='Show results']]",
        ".//button[normalize-space()='Show results' or .//*[normalize-space()='Show results']]",
    ]
    for xp in show_xpaths:
        try:
            show = _wait(search_root, timeout=timeout).until(
                EC.presence_of_element_located((By.XPATH, xp))
            )
            if not show.is_displayed():
                continue
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                    show,
                )
            except Exception:
                pass
            try:
                show.click()
            except Exception:
                driver.execute_script("arguments[0].click();", show)
            return True
        except Exception as exc:
            last_exc = exc
            continue

    print(f"[LinkedIn] Could not click 'Show results' after typing company '{company}': {last_exc}")
    _dump_linkedin_debug(driver, tag=f"company_show_results_missing_{company}")
    return False


def _parse_name(full_name: str) -> Tuple[str, str]:
    parts = [p for p in full_name.strip().split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _dump_linkedin_debug(driver: WebDriver, *, tag: str) -> None:
    if not DEBUG_LINKEDIN_DUMPS:
        return

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path("debug") / "linkedin"
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_tag = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in tag)[:80]
    base = out_dir / f"{ts}_{safe_tag}"

    try:
        (base.with_suffix(".url.txt")).write_text(driver.current_url or "", encoding="utf-8")
    except Exception:
        pass
    try:
        (base.with_suffix(".html")).write_text(driver.page_source or "", encoding="utf-8")
    except Exception:
        pass
    try:
        driver.get_screenshot_as_file(str(base.with_suffix(".png")))
    except Exception:
        pass


def _page_state_hint(driver: WebDriver) -> str:
    url = (driver.current_url or "").lower()
    title = (driver.title or "").lower()
    if "checkpoint" in url or "challenge" in url or "captcha" in url:
        return "BLOCKED_CHECKPOINT"
    if "login" in url or "signup" in url or "authwall" in url:
        return "NOT_LOGGED_IN"
    if "sign in" in title:
        return "NOT_LOGGED_IN"

    # Empty state patterns
    try:
        if driver.find_elements(By.CSS_SELECTOR, ".artdeco-empty-state"):
            return "EMPTY_STATE"
    except Exception:
        pass
    try:
        if driver.find_elements(By.XPATH, "//*[contains(., 'No results found')]"):
            return "NO_RESULTS_TEXT"
    except Exception:
        pass

    return "UNKNOWN"


def _wait_for_results_or_terminal_state(driver: WebDriver, timeout: int = 35) -> str:
    """Wait until results exist or the page is in a terminal/non-usable state.

    Returns the final state hint.
    """

    terminal = {"EMPTY_STATE", "NO_RESULTS_TEXT", "NOT_LOGGED_IN", "BLOCKED_CHECKPOINT"}
    try:
        _wait(driver, timeout=timeout).until(
            lambda d: (len(_find_result_cards(d)) > 0) or (_page_state_hint(d) in terminal)
        )
    except TimeoutException:
        pass
    return _page_state_hint(driver)


def _find_result_cards(driver: WebDriver):
    xpaths = [
        "//div[@role='listitem']",
        "//li[contains(@class,'reusable-search__result-container')]",
        "//div[contains(@class,'reusable-search__result-container')]",
        "//li[@data-view-name='search-entity-result']",
        "//div[@data-view-name='search-entity-result']",
        "//li[contains(@class,'entity-result__item')]",
        "//div[contains(@class,'entity-result__item')]",
        "//li[.//a[contains(@href,'/in/')]]",
    ]
    cards = []
    for xp in xpaths:
        try:
            found = driver.find_elements(By.XPATH, xp)
            if found:
                cards.extend(found)
        except Exception:
            continue
    return cards


def _extract_profile_url(card) -> str:
    try:
        a = card.find_element(By.XPATH, ".//a[contains(@href,'/in/')]")
        href = (a.get_attribute("href") or "").strip()
        if href:
            href = href.split("?", 1)[0].split("#", 1)[0]
        return href
    except Exception:
        return ""


def _extract_full_name(card) -> str:
    # Prefer stable accessible names first.
    try:
        figure = card.find_element(
            By.XPATH, ".//figure[@aria-label and normalize-space(@aria-label)!='']"
        )
        name = (figure.get_attribute("aria-label") or "").strip()
        if name:
            return name
    except Exception:
        pass

    try:
        img = card.find_element(By.XPATH, ".//img[@alt and normalize-space(@alt)!='']")
        name = (img.get_attribute("alt") or "").strip()
        if name:
            return name
    except Exception:
        pass

    # Fallback: title text inside the primary profile link
    try:
        a = card.find_element(By.XPATH, ".//a[contains(@href,'/in/')]")
        name = (a.get_attribute("aria-label") or "").strip()
        if name:
            return name
        name = (a.text or "").strip()
        if name:
            return name
    except Exception:
        pass

    # Fallback: common title span
    try:
        span = card.find_element(By.CSS_SELECTOR, "span.entity-result__title-text")
        name = (span.text or "").strip()
        if name:
            return name
    except Exception:
        pass

    return ""


def search_engineering_managers(
    driver: WebDriver, company: str, location: str
) -> List[Tuple[str, str, str]]:
    """
    Search LinkedIn for 'Engineering Manager'.

    This function intentionally navigates using a simple keyword-only query
    so that UI filters (company, location, etc.) can be applied afterwards.

    Returns a list of (first_name, last_name, profile_url) tuples for up to MAX_LINKEDIN_RESULTS people.
    """
    # NOTE: company/location are kept in the signature because the pipeline processes
    # a company+location spreadsheet, but filtering will be applied via LinkedIn UI later.
    query = "engineering manager"
    query_url = (
        "https://www.linkedin.com/search/results/people/"
        f"?keywords={quote_plus(query)}"
    )
    print(f"[LinkedIn] Navigating to: {query_url}")
    driver.get(query_url)

    # Even if random_delay is configured as a no-op, we still want a real wait
    # for results to exist before parsing.
    random_delay("Initial LinkedIn results load")

    candidates: List[Tuple[str, str, str]] = []

    # Wait for either results, an empty state, or an auth/checkpoint wall.
    hint = _wait_for_results_or_terminal_state(driver, timeout=35)
    if hint == "UNKNOWN" and len(_find_result_cards(driver)) == 0:
        print(
            f"[LinkedIn] Timed out waiting for results for company='{company}', location='{location}'. State={hint}"
        )
        _dump_linkedin_debug(driver, tag=f"timeout_{company}_{location}")
        return candidates

    if hint in {"NOT_LOGGED_IN", "BLOCKED_CHECKPOINT"}:
        print(
            f"[LinkedIn] Search page not usable (state={hint}). Are you logged in and not seeing a checkpoint/captcha?"
        )
        _dump_linkedin_debug(driver, tag=f"blocked_{company}_{location}")
        return candidates

    # Open the Locations filter UI (filters can be applied afterwards).
    print("[LinkedIn] Attempting to click filter label: Locations")
    clicked_locations = _click_linkedin_filter_label(driver, label_text="Locations", timeout=10)
    print(f"[LinkedIn] Clicked Locations label: {clicked_locations}")

    if clicked_locations:
        applied = _apply_linkedin_locations_filter(driver, location=location, timeout=20)
        print(f"[LinkedIn] Applied location filter '{location}': {applied}")
        if applied:
            # Give the results page a moment to refresh.
            random_delay("After LinkedIn location filter")
            hint = _wait_for_results_or_terminal_state(driver, timeout=35)

    # Next filter step: open the Current companies filter UI.
    print("[LinkedIn] Attempting to click filter label: Current companies")
    clicked_current_companies = _click_linkedin_filter_label(
        driver, label_text="Current companies", timeout=10
    )
    print(f"[LinkedIn] Clicked Current companies label: {clicked_current_companies}")

    if clicked_current_companies:
        applied_company = _apply_linkedin_current_companies_filter(
            driver, company=company, timeout=20
        )
        print(f"[LinkedIn] Applied current company filter '{company}': {applied_company}")
        if applied_company:
            random_delay("After LinkedIn current company filter")
            hint = _wait_for_results_or_terminal_state(driver, timeout=35)

    listitems = _find_result_cards(driver)
    if not listitems:
        print(
            f"[LinkedIn] No result cards detected for company='{company}', location='{location}'. State={hint}"
        )
        _dump_linkedin_debug(driver, tag=f"no_cards_{company}_{location}")
        return candidates

    seen_profile_urls: set[str] = set()
    for item in listitems:
        if len(candidates) >= MAX_LINKEDIN_RESULTS:
            break

        try:
            full_name = _extract_full_name(item)
        except StaleElementReferenceException:
            continue
        except Exception:
            full_name = ""

        if not full_name or "LinkedIn Member" in full_name:
            continue

        try:
            profile_url = _extract_profile_url(item)
        except StaleElementReferenceException:
            continue
        except Exception:
            profile_url = ""

        if not profile_url:
            continue
        if profile_url in seen_profile_urls:
            continue

        first, last = _parse_name(full_name)
        if not first:
            continue

        candidates.append((first, last, profile_url))
        seen_profile_urls.add(profile_url)

    print(
        f"[LinkedIn] Found {len(candidates)} engineering manager candidates for {company} in {location}."
    )

    if len(candidates) == 0:
        # Useful for diagnosing intermittent failures or UI changes.
        _dump_linkedin_debug(driver, tag=f"zero_{company}_{location}")

    return candidates


def search_technical_recruiters(
    driver: WebDriver, company: str, location: str
) -> List[Tuple[str, str, str]]:
    """Search LinkedIn for 'technical recruiter' and filter by company/location."""

    query = "technical recruiter"
    query_url = (
        "https://www.linkedin.com/search/results/people/"
        f"?keywords={quote_plus(query)}"
    )
    print(f"[LinkedIn] Navigating to: {query_url}")
    driver.get(query_url)

    random_delay("Initial LinkedIn results load")

    candidates: List[Tuple[str, str, str]] = []

    hint = _wait_for_results_or_terminal_state(driver, timeout=35)
    if hint == "UNKNOWN" and len(_find_result_cards(driver)) == 0:
        print(
            f"[LinkedIn] Timed out waiting for recruiter results for company='{company}', location='{location}'. State={hint}"
        )
        _dump_linkedin_debug(driver, tag=f"recruiter_timeout_{company}_{location}")
        return candidates

    if hint in {"NOT_LOGGED_IN", "BLOCKED_CHECKPOINT"}:
        print(
            f"[LinkedIn] Recruiter search page not usable (state={hint}). Are you logged in and not seeing a checkpoint/captcha?"
        )
        _dump_linkedin_debug(driver, tag=f"recruiter_blocked_{company}_{location}")
        return candidates

    print("[LinkedIn] Attempting to click filter label: Locations")
    clicked_locations = _click_linkedin_filter_label(driver, label_text="Locations", timeout=10)
    print(f"[LinkedIn] Clicked Locations label: {clicked_locations}")

    if clicked_locations:
        applied = _apply_linkedin_locations_filter(driver, location=location, timeout=20)
        print(f"[LinkedIn] Applied location filter '{location}': {applied}")
        if applied:
            random_delay("After LinkedIn location filter")
            hint = _wait_for_results_or_terminal_state(driver, timeout=35)

    print("[LinkedIn] Attempting to click filter label: Current companies")
    clicked_current_companies = _click_linkedin_filter_label(
        driver, label_text="Current companies", timeout=10
    )
    print(f"[LinkedIn] Clicked Current companies label: {clicked_current_companies}")

    if clicked_current_companies:
        applied_company = _apply_linkedin_current_companies_filter(
            driver, company=company, timeout=20
        )
        print(f"[LinkedIn] Applied current company filter '{company}': {applied_company}")
        if applied_company:
            random_delay("After LinkedIn current company filter")
            hint = _wait_for_results_or_terminal_state(driver, timeout=35)

    listitems = _find_result_cards(driver)
    if not listitems:
        print(
            f"[LinkedIn] No recruiter result cards detected for company='{company}', location='{location}'. State={hint}"
        )
        _dump_linkedin_debug(driver, tag=f"recruiter_no_cards_{company}_{location}")
        return candidates

    seen_profile_urls: set[str] = set()
    for item in listitems:
        if len(candidates) >= MAX_LINKEDIN_RESULTS:
            break

        try:
            full_name = _extract_full_name(item)
        except StaleElementReferenceException:
            continue
        except Exception:
            full_name = ""

        if not full_name or "LinkedIn Member" in full_name:
            continue

        try:
            profile_url = _extract_profile_url(item)
        except StaleElementReferenceException:
            continue
        except Exception:
            profile_url = ""

        if not profile_url:
            continue
        if profile_url in seen_profile_urls:
            continue

        first, last = _parse_name(full_name)
        if not first:
            continue

        candidates.append((first, last, profile_url))
        seen_profile_urls.add(profile_url)

    print(
        f"[LinkedIn] Found {len(candidates)} technical recruiter candidates for {company} in {location}."
    )

    if len(candidates) == 0:
        _dump_linkedin_debug(driver, tag=f"recruiter_zero_{company}_{location}")

    return candidates


def search_neu_alumni_by_company(
    driver: WebDriver, *, company: str, location: str | None = None
) -> List[Tuple[str, str, str]]:
    """Search LinkedIn People for Northeastern University alumni working at a company.

    Applies NEU via URL param: schoolFilter={NORTHEASTERN_SCHOOL_FILTER}
    and then applies the Current companies (and optionally Locations) UI filters.
    """

    query_url = (
        "https://www.linkedin.com/search/results/people/"
        f"?keywords=&schoolFilter={NORTHEASTERN_SCHOOL_FILTER}"
    )
    print(f"[LinkedIn] Navigating to: {query_url}")
    driver.get(query_url)

    random_delay("Initial LinkedIn NEU alumni results load")

    candidates: List[Tuple[str, str, str]] = []

    hint = _wait_for_results_or_terminal_state(driver, timeout=35)
    if hint == "UNKNOWN" and len(_find_result_cards(driver)) == 0:
        print(
            f"[LinkedIn] Timed out waiting for NEU alumni results for company='{company}'. State={hint}"
        )
        _dump_linkedin_debug(driver, tag=f"neu_alumni_timeout_{company}")
        return candidates

    if hint in {"NOT_LOGGED_IN", "BLOCKED_CHECKPOINT"}:
        print(
            f"[LinkedIn] NEU alumni search page not usable (state={hint}). Are you logged in and not seeing a checkpoint/captcha?"
        )
        _dump_linkedin_debug(driver, tag=f"neu_alumni_blocked_{company}")
        return candidates

    if location:
        print("[LinkedIn] Attempting to click filter label: Locations")
        clicked_locations = _click_linkedin_filter_label(driver, label_text="Locations", timeout=10)
        print(f"[LinkedIn] Clicked Locations label: {clicked_locations}")
        if clicked_locations:
            applied = _apply_linkedin_locations_filter(driver, location=location, timeout=20)
            print(f"[LinkedIn] Applied location filter '{location}': {applied}")
            if applied:
                random_delay("After LinkedIn location filter")
                hint = _wait_for_results_or_terminal_state(driver, timeout=35)

    print("[LinkedIn] Attempting to click filter label: Current companies")
    clicked_current_companies = _click_linkedin_filter_label(
        driver, label_text="Current companies", timeout=10
    )
    print(f"[LinkedIn] Clicked Current companies label: {clicked_current_companies}")

    if clicked_current_companies:
        applied_company = _apply_linkedin_current_companies_filter(driver, company=company, timeout=20)
        print(f"[LinkedIn] Applied current company filter '{company}': {applied_company}")
        if applied_company:
            random_delay("After LinkedIn current company filter")
            hint = _wait_for_results_or_terminal_state(driver, timeout=35)

    listitems = _find_result_cards(driver)
    if not listitems:
        print(
            f"[LinkedIn] No result cards detected for NEU alumni company='{company}'. State={hint}"
        )
        _dump_linkedin_debug(driver, tag=f"neu_alumni_no_cards_{company}")
        return candidates

    seen_profile_urls: set[str] = set()
    for item in listitems:
        if len(candidates) >= MAX_LINKEDIN_RESULTS:
            break

        try:
            full_name = _extract_full_name(item)
        except StaleElementReferenceException:
            continue
        except Exception:
            full_name = ""

        if not full_name or "LinkedIn Member" in full_name:
            continue

        try:
            profile_url = _extract_profile_url(item)
        except StaleElementReferenceException:
            continue
        except Exception:
            profile_url = ""

        if not profile_url:
            continue
        if profile_url in seen_profile_urls:
            continue

        first, last = _parse_name(full_name)
        if not first:
            continue

        candidates.append((first, last, profile_url))
        seen_profile_urls.add(profile_url)

    print(f"[LinkedIn] Found {len(candidates)} NEU alumni candidates for {company}.")

    if len(candidates) == 0:
        _dump_linkedin_debug(driver, tag=f"neu_alumni_zero_{company}")

    return candidates

