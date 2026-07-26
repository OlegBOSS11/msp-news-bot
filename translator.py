"""Translation module for Serbian news to Russian."""

from __future__ import annotations

import logging

from gigachat_client import get_gigachat_client

logger = logging.getLogger(__name__)

# System prompt for translation
TRANSLATION_PROMPT = """Ты — профессиональный переводчик с сербского на русский язык.

Переводи новости и тексты с сербского (кириллица и латиница) на русский язык.

Правила перевода:
1. Переводи точно, сохраняя смысл оригинала
2. Используй естественный русский язык
3. Сохраняй имена собственные, названия организаций
4. Если текст уже на русском — верни его как есть
5. Кратко и по существу

Формат ответа: только переведенный текст, без пояснений."""


async def translate_to_russian(text: str) -> str:
    """Translate text from Serbian to Russian using GigaChat."""
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
        from gigachat.models.chat import Chat, Messages

        giga = get_gigachat_client()
        if not giga:
            logger.error("GigaChat client not available")
            return text

        messages = [
            Messages(role="system", content=TRANSLATION_PROMPT),
            Messages(role="user", content=f"Переведи на русский:\n\n{text}"),
        ]

        chat = Chat(model="GigaChat", messages=messages)
        response = await giga.achat(chat)
        translated = response.choices[0].message.content

        # If translation is empty or error, return original
        if not translated or len(translated) < len(text) * 0.3:
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
