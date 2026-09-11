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
```

Бот подтверждает задачу и просит тег с названием предмета — следующее
инжект-сообщение трактуется как тег (нормализуется в нижний регистр с
подчёркиваниями и досылается в конце результата):

```bash
curl -X POST http://localhost:8081/inject \
  -H 'Content-Type: application/json' \
  -d '{"text": "Высшая Математика"}'   # → «высшая_математика» в конце результата

docker compose -f docker-compose.dev.yml logs -f fake-tg   # ответы бота (summary + SRT + тег) логируются здесь
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
| `bot` | aiogram-бот: принимает ссылки и теги предметов, публикует результат |
| `downloader` | качает видео (yt-dlp / прямое HTTP), извлекает медиа |
| `transcriber` | извлекает аудио (ffmpeg) и шлёт в ASR |
| `analyzer` | LLM-выжимка с фактами и таймкодами |
| `asr-service`, `llm-service` | фэйки (OpenAI-совместимые) |
| `fake-tg` | заглушка Telegram Bot API (`POST /inject`) |

### Интеграция с другими сущностями

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

## Локальные проверки (pre-commit)

Перед коммитом те же проверки, что в CI: форматирование (`ruff format`),
линт (`ruff check`), `mypy packages services`, unit-тесты (`pytest tests/unit`),
импорт всех пакетов workspace.

```bash
uv sync                      # зависимости (включая pre-commit)
uv run pre-commit install    # один раз — хуки на git commit
uv run pre-commit run --all-files   # проверка всех файлов вручную
```

Конфиг: `.pre-commit-config.yaml`. Отключить на один раз: `git commit --no-verify`
(не рекомендуется).
