import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
CHAT_ID: int = int(os.environ["CHAT_ID"])
GIGACHAT_CREDENTIALS: str = os.environ["GIGACHAT_CREDENTIALS"]

# --- Whitelist domains (only these are parsed) ---
WHITELIST_DOMAINS: set[str] = {
    "pravo.gov.ru",
    "economy.gov.ru",
    "nalog.gov.ru",
    "consultant.ru",
    "garant.ru",
    "rbc.ru",
    "kommersant.ru",
    "vedomosti.ru",
}

# --- RSS / Atom feeds to poll ---
# Only feeds whose netloc is in WHITELIST_DOMAINS.
# Verified working as of 2026-07-21.
RSS_FEEDS: list[dict[str, str]] = [
    # --- Legal databases ---
    {
        "name": "garant.ru",
        "url": "https://www.garant.ru/rss/news/",
    },
    # --- Business media ---
    {
        "name": "kommersant.ru",
        "url": "https://www.kommersant.ru/rss/section-business.xml",
    },
    {
        "name": "vedomosti.ru",
        "url": "https://www.vedomosti.ru/rss/news.xml",
    },
]

# --- MSP keywords for relevance filtering ---
MSP_KEYWORDS: list[str] = [
    "мсп",
    "малый бизнес",
    "субсиди",
    "налог",
    "проверк",
    "44-фз",
    "223-фз",
    "грант",
    "ограничен",
    "малые предприятия",
    "средний бизнес",
    "микропредприят",
    "самозанят",
    "упрощенк",
    "ЕНС",
    "единый налоговый",
]

MAX_NEWS: int = 15

# Moscow timezone name
TIMEZONE: str = "Europe/Moscow"
