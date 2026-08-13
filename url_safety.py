"""SSRF guard for URLs the bot fetches server-side.

aiogram's URLInputFile does NOT hand Telegram a URL to fetch on its own
infrastructure — its .read() calls bot.session.stream_content(url=...),
which downloads through *this server's own* aiohttp session (see
aiogram.types.URLInputFile). An image_url sourced from an RSS feed or a
scraped real-estate listing is external, semi-trusted input; unfiltered,
it could point this server at an internal service, a cloud metadata
endpoint (e.g. 169.254.169.254), or anything else reachable from here.

Call is_safe_to_fetch() right before constructing a URLInputFile from any
externally-sourced URL.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def is_safe_to_fetch(url: str) -> bool:
    """Best-effort check that `url` doesn't point at this server's own
    private network before we let aiogram download it.

    Not a defense against DNS rebinding (a hostname resolving to a public
    IP now and a private one when actually fetched moments later) — that
    would need a custom resolver wired into the connector doing the
    fetch, which is out of scope here: these URLs come from RSS feeds and
    scraped listing sites, not open attacker submissions.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False

    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not a literal IP — resolve off the event loop and check every
        # address it points to.
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except socket.gaierror as exc:
            logger.debug("is_safe_to_fetch: DNS resolution failed for %s: %s", host, exc)
            return False
        except Exception as exc:
            logger.warning("is_safe_to_fetch: unexpected error resolving %s: %s", host, exc)
            return False
        return not any(_is_disallowed_ip(ipaddress.ip_address(info[4][0])) for info in infos)

    return not _is_disallowed_ip(ip)
