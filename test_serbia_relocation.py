"""Tests for Serbia Relocation Bot."""

import pytest
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path


# --- Fixtures ---

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary test database."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE sent_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT NOT NULL UNIQUE,
            title TEXT,
            sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def mock_env():
    """Mock environment variables."""
    with patch.dict("os.environ", {
        "BOT_TOKEN": "test:token",
        "CHAT_ID": "123456",
        "GIGACHAT_CREDENTIALS": "test:cred",
    }):
        yield


# --- Test 1: System prompt content ---

class TestSystemPrompt:
    """Test that system prompt is properly configured for Serbia relocation."""

    def test_system_prompt_exists(self):
        """System prompt should exist and be non-empty."""
        from bot import get_system_prompt
        SYSTEM_PROMPT = get_system_prompt()
        assert SYSTEM_PROMPT
        assert len(SYSTEM_PROMPT) > 100

    def test_system_prompt_mentions_serbia(self):
        """System prompt should mention Serbia."""
        from bot import get_system_prompt
        SYSTEM_PROMPT = get_system_prompt()
        assert "Сербия" in SYSTEM_PROMPT or "серб" in SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_relocation(self):
        """System prompt should mention relocation."""
        from bot import get_system_prompt
        SYSTEM_PROMPT = get_system_prompt()
        assert "релокац" in SYSTEM_PROMPT.lower() or "переезд" in SYSTEM_PROMPT.lower()

    def test_system_prompt_july_2026(self):
        """System prompt should specify July 2026 as minimum date."""
        from bot import get_system_prompt
        SYSTEM_PROMPT = get_system_prompt()
        assert "июля 2026" in SYSTEM_PROMPT or "2026" in SYSTEM_PROMPT

    def test_system_prompt_topics(self):
        """System prompt should cover key relocation topics."""
        from bot import get_system_prompt
        SYSTEM_PROMPT = get_system_prompt()
        topics = ["работа", "недвижим", "образован", "виза", "документ"]
        found = [t for t in topics if t in SYSTEM_PROMPT.lower()]
        assert len(found) >= 3, f"Expected at least 3 topics, found: {found}"


# --- Test 2: Configuration ---

class TestConfiguration:
    """Test that configuration is properly set up for Serbia."""

    def test_serbian_sources_in_whitelist(self):
        """Whitelist should include Serbian sources."""
        import config
        whitelist = config.get_whitelist_domains("serbia")
        serbian_domains = ["n1info.rs", "b92.net", "blic.rs"]
        for domain in serbian_domains:
            assert domain in whitelist, f"Missing Serbian domain: {domain}"

    def test_serbian_rss_feeds(self):
        """RSS feeds should include Serbian sources."""
        import config
        feeds = config.get_rss_feeds("serbia")
        serbian_feeds = [f for f in feeds if "rs" in f["name"] or "serbia" in f["name"]]
        assert len(serbian_feeds) >= 2, "Should have at least 2 Serbian RSS feeds"

    def test_relocation_keywords(self):
        """Keywords should include relocation-related terms."""
        import config
        keywords = config.get_all_keywords("serbia")
        relocation_keywords = ["сербия", "релокац", "переезд", "виза", "внж"]
        found = [k for k in relocation_keywords if any(k in kw for kw in keywords)]
        assert len(found) >= 3, f"Expected at least 3 relocation keywords, found: {found}"


# --- Test 3: Parser functionality ---

class TestParser:
    """Test that parser works with new configuration."""

    @pytest.mark.asyncio
    async def test_collect_news_with_mock(self):
        """collect_news should work with mocked feeds."""
        from parser import collect_news

        with patch("parser._fetch_feed") as mock_fetch:
            mock_fetch.return_value = [
                {
                    "title": "Serbia news",
                    "link": "https://n1info.rs/test",
                    "summary": "Test summary",
                    "score": 1,
                    "source": "n1info.rs",
                }
            ]
            with patch("config.get_rss_feeds", return_value=[
                {"name": "n1info.rs", "url": "https://test1"},
            ]):
                news = await collect_news("serbia")
                assert len(news) >= 1
                assert news[0]["source"] == "n1info.rs"

    @pytest.mark.asyncio
    async def test_keyword_scoring(self):
        """Parser should score entries based on relocation keywords."""
        import config
        from parser import _score

        keywords = config.get_all_keywords("serbia")

        # Test entry with Serbian keywords
        entry_serbian = {
            "title": "Работа в Сербии для русских",
            "summary": "Новые возможности для релокации",
        }
        score_serbian = _score(entry_serbian, keywords)
        assert score_serbian > 0, "Serbian keywords should produce positive score"

        # Test entry without relevant keywords (using words not in keyword list)
        entry_irrelevant = {
            "title": "Погода в Москве",
            "summary": "Прогноз на завтра",
        }
        score_irrelevant = _score(entry_irrelevant, keywords)
        assert score_irrelevant == 0, "Irrelevant keywords should produce zero score"


# --- Test 4: Bot responses ---

class TestBotResponses:
    """Test that bot responds correctly to user queries."""

    @pytest.mark.asyncio
    async def test_help_command(self):
        """Help command should mention Serbia relocation."""
        from bot import cmd_help

        message = AsyncMock()
        message.answer = AsyncMock()

        await cmd_help(message)
        message.answer.assert_called_once()
        call_args = message.answer.call_args[0][0]
        assert "Сербия" in call_args or "серб" in call_args.lower()

    @pytest.mark.asyncio
    async def test_welcome_text(self):
        """Welcome text should mention Serbia relocation."""
        from bot import WELCOME_TEXT
        assert "Сербия" in WELCOME_TEXT or "серб" in WELCOME_TEXT.lower()
        assert "релокац" in WELCOME_TEXT.lower() or "переезд" in WELCOME_TEXT.lower()


# --- Test 5: RSS feed resilience ---

class TestRSSResilience:
    """Test that bot survives RSS feed failures."""

    @pytest.mark.asyncio
    async def test_one_feed_down_others_work(self):
        """Bot should collect news even if one feed is down."""
        from parser import collect_news

        with patch("parser._fetch_feed") as mock_fetch:
            # First feed fails, others return data
            mock_fetch.side_effect = [
                Exception("Connection timeout"),
                [{"title": "Test news", "link": "https://n1info.rs/test",
                  "summary": "Test", "score": 1, "source": "n1info.rs"}],
                [{"title": "Test news 2", "link": "https://blic.rs/test",
                  "summary": "Test 2", "score": 1, "source": "blic.rs"}],
            ]
            with patch("config.get_rss_feeds", return_value=[
                {"name": "failed.rs", "url": "https://test1"},
                {"name": "n1info.rs", "url": "https://test2"},
                {"name": "blic.rs", "url": "https://test3"},
            ]):
                news = await collect_news("serbia")
                assert len(news) == 2

    @pytest.mark.asyncio
    async def test_all_feeds_down(self):
        """Bot should return empty list, not crash."""
        from parser import collect_news

        with patch("parser._fetch_feed", side_effect=Exception("Network error")):
            with patch("config.get_rss_feeds", return_value=[
                {"name": "test1", "url": "https://test1"},
                {"name": "test2", "url": "https://test2"},
            ]):
                news = await collect_news("serbia")
                assert news == []


# --- Test 6: Database resilience ---

class TestDatabaseResilience:
    """Test that bot survives database failures."""

    @pytest.mark.asyncio
    async def test_is_sent_db_locked(self):
        """is_sent should handle locked database."""
        import database
        with patch("database.DB_PATH", Path("/nonexistent/db.sqlite")):
            result = await database.is_sent("https://test.com")
            assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_mark_sent_db_locked(self):
        """mark_sent should handle locked database."""
        import database
        with patch("database.DB_PATH", Path("/nonexistent/db.sqlite")):
            await database.mark_sent("https://test.com", "Test")


# --- Test 7: GigaChat resilience ---

class TestGigaChatResilience:
    """Test that bot survives GigaChat API failures."""

    @pytest.mark.asyncio
    async def test_gigachat_timeout(self):
        """Bot should handle GigaChat timeout."""
        mock_giga = MagicMock()
        mock_giga.achat = AsyncMock(side_effect=Exception("Timeout"))

        # get_gigachat_client() (not a module-level `bot.giga`) is what bot.py
        # actually calls — patch that instead.
        with patch("bot.get_gigachat_client", return_value=mock_giga):
            from bot import handle_question
            message = AsyncMock()
            # Deliberately generic text: a relocation-flavored question
            # would route through get_country_answer()'s web-search branch
            # instead of the plain-GigaChat branch this test targets.
            message.text = "Test question"
            message.from_user.id = 123
            message.chat.id = 123
            message.answer = AsyncMock()

            with patch("bot.bot.send_chat_action", new_callable=AsyncMock):
                await handle_question(message)
                message.answer.assert_called()
                call_args = message.answer.call_args[0][0]
                assert "Ошибка" in call_args


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
