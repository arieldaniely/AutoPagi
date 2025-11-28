import argparse
import asyncio
import logging
from datetime import datetime
import json
import os
import io
from bs4 import BeautifulSoup
import csv
import pandas as pd
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, FrameLocator, Page

from pagi_login import login_to_pagi


def parse_args() -> argparse.Namespace:
    """Parses command-line arguments for the Pagi automation script."""
    parser = argparse.ArgumentParser(
        description="Login to Pagi and perform automated actions."
    )
    parser.add_argument("--username", required=True, help="User code for Pagi login.")
    parser.add_argument("--password", required=True, help="Password for Pagi login.")
    parser.add_argument(
        "--url",
        default="https://www.pagi.co.il/private/",
        help="Initial Pagi URL to open for login.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser in headless mode (without a visible window).",
    )
    parser.add_argument(
        "--skip-institution-mapping",
        action="store_true",
        help="Skip fetching the institution mapping from Google Sheets.",
    )
    parser.add_argument(
        "--run-output-dir",
        default="output",
        help="Directory to save the output reports.",
    )
    return parser.parse_args()

def setup_logging(log_dir: str) -> None:
    """Sets up logging to both console and a file in the specified directory."""
    # Ensure the log directory exists
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, "run.log")

    # Get the root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Remove any existing handlers to avoid duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    # Create a formatter
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # Add a handler to write to the console (stdout)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # Add a handler to write to the log file
    file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

async def click_row_by_text(
    page: Page,
    frame_locator: FrameLocator,
    table_selector: str,
    row_text: str, # The text to find in the row
    click_selector: str = "a",
    timeout: int = 30000,
):
    """
    Finds a table row containing `row_text` and performs a robust, multi-stage "hybrid click"
    on a `click_selector` element within it to ensure the action is registered on complex sites.
    If `click_selector` is "row", it clicks the row itself.
    """
    logging.info(f"Waiting for table '{table_selector}'...")
    table = frame_locator.locator(table_selector)
    await table.wait_for(state="visible", timeout=timeout)

    logging.info(f"Searching for row containing text: '{row_text}'")
    # Use a locator that finds a 'tr' that has a descendant with the specified text.
    row_locator = table.locator("tr", has_text=row_text).first

    await row_locator.wait_for(state="visible", timeout=timeout)

    if click_selector == "row":
        target_element = row_locator
    else:
        # Otherwise, find the specified selector within the row
        target_element = row_locator.locator(click_selector).first

    await target_element.wait_for(state="visible", timeout=timeout)

    # --- Hybrid Click Implementation ---
    logging.info("Performing hybrid click...")
    # Wait a moment for any client-side JS event listeners to attach.
    await page.wait_for_timeout(1200)

    await target_element.scroll_into_view_if_needed()

    # Attempt 1: A forceful Playwright click with a small delay.
    try:
        logging.info("Hybrid Click - Step 1: Forceful click.")
        await target_element.click(force=True, delay=50, timeout=3000)
        return # If it works, we're done.
    except PlaywrightTimeoutError:
        logging.warning("Step 1 (forceful click) timed out. Proceeding to next step.")
    except Exception:
        logging.warning("Step 1 (forceful click) failed. Proceeding to next step.")

    # Attempt 2: Dispatch a 'click' event directly to the element.
    logging.info("Hybrid Click - Step 2: Dispatching 'click' event.")
    await target_element.dispatch_event("click")
    await page.wait_for_timeout(500) # Give it a moment to react

    # Attempt 3 (Pagi-specific): Evaluate JavaScript to find the element and click it.
    # This is a powerful fallback that simulates how the site's own JS might work.
    logging.info("Hybrid Click - Step 3: Evaluating JS to trigger click.")
    await frame_locator.evaluate(
        'text => document.querySelectorAll("a.PW, a:not([class])").find(a => a.textContent.includes(text))?.click()',
        row_text
    )

async def get_google_service(service_name: str, version: str):
    """Authenticates with Google and returns a service object (e.g., for Sheets or Drive)."""
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/drive.readonly'
    ]
    creds = None
    # The file token.json stores the user's access and refresh tokens.
    # Ensure the path is relative to the script's directory.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    token_path = os.path.join(script_dir, 'token.json')
    credentials_path = os.path.join(script_dir, 'credentials.json')

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logging.info("Refreshing expired Google credentials...")
            creds.refresh(Request())
        else:
            logging.info("Google credentials not found or invalid, starting new login flow...")
            if not os.path.exists(credentials_path):
                logging.error(f"credentials.json not found at '{credentials_path}'. Please download it from your Google Cloud project.")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            # Note: run_local_server will open a browser tab for authentication on the first run.
            creds = flow.run_local_server(port=0)
        
        # Save the credentials for the next run
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
            logging.info(f"Google credentials saved to '{token_path}'.")
    
    try:
        return build(service_name, version, credentials=creds)
    except HttpError as err:
        logging.error(f"An error occurred building the Google service '{service_name}': {err}")
        return None

async def get_institution_map() -> dict[str, str]:
    sheet_id = "11Ev1xHx22tCVtI8mMUWYYlENSzBOxFn8"
    
    logging.info("Fetching institution map from Google Drive API...")
    try:
        drive_service = await get_google_service('drive', 'v3')
        if not drive_service:
            raise Exception("Failed to get Google Drive service.")

        request = drive_service.files().get_media(fileId=sheet_id)
        
        file_handle = io.BytesIO()
        downloader = MediaIoBaseDownload(file_handle, request)
        
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                logging.info(f"Download progress: {int(status.progress() * 100)}%")

        logging.info("Download complete. Parsing Excel data...")
        file_handle.seek(0)

        df = pd.read_excel(
            file_handle,
            sheet_name="חוזי חשמל כל המוסדות ",
            usecols=["מוסד ", "מספר חוזה "],
            engine="openpyxl"
        )

        df.columns = df.columns.map(lambda x: str(x).strip())

        df.dropna(subset=["מספר חוזה"], inplace=True)

        institution_map = pd.Series(
            df["מוסד"].values,
            index=df["מספר חוזה"].astype(str)
        ).to_dict()

        logging.info(f"Successfully loaded {len(institution_map)} institution mappings.")
        return institution_map

    except HttpError as err:
        logging.error(f"An API error occurred while fetching from Google Drive: {err}")
        return {}
    except Exception as e:
        logging.error(f"Failed to fetch or parse institution map from Google Drive: {e}")
        return {}

async def main() -> None:
    """Main function to run the Pagi automation process."""
    args = parse_args()
    
    # --- Setup Logging ---
    setup_logging(args.run_output_dir)

    # --- Enhanced Logging ---
    logging.info("Starting AutoPagi automation script.")
    
    # Create a dictionary of the arguments, masking the password
    args_dict = vars(args).copy()
    if 'password' in args_dict:
        args_dict['password'] = '********'
    
    logging.info(f"Running with configuration: {json.dumps(args_dict, ensure_ascii=False)}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=args.headless)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            logging.info("Attempting to log in to Pagi...")
            await login_to_pagi(page, args.username, args.password, args.url)
            logging.info("Login successful. Waiting for post-login page to load...")

            # Wait for navigation to the account summary page after login
            await page.wait_for_url("https://online.pagi.co.il/appsng/Resources/PortalNG/shell/#/accountSummary*", timeout=30000)

            transactions_url = "https://online.pagi.co.il/appsng/Resources/PortalNG/shell/#/Online/OnAccountMngment/OnBalanceTrans/PrivateAccountFlow"
            logging.info(f"Navigating directly to transactions page: {transactions_url}")
            # Use wait_until="domcontentloaded" for faster navigation in SPAs
            # and add a small delay for client-side scripts to initialize.
            await page.goto(transactions_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000) # Add a small delay for client-side scripts
 
            # Use frame_locator. It's designed to handle frames that reload.
            # We define it once and use it for all subsequent actions inside the iframe.
            transactions_frame = page.frame_locator("#iframe-old-pages")
 
            # Wait for the transactions tabs to be visible inside the iframe
            await transactions_frame.locator("#ulTabs").wait_for(state="visible", timeout=30000)
            logging.info("Transactions page loaded. Selecting 'Previous Month' tab.")
 
            # Now, evaluate the JavaScript function within the context of the correct frame.
            logging.info("Executing JavaScript to switch to previous month's transactions...")
            await transactions_frame.locator("body").evaluate("() => submitTab('3')")
 
            # The frame_locator will automatically wait for the frame to reload and find the element.
            logging.info("Waiting for previous month's transactions table to load...")
            await transactions_frame.locator("#dataTable077").wait_for(state="visible", timeout=20000)
 
            logging.info("Searching for the first transaction from 'חברת החשמל לישר'...")

            # --- NEW APPROACH: Intercept the network request for the details ---
            # We must start listening BEFORE the action that triggers the response.
            logging.info("Setting up listener and clicking row to intercept network response...")
            async with page.expect_response(
                lambda response: "MatafPortalServiceServlet" in response.url and "SUGBAKA=221" in response.url,
                timeout=20000
            ) as response_info:
                # Now, perform the click that triggers the network request.
                await click_row_by_text(
                    page,
                    transactions_frame,
                    table_selector="#dataTable077",
                    row_text="חברת החשמל לישר",
                    click_selector="a",
                )
            
            response = await response_info.value
            if response.status != 200:
                raise Exception(f"Failed to fetch charge details. Status: {response.status}")

            logging.info("Successfully intercepted the details response. Parsing HTML...")
            html_content = await response.text()

            # Use BeautifulSoup to parse the HTML content
            soup = BeautifulSoup(html_content, "html.parser")
            
            # Find the table by its ID
            table = soup.find("table", id="Chiuvim")
            if not table:
                raise Exception("Could not find the charges table with id='Chiuvim' in the response.")

            institution_map = {}
            if not args.skip_institution_mapping:
                # --- NEW: Fetch institution mapping ---
                institution_map = await get_institution_map()
                # If mapping was requested but failed, stop execution.
                if not institution_map:
                    logging.error("Failed to load institution map and --skip-institution-mapping was not provided. Halting execution.")
                    # You might want to raise an exception here or just return
                    return
            # Extract data from the table
            headers = [th.get_text(strip=True) for th in table.select("thead th")]
            # Add the new column header only if we have the mapping
            if institution_map:
                headers.append("מוסד")

            all_rows_data = []
            
            rows = table.select("tbody tr")
            for row in rows:
                cells = row.find_all("td")
                row_data = [cell.get_text(strip=True) for cell in cells]
                
                # --- Find and add institution name only if mapping exists ---
                if institution_map:
                    institution_name = "לא נמצא" # Default value
                    # Assuming the contract number is in the 3rd column ('פרטי בית העסק')
                    if len(row_data) > 2:
                        business_details = row_data[2]
                        # Extract the last 9 characters as the contract number
                        contract_number = business_details[-9:]
                        institution_name = institution_map.get(contract_number, "לא נמצא")
                    row_data.append(institution_name)

                all_rows_data.append(row_data)

            # --- Save data with append and de-duplication logic ---
            output_dir = args.run_output_dir
            os.makedirs(output_dir, exist_ok=True)
            run_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            individual_csv_path = os.path.join(output_dir, f"charges_report_{run_timestamp}.csv")
            with open(individual_csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(all_rows_data)
            logging.info(f"Saved individual run report to '{individual_csv_path}'")

            # Read existing master data, combine, de-duplicate, and save
            master_csv_path = os.path.join(output_dir, "all_charges_report.csv")
            combined_data = {} # Use a dict for easy de-duplication based on authorization number

            # Find the index of the authorization number column
            try:
                auth_num_index = headers.index("מספר הרשאה")
            except (ValueError, IndexError):
                raise Exception("Could not find 'מספר הרשאה' in headers. Cannot de-duplicate.")

            # Read existing data from the master file if it exists
            if os.path.exists(master_csv_path):
                with open(master_csv_path, "r", newline="", encoding="utf-8-sig") as f:
                    reader = csv.reader(f)
                    _ = next(reader) # Skip header row
                    for row in reader:
                        if len(row) > auth_num_index:
                            auth_number = row[auth_num_index]
                            combined_data[auth_number] = row

            # Add new data, overwriting duplicates
            for row in all_rows_data:
                if len(row) > auth_num_index:
                    auth_number = row[auth_num_index]
                    combined_data[auth_number] = row

            # Sort the combined data by date (first column, newest first)
            sorted_data = sorted(combined_data.values(), key=lambda x: datetime.strptime(x[0], "%d/%m/%Y"), reverse=True)

            # Write the final, de-duplicated, and sorted data back to the master file
            with open(master_csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(sorted_data)

            logging.info(f"Successfully updated master report '{master_csv_path}' with {len(sorted_data)} unique rows.")
            logging.info("Script finished successfully.")
            await asyncio.sleep(5)  # Keep browser open for 5 seconds to observe the result

        except PlaywrightTimeoutError as e:
            logging.error(f"A timeout occurred: {e}")
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}", exc_info=True)
            logging.error("Script finished with an error.")
        finally:
            await browser.close()
            logging.info("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())