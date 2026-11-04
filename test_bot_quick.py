"""Quick test to verify bot functionality."""

import asyncio
from unittest.mock import AsyncMock
import pytest


def test_bot_system_prompt():
    """Test that system prompt is properly configured."""
    from bot import get_system_prompt
    SYSTEM_PROMPT = get_system_prompt()

    assert len(SYSTEM_PROMPT) > 100
    assert "Сербию" in SYSTEM_PROMPT
    assert "релокац" in SYSTEM_PROMPT.lower()
    # The system prompt embeds the current date, e.g. "5 августа 2026 года".
    assert " года" in SYSTEM_PROMPT


def test_config():
    """Test that configuration is properly set up (Serbia, the reference country)."""
    import config

    whitelist = config.get_whitelist_domains("serbia")
    feeds = config.get_rss_feeds("serbia")
    keywords = config.get_all_keywords("serbia")

    assert len(whitelist) > 0
    assert len(feeds) > 0
    assert len(keywords) > 0

    serbian_domains = [d for d in whitelist if "rs" in d or "serbia" in d]
    assert len(serbian_domains) > 0, "Whitelist should include at least one Serbian domain"


def test_parser_keywords():
    """Test that parser scores keywords correctly (Serbia, the reference country)."""
    import config
    from parser import _score

    keywords = config.get_all_keywords("serbia")

    test_cases = [
        ("Работа в Сербии", True),
        ("ВНЖ в Сербии для русских", True),
        ("Аренда квартиры в Белграде", True),
        ("Школы для детей в Сербии", True),
        ("Погода в Москве", False),
        ("Спортные результаты", False),
    ]

    for title, expected_relevant in test_cases:
        score = _score({"title": title, "summary": ""}, keywords)
        relevant = score > 0
        assert relevant == expected_relevant, (
            f"'{title}': expected relevant={expected_relevant}, got score={score}"
        )


@pytest.mark.asyncio
async def test_bot_help():
    """Test that help command mentions Serbia."""
    from bot import cmd_help

    message = AsyncMock()
    message.answer = AsyncMock()

    await cmd_help(message)
    message.answer.assert_called_once()
    call_args = message.answer.call_args[0][0]

    assert "Сербии" in call_args
    assert "ВНЖ" in call_args
    assert "работа" in call_args.lower()


def test_welcome_text():
    """Test that welcome text is updated."""
    from bot import WELCOME_TEXT

    assert len(WELCOME_TEXT) > 0
    assert "Сербию" in WELCOME_TEXT
    assert "релокац" in WELCOME_TEXT.lower()


def main():
    """Run all tests standalone (without pytest) with progress output."""
    print("Serbia Relocation Bot - Quick Test")
    print("=" * 50)
    print()

    tests = [
        test_bot_system_prompt,
        test_config,
        test_parser_keywords,
        test_welcome_text,
    ]
    for test in tests:
        test()
        print(f"✓ {test.__name__}")

    asyncio.run(test_bot_help())
    print("✓ test_bot_help")

    print()
    print("=" * 50)
    print("All tests completed!")


if __name__ == "__main__":
    main()
