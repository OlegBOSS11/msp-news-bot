"""Translation module for Serbian news to Russian."""

from __future__ import annotations

import asyncio
import logging

from deep_translator import GoogleTranslator
from deep_translator.exceptions import BaseError as TranslationError

logger = logging.getLogger(__name__)


def _translate_sync(text: str) -> str:
    """Blocking call to Google Translate (via deep-translator). Run in a thread."""
    return GoogleTranslator(source="auto", target="ru").translate(text)


async def translate_to_russian(text: str) -> str:
    """Translate text from Serbian to Russian using a free translation service."""
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
        # GoogleTranslator.translate() makes a blocking HTTP request —
        # run it off the event loop so it doesn't stall the whole bot.
        translated = await asyncio.to_thread(_translate_sync, text)

        # If translation is empty or error, return original
        if not translated or len(translated) < len(text) * 0.3:
            return text

        return translated

    except TranslationError as exc:
        logger.error("Translation error: %s", exc)
        return text
    except Exception as exc:
        logger.error("Unexpected translation error: %s", exc)
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
