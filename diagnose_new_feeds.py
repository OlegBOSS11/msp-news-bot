"""One-off diagnostic: which of the candidate news sites have a working
RSS/Atom feed reachable from this server?

Tag/topic pages (e.g. "BBC — тема Сербия", "euronews — tag serbia") are
not real feeds — this checks the outlets' general RSS endpoints instead;
collect_news() already filters every non-Serbian-domain item for
Serbia/relocation relevance (see MSP_KEYWORDS / SERBIA_REQUIRED_TERMS in
parser.py), so a general outlet feed works fine as an input.

Run with: python3 diagnose_new_feeds.py

Safe to delete after diagnosis.
"""

from __future__ import annotations

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# name -> list of candidate feed URLs to try, first one that works wins
CANDIDATES: dict[str, list[str]] = {
    "DW (Russian)": [
        "https://rss.dw.com/rdf/rss-ru-all",
        "https://rss.dw.com/xml/rss-ru-all",
    ],
    "euronews (Russian)": [
        "https://ru.euronews.com/rss",
    ],
    "BBC Russian": [
        "http://feeds.bbci.co.uk/russian/rss.xml",
    ],
    "Sputnik Radio": [
        "https://radiosputnik.ru/export/rss2/index.xml",
        "https://radiosputnik.ru/export/rss2/archive/index.xml",
    ],
    "Rossiyskaya Gazeta": [
        "https://rg.ru/xml/index.xml",
    ],
    "RuSerbia.com": [
        "https://ruserbia.com/feed/",
        "https://ruserbia.com/rss/",
    ],
    "Serbskoeslovo.ru": [
        "https://www.serbskoeslovo.ru/feed/",
        "https://www.serbskoeslovo.ru/rss/",
    ],
    "kurir.rs": [
        "https://www.kurir.rs/rss/",
        "https://www.kurir.rs/feed/",
    ],
    "021.rs": [
        "https://www.021.rs/rss",
        "https://www.021.rs/feed/",
    ],
    "informer.rs": [
        "https://informer.rs/rss",
        "https://informer.rs/feed",
    ],
}


def main() -> None:
    for name, urls in CANDIDATES.items():
        print(f"=== {name} ===")
        for url in urls:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=12)
                ctype = resp.headers.get("content-type", "")
                looks_like_feed = "<rss" in resp.text[:500] or "<feed" in resp.text[:500] or "xml" in ctype
                print(
                    f"  {url}: STATUS {resp.status_code}, "
                    f"content-type={ctype!r}, looks_like_feed={looks_like_feed}, "
                    f"len={len(resp.text)}"
                )
            except Exception as exc:
                print(f"  {url}: EXCEPTION {type(exc).__name__}: {exc!r}")
        print()


if __name__ == "__main__":
    main()
