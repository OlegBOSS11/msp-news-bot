"""Web search module for Serbian information with source links."""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from urllib.parse import quote_plus

import aiohttp
from bs4 import BeautifulSoup

from gigachat_client import get_gigachat_client
from telegram_format import telegram_link, telegram_text

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Represents a search result with title, URL, and snippet."""
    title: str
    url: str
    snippet: str = ""


async def search_web(query: str, num_results: int = 5) -> list[SearchResult]:
    """Search the web using DuckDuckGo and return results with links."""
    results = []

    try:
        # Use DuckDuckGo HTML search
        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        # Force IPv4 — see the comment in parser.collect_news() for why.
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(search_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")

                    # Parse search results
                    for result in soup.select(".result")[:num_results]:
                        title_el = result.select_one(".result__title")
                        url_el = result.select_one(".result__url")
                        snippet_el = result.select_one(".result__snippet")

                        if title_el and url_el:
                            title = title_el.get_text(strip=True)
                            url = url_el.get_text(strip=True)
                            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                            # .result__url's *text* is a display URL, which
                            # DuckDuckGo renders without a scheme
                            # ("n1info.rs/vesti/nesto"). telegram_url()
                            # rightly rejects schemeless values, so without
                            # this every source would be rendered as plain
                            # text instead of a link.
                            if url.startswith("//"):
                                url = "https:" + url
                            elif not url.startswith(("http://", "https://")):
                                url = "https://" + url

                            results.append(SearchResult(
                                title=title,
                                url=url,
                                snippet=snippet,
                            ))
    except Exception as exc:
        logger.error("Web search error: %s", exc)

    return results


async def search_serbia_info(query: str) -> tuple[str, list[SearchResult]]:
    """Search for information about Serbia and return answer with sources."""
    # Add "Serbia" or "Сербия" to the query if not already present
    query_lower = query.lower()
    if "сербия" not in query_lower and "serbia" not in query_lower and "сербск" not in query_lower:
        query = f"{query} Сербия"

    # Search for information
    search_results = await search_web(query, num_results=5)

    # Build context for GigaChat
    context_parts = []
    sources = []

    for i, result in enumerate(search_results, 1):
        context_parts.append(f"[{i}] {result.title}\nURL: {result.url}\n{result.snippet}")
        sources.append(result)

    context = "\n\n".join(context_parts) if context_parts else "Информация не найдена."

    return context, sources


def format_answer_with_sources(answer: str, sources: list[SearchResult]) -> str:
    """Format the answer with source links.

    Both the model's answer text and every source title/URL are
    external/untrusted input (search results, LLM output) — escaped here
    since this string is sent with parse_mode="HTML".
    """
    if not sources:
        return telegram_text(answer)

    # Add sources section
    sources_text = "\n\n📚 <b>Источники:</b>\n"
    for i, source in enumerate(sources, 1):
        # Truncate title if too long
        title = source.title[:80] + "..." if len(source.title) > 80 else source.title
        sources_text += f"{i}. {telegram_link(source.url, title)}\n"

    return telegram_text(answer) + sources_text


# System prompt for GigaChat with context
SERBIA_EXPERT_PROMPT = """Ты — эксперт по Сербии, который помогает людям, рассматривающим переезд из России в Сербию.

Ты получаешь контекст из интернет-поиска с актуальной информацией и ссылками на источники.

ВАЖНЫЕ ПРАВИЛА:
1. Используй информацию из предоставленного контекста для ответа
2. Если информации нет в контексте, честно скажи об этом
3. Всегда указывай источники в формате [номер] в тексте ответа
4. Отвечай кратко, по делу, на русском языке
5. Если информации нет или она устаревшая, скажи об этом

Ты специализируешься на информации для русскоязычных людей, планирующих переезд."""


async def get_serbia_answer(query: str) -> str:
    """Get an answer about Serbia with source links."""
    # Search for information
    context, sources = await search_serbia_info(query)

    if not sources:
        return "К сожалению, не удалось найти актуальную информацию по вашему запросу. Попробуйте переформулировать вопрос."

    # Prepare messages for GigaChat
    from gigachat.models.chat import Messages

    messages = [
        Messages(role="system", content=SERBIA_EXPERT_PROMPT),
        Messages(role="user", content=f"Контекст из интернета:\n\n{context}\n\nВопрос пользователя: {query}\n\nОтветь на вопрос, используя информацию из контекста. Указывай источники в формате [номер] в тексте ответа."),
    ]

    try:
        from gigachat.models.chat import Chat

        giga = get_gigachat_client()
        if not giga:
            logger.error("GigaChat client not available")
            # Fallback: return search results without AI processing
            return format_answer_with_sources(
                "Не удалось получить ответ от ИИ. Вот найденные источники:",
                sources,
            )

        chat = Chat(model="GigaChat", messages=messages)
        response = await giga.achat(chat)
        answer = response.choices[0].message.content

        # Format answer with source links
        return format_answer_with_sources(answer, sources)

    except Exception as exc:
        logger.error("GigaChat error: %s", exc)
        # Fallback: return search results without AI processing
        return format_answer_with_sources(
            "Не удалось получить ответ от ИИ. Вот найденные источники:",
            sources,
        )
