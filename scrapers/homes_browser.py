import json
import time
import re
import logging
import argparse
import os
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup simple logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def extract_listings_data_and_pages(html_content):
    """
    Extracts all listings AND the max page number from the HTML content
    using BeautifulSoup for instant, offline parsing.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    listings = []

    max_page = 1
    paging_list = soup.find("ol", {"data-qa": "paging"})
    if paging_list:
        page_count = paging_list.get("data-page-count")
        if isinstance(page_count, str) and page_count.isdigit():
            max_page = int(page_count)

    cards = soup.find_all("div", class_="nhs-c-card--housing")

    for card in cards:
        try:
            plan_id = card.get("data-plan-id")
            spec_id = card.get("data-spec-id")

            listing = {}

            if isinstance(plan_id, str) and plan_id.strip():
                listing["plan_id"] = plan_id
                listing["listing_type"] = "plan"
            elif isinstance(spec_id, str) and spec_id.strip():
                listing["spec_id"] = spec_id
                listing["listing_type"] = "spec"
            else:
                continue

            listing.update(
                {
                    "community_id": card.get("data-community-id"),
                    "community_name": card.get("data-community-name"),
                    "plan_name": card.get("data-name"),
                    "builder_name": card.get("data-brand-name"),
                    "builder_id": card.get("data-builder-id"),
                    "city": card.get("data-city"),
                    "state": card.get("data-state-abbreviation"),
                    "zip_code": card.get("data-zip"),
                    "latitude": card.get("data-latitude"),
                    "longitude": card.get("data-longitude"),
                    "price_raw": card.get("data-price"),
                    "home_status": card.get("data-home-status"),
                    "phone_number": card.get("data-phone-number"),
                    "image_url": card.get("data-image-url"),
                }
            )

            price_elem = card.find("span", {"data-qa": "price_label"})
            if price_elem:
                listing["price_display"] = price_elem.get_text(strip=True)

            specs_elem = card.find("p", {"data-qa": "card_specs"})
            if specs_elem:
                specs_text = specs_elem.get_text(strip=True)
                listing["specs_raw"] = specs_text

                br = re.search(r"([\d.]+) Br", specs_text)
                ba = re.search(r"([\d.]+) Ba", specs_text)
                gr = re.search(r"([\d.]+) Gr", specs_text)
                sf = re.search(r"([\d,]+) sq ft", specs_text)

                if br:
                    listing["bedrooms"] = br.group(1)
                if ba:
                    listing["bathrooms"] = ba.group(1)
                if gr:
                    listing["garages"] = gr.group(1)
                if sf:
                    listing["sq_ft"] = sf.group(1).replace(",", "")

            url_elem = card.find("a", {"data-card-element": "planName"})
            if url_elem:
                listing["url"] = url_elem.get("href")
            else:
                url_elem = card.find("a", {"data-qa": "card_link"})
                if url_elem:
                    listing["url"] = url_elem.get("href")

            listings.append(listing)

        except Exception:
            continue

    return listings, max_page


def scrape_all_pages(base_url, max_pages=None):
    """
    Scrape pages by clicking the 'Next' button.
    """
    all_listings = []

    logger.info("Starting scrape using Bright Data Browser API...\n")

    browser_user = os.getenv("BRIGHTDATA_BROWSER_USER")
    browser_pass = os.getenv("BRIGHTDATA_BROWSER_PASS")

    if not all([browser_user, browser_pass]):
        raise ValueError(
            "Missing BrightData Scraping Browser credentials. "
            "Please set BRIGHTDATA_BROWSER_USER and BRIGHTDATA_BROWSER_PASS in .env file"
        )

    auth = f"{browser_user}:{browser_pass}"
    brd_connection_string = f"wss://{auth}@brd.superproxy.io:9222"

    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.connect_over_cdp(brd_connection_string, timeout=60000)

            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()

            def block_resources(route):
                if route.request.resource_type in ["image", "font", "media"]:
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", block_resources)

            logger.info(f"Loading: {base_url}")
            page.goto(base_url, wait_until="domcontentloaded", timeout=60000)

            tab_selector = 'a[data-qa="filters-result-type-homes"]'
            page.wait_for_selector(tab_selector, state="visible", timeout=30000)
            page.click(tab_selector)

            page.wait_for_selector(
                'ol[data-qa="paging"][data-page-count]', timeout=30000
            )

            initial_html = page.content()

            logger.info("Scraping page 1...")
            listings, total_pages = extract_listings_data_and_pages(initial_html)
            logger.info(f"  ✓ Found {len(listings)} listings")
            logger.info(f"  Total pages available: {total_pages}")
            all_listings.extend(listings)

            if max_pages:
                total_pages = min(total_pages, max_pages)
                logger.info(f"  ⚠ Limiting to {total_pages} pages\n")

            next_button_selector = "button[data-next]"

            for page_num in range(2, total_pages + 1):
                logger.info(f"Scraping page {page_num}/{total_pages}...")

                try:
                    page.wait_for_selector(
                        next_button_selector, state="visible", timeout=10000
                    )
                    if page.is_disabled(next_button_selector):
                        logger.warning("  ⚠ 'Next' button disabled. Ending scrape.")
                        break

                    page.click(next_button_selector)

                    active_page_selector = (
                        f'a[data-qa="pagination_number_active"][data-page="{page_num}"]'
                    )
                    page.wait_for_selector(active_page_selector, timeout=30000)

                    time.sleep(1)

                    current_html = page.content()
                    listings, _ = extract_listings_data_and_pages(current_html)

                    if not listings:
                        logger.warning(f"  ⚠ No listings found on page {page_num}")
                        break

                    logger.info(f"  ✓ Found {len(listings)} listings")
                    all_listings.extend(listings)

                except PlaywrightTimeoutError:
                    logger.error(f"  ✗ Timeout on page {page_num}. Ending scrape.")
                    break

                except Exception as e:
                    logger.error(f"  ✗ Error on page {page_num}: {e}")
                    break

        except PlaywrightTimeoutError as e:
            logger.error(f"✗ Timeout during initial load: {e}")

        finally:
            if browser:
                browser.close()

    return all_listings


def main():
    """
    Main function
    """
    parser = argparse.ArgumentParser(
        description="Scrape home listings from newhomesource.com using BrightData"
    )
    parser.add_argument(
        "--url",
        type=str,
        default="https://www.newhomesource.com/communities/ga/atlanta-area#home-listings",
        help="URL to scrape",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Maximum number of pages to scrape (default: 5)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="homes_browser.json",
        help="Output JSON file for homes",
    )

    args = parser.parse_args()

    listings = scrape_all_pages(args.url, args.max_pages)

    logger.info(f"\n{'='*50}")
    logger.info(f"✓ Successfully extracted {len(listings)} total listings")

    if listings:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(listings, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Data saved to: {args.output}")
    else:
        logger.error("✗ No listings found")

    logger.info(f"{'='*50}\n")


if __name__ == "__main__":
    main()
