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
    """Check whether the URL's domain is in (or a subdomain of) the whitelist.

    Exact match or a proper subdomain only — plain `.endswith(d)` would
    also match a domain that merely ends with the same characters, e.g.
    "eviln1info.rs".endswith("n1info.rs") is True even though it's an
    unrelated (attacker-controlled) domain.
    """
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        if not host:
            return False
        return any(
            host == domain or host.endswith(f".{domain}")
            for domain in WHITELIST_DOMAINS
        )
    except Exception:
        return False


def _entry_link(entry: dict) -> str | None:
    """Extract the first link whose domain is whitelisted, or None.

    No longer falls back to an unwhitelisted link — a feed entry can point
    anywhere in its <link>/<links>, and the whole point of WHITELIST_DOMAINS
    is to bound what URLs this bot will ever forward to users.
    """
    links = entry.get("links", [])
    if not links and entry.get("link"):
        links = [{"href": entry["link"]}]
    for link in links:
        href = link.get("href", "")
        if _domain_in_whitelist(href):
            return href
    return None


def _parse_pub_date(entry: dict) -> datetime | None:
    """Best-effort extraction of a timezone-aware (UTC) pub date.

    Some feeds omit the timezone offset in their RFC822 date, which makes
    parsedate_to_datetime() return a naive datetime — comparing that
    against the timezone-aware `cutoff` in collect_news() raises
    TypeError, which would otherwise take the whole feed down (caught by
    the outer per-entry try/except, but still loses every entry from that
    feed for no good reason). Treat a missing offset as UTC.
    """
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
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


def _is_safe_media_url(url: str) -> bool:
    """Only allow plain http(s) URLs through.

    image_url ends up handed to Telegram (URLInputFile — Telegram's own
    servers fetch it, not ours), but a feed entry is external input and
    shouldn't be able to hand the bot something like a file:/data: URL or
    an empty/garbage value that gets passed along unexamined.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _entry_image(entry: dict) -> str | None:
    """Best-effort image URL for a feed entry, for the picture-digest feature.

    Tries the common RSS/Atom conventions in order of reliability:
    media:thumbnail, media:content, <enclosure>, then a raw <img src="...">
    inside the summary HTML. Many feeds have none of these — that's fine,
    the caller falls back to a text-only message.
    """
    thumbs = entry.get("media_thumbnail") or []
    if thumbs and thumbs[0].get("url") and _is_safe_media_url(thumbs[0]["url"]):
        return thumbs[0]["url"]

    media = entry.get("media_content") or []
    for m in media:
        url = m.get("url")
        medium = (m.get("medium") or "").lower()
        mtype = (m.get("type") or "").lower()
        if url and _is_safe_media_url(url) and (medium == "image" or mtype.startswith("image/") or not mtype):
            return url

    for enc in entry.get("enclosures", []) or entry.get("links", []):
        etype = (enc.get("type") or "").lower()
        href = enc.get("href")
        if etype.startswith("image/") and href and _is_safe_media_url(href):
            return href

    raw_html = entry.get("summary") or entry.get("description") or ""
    import re
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw_html)
    if match and _is_safe_media_url(match.group(1)):
        return match.group(1)

    return None


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
        # One malformed entry (weird date format, missing fields, whatever)
        # must not take out the rest of an otherwise-good feed.
        try:
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
                    "image_url": _entry_image(entry),
                }
            )
        except Exception as exc:
            logger.warning("Feed %s: skipping malformed entry: %s", name, exc)
            continue
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
        if e["source"] in [
            "n1info.rs", "blic.rs", "b92.net", "telegraf.rs", "kurir.rs",
            "danas.rs", "nova.rs", "ruserbia.com", "russian.rs",
        ]:
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
        "danas.rs": 0,
        "nova.rs": 0,
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
        "russian.rs": 1,
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
