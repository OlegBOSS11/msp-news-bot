"""One-off diagnostic: which of these candidate RSS/Atom feeds actually
work from this server (HTTP 200 + parses as a real feed)?

Candidates are Serbian news outlets not yet in config.WHITELIST_DOMAINS/
RSS_FEEDS, plus russian.rs (Russian diaspora in Serbia — relocation-focused,
not pure news, but on-topic). Found via web search, URLs guessed from
common feed path conventions — several are likely wrong/dead, that's
expected and fine, this script is exactly how we find out which.

Run with: python3 diagnose_new_feeds2.py

Safe to delete after diagnosis.
"""

from __future__ import annotations

import feedparser
import requests

CANDIDATES = [
    ("danas.rs", "https://danas.rs/feed/"),
    ("danas.rs (www)", "https://www.danas.rs/feed/"),
    ("informer.rs", "https://informer.rs/rss"),
    ("informer.rs (feed)", "https://informer.rs/feed"),
    ("republika.rs", "https://www.republika.rs/rss"),
    ("mondo.rs", "https://mondo.rs/rss"),
    ("021.rs", "https://www.021.rs/rss"),
    ("insajder.net", "https://insajder.net/sr/feed/"),
    ("nova.rs", "https://nova.rs/feed/"),
    ("russian.rs", "https://russian.rs/feed/"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def check(name: str, url: str) -> None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
    except Exception as exc:
        print(f"{name:20} {url:45} FAILED: {type(exc).__name__}: {exc}")
        return

    if resp.status_code != 200:
        print(f"{name:20} {url:45} HTTP {resp.status_code}")
        return

    parsed = feedparser.parse(resp.content)
    n_entries = len(parsed.entries)
    bozo = parsed.bozo  # 1 if feedparser had to guess/repair the XML
    if n_entries == 0:
        print(f"{name:20} {url:45} HTTP 200 but 0 entries parsed (bozo={bozo}) — probably not a real feed")
    else:
        title = parsed.entries[0].get("title", "?")[:50]
        print(f"{name:20} {url:45} OK — {n_entries} entries, bozo={bozo}, first: {title!r}")


if __name__ == "__main__":
    print(f"Checking {len(CANDIDATES)} candidate feeds...\n")
    for name, url in CANDIDATES:
        check(name, url)
