"""One-off diagnostic: do halooglasi.com's per-city listing URLs exist for
Novi Sad, Nis, and Kragujevac (the other cities the bot already recognizes
in its keyword lists)?

Run with: python3 diagnose_cities.py

Safe to delete after diagnosis.
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

CITIES = {
    "Novi Sad": "novi-sad",
    "Nis": "nis",
    "Kragujevac": "kragujevac",
}


def main() -> None:
    for name, slug in CITIES.items():
        url = f"https://www.halooglasi.com/nekretnine/prodaja-stanova/{slug}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".my-product-placeholder")
            print(
                f"{name} ({url}): STATUS {resp.status_code}, "
                f"cards found: {len(cards)}, "
                f"<title>: {soup.title.string if soup.title else None}"
            )
        except Exception as exc:
            print(f"{name} ({url}): EXCEPTION {type(exc).__name__}: {exc!r}")


if __name__ == "__main__":
    main()
