"""One-off diagnostic script: is nekretnine.rs reachable and does it expose
__NEXT_DATA__ JSON like cityexpert.rs's Cloudflare-blocked scraping did not?

Run with: python3 diagnose_nekretnine.py

Safe to delete after diagnosis.
"""

from __future__ import annotations

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
URL = "https://www.nekretnine.rs/prodaja-stambenih-nekretnina/"


def main() -> None:
    for i in range(5):
        try:
            resp = requests.get(URL, headers=HEADERS, timeout=15)
            has_data = "__NEXT_DATA__" in resp.text
            print(
                f"try {i}: STATUS {resp.status_code}, "
                f"body length {len(resp.text)}, "
                f"has __NEXT_DATA__: {has_data}, "
                f"server header: {resp.headers.get('server')}"
            )
        except Exception as exc:
            print(f"try {i}: EXCEPTION {type(exc).__name__}: {exc!r}")


if __name__ == "__main__":
    main()
