"""
Check-in script for ro.gnjoylatam.com events.

Flow per account:
  1. Opens a visible browser window and navigates to the event page
  2. If a login button is found, auto-fills email + password
  3. Waits for YOU to click the Cloudflare Turnstile checkbox ("Confirme que e humano")
  4. Automatically submits the form, navigates back to the event, and clicks Check-in
"""

import json
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

TIMEOUT = 20_000  # ms

# Works both as .py script and as a frozen .exe (PyInstaller)
import sys as _sys
_BASE = Path(_sys.executable).parent if getattr(_sys, "frozen", False) else Path(__file__).parent
ACCOUNTS_FILE = _BASE / "accounts.json"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def log(msg: str, color: str = RESET):
    print(f"{color}{msg}{RESET}", flush=True)


def load_config() -> tuple[str, list[dict]]:
    with open(ACCOUNTS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    event_url = data["event_url"]
    accounts = data["accounts"]
    return event_url, accounts


async def do_login(page, account: dict, event_url: str):
    """Fill credentials and wait for user to solve Turnstile."""
    email = account["email"]

    log(f"[{email}] Navigating to login page...")
    login_url = f"https://login.gnjoylatam.com?redirectUrl={event_url}"
    await page.goto(login_url, wait_until="domcontentloaded", timeout=TIMEOUT)
    await page.wait_for_timeout(2_000)

    log(f"[{email}] Filling credentials...")
    await page.locator("input[name='email'], input[placeholder='E-mail']").first.fill(account["email"])
    await page.locator("input[type='password']").first.fill(account["password"])

    log(
        f"\n{CYAN}[{email}] ACTION REQUIRED:{RESET} "
        f"Please click the Cloudflare checkbox ('Confirme que é humano') "
        f"in the browser window. Waiting...",
    )

    await page.wait_for_function(
        "document.querySelector('input[name=\"cf-turnstile-response\"]')?.value?.length > 0",
        timeout=120_000,
    )
    log(f"[{email}] Turnstile solved! Submitting...", GREEN)

    await page.locator("button:has-text('CONTINUAR'), button[type='submit']").first.click()

    await page.wait_for_url(
        lambda url: "login.gnjoylatam.com" not in url,
        timeout=TIMEOUT,
    )
    log(f"[{email}] Login successful. Redirected to: {page.url}", GREEN)


async def dismiss_cookie_banner(page):
    """Dismiss the cookie consent banner if present."""
    try:
        btn = page.locator("button.cookieprivacy_btn__Pqz8U, button:has-text('concordo')").first
        if await btn.is_visible(timeout=3_000):
            await btn.click()
            await page.wait_for_timeout(500)
            log("  Cookie banner dismissed.", YELLOW)
    except PlaywrightTimeout:
        pass


async def needs_login(page) -> bool:
    """Return True if there is a visible login/sign-in button on the page."""
    login_btn = page.locator(
        "button:has-text('Login'), "
        "button:has-text('Sign in'), "
        "button:has-text('Entrar'), "
        "a:has-text('Login'), "
        "a:has-text('Entrar'), "
        "button[aria-label='Login'], "
        "button[aria-label='Entrar']"
    ).first
    try:
        return await login_btn.is_visible(timeout=3_000)
    except PlaywrightTimeout:
        return False


async def do_checkin(page, email: str, event_url: str) -> bool:
    """Navigate to event page and click the check-in button."""
    if event_url not in page.url:
        log(f"[{email}] Navigating to event page...")
        await page.goto(event_url, wait_until="domcontentloaded", timeout=TIMEOUT)
        await page.wait_for_timeout(3_000)

    await dismiss_cookie_banner(page)

    # Take a screenshot to see what the page looks like before acting
    pre_shot = _BASE / f"pre_checkin_{email.split('@')[0]}.png"
    await page.screenshot(path=str(pre_shot), full_page=True)
    log(f"[{email}] Pre-checkin screenshot: {pre_shot.name}", YELLOW)

    log(f"[{email}] Looking for check-in button...")

    checkin_btn = page.locator(
        "button[aria-label='Check-in'], "
        "button[title='Check-in'], "
        "button.styles_checkin_button__YOuXP"
    ).first

    try:
        await checkin_btn.wait_for(state="attached", timeout=TIMEOUT)
    except PlaywrightTimeout:
        already_done = page.locator(
            "button[aria-label='Completed'], button.styles_complete_button__m12Yr"
        ).first
        try:
            if await already_done.is_visible(timeout=2_000):
                log(f"[{email}] Already checked in today (Completed).", YELLOW)
                screenshot = _BASE / f"checkin_{email.split('@')[0]}.png"
                await page.screenshot(path=str(screenshot))
                log(f"[{email}] Screenshot: {screenshot.name}", YELLOW)
                return True
        except PlaywrightTimeout:
            pass
        log(f"[{email}] Check-in button not found in DOM.", RED)
        screenshot = _BASE / f"error_{email.split('@')[0]}.png"
        await page.screenshot(path=str(screenshot), full_page=True)
        return False

    await checkin_btn.scroll_into_view_if_needed()
    await page.wait_for_timeout(500)

    # Check if there is a Turnstile widget in the check-in section that needs solving
    checkin_turnstile = page.locator("input[name='cf-turnstile-response']").first
    try:
        turnstile_value = await checkin_turnstile.get_attribute("value", timeout=2_000)
        if not turnstile_value:
            log(
                f"\n{CYAN}[{email}] ACTION REQUIRED:{RESET} "
                f"A second Cloudflare checkbox appeared for the check-in section. "
                f"Please solve it in the browser window. Waiting...",
            )
            await page.wait_for_function(
                "document.querySelector('input[name=\"cf-turnstile-response\"]')?.value?.length > 0",
                timeout=120_000,
            )
            log(f"[{email}] Check-in Turnstile solved!", GREEN)
            await page.wait_for_timeout(500)
    except PlaywrightTimeout:
        pass  # No Turnstile found, proceed normally

    await checkin_btn.click(force=True)
    log(f"[{email}] Check-in button clicked!", GREEN)

    # Wait for any confirmation modal or page state change
    await page.wait_for_timeout(3_000)

    # Check for and dismiss any confirmation/modal button (e.g. "Confirmar", "OK", "Sim")
    for selector in [
        "button:has-text('Confirmar')",
        "button:has-text('Confirm')",
        "button:has-text('OK')",
        "button:has-text('Sim')",
        "button:has-text('ok')",
    ]:
        try:
            confirm_btn = page.locator(selector).first
            if await confirm_btn.is_visible(timeout=2_000):
                await confirm_btn.click()
                log(f"[{email}] Confirmation dialog dismissed.", GREEN)
                await page.wait_for_timeout(2_000)
                break
        except PlaywrightTimeout:
            continue

    # Wait for the button to change state (Completed) or page to settle
    try:
        await page.locator(
            "button[aria-label='Completed'], button.styles_complete_button__m12Yr"
        ).first.wait_for(state="visible", timeout=5_000)
        log(f"[{email}] Check-in confirmed — button changed to Completed.", GREEN)
    except PlaywrightTimeout:
        log(f"[{email}] Button did not change to Completed (may still be OK).", YELLOW)

    screenshot = _BASE / f"checkin_{email.split('@')[0]}.png"
    await page.screenshot(path=str(screenshot), full_page=True)
    log(f"[{email}] Screenshot: {screenshot.name}", YELLOW)
    return True


async def process_account(account: dict, playwright, event_url: str) -> bool:
    email = account["email"]
    browser = await playwright.chromium.launch(
        headless=False,
        channel="chrome",
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context()
    page = await context.new_page()

    try:
        log(f"[{email}] Navigating to event page...")
        await page.goto(event_url, wait_until="domcontentloaded", timeout=TIMEOUT)
        await page.wait_for_timeout(3_000)

        await dismiss_cookie_banner(page)

        if await needs_login(page):
            log(f"[{email}] Login required — starting login flow.", YELLOW)
            await do_login(page, account, event_url)
        else:
            log(f"[{email}] Already logged in (no login button found).", GREEN)

        return await do_checkin(page, email, event_url)

    except PlaywrightTimeout as e:
        log(f"[{email}] TIMEOUT: {e}", RED)
        try:
            await page.screenshot(path=str(_BASE / f"error_{email.split('@')[0]}.png"))
        except Exception:
            pass
        return False
    except Exception as e:
        log(f"[{email}] ERROR: {e}", RED)
        try:
            await page.screenshot(path=str(_BASE / f"error_{email.split('@')[0]}.png"))
        except Exception:
            pass
        return False
    finally:
        await context.close()
        await browser.close()


async def main():
    event_url, accounts = load_config()
    log(f"Loaded {len(accounts)} account(s). Event: {event_url}", YELLOW)
    log("Note: A browser window will open for each account. Solve the Turnstile when prompted.", CYAN)

    results = {}
    async with async_playwright() as playwright:
        for account in accounts:
            success = await process_account(account, playwright, event_url)
            results[account["email"]] = success

    log("\n--- Summary ---", YELLOW)
    for email, status in results.items():
        if status is True:
            log(f"  {email}: OK", GREEN)
        else:
            log(f"  {email}: FAIL", RED)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except FileNotFoundError as e:
        log(f"\nERROR: {e}", RED)
        log("Make sure accounts.json is in the same folder as this program.", YELLOW)
    except json.JSONDecodeError as e:
        log(f"\nERROR: accounts.json has invalid JSON — {e}", RED)
        log("Check for missing commas, quotes, or brackets in accounts.json.", YELLOW)
    except Exception as e:
        log(f"\nUnexpected error: {e}", RED)
    finally:
        if getattr(_sys, "frozen", False):
            input("\nPress ENTER to close...")
