from __future__ import annotations

import aiosqlite
from pathlib import Path

DB_PATH: Path = Path(__file__).parent / "sent_news.db"


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
        await db.commit()


async def is_new_user(user_id: int) -> bool:
    """Return True if the user has never interacted with the bot."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        return await cursor.fetchone() is None


async def register_user(user_id: int, username: str = "") -> None:
    """Save user to the database."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        await db.commit()


async def is_sent(link: str) -> bool:
    """Return True if the link was already sent."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM sent_news WHERE link = ?", (link,))
        return await cursor.fetchone() is not None


async def mark_sent(link: str, title: str = "") -> None:
    """Insert a record so we never resend the same link."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO sent_news (link, title) VALUES (?, ?)",
            (link, title),
        )
        await db.commit()


async def get_stats() -> dict:
    """Return stats about sent news."""
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


async def get_recent_news(limit: int = 10) -> list[dict]:
    """Return the most recent news items."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT title, link, sent FROM sent_news ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
