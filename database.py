from __future__ import annotations

import logging
import aiosqlite
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH: Path = Path(__file__).parent / "sent_news.db"

# Maximum number of messages to keep in conversation history
MAX_HISTORY_LENGTH = 10


async def init_db() -> None:
    """Create the tables if they don't exist yet."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_news (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                link  TEXT    NOT NULL UNIQUE,
                title TEXT,
                sent  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_history (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  INTEGER NOT NULL,
                role     TEXT    NOT NULL,
                content  TEXT    NOT NULL,
                created  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS real_estate_listings (
                ad_id      TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                price      TEXT,
                location   TEXT,
                url        TEXT NOT NULL,
                image_url  TEXT,
                source     TEXT NOT NULL,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()


async def is_new_user(user_id: int) -> bool:
    """Return True if the user has never interacted with the bot."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
            return await cursor.fetchone() is None
    except Exception as exc:
        logger.error("DB is_new_user error: %s", exc)
        return True


async def register_user(user_id: int, username: str = "") -> None:
    """Save user to the database."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
                (user_id, username),
            )
            await db.commit()
    except Exception as exc:
        logger.error("DB register_user error: %s", exc)


async def get_all_user_ids() -> list[int]:
    """Return the user_id of every user who has ever started the bot."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT user_id FROM users")
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
    except Exception as exc:
        logger.error("DB get_all_user_ids error: %s", exc)
        return []


async def is_sent(link: str) -> bool:
    """Return True if the link was already sent."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT 1 FROM sent_news WHERE link = ?", (link,))
            return await cursor.fetchone() is not None
    except Exception as exc:
        logger.error("DB is_sent error: %s", exc)
        return False


async def mark_sent(link: str, title: str = "") -> None:
    """Insert a record so we never resend the same link."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO sent_news (link, title) VALUES (?, ?)",
                (link, title),
            )
            await db.commit()
    except Exception as exc:
        logger.error("DB mark_sent error: %s", exc)


async def get_stats() -> dict:
    """Return stats about sent news."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            total = (await (await db.execute("SELECT COUNT(*) FROM sent_news")).fetchone())[0]
            today = (await (await db.execute(
                "SELECT COUNT(*) FROM sent_news WHERE DATE(sent) = DATE('now')"
            )).fetchone())[0]
            last = (await (await db.execute(
                "SELECT sent FROM sent_news ORDER BY id DESC LIMIT 1"
            )).fetchone())
            return {
                "total": total,
                "today": today,
                "last_sent": last[0] if last else None,
            }
    except Exception as exc:
        logger.error("DB get_stats error: %s", exc)
        return {"total": 0, "today": 0, "last_sent": None}


async def get_recent_news(limit: int = 10) -> list[dict]:
    """Return the most recent news items."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT title, link, sent FROM sent_news ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as exc:
        logger.error("DB get_recent_news error: %s", exc)
        return []


async def save_message(user_id: int, role: str, content: str) -> None:
    """Save a message to conversation history."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO conversation_history (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content),
            )
            await db.commit()

            # Trim old messages to keep only MAX_HISTORY_LENGTH
            await db.execute(
                """
                DELETE FROM conversation_history
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM conversation_history
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (user_id, user_id, MAX_HISTORY_LENGTH),
            )
            await db.commit()
    except Exception as exc:
        logger.error("DB save_message error: %s", exc)


async def get_conversation_history(user_id: int, limit: int = MAX_HISTORY_LENGTH) -> list[dict]:
    """Get conversation history for a user."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT role, content FROM conversation_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            )
            rows = await cursor.fetchall()
            # Reverse to get chronological order
            return [dict(row) for row in reversed(rows)]
    except Exception as exc:
        logger.error("DB get_conversation_history error: %s", exc)
        return []


async def clear_conversation_history(user_id: int) -> None:
    """Clear conversation history for a user."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM conversation_history WHERE user_id = ?",
                (user_id,),
            )
            await db.commit()
    except Exception as exc:
        logger.error("DB clear_conversation_history error: %s", exc)


async def upsert_real_estate_listing(
    ad_id: str,
    title: str,
    price: str | None,
    location: str | None,
    url: str,
    image_url: str | None,
    source: str,
) -> None:
    """Insert a listing, or refresh it (and bump last_seen) if already known."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO real_estate_listings
                    (ad_id, title, price, location, url, image_url, source, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(ad_id) DO UPDATE SET
                    title=excluded.title,
                    price=excluded.price,
                    location=excluded.location,
                    url=excluded.url,
                    image_url=excluded.image_url,
                    source=excluded.source,
                    last_seen=CURRENT_TIMESTAMP
                """,
                (ad_id, title, price, location, url, image_url, source),
            )
            await db.commit()
    except Exception as exc:
        logger.error("DB upsert_real_estate_listing error: %s", exc)


async def get_real_estate_listings(limit: int = 100) -> list[dict]:
    """Return the most recently seen real estate listings."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT ad_id, title, price, location, url, image_url, source
                FROM real_estate_listings
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as exc:
        logger.error("DB get_real_estate_listings error: %s", exc)
        return []


async def prune_stale_listings(days: int = 7) -> None:
    """Delete listings that haven't been seen by the collector in `days` days.

    Ads eventually get taken down (sold, rented, expired) — this keeps the
    table from silently accumulating dead listings forever.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM real_estate_listings WHERE last_seen < datetime('now', ?)",
                (f"-{days} days",),
            )
            await db.commit()
    except Exception as exc:
        logger.error("DB prune_stale_listings error: %s", exc)
