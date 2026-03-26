import logging
from enum import Enum, auto

from selenium.common.exceptions import (
    ElementNotInteractableException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


logger = logging.getLogger(__name__)


class ConnectOutcome(Enum):
    CONNECTED = auto()
    PENDING = auto()
    FOLLOW_FLOW = auto()
    ALREADY_CONNECTED = auto()
    ERROR = auto()


def _get_visible_more_button(driver: WebDriver) -> WebElement | None:
    """Return the first visible 'More' button on a profile."""
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_all_elements_located((By.XPATH, "//button[@aria-label='More']"))
        )
        candidates = driver.find_elements(By.XPATH, "//button[@aria-label='More']")
        return next((el for el in candidates if el.is_displayed()), candidates[0])
    except Exception:
        return None


def _menu_has_remove_connection(driver: WebDriver) -> bool:
    """Check whether the open More actions menu contains Remove connection."""
    remove_xpaths = [
        "//div[@role='menu']//*[@aria-label='Remove connection']",
        "//div[@role='menu']//*[normalize-space()='Remove connection']",
    ]
    for xp in remove_xpaths:
        try:
            matches = driver.find_elements(By.XPATH, xp)
            if any(el.is_displayed() for el in matches):
                return True
        except Exception:
            continue
    return False


def is_already_connected_via_more_actions(driver: WebDriver) -> bool:
    """
    Open More actions and detect connection state via 'Remove connection' menu item.
    """
    logger.info("Checking More actions menu for 'Remove connection'")
    more_button = _get_visible_more_button(driver)
    if more_button is None:
        logger.info("More actions button not found; skipping remove-connection check.")
        return False

    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
            more_button,
        )
    except Exception:
        pass

    try:
        more_button.click()
    except ElementNotInteractableException:
        logger.info("Native click on 'More' failed; using JavaScript click.")
        driver.execute_script("arguments[0].click();", more_button)

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='menu']"))
        )
    except TimeoutException:
        logger.info("More actions menu did not open in time.")
        return False

    connected = _menu_has_remove_connection(driver)
    if connected:
        logger.info("'Remove connection' found. Marking as already connected.")
    else:
        logger.info("'Remove connection' not found in More actions menu.")

    # Best-effort close of the menu so the next step can interact cleanly.
    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    except Exception:
        pass

    return connected


def open_profile(driver: WebDriver, url: str) -> None:
    """Open a LinkedIn profile URL and wait for page load completion."""
    logger.info("Navigating to profile URL: %s", url)
    driver.get(url)
    WebDriverWait(driver, 20).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    logger.info("Profile page load complete")


def get_primary_action_and_aria_label(driver: WebDriver) -> tuple[WebElement | None, str]:
    """
    Locate the profile's primary action control and return it with its aria-label.
    """
    logger.info("Locating primary action control on profile")
    primary_action_xpath = (
        "//a[@aria-label and ("
        "starts-with(@aria-label, 'Invite ') or "
        "starts-with(@aria-label, 'Pending, click to withdraw invitation sent to ') or "
        "starts-with(@aria-label, 'Follow ')"
        ")]"
        " | "
        "//button[@aria-label and ("
        "starts-with(@aria-label, 'Invite ') or "
        "starts-with(@aria-label, 'Pending, click to withdraw invitation sent to ') or "
        "starts-with(@aria-label, 'Follow ')"
        ")]"
    )
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_all_elements_located((By.XPATH, primary_action_xpath))
        )
        primary_candidates = driver.find_elements(By.XPATH, primary_action_xpath)
        primary_action = next(
            (element for element in primary_candidates if element.is_displayed()),
            primary_candidates[0],
        )
        logger.info("Primary action candidates found: %s", len(primary_candidates))
    except TimeoutException:
        logger.info("Invite/Pending/Follow action is not present; treating as connected.")
        return None, ""

    aria_label = (primary_action.get_attribute("aria-label") or "").strip()
    logger.info("Primary action aria-label extracted: %s", aria_label)
    return primary_action, aria_label


def click_invite_if_present(primary_action: WebElement | None, aria_label: str) -> bool:
    """
    Click the action only when aria-label exactly matches the invite pattern.
    """
    if primary_action is None:
        logger.info("Primary action element missing. Skipping invite click.")
        return False

    if (
        aria_label.startswith("Invite ")
        and aria_label.endswith(" to connect")
        and "Pending, click to withdraw invitation sent to " not in aria_label
    ):
        logger.info("Invite action detected. Clicking invite button.")
        driver = primary_action.parent
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
            primary_action,
        )
        WebDriverWait(driver, 10).until(
            lambda d: primary_action.is_displayed() and primary_action.is_enabled()
        )
        try:
            primary_action.click()
        except ElementNotInteractableException:
            logger.info("Native click failed; falling back to JavaScript click.")
            driver.execute_script("arguments[0].click();", primary_action)
        return True

    logger.info("Invite action not detected. Skipping invite click.")
    return False


def add_note_and_send_invite(driver: WebDriver, note: str) -> None:
    """Handle invite modal by adding a note and sending invitation."""

    def find_element_including_shadow(css_selector: str) -> WebElement | None:
        return driver.execute_script(
            """
            const selector = arguments[0];

            function findIn(root) {
                const direct = root.querySelector(selector);
                if (direct) return direct;

                const nodes = root.querySelectorAll("*");
                for (const node of nodes) {
                    if (node.shadowRoot) {
                        const nested = findIn(node.shadowRoot);
                        if (nested) return nested;
                    }
                }
                return null;
            }

            return findIn(document);
            """,
            css_selector,
        )

    logger.info("Waiting for 'Add a note' button")
    add_note_button = WebDriverWait(driver, 20).until(
        lambda d: find_element_including_shadow("button[aria-label='Add a note']")
    )
    logger.info("Clicking 'Add a note' in invite modal")
    add_note_button.click()

    logger.info("Filling note textarea")
    note_textarea = WebDriverWait(driver, 20).until(
        lambda d: find_element_including_shadow("textarea#custom-message")
    )
    note_textarea.clear()
    note_textarea.send_keys(Keys.CONTROL, "a")
    note_textarea.send_keys(Keys.DELETE)
    note_textarea.send_keys(note)

    logger.info("Clicking 'Send invitation'")
    send_invite_button = WebDriverWait(driver, 20).until(
        lambda d: find_element_including_shadow("button[aria-label='Send invitation']")
    )
    send_invite_button.click()


def get_pending_outcome_if_present(aria_label: str) -> ConnectOutcome | None:
    """
    Return pending outcome only when aria-label matches the pending invitation pattern.
    """
    pending_prefix = "Pending, click to withdraw invitation sent to "
    if aria_label.startswith(pending_prefix):
        logger.info("Pending invitation detected from aria-label")
        return ConnectOutcome.PENDING

    return None


def handle_follow_via_more_actions(
    driver: WebDriver, aria_label: str, note: str | None = None
) -> ConnectOutcome | None:
    """
    If Follow is the primary action, open More actions and click Connect.
    """
    if not aria_label.startswith("Follow "):
        return None

    logger.info("Follow action detected. Opening 'More' menu")
    more_button = _get_visible_more_button(driver)
    if more_button is None:
        logger.info("More actions button not found during Follow flow.")
        return ConnectOutcome.ERROR
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
        more_button,
    )
    try:
        more_button.click()
    except ElementNotInteractableException:
        logger.info("Native click on 'More' failed; using JavaScript click.")
        driver.execute_script("arguments[0].click();", more_button)

    logger.info("Waiting for More actions menu")
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, "//div[@role='menu']"))
    )

    logger.info("Clicking Connect from More actions")
    connect_menu_item = WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//div[@role='menu']//a[contains(@href, '/preload/custom-invite/')]",
            )
        )
    )
    connect_menu_item.click()
    logger.info("Connect clicked from More actions flow")
    if note:
        logger.info("Handling invite modal after Follow flow")
        add_note_and_send_invite(driver, note)
    return ConnectOutcome.FOLLOW_FLOW


def get_already_connected_fallback_outcome(aria_label: str) -> ConnectOutcome | None:
    """
    Fallback to already connected when aria-label matches no handled conditions.
    """
    if (
        not aria_label.startswith("Invite ")
        and not aria_label.startswith("Pending, click to withdraw invitation sent to ")
        and not aria_label.startswith("Follow ")
    ):
        logger.info("No matching connect action. Marking as already connected.")
        return ConnectOutcome.ALREADY_CONNECTED

    return None


def connect_to_profile(driver: WebDriver, url: str, note: str) -> ConnectOutcome:
    """
    Open a LinkedIn profile and attempt to connect based on current button state.
    """
    logger.info("Starting connection flow for profile: %s", url)
    try:
        open_profile(driver, url)

        primary_action, aria_label = get_primary_action_and_aria_label(driver)

        if click_invite_if_present(primary_action, aria_label):
            add_note_and_send_invite(driver, note)
            logger.info(
                "Connection flow complete with outcome: %s", ConnectOutcome.CONNECTED
            )
            return ConnectOutcome.CONNECTED

        pending_outcome = get_pending_outcome_if_present(aria_label)
        if pending_outcome is not None:
            logger.info("Connection flow complete with outcome: %s", pending_outcome)
            return pending_outcome

        if is_already_connected_via_more_actions(driver):
            logger.info("Connection flow complete: already connected (More actions check).")
            return ConnectOutcome.ALREADY_CONNECTED

        follow_outcome = handle_follow_via_more_actions(driver, aria_label, note)
        if follow_outcome is not None:
            logger.info("Connection flow complete with outcome: %s", follow_outcome)
            return follow_outcome

        fallback_outcome = get_already_connected_fallback_outcome(aria_label)
        if fallback_outcome is not None:
            logger.info("Connection flow complete with outcome: %s", fallback_outcome)
            return fallback_outcome

        logger.info("No action path matched; defaulting to already connected outcome.")
        return ConnectOutcome.ALREADY_CONNECTED
    except Exception:
        logger.exception("Connection flow failed with an unexpected error.")
        return ConnectOutcome.ERROR


def main() -> None:
    """Placeholder entrypoint for LinkedIn connector flow."""
    pass
