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
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_disabled_topics (
                user_id INTEGER NOT NULL,
                topic   TEXT    NOT NULL,
                PRIMARY KEY (user_id, topic)
            )
            """
        )
        # Per-user delivery record for the scheduled digest broadcast.
        # `sent_news` (above) is a global "have we ever seen this link"
        # log used only for /news and /status — it must NOT gate what a
        # given user gets sent, or a user whose send failed (or who
        # subscribed after the item was already broadcast to others) would
        # never see it. Delivery success/failure is tracked per user here,
        # and only written after an actual successful send.
        cursor = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_sent_news'"
        )
        user_sent_news_is_new = await cursor.fetchone() is None

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sent_news (
                user_id INTEGER NOT NULL,
                link    TEXT    NOT NULL,
                sent    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, link)
            )
            """
        )

        if user_sent_news_is_new:
            # One-time migration, the moment this deploys onto a database
            # that predates per-user tracking: without it, every currently
            # registered user's `user_sent_news` starts empty, so the very
            # next digest (including the one-off job that runs on every
            # process start) would treat everything still inside
            # collect_news()'s 24h window as brand new and re-send items
            # already delivered under the old global-only `sent_news` gate.
            #
            # The old system never recorded *which* user got *which* link,
            # only that a link was broadcast at all — so this is a best
            # effort, not a perfect backfill: it marks every currently
            # registered user as having already received every link ever
            # in `sent_news`. That over-approximates for anyone who
            # subscribed after a given item was broadcast (they'll miss
            # re-seeing it once, same as before this feature existed) and
            # under-approximates nothing that matters, since anything
            # outside the last 24h can never reappear in collect_news()
            # anyway. Runs only once — user_sent_news_is_new is False on
            # every subsequent init_db() call.
            logger.info("Migrating sent_news history into user_sent_news (one-time)...")
            await db.execute(
                """
                INSERT OR IGNORE INTO user_sent_news (user_id, link)
                SELECT u.user_id, s.link FROM users u CROSS JOIN sent_news s
                """
            )
        # Migration: city / deal_type / price_value were added later, for
        # the real-estate menu's filter+sort feature. ALTER TABLE ADD
        # COLUMN is not naturally idempotent in SQLite, so on a database
        # that already has these columns (i.e. every run after the first)
        # each statement below just fails with "duplicate column name" —
        # caught and ignored.
        for ddl in (
            "ALTER TABLE real_estate_listings ADD COLUMN city TEXT",
            "ALTER TABLE real_estate_listings ADD COLUMN deal_type TEXT",
            "ALTER TABLE real_estate_listings ADD COLUMN price_value INTEGER",
        ):
            try:
                await db.execute(ddl)
            except Exception:
                pass  # column already exists
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
    city: str | None = None,
    deal_type: str | None = None,
    price_value: int | None = None,
) -> None:
    """Insert a listing, or refresh it (and bump last_seen) if already known."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO real_estate_listings
                    (ad_id, title, price, location, url, image_url, source,
                     city, deal_type, price_value, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(ad_id) DO UPDATE SET
                    title=excluded.title,
                    price=excluded.price,
                    location=excluded.location,
                    url=excluded.url,
                    image_url=excluded.image_url,
                    source=excluded.source,
                    city=excluded.city,
                    deal_type=excluded.deal_type,
                    price_value=excluded.price_value,
                    last_seen=CURRENT_TIMESTAMP
                """,
                (ad_id, title, price, location, url, image_url, source,
                 city, deal_type, price_value),
            )
            await db.commit()
    except Exception as exc:
        logger.error("DB upsert_real_estate_listing error: %s", exc)


async def get_real_estate_listings(limit: int = 100) -> list[dict]:
    """Return the most recently seen real estate listings.

    Includes city/deal_type — callers doing their own free-text filtering
    (real_estate.search_real_estate_with_fallback) need these to match a
    detected city/deal-type signal against structured data, not just the
    free-text `location` string.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT ad_id, title, price, location, url, image_url, source,
                       city, deal_type
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


async def get_real_estate_listings_filtered(
    city: str | None = None,
    deal_type: str | None = None,
    price_dir: str | None = None,
    date_dir: str = "n",
    limit: int = 10,
) -> list[dict]:
    """Return listings for the real-estate menu, filtered and sorted.

    Price and date are independent, combinable sort criteria (checkboxes
    in the bot's UI) rather than a single mutually-exclusive choice — when
    both are set, price is the primary key and date the tiebreaker.

    Args:
        city: HALOOGLASI_CITIES slug (e.g. "beograd"), or None for all cities.
        deal_type: "sale" or "rent", or None for both.
        price_dir: "a" (cheapest first), "d" (priciest first), or None/"-"
            to leave price out of the sort entirely.
        date_dir: "o" (oldest first) or anything else (default) for newest
            first. Always applied — either as the primary key, or as the
            tiebreaker when price_dir is also set.
    """
    order_parts = []
    if price_dir in ("a", "d"):
        # NULL price_value (couldn't be parsed from the listing) always
        # sorts last regardless of direction, so a currently-priceless
        # listing doesn't jump to the top of "cheapest first".
        order_parts.append("price_value IS NULL")
        order_parts.append(f"price_value {'ASC' if price_dir == 'a' else 'DESC'}")
    order_parts.append(f"first_seen {'ASC' if date_dir == 'o' else 'DESC'}")
    order_by = ", ".join(order_parts)

    conditions = []
    params: list = []
    if city:
        conditions.append("city = ?")
        params.append(city)
    if deal_type:
        conditions.append("deal_type = ?")
        params.append(deal_type)
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"""
                SELECT ad_id, title, price, location, url, image_url, source,
                       city, deal_type, price_value
                FROM real_estate_listings
                {where_clause}
                ORDER BY {order_by}
                LIMIT ?
                """,
                params,
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as exc:
        logger.error("DB get_real_estate_listings_filtered error: %s", exc)
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


async def get_disabled_topics(user_id: int) -> set[str]:
    """Return the set of news category keys this user has turned off.

    Absence from this table means "on" — new users get everything by
    default, matching the all-checked starting state in the settings menu.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT topic FROM user_disabled_topics WHERE user_id = ?",
                (user_id,),
            )
            rows = await cursor.fetchall()
            return {row[0] for row in rows}
    except Exception as exc:
        logger.error("DB get_disabled_topics error: %s", exc)
        return set()


async def mark_user_sent(user_id: int, link: str) -> None:
    """Record that this specific user was successfully sent this item.

    Only call this after the send actually succeeded — this table is the
    source of truth for "did this user get this news item", used to avoid
    re-sending the same item to them at the next scheduled digest.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO user_sent_news (user_id, link) VALUES (?, ?)",
                (user_id, link),
            )
            await db.commit()
    except Exception as exc:
        logger.error("DB mark_user_sent error: %s", exc)


async def get_user_sent_links(user_id: int) -> set[str]:
    """Return the set of links already delivered to this user."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT link FROM user_sent_news WHERE user_id = ?",
                (user_id,),
            )
            rows = await cursor.fetchall()
            return {row[0] for row in rows}
    except Exception as exc:
        logger.error("DB get_user_sent_links error: %s", exc)
        return set()


async def prune_old_user_sent_news(days: int = 30) -> None:
    """Delete per-user delivery records older than `days`.

    The window has to outlive how long an item can keep reappearing in a
    feed, not just collect_news()'s 24h cutoff: _fetch_feed() deliberately
    keeps entries whose pub date can't be parsed, and such an entry stays
    eligible for as long as the source lists it. Pruning too eagerly (the
    original 3 days) meant a long-lived undated item lost its delivery
    record and got re-broadcast to everyone. 30 days costs very little —
    a handful of subscribers × ~15 items/day is a few thousand rows.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM user_sent_news WHERE sent < datetime('now', ?)",
                (f"-{days} days",),
            )
            await db.commit()
    except Exception as exc:
        logger.error("DB prune_old_user_sent_news error: %s", exc)


async def toggle_topic(user_id: int, topic: str) -> bool:
    """Flip a user's subscription to one news category.

    Returns the new enabled state (True = will now receive this topic).
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT 1 FROM user_disabled_topics WHERE user_id = ? AND topic = ?",
                (user_id, topic),
            )
            currently_disabled = await cursor.fetchone() is not None

            if currently_disabled:
                await db.execute(
                    "DELETE FROM user_disabled_topics WHERE user_id = ? AND topic = ?",
                    (user_id, topic),
                )
            else:
                await db.execute(
                    "INSERT OR IGNORE INTO user_disabled_topics (user_id, topic) VALUES (?, ?)",
                    (user_id, topic),
                )
            await db.commit()
            return currently_disabled  # was disabled -> now enabled, and vice versa
    except Exception as exc:
        logger.error("DB toggle_topic error: %s", exc)
        return True  # fail open: better to over-send than silently lose a subscriber
