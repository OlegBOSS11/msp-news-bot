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
HALOOGLASI_CITIES = ["beograd", "novi-sad", "nis", "kragujevac"]
# (URL path segment, our internal deal_type value)
HALOOGLASI_DEALS = [("prodaja", "sale"), ("izdavanje", "rent")]
# Each entry: (url, city slug, deal_type) — city/deal_type aren't tagged on
# individual cards by the site, so we derive them from which URL we fetched.
HALOOGLASI_COLLECTOR_URLS = [
    (f"https://www.halooglasi.com/nekretnine/{slug}-stanova/{city}", city, deal_type)
    for city in HALOOGLASI_CITIES
    for slug, deal_type in HALOOGLASI_DEALS
]

# Common Cyrillic/Latin spellings a user might type -> HALOOGLASI_CITIES slug.
QUERY_CITY_ALIASES: dict[str, str] = {
    "белград": "beograd", "beograd": "beograd", "belgrade": "beograd",
    "нови-сад": "novi-sad", "нови сад": "novi-sad", "novi sad": "novi-sad", "novi-sad": "novi-sad",
    "ниш": "nis", "nis": "nis", "niš": "nis",
    "крагуевац": "kragujevac", "kragujevac": "kragujevac",
}

# Property-type / deal-type signals in a free-text query. Compiled once at
# import time and shared by every free-text filter below them, so
# "квартира"/"дом"/"купить"/"аренда" mean the same thing everywhere in
# this module — word stems handle Russian inflection (дом/дома/домов),
# with a negative lookbehind so "дом" doesn't match inside "рядом".
_HOUSE_RE = re.compile(r'(?<![а-яё])дом(?:а|ов|е|ый|ой|ашн)?(?=\s|$)|коттедж|вилл|villa|house|kuća')
_APARTMENT_RE = re.compile(r'(?<![а-яё])квартир(?:а|ы|у|е|ой|ам)?(?=\s|$)|студи(?:я|и|ю|ей)?(?=\s|$)|апартамент|stan|apartment')
_BUY_QUERY_RE = re.compile(r'купить|покупк(?:а|и|у|е|ой)?|продаж(?:а|и|у|е|ой)?|prodaja|buy')
_RENT_QUERY_RE = re.compile(r'аренд(?:а|ы|у|е|ой)?|снять|iznajmljiv(?:anje|ati)?|rent')


def _detect_query_city(query_lower: str) -> str | None:
    """Best-effort city slug from a free-text query, or None if none of
    the known cities/aliases are mentioned."""
    for alias, slug in QUERY_CITY_ALIASES.items():
        if alias in query_lower:
            return slug
    return None


def _listing_matches_query_filters(
    title_lower: str, url_lower: str,
    is_house: bool, is_apartment: bool, is_buy: bool, is_rent: bool,
) -> bool:
    """AND-combine every signal actually present in the query — a signal
    that's False (not mentioned) is a no-op, but ones that ARE present
    must ALL agree. This used to be an if/elif chain, where matching
    *any one* signal (e.g. property type) included the listing regardless
    of the others (e.g. deal type) — "купить квартиру" (buy an apartment)
    would pull in rentals too, as long as they were apartments, because
    the elif for is_buy never even ran once is_apartment's branch matched.
    """
    if is_house and not (_HOUSE_RE.search(title_lower) or "/kuce/" in url_lower or "/kuce-" in url_lower):
        return False
    if is_apartment and not (_APARTMENT_RE.search(title_lower) or "/stanovi/" in url_lower):
        return False
    if is_buy and not ("покупк" in title_lower or "продаж" in title_lower or "/prodaja/" in url_lower):
        return False
    if is_rent and not ("аренд" in title_lower or "iznajmljiv" in title_lower or "/iznajmljivanje/" in url_lower):
        return False
    return True


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
    city: str = ""  # HALOOGLASI_CITIES slug, e.g. "beograd"
    deal_type: str = ""  # "sale" or "rent"
    price_value: int | None = None  # comparable numeric price (EUR), for sorting


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


def _parse_price_value(raw: str) -> int | None:
    """Extract a comparable integer EUR amount, e.g. '199.800' -> 199800.

    Serbian sites format prices with '.' as the thousands separator.
    Returns None for non-numeric prices (e.g. "Cena na upit" / "on request").
    """
    digits = re.sub(r"[^\d]", "", raw or "")
    return int(digits) if digits else None


def parse_halooglasi(
    html: str,
    base_url: str,
    city: str = "",
    deal_type: str = "",
) -> list[PropertyListing]:
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

    Args:
        city: HALOOGLASI_CITIES slug this page was fetched for (e.g.
            "beograd") — the site doesn't tag individual cards with it,
            but every card on a per-city URL belongs to that city.
        deal_type: "sale" or "rent" — likewise derived from which URL
            (prodaja-stanova vs izdavanje-stanova) was fetched.
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
            value_el = card.select_one(".central-feature span[data-value]")
            price_value = _parse_price_value(value_el.get("data-value", "")) if value_el else None

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
                city=city,
                deal_type=deal_type,
                price_value=price_value,
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
    for url, city, deal_type in HALOOGLASI_COLLECTOR_URLS:
        html = await asyncio.to_thread(_fetch_page_sync, url, 15)
        if html:
            all_listings.extend(
                parse_halooglasi(html, "https://www.halooglasi.com", city=city, deal_type=deal_type)
            )
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
            city=listing.city,
            deal_type=listing.deal_type,
            price_value=listing.price_value,
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
            listing for listing in all_listings
            if query_lower in listing.title.lower()
            or (listing.location and query_lower in listing.location.lower())
        ]

    return all_listings[:15]  # Limit to 15 results


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
                city=row["city"] or "",
                deal_type=row["deal_type"] or "",
            )
            for row in db_rows
        ]
        if query:
            query_lower = query.lower()
            # Matching the query as one whole substring against the title
            # basically never hits real listing text ("покажи дом в
            # нови-саде" doesn't literally appear anywhere) — extract the
            # actual signals (city/type/deal) instead and AND them.
            city = _detect_query_city(query_lower)
            is_house = bool(_HOUSE_RE.search(query_lower))
            is_apartment = bool(_APARTMENT_RE.search(query_lower))
            is_buy = bool(_BUY_QUERY_RE.search(query_lower))
            is_rent = bool(_RENT_QUERY_RE.search(query_lower))

            def _city_matches(listing: PropertyListing) -> bool:
                if not city:
                    return True
                if listing.city == city:  # structured field, e.g. "novi-sad"
                    return True
                # Free-text `location` ("Novi Sad, Grbavica") uses a space
                # where the city slug uses a hyphen — normalize before
                # comparing, or "novi-sad" never matches "novi sad, ...".
                return bool(listing.location) and city.replace("-", " ") in listing.location.lower()

            if city or is_house or is_apartment or is_buy or is_rent:
                matched = [
                    listing for listing in db_listings
                    if _city_matches(listing)
                    and _listing_matches_query_filters(
                        listing.title.lower(), listing.url.lower(),
                        is_house, is_apartment, is_buy, is_rent,
                    )
                ]
                if matched:
                    db_listings = matched
            else:
                # No recognizable signal in the query at all — fall back
                # to a plain substring match rather than showing nothing
                # for a query about something this filter doesn't model
                # (e.g. a neighborhood name, a price). Checked first, not
                # as an "elif matched is empty" branch: with no signals,
                # the signal-based filter above would trivially match
                # every listing (nothing to disagree with), so it would
                # never even fall through to this branch otherwise.
                substring_matched = [
                    listing for listing in db_listings
                    if query_lower in listing.title.lower()
                    or (listing.location and query_lower in listing.location.lower())
                ]
                if substring_matched:
                    db_listings = substring_matched
        return db_listings[:15], False

    # DB not populated yet (e.g. collector hasn't run) — try a live scrape.
    listings = await search_real_estate(query)
    is_predefined = False

    # If no listings found, use predefined links with filtering
    if not listings:
        logger.info("Using predefined real estate listings as fallback")
        listings = PREDEFINED_LISTINGS
        is_predefined = True

        # Filter predefined listings based on query keywords. AND-combines
        # every signal actually present in the query (see
        # _listing_matches_query_filters) — this used to be an if/elif
        # chain, where matching *any one* signal included the listing
        # regardless of the others: "купить квартиру" (buy an apartment)
        # pulled in rentals too, as long as they were apartments, since
        # the elif checking is_buy never ran once is_apartment matched.
        if query:
            query_lower = query.lower()
            is_house = bool(_HOUSE_RE.search(query_lower))
            is_apartment = bool(_APARTMENT_RE.search(query_lower))
            is_buy = bool(_BUY_QUERY_RE.search(query_lower))
            is_rent = bool(_RENT_QUERY_RE.search(query_lower))

            filtered = [
                listing for listing in listings
                if _listing_matches_query_filters(
                    listing.title.lower(), listing.url.lower(),
                    is_house, is_apartment, is_buy, is_rent,
                )
            ]

            # If nothing matched specific filters, show all
            if filtered:
                listings = filtered

    return listings[:15], is_predefined
