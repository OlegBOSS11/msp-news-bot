# Contributing

Гайд для тех, кто разрабатывает бота локально (не для деплоя на прод —
см. `DEPLOY.md`).

## Быстрый старт

```bash
git clone https://github.com/OlegBOSS11/msp-news-bot.git
cd msp-news-bot
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # pytest, для запуска тестов
```

⚠️ Если ловишь ошибку сборки `sgmllib3k` (зависимость `feedparser`) —
см. раздел "Известные проблемы установки" в `DEPLOY.md`, там рабочий обход.

### Переменные окружения

`.env` в репозитории нет и не будет — файл в `.gitignore`, там реальные
токены. Для локальной разработки создай свой `.env` в корне проекта:

```
BOT_TOKEN=любой_валидно_выглядящий_токен  # формат "123456:abc..."
CHAT_ID=123456
GIGACHAT_CREDENTIALS=что-угодно_если_GigaChat_не_нужен
```

Если не собираешься реально запускать `bot.py` (`python bot.py`), а
работаешь только с `real_estate.py`/`database.py` напрямую или через
тесты — реальные значения не нужны, подойдут любые "похожие на правду"
строки (`config.py` только проверяет, что переменные заданы и что
`BOT_TOKEN` проходит формальную валидацию формата в `aiogram`).

### Тесты

```bash
BOT_TOKEN=123456:test-token CHAT_ID=123456 GIGACHAT_CREDENTIALS=test:cred \
  python -m pytest -v
```

Все тесты должны быть зелёными на чистом клоне.

Линтер (`ruff check .`) — тоже должен быть чистым, `pip install ruff` из
`requirements-dev.txt`. `ruff format` пока не применяется ко всему проекту
(понадобится отдельный коммит-переформатирование) — не запускайте
`ruff format` на файлах, которые не трогаете в своём PR.

## Ветки и Pull Request'ы

- Не работай напрямую в `main`.
- Новая ветка на фичу: `git checkout -b feature/<короткое-название>`
- Перед началом — `git pull origin main`, чтобы не разойтись с
  актуальным состоянием.
- По готовности — Pull Request в `main` (не пуш напрямую, если не
  договорились об обратном).
- На каждый PR автоматически запускается CI (`.github/workflows/ci.yml`):
  тесты + `ruff check`. PR с красным CI не мержится.

## Архитектура: недвижимость и база данных

Модуль `real_estate.py` + таблица `real_estate_listings` в `database.py`
(`sent_news.db`, SQLite) — то, чем предстоит заниматься. Как это устроено
сейчас:

### Откуда берутся данные

- **Рабочий источник: halooglasi.com.** `parse_halooglasi()` парсит
  карточки объявлений (`.my-product-placeholder`, атрибут `data-id`,
  цена в `data-value` и т.д. — селекторы задокументированы в докстринге
  функции). Собирает 4 города × (продажа + аренда) = 8 URL, см.
  `HALOOGLASI_CITIES` / `HALOOGLASI_COLLECTOR_URLS`.
- **cityexpert.rs и nekretnine.rs — НЕ работают.** Оба сайта блокируют
  автоматические запросы (Cloudflare отдаёт `ClientResponseError`
  status=0 / стабильный 403). Проверено многократно вручную с прод-сервера
  — не тратьте время на попытки их оживить без смены подхода (см. ниже
  про `curl_cffi`).
- **4zida.rs — не подключён,** но не заблокирован (200 OK на запрос),
  просто ещё не разобрана актуальная вёрстка (старый селектор `.oglas`
  мёртвый — сайт сменил разметку). Хороший кандидат для следующего
  источника, если возьмётесь.

### Важный технический нюанс: `aiohttp` vs `requests`

Сбор с halooglasi.com **обязательно** идёт через синхронный `requests`
(функция `_fetch_page_sync`, обёрнута в `asyncio.to_thread`), **не**
через `aiohttp`. Это не случайность: `aiohttp` (HTTP/1.1) на этот сайт
стабильно ловит блокировку, а `requests` с теми же заголовками —
стабильно нет (проверено на проде). Похоже на детект по TLS/HTTP-стеку,
а не по заголовкам. Если добавляете новый источник и он вдруг "не
находит объявлений" — проверьте сначала `requests`, прежде чем чинить
селекторы, может оказаться, что дело не в парсинге.

### Схема БД (`real_estate_listings`)

```
ad_id       TEXT PRIMARY KEY   -- ID объявления на сайте-источнике
title       TEXT
price       TEXT               -- для показа человеку, напр. "199.800 €"
price_value INTEGER             -- для сортировки, напр. 199800 (см. _parse_price_value)
location    TEXT
url         TEXT
image_url   TEXT
source      TEXT               -- "HaloOglasi" и т.д.
city        TEXT               -- слаг из HALOOGLASI_CITIES, напр. "beograd"
deal_type   TEXT               -- "sale" | "rent"
first_seen  TIMESTAMP
last_seen   TIMESTAMP           -- обновляется при каждом повторном сборе
```

Функции в `database.py`: `upsert_real_estate_listing()` (INSERT или
обновление по `ad_id`), `get_real_estate_listings_filtered(city, deal_type,
sort, limit)` — используется меню бота (`bot.py`, кнопка "🏠 Недвижимость"),
`prune_stale_listings(days)` — чистит объявления, которые давно не
попадались сборщику (снято с продажи/аренды).

### Сборщик и планировщик

`refresh_real_estate_database()` в `real_estate.py` — вызывается из
`bot.py` раз в 6 часов (`IntervalTrigger`) и один раз при старте бота.
Между запросами к сайту — `asyncio.sleep(3)`, чтобы не долбить сайт
слишком часто.

### Если добавляете новый источник

1. Сначала проверьте `requests.get()` с браузерным `User-Agent` — жив ли
   сайт, отдаёт ли 200 без JS-челленджа.
2. Найдите актуальные CSS-селекторы карточек (структура сайтов меняется —
   не доверяйте старым селекторам в этом файле без проверки).
3. Напишите `parse_<источник>()` по образцу `parse_halooglasi()` —
   возвращает `list[PropertyListing]`, с `ad_id`/`city`/`deal_type`/
   `price_value` заполненными, если хотите, чтобы источник участвовал в
   меню бота (не только в live-поиске по тексту).
4. Добавьте сбор в `refresh_real_estate_database()` и апсерты в БД.
5. Заголовок/локация/цена объявления — данные со стороннего сайта, то есть
   внешний ввод. Если формируете текст сообщения с `parse_mode="HTML"`
   (как `_send_real_estate_listing()` в `bot.py` или `format_listings()` в
   `real_estate.py`), оборачивайте их в `telegram_text()` /
   `telegram_link()` из `telegram_format.py` — иначе `<`, `>`, `&` в
   заголовке объявления сломают сообщение или подменят ссылку. Смотрите,
   как это уже сделано в `_send_real_estate_listing()`.

## Общий стиль кода в проекте

- Всё асинхронное (`async`/`await`), aiogram 3.x.
- Философия обработки ошибок — "graceful degradation": почти каждая
  функция в `database.py`/`real_estate.py` оборачивает свою логику в
  `try/except`, логирует через `logger.error/warning`, возвращает
  безопасное значение по умолчанию (`[]`, `None`, `False`) вместо падения.
  Следуйте этому паттерну — бот не должен падать целиком из-за сбоя
  одного источника.
- Блокирующие вызовы (`requests`, синхронные библиотеки) всегда через
  `asyncio.to_thread(...)`, не напрямую в async-функции.
