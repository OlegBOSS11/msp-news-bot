"""Shared helpers for safely building Telegram HTML-parse-mode messages.

Telegram's HTML parse mode is not real HTML — it accepts a small fixed tag
subset and treats any other stray '<', '>' or '&' as a broken tag/entity,
which makes the whole sendMessage/sendPhoto call fail (or, worse, lets a
crafted value forge a fake link). Any value that comes from outside the
bot's own hardcoded strings — RSS titles/summaries, scraped listing
fields, GigaChat/model output, transcribed voice text, third-party URLs —
MUST go through telegram_text() / telegram_url() / telegram_link() before
being embedded in a message sent with parse_mode="HTML".
"""

from __future__ import annotations

from html import escape
from urllib.parse import urlparse


def telegram_text(value: object) -> str:
    """Escape a value for safe inclusion as HTML text content."""
    return escape(str(value), quote=False)


def telegram_url(value: str) -> str:
    """Escape a value for safe inclusion as an href attribute.

    Rejects anything that isn't a plain http(s) URL — a scraped/RSS field
    could otherwise contain a javascript: URL or similar, and an <a href>
    built from unescaped input could break out of the attribute entirely.
    Raises ValueError for anything that doesn't look like a real link;
    callers should fall back to plain (escaped) text in that case.
    """
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid HTTP URL: {value!r}")
    return escape(value, quote=True)


def telegram_link(url: str, text: object) -> str:
    """Build a safe <a href="...">text</a>, or just escaped text if the
    URL isn't a valid http(s) link — never omit the label entirely."""
    try:
        href = telegram_url(url)
    except ValueError:
        return telegram_text(text)
    return f'<a href="{href}">{telegram_text(text)}</a>'
