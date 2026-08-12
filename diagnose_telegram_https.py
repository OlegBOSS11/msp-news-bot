"""One-off diagnostic: is HTTPS to Telegram's API itself unreliable, even
though raw ICMP ping/mtr showed a clean path (0% loss, stable ~48ms)?

Makes ~40 sequential HTTPS calls to api.telegram.org/bot<token>/getMe over
a few minutes, timing each and logging failures — the same kind of call
aiogram's long-polling makes constantly. If this shows a meaningfully
higher failure/timeout rate than the ICMP test, that points at TLS/HTTPS
level interference (e.g. DPI) rather than raw packet loss.

Run with: python3 diagnose_telegram_https.py

Safe to delete after diagnosis.
"""

from __future__ import annotations

import asyncio
import os
import time

import aiohttp
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ["BOT_TOKEN"]
URL = f"https://api.telegram.org/bot{TOKEN}/getMe"

N_REQUESTS = 40
DELAY_SECONDS = 5


async def main() -> None:
    ok = 0
    failed = 0
    timings = []

    async with aiohttp.ClientSession() as session:
        for i in range(N_REQUESTS):
            start = time.monotonic()
            try:
                async with session.get(URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    await resp.read()
                    elapsed = time.monotonic() - start
                    timings.append(elapsed)
                    ok += 1
                    print(f"try {i}: OK, {elapsed*1000:.0f} ms, status {resp.status}")
            except Exception as exc:
                elapsed = time.monotonic() - start
                failed += 1
                print(f"try {i}: FAILED after {elapsed*1000:.0f} ms — {type(exc).__name__}: {exc!r}")
            await asyncio.sleep(DELAY_SECONDS)

    print()
    print(f"=== Summary: {ok} ok, {failed} failed out of {N_REQUESTS} ===")
    if timings:
        print(f"latency: min={min(timings)*1000:.0f}ms max={max(timings)*1000:.0f}ms "
              f"avg={sum(timings)/len(timings)*1000:.0f}ms")


if __name__ == "__main__":
    asyncio.run(main())
