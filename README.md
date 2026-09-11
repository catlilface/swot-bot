# swot-bot

Бот: ссылка на лекцию → транскрипция (faster-whisper) → выжимка (LLM, факты
со ссылками на таймкоды) → публикация в Telegram.

## Стек (docker-compose файлы)

| файл | режим | Telegram | ASR | LLM | что нужно от вас |
|---|---|---|---|---|---|
| `docker-compose.dev.yml` | тестовый (dev/CI) | `fake-tg` (заглушка) | фэйк `asr-service` | фэйк `llm-service` | ничего — поднимается из коробки |
| `docker-compose.slim.yml` | production-минимум | реальный (`.env`) | внешний (`.env`) | внешний (`.env`) | реальные `TELEGRAM__*/TRANSCRIBER__*/LLM__*` в `.env` |
| `docker-compose.full.yml` | production, свои ASR/LLM | реальный (`.env`) | self-hosted `whisper-server` | self-hosted `ollama` | только реальный `TELEGRAM__*/…`; модели — `ASR_MODEL` / `LLM_OLLAMA_MODEL` |

Все три файла читают один и тот же `.env` (пути, broker, health-порты,
Langfuse); compose-файлы перекрывают лишь то, что должно отличаться между
режимами. Команды: `docker compose -f <файл> up -d --build` (и т.д.).

## Quickstart (dev-стек, без API-ключей)

Весь стек поднимается в Docker-компазе и полностью автономен:
ASR/LLM — OpenAI-совместимые фэйки, Telegram — заглушка (`fake-tg`),
RabbitMQ — обычный broker с dev-креденшалами.

```bash
cp .env.example .env        # значения уже рабочие из коробки
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml ps   # всё running/healthy через ~1–2 мин
```

Сквозной прогон (без реального Telegram) — "сообщение от админа" с ссылкой:

```bash
curl -X POST http://localhost:8081/inject \
  -H 'Content-Type: application/json' \
  -d '{"text": "http://fake-tg:8081/media/sample.wav"}'

docker compose -f docker-compose.dev.yml logs -f fake-tg   # ответ бота (summary + SRT) логируется здесь
```

Пример ссылки: платформа (YouTube, VK, Rutube, диск, Drive…)
или прямой URL на медиа-файл (mp4/m4a/wav…).

Логи пайплайна: `docker compose -f docker-compose.dev.yml logs -f bot downloader transcriber analyzer`.
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

### Dev-режим (fakes) и их семантика

Единственный dev-режим в проекте — отдельные контейнеры dev-стека:
`asr-service`, `llm-service` (OpenAI-совместимые фэйки, `services/fake/fake_openai.py`)
и `fake-tg` (заглушка Telegram Bot API, `services/fake/fake_telegram.py`; образ
`swot-bot/fake:dev`). Они существуют только в `docker-compose.dev.yml`, не входят
в образы сервисов пайплайна (их Dockerfile копируют только `packages/` и
свой `services/<svc>/`) и не активны, когда dev-стек не поднят.

Внутри процессов сервисов фэйков нет (T-2.3): in-process-двойники
(`FakeBus`, `FakeTranscriber` / `FakeTranscriberProvider`, `StubSummarizer`
и их `dishka`-провайдеры) удалены из production-модулей —
`FakeTranscriberProvider` в коде больше не существует, поэтому он не
регистрируется ни по умолчанию, ни по какому-либо флагу; активировать
in-process-фейки нечем. Тестовые двойники (`FakeBus` + `FakeBusProvider`)
живут в `tests/unit/fakes.py` и используются только из тестов.

Следствие: в production-контейнерах (реальные ASR/LLM/Telegram) фэйки
включить невозможно без явного изменения стека — только теми же
compose-контейнерами, которые оператор осознанно включает в dev-стек.

### Переход на реальные зависимости

Комментировать контейнеры больше не нужно — реальные режимы это отдельные
файлы (таблица выше), dev-стек остаётся нетронутым:

- **`docker-compose.slim.yml`** (внешние ASR/LLM): в `.env` —
  `TELEGRAM__TOKEN=<токен BotFather>`, `TELEGRAM__API_BASE_URL=` (пусто =
  `api.telegram.org`), реальные `TELEGRAM__ADMIN_ID` / `TELEGRAM__TARGET_CHAT_ID`;
  `LLM__API_URL` (с `http(s)://`) / `LLM__API_KEY` / `LLM__MODEL_ID`;
  `TRANSCRIBER__API_URL` / `TRANSCRIBER__API_KEY` / `TRANSCRIBER__MODEL`.
- **`docker-compose.full.yml`** (свои ASR/LLM в стеке): Telegram — то же самое;
  ASR/LLM уже в стеке (`whisper-server` + `ollama`), модели выбираются
  `ASR_MODEL` (tiny/base/small/medium/large-v3-turbo) и `LLM_OLLAMA_MODEL`
  (любая модель ollama, https://ollama.com/library). GPU: `ASR_DEVICE=cuda`
  + `ASR_IMAGE_TAG=cuda`; для ollama раскомментировать GPU-блок в compose.

### Optional services

- **Langfuse** (v3) — входит в dev-стек `docker-compose.dev.yml` (T-2.8):
  `langfuse` (UI — http://localhost:3000) + `langfuse-worker` + Postgres,
  ClickHouse, MinIO. При первом старте проект/пользователь создаются
  автоматически (`LANGFUSE_INIT_*`, dev-ключи — в `docker-compose.dev.yml`;
  те же ключи задаёт compose сервису `analyzer`, так что трейсинг включён
  по умолчанию в dev-стеке). В slim/full-стеках Langfuse внешний — задаётся
  в `.env` (`LANGFUSE__HOST/PORT/PUBLIC_KEY/SECRET_KEY`) или не используется
  вообще (fallback на локальный промпт, T-0.4). Чтобы отключить трейсинг —
  зачистить `LANGFUSE__PUBLIC_KEY/SECRET_KEY`. **Postgres** ходит вместе с
  Langfuse; отдельно боту не нужен.

## Документация

- [docs/architecture.md](docs/architecture.md) — каноническая архитектура
  (пайплайн, контракты, адаптеры источников, безопасность, dev-стек).
- [todo/plan.md](todo/plan.md) — этапный план работ; исследование —
  [audit.md](audit.md), снимки ревью — `reviews/`.
