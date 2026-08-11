from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import aiohttp
import feedparser

from config import (
    MAX_NEWS,
    MSP_KEYWORDS,
    RSS_FEEDS,
    WHITELIST_DOMAINS,
)

logger = logging.getLogger(__name__)

MOSCOW_TZ = timezone(timedelta(hours=3))


def _domain_in_whitelist(url: str) -> bool:
    """Check whether the URL's domain is in the whitelist."""
    try:
        host = urlparse(url).hostname or ""
        return any(host.endswith(d) for d in WHITELIST_DOMAINS)
    except Exception:
        return False


def _entry_link(entry: dict) -> str | None:
    """Extract the first link whose domain is whitelisted."""
    links = entry.get("links", [])
    if not links and entry.get("link"):
        links = [{"href": entry["link"]}]
    for link in links:
        href = link.get("href", "")
        if _domain_in_whitelist(href):
            return href
    # Fallback: accept any link from a whitelisted feed (the feed itself
    # is whitelisted, so individual links may point to non-whitelisted
    # subdomains — we still accept them).
    if links:
        return links[0].get("href")
    return None


def _parse_pub_date(entry: dict) -> datetime | None:
    """Best-effort extraction of a timezone-aware pub date."""
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        pass
    # feedparser sometimes gives a struct_time
    for field in ("published_parsed", "updated_parsed"):
        st = entry.get(field)
        if st:
            from time import mktime
            try:
                return datetime.fromtimestamp(mktime(st), tz=timezone.utc)
            except Exception:
                pass
    return None


def _score(entry: dict) -> int:
    """How many MSP keywords appear in title + summary."""
    text = (
        (entry.get("title") or "")
        + " "
        + (entry.get("summary") or entry.get("description") or "")
    ).lower()
    return sum(1 for kw in MSP_KEYWORDS if kw.lower() in text)


def _entry_summary(entry: dict) -> str:
    """Plain-text summary, stripped of HTML tags."""
    import re
    raw = entry.get("summary") or entry.get("description") or ""
    # Strip tags
    clean = re.sub(r"<[^>]+>", "", raw)
    # Collapse whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    # Truncate to ~300 chars
    if len(clean) > 300:
        clean = clean[:297] + "..."
    return clean


async def _fetch_feed(
    session: aiohttp.ClientSession,
    feed_cfg: dict[str, str],
) -> list[dict]:
    """Fetch and parse one RSS/Atom feed."""
    url = feed_cfg["url"]
    name = feed_cfg["name"]
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "MSP-News-Bot/1.0"},
        ) as resp:
            if resp.status != 200:
                logger.warning("Feed %s returned HTTP %s", name, resp.status)
                return []
            raw_bytes = await resp.read()
    except Exception as exc:
        logger.warning("Feed %s fetch error: %s", name, exc)
        return []

    # Try utf-8 first, then cp1251 (garant.ru uses windows-1251)
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            raw = raw_bytes.decode("cp1251")
        except UnicodeDecodeError:
            raw = raw_bytes.decode("latin-1")

    d = feedparser.parse(raw)
    results: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    for entry in d.entries:
        link = _entry_link(entry)
        if not link:
            continue

        pub_date = _parse_pub_date(entry)
        # If we can't parse the date, keep the entry (some feeds omit it)
        if pub_date and pub_date < cutoff:
            continue

        results.append(
            {
                "title": (entry.get("title") or "Без заголовка").strip(),
                "link": link,
                "summary": _entry_summary(entry),
                "score": _score(entry),
                "source": name,
            }
        )
    return results


async def collect_news() -> list[dict]:
    """Gather, filter, and rank news from all whitelisted feeds."""
    # Force IPv4: some hosts (e.g. IPv6-less VDS) have working IPv4 routes
    # but no IPv6 route, and letting aiohttp's happy-eyeballs try IPv6 first
    # can cause intermittent connection failures instead of a clean fallback.
    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [_fetch_feed(session, cfg) for cfg in RSS_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_entries: list[dict] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Feed task raised: %s", r)
            continue
        all_entries.extend(r)

    # Filter: only entries with score > 0 (at least one relevant keyword)
    relevant = [e for e in all_entries if e["score"] > 0]

    # Additional filter: news must mention Serbia or relocation-related terms
    # to avoid showing unrelated Russian news
    # Only Serbian-specific terms are used for filtering Russian sources
    SERBIA_REQUIRED_TERMS = [
        "серб", "релокац", "переезд", "внж", "пмж", "белград",
        "nekretnine", "stan", "kuća", "iznajmljivanje", "prodaja",
    ]
    filtered = []
    for e in relevant:
        text = (e.get("title", "") + " " + e.get("summary", "")).lower()
        # Serbian sources (and Serbia-focused portals) always pass,
        # general Russian/international sources must mention Serbia terms
        if e["source"] in ["n1info.rs", "blic.rs", "b92.net", "telegraf.rs", "kurir.rs", "ruserbia.com"]:
            filtered.append(e)
        elif any(term in text for term in SERBIA_REQUIRED_TERMS):
            filtered.append(e)
    relevant = filtered

    # Sort by score descending, then by source priority (Serbian sources first)
    source_priority = {
        # Сербские источники (высший приоритет)
        "n1info.rs": 0,
        "b92.net": 0,
        "blic.rs": 0,
        "telegraf.rs": 0,
        "rsponline.rs": 0,
        "novosti.rs": 0,
        "politika.rs": 0,
        "tanjug.rs": 0,
        "serbia.travel": 0,
        "investserbia.org": 0,
        "kurir.rs": 0,
        # Международные источники
        "reuters.com": 1,
        "bbc.com": 1,
        "bbci.co.uk": 1,
        "euronews.com": 1,
        # Российские источники (контекст)
        "rbc.ru": 2,
        "kommersant.ru": 2,
        "vedomosti.ru": 2,
        "tass.ru": 2,
        "ria.ru": 2,
        "radiosputnik.ru": 2,
        "rg.ru": 2,
        # Русскоязычные источники о Сербии
        "serbiarus.com": 1,
        "rsmedia.ru": 1,
        "ruserbia.com": 1,
    }
    relevant.sort(
        key=lambda e: (-e["score"], source_priority.get(e["source"], 9)),
    )

    # Deduplicate by link
    seen: set[str] = set()
    deduped: list[dict] = []
    for e in relevant:
        if e["link"] not in seen:
            seen.add(e["link"])
            deduped.append(e)

    return deduped[:MAX_NEWS]
