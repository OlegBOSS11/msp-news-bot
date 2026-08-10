"""One-off diagnostic: are 4zida.rs / halooglasi.com reachable via plain
requests (like cityexpert.rs and nekretnine.rs turned out NOT to be)?

Run with: python3 diagnose_other_sites.py

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

SITES = {
    "4zida.rs": "https://www.4zida.rs/prodaja-stanova",
    "halooglasi.com": "https://www.halooglasi.com/nekretnine/prodaja-stanova/beograd",
}


def main() -> None:
    for name, url in SITES.items():
        print(f"=== {name} ===")
        for i in range(3):
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
                print(
                    f"  try {i}: STATUS {resp.status_code}, "
                    f"body length {len(resp.text)}, "
                    f"server header: {resp.headers.get('server')}"
                )
            except Exception as exc:
                print(f"  try {i}: EXCEPTION {type(exc).__name__}: {exc!r}")
        print()


if __name__ == "__main__":
    main()
