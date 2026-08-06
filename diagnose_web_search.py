"""One-off diagnostic script for serbia_search.py's DuckDuckGo fetch.

Run with: python3 diagnose_web_search.py

Checks whether the DuckDuckGo HTML search endpoint (used by
serbia_search.search_web, which backs every "search the web with
sources" answer in the bot, not just real estate) is reachable from
this server. Safe to delete after diagnosis.
"""

from __future__ import annotations

import asyncio
import socket
from urllib.parse import quote_plus

import aiohttp

QUERY = "квартира Белград аренда Сербия"
URL = f"https://html.duckduckgo.com/html/?q={quote_plus(QUERY)}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


async def main() -> None:
    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    for i in range(5):
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    URL, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    html = await resp.text()
                    has_results = "result__title" in html
                    print(
                        f"try {i}: STATUS {resp.status}, body length {len(html)}, "
                        f"has result markup: {has_results}"
                    )
        except Exception as exc:
            print(f"try {i}: EXCEPTION {type(exc).__name__}: {exc!r}")
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
