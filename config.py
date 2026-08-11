import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
CHAT_ID: int = int(os.environ["CHAT_ID"])
GIGACHAT_CREDENTIALS: str = os.environ["GIGACHAT_CREDENTIALS"]

# --- Serbian news sources (for translation) ---
SERBIAN_NEWS_SOURCES: list[str] = ["n1info.rs", "blic.rs", "telegraf.rs", "b92.net", "kurir.rs"]

# --- Whitelist domains (only these are parsed) ---
WHITELIST_DOMAINS: set[str] = {
    # Сербские источники
    "rsponline.rs",
    "blic.rs",
    "novosti.rs",
    "politika.rs",
    "n1info.rs",
    "b92.net",
    "tanjug.rs",
    "serbia.travel",
    "investserbia.org",
    "telegraf.rs",
    "kurir.rs",
    # Русскоязычные источники о Сербии
    "serbiarus.com",
    "rsmedia.ru",
    "ruserbia.com",
    # Международные источники
    "reuters.com",
    "bbc.com",
    "bbci.co.uk",  # BBC's actual RSS feeds are served from this domain
    "euronews.com",
    # Российские источники (для сравнения/контекста)
    "rbc.ru",
    "kommersant.ru",
    "vedomosti.ru",
    "tass.ru",
    "ria.ru",
    "radiosputnik.ru",
    "rg.ru",
}

# --- RSS / Atom feeds to poll ---
# Only feeds whose netloc is in WHITELIST_DOMAINS.
# Updated for Serbian relocation news (July 2026)
RSS_FEEDS: list[dict[str, str]] = [
    # --- Сербские новости ---
    {
        "name": "n1info.rs",
        "url": "https://n1info.rs/feed/",
    },
    {
        "name": "blic.rs",
        "url": "https://www.blic.rs/rss",
    },
    {
        "name": "telegraf.rs",
        "url": "https://www.telegraf.rs/rss",
    },
    {
        "name": "kurir.rs",
        "url": "https://www.kurir.rs/rss/",
    },
    # --- Русскоязычные источники о Сербии ---
    {
        "name": "ruserbia.com",
        "url": "https://ruserbia.com/feed/",
    },
    # --- Международные источники ---
    {
        "name": "euronews.com",
        "url": "https://ru.euronews.com/rss",
    },
    {
        "name": "bbci.co.uk",
        "url": "http://feeds.bbci.co.uk/russian/rss.xml",
    },
    # --- Российские источники (контекст) ---
    {
        "name": "tass.ru",
        "url": "https://tass.ru/rss/v2.xml",
    },
    {
        "name": "ria.ru",
        "url": "https://ria.ru/export/rss2/archive/index.xml",
    },
    {
        "name": "radiosputnik.ru",
        "url": "https://radiosputnik.ru/export/rss2/archive/index.xml",
    },
    {
        "name": "rg.ru",
        "url": "https://rg.ru/xml/index.xml",
    },
]

# --- MSP keywords for relevance filtering ---
MSP_KEYWORDS: list[str] = [
    # Сербия и релокация
    "сербия",
    "сербск",
    "релокац",
    "переезд",
    "миграц",
    "виза",
    "внж",
    "пмж",
    "гражданств",
    # Работа
    "работа",
    "трудоустро",
    "зарплат",
    "резюме",
    "ваканси",
    "работодател",
    # Недвижимость (русские термины)
    "недвижим",
    "аренд",
    "квартир",
    "дом",
    "покупк",
    "продаж",
    "жилье",
    "жилая",
    "квартал",
    "район",
    "ремонт",
    "строительств",
    # Недвижимость (сербские термины)
    "nekretnine",
    "stan",
    "kuća",
    "iznajmljivanje",
    "prodaja",
    "kupovina",
    "gradnja",
    "adaptacija",
    "enterijer",
    "eksterijer",
    # Образование
    "образован",
    "школ",
    "университет",
    "учеб",
    "детск",
    # Финансы
    "банк",
    "налог",
    "счет",
    "перевод",
    "валют",
    # Документы
    "документ",
    "паспорт",
    "регистрац",
    "справк",
    # Новости
    "новост",
    "закон",
    "изменен",
    "ограничен",
]

# --- News categories for the personalized digest ---
# Each item in the digest is tagged with every category whose keywords
# match its title/summary (an item can belong to more than one); items
# matching none of these fall into "general". Users pick which categories
# they want to receive via /settings — see bot.py's topic toggle menu.
NEWS_CATEGORIES: dict[str, dict] = {
    "serbia": {
        "label": "🇷🇸 Сербия и релокация",
        "keywords": [
            "сербия", "сербск", "релокац", "переезд", "миграц",
            "виза", "внж", "пмж", "гражданств",
        ],
    },
    "work": {
        "label": "💼 Работа",
        "keywords": [
            "работа", "трудоустро", "зарплат", "резюме", "ваканси", "работодател",
        ],
    },
    "realestate": {
        "label": "🏠 Недвижимость",
        "keywords": [
            "недвижим", "аренд", "квартир", "дом", "покупк", "продаж",
            "жилье", "жилая", "строительств",
            "nekretnine", "stan", "kuća", "iznajmljivanje", "prodaja", "kupovina",
        ],
    },
    "education": {
        "label": "🎓 Образование",
        "keywords": ["образован", "школ", "университет", "учеб", "детск"],
    },
    "finance": {
        "label": "💰 Финансы и налоги",
        "keywords": ["банк", "налог", "счет", "перевод", "валют"],
    },
    "documents": {
        "label": "📄 Документы",
        "keywords": ["документ", "паспорт", "регистрац", "справк"],
    },
    "general": {
        "label": "📰 Общие новости",
        "keywords": [],  # catch-all for items matching no other category
    },
}

MAX_NEWS: int = 15

# Moscow timezone name
TIMEZONE: str = "Europe/Moscow"
