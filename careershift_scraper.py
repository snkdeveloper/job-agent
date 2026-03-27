from __future__ import annotations

import re
from typing import Optional

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import random_delay


EMAIL_REGEX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _wait(driver: WebDriver, timeout: int = 20) -> WebDriverWait:
    return WebDriverWait(driver, timeout)


def _go_to_contacts_search(driver: WebDriver, *, force_reload: bool = False) -> None:
    # Use the dedicated Contacts Search page (user provided).
    target_url = "https://careershift.com/App/Contacts/Search"
    current = (driver.current_url or "").lower()
    # NOTE: '/App/Contacts/SearchDetails' contains '/App/Contacts/Search' as a
    # substring, so we must explicitly treat details pages as NOT being the
    # search form page.
    if force_reload or ("/app/contacts/search" not in current) or (
        "/app/contacts/searchdetails" in current
    ):
        driver.get(target_url)
        random_delay("CareerShift contacts search load")

    # Wait for the form fields to exist.
    _wait(driver, timeout=30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "form[action^='/App/Contacts/Search']"))
    )
    _wait(driver, timeout=30).until(
        EC.presence_of_element_located((By.ID, "FirstName"))
    )


def _set_input(driver: WebDriver, input_id: str, value: str) -> None:
    el = _wait(driver, timeout=20).until(
        EC.presence_of_element_located((By.ID, input_id))
    )
    el.clear()
    if value:
        el.send_keys(value)


def _extract_first_email_from_text(text: str) -> Optional[str]:
    match = EMAIL_REGEX.search(text or "")
    if match:
        return match.group(0)
    return None


def _normalize_name_token(token: str) -> str:
    # Lowercase, strip punctuation commonly present in initials.
    return re.sub(r"[^a-z0-9]", "", (token or "").lower())


def _name_matches_result(full_name: str, first_name: str, last_name: str) -> bool:
    """Best-effort match between the query name and a result card name."""
    if not full_name:
        return False
    first = _normalize_name_token(first_name)
    last = _normalize_name_token(last_name)
    if not first and not last:
        return True

    tokens = [_normalize_name_token(t) for t in (full_name or "").split() if t.strip()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return False

    # Match first token against first name.
    if first and tokens[0] != first:
        return False

    # If we have a last name, match last token or last initial.
    if last:
        last_token = tokens[-1]
        if last_token == last:
            return True
        # Handle cases like "Rahul L." where last_name is "L." or last initial.
        if len(last) == 1 and last_token.startswith(last):
            return True
        if len(last_token) == 1 and last.startswith(last_token):
            return True
        return False

    return True


def _extract_email_from_details_page(driver: WebDriver) -> Optional[str]:
    # Details page contains explicit mailto links.
    try:
        _wait(driver, timeout=25).until(
            EC.presence_of_element_located(
                (By.XPATH, "//a[starts-with(@href,'mailto:') and string-length(@href) > 7]")
            )
        )
    except TimeoutException:
        return None

    try:
        mailtos = driver.find_elements(
            By.XPATH,
            "//a[starts-with(@href,'mailto:') and string-length(@href) > 7]",
        )
    except Exception:
        mailtos = []

    for a in mailtos:
        href = (a.get_attribute("href") or "").strip()
        if href.lower().startswith("mailto:"):
            candidate = href.split("mailto:", 1)[1].split("?", 1)[0].strip()
            email = _extract_first_email_from_text(candidate)
            if email:
                return email
    return None


def _open_matching_contact_details(
    driver: WebDriver, first_name: str, last_name: str
) -> bool:
    """Click the best matching contact name in search results.

    Returns True if navigation to a details page was triggered.
    """
    # Only open details when the card has the explicit contact icon to avoid
    # spending credits on contacts without available contact details.
    required_icon_xpath = (
        ".//a[contains(@class,'contact-icon') "
        "and @title='View Contact Details for more information' "
        "and .//span[contains(@class,'fa-envelope-o')]]"
    )

    # Result cards contain links like /App/Contacts/SearchDetails?personId=...
    # Prefer the name link (id starts with contact-title-) to avoid picking controls.
    try:
        anchors = driver.find_elements(
            By.XPATH,
            "//a[starts-with(@id,'contact-title-') and contains(@href,'/App/Contacts/SearchDetails') and contains(@href,'personId=')]",
        )
    except Exception:
        anchors = []

    if not anchors:
        try:
            anchors = driver.find_elements(
                By.XPATH,
                "//h3[contains(@class,'title')]//a[contains(@href,'/App/Contacts/SearchDetails') and contains(@href,'personId=')]",
            )
        except Exception:
            anchors = []

    if not anchors:
        return False

    chosen = None
    for a in anchors:
        try:
            name_text = (a.text or a.get_attribute("innerText") or "").strip()
            if _name_matches_result(name_text, first_name, last_name):
                # Ensure this candidate card includes the required contact icon.
                card = a.find_element(
                    By.XPATH,
                    "ancestor::div[contains(@class,'cs-flex')][1]",
                )
                icon_matches = card.find_elements(By.XPATH, required_icon_xpath)
                if icon_matches:
                    chosen = a
                    break
        except Exception:
            continue

    if chosen is None:
        # Do not open any details page unless the card has the required icon.
        return False

    # Try a robust click.
    href = (chosen.get_attribute("href") or "").strip()
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
            chosen,
        )
    except Exception:
        pass

    click_succeeded = False
    try:
        driver.execute_script("arguments[0].click();", chosen)
        click_succeeded = True
    except Exception:
        try:
            chosen.click()
            click_succeeded = True
        except Exception:
            click_succeeded = False

    if not click_succeeded and href:
        # Fallback: navigate directly via href.
        if href.startswith("/"):
            href = "https://careershift.com" + href
        driver.get(href)
        click_succeeded = True

    if not click_succeeded:
        return False

    # Wait for details page signature (URL or H1 title).
    try:
        _wait(driver, timeout=25).until(
            lambda d: (
                "/app/contacts/searchdetails" in ((d.current_url or "").lower())
                or len(d.find_elements(By.CSS_SELECTOR, "h1.title")) > 0
            )
        )
        return True
    except TimeoutException:
        return False


def find_email(first_name: str, last_name: str, company: str, driver: WebDriver) -> str:
    """
    Search CareerShift for the given person and company, returning the first email found.

    Returns "NOT_FOUND" if no email could be extracted.

    NOTE: The exact selectors may need adjusting to match CareerShift's UI.
    """
    query = f"{first_name} {last_name} {company}".strip()
    print(f"[CareerShift] Searching for: {query}")

    # Always return to the Contacts Search page after each lookup so the next
    # person search starts from a known state (user requirement).
    target_search_url = "https://careershift.com/App/Contacts/Search"

    try:
        # Assume the user is already logged in to CareerShift in the attached Chrome.
        # Force a reload so we don't accidentally interact with stale results from
        # a previous search.
        _go_to_contacts_search(driver, force_reload=True)

        # Fill the dedicated contacts search form.
        _set_input(driver, "FirstName", first_name)
        _set_input(driver, "LastName", last_name)
        _set_input(driver, "CompanyName", company)

        random_delay("CareerShift before contacts search submit")

        # Submit the form.
        url_before_submit = driver.current_url or ""
        try:
            submit_btn = _wait(driver, timeout=15).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "#main_search form button[type='submit']")
                )
            )
            submit_btn.click()
        except TimeoutException:
            # Fallback: submit the form element directly.
            form = driver.find_element(By.CSS_SELECTOR, "#main_search form")
            form.submit()

        # If the click didn't trigger anything (common with overlays), submit explicitly.
        def _submit_progress(d: WebDriver) -> bool:
            try:
                if (d.current_url or "") != url_before_submit and "#contacts_search_results" in (d.current_url or ""):
                    return True
            except Exception:
                pass
            try:
                if d.find_elements(
                    By.XPATH,
                    "//a[contains(@href,'/App/Contacts/SearchDetails') and contains(@href,'personId=')]",
                ):
                    return True
            except Exception:
                pass
            try:
                container = d.find_element(By.ID, "contacts_search_results")
                container_text = (container.text or "").lower()
                if "no results" in container_text or "no contacts" in container_text:
                    return True
            except Exception:
                pass
            return False

        try:
            _wait(driver, timeout=8).until(_submit_progress)
        except TimeoutException:
            try:
                driver.find_element(By.CSS_SELECTOR, "#main_search form").submit()
            except Exception:
                pass

        # Results are appended/available near the anchor mentioned in the form action.
        random_delay("CareerShift contacts search results load")

        # Wait for either real results (SearchDetails links) or an explicit no-results message.
        def _results_or_message_present(d: WebDriver) -> bool:
            # Preferred: SearchDetails links exist.
            try:
                if d.find_elements(
                    By.XPATH,
                    "//a[contains(@href,'/App/Contacts/SearchDetails') and contains(@href,'personId=')]",
                ):
                    return True
            except Exception:
                pass

            # Or: results container shows a no-results message.
            try:
                container = d.find_element(By.ID, "contacts_search_results")
                container_text = (container.text or "").lower()
                if "no results" in container_text or "no contacts" in container_text:
                    return True
            except Exception:
                pass
            return False

        _wait(driver, timeout=60).until(_results_or_message_present)

        # Primary path: open the contact details page and read the mailto link.
        opened = _open_matching_contact_details(driver, first_name, last_name)
        if opened:
            random_delay("CareerShift contact details load")
            email = _extract_email_from_details_page(driver)
            if email:
                print(f"[CareerShift] Found email (details): {email}")
                return email

            # If details page didn't yield, fall back to scanning details page text.
            try:
                body = driver.find_element(By.TAG_NAME, "body")
                email = _extract_first_email_from_text(body.text or "")
                if email:
                    print(f"[CareerShift] Found email (details text): {email}")
                    return email
            except Exception:
                pass

            # Return to search results for the fallback scan.
            try:
                driver.back()
                _wait(driver, timeout=15).until(EC.url_contains("/App/Contacts/Search"))
            except Exception:
                pass

        # Fallback: some UIs require clicking the envelope icon to reveal details in a modal.
        try:
            # Find the matching contact card container first.
            cards = driver.find_elements(By.CSS_SELECTOR, "div.cs-flex")
        except Exception:
            cards = []

        for card in cards:
            try:
                name_el = None
                try:
                    name_el = card.find_element(
                        By.XPATH,
                        ".//a[starts-with(@id,'contact-title-') and contains(@href,'/App/Contacts/SearchDetails')]",
                    )
                except NoSuchElementException:
                    continue

                name_text = (name_el.text or name_el.get_attribute("innerText") or "").strip()
                if not _name_matches_result(name_text, first_name, last_name):
                    continue

                icon = card.find_element(
                    By.CSS_SELECTOR,
                    "a.contact-icon[title='View Contact Details for more information']",
                )
                if not icon.find_elements(By.CSS_SELECTOR, "span.fa.fa-envelope-o"):
                    continue
                try:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
                        icon,
                    )
                    driver.execute_script("arguments[0].click();", icon)
                except Exception:
                    icon.click()

                random_delay("CareerShift contact icon details")
                email = _extract_email_from_details_page(driver)
                if email:
                    print(f"[CareerShift] Found email (modal/details): {email}")
                    return email
            except Exception:
                continue

        # Secondary path: some orgs may show email inline in results.
        try:
            mailtos = driver.find_elements(
                By.XPATH,
                "//a[starts-with(@href,'mailto:') and string-length(@href) > 7]",
            )
        except Exception:
            mailtos = []

        for a in mailtos:
            href = (a.get_attribute("href") or "").strip()
            if href.lower().startswith("mailto:"):
                candidate = href.split("mailto:", 1)[1].split("?", 1)[0].strip()
                email = _extract_first_email_from_text(candidate)
                if email:
                    print(f"[CareerShift] Found email (results mailto): {email}")
                    return email

        # Second pass: scan a likely results container if present.
        results_container = None
        try:
            results_container = driver.find_element(By.ID, "contacts_search_results")
        except NoSuchElementException:
            results_container = None

        text_to_scan = ""
        if results_container is not None:
            try:
                text_to_scan = results_container.text or ""
            except Exception:
                text_to_scan = ""
        if not text_to_scan:
            # Fallback to full page text (kept last to avoid noise).
            try:
                body = driver.find_element(By.TAG_NAME, "body")
                text_to_scan = body.text or ""
            except Exception:
                text_to_scan = driver.page_source or ""

        email = _extract_first_email_from_text(text_to_scan)
        if email:
            print(f"[CareerShift] Found email: {email}")
            return email

        print("[CareerShift] Email not found in contacts search results.")
        return "NOT_FOUND"

    except TimeoutException:
        print("[CareerShift] Timeout while searching for email.")
        return "NOT_FOUND"
    except Exception as exc:
        print(f"[CareerShift] Unexpected error while searching for email: {exc}")
        return "NOT_FOUND"

    finally:
        # Best-effort reset: if we ended up on a details page or elsewhere, go
        # back to Contacts Search for the next lookup.
        try:
            current = (driver.current_url or "").lower()
            if ("/app/contacts/searchdetails" in current) or (
                "/app/contacts/search" not in current
            ):
                driver.get(target_search_url)
        except Exception:
            pass

