"""Translation module for Serbian news to Russian.

History: started as deep-translator's GoogleTranslator (scrapes
translate.google.com) — dropped after Google started returning its own
error page with an outer HTTP 200 (so the scraper's status-code check
never caught it) as if it were the translation, verbatim, in production.
Switched to GigaChat, which worked but costs GigaChat quota for every
short title/summary. Now: Ollama Cloud's free tier (OpenAI-compatible
API, no cost while the free quota lasts) as the primary path, falling
back to GigaChat automatically if OLLAMA_API_KEY isn't set or a call
fails — translation never breaks outright, it just stops being free for
that one call.
"""

from __future__ import annotations

import logging
import socket

import aiohttp
from gigachat.models.chat import Chat, Messages

import config
from gigachat_client import get_gigachat_client

logger = logging.getLogger(__name__)

OLLAMA_CHAT_URL = "https://ollama.com/v1/chat/completions"

_TRANSLATE_SYSTEM_PROMPT = (
    "Ты профессиональный переводчик с сербского на русский язык. "
    "Переведи текст пользователя на русский. Ответь ТОЛЬКО переводом, "
    "без кавычек, пояснений, комментариев или каких-либо добавлений. "
    "Сохрани исходный смысл и стиль (заголовок остаётся заголовком, "
    "обычный текст — обычным текстом). Если текст уже на русском — "
    "верни его без изменений."
)


async def _translate_via_ollama_cloud(text: str) -> str | None:
    """One call to Ollama Cloud's free tier. Returns None on any failure
    (missing key, network error, non-200, malformed response) — the
    caller falls back to GigaChat in that case."""
    if not config.OLLAMA_API_KEY:
        return None

    headers = {
        "Authorization": f"Bearer {config.OLLAMA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": _TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    try:
        # Force IPv4 — see the comment in parser.collect_news() for why.
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                OLLAMA_CHAT_URL, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        "Ollama Cloud HTTP %s, falling back to GigaChat: %s",
                        resp.status, body[:300],
                    )
                    return None
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("Ollama Cloud request failed, falling back to GigaChat: %s", exc)
        return None


async def _translate_via_gigachat(text: str) -> str | None:
    """Fallback path — same GigaChat client used for the bot's Q&A."""
    try:
        giga = get_gigachat_client()
        if not giga:
            logger.warning("GigaChat client not available, skipping translation")
            return None

        chat = Chat(model="GigaChat", messages=[
            Messages(role="system", content=_TRANSLATE_SYSTEM_PROMPT),
            Messages(role="user", content=text),
        ])
        response = await giga.achat(chat)
        return response.choices[0].message.content
    except Exception as exc:
        logger.error("GigaChat translation error: %s", exc)
        return None


async def translate_to_russian(text: str) -> str:
    """Translate text from Serbian to Russian — Ollama Cloud (free) first,
    GigaChat as a fallback if that's unavailable or fails."""
    if not text:
        return text

    # Check if text is already in Russian (simple heuristic)
    russian_chars = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
    latin_chars = sum(1 for c in text if 'a' <= c.lower() <= 'z')
    total_alpha = sum(1 for c in text if c.isalpha())

    if total_alpha > 0:
        russian_ratio = russian_chars / total_alpha
        latin_ratio = latin_chars / total_alpha

        # If mostly Russian, return as is
        if russian_ratio > 0.7:
            return text

        # If mostly Latin (possibly Serbian Latin), translate
        if latin_ratio > 0.3 or russian_ratio < 0.3:
            pass  # Continue to translation
        else:
            return text

    translated = await _translate_via_ollama_cloud(text)
    if translated is None:
        translated = await _translate_via_gigachat(text)
    if translated is None:
        return text

    translated = translated.strip()

    # If translation is empty or suspiciously short, return original
    if not translated or len(translated) < len(text) * 0.3:
        return text

    # A genuine translation *into Russian* should be predominantly
    # Cyrillic. Defense in depth against either provider wandering off
    # format (e.g. answering *about* the text instead of translating it)
    # or returning something else broken — not tied to a specific
    # failure mode of either provider.
    translated_alpha = sum(1 for c in translated if c.isalpha())
    translated_cyrillic = sum(1 for c in translated if '\u0400' <= c <= '\u04FF')
    if translated_alpha > 0 and translated_cyrillic / translated_alpha < 0.5:
        logger.warning(
            "Translation result doesn't look like Russian, discarding: %r",
            translated[:100],
        )
        return text

    return translated


def detect_language(text: str) -> str:
    """Detect if text is in Russian or Serbian."""
    if not text:
        return "unknown"

    russian_chars = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
    latin_chars = sum(1 for c in text if 'a' <= c.lower() <= 'z')
    total_alpha = sum(1 for c in text if c.isalpha())

    if total_alpha == 0:
        return "unknown"

    russian_ratio = russian_chars / total_alpha
    latin_ratio = latin_chars / total_alpha

    if russian_ratio > 0.5:
        return "russian"
    elif latin_ratio > 0.3:
        return "serbian"
    else:
        return "unknown"
