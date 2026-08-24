"""Translation module for Serbian news to Russian.

Was deep-translator's GoogleTranslator (scrapes translate.google.com) —
switched to GigaChat. That scraper turned out to be unreliably blocked for
this server's IP: Google would return its own error page with an outer
HTTP 200 (so deep-translator's status-code check never caught it) and the
page's own "Error 500 (Server Error)!! ...That's all we know." boilerplate
got returned as if it were the translation — seen verbatim in production.
A polite delay between requests didn't help (checked days later, still
failing for most items), meaning it's a standing block, not a rate limit.
GigaChat is already a hard dependency of this bot (used for every Q&A
answer) and reachable reliably from this server, so it has no separate
failure mode to work around, and drops deep-translator (with its own
unresolved supply-chain advisory, see CONTRIBUTING.md history) entirely.
"""

from __future__ import annotations

import logging

from gigachat.models.chat import Chat, Messages

from gigachat_client import get_gigachat_client

logger = logging.getLogger(__name__)

_TRANSLATE_SYSTEM_PROMPT = (
    "Ты профессиональный переводчик с сербского на русский язык. "
    "Переведи текст пользователя на русский. Ответь ТОЛЬКО переводом, "
    "без кавычек, пояснений, комментариев или каких-либо добавлений. "
    "Сохрани исходный смысл и стиль (заголовок остаётся заголовком, "
    "обычный текст — обычным текстом). Если текст уже на русском — "
    "верни его без изменений."
)


async def translate_to_russian(text: str) -> str:
    """Translate text from Serbian to Russian, via GigaChat."""
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

    try:
        giga = get_gigachat_client()
        if not giga:
            logger.warning("GigaChat client not available, skipping translation")
            return text

        chat = Chat(model="GigaChat", messages=[
            Messages(role="system", content=_TRANSLATE_SYSTEM_PROMPT),
            Messages(role="user", content=text),
        ])
        response = await giga.achat(chat)
        translated = (response.choices[0].message.content or "").strip()

        # If translation is empty or suspiciously short, return original
        if not translated or len(translated) < len(text) * 0.3:
            return text

        # A genuine translation *into Russian* should be predominantly
        # Cyrillic. Defense in depth against the model wandering off
        # (e.g. answering *about* the text instead of just translating
        # it) rather than a specific failure mode like the old scraper's.
        translated_alpha = sum(1 for c in translated if c.isalpha())
        translated_cyrillic = sum(1 for c in translated if '\u0400' <= c <= '\u04FF')
        if translated_alpha > 0 and translated_cyrillic / translated_alpha < 0.5:
            logger.warning(
                "Translation result doesn't look like Russian, discarding: %r",
                translated[:100],
            )
            return text

        return translated

    except Exception as exc:
        logger.error("Translation error: %s", exc)
        return text


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
