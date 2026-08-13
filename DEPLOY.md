# Деплой на VDS

Инструкция для развёртывания бота на собственном сервере (например, VDS от
vdsina.ru). Рассчитана как на человека, так и на Claude Code, запущенный
прямо на сервере — если это ты, просто выполняй шаги по порядку.

Предполагается Ubuntu/Debian. Проверить: `cat /etc/os-release`.

## 1. Системные зависимости

```bash
apt update
apt install -y python3 python3-venv python3-pip git ffmpeg
```

`ffmpeg` обязателен — без него не работает распознавание голосовых сообщений
(`bot.py` конвертирует OGG → WAV перед отправкой в SpeechRecognition).

## 2. Клонирование репозитория

```bash
cd /root   # или другая рабочая директория
git clone https://github.com/OlegBOSS11/msp-news-bot.git
cd msp-news-bot
```

Если нужна конкретная ветка (например, ещё не смёрженная):
```bash
git clone -b claude/code-review-9v3mb0 https://github.com/OlegBOSS11/msp-news-bot.git
```

## 3. Виртуальное окружение и зависимости

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
```

⚠️ **Известная проблема**: у `feedparser` есть зависимость `sgmllib3k`,
которая не собирается на новых версиях `pip`/`setuptools` (ошибка сборки
колеса с упоминанием `install_layout`). Если словишь такую ошибку —
используй этот обходной путь (нужны и `setuptools` постарше, и пакет
`wheel`, иначе следующим шагом упадёт с `invalid command 'bdist_wheel'`):

```bash
pip install "setuptools==68.2.2" wheel
pip install --no-build-isolation -r requirements.txt
```

Если `pip install -r requirements.txt` отработал сразу без ошибок — обходной
путь не нужен.

Если сервер стоит на **Python 3.14** (самые свежие Ubuntu/Debian) и после
этого `lxml` или `pydantic-core` (зависимость `aiogram`) всё равно пытаются
собираться из исходников с ошибками компиляции — не пытайся ставить
Rust/`libxml2-dev` вручную. Правильный фикс — версии в `requirements.txt`
уже подняты (актуально с августа 2026) до релизов, у которых есть готовые
wheels под Python 3.14, так что собирать ничего не должно. Если ошибка
всё же повторяется — на PyPI могли выйти версии ещё новее без wheel под
свежий Python; в таком случае напиши мне (или загугли
"<пакет> cp314 wheel"), не гадай с системными библиотеками вслепую.

Для запуска тестов дополнительно поставь dev-зависимости:
```bash
pip install --no-build-isolation -r requirements-dev.txt
```

## 4. Файл `.env` с ключами

**Никогда не коммить `.env` в git** (он уже в `.gitignore`). Создать его
нужно прямо на сервере:

```bash
nano .env
```

Содержимое (подставить свои значения):
```
BOT_TOKEN=токен_от_BotFather
CHAT_ID=твой_личный_telegram_chat_id
GIGACHAT_CREDENTIALS=credentials_от_GigaChat
```

Пояснение по `CHAT_ID`: сейчас эта переменная не используется для рассылки
новостей (рассылка идёт всем, кто нажал `/start` в боте, — список берётся
из таблицы `users` в `sent_news.db`). Переменная оставлена как задел на
будущее (например, для модерации/уведомлений), но обязательна для запуска
(`config.py` падает при старте, если её нет в окружении).

Ограничить права на файл:
```bash
chmod 600 .env
```

## 5. Проверка ручного запуска

```bash
source venv/bin/activate
python bot.py
```

Ожидаемые строки в логе:
```
Database initialized.
Scheduler started — next runs at 10:00 and 18:00 MSK.
Sending initial digest now...
Starting bot polling...
```

Останавливаешь через Ctrl+C и переходишь к автозапуску.

## 6. Автозапуск через systemd

```bash
nano /etc/systemd/system/msp-news-bot.service
```

Содержимое (поправить пути, если репозиторий не в `/root/msp-news-bot`):
```ini
[Unit]
Description=MSP News Bot (Serbia relocation)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/msp-news-bot
ExecStart=/root/msp-news-bot/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

⚠️ **`User=root` — известное упрощение, не обязательное.** Сервис не
нуждается в правах root для своей работы (только читает/пишет `sent_news.db`
и `.env` в своей директории). Если хотите ужесточить — заведите отдельного
пользователя и поправьте владельца:
```bash
useradd -r -s /usr/sbin/nologin mspbot
chown -R mspbot:mspbot /root/msp-news-bot
```
и замените `User=root` на `User=mspbot` в юните выше. Это меняет уже
работающий сервис — тестируйте на копии/в тихий час, а не вслепую на
проде: если что-то из зависимостей (ffmpeg, venv) окажется недоступно
новому пользователю, бот не запустится, пока не поправите права.

Запуск:
```bash
systemctl daemon-reload
systemctl enable --now msp-news-bot
systemctl status msp-news-bot     # проверить, что активен (active (running))
journalctl -u msp-news-bot -f     # смотреть логи в реальном времени, Ctrl+C для выхода
```

## 7. Обновление после изменений в коде

```bash
cd /root/msp-news-bot
git pull origin main    # или нужную ветку
source venv/bin/activate
pip install --no-build-isolation -r requirements.txt   # если requirements.txt менялся
systemctl restart msp-news-bot
systemctl status msp-news-bot
```

## 8. Быстрая диагностика проблем

| Симптом | Что проверить |
|---|---|
| Сервис не стартует (`systemctl status` — failed) | `journalctl -u msp-news-bot -n 50` — обычно не хватает переменной в `.env` или зависимости |
| Бот не отвечает в Telegram | `BOT_TOKEN` верный? Сервис вообще запущен (`systemctl status`)? |
| Голосовые сообщения не распознаются | `ffmpeg -version` — установлен ли ffmpeg |
| Ошибки от GigaChat | `GIGACHAT_CREDENTIALS` актуальны (не истекли)? В `gigachat_client.py` намеренно отключена проверка TLS-сертификата (`verify_ssl_certs=False`) — это осознанное решение из-за корневого сертификата Минцифры, трогать не нужно |
| RSS-новости не приходят | Часть источников может быть недоступна из региона сервера (проверялось: `tass.ru`, `n1info.rs`, `blic.rs` иногда отдают HTTP 403) — это не баг деплоя, а доступность конкретных сайтов |

## 9. (Опционально) Тесты перед деплоем на прод

```bash
source venv/bin/activate
pip install --no-build-isolation -r requirements-dev.txt
BOT_TOKEN=123456:test-token CHAT_ID=123456 GIGACHAT_CREDENTIALS=test:cred \
  python -m pytest -v
```
Все тесты должны быть зелёными (92 теста на момент последнего обновления
этого файла).

## 10. (Опционально) Прокси для Telegram-трафика через EU VPS

Зачем: если сервер бота стоит в России, `api.telegram.org` бывает
нестабилен именно на HTTPS-уровне (не путать с обрывом сети целиком —
`ping`/`mtr` могут быть идеально чистыми). Симптомы: кнопки меню временно
"замирают" и сами отвисают через 10-20 сек, дайджест зависает на сборе и
дособирается сам, в `journalctl` — `aiogram.exceptions.TelegramNetworkError:
Request timeout error` с последующим авто-retry. Похоже на DPI-уровневое
вмешательство именно в трафик к Telegram, а не на общую проблему сети или
код бота.

Решение — направить **только** Bot API трафик (`getUpdates`, `sendMessage`
и т.д.) через SOCKS5-прокси на отдельном маленьком VPS в ЕС (например,
Hetzner ~€3.79/мес, Amsterdam/Falkenstein). GigaChat и парсинг RSS/недвижимости
остаются идти напрямую с российского сервера — это осознанно, доступ к
GigaChat зависит от того, что сервер физически в России.

### 10.1. На EU VPS: поднять SOCKS5-прокси (microsocks)

```bash
apt update && apt install -y git build-essential
git clone https://github.com/rofl0r/microsocks.git
cd microsocks
make
cp microsocks /usr/local/bin/
```

Systemd-юнит:
```bash
nano /etc/systemd/system/microsocks.service
```
```ini
[Unit]
Description=microsocks SOCKS5 proxy for Telegram traffic
After=network.target

[Service]
ExecStart=/usr/local/bin/microsocks -1 -i 0.0.0.0 -p 1080 -u ПРОКСИ_ЛОГИН -P ПРОКСИ_ПАРОЛЬ
Restart=always
User=nobody
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```
`-1` — требовать авторизацию даже без неё бы разрешил анонимный доступ.
Логин/пароль — свои, посложнее.

```bash
systemctl daemon-reload
systemctl enable --now microsocks
systemctl status microsocks
```

**Файрвол — обязательно**, иначе прокси открыт всему интернету:
```bash
ufw allow from <IP_ВАШЕГО_RU_VDS> to any port 1080 proto tcp
ufw status
```
(`<IP_ВАШЕГО_RU_VDS>` — публичный IP сервера, на котором крутится сам бот,
не этого прокси-сервера.)

### 10.2. На российском VDS (сервер бота): подключить прокси

`aiohttp-socks` уже в `requirements.txt` — если ставили зависимости раньше
и `pip install` не переделывали, доустановить:
```bash
source venv/bin/activate
pip install aiohttp-socks==0.12.0
```

Добавить в `.env`:
```
TELEGRAM_PROXY_URL=socks5://ПРОКСИ_ЛОГИН:ПРОКСИ_ПАРОЛЬ@IP_EU_VPS:1080
```

Больше ничего менять не нужно — `bot.py` сам подхватывает переменную:
если она задана, весь трафик к Telegram Bot API идёт через прокси; если
не задана (переменной нет в `.env`) — бот работает как раньше, напрямую.

```bash
systemctl restart msp-news-bot
journalctl -u msp-news-bot -n 20 --no-pager
```
Должна появиться строка `Telegram Bot API traffic routed via proxy` сразу
после старта — значит прокси подхватился.

### 10.3. Проверка и откат

Дальше — то же самое сравнение по логам, что и для IPv4-фикса:
```bash
journalctl -u msp-news-bot --since "24 hours ago" | grep -ic error
```
Если хотите быстро откатиться на прямое подключение — удалить/закомментировать
`TELEGRAM_PROXY_URL` в `.env` и `systemctl restart msp-news-bot`, без
доп. изменений в коде.
