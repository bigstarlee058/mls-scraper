import json
import time
import re
import logging
import argparse
from curl_cffi import requests
from bs4 import BeautifulSoup

# Setup simple logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def fetch_html(url):
    """
    Fetch HTML content from the URL using curl_cffi
    """
    try:
        response = requests.get(url, timeout=30, impersonate="chrome", verify=False)
        response.raise_for_status()
        return response.text

    except requests.exceptions.RequestException as e:
        logger.error(f"✗ Error fetching URL: {e}")
        return None


def extract_property_listings(html_content):
    """
    Extract property listing data from foreclosure.com HTML
    """
    soup = BeautifulSoup(html_content, "html.parser")

    properties = []

    listings = soup.find_all("div", class_="rowResults")

    for listing in listings:
        property_data = {}

        message_type = listing.find("div", class_="messajeType")
        if message_type:
            type_text = message_type.get_text(strip=True)
            type_text = type_text.replace("NEW", "").strip()
            property_data["listing_type"] = type_text

        price_elem = listing.find("span", class_="tdprice")
        if price_elem:
            price_strong = price_elem.find("strong")
            if price_strong:
                property_data["price"] = price_strong.get_text(strip=True)

            price_text_elem = price_elem.find("span", class_="priceText")
            if price_text_elem:
                property_data["price_type"] = price_text_elem.get_text(strip=True)

        address_div = listing.find("div", class_="address")
        if address_div:
            address_span = address_div.find("span", class_="address")
            if address_span:
                address_parts = address_span.find_all("span")
                if len(address_parts) >= 3:
                    property_data["street"] = address_parts[0].get_text(strip=True)
                    property_data["city"] = address_parts[1].get_text(strip=True)
                    property_data["state"] = address_parts[2].get_text(strip=True)
                    if len(address_parts) >= 4:
                        property_data["zip_code"] = address_parts[3].get_text(
                            strip=True
                        )

        bedrooms_box = listing.find("div", class_="bedroomsbox")
        if bedrooms_box:
            bed_text = bedrooms_box.get_text(strip=True)
            bed_text = bed_text.replace("bed", "").strip()
            if bed_text:
                property_data["bedrooms"] = bed_text

        bathrooms_box = listing.find("div", class_="barhroomsbox")
        if bathrooms_box:
            bath_text = bathrooms_box.get_text(strip=True)
            bath_text = bath_text.replace("bath", "").strip()
            if bath_text:
                property_data["bathrooms"] = bath_text

        size_box = listing.find("div", class_="sizebox")
        if size_box:
            sqft_text = size_box.get_text(strip=True)
            sqft_text = sqft_text.replace("sqft.", "").strip()
            if sqft_text:
                property_data["square_feet"] = sqft_text

        ptype_box = listing.find("div", class_="ptypebox")
        if ptype_box:
            ptype_text = ptype_box.get_text(strip=True)
            if ptype_text and ptype_text != "\xa0":
                property_data["property_type"] = ptype_text

        # Find all badges and check their content
        badges = listing.find_all("span", class_="badge")
        for badge in badges:
            badge_text = badge.get_text(strip=True)
            if "EST. RENT:" in badge_text:
                property_data["estimated_rent"] = badge_text.replace(
                    "EST. RENT:", ""
                ).strip()
            elif "AUCTION:" in badge_text:
                property_data["auction_date"] = badge_text.replace(
                    "AUCTION:", ""
                ).strip()

        listing_id = listing.get("id", "")
        if listing_id:
            property_data["listing_id"] = str(listing_id).replace("clone_", "")

        img_tag = listing.find("img", alt=True)
        if img_tag:
            img_src = img_tag.get("src")
            if isinstance(img_src, str):
                if img_src.startswith("//"):
                    property_data["image_url"] = "https:" + img_src
                elif not img_src.startswith("http"):
                    property_data["image_url"] = "https://mls.foreclosure.com" + img_src
                else:
                    property_data["image_url"] = img_src

        if property_data.get("street") or property_data.get("price"):
            properties.append(property_data)

    return properties


def get_next_page_number(html_content):
    """
    Extract the next page number from pagination
    """
    soup = BeautifulSoup(html_content, "html.parser")
    next_button = soup.find("a", {"id": "pageNextBottom"})
    if next_button:
        return next_button.get("data-nextpage")
    return None


def build_page_url(base_url, page_number):
    """
    Build URL for a specific page number
    """
    if "pg=" in base_url:
        return re.sub(r"pg=\d+", f"pg={page_number}", base_url)
    else:
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}pg={page_number}"


def scrape_all_pages(base_url, max_pages=None):
    """
    Scrape all pages starting from the base URL
    """
    all_properties = []
    current_page = 1

    logger.info("Starting scrape...\n")

    while True:
        if current_page == 1:
            url = base_url
        else:
            url = build_page_url(base_url, str(current_page))

        logger.info(f"Scraping page {current_page}...")

        html_content = fetch_html(url)

        if not html_content:
            logger.error(f"✗ Failed to fetch page {current_page}")
            break

        properties = extract_property_listings(html_content)

        if not properties:
            logger.warning(f"⚠ No properties found on page {current_page}")
            break

        logger.info(f"  ✓ Found {len(properties)} properties")
        all_properties.extend(properties)

        next_page = get_next_page_number(html_content)

        if not next_page:
            logger.info(f"\n✓ Reached last page (page {current_page})")
            break

        if max_pages and current_page >= max_pages:
            logger.info(f"\n⚠ Reached maximum page limit ({max_pages})")
            break

        try:
            current_page = int(next_page)
        except (ValueError, TypeError):
            logger.error(f"✗ Error parsing next page number: {next_page}")
            break

        time.sleep(1)

    return all_properties


def main():
    """
    Main function
    """
    parser = argparse.ArgumentParser(description="Scrape foreclosure property listings")
    parser.add_argument(
        "--url",
        type=str,
        default="https://mls.foreclosure.com/listing/search.html?ci=abilene&st=tx",
        help="URL to scrape",
    )
    parser.add_argument(
        "--max-pages", type=int, default=None, help="Maximum number of pages to scrape"
    )
    parser.add_argument(
        "--output", type=str, default="foreclosures.json", help="Output JSON file"
    )

    args = parser.parse_args()

    properties = scrape_all_pages(args.url, args.max_pages)

    if not properties:
        logger.error("\n✗ No properties found\n")
        return

    logger.info(f"\n{'='*50}")
    logger.info(f"✓ Successfully extracted {len(properties)} total properties")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(properties, f, indent=2, ensure_ascii=False)

    logger.info(f"✓ Data saved to: {args.output}")
    logger.info(f"{'='*50}\n")


if __name__ == "__main__":
    main()
