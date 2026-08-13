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

import re
from html import escape
from urllib.parse import urlparse

TELEGRAM_TEXT_LIMIT = 4096

_TAG_RE = re.compile(r"<[^>]+>")


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


def split_telegram_message(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Split `text` into <=limit-char chunks for sendMessage/answer_photo
    caption, without ever cutting inside an HTML tag.

    GigaChat answers and search-result-with-sources text have no fixed
    upper bound, but Telegram's text message limit is 4096 chars — sending
    a longer string as one message just fails outright. Naive slicing
    would happily cut through the middle of a `<a href="...">` built by
    telegram_link(), which either breaks the tag or (worse) sends the back
    half of an href/onclick-lookalike string as if it were plain text in
    the next message. Prefers breaking on a blank line, then a single
    newline, then a space, before falling back to a hard cut — and if
    that landing point is inside a tag, backs off to just before the tag
    starts instead.

    Chunks always concatenate back to the original text exactly (no
    content is dropped or duplicated).
    """
    n = len(text)
    if n <= limit:
        return [text]

    tag_spans = [m.span() for m in _TAG_RE.finditer(text)]

    def _tag_containing(pos: int) -> tuple[int, int] | None:
        for start, end in tag_spans:
            if start < pos < end:
                return start, end
        return None

    def _safe(pos: int) -> int:
        span = _tag_containing(pos)
        return span[0] if span else pos

    chunks: list[str] = []
    start = 0
    while n - start > limit:
        hard_end = start + limit
        window = text[start:hard_end]
        split_at = None
        for sep in ("\n\n", "\n", " "):
            idx = window.rfind(sep)
            # Require the break to leave a reasonably sized chunk, so a
            # stray early separator doesn't produce a near-empty message.
            if idx > limit // 4:
                split_at = start + idx + len(sep)
                break
        if split_at is None:
            split_at = hard_end
        split_at = _safe(split_at)
        if split_at <= start:
            # Nothing safe to break on before the limit (e.g. one huge
            # tag) — hard cut, still tag-safe.
            split_at = _safe(hard_end)
            if split_at <= start:
                split_at = hard_end  # last resort; shouldn't happen in practice
        chunks.append(text[start:split_at])
        start = split_at
    if start < n:
        chunks.append(text[start:])
    return chunks
