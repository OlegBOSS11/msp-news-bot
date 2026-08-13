import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
CHAT_ID: int = int(os.environ["CHAT_ID"])
GIGACHAT_CREDENTIALS: str = os.environ["GIGACHAT_CREDENTIALS"]

# Optional: route only Telegram Bot API traffic (getUpdates/sendMessage/etc.)
# through a SOCKS5 proxy — e.g. a small VPS in the EU, to work around
# intermittent DPI-level interference on HTTPS to api.telegram.org from the
# RU-hosted VDS. Leave unset to talk to Telegram directly (default).
# Format: socks5://user:password@host:port
TELEGRAM_PROXY_URL: str | None = os.environ.get("TELEGRAM_PROXY_URL") or None

# --- Serbian news sources (for translation) ---
SERBIAN_NEWS_SOURCES: list[str] = [
    "n1info.rs", "blic.rs", "telegraf.rs", "b92.net", "kurir.rs", "danas.rs", "nova.rs",
]

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
    "danas.rs",
    "nova.rs",
    # Русскоязычные источники о Сербии
    "serbiarus.com",
    "rsmedia.ru",
    "ruserbia.com",
    "russian.rs",  # Русская диаспора Сербии — гайды и новости по релокации
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
    {
        "name": "danas.rs",
        "url": "https://danas.rs/feed/",
    },
    {
        "name": "nova.rs",
        "url": "https://nova.rs/feed/",
    },
    # --- Русскоязычные источники о Сербии ---
    {
        "name": "ruserbia.com",
        "url": "https://ruserbia.com/feed/",
    },
    {
        "name": "russian.rs",
        "url": "https://russian.rs/feed/",
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
# Applied to RAW feed text (parser._score(), before translation) — a
# Serbian-language article about e.g. visas never gets translated at all
# unless it scores > 0 here first, so every category below needs Serbian
# terms alongside the Russian ones, not just real estate (which already
# had them). Missing this meant Serbian-source articles about visas/ВНЖ/
# taxes/work/education silently never reached anyone, no matter how
# relevant, while real-estate articles from the same sources sailed
# through — verified: "Nove vize za strane radnike u Srbiji" scored 0,
# "Iznajmljivanje stanova u Beogradu" scored 2, from the same outlet.
MSP_KEYWORDS: list[str] = [
    # Сербия и релокация (русские термины)
    "сербия",
    "сербск",
    "релокац",
    "переезд",
    "миграц",
    "виза",
    "внж",
    "пмж",
    "гражданств",
    # Сербия и релокация (сербские термины)
    "srbij",
    "viz",  # viza/vize/vizu
    "borav",  # boravak/boravka/boravište/boravišni — case-inflection-safe
    "državljanstv",
    "stran",  # stranci/strancu/stranaca — plural genitive drops the "c",
              # a normal Slavic vowel/consonant alternation "stranci"→"stranaca"
    "iselj",  # iseljenje — emigration
    # Работа (русские термины)
    "работа",
    "трудоустро",
    "зарплат",
    "резюме",
    "ваканси",
    "работодател",
    # Работа (сербские термины)
    "posao",
    "poslovi",
    "zaposlenj",
    "radno mesto",
    "radnik",
    "plata",
    "poslodavac",
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
    # Образование (русские термины)
    "образован",
    "школ",
    "университет",
    "учеб",
    "детск",
    # Образование (сербские термины)
    "škol",
    "univerzitet",
    "obrazovanj",
    "vrtić",  # детский сад
    "student",
    # Финансы (русские термины)
    "банк",
    "налог",
    "счет",
    "перевод",
    "валют",
    # Финансы (сербские термины)
    "bank",
    "porez",
    "račun",
    "valut",
    # Документы (русские термины)
    "документ",
    "паспорт",
    "регистрац",
    "справк",
    # Документы (сербские термины)
    "dokument",
    "pasoš",
    "registracij",
    "potvrd",  # potvrda — справка
    "dozvola",  # разрешение/справка (шире, чем dozvola za boravak выше)
    # Новости (русские термины)
    "новост",
    "закон",
    "изменен",
    "ограничен",
    # Новости (сербские термины)
    "zakon",
    "izmen",  # izmena/izmene — изменения
    "ograničenj",
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
            "srbij", "viz", "borav", "državljanstv", "stran", "iselj",
        ],
    },
    "work": {
        "label": "💼 Работа",
        "keywords": [
            "работа", "трудоустро", "зарплат", "резюме", "ваканси", "работодател",
            "posao", "poslovi", "zaposlenj", "radno mesto", "radnik", "plata", "poslodavac",
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
        "keywords": [
            "образован", "школ", "университет", "учеб", "детск",
            "škol", "univerzitet", "obrazovanj", "vrtić", "student",
        ],
    },
    "finance": {
        "label": "💰 Финансы и налоги",
        "keywords": ["банк", "налог", "счет", "перевод", "валют", "bank", "porez", "račun", "valut"],
    },
    "documents": {
        "label": "📄 Документы",
        "keywords": [
            "документ", "паспорт", "регистрац", "справк",
            "dokument", "pasoš", "registracij", "potvrd", "dozvola",
        ],
    },
    "general": {
        "label": "📰 Общие новости",
        "keywords": [],  # catch-all for items matching no other category
    },
}

MAX_NEWS: int = 15

# Moscow timezone name
TIMEZONE: str = "Europe/Moscow"
