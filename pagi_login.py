import argparse
import asyncio

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Page, FrameLocator




def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automate navigation to the Pagi login modal and submit credentials."
    )
    parser.add_argument("--username", required=True, help="User code to use for login")
    parser.add_argument("--password", required=True, help="Password to use for login")
    parser.add_argument(
        "--url",
        default="https://www.pagi.co.il/private/",
        help="URL to open (default: https://www.pagi.co.il/private/)",
    )
    parser.add_argument(
        "--stay-open",
        action="store_true",
        help="Keep the browser session open after submitting the form",
    )
    return parser.parse_args()


async def fill_credentials(frame_locator, username: str, password: str) -> None:
    """Fills credentials inside the provided iframe locator."""
    # Wait for the username field to be visible within the frame before filling
    await frame_locator.locator("#username").wait_for(state="visible", timeout=10000)
    await frame_locator.locator("#username").fill(username)
    await frame_locator.locator("#password").fill(password)


async def submit_login(page, frame_locator) -> None:
    """Submits the login form inside the provided iframe locator."""
    # The login button on this site can be tricky. It might appear enabled
    # before its click handler is fully attached, or it might be briefly
    # obscured. A retry loop provides maximum stability.
    submit_button = frame_locator.locator("#continueBtn")
    
    for attempt in range(5):
        try:
            # First, wait for the button to not be disabled.
            # We use a short timeout as this check is inside a loop.
            await submit_button.wait_for(state="visible", timeout=5000)
            if await submit_button.is_disabled():
                raise PlaywrightTimeoutError("Button is disabled.")

            # Hovering forces Playwright to wait for the element to be stable
            # and actionable (not obscured, not animating). This is a key step.
            await submit_button.hover(timeout=5000)
            
            # Now, attempt the click.
            await submit_button.click(timeout=2000) # Use a short click timeout
            return # If click succeeds, exit the function.
        except PlaywrightTimeoutError:
            print(f"Attempt {attempt + 1} to click login button failed. Retrying...")
            await page.wait_for_timeout(500) # Brief pause before next attempt.

    raise PlaywrightTimeoutError("Failed to click the login button after multiple retries.")


async def open_login_modal(page: Page, url: str) -> None:
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

    # After clicking the trigger, wait for the login iframe itself to become visible.
    # This is more reliable than waiting for an element inside it.
    try:
        await page.locator("#loginFrame").wait_for(state="visible", timeout=10000)
    except PlaywrightTimeoutError:
        raise PlaywrightTimeoutError(
            "The login iframe (#loginFrame) did not appear after clicking the trigger."
        )


async def login_to_pagi(page: Page, username: str, password: str, url: str) -> None:
    """
    Main function to perform login on a given Playwright page.
    This is the primary entry point when using this script as a module.
    """
    try:
        await open_login_modal(page, url)
    except PlaywrightTimeoutError as e:
        print(f"Login form did not appear. Check that the page loaded correctly. Error: {e}")
        raise  # Re-raise the exception to be handled by the caller

    # Use frame_locator to get a reference to the iframe. This is the most reliable way.
    login_frame = page.frame_locator("#loginFrame")

    await fill_credentials(login_frame, username, password)
    await submit_login(page, login_frame)
    print("Submitted login form.")


async def main_standalone(username: str, password: str, url: str, stay_open: bool) -> None:
    """Function to run the login process as a standalone script."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await login_to_pagi(page, username, password, url)
            print("Login successful. The browser will remain open for manual control.")
        except Exception as e:
            await browser.close()
            raise SystemExit(f"An error occurred during the login process: {e}")

        if stay_open:
            print("Press CTRL+C in the console to close the session when finished.")
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                pass

        await browser.close()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        main_standalone(
            username=args.username,
            password=args.password,
            url=args.url,
            stay_open=args.stay_open,
        )
    )