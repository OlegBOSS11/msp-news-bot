"""One-off inspection: dump the actual JSON/HTML structure of a
halooglasi.com listing page, so the real parser can be written against
real field names instead of guesses.

Run with: python3 inspect_halooglasi.py

Safe to delete after inspection.
"""

from __future__ import annotations

import json
import re

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
URL = "https://www.halooglasi.com/nekretnine/prodaja-stanova/beograd"


def main() -> None:
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    html = resp.text

    # --- 1. The serverListData JSON blob ---
    match = re.search(
        r"QuidditaEnvironment\.serverListData=(.*?);\s*QuidditaEnvironment",
        html,
        re.DOTALL,
    )
    if match:
        try:
            data = json.loads(match.group(1))
            print("=== serverListData top-level keys ===")
            print(list(data.keys()))
            # Try common list-holding keys
            for key in ("AdvertSummaryList", "Ads", "Items", "Results"):
                if key in data and isinstance(data[key], list) and data[key]:
                    print(f"\n=== first item of data['{key}'] ===")
                    print(json.dumps(data[key][0], ensure_ascii=False, indent=2)[:2000])
                    break
        except Exception as exc:
            print("Failed to parse serverListData JSON:", exc)
            print("Raw snippet (first 1500 chars):")
            print(match.group(1)[:1500])
    else:
        print("serverListData blob not found with this regex.")

    # --- 2. One raw .my-product-placeholder card's HTML ---
    soup = BeautifulSoup(html, "html.parser")
    card = soup.select_one(".my-product-placeholder")
    print("\n=== first .my-product-placeholder card, raw HTML (trimmed) ===")
    if card:
        print(str(card)[:3000])
    else:
        print("No card found.")


if __name__ == "__main__":
    main()
