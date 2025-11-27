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


async def open_login_modal(page) -> None:
    await page.goto(DEFAULT_URL)
    await page.wait_for_timeout(1500)
    await page.click("a.login-trigger")
    await page.wait_for_selector("#loginForm", timeout=10000)


async def fill_credentials(page, username: str, password: str) -> None:
    await page.fill("#username", username)
    await page.fill("#password", password)


async def submit_login(page) -> None:
    await page.click("#continueBtn")


async def main(username: str, password: str, url: str, stay_open: bool) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await page.goto(url)
            await page.wait_for_timeout(1500)
            await page.click("a.login-trigger")
            await page.wait_for_selector("#loginForm", timeout=10000)
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
