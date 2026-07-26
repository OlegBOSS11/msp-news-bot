"""Quick test to verify bot functionality."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


def test_bot_system_prompt():
    """Test that system prompt is properly configured."""
    from bot import get_system_prompt
    SYSTEM_PROMPT = get_system_prompt()

    print("=== System Prompt Test ===")
    print(f"Length: {len(SYSTEM_PROMPT)} characters")
    print(f"Contains 'Сербию': {'Сербию' in SYSTEM_PROMPT}")
    print(f"Contains 'релокац': {'релокац' in SYSTEM_PROMPT.lower()}")
    print(f"Contains current date: {'2026' in SYSTEM_PROMPT}")
    print(f"Contains 'июля 2026': {'июля 2026' in SYSTEM_PROMPT}")
    print()


def test_config():
    """Test that configuration is properly set up."""
    from config import WHITELIST_DOMAINS, RSS_FEEDS, MSP_KEYWORDS

    print("=== Configuration Test ===")
    print(f"Whitelist domains: {len(WHITELIST_DOMAINS)}")
    print(f"RSS feeds: {len(RSS_FEEDS)}")
    print(f"Keywords: {len(MSP_KEYWORDS)}")

    # Check Serbian sources
    serbian_domains = [d for d in WHITELIST_DOMAINS if "rs" in d or "serbia" in d]
    print(f"Serbian domains: {len(serbian_domains)}")
    for d in serbian_domains:
        print(f"  - {d}")
    print()


def test_parser_keywords():
    """Test that parser scores keywords correctly."""
    from parser import _score

    print("=== Parser Keyword Test ===")

    test_cases = [
        ("Работа в Сербии", True),
        ("ВНЖ в Сербии для русских", True),
        ("Аренда квартиры в Белграде", True),
        ("Школы для детей в Сербии", True),
        ("Погода в Москве", False),
        ("Спортные результаты", False),
    ]

    for title, expected_relevant in test_cases:
        score = _score({"title": title, "summary": ""})
        relevant = score > 0
        status = "✓" if relevant == expected_relevant else "✗"
        print(f"{status} '{title}': score={score}, relevant={relevant}")
    print()


def test_bot_help():
    """Test that help command mentions Serbia."""
    from bot import cmd_help

    print("=== Bot Help Test ===")

    message = AsyncMock()
    message.answer = AsyncMock()

    asyncio.get_event_loop().run_until_complete(cmd_help(message))
    message.answer.assert_called_once()
    call_args = message.answer.call_args[0][0]

    print(f"Help mentions 'Сербии': {'Сербии' in call_args}")
    print(f"Help mentions 'ВНЖ': {'ВНЖ' in call_args}")
    print(f"Help mentions 'работа': {'работа' in call_args.lower()}")
    print()


def test_welcome_text():
    """Test that welcome text is updated."""
    from bot import WELCOME_TEXT

    print("=== Welcome Text Test ===")
    print(f"Length: {len(WELCOME_TEXT)} characters")
    print(f"Mentions 'Сербию': {'Сербию' in WELCOME_TEXT}")
    print(f"Mentions 'релокац': {'релокац' in WELCOME_TEXT.lower()}")
    print(f"Mentions 'июля 2026': {'июля 2026' in WELCOME_TEXT}")
    print()


def main():
    """Run all tests."""
    print("Serbia Relocation Bot - Quick Test")
    print("=" * 50)
    print()

    test_bot_system_prompt()
    test_config()
    test_parser_keywords()
    test_bot_help()
    test_welcome_text()

    print("=" * 50)
    print("All tests completed!")


if __name__ == "__main__":
    main()
