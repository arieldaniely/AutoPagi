import argparse
import asyncio

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


DEFAULT_URL = "https://www.pagi.co.il/private/"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automate navigation to the Pagi login modal and submit credentials."
    )
    parser.add_argument("--username", required=True, help="User code to use for login")
    parser.add_argument("--password", required=True, help="Password to use for login")
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"URL to open (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--stay-open",
        action="store_true",
        help="Keep the browser session open after submitting the form",
    )
    return parser.parse_args()


async def fill_credentials(page, username: str, password: str) -> None:
    await page.fill("#username", username)
    await page.fill("#password", password)


async def submit_login(page) -> None:
    await page.click("#continueBtn")


async def open_login_modal(page, url: str) -> None:
    """Navigate to the login form using several fallback selectors.

    The Pagi site has changed its HTML multiple times. We try a handful of
    selectors so the script keeps working even if the login trigger's class or
    tag changes, and we provide clearer errors when the modal cannot be found.
    """

    await page.goto(url, wait_until="load")
    await page.wait_for_timeout(1500)

    login_triggers = [
        "a.login-trigger",
        "button.login-trigger",
        "a[href*='login']",
        "button:has-text('כניסה לחשבונך')",
        "text=כניסה לחשבונך",
    ]

    for selector in login_triggers:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(timeout=3000)
        except PlaywrightTimeoutError:
            continue

        try:
            await locator.click()
            break
        except PlaywrightTimeoutError:
            continue
    else:
        raise PlaywrightTimeoutError(
            "Login trigger not found on the page. Confirm the URL is correct and the page is fully loaded."
        )

    form_selectors = [
        "#loginForm",
        "form#loginForm",
        "form[action*='login']",
        "input#username",
        "input[name='username']",
    ]

    for selector in form_selectors:
        try:
            await page.wait_for_selector(selector, timeout=5000)
            return
        except PlaywrightTimeoutError:
            continue

    raise PlaywrightTimeoutError(
        "Login form did not appear after clicking the trigger. It may be blocked by another dialog or the page layout has changed."
    )


async def main(username: str, password: str, url: str, stay_open: bool) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await open_login_modal(page, url)
        except PlaywrightTimeoutError:
            await browser.close()
            raise SystemExit(
                "Login form did not appear. Check that the page loaded correctly and try again."
            )

        await fill_credentials(page, username, password)
        await submit_login(page)
        print("Submitted login form. The browser will remain open for manual control.")

        if stay_open:
            print("Press CTRL+C to close the session when finished.")
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                pass

        await browser.close()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        main(
            username=args.username,
            password=args.password,
            url=args.url,
            stay_open=args.stay_open,
        )
    )
