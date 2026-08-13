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
            with patch("database.mark_sent", new_callable=AsyncMock):
                # Two subscribers, so the broadcast loop actually has
                # something to iterate over. Neither has been sent
                # anything yet, and per-user delivery is not recorded
                # here — this test only cares about broadcast resilience.
                with patch("database.get_all_user_ids", return_value=[111, 222]):
                    with patch("database.get_disabled_topics", return_value=set()):
                        with patch("database.get_user_sent_links", return_value=set()):
                            with patch("database.mark_user_sent", new_callable=AsyncMock):
                                with patch("database.prune_old_user_sent_news", new_callable=AsyncMock):
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

def _fake_ffmpeg_process(returncode: int = 0) -> MagicMock:
    """A fake asyncio subprocess handle standing in for
    asyncio.create_subprocess_exec("ffmpeg", ...)."""
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = returncode
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    return proc


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
        """Bot should handle missing ffmpeg.

        transcribe_voice() converts audio via asyncio.create_subprocess_exec
        (not subprocess.run — patching that, as this test used to, mocks a
        function the real code never calls, so ffmpeg would actually run
        for real against a throwaway temp file underneath the test).
        """
        from bot import transcribe_voice

        with patch("bot.bot.get_file") as mock_file:
            mock_file.return_value.file_path = "/voice/file.ogg"
            with patch("bot.bot.download_file", new_callable=AsyncMock):
                with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
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
                # A successful "conversion" so the code actually reaches
                # the recognizer step below, rather than bailing out early
                # on a real ffmpeg run against a fake/missing file.
                with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_fake_ffmpeg_process(0))):
                    with patch("speech_recognition.AudioFile"):
                        with patch("speech_recognition.Recognizer") as mock_rec:
                            mock_rec.return_value.recognize_google.side_effect = sr.UnknownValueError()
                            result = await transcribe_voice("file_id")
                            assert result is None
                            mock_rec.return_value.recognize_google.assert_called_once()

    @pytest.mark.asyncio
    async def test_ffmpeg_conversion_times_out(self):
        """A hung ffmpeg process must be killed, not left to hang the handler."""
        from bot import transcribe_voice

        with patch("bot.bot.get_file") as mock_file:
            mock_file.return_value.file_path = "/voice/file.ogg"
            with patch("bot.bot.download_file", new_callable=AsyncMock):
                proc = _fake_ffmpeg_process(0)
                # asyncio.wait_for(proc.communicate(), ...) propagates
                # whatever the wrapped awaitable raises — no need to patch
                # wait_for itself, just make communicate() the thing that
                # "times out".
                proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
                with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
                    result = await transcribe_voice("file_id")
                    assert result is None
                    proc.kill.assert_called_once()


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

def _mock_get_raising(exc: Exception) -> MagicMock:
    """A fake aiohttp ClientSession whose .get(...) — used as
    `async with session.get(...) as resp:` — raises `exc` on __aenter__.

    session.get(...) itself is a plain (sync) call that returns an async
    context manager; the actual request happens in __aenter__. An
    AsyncMock() with .get.side_effect set (the previous shape of these
    tests) makes session.get(...) return a bare coroutine instead, which
    `async with` can't use as a context manager at all — that TypeError
    happened to also land in _fetch_feed's except Exception, so the test
    passed without ever exercising real timeout/connection-error handling.
    """
    session = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(side_effect=exc)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session.get.return_value = ctx
    return session


class TestNetworkResilience:
    """Test handling of network timeouts."""

    @pytest.mark.asyncio
    async def test_rss_fetch_timeout(self):
        """Feed fetch should handle timeout."""
        from parser import _fetch_feed

        mock_session = _mock_get_raising(asyncio.TimeoutError())
        result = await _fetch_feed(mock_session, {"name": "test", "url": "https://test"})
        assert result == []

    @pytest.mark.asyncio
    async def test_rss_fetch_connection_error(self):
        """Feed fetch should handle connection error."""
        from parser import _fetch_feed

        mock_session = _mock_get_raising(Exception("Connection refused"))
        result = await _fetch_feed(mock_session, {"name": "test", "url": "https://test"})
        assert result == []


# --- Test 8: Per-user digest delivery tracking ---

class TestPerUserDigestDelivery:
    """Regression tests for user_sent_news — delivery is tracked per user,
    not globally, and only recorded after an actual successful send."""

    @pytest.mark.asyncio
    async def test_mark_and_get_user_sent_links(self, tmp_path):
        import database

        db_path = tmp_path / "test.db"
        with patch("database.DB_PATH", db_path):
            await database.init_db()
            assert await database.get_user_sent_links(111) == set()

            await database.mark_user_sent(111, "https://a.test")
            await database.mark_user_sent(111, "https://b.test")
            assert await database.get_user_sent_links(111) == {
                "https://a.test", "https://b.test",
            }
            # A different (e.g. newly registered) user has their own,
            # independent, initially-empty record — they are not
            # affected by what's already been sent to someone else.
            assert await database.get_user_sent_links(222) == set()

    @pytest.mark.asyncio
    async def test_prune_old_user_sent_news(self, tmp_path):
        import aiosqlite
        import database

        db_path = tmp_path / "test.db"
        with patch("database.DB_PATH", db_path):
            await database.init_db()
            await database.mark_user_sent(111, "https://old.test")
            async with aiosqlite.connect(db_path) as db:
                await db.execute("UPDATE user_sent_news SET sent = datetime('now', '-10 days')")
                await db.commit()

            await database.prune_old_user_sent_news(days=3)
            assert await database.get_user_sent_links(111) == set()

    @pytest.mark.asyncio
    async def test_failed_send_not_marked_delivered(self):
        """If sending an item to a user fails, it must not be recorded as
        delivered — otherwise that user loses the item permanently instead
        of getting it retried at the next scheduled digest."""
        from bot import _send_personalized_digest

        items = [{
            "title": "News 1", "link": "https://test1.com", "summary": "s",
            "source": "test", "image_url": None, "categories": {"general"},
        }]

        with patch("database.get_disabled_topics", return_value=set()):
            with patch("database.get_user_sent_links", return_value=set()):
                with patch("database.mark_user_sent", new_callable=AsyncMock) as mock_mark:
                    call_count = 0

                    async def header_ok_item_fails(**kwargs):
                        nonlocal call_count
                        call_count += 1
                        if call_count > 1:
                            raise Exception("send failed")

                    with patch("bot.bot.send_message", side_effect=header_ok_item_fails):
                        await _send_personalized_digest(111, 111, items, record_sent=True)
                        mock_mark.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_send_marked_delivered(self):
        from bot import _send_personalized_digest

        items = [{
            "title": "News 1", "link": "https://test1.com", "summary": "s",
            "source": "test", "image_url": None, "categories": {"general"},
        }]

        with patch("database.get_disabled_topics", return_value=set()):
            with patch("database.get_user_sent_links", return_value=set()):
                with patch("bot.bot.send_message", new_callable=AsyncMock):
                    with patch("database.mark_user_sent", new_callable=AsyncMock) as mock_mark:
                        await _send_personalized_digest(111, 111, items, record_sent=True)
                        mock_mark.assert_awaited_once_with(111, "https://test1.com")

    @pytest.mark.asyncio
    async def test_already_delivered_item_not_resent(self):
        """A user who already has an item in user_sent_news shouldn't get
        it broadcast to them again at the next scheduled digest."""
        from bot import _send_personalized_digest

        items = [{
            "title": "News 1", "link": "https://test1.com", "summary": "s",
            "source": "test", "image_url": None, "categories": {"general"},
        }]

        with patch("database.get_disabled_topics", return_value=set()):
            with patch("database.get_user_sent_links", return_value={"https://test1.com"}):
                with patch("bot.bot.send_message", new_callable=AsyncMock) as mock_send:
                    result = await _send_personalized_digest(111, 111, items, record_sent=True)
                    assert result is False
                    mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_demand_digest_ignores_delivery_history(self):
        """/digest (record_sent=False) is a live snapshot, not a delivery
        channel: it must not consult or write user_sent_news, so it never
        hides an item the scheduled broadcast hasn't sent yet, and never
        suppresses the scheduled broadcast from reaching a subscriber."""
        from bot import _send_personalized_digest

        items = [{
            "title": "News 1", "link": "https://test1.com", "summary": "s",
            "source": "test", "image_url": None, "categories": {"general"},
        }]

        with patch("database.get_disabled_topics", return_value=set()):
            with patch("database.get_user_sent_links", new_callable=AsyncMock) as mock_get:
                with patch("bot.bot.send_message", new_callable=AsyncMock):
                    with patch("database.mark_user_sent", new_callable=AsyncMock) as mock_mark:
                        result = await _send_personalized_digest(111, 111, items, record_sent=False)
                        assert result is True
                        mock_get.assert_not_called()
                        mock_mark.assert_not_called()


# --- Test 9: HTML escaping in Telegram messages ---

class TestHTMLEscaping:
    """Telegram's HTML parse mode breaks (or can be abused) on unescaped
    '<', '>', '&' from external input — RSS titles, scraped listing
    fields, LLM output, third-party URLs."""

    def test_telegram_text_escapes_special_chars(self):
        from telegram_format import telegram_text

        assert telegram_text("<b>hi</b> & co") == "&lt;b&gt;hi&lt;/b&gt; &amp; co"

    def test_telegram_url_accepts_http_https(self):
        from telegram_format import telegram_url

        assert telegram_url("https://example.com/a?b=1") == "https://example.com/a?b=1"

    def test_telegram_url_rejects_non_http_scheme(self):
        from telegram_format import telegram_url

        with pytest.raises(ValueError):
            telegram_url("javascript:alert(1)")
        with pytest.raises(ValueError):
            telegram_url("not a url at all")

    def test_telegram_link_falls_back_to_text_on_bad_url(self):
        from telegram_format import telegram_link

        assert telegram_link("javascript:alert(1)", "click me") == "click me"

    def test_digest_caption_escapes_html_in_external_fields(self):
        from bot import _digest_item_caption

        item = {
            "title": "Цены <script>alert(1)</script> выросли",
            "source": "test & co",
            "link": "https://example.com/a",
            "summary": "текст с <тегами> и & амперсандом",
        }
        caption = _digest_item_caption(1, item)
        assert "<script>" not in caption
        assert "&lt;script&gt;" in caption
        assert "test &amp; co" in caption


# --- Test 10: Telegram message length limits ---

class TestTelegramLimits:
    def test_digest_caption_within_photo_caption_limit(self):
        """Telegram's photo caption limit is 1024 chars — a long RSS
        summary must be trimmed to fit, not sent as-is and rejected."""
        from bot import _digest_item_caption

        item = {
            "title": "A" * 100,
            "source": "test",
            "link": "https://example.com/" + "a" * 200,
            "summary": "B" * 5000,
        }
        caption = _digest_item_caption(1, item)
        assert len(caption) <= 1024


# --- Test 11: RSS whitelist / date parsing hardening ---

class TestParserHardening:
    def test_whitelist_rejects_lookalike_domain(self):
        """"eviln1info.rs" ends with the same characters as "n1info.rs"
        but is a completely different, unrelated domain — plain
        str.endswith() would wrongly accept it."""
        from parser import _domain_in_whitelist

        assert _domain_in_whitelist("https://eviln1info.rs/fake") is False
        assert _domain_in_whitelist("https://n1info.rs/real") is True

    def test_whitelist_accepts_real_subdomain(self):
        from parser import _domain_in_whitelist

        assert _domain_in_whitelist("https://www.blic.rs/article") is True

    def test_entry_link_returns_none_for_unwhitelisted_link(self):
        """No fallback to an unwhitelisted link — if nothing in the entry
        matches, the entry is dropped, not forwarded anyway."""
        from parser import _entry_link

        entry = {"links": [{"href": "https://eviln1info.rs/fake"}]}
        assert _entry_link(entry) is None

    def test_pub_date_without_timezone_is_utc_aware(self):
        """A naive datetime here would raise TypeError when compared
        against the timezone-aware cutoff in collect_news()."""
        from parser import _parse_pub_date

        entry = {"published": "Wed, 12 Aug 2026 10:00:00"}  # no TZ offset
        result = _parse_pub_date(entry)
        assert result is not None
        assert result.tzinfo is not None


# --- Test 12: SQLite schema migrations ---

class TestDatabaseMigrations:
    @pytest.mark.asyncio
    async def test_init_db_idempotent_and_complete(self, tmp_path):
        """init_db() runs on every startup against a possibly-existing
        production DB — it must be safe to call repeatedly, and every
        table/column added by later migrations must actually exist."""
        import aiosqlite
        import database

        db_path = tmp_path / "test.db"
        with patch("database.DB_PATH", db_path):
            await database.init_db()
            await database.init_db()  # must not raise

            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute("PRAGMA table_info(real_estate_listings)")
                cols = {row[1] for row in await cursor.fetchall()}
                assert {"city", "deal_type", "price_value"}.issubset(cols)

                cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = {row[0] for row in await cursor.fetchall()}
                assert {"user_sent_news", "user_disabled_topics", "real_estate_listings"}.issubset(tables)


# --- Test 13: combinable real-estate filters (price + date, checkboxes) ---

class TestRealEstateCombinedFilters:
    """price_dir and date_dir are independent, combinable filters — not a
    single mutually-exclusive sort choice."""

    @pytest.mark.asyncio
    async def test_price_primary_date_tiebreaker(self, tmp_path):
        import database

        db_path = tmp_path / "test.db"
        with patch("database.DB_PATH", db_path):
            await database.init_db()
            # Two listings tied on price, one older, one newer.
            await database.upsert_real_estate_listing(
                "old-cheap", "Old cheap", "100 €", "Beograd", "https://x/1", None,
                "HaloOglasi", city="beograd", deal_type="rent", price_value=100,
            )
            await database.upsert_real_estate_listing(
                "new-cheap", "New cheap", "100 €", "Beograd", "https://x/2", None,
                "HaloOglasi", city="beograd", deal_type="rent", price_value=100,
            )
            await database.upsert_real_estate_listing(
                "pricey", "Pricey", "500 €", "Beograd", "https://x/3", None,
                "HaloOglasi", city="beograd", deal_type="rent", price_value=500,
            )
            # Backdate "old-cheap" so date-as-tiebreaker is meaningful.
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "UPDATE real_estate_listings SET first_seen = datetime('now', '-1 day') "
                    "WHERE ad_id = 'old-cheap'"
                )
                await db.commit()

            rows = await database.get_real_estate_listings_filtered(
                city="beograd", deal_type="rent", price_dir="a", date_dir="n", limit=10,
            )
            # Cheapest first (price primary) — the two 100€ listings before
            # the 500€ one — and within the tie, newest first (tiebreaker).
            assert [r["ad_id"] for r in rows] == ["new-cheap", "old-cheap", "pricey"]

    @pytest.mark.asyncio
    async def test_no_price_filter_falls_back_to_date_only(self, tmp_path):
        import database

        db_path = tmp_path / "test.db"
        with patch("database.DB_PATH", db_path):
            await database.init_db()
            await database.upsert_real_estate_listing(
                "a", "A", "500 €", "Beograd", "https://x/a", None,
                "HaloOglasi", city="beograd", deal_type="rent", price_value=500,
            )
            await database.upsert_real_estate_listing(
                "b", "B", "100 €", "Beograd", "https://x/b", None,
                "HaloOglasi", city="beograd", deal_type="rent", price_value=100,
            )
            # Backdate "a" so date order is unambiguous regardless of
            # CURRENT_TIMESTAMP's second-level resolution.
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "UPDATE real_estate_listings SET first_seen = datetime('now', '-1 day') "
                    "WHERE ad_id = 'a'"
                )
                await db.commit()

            # price_dir="-" (off): the pricier listing ("a", 500€) must NOT
            # be pushed down by price — only date order applies, and "b"
            # (inserted after backdating "a") sorts first as newest.
            rows = await database.get_real_estate_listings_filtered(
                city="beograd", deal_type="rent", price_dir="-", date_dir="n", limit=10,
            )
            assert [r["ad_id"] for r in rows] == ["b", "a"]


class TestRealEstateFilterKeyboard:
    """The filter panel toggles one dimension per tap while preserving the
    other — this is what makes the two filters independently combinable."""

    def test_tapping_active_price_button_turns_it_off(self):
        from bot import real_estate_filters_kb

        kb = real_estate_filters_kb("beograd", "rent", price_dir="a", date_dir="n")
        price_asc_button = kb.inline_keyboard[0][0]
        assert price_asc_button.text.startswith("✅")
        assert price_asc_button.callback_data == "re_sort:beograd:rent:-:n"

    def test_tapping_inactive_price_button_preserves_date(self):
        from bot import real_estate_filters_kb

        kb = real_estate_filters_kb("beograd", "rent", price_dir="-", date_dir="o")
        price_desc_button = kb.inline_keyboard[1][0]
        assert price_desc_button.text.startswith("⬜")
        # Turns price on (desc) without disturbing the already-active
        # "oldest first" date filter.
        assert price_desc_button.callback_data == "re_sort:beograd:rent:d:o"

    def test_date_buttons_are_mutually_exclusive_within_their_own_axis(self):
        from bot import real_estate_filters_kb

        kb = real_estate_filters_kb("beograd", "rent", price_dir="a", date_dir="n")
        newest_button = kb.inline_keyboard[2][0]
        oldest_button = kb.inline_keyboard[3][0]
        assert newest_button.text.startswith("✅")
        assert oldest_button.text.startswith("⬜")
        # Switching date doesn't touch the active price filter.
        assert oldest_button.callback_data == "re_sort:beograd:rent:a:o"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
