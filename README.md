# swot-bot

Бот: ссылка на лекцию → транскрипция (faster-whisper) → выжимка (LLM, факты
со ссылками на таймкоды) → публикация в Telegram.

## Quickstart (dev-стек, без API-ключей)

Весь стек поднимается в Docker-компазе и полностью автономен:
ASR/LLM — OpenAI-совместимые фэйки, Telegram — заглушка (`fake-tg`),
RabbitMQ — обычный broker с dev-креденшалами.

```bash
cp .env.example .env        # значения уже рабочие из коробки
docker compose up -d --build
docker compose ps           # всё running/healthy через ~1–2 мин
```

Сквозной прогон (без реального Telegram) — "сообщение от админа" с ссылкой:

```bash
curl -X POST http://localhost:8081/inject \
  -H 'Content-Type: application/json' \
  -d '{"text": "http://fake-tg:8081/media/sample.wav"}'

docker compose logs -f fake-tg   # ответ бота (summary + SRT) логируется здесь
```

Пример ссылки: любой прямо-доступный mp4/m4a (например,
`https://www.dropbox.com/...?dl=1` не поддерживается — нужен прямой URL).

Логи пайплайна: `docker compose logs -f bot downloader transcriber analyzer`.
Все четыре сервиса пишут общий `trace_id` (заголовок сообщения в RabbitMQ) —
его можно отследить в логах каждого сервиса.

### Что внутри

| сервис | роль |
|---|---|
| `rabbitmq` | broker (dev-пользователь `swot`/`swot`, UI на `:15672`) |
| `bot` | aiogram-бот: принимает ссылки, публикует результат |
| `downloader` | качает видео (yt-dlp / прямое HTTP), извлекает медиа |
| `transcriber` | извлекает аудио (ffmpeg) и шлёт в ASR |
| `analyzer` | LLM-выжимка с фактами и таймкодами |
| `asr-service`, `llm-service` | фэйки (OpenAI-совместимые) |
| `fake-tg` | заглушка Telegram Bot API (`POST /inject`) |

### Переход на реальные зависимости

В `.env` (комментарии там же):

1. **Telegram**: `TELEGRAM__TOKEN=<токен BotFather>`,
   `TELEGRAM__API_BASE_URL=` (пусто = `api.telegram.org`), реальные
   `TELEGRAM__ADMIN_ID` / `TELEGRAM__TARGET_CHAT_ID`; закомментировать
   `fake-tg` в `docker-compose.yml`.
2. **LLM**: `LLM__API_URL` (с `http(s)://`), `LLM__API_KEY`, `LLM__MODEL_ID`;
   закомментировать `llm-service`.
3. **ASR**: `TRANSCRIBER__API_URL` (с `http(s)://`), `TRANSCRIBER__API_KEY`,
   `TRANSCRIBER__MODEL`; закомментировать `asr-service`.

### Optional services

- **Langfuse** — не входит в dev-compose сознательно: v3 требует Postgres +
  ClickHouse + Redis + MinIO, а с пустыми `LANGFUSE__PUBLIC_KEY/SECRET_KEY`
  аналитик работает на локальном промпте (fallback, добавлен в T-0.4).
  Если нужны промпт-менеджмент/трейсинг: поднять официальный
  langfuse docker-compose (v3) и заполнить ключи в `.env` — бинарник
  `analyzer` менять не нужно (конфиг уже читает `LANGFUSE__*`).
- **Postgres** — вместе с Langfuse; отдельно боту не нужен.

## Документация

- [docs/architecture.md](docs/architecture.md) — каноническая архитектура
  (микросервисы, CQRS/event-driven, адаптеры источников, шаблон сообщения,
  docker-compose).
- [docs/plan.md](docs/plan.md) — этапный план работ и исследование стека.
