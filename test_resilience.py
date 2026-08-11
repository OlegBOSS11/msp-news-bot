"""Fault tolerance tests for MSP News Bot."""

import asyncio
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


# --- Test 1: RSS feed failures ---

class TestRSSResilience:
    """Test that bot survives RSS feed failures."""

    @pytest.mark.asyncio
    async def test_one_feed_down_others_work(self):
        """Bot should collect news even if one feed is down."""
        from parser import collect_news

        with patch("parser._fetch_feed") as mock_fetch:
            # First feed fails, others return data.
            # Russian-source items must mention Serbia to survive collect_news()'s
            # relocation-relevance filter (Serbian-domain sources pass unfiltered).
            mock_fetch.side_effect = [
                Exception("Connection timeout"),
                [{"title": "Новости Сербии", "link": "https://kommersant.ru/test",
                  "summary": "Релокация в Сербию", "score": 1, "source": "kommersant.ru"}],
                [{"title": "Новости Сербии 2", "link": "https://vedomosti.ru/test",
                  "summary": "Релокация в Сербию", "score": 1, "source": "vedomosti.ru"}],
            ]
            with patch("parser.RSS_FEEDS", [
                {"name": "garant.ru", "url": "https://test1"},
                {"name": "kommersant.ru", "url": "https://test2"},
                {"name": "vedomosti.ru", "url": "https://test3"},
            ]):
                news = await collect_news()
                assert len(news) == 2

    @pytest.mark.asyncio
    async def test_all_feeds_down(self):
        """Bot should return empty list, not crash."""
        from parser import collect_news

        with patch("parser._fetch_feed", side_effect=Exception("Network error")):
            with patch("parser.RSS_FEEDS", [
                {"name": "test1", "url": "https://test1"},
                {"name": "test2", "url": "https://test2"},
            ]):
                news = await collect_news()
                assert news == []

    @pytest.mark.asyncio
    async def test_feed_returns_empty(self):
        """Bot should handle empty RSS feed gracefully."""
        from parser import collect_news

        with patch("parser._fetch_feed", return_value=[]):
            with patch("parser.RSS_FEEDS", [{"name": "test", "url": "https://test"}]):
                news = await collect_news()
                assert news == []


# --- Test 2: Database failures ---

class TestDatabaseResilience:
    """Test that bot survives database failures."""

    @pytest.mark.asyncio
    async def test_is_sent_db_locked(self):
        """is_sent should handle locked database."""
        import database
        with patch("database.DB_PATH", Path("/nonexistent/db.sqlite")):
            result = await database.is_sent("https://test.com")
            # Should not crash, return False or handle error
            assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_mark_sent_db_locked(self):
        """mark_sent should handle locked database."""
        import database
        with patch("database.DB_PATH", Path("/nonexistent/db.sqlite")):
            # Should not raise
            await database.mark_sent("https://test.com", "Test")

    @pytest.mark.asyncio
    async def test_corrupted_db(self):
        """Bot should handle corrupted database."""
        import database
        with patch("database.DB_PATH", Path("/dev/null")):
            stats = await database.get_stats()
            # Should return default values
            assert "total" in stats


# --- Test 3: GigaChat failures ---

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
            # Deliberately generic text so it goes through the plain-GigaChat
            # branch of handle_question rather than the real-estate/Serbia
            # web-search branches.
            message.text = "Test question"
            message.from_user.id = 123
            message.chat.id = 123
            message.answer = AsyncMock()

            with patch("bot.bot.send_chat_action", new_callable=AsyncMock):
                await handle_question(message)
                # Should send error message, not crash
                message.answer.assert_called()
                call_args = message.answer.call_args[0][0]
                assert "Ошибка" in call_args

    @pytest.mark.asyncio
    async def test_gigachat_invalid_response(self):
        """Bot should handle invalid GigaChat response."""
        mock_response = MagicMock()
        mock_response.choices = []
        mock_giga = MagicMock()
        mock_giga.achat = AsyncMock(return_value=mock_response)

        with patch("bot.get_gigachat_client", return_value=mock_giga):
            from bot import handle_question
            message = AsyncMock()
            message.text = "Test"
            message.from_user.id = 123
            message.chat.id = 123
            message.answer = AsyncMock()

            with patch("bot.bot.send_chat_action", new_callable=AsyncMock):
                await handle_question(message)
                message.answer.assert_called()


# --- Test 4: Telegram API failures ---

class TestTelegramResilience:
    """Test that bot survives Telegram API failures."""

    @pytest.mark.asyncio
    async def test_send_message_fails(self):
        """Bot should keep broadcasting to other subscribers if one send fails."""
        from bot import send_daily_digest

        # bot.py does `from parser import collect_news`, so the name to patch
        # is bot.collect_news (where it's looked up), not parser.collect_news
        # (where it's defined) — otherwise this would hit the real network.
        with patch("bot.collect_news", return_value=[
            {"title": "News 1", "link": "https://test1.com",
             "summary": "Summary 1", "score": 1, "source": "test"},
            {"title": "News 2", "link": "https://test2.com",
             "summary": "Summary 2", "score": 1, "source": "test"},
        ]):
            with patch("database.is_sent", return_value=False):
                with patch("database.mark_sent", new_callable=AsyncMock):
                    # Two subscribers, so the broadcast loop actually has
                    # something to iterate over.
                    with patch("database.get_all_user_ids", return_value=[111, 222]):
                        with patch("database.get_disabled_topics", return_value=set()):
                            call_count = 0

                            async def failing_send(**kwargs):
                                nonlocal call_count
                                call_count += 1
                                if call_count == 1:
                                    raise Exception("Telegram API error")

                            with patch("bot.bot.send_message", side_effect=failing_send):
                                # Should not crash. Subscriber 1's digest
                                # header send fails (so nothing else goes
                                # out to them), but subscriber 2 still gets
                                # their full digest: 1 header + 1 message
                                # per news item (2 items, no images in this
                                # test) = 3 sends. Total = 1 + 3 = 4.
                                await send_daily_digest()
                                assert call_count == 4


# --- Test 5: Voice transcription failures ---

class TestVoiceResilience:
    """Test that bot survives voice transcription failures."""

    @pytest.mark.asyncio
    async def test_voice_file_corrupted(self):
        """Bot should handle corrupted voice file."""
        from bot import transcribe_voice

        with patch("bot.bot.get_file", side_effect=Exception("File not found")):
            result = await transcribe_voice("invalid_file_id")
            assert result is None

    @pytest.mark.asyncio
    async def test_ffmpeg_not_installed(self):
        """Bot should handle missing ffmpeg."""
        from bot import transcribe_voice

        with patch("bot.bot.get_file") as mock_file:
            mock_file.return_value.file_path = "/voice/file.ogg"
            with patch("bot.bot.download_file", new_callable=AsyncMock):
                with patch("subprocess.run", side_effect=FileNotFoundError):
                    result = await transcribe_voice("file_id")
                    assert result is None

    @pytest.mark.asyncio
    async def test_speech_recognition_fails(self):
        """Bot should handle speech recognition failure."""
        import speech_recognition as sr
        from bot import transcribe_voice

        with patch("bot.bot.get_file") as mock_file:
            mock_file.return_value.file_path = "/voice/file.ogg"
            with patch("bot.bot.download_file", new_callable=AsyncMock):
                with patch("subprocess.run"):
                    with patch("speech_recognition.Recognizer") as mock_rec:
                        mock_rec.return_value.recognize_google.side_effect = sr.UnknownValueError()
                        result = await transcribe_voice("file_id")
                        assert result is None


# --- Test 6: Scheduler resilience ---

class TestSchedulerResilience:
    """Test that scheduler survives failures."""

    @pytest.mark.asyncio
    async def test_digest_fails_scheduler_continues(self):
        """Scheduler should continue even if digest fails."""
        from bot import send_daily_digest

        with patch("bot.collect_news", side_effect=Exception("Network error")):
            # Should not raise
            await send_daily_digest()


# --- Test 7: Network timeout ---

class TestNetworkResilience:
    """Test handling of network timeouts."""

    @pytest.mark.asyncio
    async def test_rss_fetch_timeout(self):
        """Feed fetch should handle timeout."""
        from parser import _fetch_feed

        mock_session = AsyncMock()
        mock_session.get.side_effect = asyncio.TimeoutError()

        result = await _fetch_feed(mock_session, {"name": "test", "url": "https://test"})
        assert result == []

    @pytest.mark.asyncio
    async def test_rss_fetch_connection_error(self):
        """Feed fetch should handle connection error."""
        from parser import _fetch_feed

        mock_session = AsyncMock()
        mock_session.get.side_effect = Exception("Connection refused")

        result = await _fetch_feed(mock_session, {"name": "test", "url": "https://test"})
        assert result == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
