"""Real estate search module for Serbian property listings."""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin

import aiohttp
import requests
from bs4 import BeautifulSoup

import database

logger = logging.getLogger(__name__)

# Popular Serbian real estate websites
REAL_ESTATE_SOURCES = [
    {
        "name": "CityExpert",
        "base_url": "https://cityexpert.rs",
        "search_url": "https://cityexpert.rs/prodaja-nekretnina/beograd",
        "categories": [
            {"name": "Квартиры", "url": "https://cityexpert.rs/prodaja-stanova/beograd"},
            {"name": "Дома", "url": "https://cityexpert.rs/prodaja-kuci/beograd"},
            {"name": "Коммерческие", "url": "https://cityexpert.rs/prodaja-poslovnih-prostora/beograd"},
        ],
    },
    {
        # Confirmed reachable (unlike CityExpert/nekretnine.rs, which block
        # scripted requests) — August 2026.
        "name": "HaloOglasi",
        "base_url": "https://www.halooglasi.com",
        "search_url": "https://www.halooglasi.com/nekretnine/prodaja-stanova/beograd",
    },
    {
        "name": "Avito",
        "base_url": "https://www.avito.ru",
        "search_url": "https://www.avito.ru/all/serbiya/nedvizhimost",
        "skip_scraping": True,  # Avito requires JavaScript, can't scrape
    },
]

# Pages the background collector scrapes on a schedule to populate the
# local real_estate_listings DB table (see database.upsert_real_estate_listing).
# Kept short and polite (small number of pages, delay between requests) —
# this runs unattended every few hours, not on every user request.
HALOOGLASI_COLLECTOR_URLS = [
    "https://www.halooglasi.com/nekretnine/prodaja-stanova/beograd",
    "https://www.halooglasi.com/nekretnine/izdavanje-stanova/beograd",
]


@dataclass
class PropertyListing:
    """Represents a property listing."""
    title: str
    url: str
    price: str | None = None
    location: str | None = None
    source: str = ""
    image_url: str | None = None
    ad_id: str = ""  # stable per-site ad ID, used as the DB primary key


async def fetch_page(url: str, timeout: int = 10) -> str | None:
    """Fetch a web page and return its HTML content."""
    try:
        # Force IPv4 — see the comment in parser.collect_news() for why.
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        async with aiohttp.ClientSession(connector=connector) as session:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            }
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout), headers=headers) as resp:
                if resp.status == 200:
                    return await resp.text()
                logger.warning("Failed to fetch %s: HTTP %d", url, resp.status)
    except Exception as exc:
        logger.error("Error fetching %s: %s", url, exc)
    return None


def _fetch_page_sync(url: str, timeout: int = 15) -> str | None:
    """Blocking HTTP GET via `requests`, for hosts that block aiohttp.

    Confirmed empirically against halooglasi.com: identical User-Agent and
    target URL, but `requests` gets a clean 200 while `aiohttp` gets a
    blocked/malformed response — almost certainly Cloudflare bot-scoring
    based on TLS/HTTP-stack fingerprint rather than headers. Run through
    asyncio.to_thread() so this blocking call doesn't stall the event loop.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
        logger.warning("Failed to fetch %s: HTTP %d", url, resp.status_code)
    except Exception as exc:
        logger.error("Error fetching %s: %s", url, exc)
    return None


def parse_nekretnine(html: str, base_url: str) -> list[PropertyListing]:
    """Parse listings from nekretnine.rs."""
    listings = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".product-card")
        for card in cards[:10]:  # Limit to 10 results
            title_el = card.select_one(".product-card__title")
            link_el = card.select_one("a[href]")
            price_el = card.select_one(".product-card__price")
            location_el = card.select_one(".product-card__location")

            if title_el and link_el:
                title = title_el.get_text(strip=True)
                link = link_el.get("href", "")
                if not link.startswith("http"):
                    link = urljoin(base_url, link)
                price = price_el.get_text(strip=True) if price_el else None
                location = location_el.get_text(strip=True) if location_el else None

                listings.append(PropertyListing(
                    title=title,
                    url=link,
                    price=price,
                    location=location,
                    source="Nekretnine.rs",
                ))
    except Exception as exc:
        logger.error("Error parsing nekretnine.rs: %s", exc)
    return listings


def parse_halooglasi(html: str, base_url: str) -> list[PropertyListing]:
    """Parse listings from halooglasi.com.

    Selectors confirmed against the live site in August 2026 — each result
    card is a `.my-product-placeholder` div carrying the ad ID in
    `data-id`, e.g.:

        <div class="... my-product-placeholder" data-id="5425647385143">
          <div class="central-feature-wrapper">
            <div class="central-feature"><span data-value="199.800">
              <i>199.800 €</i></span></div>
          </div>
          ...
          <h3 class="product-title"><a href="/nekretnine/...">Title</a></h3>
          <ul class="subtitle-places"><li>Beograd</li>...</ul>
          <a class="a-images" href="..."><img src="https://img..."/></a>
        </div>
    """
    listings = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".my-product-placeholder")
        for card in cards:
            ad_id = card.get("data-id", "")
            title_el = card.select_one(".product-title a")
            if not ad_id or not title_el:
                continue

            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")
            if link and not link.startswith("http"):
                link = urljoin(base_url, link)

            price_el = card.select_one(".central-feature")
            price = price_el.get_text(strip=True) if price_el else None

            location_parts = [
                li.get_text(strip=True) for li in card.select(".subtitle-places li")
            ]
            location = ", ".join(p for p in location_parts if p) or None

            img_el = card.select_one("a.a-images img")
            image_url = img_el.get("src") if img_el else None
            if image_url and not image_url.startswith("http"):
                image_url = urljoin(base_url, image_url)

            listings.append(PropertyListing(
                ad_id=ad_id,
                title=title,
                url=link,
                price=price,
                location=location,
                source="HaloOglasi",
                image_url=image_url,
            ))
    except Exception as exc:
        logger.error("Error parsing halooglasi.com: %s", exc)
    return listings


def parse_4zida(html: str, base_url: str) -> list[PropertyListing]:
    """Parse listings from 4zida.rs."""
    listings = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".oglas")
        for card in cards[:10]:
            title_el = card.select_one(".oglas-title")
            link_el = card.select_one(".oglas-title a[href]")
            price_el = card.select_one(".oglas-price")
            location_el = card.select_one(".oglas-location")

            if title_el and link_el:
                title = title_el.get_text(strip=True)
                link = link_el.get("href", "")
                if not link.startswith("http"):
                    link = urljoin(base_url, link)
                price = price_el.get_text(strip=True) if price_el else None
                location = location_el.get_text(strip=True) if location_el else None

                listings.append(PropertyListing(
                    title=title,
                    url=link,
                    price=price,
                    location=location,
                    source="4zida.rs",
                ))
    except Exception as exc:
        logger.error("Error parsing 4zida.rs: %s", exc)
    return listings


def parse_cityexpert(html: str, base_url: str) -> list[PropertyListing]:
    """Parse listings from CityExpert.rs."""
    listings = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        # CityExpert uses property cards
        cards = soup.select("app-property-card")
        for card in cards[:30]:  # Show more listings to include houses
            # Get link - look for any anchor tag
            link_el = card.select_one("a[href]")
            link = ""
            if link_el:
                link = link_el.get("href", "")
                if link and not link.startswith("http"):
                    link = urljoin(base_url, link)

            # Get price - look for span with euro symbol
            price = None
            for span in card.select("span"):
                text = span.get_text(strip=True)
                if "€" in text and any(c.isdigit() for c in text):
                    price = text
                    break

            # Get location - look for div with address
            location = None
            for div in card.select("div"):
                text = div.get_text(strip=True)
                if "," in text and len(text) < 100 and "€" not in text:
                    location = text
                    break

            # Get property type and title from URL
            title = "Объект в Сербии"
            property_type = ""
            if link:
                # Extract description from URL
                url_parts = link.split("/")
                if len(url_parts) > 0:
                    slug = url_parts[-1] if url_parts[-1] else url_parts[-2]
                    # Convert slug to readable title
                    title = slug.replace("-", " ").title()
                    
                    # Detect property type from slug
                    slug_lower = slug.lower()
                    if "stan" in slug_lower:
                        property_type = "🏠 Квартира"
                    elif "kuc" in slug_lower or "kuć" in slug_lower:
                        property_type = "🏡 Дом"
                    elif "poslovn" in slug_lower:
                        property_type = "🏢 Коммерческий"
                    elif "garsonj" in slug_lower:
                        property_type = "🏠 Гарсоньера"
                    else:
                        property_type = "🏠 Недвижимость"

            # Get image URL
            image_el = card.select_one("img[alt='property photo']")
            image_url = None
            if image_el:
                image_url = image_el.get("src", "")
                if image_url and not image_url.startswith("http"):
                    image_url = urljoin(base_url, image_url)

            if link:
                listings.append(PropertyListing(
                    title=f"{property_type} {title}" if property_type else title,
                    url=link,
                    price=price,
                    location=location or "Белград",
                    source="CityExpert",
                    image_url=image_url,
                ))
    except Exception as exc:
        logger.error("Error parsing CityExpert: %s", exc)
    return listings


def parse_avito(html: str, base_url: str) -> list[PropertyListing]:
    """Parse listings from Avito."""
    listings = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        # Avito uses different selectors
        cards = soup.select("[data-marker='item']")
        for card in cards[:10]:
            title_el = card.select_one("[itemprop='name']")
            link_el = card.select_one("[itemprop='url']")
            price_el = card.select_one("[itemprop='price']")
            location_el = card.select_one("[data-marker='item-date']")

            if title_el:
                title = title_el.get_text(strip=True)
                link = ""
                if link_el:
                    link = link_el.get("href", "")
                    if not link.startswith("http"):
                        link = urljoin(base_url, link)
                price = price_el.get("content", "") if price_el else None
                location = location_el.get_text(strip=True) if location_el else None

                listings.append(PropertyListing(
                    title=title,
                    url=link,
                    price=price,
                    location=location,
                    source="Avito",
                ))
    except Exception as exc:
        logger.error("Error parsing Avito: %s", exc)
    return listings


async def collect_halooglasi_listings() -> list[PropertyListing]:
    """Fetch a few halooglasi.com listing pages for the background collector.

    Separate from search_real_estate()'s live per-request path — this is
    meant to be called on a schedule (see bot.py), not per user message.
    """
    all_listings: list[PropertyListing] = []
    for url in HALOOGLASI_COLLECTOR_URLS:
        html = await asyncio.to_thread(_fetch_page_sync, url, 15)
        if html:
            all_listings.extend(parse_halooglasi(html, "https://www.halooglasi.com"))
        await asyncio.sleep(3)  # be polite between requests
    return all_listings


async def refresh_real_estate_database() -> int:
    """Scrape halooglasi.com and upsert the results into the local DB.

    Called by the scheduler in bot.py every few hours (and once at
    startup) so search_real_estate_with_fallback() can serve fresh-ish
    listings straight from SQLite instead of scraping on every user
    request. Returns the number of listings collected (0 on failure).
    """
    try:
        listings = await collect_halooglasi_listings()
    except Exception as exc:
        logger.error("Real estate collector failed: %s", exc, exc_info=True)
        return 0

    for listing in listings:
        if not listing.ad_id:
            continue
        await database.upsert_real_estate_listing(
            ad_id=listing.ad_id,
            title=listing.title,
            price=listing.price,
            location=listing.location,
            url=listing.url,
            image_url=listing.image_url,
            source=listing.source,
        )

    await database.prune_stale_listings(days=7)

    logger.info("Real estate collector: upserted %d listings.", len(listings))
    return len(listings)


async def search_real_estate(query: str = "") -> list[PropertyListing]:
    """Search for real estate listings in Serbia."""
    all_listings = []

    for source in REAL_ESTATE_SOURCES:
        # Skip scraping for sources that require JavaScript (like Avito)
        if source.get("skip_scraping"):
            continue

        html = await fetch_page(source["search_url"])
        if html:
            if "cityexpert" in source["base_url"]:
                listings = parse_cityexpert(html, source["base_url"])
            elif "nekretnine.rs" in source["base_url"]:
                listings = parse_nekretnine(html, source["base_url"])
            elif "halooglasi" in source["base_url"]:
                listings = parse_halooglasi(html, source["base_url"])
            elif "4zida" in source["base_url"]:
                listings = parse_4zida(html, source["base_url"])
            elif "avito" in source["base_url"]:
                listings = parse_avito(html, source["base_url"])
            else:
                listings = []

            all_listings.extend(listings)

    # For CityExpert, don't filter by query - it's already filtered by location (Belgrade)
    # For other sources, filter by query if provided
    if query and all_listings and all_listings[0].source != "CityExpert":
        query_lower = query.lower()
        all_listings = [
            l for l in all_listings
            if query_lower in l.title.lower()
            or (l.location and query_lower in l.location.lower())
        ]

    return all_listings[:15]  # Limit to 15 results


def format_listings(listings: list[PropertyListing], is_predefined: bool = False) -> str:
    """Format property listings for Telegram message.

    Args:
        is_predefined: True when `listings` came from the static
            PREDEFINED_LISTINGS fallback rather than a live scrape — the
            caller should set this so the message warns the user that the
            prices/links may be outdated.
    """
    if not listings:
        return "🏠 К сожалению, подходящих предложений не найдено. Попробуйте изменить запрос."

    lines = ["🏠 <b>Актуальные предложения недвижимости в Сербии:</b>\n"]
    if is_predefined:
        lines.append(
            "⚠️ <i>Не удалось получить свежие данные с сайтов. Это примерные "
            "варианты — актуальность цен и наличие не гарантированы, "
            "проверяйте по ссылке.</i>\n"
        )

    for i, listing in enumerate(listings, 1):
        lines.append(f"<b>{i}. {listing.title}</b>")
        if listing.price:
            lines.append(f"💰 Цена: {listing.price}")
        if listing.location:
            lines.append(f"📍 Локация: {listing.location}")
        lines.append(f"🔗 <a href=\"{listing.url}\">Подробнее</a>")
        lines.append(f"🌐 Источник: {listing.source}")
        lines.append("")  # Empty line for spacing

    return "\n".join(lines)


# Keywords that indicate real estate query
REAL_ESTATE_KEYWORDS = [
    "недвижим", "квартир", "дом", "аренд", "купить", "продать",
    "жила", "жилье", "объект", "предложени",
    "nekretnine", "stan", "kuća", "iznajmljivanje", "prodaja",
    "real estate", "property", "apartment", "house",
]

# Keywords that indicate the user wants to see actual listings
LISTINGS_KEYWORDS = [
    "покажи", "найди", "есть ли", "где купить", "где снять",
    "поиск", "поискать", "объявлен", "предложен",
    "сколько стоит", "цена", "стоимость",
]

# Keywords that indicate the user wants information about the process
INFO_KEYWORDS = [
    "особенности", "как", "процедура", "порядок", "условия",
    "требовани", "документ", "налог", "закон",
    "процесс", "этап", "шаг",
]


def is_real_estate_query(text: str) -> str | None:
    """Check if the user's query is about real estate.

    Returns:
        "listings" - if user wants to see actual property listings
        "info" - if user wants information about the process/features
        None - if not a real estate query
    """
    text_lower = text.lower()

    # Check if it's a real estate query at all
    is_re = any(keyword in text_lower for keyword in REAL_ESTATE_KEYWORDS)
    if not is_re:
        return None

    # Check if user wants to see listings
    wants_listings = any(kw in text_lower for kw in LISTINGS_KEYWORDS)
    if wants_listings:
        return "listings"

    # Check if user wants information about the process
    wants_info = any(kw in text_lower for kw in INFO_KEYWORDS)
    if wants_info:
        return "info"

    # Default: if query is about real estate but doesn't match specific patterns,
    # check if it looks like a search query (contains location, price, etc.)
    has_location = any(loc in text_lower for loc in [
        "белград", "belgrade", "нови сад", "ниш", "крагуевац",
        "центр", "район", "улиц",
    ])
    has_price = any(p in text_lower for p in [
        "евро", "eur", "цена", "стоимость", "бюджет",
    ])

    if has_location or has_price:
        return "listings"

    # Default to info for general questions about real estate
    return "info"


# Predefined listings as fallback when scraping fails
PREDEFINED_LISTINGS = [
    # Квартиры в Белграде
    PropertyListing(
        title="2-комнатная квартира в центре Белграда",
        url="https://www.nekretnine.rs/stanovi/iznajmljivanje/beograd",
        price="от 500 EUR/мес",
        location="Белград, центр",
        source="Nekretnine.rs",
    ),
    PropertyListing(
        title="Квартира рядом с парком Калемегдан",
        url="https://www.nekretnine.rs/stanovi/iznajmljivanje/beograd-stari-grad",
        price="от 600 EUR/мес",
        location="Белград, Стари Град",
        source="Nekretnine.rs",
    ),
    PropertyListing(
        title="Просторная квартира в Новом Белграде",
        url="https://www.nekretnine.rs/stanovi/iznajmljivanje/novi-beograd",
        price="от 450 EUR/мес",
        location="Нови Белград",
        source="Nekretnine.rs",
    ),
    PropertyListing(
        title="Квартира в районе Дорчол",
        url="https://www.halooglasi.com/nekretnine/stanovi/iznajmljivanje/beograd",
        price="от 550 EUR/мес",
        location="Белград, Дорчол",
        source="HaloOglasi",
    ),
    PropertyListing(
        title="Уютная студия в Земуне",
        url="https://www.4zida.rs/iznajmljivanje-stanova/beograd",
        price="от 350 EUR/мес",
        location="Земун",
        source="4zida.rs",
    ),
    # Дома под Белградом
    PropertyListing(
        title="Дом с садом в пригороде Белграда",
        url="https://www.nekretnine.rs/kuce/iznajmljivanje/beograd-okolina",
        price="от 800 EUR/мес",
        location="Белград, пригород",
        source="Nekretnine.rs",
    ),
    PropertyListing(
        title="Вилла с бассейном в Раковице",
        url="https://www.nekretnine.rs/kuce/iznajmljivanje/rakovica",
        price="от 1200 EUR/мес",
        location="Раковица",
        source="Nekretnine.rs",
    ),
    PropertyListing(
        title="Загородный дом в Савском Венце",
        url="https://www.halooglasi.com/nekretnine/kuce/iznajmljivanje/beograd",
        price="от 900 EUR/мес",
        location="Савски Венац",
        source="HaloOglasi",
    ),
    # Квартиры для покупки
    PropertyListing(
        title="Квартира для покупки в центре Белграда",
        url="https://www.nekretnine.rs/stanovi/prodaja/beograd",
        price="от 1500 EUR/м²",
        location="Белград, центр",
        source="Nekretnine.rs",
    ),
    PropertyListing(
        title="Новостройка в Земуне",
        url="https://www.nekretnine.rs/novi-objekti/beograd",
        price="от 1200 EUR/м²",
        location="Земун",
        source="Nekretnine.rs",
    ),
    # Объявления с Авито (только прямая ссылка, без скрапинга)
    PropertyListing(
        title="🔍 Недвижимость в Сербии на Авито",
        url="https://www.avito.ru/all/serbiya/nedvizhimost",
        price="",
        location="Сербия",
        source="Avito",
    ),
]


async def search_real_estate_with_fallback(query: str = "") -> tuple[list[PropertyListing], bool]:
    """Search for real estate listings, preferring the locally collected DB.

    Priority order:
        1. The real_estate_listings DB table, populated on a schedule by
           refresh_real_estate_database() — fast, no live request needed.
        2. A live scrape (search_real_estate()), if the DB is empty (e.g.
           the collector hasn't run yet).
        3. The static PREDEFINED_LISTINGS, if even the live scrape failed.

    Returns:
        A tuple of (listings, is_predefined). `is_predefined` is True only
        for step 3 — callers should surface that to the user, since those
        prices/links are not guaranteed to be current. DB-backed (step 1)
        and freshly-scraped (step 2) listings are both real data.
    """
    db_rows = await database.get_real_estate_listings(limit=100)
    if db_rows:
        db_listings = [
            PropertyListing(
                ad_id=row["ad_id"],
                title=row["title"],
                url=row["url"],
                price=row["price"],
                location=row["location"],
                source=row["source"],
                image_url=row["image_url"],
            )
            for row in db_rows
        ]
        if query:
            query_lower = query.lower()
            matched = [
                l for l in db_listings
                if query_lower in l.title.lower()
                or (l.location and query_lower in l.location.lower())
            ]
            if matched:
                db_listings = matched
        return db_listings[:15], False

    # DB not populated yet (e.g. collector hasn't run) — try a live scrape.
    listings = await search_real_estate(query)
    is_predefined = False

    # If no listings found, use predefined links with filtering
    if not listings:
        logger.info("Using predefined real estate listings as fallback")
        listings = PREDEFINED_LISTINGS
        is_predefined = True

        # Filter predefined listings based on query keywords
        if query:
            query_lower = query.lower()
            filtered = []

            # Determine what type of property the user is looking for
            # Match word stems to handle Russian word forms (дом, дома, домов, etc.)
            # Use negative lookbehind to avoid matching inside words (e.g. "дом" in "рядом")
            import re
            is_house = bool(re.search(r'(?<![а-яё])дом(?:а|ов|е|ый|ой|ашн)?(?=\s|$)|коттедж|вилл|villa|house|kuća', query_lower))
            is_apartment = bool(re.search(r'(?<![а-яё])квартир(?:а|ы|у|е|ой|ам)?(?=\s|$)|студи(?:я|и|ю|ей)?(?=\s|$)|апартамент|stan|apartment', query_lower))
            is_buy = bool(re.search(r'купить|покупк(?:а|и|у|е|ой)?|продаж(?:а|и|у|е|ой)?|prodaja|buy', query_lower))
            is_rent = bool(re.search(r'аренд(?:а|ы|у|е|ой)?|снять|изнajmljiv(?:anje|ati)?|rent', query_lower))

            for listing in listings:
                title_lower = listing.title.lower()
                url_lower = listing.url.lower()

                # Check if listing matches the query type
                # Use regex with negative lookbehind for title matching to avoid false positives
                if is_house and (re.search(r'(?<![а-яё])дом(?:а|ов|е|ый|ой|ашн)?(?=\s|$)|коттедж|вилл|villa', title_lower) or "/kuce/" in url_lower or "/kuce-" in url_lower):
                    filtered.append(listing)
                elif is_apartment and (re.search(r'(?<![а-яё])квартир(?:а|ы|у|е|ой|ам)?(?=\s|$)|студи(?:я|и|ю|ей)?(?=\s|$)', title_lower) or "/stanovi/" in url_lower):
                    filtered.append(listing)
                elif is_buy and ("покупк" in title_lower or "продаж" in title_lower or "/prodaja/" in url_lower):
                    filtered.append(listing)
                elif is_rent and ("аренд" in title_lower or "изнajmljiv" in title_lower or "/iznajmljivanje/" in url_lower):
                    filtered.append(listing)
                elif not is_house and not is_apartment and not is_buy and not is_rent:
                    # No specific filter, show all
                    filtered.append(listing)

            # If nothing matched specific filters, show all
            if filtered:
                listings = filtered

    return listings[:15], is_predefined
