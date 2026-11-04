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

# Optional: translate news via Ollama Cloud's free tier
# (OpenAI-compatible API, https://ollama.com/settings/keys) instead of
# GigaChat — costs nothing while the free quota lasts. Falls back to
# GigaChat automatically (translator.py) if this isn't set or a call
# fails, so leaving it unset just means translation goes through
# GigaChat as before — nothing breaks either way.
OLLAMA_API_KEY: str | None = os.environ.get("OLLAMA_API_KEY") or None
OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "gpt-oss:20b-cloud")

MAX_NEWS: int = 15

# Moscow timezone name — digest send time is anchored to this regardless
# of which country(ies) a given run covers, since the audience reads the
# digest in Moscow-time habits regardless of where they relocated to.
TIMEZONE: str = "Europe/Moscow"

DEFAULT_COUNTRY: str = "serbia"


# --- International / Russian-context sources shared by every country ---
# These aren't about any one destination country, so every country's feed
# list includes them — but an article from one of them only reaches a
# given country's users if it also matches that country's `required_terms`
# (see parser.collect_news()), the same way it always worked for Serbia.
SHARED_INTL_FEEDS: list[dict[str, str]] = [
    {"name": "euronews.com", "url": "https://ru.euronews.com/rss"},
    {"name": "bbci.co.uk", "url": "http://feeds.bbci.co.uk/russian/rss.xml"},
]
SHARED_RU_FEEDS: list[dict[str, str]] = [
    {"name": "tass.ru", "url": "https://tass.ru/rss/v2.xml"},
    {"name": "ria.ru", "url": "https://ria.ru/export/rss2/archive/index.xml"},
    {"name": "radiosputnik.ru", "url": "https://radiosputnik.ru/export/rss2/archive/index.xml"},
    {"name": "rg.ru", "url": "https://rg.ru/xml/index.xml"},
]
SHARED_WHITELIST_DOMAINS: set[str] = {
    "reuters.com", "bbc.com", "bbci.co.uk", "euronews.com",
    "rbc.ru", "kommersant.ru", "vedomosti.ru", "tass.ru", "ria.ru",
    "radiosputnik.ru", "rg.ru",
}
# Priority bucket used to sort the digest — lower sorts first. Local
# (country-specific) sources always get bucket 0 (see COUNTRIES below);
# these are the buckets for the shared sources every country includes.
SHARED_SOURCE_PRIORITY: dict[str, int] = {
    "euronews.com": 1, "bbci.co.uk": 1, "reuters.com": 1, "bbc.com": 1,
    "tass.ru": 2, "ria.ru": 2, "radiosputnik.ru": 2, "rg.ru": 2,
}

# --- Keyword categories shared across every country (Russian terms only — ---
# a Russian-speaking user's own queries and a digest's Russian-language
# articles always match these regardless of destination country). Each
# country adds its own `local_keywords` on top (local-language terms +
# the country's own name) for relevance-scoring Serbian/Georgian/Turkish/
# Armenian/Montenegrin-language source articles that haven't been
# translated yet (parser._score() runs on the raw feed text).
NEWS_CATEGORIES: dict[str, dict] = {
    "country": {
        # No static label — formatted per-country as "{flag} {name} и
        # релокация" by get_category_label() below.
        "shared_keywords": [
            "релокац", "переезд", "миграц", "виза", "внж", "пмж", "гражданств",
        ],
    },
    "work": {
        "label": "💼 Работа",
        "shared_keywords": [
            "работа", "трудоустро", "зарплат", "резюме", "ваканси", "работодател",
        ],
    },
    "realestate": {
        "label": "🏠 Недвижимость",
        "shared_keywords": [
            "недвижим", "аренд", "квартир", "дом", "покупк", "продаж",
            "жилье", "жилая", "квартал", "район", "ремонт", "строительств",
        ],
    },
    "education": {
        "label": "🎓 Образование",
        "shared_keywords": [
            "образован", "школ", "университет", "учеб", "детск",
        ],
    },
    "finance": {
        "label": "💰 Финансы и налоги",
        "shared_keywords": ["банк", "налог", "счет", "перевод", "валют"],
    },
    "documents": {
        "label": "📄 Документы",
        "shared_keywords": [
            "документ", "паспорт", "регистрац", "справк",
        ],
    },
    "general": {
        "label": "📰 Общие новости",
        "shared_keywords": [],  # catch-all for items matching no other category
    },
}


def get_category_label(country_code: str, category_key: str) -> str:
    """Human-readable label for a category, country-aware for "country"."""
    cat = NEWS_CATEGORIES.get(category_key, {})
    if category_key == "country":
        info = COUNTRIES.get(country_code, COUNTRIES[DEFAULT_COUNTRY])
        return f"{info['flag']} {info['name']} и релокация"
    return cat.get("label", category_key)


def get_category_keywords(country_code: str, category_key: str) -> list[str]:
    """Shared (Russian) + country-local keywords for one category."""
    shared = NEWS_CATEGORIES.get(category_key, {}).get("shared_keywords", [])
    info = COUNTRIES.get(country_code, COUNTRIES[DEFAULT_COUNTRY])
    local = info["local_keywords"].get(category_key, [])
    return shared + local


def get_all_keywords(country_code: str) -> list[str]:
    """Flat list of every keyword (every category) for scoring relevance —
    the country-aware equivalent of the old flat MSP_KEYWORDS list."""
    out: list[str] = []
    for key in NEWS_CATEGORIES:
        out.extend(get_category_keywords(country_code, key))
    return out


def get_rss_feeds(country_code: str) -> list[dict[str, str]]:
    info = COUNTRIES.get(country_code, COUNTRIES[DEFAULT_COUNTRY])
    return info["local_feeds"] + SHARED_INTL_FEEDS + SHARED_RU_FEEDS


def get_whitelist_domains(country_code: str) -> set[str]:
    info = COUNTRIES.get(country_code, COUNTRIES[DEFAULT_COUNTRY])
    return info["local_domains"] | SHARED_WHITELIST_DOMAINS


def get_source_priority(country_code: str) -> dict[str, int]:
    """Lower sorts first. Local sources are bucket 0, shared intl/RU
    sources keep their bucket from SHARED_SOURCE_PRIORITY."""
    info = COUNTRIES.get(country_code, COUNTRIES[DEFAULT_COUNTRY])
    priority = dict(SHARED_SOURCE_PRIORITY)
    for feed in info["local_feeds"]:
        priority[feed["name"]] = 0
    return priority


def get_local_source_names(country_code: str) -> set[str]:
    """Source names that always pass the required_terms filter in
    parser.collect_news() — i.e. every local source for this country."""
    info = COUNTRIES.get(country_code, COUNTRIES[DEFAULT_COUNTRY])
    return {feed["name"] for feed in info["local_feeds"]}


# --- Per-country registry ---
# Adding a country means adding one entry here (+ a real-estate collector
# in real_estate.py, registered in real_estate.COUNTRY_REAL_ESTATE).
#
# local_feeds / local_domains: that country's own news sources (local-
#   language outlets + any dedicated Russian-language diaspora site, the
#   Serbia precedent being ruserbia.com/russian.rs).
# local_keywords: per-category local-language terms (see NEWS_CATEGORIES
#   docstring above) — merged with the shared Russian terms.
# required_terms: when a SHARED_INTL_FEEDS/SHARED_RU_FEEDS article (i.e.
#   not already about this country specifically) is considered for this
#   country's digest, it must mention one of these terms to pass. Local
#   sources (local_feeds) always pass regardless.
COUNTRIES: dict[str, dict] = {
    "serbia": {
        "name": "Сербия",
        "flag": "🇷🇸",
        "local_domains": {
            "rsponline.rs", "blic.rs", "novosti.rs", "politika.rs", "n1info.rs",
            "b92.net", "tanjug.rs", "serbia.travel", "investserbia.org",
            "telegraf.rs", "kurir.rs", "danas.rs", "nova.rs",
            "serbiarus.com", "rsmedia.ru", "ruserbia.com", "russian.rs",
        },
        "local_feeds": [
            {"name": "n1info.rs", "url": "https://n1info.rs/feed/"},
            {"name": "blic.rs", "url": "https://www.blic.rs/rss"},
            {"name": "telegraf.rs", "url": "https://www.telegraf.rs/rss"},
            {"name": "kurir.rs", "url": "https://www.kurir.rs/rss/"},
            {"name": "danas.rs", "url": "https://danas.rs/feed/"},
            {"name": "nova.rs", "url": "https://nova.rs/feed/"},
            {"name": "ruserbia.com", "url": "https://ruserbia.com/feed/"},
            {"name": "russian.rs", "url": "https://russian.rs/feed/"},
        ],
        "local_keywords": {
            "country": ["сербия", "сербск", "srbij", "viz", "borav", "državljanstv", "stran", "iselj"],
            "work": ["posao", "poslovi", "zaposlenj", "radno mesto", "radnik", "plata", "poslodavac"],
            "realestate": ["nekretnine", "stan", "kuća", "iznajmljivanje", "prodaja", "kupovina", "gradnja", "adaptacija", "enterijer", "eksterijer"],
            "education": ["škol", "univerzitet", "obrazovanj", "vrtić", "student"],
            "finance": ["bank", "porez", "račun", "valut"],
            "documents": ["dokument", "pasoš", "registracij", "potvrd", "dozvola"],
        },
        "required_terms": [
            "серб", "релокац", "переезд", "внж", "пмж", "белград",
            "nekretnine", "stan", "kuća", "iznajmljivanje", "prodaja",
        ],
    },
    "georgia": {
        "name": "Грузия",
        "flag": "🇬🇪",
        # Verified reachable + valid RSS Nov 2026 (see probe_sources.sh run).
        "local_domains": {"civil.ge", "jam-news.net", "oc-media.org"},
        "local_feeds": [
            {"name": "civil.ge", "url": "https://civil.ge/feed"},
            {"name": "jam-news.net", "url": "https://jam-news.net/ru/feed/"},
            {"name": "oc-media.org", "url": "https://oc-media.org/feed/"},
        ],
        "local_keywords": {
            # English terms included too — civil.ge/oc-media.org publish in
            # English, and _score() runs on raw (untranslated) feed text,
            # so an English-only article needs an English keyword to even
            # register a score > 0 and survive the relevance filter.
            "country": ["грузия", "грузинск", "тбилиси", "sakartvelo", "tbilisi", "georgia", "georgian"],
            "work": [],
            "realestate": [],
            "education": [],
            "finance": [],
            "documents": [],
        },
        "required_terms": ["грузи", "тбилиси", "релокац", "переезд", "внж", "пмж"],
    },
    "turkey": {
        "name": "Турция",
        "flag": "🇹🇷",
        "local_domains": set(),
        "local_feeds": [],
        "local_keywords": {
            "country": ["турция", "турецк", "стамбул", "анталья", "turkiye", "istanbul"],
            "work": [],
            "realestate": [],
            "education": [],
            "finance": [],
            "documents": [],
        },
        "required_terms": ["турци", "турецк", "стамбул", "анталь", "релокац", "переезд", "внж", "пмж", "икамет"],
    },
    "armenia": {
        "name": "Армения",
        "flag": "🇦🇲",
        # Verified reachable + valid RSS Nov 2026 (see probe_sources.sh run).
        "local_domains": {"news.am"},
        "local_feeds": [
            {"name": "news.am", "url": "https://news.am/rus/rss/"},
        ],
        "local_keywords": {
            "country": ["армения", "армянск", "ереван", "hayastan", "yerevan"],
            "work": [],
            "realestate": [],
            "education": [],
            "finance": [],
            "documents": [],
        },
        "required_terms": ["армени", "ереван", "релокац", "переезд", "внж", "пмж"],
    },
    "montenegro": {
        "name": "Черногория",
        "flag": "🇲🇪",
        # Verified reachable + valid RSS Nov 2026 (see probe_sources.sh run).
        "local_domains": {"mntimes.me"},
        "local_feeds": [
            {"name": "mntimes.me", "url": "https://mntimes.me/feed/"},
        ],
        "local_keywords": {
            "country": ["черногория", "черногорск", "подгориц", "будв", "crna gora", "podgorica"],
            "work": [],
            "realestate": [],
            "education": [],
            "finance": [],
            "documents": [],
        },
        "required_terms": ["черногор", "подгориц", "будв", "котор", "релокац", "переезд", "внж", "пмж"],
    },
}
