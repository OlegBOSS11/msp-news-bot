"""One-off diagnostic: does the 200 OK response from 4zida.rs / halooglasi.com
actually contain listing data, or is it a disguised JS-challenge page?

Run with: python3 diagnose_content.py

Safe to delete after diagnosis.
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def check_4zida() -> None:
    print("=== 4zida.rs ===")
    resp = requests.get(
        "https://www.4zida.rs/prodaja-stanova", headers=HEADERS, timeout=15
    )
    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.select(".oglas")
    print(f"  .oglas cards found (existing selector in real_estate.py): {len(cards)}")
    # Some sites moved to different markup over time; check a few common
    # alternatives too, just to see what's actually there.
    for sel in ["article", "[data-testid]", ".card", "li"]:
        print(f"  '{sel}' matches: {len(soup.select(sel))}")
    print(f"  <title>: {soup.title.string if soup.title else None}")


def check_halooglasi() -> None:
    print("=== halooglasi.com ===")
    resp = requests.get(
        "https://www.halooglasi.com/nekretnine/prodaja-stanova/beograd",
        headers=HEADERS,
        timeout=15,
    )
    has_quiddita = "QuidditaEnvironment.serverListData=" in resp.text
    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.select(".my-product-placeholder")
    print(f"  has QuidditaEnvironment.serverListData= JSON blob: {has_quiddita}")
    print(f"  .my-product-placeholder cards found: {len(cards)}")
    print(f"  <title>: {soup.title.string if soup.title else None}")


if __name__ == "__main__":
    check_4zida()
    print()
    check_halooglasi()
