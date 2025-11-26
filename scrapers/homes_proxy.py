import json
import re
import logging
import argparse
import os
from playwright.sync_api import (
    sync_playwright,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup simple logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def get_max_page_number(html_content):
    """
    Extract the maximum page number from the pagination bar using BeautifulSoup.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    paging_list = soup.find("ol", {"data-qa": "paging"})
    if paging_list:
        page_count = paging_list.get("data-page-count")
        if isinstance(page_count, str) and page_count.isdigit():
            return int(page_count)
    return 1


def extract_listings_data(page: Page):
    """
    Extract property/plan data directly using Playwright Locators for robustness.
    This version handles both 'plan' and 'spec' type listings.
    """
    listings = []
    cards = page.locator("div.nhs-c-card--housing")
    card_count = cards.count()

    for i in range(card_count):
        card = cards.nth(i)
        try:
            plan_id = card.get_attribute("data-plan-id")
            spec_id = card.get_attribute("data-spec-id")

            listing = {}

            if plan_id and plan_id.strip():
                listing["plan_id"] = plan_id
                listing["listing_type"] = "plan"
            elif spec_id and spec_id.strip():
                listing["spec_id"] = spec_id
                listing["listing_type"] = "spec"
            else:
                continue

            listing.update(
                {
                    "community_id": card.get_attribute("data-community-id"),
                    "community_name": card.get_attribute("data-community-name"),
                    "plan_name": card.get_attribute("data-name"),
                    "builder_name": card.get_attribute("data-brand-name"),
                    "builder_id": card.get_attribute("data-builder-id"),
                    "city": card.get_attribute("data-city"),
                    "state": card.get_attribute("data-state-abbreviation"),
                    "zip_code": card.get_attribute("data-zip"),
                    "latitude": card.get_attribute("data-latitude"),
                    "longitude": card.get_attribute("data-longitude"),
                    "price_raw": card.get_attribute("data-price"),
                    "home_status": card.get_attribute("data-home-status"),
                    "phone_number": card.get_attribute("data-phone-number"),
                    "image_url": card.get_attribute("data-image-url"),
                }
            )

            price_elem = card.locator('span[data-qa="price_label"]')
            if price_elem.count() > 0:
                listing["price_display"] = price_elem.inner_text()

            specs_elem = card.locator('p[data-qa="card_specs"]')
            if specs_elem.count() > 0:
                specs_text = specs_elem.inner_text()
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

            url_elem = card.locator('a[data-card-element="planName"]')
            if url_elem.count() > 0:
                listing["url"] = url_elem.get_attribute("href")
            else:
                url_elem = card.locator('a[data-qa="card_link"]')
                if url_elem.count() > 0:
                    listing["url"] = url_elem.get_attribute("href")

            listings.append(listing)

        except Exception:
            continue

    return listings


def scrape_all_pages(base_url, max_pages=None):
    """
    Scrape pages by clicking the 'Next' button, which is more reliable for JS-driven sites.
    """
    all_listings = []

    logger.info("Starting scrape using Bright Data proxy...\n")

    with sync_playwright() as p:
        proxy_host = os.getenv("BRIGHTDATA_PROXY_HOST")
        proxy_user = os.getenv("BRIGHTDATA_PROXY_USER")
        proxy_pass = os.getenv("BRIGHTDATA_PROXY_PASS")

        if not all([proxy_host, proxy_user, proxy_pass]):
            raise ValueError(
                "Missing BrightData proxy credentials. "
                "Please set BRIGHTDATA_PROXY_HOST, BRIGHTDATA_PROXY_USER, and BRIGHTDATA_PROXY_PASS in .env file"
            )

        proxy_config = {
            "server": f"http://{proxy_host}",
            "username": proxy_user,
            "password": proxy_pass,
        }

        browser = None
        try:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                proxy=proxy_config, ignore_https_errors=True  # type: ignore
            )
            page = context.new_page()

            logger.info(f"Loading: {base_url}")
            page.goto(base_url, wait_until="load", timeout=60000)
            page.wait_for_selector(
                'ol[data-qa="paging"][data-page-count]', timeout=30000
            )

            initial_html = page.content()
            total_pages = get_max_page_number(initial_html)
            logger.info(f"  Total pages available: {total_pages}")

            if max_pages:
                total_pages = min(total_pages, max_pages)
                logger.info(f"  ⚠ Limiting to {total_pages} pages\n")

            logger.info("Scraping page 1...")
            listings = extract_listings_data(page)
            logger.info(f"  ✓ Found {len(listings)} listings")
            all_listings.extend(listings)

            for page_num in range(2, total_pages + 1):
                logger.info(f"Scraping page {page_num}/{total_pages}...")

                next_button_selector = "button[data-next]"

                try:
                    page.wait_for_selector(
                        next_button_selector, state="visible", timeout=10000
                    )
                    if page.is_disabled(next_button_selector):
                        logger.warning("  ⚠ 'Next' button disabled. Ending scrape.")
                        break

                    page.click(next_button_selector)
                    page.wait_for_url(
                        f"**/page-{page_num}#home-listings", timeout=60000
                    )
                    page.locator("div.nhs-c-card--housing").first.wait_for(
                        timeout=60000
                    )

                    listings = extract_listings_data(page)

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

        finally:
            if browser:
                browser.close()

    return all_listings


def main():
    """
    Main function
    """
    parser = argparse.ArgumentParser(
        description="Scrape home listings from newhomesource.com using Bright Data proxy"
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
        default=15,
        help="Maximum number of pages to scrape (default: 15)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="homes_http.json",
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
