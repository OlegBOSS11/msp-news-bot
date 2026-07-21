from __future__ import annotations

import asyncio
import logging
import tempfile
import os
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
from gigachat import GigaChatAsyncClient
from gigachat.models.chat import Chat, Messages
import speech_recognition as sr

import config
import database
from parser import collect_news

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MOSCOW_TZ = timezone(timedelta(hours=3))

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

giga = GigaChatAsyncClient(
    credentials=config.GIGACHAT_CREDENTIALS,
    verify_ssl_certs=False,
)

SYSTEM_PROMPT = """Ты — эксперт по малому и среднему бизнесу (МСП) в России.

Ты работаешь в Telegram-боте для МСП. Вот что бот умеет:
- /digest — получить сводку новостей для МСП за сегодня
- Автоматическая ежедневная рассылка новостей в 10:00 и 18:00 МСК
- Ответы на вопросы по МСП, налогам, законам, грантам, субсидиям

Отвечай на вопросы пользователей о:
- Законах и НПА, регулирующих МСП (44-ФЗ, 223-ФЗ, налоговое законодательство)
- Субсидиях, грантах и поддержке для малого бизнеса
- Налоговых льготах и проверках
- Госзакупках и тендерах
- Самозанятости и упрощённой системе налогообложения
- Функциях и возможностях этого бота

Отвечай кратко, по делу, на русском языке. Если не знаешь точный ответ — скажи об этом."""


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

def _format_news_block(idx: int, item: dict) -> str:
    return (
        f"<b>📌 {idx}. {item['title']}</b>\n"
        f"<i>Источник: {item['source']}</i>\n"
        f"{item['summary']}\n"
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
        f"📊 <b>Ежедневная сводка для МСП на {today}.</b>\n"
        f"Проверенные источники.\n"
    )

    blocks = [_format_news_block(i + 1, item) for i, item in enumerate(fresh)]
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
    "Я — бот для малого и среднего бизнеса (МСП).\n"
    "Автоматически собираю новости и отвечаю на вопросы.\n\n"
    "📌 <b>Что умеют кнопки:</b>\n\n"
    "📋 <b>Сводка</b> — ежедневная подборка новостей для МСП\n"
    "📰 <b>Новости</b> — последние новости из базы\n"
    "📊 <b>Статус</b> — проверить, работает ли бот\n"
    "❓ <b>Помощь</b> — справка по всем функциям\n\n"
    "💬 Просто напишите текст — и я отвечу с помощью ИИ!\n\n"
    "🕐 Сводка приходит автоматически в <b>10:00</b> и <b>18:00</b> МСК"
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
        "📋 /digest — получить сводку новостей для МСП\n"
        "📰 /news — последние новости из базы\n"
        "📊 /status — статус бота и статистика\n"
        "💬 Задать вопрос — отвечу по МСП, налогам, законам\n\n"
        "<b>Примеры вопросов:</b>\n"
        "• Что такое МСП?\n"
        "• Какие субсидии доступны для малого бизнеса?\n"
        "• Что такое 44-ФЗ?\n"
        "• Как оформить грант на бизнес?\n"
        "• Какие проверки ждут ИП?",
        parse_mode="HTML",
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
        lines.append(f'{i}. <a href="{item["link"]}">{item["title"]}</a>')
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
        "📋 <b>Сводка</b> — ежедневная подборка новостей для МСП\n"
        "📰 <b>Новости</b> — последние новости из базы\n"
        "📊 <b>Статус</b> — статус бота и статистика\n"
        "💬 <b>Вопрос</b> — просто напишите текст и получите ответ от ИИ\n\n"
        "<b>Примеры вопросов:</b>\n"
        "• Что такое МСП?\n"
        "• Какие субсидии доступны для малого бизнеса?\n"
        "• Что такое 44-ФЗ?\n"
        "• Как оформить грант на бизнес?\n"
        "• Какие проверки ждут ИП?",
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
        lines.append(f'{i}. <a href="{item["link"]}">{item["title"]}</a>')
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

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        chat = Chat(
            model="GigaChat",
            messages=[
                Messages(role="system", content=SYSTEM_PROMPT),
                Messages(role="user", content=user_text),
            ],
        )
        response = await giga.achat(chat)
        answer = response.choices[0].message.content
    except Exception as exc:
        logger.error("GigaChat error: %s", exc)
        answer = "⚠️ Ошибка при обращении к ИИ. Попробуйте позже."

    await message.answer(answer, reply_markup=main_menu_kb())


# --- Voice message handler ---

import subprocess

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
        subprocess.run(
            ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path],
            capture_output=True,
            check=True,
        )

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

    try:
        chat = Chat(
            model="GigaChat",
            messages=[
                Messages(role="system", content=SYSTEM_PROMPT),
                Messages(role="user", content=text),
            ],
        )
        response = await giga.achat(chat)
        answer = response.choices[0].message.content
    except Exception as exc:
        logger.error("GigaChat error: %s", exc)
        answer = "⚠️ Ошибка при обращении к ИИ. Попробуйте позже."

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
