import json
import time
import re
import logging
import argparse
import os
from curl_cffi import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup simple logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def fetch_html(url):
    """
    Fetch HTML content from the given URL with BrightData proxy support
    using curl_cffi to impersonate a real browser.
    """
    proxy_host = os.getenv("BRIGHTDATA_PROXY_HOST")
    proxy_user_base = os.getenv("BRIGHTDATA_PROXY_USER")
    proxy_pass = os.getenv("BRIGHTDATA_PROXY_PASS")

    if not all([proxy_host, proxy_user_base, proxy_pass]):
        raise ValueError(
            "Missing BrightData proxy credentials. "
            "Please set BRIGHTDATA_PROXY_HOST, BRIGHTDATA_PROXY_USER, and BRIGHTDATA_PROXY_PASS in .env file"
        )

    proxy_user = f"{proxy_user_base}-country-us"
    proxy_url = f"http://{proxy_user}:{proxy_pass}@{proxy_host}"

    proxies = {"http": proxy_url, "https": proxy_url}

    try:
        response = requests.get(
            url,
            proxies=proxies,  # type: ignore
            timeout=30,
            verify=False,
            impersonate="chrome",
        )
        response.raise_for_status()
        return response.text

    except requests.exceptions.RequestException as e:
        logger.error(f"✗ Error: {e}")
        return None


def get_max_page_number(html_content):
    """
    Extract the maximum page number from pagination
    """
    soup = BeautifulSoup(html_content, "html.parser")
    paging_list = soup.find("ol", {"data-qa": "paging"})
    if paging_list:
        page_count = paging_list.get("data-page-count")
        if isinstance(page_count, str) and page_count.isdigit():
            return int(page_count)
    return 1


def build_page_url(base_url, page_number):
    """
    Build URL for a specific page number
    """
    if page_number == 1:
        return base_url

    base_url = base_url.rstrip("/")

    return f"{base_url}/page-{page_number}"


def extract_community_data(html_content):
    """
    Extract community/property data from newhomesource.com HTML
    """
    soup = BeautifulSoup(html_content, "html.parser")

    communities = []

    cards = soup.find_all("div", class_="nhs-c-card--housing")

    for card in cards:
        community = {}

        community["community_id"] = card.get("data-community-id", "")
        community["community_name"] = card.get("data-community-name", "")
        community["city"] = card.get("data-city", "")
        community["state"] = card.get("data-state-abbreviation", "")
        community["state_name"] = card.get("data-state-name", "")
        community["zip_code"] = card.get("data-zip", "")

        community["latitude"] = card.get("data-latitude", "")
        community["longitude"] = card.get("data-longitude", "")

        community["price_low"] = card.get("data-price-low", "")
        community["price_high"] = card.get("data-price-high", "")

        community["builder_id"] = card.get("data-builder-id", "")
        community["brand_name"] = card.get("data-brand-name", "")
        community["brand_id"] = card.get("data-brand-id", "")

        community["market_name"] = card.get("data-market-name", "")
        community["market_id"] = card.get("data-market-id", "")

        community["marketing_status"] = card.get("data-marketing-status", "")

        community["primary_image"] = card.get("data-image-url", "")
        community["secondary_image"] = card.get("data-secondary-image-url", "")
        community["tertiary_image"] = card.get("data-tertiary-image-url", "")

        community["phone_number"] = card.get("data-phone-number", "")

        community["community_type"] = card.get("data-community-type", "")

        price_elem = card.find("span", {"data-qa": "price_label"})
        if price_elem:
            community["price_display"] = price_elem.get_text(strip=True)

        url_link = card.find("a", {"data-card-element": "communityName"})
        if url_link:
            community["url"] = url_link.get("href", "")

        address_elem = card.find("p", {"data-qa": "listing_address"})
        if address_elem:
            community["address_display"] = address_elem.get_text(strip=True)

        stats = card.find_all("span", class_="nhs-c-card__stat")
        for stat in stats:
            stat_text = stat.get_text(strip=True)
            homes_match = re.search(r"(\d+)\s+Homes?", stat_text)
            if homes_match:
                community["num_homes"] = homes_match.group(1)
            plans_match = re.search(r"(\d+)\s+Floor plans?", stat_text)
            if plans_match:
                community["num_floor_plans"] = plans_match.group(1)

        hot_deal = card.find("span", {"data-qa": "hot-deal"})
        community["hot_deal"] = bool(hot_deal)

        if community.get("community_name") or community.get("community_id"):
            communities.append(community)

    return communities


def scrape_all_pages(base_url, max_pages=None):
    """
    Scrape all pages starting from the base URL
    """
    all_communities = []

    logger.info("Starting scrape...")
    logger.info("  Using BrightData proxy\n")

    logger.info("Fetching page 1...")
    html_content = fetch_html(base_url)

    if not html_content:
        logger.error("✗ Failed to fetch first page")
        return []

    total_pages = get_max_page_number(html_content)
    logger.info(f"  Total pages available: {total_pages}")

    if max_pages:
        total_pages = min(total_pages, max_pages)
        logger.info(f"  ⚠ Limiting to {total_pages} pages")

    communities = extract_community_data(html_content)
    logger.info(f"  ✓ Found {len(communities)} communities\n")
    all_communities.extend(communities)

    for page_num in range(2, total_pages + 1):
        url = build_page_url(base_url, page_num)
        logger.info(f"Scraping page {page_num}/{total_pages}...")

        html_content = fetch_html(url)

        if not html_content:
            logger.error(f"  ✗ Failed to fetch page {page_num}")
            continue

        communities = extract_community_data(html_content)

        if not communities:
            logger.warning(f"  ⚠ No communities found on page {page_num}")
            continue

        logger.info(f"  ✓ Found {len(communities)} communities")
        all_communities.extend(communities)

        time.sleep(2)

    return all_communities


def main():
    """
    Main function
    """
    parser = argparse.ArgumentParser(
        description="Scrape community listings from newhomesource.com"
    )
    parser.add_argument(
        "url",
        nargs="?",
        default="https://www.newhomesource.com/communities/ga/atlanta-area",
        help="URL to scrape",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
        help="Maximum number of pages to scrape (default: 20)",
    )
    parser.add_argument(
        "--output", type=str, default="communities.json", help="Output JSON file"
    )

    args = parser.parse_args()

    communities = scrape_all_pages(args.url, args.max_pages)

    if not communities:
        logger.error("\n✗ No communities found\n")
        return

    logger.info(f"\n{'='*50}")
    logger.info(f"✓ Successfully extracted {len(communities)} total communities")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(communities, f, indent=2, ensure_ascii=False)

    logger.info(f"✓ Data saved to: {args.output}")
    logger.info(f"{'='*50}\n")


if __name__ == "__main__":
    main()
