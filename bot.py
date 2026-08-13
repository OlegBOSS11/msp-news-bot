from __future__ import annotations

import asyncio
import logging
import os
import socket
import tempfile
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    URLInputFile,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from gigachat.models.chat import Chat, Messages
import speech_recognition as sr

import config
import database
from gigachat_client import get_gigachat_client
from parser import collect_news
from real_estate import (
    search_real_estate_with_fallback,
    is_real_estate_query,
    refresh_real_estate_database,
)
from serbia_search import get_serbia_answer
from telegram_format import telegram_link, telegram_text
from translator import translate_to_russian, detect_language

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MOSCOW_TZ = timezone(timedelta(hours=3))

# Force IPv4: this VDS has no IPv6 route at all, and aiogram's default
# aiohttp connector may otherwise try IPv6 first and stall/fail before
# falling back. Same fix already applied to aiohttp usage in parser.py,
# real_estate.py and serbia_search.py — this covers the Bot API session
# itself (getUpdates/sendMessage/etc.), which was the one gap left.
#
# If TELEGRAM_PROXY_URL is set, all Bot API traffic is additionally routed
# through it — used to work around DPI-level interference on HTTPS to
# api.telegram.org from the RU-hosted VDS (measured: ~2.5% request failure
# rate, ~195 errors/day, despite a clean ICMP path). Only Telegram traffic
# goes through the proxy; GigaChat and RSS/real-estate scraping stay direct,
# since GigaChat access depends on the server being in Russia.
if config.TELEGRAM_PROXY_URL:
    _bot_session = AiohttpSession(proxy=config.TELEGRAM_PROXY_URL)
    logger.info("Telegram Bot API traffic routed via proxy")
else:
    _bot_session = AiohttpSession()
_bot_session._connector_init["family"] = socket.AF_INET

bot = Bot(token=config.BOT_TOKEN, session=_bot_session)
dp = Dispatcher()

# Get current date for system prompt
def get_current_date() -> str:
    """Get current date in Russian format."""
    now = datetime.now(MOSCOW_TZ)
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря"
    ]
    return f"{now.day} {months[now.month - 1]} {now.year} года"

SYSTEM_PROMPT_TEMPLATE = """Ты — эксперт по релокации из России в Сербию.

Текущая дата: {current_date}

Ты работаешь в Telegram-боте, который помогает людям, рассматривающим переезд из России в Сербию. Вот что бот умеет:
- /digest — получить сводку актуальных новостей о Сербии и релокации за сегодня
- Автоматическая ежедневная рассылка новостей в 10:00 и 18:00 МСК
- Ответы на вопросы о жизни в Сербии, переезде, визах, работе, недвижимости

Отвечай на вопросы пользователей о:
- Работе в Сербии: трудоустройство, визы, рабочие разрешения, налоговая система
- Недвижимости в Сербии: покупка, аренда, цены на рынке, юридические аспекты
- Образовании: школы и детские сады для детей, университеты для взрослых
- Новостях Сербии: политика, экономика, изменения в законах
- Ограничениях для переезда: визовые требования, таможенные правила, запреты
- Повседневной жизни: медицина, банковская система, язык, культура
- Оформлении документов: ВНЖ, ПМЖ, гражданство, регистрация

ВАЖНЫЕ ПРАВИЛА:
- Используй ТОЛЬКО свежую информацию (учитывай текущую дату)
- Если информации нет или она устаревшая, ЧЕСТНО скажи об этом
- Указывай дату и источник информации, когда это возможно
- Отвечай кратко, по делу, на русском языке
- Если не знаешь точный ответ — скажи об этом, не придумывай

Ты специализируешься на информации для русскоязычных людей, планирующих переезд."""


def get_system_prompt() -> str:
    """Get system prompt with current date."""
    return SYSTEM_PROMPT_TEMPLATE.format(current_date=get_current_date())


# --- Keyboard builders ---

OPEN_MENU_TEXT = "☰ Открыть меню"


def persistent_menu_kb() -> ReplyKeyboardMarkup:
    """A reply keyboard row that stays pinned at the bottom of the chat —
    unlike inline buttons (tied to one message), this is reachable from
    anywhere the user has scrolled to."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=OPEN_MENU_TEXT)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Сводка", callback_data="cmd_digest"),
            InlineKeyboardButton(text="📰 Новости", callback_data="cmd_news"),
        ],
        [
            InlineKeyboardButton(text="📊 Статус", callback_data="cmd_status"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="cmd_help"),
        ],
        [
            InlineKeyboardButton(text="🏠 Недвижимость", callback_data="re_menu"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu"),
        ],
    ])


async def settings_topics_kb(user_id: int) -> InlineKeyboardMarkup:
    """Checkbox-style toggle list of news categories for /settings."""
    disabled = await database.get_disabled_topics(user_id)
    rows = []
    for key, cat in config.NEWS_CATEGORIES.items():
        mark = "⬜" if key in disabled else "✅"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {cat['label']}", callback_data=f"topic_toggle:{key}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="cmd_start")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_button_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="cmd_start")],
    ])


# --- Real estate menu ---

REAL_ESTATE_CITY_LABELS = {
    "beograd": "🏙 Белград",
    "novi-sad": "🏙 Нови-Сад",
    "nis": "🏙 Ниш",
    "kragujevac": "🏙 Крагуевац",
}

REAL_ESTATE_DEAL_LABELS = {
    "sale": "🛒 Купить",
    "rent": "🏠 Снять",
}

def real_estate_city_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"re_city:{slug}")]
        for slug, label in REAL_ESTATE_CITY_LABELS.items()
    ]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="cmd_start")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def real_estate_deal_kb(city: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"re_deal:{city}:{deal}")]
        for deal, label in REAL_ESTATE_DEAL_LABELS.items()
    ]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="re_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def real_estate_filters_kb(city: str, deal: str, price_dir: str, date_dir: str) -> InlineKeyboardMarkup:
    """Checkbox-style filter panel — price direction and date order are
    independent and combinable (unlike the old single-choice sort menu).

    price_dir: "a" (cheapest first), "d" (priciest first), or "-" (off).
    date_dir: "n" (newest first, default) or "o" (oldest first) — always
    on, since a listing list needs *some* base order even with no price
    filter active.
    """
    def _toggle(dim: str, value: str) -> str:
        if dim == "price":
            new_price = "-" if price_dir == value else value
            return f"re_sort:{city}:{deal}:{new_price}:{date_dir}"
        new_date = value if date_dir != value else date_dir  # date always has exactly one active value
        return f"re_sort:{city}:{deal}:{price_dir}:{new_date}"

    def _mark(active: bool) -> str:
        return "✅" if active else "⬜"

    rows = [
        [InlineKeyboardButton(
            text=f"{_mark(price_dir == 'a')} 💰⬆️ Дешевле → дороже",
            callback_data=_toggle("price", "a"),
        )],
        [InlineKeyboardButton(
            text=f"{_mark(price_dir == 'd')} 💰⬇️ Дороже → дешевле",
            callback_data=_toggle("price", "d"),
        )],
        [InlineKeyboardButton(
            text=f"{_mark(date_dir != 'o')} 🆕 Сначала новые",
            callback_data=_toggle("date", "n"),
        )],
        [InlineKeyboardButton(
            text=f"{_mark(date_dir == 'o')} 🕰 Сначала старые",
            callback_data=_toggle("date", "o"),
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"re_city:{city}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- News formatting ---

def _categorize_item(title: str, summary: str) -> set[str]:
    """Which NEWS_CATEGORIES keys this item's text matches (never empty)."""
    text = (title + " " + summary).lower()
    matched = {
        key for key, cat in config.NEWS_CATEGORIES.items()
        if cat["keywords"] and any(kw in text for kw in cat["keywords"])
    }
    return matched or {"general"}


async def _collect_fresh_news() -> list[dict]:
    """Collect today's relevant news.

    collect_news() already limits results to the last 24h. This used to
    also drop anything in the global `sent_news` log, but that made
    delivery depend on whether *anyone* had ever been sent an item before —
    a user whose send failed, or who subscribed after another user already
    got it, would never see it. Delivery tracking is per-user now (see
    database.get_user_sent_links/mark_user_sent), so this is unfiltered.
    """
    try:
        return await collect_news()
    except Exception as exc:
        logger.error("Failed to collect news: %s", exc, exc_info=True)
        return []


async def _prepare_digest_items(fresh: list[dict]) -> list[dict]:
    """Translate (once) and tag each item with its news categories.

    Done once per digest run regardless of how many subscribers there are —
    per-user personalization only filters this shared, already-prepared list.
    """
    prepared = []
    for item in fresh:
        title = item["title"]
        summary = item.get("summary", "")
        source = item["source"]

        if source in config.SERBIAN_NEWS_SOURCES:
            if detect_language(title) == "serbian":
                title = await translate_to_russian(title)
            if detect_language(summary) == "serbian":
                summary = await translate_to_russian(summary)

        prepared.append({
            "title": title,
            "summary": summary,
            "link": item["link"],
            "source": source,
            "image_url": item.get("image_url"),
            "categories": _categorize_item(title, summary),
        })
    return prepared


def _digest_header_text(count: int) -> str:
    today = datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y")
    return (
        f"📊 <b>Ежедневная сводка о Сербии на {today}.</b>\n"
        f"Актуальные новости для релокации.\n"
        f"Новостей по вашим темам: {count}"
    )


def _digest_item_caption(idx: int, item: dict) -> str:
    """Caption for one digest item, trimmed to fit Telegram's 1024-char
    photo caption limit (only summary gets shortened, never the markup).

    title/source/summary come from RSS feeds and link from the feed's own
    <link> — all external input, escaped before going into an HTML message.
    """
    title = telegram_text(item["title"])
    source = telegram_text(item["source"])
    link = telegram_link(item["link"], "Читать далее")
    header = f"<b>📌 {idx}. {title}</b>\n<i>Источник: {source}</i>\n"
    footer = f"\n🔗 {link}"
    budget = 1024 - len(header) - len(footer) - 1
    summary = telegram_text(item["summary"])
    if budget <= 0:
        summary = ""
    elif len(summary) > budget:
        summary = summary[:budget - 1] + "…"
    return header + summary + footer


async def _send_digest_item(chat_id: int, idx: int, item: dict) -> bool:
    """Send one digest item — as a photo if it has an image, else plain text.

    Returns True only if the item actually reached the user — the caller
    uses this to decide whether to record it as delivered.
    """
    caption = _digest_item_caption(idx, item)
    if item.get("image_url"):
        try:
            photo = URLInputFile(item["image_url"])
            await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption, parse_mode="HTML")
            return True
        except Exception as exc:
            logger.warning("Failed to send digest photo to %s: %s", chat_id, exc)
            # Fall through to the text-only retry below.
    try:
        await bot.send_message(
            chat_id=chat_id, text=caption, parse_mode="HTML", disable_web_page_preview=True,
        )
        return True
    except Exception as exc:
        logger.error("Failed to send digest item to %s: %s", chat_id, exc)
        return False


async def _send_personalized_digest(
    chat_id: int, user_id: int, items: list[dict], record_sent: bool,
) -> bool:
    """Send only the items matching this user's enabled topics.

    record_sent=True (scheduled broadcast): items this user already has
    (database.get_user_sent_links) are skipped, and each item is recorded
    as delivered only after it's actually sent successfully — a failed
    send or a Telegram outage no longer loses the item for that user, it's
    just retried at the next scheduled run.

    record_sent=False (on-demand /digest): no history is consulted or
    written — it's a live snapshot, not a delivery channel, and must not
    affect what the scheduled broadcast later sends.

    Returns True if anything was actually sent.
    """
    disabled = await database.get_disabled_topics(user_id)
    candidates = [it for it in items if it["categories"] - disabled]

    if record_sent:
        already_sent = await database.get_user_sent_links(user_id)
        candidates = [it for it in candidates if it["link"] not in already_sent]

    if not candidates:
        return False

    try:
        await bot.send_message(
            chat_id=chat_id, text=_digest_header_text(len(candidates)),
            parse_mode="HTML", disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.error("Failed to send digest header to %s: %s", chat_id, exc)
        return False

    for i, item in enumerate(candidates, 1):
        ok = await _send_digest_item(chat_id, i, item)
        if ok and record_sent:
            await database.mark_user_sent(user_id, item["link"])

    return True


async def send_daily_digest() -> None:
    """Broadcast the scheduled digest to every user who has started the bot,
    personalized per user by their enabled news topics and by what they've
    already been sent (database.user_sent_news).

    Called only by the 10:00/18:00 scheduler jobs.
    """
    logger.info("Starting daily digest collection...")
    fresh = await _collect_fresh_news()
    if not fresh:
        logger.info("No new MSP news found for today.")
        return

    items = await _prepare_digest_items(fresh)

    # Record-keeping for /news and /status — not used to gate delivery
    # anymore (see _collect_fresh_news / _send_personalized_digest).
    for item in fresh:
        try:
            await database.mark_sent(item["link"], item["title"])
        except Exception as exc:
            logger.warning("DB mark_sent failed: %s", exc)

    await database.prune_old_user_sent_news(days=3)

    user_ids = await database.get_all_user_ids()
    if not user_ids:
        logger.warning("No subscribed users found — digest was not sent to anyone.")
    sent_count = 0
    for user_id in user_ids:
        if await _send_personalized_digest(user_id, user_id, items, record_sent=True):
            sent_count += 1
        await asyncio.sleep(0.05)  # stay well under Telegram's rate limits

    logger.info("Digest sent to %d/%d subscribers (%d items collected).", sent_count, len(user_ids), len(fresh))


async def send_personal_digest(chat_id: int) -> bool:
    """Send an on-demand digest to a single chat (used by /digest and the button).

    This is a live snapshot of the last 24h of relevant news, personalized
    only by topic — it doesn't consult or write per-user delivery history,
    so it neither hides items the scheduled broadcast hasn't sent yet nor
    suppresses the scheduled broadcast from later reaching every subscriber.

    Returns True if a digest was actually sent, False if there was nothing new
    (either no fresh news at all, or none matched this user's enabled topics).
    """
    logger.info("Collecting personal digest for chat %s...", chat_id)
    fresh = await _collect_fresh_news()
    if not fresh:
        return False

    items = await _prepare_digest_items(fresh)
    # Private chats: chat_id == user_id, safe to reuse for topic lookup.
    return await _send_personalized_digest(chat_id, chat_id, items, record_sent=False)


# --- Welcome message ---

WELCOME_TEXT = (
    "👋 <b>Добро пожаловать!</b>\n\n"
    "Я — бот для помощи с релокацией из России в Сербию.\n"
    "Автоматически собираю актуальные новости и отвечаю на вопросы.\n\n"
    "📌 <b>Что умеют кнопки:</b>\n\n"
    "📋 <b>Сводка</b> — ежедневная подборка новостей о Сербии\n"
    "📰 <b>Новости</b> — последние новости из базы\n"
    "📊 <b>Статус</b> — проверить, работает ли бот\n"
    "❓ <b>Помощь</b> — справка по всем функциям\n"
    "🏠 <b>Недвижимость</b> — подбор объявлений по городу, типу сделки и сортировке\n\n"
    "💬 Просто напишите текст — и я отвечу с помощью ИИ!\n\n"
    "🕐 Сводка приходит автоматически в <b>10:00</b> и <b>18:00</b> МСК\n\n"
    "⚠️ Использую только свежую информацию"
)

SIMPLE_MENU_TEXT = "Выберите действие:"


# --- Command handlers (slash commands still work) ---

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user_id = message.from_user.id
    username = message.from_user.username or ""
    new = await database.is_new_user(user_id)
    await database.register_user(user_id, username)

    # Pin the "☰ Открыть меню" button to the bottom of the chat — a reply
    # keyboard (unlike inline buttons) stays reachable no matter how far
    # the user has scrolled. Only needs setting once; Telegram remembers it.
    await message.answer("🤖 Бот готов к работе.", reply_markup=persistent_menu_kb())

    if new:
        await message.answer(WELCOME_TEXT, parse_mode="HTML", reply_markup=main_menu_kb())
    else:
        await message.answer(SIMPLE_MENU_TEXT, reply_markup=main_menu_kb())


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🤖 <b>Что я умею:</b>\n\n"
        "📋 /digest — получить сводку новостей о Сербии\n"
        "📰 /news — последние новости из базы\n"
        "📊 /status — статус бота и статистика\n"
        "🗑️ /clear — очистить историю диалога\n"
        "🏠 Недвижимость — подбор объявлений по городу и фильтрам (кнопка в меню)\n"
        "💬 Задать вопрос — отвечу о релокации в Сербию\n\n"
        "<b>Примеры вопросов:</b>\n"
        "• Как получить ВНЖ в Сербии?\n"
        "• Какие школы есть в Белграде?\n"
        "• Сколько стоит аренда квартиры?\n"
        "• Как открыть банковский счёт?\n"
        "• Какие налоги в Сербии?\n"
        "• Какие документы нужны для переезда?\n"
        "• Есть ли работа для русских?\n"
        "• Какая медицина в Сербии?",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


@dp.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    """Clear conversation history for the user."""
    user_id = message.from_user.id
    await database.clear_conversation_history(user_id)
    await message.answer(
        "🗑️ История диалога очищена.",
        reply_markup=main_menu_kb(),
    )


@dp.message(Command("status"))
async def cmd_status(message: Message) -> None:
    stats = await database.get_stats()
    now = datetime.now(MOSCOW_TZ)
    await message.answer(
        "📊 <b>Статус бота:</b>\n\n"
        f"✅ Бот работает\n"
        f"🕐 Текущее время: {now.strftime('%H:%M %d.%m.%Y')} (МСК)\n"
        f"📰 Всего новостей отправлено: {stats['total']}\n"
        f"📅 За сегодня: {stats['today']}\n"
        f"⏰ Последняя отправка: {stats['last_sent'] or 'нет'}\n"
        f"🔔 Следующая сводка: в 18:00 МСК",
        parse_mode="HTML",
        reply_markup=back_button_kb(),
    )


@dp.message(Command("news"))
async def cmd_news(message: Message) -> None:
    news = await database.get_recent_news(limit=10)
    if not news:
        await message.answer(
            "📭 Пока нет сохранённых новостей.",
            reply_markup=back_button_kb(),
        )
        return

    lines = ["📰 <b>Последние новости:</b>\n"]
    for i, item in enumerate(news, 1):
        title = item["title"]
        link = item["link"]

        # Check if news is from Serbian source and translate
        if any(src in link for src in config.SERBIAN_NEWS_SOURCES):
            lang = detect_language(title)
            if lang == "serbian":
                title = await translate_to_russian(title)

        lines.append(f"{i}. {telegram_link(link, title)}")

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=back_button_kb(),
    )


@dp.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    await message.answer("⏳ Собираю новости...")
    sent = await send_personal_digest(message.chat.id)
    await message.answer(
        "✅ Сводка отправлена!" if sent else "📭 Новых новостей пока нет.",
        reply_markup=main_menu_kb(),
    )


# --- Callback handlers (inline buttons) ---

@dp.callback_query(F.data == "cmd_start")
async def cb_start(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        SIMPLE_MENU_TEXT,
        reply_markup=main_menu_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "cmd_help")
async def cb_help(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🤖 <b>Что я умею:</b>\n\n"
        "📋 <b>Сводка</b> — ежедневная подборка новостей о Сербии\n"
        "📰 <b>Новости</b> — последние новости из базы\n"
        "📊 <b>Статус</b> — статус бота и статистика\n"
        "🏠 <b>Недвижимость</b> — подбор объявлений по городу и фильтрам\n"
        "💬 <b>Вопрос</b> — просто напишите текст и получите ответ от ИИ\n\n"
        "<b>Примеры вопросов:</b>\n"
        "• Как получить ВНЖ в Сербии?\n"
        "• Какие школы есть в Белграде?\n"
        "• Сколько стоит аренда квартиры?\n"
        "• Как открыть банковский счёт?\n"
        "• Какие налоги в Сербии?\n"
        "• Какие документы нужны для переезда?\n"
        "• Есть ли работа для русских?\n"
        "• Какая медицина в Сербии?",
        parse_mode="HTML",
        reply_markup=back_button_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "cmd_status")
async def cb_status(callback: CallbackQuery) -> None:
    stats = await database.get_stats()
    now = datetime.now(MOSCOW_TZ)
    await callback.message.edit_text(
        "📊 <b>Статус бота:</b>\n\n"
        f"✅ Бот работает\n"
        f"🕐 Текущее время: {now.strftime('%H:%M %d.%m.%Y')} (МСК)\n"
        f"📰 Всего новостей отправлено: {stats['total']}\n"
        f"📅 За сегодня: {stats['today']}\n"
        f"⏰ Последняя отправка: {stats['last_sent'] or 'нет'}\n"
        f"🔔 Следующая сводка: в 18:00 МСК",
        parse_mode="HTML",
        reply_markup=back_button_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "cmd_news")
async def cb_news(callback: CallbackQuery) -> None:
    news = await database.get_recent_news(limit=10)
    if not news:
        await callback.message.edit_text(
            "📭 Пока нет сохранённых новостей.",
            reply_markup=back_button_kb(),
        )
        await callback.answer()
        return
    lines = ["📰 <b>Последние новости:</b>\n"]
    for i, item in enumerate(news, 1):
        title = item["title"]
        link = item["link"]

        # Check if news is from Serbian source and translate
        if any(src in link for src in config.SERBIAN_NEWS_SOURCES):
            lang = detect_language(title)
            if lang == "serbian":
                title = await translate_to_russian(title)

        lines.append(f"{i}. {telegram_link(link, title)}")

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=back_button_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "cmd_digest")
async def cb_digest(callback: CallbackQuery) -> None:
    await callback.message.edit_text("⏳ Собираю новости...")
    await callback.answer()
    sent = await send_personal_digest(callback.message.chat.id)
    await callback.message.edit_text(
        "✅ Сводка отправлена!" if sent else "📭 Новых новостей пока нет.",
        reply_markup=main_menu_kb(),
    )


# --- Real estate menu callbacks ---

async def _send_real_estate_listing(message: Message, row: dict, idx: int) -> None:
    """Send one listing as a photo (with a text fallback if the photo fails).

    All fields come from scraped listing pages (real_estate_listings table)
    — external input, escaped before going into an HTML message.
    """
    title = telegram_text(row["title"])
    price = telegram_text(row["price"]) if row["price"] else "не указана"
    location = telegram_text(row["location"]) if row["location"] else "не указано"
    link = telegram_link(row["url"], "Подробнее")
    caption = (
        f"<b>📌 {idx}. {title}</b>\n"
        f"💰 Цена: {price}\n"
        f"📍 {location}\n"
        f"🔗 {link}"
    )
    if row["image_url"]:
        try:
            photo = URLInputFile(row["image_url"])
            await message.answer_photo(photo=photo, caption=caption, parse_mode="HTML")
            return
        except Exception as exc:
            logger.warning("Failed to send real estate photo: %s", exc)
    await message.answer(caption, parse_mode="HTML", disable_web_page_preview=True)


@dp.callback_query(F.data == "re_menu")
async def cb_real_estate_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🏠 <b>Недвижимость в Сербии</b>\n\nВыберите город:",
        parse_mode="HTML",
        reply_markup=real_estate_city_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("re_city:"))
async def cb_real_estate_city(callback: CallbackQuery) -> None:
    city = callback.data.split(":", 1)[1]
    # .get(..., city) falls back to the raw callback_data value, which is
    # technically attacker-controllable (Telegram doesn't cryptographically
    # bind callback_data to an actual button press) — escape the fallback.
    label = REAL_ESTATE_CITY_LABELS.get(city, telegram_text(city))
    await callback.message.edit_text(
        f"🏠 <b>Недвижимость — {label}</b>\n\nЧто вас интересует?",
        parse_mode="HTML",
        reply_markup=real_estate_deal_kb(city),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("re_deal:"))
async def cb_real_estate_deal(callback: CallbackQuery) -> None:
    _, city, deal = callback.data.split(":", 2)
    await callback.answer()
    # Default filters: no price sort, newest first — same starting point
    # as the old single-choice menu's "newest" option.
    await _show_real_estate_results(callback, city, deal, price_dir="-", date_dir="n")


@dp.callback_query(F.data.startswith("re_sort:"))
async def cb_real_estate_sort(callback: CallbackQuery) -> None:
    _, city, deal, price_dir, date_dir = callback.data.split(":", 4)
    await callback.answer()
    await _show_real_estate_results(callback, city, deal, price_dir, date_dir)


def _filters_summary_text(price_dir: str, date_dir: str) -> str:
    parts = []
    if price_dir == "a":
        parts.append("дешевле → дороже")
    elif price_dir == "d":
        parts.append("дороже → дешевле")
    parts.append("сначала старые" if date_dir == "o" else "сначала новые")
    return ", ".join(parts)


async def _show_real_estate_results(
    callback: CallbackQuery, city: str, deal: str, price_dir: str, date_dir: str,
) -> None:
    """Shared by the deal-type step (default filters) and every filter
    toggle — re-queries and re-renders the listing list for the given
    filter combination."""
    city_label = REAL_ESTATE_CITY_LABELS.get(city, telegram_text(city))
    deal_label = REAL_ESTATE_DEAL_LABELS.get(deal, telegram_text(deal))
    filters_label = _filters_summary_text(price_dir, date_dir)
    kb = real_estate_filters_kb(city, deal, price_dir, date_dir)

    rows = await database.get_real_estate_listings_filtered(
        city=city, deal_type=deal, price_dir=price_dir, date_dir=date_dir, limit=10,
    )

    if not rows:
        await callback.message.edit_text(
            f"🏠 <b>{city_label} — {deal_label}</b>\n\n"
            "📭 Пока нет собранных объявлений под эти фильтры — сборщик "
            "обновляет базу каждые 6 часов. Попробуйте другой город, "
            "тип сделки или снимите часть фильтров.",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    await callback.message.edit_text(
        f"🏠 <b>{city_label} — {deal_label}</b>\n{filters_label}\n\nНайдено: {len(rows)}",
        parse_mode="HTML",
    )

    for i, row in enumerate(rows, 1):
        await _send_real_estate_listing(callback.message, row, i)

    await callback.message.answer(
        "Фильтры (можно сочетать):",
        reply_markup=kb,
    )


# --- Settings (news topics) callbacks ---

@dp.callback_query(F.data == "settings_menu")
async def cb_settings_menu(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    await callback.message.edit_text(
        "⚙️ <b>Настройки дайджеста</b>\n\n"
        "Выберите темы, которые хотите получать в ежедневной сводке "
        "(нажмите, чтобы включить/выключить):",
        parse_mode="HTML",
        reply_markup=await settings_topics_kb(user_id),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("topic_toggle:"))
async def cb_topic_toggle(callback: CallbackQuery) -> None:
    topic = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    enabled = await database.toggle_topic(user_id, topic)
    label = config.NEWS_CATEGORIES.get(topic, {}).get("label", topic)
    await callback.message.edit_reply_markup(reply_markup=await settings_topics_kb(user_id))
    await callback.answer(f"{label}: {'включено' if enabled else 'выключено'}")


# --- Persistent menu button ---
# Registered before the F.text catch-all below so this exact match wins.

@dp.message(F.text == OPEN_MENU_TEXT)
async def cmd_open_menu(message: Message) -> None:
    await message.answer(SIMPLE_MENU_TEXT, reply_markup=main_menu_kb())


# --- Text message handler (GigaChat) ---

@dp.message(F.text)
async def handle_question(message: Message) -> None:
    user_text = message.text.strip()
    if not user_text:
        return

    user_id = message.from_user.id
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Save user message to conversation history
    await database.save_message(user_id, "user", user_text)

    # Check if the query is about real estate
    real_estate_type = is_real_estate_query(user_text)
    answer = ""  # Initialize answer variable

    if real_estate_type == "listings":
        # User wants to see actual property listings.
        # This branch fully handles its own replies (one message per listing),
        # so it saves history and returns early instead of falling through to
        # the generic answer-sending code at the end of the handler.
        await message.answer("🔍 Ищу актуальные предложения недвижимости в Сербии...")

        try:
            listings, is_predefined = await search_real_estate_with_fallback(user_text)

            if not listings:
                answer = "📋 <b>Популярные сайты для поиска недвижимости:</b>\n"
                answer += "• <a href=\"https://cityexpert.rs/prodaja-nekretnina/beograd\">CityExpert.rs</a>\n"
                answer += "• <a href=\"https://www.avito.ru/all/serbiya/nedvizhimost\">Авито</a>\n"
                await message.answer(answer, parse_mode="HTML", disable_web_page_preview=True)
            else:
                if is_predefined:
                    await message.answer(
                        "⚠️ Не удалось получить свежие данные с сайтов. Ниже — примерные "
                        "варианты, актуальность цен и наличие не гарантированы, "
                        "проверяйте по ссылке.",
                    )

                # Send first listing with photo if available
                for i, listing in enumerate(listings[:5]):
                    price = telegram_text(listing.price) if listing.price else "не указана"
                    location = telegram_text(listing.location) if listing.location else "не указана"
                    caption = (
                        f"<b>📌 {i+1}. {telegram_text(listing.title)}</b>\n"
                        f"💰 Цена: {price}\n"
                        f"📍 Локация: {location}\n"
                        f"🌐 Источник: {telegram_text(listing.source)}\n"
                        f"🔗 {telegram_link(listing.url, 'Подробнее')}"
                    )

                    if listing.image_url:
                        try:
                            photo = URLInputFile(listing.image_url)
                            await message.answer_photo(
                                photo=photo,
                                caption=caption,
                                parse_mode="HTML",
                            )
                        except Exception as photo_exc:
                            logger.warning("Failed to send photo: %s", photo_exc)
                            await message.answer(caption, parse_mode="HTML", disable_web_page_preview=True)
                    else:
                        await message.answer(caption, parse_mode="HTML", disable_web_page_preview=True)

                answer = f"Показано предложений недвижимости: {min(len(listings), 5)}."
                if is_predefined:
                    answer += " (примерные варианты, не живые данные)"

        except Exception as exc:
            logger.error("Real estate search error: %s", exc)
            answer = "⚠️ Ошибка при поиске недвижимости. Попробуйте позже."
            await message.answer(answer)

        await database.save_message(user_id, "assistant", answer)
        return

    elif real_estate_type == "info":
        # User wants information about real estate process/features
        await message.answer("🔍 Ищу актуальную информацию...")
        try:
            answer = await get_serbia_answer(user_text)
        except Exception as exc:
            logger.error("Serbia search error: %s", exc)
            answer = "⚠️ Ошибка при поиске информации. Попробуйте позже."

    else:
        # Check if the query is about Serbia
        query_lower = user_text.lower()
        is_serbia_query = any(kw in query_lower for kw in [
            "сербия", "сербск", "белград", "belgrade", "serbia",
            "виза", "внж", "пмж", "гражданств",
            "недвижим", "квартир", "дом", "аренд",
            "работа", "трудоустро", "образован", "школ",
            "банк", "налог", "документ",
        ])

        if is_serbia_query:
            # Search web for information about Serbia with source links
            await message.answer("🔍 Ищу актуальную информацию...")
            try:
                answer = await get_serbia_answer(user_text)
            except Exception as exc:
                logger.error("Serbia search error: %s", exc)
                answer = "⚠️ Ошибка при поиске информации. Попробуйте позже."
        else:
            # Use GigaChat for other questions (without web search)
            # Get conversation history for context. save_message(user_id,
            # "user", user_text) already ran above (top of this handler),
            # so `history`'s last entry IS this question — do not append
            # user_text again here, or the model sees it twice.
            history = await database.get_conversation_history(user_id, limit=10)

            # Build messages with history
            messages = [Messages(role="system", content=get_system_prompt())]
            for msg in history:
                messages.append(Messages(role=msg["role"], content=msg["content"]))

            try:
                giga = get_gigachat_client()
                if not giga:
                    raise Exception("GigaChat client not available")
                chat = Chat(model="GigaChat", messages=messages)
                response = await giga.achat(chat)
                # Free-form model output — escape before it goes into the
                # HTML-parse-mode message sent below.
                answer = telegram_text(response.choices[0].message.content)
            except Exception as exc:
                logger.error("GigaChat error: %s", exc)
                answer = "⚠️ Ошибка при обращении к ИИ. Попробуйте позже."

    # Save bot response to conversation history
    await database.save_message(user_id, "assistant", answer)

    await message.answer(answer, reply_markup=main_menu_kb(), parse_mode="HTML", disable_web_page_preview=True)


# --- Voice message handler ---

async def transcribe_voice(voice_file_id: str) -> str | None:
    """Download voice message, convert OGG to WAV, and transcribe."""
    ogg_path = None
    wav_path = None
    try:
        file = await bot.get_file(voice_file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            await bot.download_file(file.file_path, tmp.name)
            ogg_path = tmp.name

        wav_path = ogg_path.replace(".ogg", ".wav")
        # Use async subprocess to avoid blocking the event loop. A hung
        # ffmpeg process (corrupt input, stalled I/O) must not hang the
        # handler forever — bound it with a timeout.
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            logger.error("ffmpeg conversion timed out")
            proc.kill()
            await proc.wait()
            return None
        if proc.returncode != 0:
            logger.error("ffmpeg conversion failed with code %d", proc.returncode)
            return None

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio = recognizer.record(source)

        # recognize_google() makes a blocking network call — run it off
        # the event loop so one slow/stuck transcription doesn't stall
        # everything else sharing this loop (polling, other users,
        # scheduled jobs).
        text = await asyncio.to_thread(recognizer.recognize_google, audio, language="ru-RU")
        return text
    except sr.UnknownValueError:
        return None
    except Exception as exc:
        logger.error("Voice transcription error: %s", exc)
        return None
    finally:
        for p in (ogg_path, wav_path):
            if p and os.path.exists(p):
                os.remove(p)


@dp.message(F.voice)
async def handle_voice(message: Message) -> None:
    """Process voice messages: transcribe and answer via GigaChat."""
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await message.answer("🎤 Распознаю голос...")

    text = await transcribe_voice(message.voice.file_id)
    if not text:
        await message.answer(
            "⚠️ Не удалось распознать голос. Попробуйте ещё раз или напишите текст.",
            reply_markup=main_menu_kb(),
        )
        return

    await message.answer(f"🎤 <i>Вы сказали:</i> {telegram_text(text)}", parse_mode="HTML")

    user_id = message.from_user.id

    # Save user message to conversation history
    await database.save_message(user_id, "user", text)

    # Get conversation history for context
    history = await database.get_conversation_history(user_id, limit=10)

    # Build messages with history
    messages = [Messages(role="system", content=get_system_prompt())]
    for msg in history:
        messages.append(Messages(role=msg["role"], content=msg["content"]))

    try:
        giga = get_gigachat_client()
        if not giga:
            raise Exception("GigaChat client not available")
        chat = Chat(model="GigaChat", messages=messages)
        response = await giga.achat(chat)
        answer = response.choices[0].message.content
    except Exception as exc:
        logger.error("GigaChat error: %s", exc)
        answer = "⚠️ Ошибка при обращении к ИИ. Попробуйте позже."

    # Save bot response to conversation history
    await database.save_message(user_id, "assistant", answer)

    await message.answer(answer, reply_markup=main_menu_kb())


# --- Main ---

async def _initial_real_estate_collection() -> None:
    """One-off startup job — see the DateTrigger job below for why this
    isn't just awaited directly in main()."""
    logger.info("Collecting real estate listings...")
    try:
        count = await refresh_real_estate_database()
        logger.info("Real estate collector: %d listings on startup.", count)
    except Exception as exc:
        logger.error("Real estate collector failed on startup: %s", exc, exc_info=True)


async def _initial_digest() -> None:
    """One-off startup job — see the DateTrigger job below for why this
    isn't just awaited directly in main()."""
    logger.info("Sending initial digest now...")
    await send_daily_digest()


async def main() -> None:
    await database.init_db()
    logger.info("Database initialized.")

    # Long polling and webhooks are mutually exclusive on Telegram's side —
    # drop any webhook left over from a previous deployment/test so
    # getUpdates() doesn't fail with TelegramConflictError. A transient
    # network hiccup here must not crash the whole process (systemd would
    # restart it, but there's no reason to pay that cost) — if this fails,
    # dp.start_polling() below will just hit TelegramConflictError and
    # retry with its own backoff, same as if no webhook was set at all.
    #
    # drop_pending_updates=False: a restart (deploy, crash, systemd bump)
    # must not silently discard messages users sent while the bot was down.
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as exc:
        logger.warning("delete_webhook failed (will rely on polling's own retry): %s", exc)

    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
    scheduler.add_job(
        send_daily_digest,
        trigger=CronTrigger(hour=10, minute=0, timezone=config.TIMEZONE),
        id="daily_digest",
        replace_existing=True,
    )
    scheduler.add_job(
        send_daily_digest,
        trigger=CronTrigger(hour=18, minute=0, timezone=config.TIMEZONE),
        id="evening_digest",
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_real_estate_database,
        trigger=IntervalTrigger(hours=6),
        id="real_estate_collector",
        replace_existing=True,
    )
    # Startup work (scraping 8 real-estate pages, collecting/translating the
    # digest) used to run inline here, before dp.start_polling() — in the
    # worst case that left the bot not responding to anyone for minutes
    # after process start. Scheduling both as one-off "run now" jobs lets
    # them run concurrently with polling instead of blocking it.
    now = datetime.now(MOSCOW_TZ)
    scheduler.add_job(
        _initial_real_estate_collection,
        trigger=DateTrigger(run_date=now),
        id="initial_real_estate_collection",
    )
    scheduler.add_job(
        _initial_digest,
        trigger=DateTrigger(run_date=now),
        id="initial_digest",
    )
    scheduler.start()
    logger.info("Scheduler started — next runs at 10:00 and 18:00 MSK.")

    logger.info("Starting bot polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
