from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


@dataclass(frozen=True)
class ConnectOutcome:
    # High-level result code for logging/analytics
    # Values used by main.py logging: CONNECTED, PENDING, ALREADY_CONNECTED, NOT_AVAILABLE, ERROR
    result: str
    detail: str


def _wait(driver: WebDriver, timeout: int = 15) -> WebDriverWait:
    return WebDriverWait(driver, timeout)


def _sleep_tiny() -> None:
    # Small, fixed delay helps the UI settle between modal transitions.
    time.sleep(0.6)


def _normalize_space(s: str) -> str:
    return " ".join((s or "").split())


def _parse_first_last(full_name: str) -> tuple[str, str]:
    parts = [p for p in _normalize_space(full_name).split(" ") if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _extract_profile_full_name(driver: WebDriver) -> str:
    """Best-effort extraction of the profile person's name from the profile page."""
    # Primary: top-card heading.
    xpaths = [
        "//h1[normalize-space(.)!='']",
        "//*[@data-anonymize='person-name' and normalize-space(.)!='']",
    ]
    for xp in xpaths:
        try:
            els = driver.find_elements(By.XPATH, xp)
            for el in els:
                try:
                    txt = _normalize_space(el.text)
                    if txt:
                        return txt
                except Exception:
                    continue
        except Exception:
            continue

    # Fallback: OpenGraph title can include "Name | LinkedIn".
    try:
        metas = driver.find_elements(By.XPATH, "//meta[@property='og:title' and @content]")
        for m in metas:
            content = _normalize_space(m.get_attribute("content") or "")
            if content:
                return content.split("|", 1)[0].strip()
    except Exception:
        pass

    return ""


def _exact_aria_button(driver: WebDriver, aria_label: str):
    if not aria_label:
        return []
    xp = f"//button[normalize-space(@aria-label)={repr(_normalize_space(aria_label))}]"
    try:
        return driver.find_elements(By.XPATH, xp)
    except Exception:
        return []


def _find_button_by_aria_prefix(driver: WebDriver, prefix: str):
    # LinkedIn uses dynamic aria-labels like:
    # - "Invite First Last to connect"
    # - "Pending, click to withdraw invitation sent to First Last"
    # - "Follow First Last"
    xp = f"//button[starts-with(normalize-space(@aria-label), {repr(prefix)})]"
    return driver.find_elements(By.XPATH, xp)


def _find_button_case_insensitive_contains(driver: WebDriver, needle: str):
    lower = needle.lower()
    xp = (
        "//button[contains(translate(normalize-space(@aria-label),"
        " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),"
        f" {repr(lower)})]"
    )
    return driver.find_elements(By.XPATH, xp)


def _click_first(elems) -> bool:
    for el in elems or []:
        try:
            if el.is_displayed() and el.is_enabled():
                el.click()
                return True
        except Exception:
            continue
    return False


def _open_more_actions(driver: WebDriver) -> bool:
    # Exact aria-label matching only (accept a couple of known exact variants).
    for label in ("more actions", "More actions"):
        btns = _exact_aria_button(driver, label)
        if _click_first(btns):
            return True
    return False


def _click_connect_in_dropdown(driver: WebDriver) -> bool:
    # Dropdown items often render as <div role="menuitem">Connect</div>
    xpaths = [
        "//div[@role='menuitem' and normalize-space(.)='Connect']",
        "//span[normalize-space(.)='Connect']/ancestor::*[@role='menuitem'][1]",
        "//button[normalize-space(.)='Connect']",
    ]
    for xp in xpaths:
        try:
            items = driver.find_elements(By.XPATH, xp)
            if _click_first(items):
                return True
        except Exception:
            continue
    return False


def _click_invite_in_overflow(driver: WebDriver, *, display_name: str) -> bool:
    """Click the exact overflow-menu item to invite the person to connect.

    Per spec, in the Follow-case we open "More actions" and then click the
    div with aria-label: "Invite {first} {last} to connect".
    """
    label = _normalize_space(f"Invite {display_name} to connect")
    if not label:
        return False

    # Prefer exact div match (user requirement), then allow other clickable elements.
    xpaths = [
        f"//div[normalize-space(@aria-label)={repr(label)}]",
        f"//*[@role='menuitem' and normalize-space(@aria-label)={repr(label)}]",
        f"//*[self::button or self::a or self::span or self::div][normalize-space(@aria-label)={repr(label)}]",
    ]
    for xp in xpaths:
        try:
            elems = driver.find_elements(By.XPATH, xp)
            if _click_first(elems):
                return True
        except Exception:
            continue
    return False


def _add_note_and_send(driver: WebDriver, note: str) -> ConnectOutcome:
    # After clicking Connect, LinkedIn typically shows a modal with:
    # - "Add a note" button
    # - textarea
    # - "Send" button

    # Wait for either the "Add a note" control or the textarea directly.
    try:
        _wait(driver, timeout=12).until(
            lambda d: d.find_elements(By.XPATH, "//button[normalize-space(.)='Add a note']")
            or d.find_elements(By.XPATH, "//textarea")
        )
    except TimeoutException:
        return ConnectOutcome(
            result="ERROR",
            detail="Connect flow started, but invite modal did not appear in time.",
        )

    # Click "Add a note" if present.
    try:
        add_note = driver.find_elements(By.XPATH, "//button[normalize-space(.)='Add a note']")
        if add_note:
            _click_first(add_note)
            _sleep_tiny()
    except Exception:
        pass

    # Fill note textarea.
    try:
        textarea = _wait(driver, timeout=8).until(
            EC.presence_of_element_located((By.XPATH, "//textarea"))
        )
        textarea.clear()
        textarea.send_keys(note)
    except Exception:
        return ConnectOutcome(result="ERROR", detail="Could not locate/fill note textarea.")

    # Click Send
    send_xpaths = [
        "//button[normalize-space(.)='Send']",
        "//button[contains(@aria-label, 'Send') and not(@disabled)]",
    ]
    for xp in send_xpaths:
        try:
            btns = driver.find_elements(By.XPATH, xp)
            if _click_first(btns):
                return ConnectOutcome(result="CONNECTED", detail="Connection invite sent with note.")
        except Exception:
            continue

    return ConnectOutcome(result="ERROR", detail="Send button not found/clickable.")


def connect_if_not_pending(driver: WebDriver, profile_url: str, *, note: str) -> ConnectOutcome:
    """Open a LinkedIn profile and attempt to send a connection request with a note.

        Decision tree (per user spec; exact aria-label matching only):
            1) If button aria-label == "Invite {firstname} {lastname} to connect": click and send with note.
            2) Else if button aria-label == "Pending, click to withdraw invitation sent to {firstname} {lastname}": mark pending, do nothing.
            3) Else if button aria-label == "Follow {firstname} {lastname}": open "more actions" and connect from dropdown.
            4) Else: mark already a connection, do nothing.

    Returns a ConnectOutcome for logging.
    """

    if not profile_url:
        return ConnectOutcome(result="ERROR", detail="Empty profile_url")

    driver.get(profile_url)

    # Give the profile page a moment to render action buttons.
    try:
        _wait(driver, timeout=20).until(
            lambda d: d.find_elements(By.XPATH, "//button[@aria-label]")
        )
    except TimeoutException:
        return ConnectOutcome(result="ERROR", detail="Profile page loaded but no action buttons found.")

    # Extract profile name to build exact aria-label matches per spec.
    profile_full_name = _extract_profile_full_name(driver)
    first_name, last_name = _parse_first_last(profile_full_name)
    display_name = _normalize_space(f"{first_name} {last_name}").strip()

    if not display_name:
        return ConnectOutcome(
            result="ERROR",
            detail="Could not extract profile first/last name for exact aria-label matching.",
        )

    invite_exact_label = f"Invite {display_name} to connect" if display_name else ""
    pending_exact_label = (
        f"Pending, click to withdraw invitation sent to {display_name}" if display_name else ""
    )
    follow_exact_label = f"Follow {display_name}" if display_name else ""

    # 1) Exact invite aria-label match (preferred)
    if invite_exact_label:
        exact_invite = _exact_aria_button(driver, invite_exact_label)
        if exact_invite:
            if not _click_first(exact_invite):
                return ConnectOutcome(
                    result="ERROR",
                    detail=f"Invite-to-connect button found ({invite_exact_label}) but not clickable.",
                )
            _sleep_tiny()
            return _add_note_and_send(driver, note)

    # 2) Exact pending aria-label match
    if pending_exact_label:
        exact_pending = _exact_aria_button(driver, pending_exact_label)
        if exact_pending:
            return ConnectOutcome(result="PENDING", detail="Invitation already pending; skipped.")

    # 3) Exact follow aria-label match
    exact_follow = _exact_aria_button(driver, follow_exact_label)
    if exact_follow:
        if not _open_more_actions(driver):
            return ConnectOutcome(
                result="NOT_AVAILABLE",
                detail="Follow button present but could not open 'More actions' menu.",
            )
        _sleep_tiny()
        if not _click_invite_in_overflow(driver, display_name=display_name):
            return ConnectOutcome(
                result="NOT_AVAILABLE",
                detail="'More actions' opened but no exact Invite-to-connect overflow item found.",
            )
        _sleep_tiny()
        return _add_note_and_send(driver, note)

    # 4) Otherwise assume already connected / connect not possible
    return ConnectOutcome(
        result="ALREADY_CONNECTED",
        detail="No exact invite/pending/follow aria-label matches found; skipped.",
    )
