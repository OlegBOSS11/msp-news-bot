from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from gigachat.models.chat import Chat, Messages
import speech_recognition as sr

import config
import database
from gigachat_client import get_gigachat_client
from parser import collect_news
from real_estate import search_real_estate_with_fallback, format_listings, is_real_estate_query
from serbia_search import get_serbia_answer
from translator import translate_to_russian, detect_language

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MOSCOW_TZ = timezone(timedelta(hours=3))

bot = Bot(token=config.BOT_TOKEN)
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
    ])


def back_button_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="cmd_start")],
    ])


# --- News formatting ---

async def _format_news_block(idx: int, item: dict) -> str:
    """Format a news block, translating Serbian news to Russian."""
    title = item['title']
    summary = item.get('summary', '')
    source = item['source']

    # Check if news is from Serbian source and needs translation
    if source in config.SERBIAN_NEWS_SOURCES:
        # Detect language and translate if needed
        lang = detect_language(title)
        if lang == "serbian":
            title = await translate_to_russian(title)
        lang = detect_language(summary)
        if lang == "serbian":
            summary = await translate_to_russian(summary)

    return (
        f"<b>📌 {idx}. {title}</b>\n"
        f"<i>Источник: {source}</i>\n"
        f"{summary}\n"
        f'🔗 <a href="{item["link"]}">Читать далее</a>'
    )


async def send_daily_digest() -> None:
    logger.info("Starting daily digest collection...")
    try:
        news = await collect_news()
    except Exception as exc:
        logger.error("Failed to collect news: %s", exc, exc_info=True)
        return

    fresh: list[dict] = []
    for item in news:
        try:
            if not await database.is_sent(item["link"]):
                fresh.append(item)
        except Exception as exc:
            logger.warning("DB check failed for %s: %s", item["link"], exc)
            fresh.append(item)

    if not fresh:
        logger.info("No new MSP news found for today.")
        return

    today = datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y")
    header = (
        f"📊 <b>Ежедневная сводка о Сербии на {today}.</b>\n"
        f"Актуальные новости для релокации.\n"
    )

    # Format blocks with translation (async)
    blocks = []
    for i, item in enumerate(fresh):
        block = await _format_news_block(i + 1, item)
        blocks.append(block)

    body = "\n\n".join(blocks)
    footer = f"\n\n<b>Всего новостей: {len(fresh)}</b>"

    message_text = header + "\n\n" + body + footer

    max_len = 4000
    parts: list[str] = []
    if len(message_text) <= max_len:
        parts = [message_text]
    else:
        current = header
        for block in blocks:
            candidate = current + "\n\n" + block
            if len(candidate) > max_len:
                parts.append(current)
                current = block
            else:
                current = candidate
        current += footer
        parts.append(current)

    for part in parts:
        try:
            await bot.send_message(
                chat_id=config.CHAT_ID,
                text=part,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.error("Failed to send message: %s", exc, exc_info=True)

    for item in fresh:
        try:
            await database.mark_sent(item["link"], item["title"])
        except Exception as exc:
            logger.warning("DB mark_sent failed: %s", exc)

    logger.info("Digest sent successfully (%d items).", len(fresh))


# --- Welcome message ---

WELCOME_TEXT = (
    "👋 <b>Добро пожаловать!</b>\n\n"
    "Я — бот для помощи с релокацией из России в Сербию.\n"
    "Автоматически собираю актуальные новости и отвечаю на вопросы.\n\n"
    "📌 <b>Что умеют кнопки:</b>\n\n"
    "📋 <b>Сводка</b> — ежедневная подборка новостей о Сербии\n"
    "📰 <b>Новости</b> — последние новости из базы\n"
    "📊 <b>Статус</b> — проверить, работает ли бот\n"
    "❓ <b>Помощь</b> — справка по всем функциям\n\n"
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

        lines.append(f'{i}. <a href="{link}">{title}</a>')

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=back_button_kb(),
    )


@dp.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    await message.answer("⏳ Собираю новости...")
    await send_daily_digest()
    await message.answer(
        "✅ Сводка отправлена!",
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

        lines.append(f'{i}. <a href="{link}">{title}</a>')

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
    await send_daily_digest()
    await callback.message.edit_text(
        "✅ Сводка отправлена!",
        reply_markup=main_menu_kb(),
    )


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
            listings = await search_real_estate_with_fallback(user_text)

            if not listings:
                answer = "📋 <b>Популярные сайты для поиска недвижимости:</b>\n"
                answer += "• <a href=\"https://cityexpert.rs/prodaja-nekretnina/beograd\">CityExpert.rs</a>\n"
                answer += "• <a href=\"https://www.avito.ru/all/serbiya/nedvizhimost\">Авито</a>\n"
                await message.answer(answer, parse_mode="HTML", disable_web_page_preview=True)
            else:
                # Send first listing with photo if available
                for i, listing in enumerate(listings[:5]):
                    caption = (
                        f"<b>📌 {i+1}. {listing.title}</b>\n"
                        f"💰 Цена: {listing.price or 'не указана'}\n"
                        f"📍 Локация: {listing.location or 'не указана'}\n"
                        f"🌐 Источник: {listing.source}\n"
                        f'🔗 <a href="{listing.url}">Подробнее</a>'
                    )

                    if listing.image_url:
                        try:
                            from aiogram.types import URLInputFile
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
            # Get conversation history for context
            history = await database.get_conversation_history(user_id, limit=10)

            # Build messages with history
            messages = [Messages(role="system", content=get_system_prompt())]
            for msg in history:
                messages.append(Messages(role=msg["role"], content=msg["content"]))
            messages.append(Messages(role="user", content=user_text))

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
        # Use async subprocess to avoid blocking the event loop
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            logger.error("ffmpeg conversion failed with code %d", proc.returncode)
            return None

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio = recognizer.record(source)

        text = recognizer.recognize_google(audio, language="ru-RU")
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

    await message.answer(f"🎤 <i>Вы сказали:</i> {text}", parse_mode="HTML")

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

async def main() -> None:
    await database.init_db()
    logger.info("Database initialized.")

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
    scheduler.start()
    logger.info("Scheduler started — next runs at 10:00 and 18:00 MSK.")

    logger.info("Sending initial digest now...")
    await send_daily_digest()

    logger.info("Starting bot polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
