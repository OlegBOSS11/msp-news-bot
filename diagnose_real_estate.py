"""One-off diagnostic script for the cityexpert.rs fetch issue.

Run with: python3 diagnose_real_estate.py

Retries the fetch several times and prints the exact exception type/repr
for each failure, to pin down whether this is a network-level problem
(connection reset, timeout) or an application-level one (bad status,
blocked by Cloudflare, etc.). Safe to delete after diagnosis is done.
"""

from __future__ import annotations

import asyncio
import socket

import aiohttp

URL = "https://cityexpert.rs/prodaja-nekretnina/beograd"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


async def main() -> None:
    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    async with aiohttp.ClientSession(connector=connector) as session:
        for i in range(8):
            try:
                async with session.get(
                    URL, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    body = await resp.text()
                    print(f"try {i}: STATUS {resp.status}, body length {len(body)}")
            except Exception as exc:
                print(f"try {i}: EXCEPTION {type(exc).__name__}: {exc!r}")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
