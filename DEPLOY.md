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
используй этот обходной путь:

```bash
pip install "setuptools==68.2.2"
pip install --no-build-isolation -r requirements.txt
```

Если `pip install -r requirements.txt` отработал сразу без ошибок — обходной
путь не нужен.

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
Все тесты должны быть зелёными (37 тестов на момент последнего обновления
этого файла).
