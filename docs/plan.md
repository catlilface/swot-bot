# План реализации бота «лекция → выжимка + транскрипт»

> **Канонический архитектурный документ — [docs/architecture.md](architecture.md)**.
> Здесь — этапный план работ (вехи, задачи, оценки) и исследование стека.


## 1. Цель и критерии приёмки

Бот получает от админа ссылку на лекцию, прогоняет её по конвейеру
`скачать → транскрибировать → проанализировать LLM` и публикует в целевую
беседу: детальную выжимку (согласно промпту из Langfuse) и файл транскрипции.

Критерии приёмки MVP:

1. Ссылку принимает только админ (проверка по `ADMIN_ID` из `.env`).
2. Целевая беседа задаётся `TARGET_CHAT_ID` из `.env`.
3. Полный конвейер работает через RabbitMQ: тяжёлые артефакты — через общие
   volume, в сообщениях — только `task_id` и пути.
4. Whisper-бэкенд и модель выбираются переменными окружения; код сервиса
   не зависит от конкретной реализации (SOLID: абстракция `Transcriber`).
5. Выжимка собирается LLM по промпту, хранимому в Langfuse (с fallback на
   локальный промпт для оффлайна/тестов); все LLM-вызовы видны в трассах Langfuse.
6. Observability на каждом этапе: структурированные логи (trace_id),
   health-эндпоинты, метрики, трассировка.
7. `uv`, `ruff format/lint`, `pre-commit`, юнит- и интеграционные тесты.
8. Каждый сервис заменяем: порты (protocols) вместо прямых зависимостей,
   стабы/fake-адаптеры для тестов и «фейкового режима» без внешних сервисов.

---

## 2. Исследование стека (актуально на момент составления плана)

| Компонент | Версия/решение | Источник |
|---|---|---|
| aiogram | 3.x (на конец 2025 — 3.31): async, Dispatcher/Router, magic-фильтры, middlewares, FSM | https://docs.aiogram.dev/en/latest/ |
| faster-whisper | CTranslate2, до 4× быстрее openai/whisper при той же точности, int8/fp16 quantization, PyAV вместо системного ffmpeg для декодирования, GPU (CUDA 12 + cuDNN 9; fallback ctranslate2 4.4.0/3.24.0) | https://github.com/SYSTRAN/faster-whisper (README, бенчмарки) |
| openai-whisper | эталон, медленнее, требует системный ffmpeg | https://github.com/openai/whisper |
| yt-dlp | downloader видео/аудио, Python API + CLI | https://github.com/yt-dlp/yt-dlp |
| aio-pika | async-клиент RabbitMQ на aiormq: автопереподключение, publisher confirms, type hints, Python 3.11+ | https://aio-pika.readthedocs.io/en/latest/ |
| langfuse + langchain | `from langfuse.langchain import CallbackHandler`; trace-инструментирование цепочек LangChain; ключи: `LANGFUSE_SECRET_KEY/PUBLIC_KEY/BASE_URL` | https://langfuse.com/docs/integrations/langchain/python |
| langfuse prompt management | промпт в Langfuse, в коде `get_client().get_prompt(name, label="production", fallback=...)`, рендер `prompt.compile(**vars)` | https://langfuse.com/docs/prompts/get-started; использование в OSS: github.com/doneyli/langfuse-llm-certification-finance, bytedance/deer-flow, langflow-ai/langflow |

Замечания:
- ketch_docs (Context7) недоступен — нет API-ключа; при реализации сверять
  версии и сигнатуры в официальных доках из таблицы.
- `faster-whisper` требует Python ≥ 3.9, но `ctranslate2`/GPU-биндинги могут
  отставать от свежего Python 3.13 — при сборке имиджа транскрайбера проверить
  наличие wheel; при необходимости зафиксировать образ на 3.12.

---

## 3. Архитектура

### 3.1 Схема конвейера

```
        Telegram                                     Docker (compose)                                        Telegram
┌─────────────────────┐   cmd url    ┌───────────────────────────────────────────────┐  результат  ┌───────────────────┐
│ Admin (человек)     │             │  rabbitmq (work queues + events, DLQ)        │             │                   │
└───┬─────────────────┘             │  video.download │ video.transcribe │          │             └───────────────────┘
    │                               │  analysis.run   │ result.deliver   │          │                ▲
┌───▼──────────────┐                │──────────────▲│────────────▲──────│          │                │
│  service:bot     │── publish ────►│              ││            │      │          │                │
│  (aiogram)       │   cmd         │              ││            │      │          │                │
│  admin-filter,   │◄─ result ─────│──────────────┴│────────────┴──────│──────────│────┐            │
│  validator,      │               │              ││            │      │          │    │            │
│  consumer        │               │          ┌───▼────┐  ┌────▼─────┐  ┌───▼─────┐ │    │            │
└──────────────────┘               │          │downl.  │  │transcr.  │  │analyzer │ │    │            │
        │                          │          │yt-dlp  │  │ffmpeg+Wh │  │langchain│ │    │            │
        │ url + task_id            │          └───┬────┘  └────┬─────┘  └───┬─────┘ │    │            │
        ▼                          │  /data/media │  /data/artifacts │        │result│    │            │
┌──────────────────────┐           │   video.m4a  ◄── transcript.srt│  summary.json │    │            │
│  общий volume /data/ │◄──────┬───│               │ .srt/.json     │            │    │            │
│  media + artifacts   │       │   └───────────────┴─────────────────┴────────────┘    │            │
└──────────────────────┘       └────── общий volume (bind/mount),тяжёлые              │            │
                                          файлы НЕ идут через rabbit                  │            │
                                                      ▲                                │
        bot читает результат и шлёт в целевой чат:     │                                │
        текст-выжимку (HTML) + документ transcript.srt└────────────────────────────────┘
```

Поток команд:

```
admin → bot: сообщение-ссылка
  bot: валидация (admin + URL) →	publish cmd("video.download", task_id, url, trace_id)
    downloader: yt-dlp/HTTP → /data/media/{task_id}.{ext}
      → publish event("video.downloaded", task_id, media_path, title, duration)
    transcriber: ffmpeg 16k mono wav → whisper → /data/artifacts/{task_id}/
                 (transcript.srt, segments.json, meta.json)
      → publish event("transcript.ready", task_id, base_dir)
    analyzer: читает segments.json/transcript.srt → промпт из langfuse → LLM (langchain) →
              /data/artifacts/{task_id}/summary.json
      → publish event("analysis.ready", task_id)
    bot (consumer result.deliver): читает summary.json + transcript.srt → sends:
      text-выжимка (HTML) + document transcript.srt
Ошибка на любом шаге → job.failed (fanout) → bot шлёт админу “задача упала” + DLQ.
```

### 3.2 Сервисы и их единственная ответственность

| Сервис | Отвечает за | Примечание |
|---|---|---|
| `services/bot` (aiogram) | приём команд от админа, валидация URL, публикация команды, потребление результата, отправка в целевую беседу (текст + документ) | единственный сервис с доступом к Telegram; `TARGET_CHAT_ID`/`ADMIN_ID` из окружения |
| `services/downloader` | скачивание медиа через **адаптеры источников** (yt-dlp для платформ, прямой HTTP для ссылок на файлы) в общий volume; публикация `video.downloaded` с `path + task_id` | не знает о других сервисах |
| `services/transcriber` | извлечение аудио (ffmpeg: 16k mono), транскрипция выбранным бэкендом whisper, запись артефактов; публикация `transcript.ready` | бэкенд (openai\|faster) — из env; `Transcriber` — протокол |
| `services/analyzer` | чтение транскрипции из volume, fetch промпта из Langfuse, вызов LLM (langchain + langfuse callback), запись результата; публикация `analysis.ready` | только чтение артефактов |

Общая библиотека `packages/contracts` (+ `packages/observability`): Pydantic-модели
сообщений, enum-ы событий, `MessageBus` (порт), `JobRegistry` (порт), утилиты
`trace_id`/логирование.

Хранилище задач (`JobRegistry`): нужен для
- идемпотентности (та же ссылка дважды → ответ «уже в работе»),
- статуса/прогресса и телеметрии.

`JobRegistry` — **абстракция**; реализации: `InMemoryJobRegistry` (по умолчанию;
для одного админа и одной целевой беседы этого достаточно — смотри задание) и
`RedisJobRegistry` (опционально, флаг `REGISTRY_BACKEND`). Данные лёгкие
(id, статус, ссылка на чат/сообщение), так что Redis тут разумно, но не обязательно.

### 3.3 CQRS и Event-driven: как это здесь выглядит

**Command side**: каждая стадия — рабочая очередь с одним потребителем на шаг
(`prefetch=1`), каждая команда — по сути **событие факта** («что произошло»),
а не инструкция «делай то-то»; следующий шаг сам решает, реагировать (choreography):

```
[Queue video.download]  → downloader → event video.downloaded
[Queue video.transcribe] → transcriber → event transcript.ready
[Queue analysis.run]     → analyzer    → event analysis.ready
[Queue result.deliver]   → bot         → Telegram
[Fanout job.failed]      → bot + DLQ
```

Это даёт: взаимозаменяемость сервисов (SOLID), независимые деплой/скейлинг.
Минус — нет оркестратора → нужен сквозной `trace_id` (в заголовках сообщений +
логах) и `JobRegistry`, чтобы видеть конвейер.

**Почему хореография, а не оркестратор:** поток линейный и короткий; отдельный
оркестратор добавил бы лишний сервис и точку отказа. DLQ + retry даются
механикой RabbitMQ бесплатно. Если появятся ветвления — оркестратор добавим
позже; контракты событий не изменятся.

**Query side — чтение:**
- bot немедленно отвечает админу «задача принята» / «уже в работе» (из `JobRegistry`),
- прогресс (опционально): событие `job.progress` (fanout) → bot обновляет сообщение,
- ошибки: событие `job.failed` (fanout) → bot пишет админу, тело уходит в DLQ,
- результат: анализируется consumer `result.deliver` → целевая беседа.

### 3.4 Контракты сообщений

Базовое сообщение (Pydantic v2):

```python
class BaseMessage(BaseModel):
    message_id: UUID
    msg_type: MessageType      # JobReceived, DownloadDone, DownloadFailed, TranscriptReady,
                               # TranscriptFailed, AnalysisReady, AnalysisFailed, JobFailed
    task_id: UUID              # сквозной id задачи
    trace_id: str              # сквозной trace_id для observability
    created_at: datetime
    url: str
    payload: Any               # по типу сообщения: paths, метаданные, ошибка (строка)
```

Конкретные сообщения — наследуются от `BaseMessage`. Тяжёлые артефакты через
Rabbit так: в `payload` — `task_id`, `path` и метаданные; сами файлы — в volume.

### 3.5 Общие volume

| Volume | Кто пишет | Кто читает |
|---|---|---|
| `swot_media` (`/data/media`) | downloader | transcriber |
| `swot_artifacts` (`/data/artifacts`) | transcriber, analyzer | bot, analyzer |
| `swot_cache` (`/models_cache`) | transcriber (whisper-модели) | – |

### 3.6 Маппинг «задача → беседа»

1. **Простой (по умолчанию)** — без Redis: константа `TARGET_CHAT_ID` из `.env`;
   bot всегда публикует результат туда. `JobRegistry` — in-memory. Один админ,
   одна беседа (по ТЗ) — этого достаточно.
2. **С Redis** — опциональный `JobRegistry`: идемпотентность, статусы задач,
   история, метрика. Включается `REGISTRY_BACKEND=redis`.

Решение: **первый этап без Redis** (`JobRegistry` интерфейс + memory-реализация);
Redis подключается одним коммитом, контракты не меняются — выигрыш от SOLID.

---

## 4. SOLID / DI в деталях

Инстанциирование — constructor DI (composition root: `app_factory()` в каждом
сервисе). Порти (typing.Protocol):

```python
class Transcriber(Protocol):
    def transcribe(self, audio: Path) -> TranscriptResult: ...      # faster/openai/fake
class MediaDownloader(Protocol):
    def download(self, url: str, dst_dir: Path) -> DownloadedMedia: ...  # yt-dlp / HTTP (по источнику)
class AudioExtractor(Protocol):
    def extract(self, src: Path, dst: Path) -> AudioChunk: ...        # ffmpeg
class PromptProvider(Protocol):
    def get(self, name: str, version: str = "production") -> Prompt: ... # langfuse | local fallback
class Summarizer(Protocol):
    def summarize(self, tr: TranscriptDocument, prompt: Prompt) -> Summary: ...  # langchain + callback
class MessageBus(Protocol):
    async def publish(self, msg: BaseMessage, routing_key: str) -> None
    async def consume(self, handler) -> None
```

SOLID:
- **S**: каждый сервис делает одно (см. табл. 3.2).
- **O/C**: новое поведение — новый адаптер (`FasterWhisperTranscriber`,
  `OpenaiWhisperTranscriber`, `FakeTranscriber`), вызывающий код не трогается.
- **L**: все транскрайберы возвращают `TranscriptResult` — клиент зависит от протокола.
- **I**: узкие интерфейсы a.k.a. `publish/consume` у `MessageBus`, не раздутые классы.
- **D**: сервисы получают зависимости в конструкторе (composition root), тесты
  подставляют фейки `MessageBus`/`Transcriber`/`Summarizer`.

---

## 5. Структура репозитория (monorepo, один uv workspace)

```
swot-bot/
├── pyproject.toml                # uv workspace, ruff-конфиг, pytest-конфиг
├── uv.lock
├── .python-version               # 3.13 (транскрайбер — при необходимости 3.12)
├── .env.example
├── README.md
├── docker-compose.yml             # основной стек
├── docker-compose.observability.yml  # профиль: langfuse/prometheus/grafana (opt)
├── docker-compose.integration.yml    # стек для интеграционных тестов
├── docker/
│   ├── bot/          Dockerfile
│   ├── downloader/   Dockerfile
│   ├── transcriber/  Dockerfile   # аргумент WHISPER_BACKEND, ffmpeg
│   └── analyzer/     Dockerfile
├── packages/
│   ├── contracts/        # models сообщений, enum, Id, JobRegistry port
│   └── observability/    # structlog-конфиг, trace_id, healthz (aiohttp)
├── services/
│   ├── bot/src/swot_bot/
│   ├── downloader/src/swot_downloader/
│   ├── transcriber/src/swot_transcriber/
│   └── analyzer/src/swot_analyzer/
├── tests/
│   ├── unit/
│   ├── integration/      # локальный rabbit + fake-бэкенды
│   └── fixtures/         # mp3-семпл, stub-LLM, yaml с expected-результатами
├── scripts/
│   └── seed_prompt.py    # создание/обновление промпта в langfuse
└── docs/plan.md
```

Каждый сервис — отдельный docker-образ; у транскрайбера тяжёлые зависимости
(whisper + ffmpeg), у bot — лёгкие (aiogram). Общие — только packages.

---

## 6. План работ по этапам

### Этап 0 — каркас
- uv workspace (packages + services + tests), `.env.example`, ruff (lint+format),
  pre-commit (ruff, ruff-format, end-of-file, check-yaml, check-toml).
  GitHub Actions: lint + unit.
- `docker-compose.yml`: rabbitmq (management), volumes.

### Этап 1 — контракты + шина
- `packages/contracts`: модели сообщений (Pydantic v2), enum, заголовки.
- `packages/observability`: structlog (trace_id/task_id/stage), healthz.
- `MessageBus` протокол + `RabbitMessageBus` (aio-pika): publish с confirms;
  consume: ack/nack, retry-очередь с TTL, DLQ-обмен.
- `JobRegistry` протокол + in-memory реализация.
- Юнит-тесты bus/логики (fake-bus).

### Этап 2 — bot
- aiogram: `/start`, admin-filter (middleware), валидатор URL
  (scheme+netloc allowlist `ALLOWED_HOSTS`), мгновенный ответ «задача принята».
- publisher команд; consumer результата: рендер `summary.json` в файл-шаблон
  (Jinja2 `templates/result_message.html.j2`) + документ `transcript.srt`;
  у каждого факта — время в видео (`MM:SS`); обработчики `job.failed`.
- Местная разработка: `fake` bus (in-memory).

### Этап 3 — downloader
- **Адаптеры источников**: `SourceRouter` по URL → `YandexDiskAdapter`,
  `GoogleDriveAdapter`, `VkVideoAdapter`, `RutubeAdapter`, `DirectFileAdapter`
  (прямая ссылка на файл), `GenericPageAdapter` (fallback через yt-dlp generic).
  Ссылкой может быть что угодно — источник не ограничен.
- Публикация `video.downloaded`; ошибка → `analysis.failed`/`job.failed`.
- Обработка cookie-файла для закрытых источников (опционально).

### Этап 4 — транскрайбер
- ffmpeg: 16k mono wav → tmp; `WhisperAdapter` (faster|openai) по env:
  `WHISPER_MODEL`, `WHISPER_DEVICE` (cpu/cuda), `WHISPER_COMPUTE_TYPE` (int8/float16),
  `WHISPER_LANGUAGE`, batch_size.
- Результат: `transcript.srt`, `segments.json`, `meta.json`
  в `/data/artifacts/{task_id}/`.
- Предзагрузка модели при старте (volume `swot_cache`).
- `FakeTranscriber` (детерминированная «транскрипция») для тестов.
- Публикация `transcript.ready`.

### Этап 5 — analyzer
- `PromptProvider`: langfuse (self-hosted) `get_prompt(name, label="production", fallback=локальный)`.
- Chain: `ChatOpenAI(OPENAI_COMPATIBLE_API_URL) + prompt.compile() + StrOutputParser`,
  модель — `LLM_MODEL_ID`; callbacks: `CallbackHandler(session_id=task_id, tags=["swot"])`.
- Chunking транскрипта при превышении контекста (text-splitters, map-reduce).
- Запись `summary.json` (структура: sections → facts c start/end) в артефакты;
  публикация `analysis.ready`.

### Этап 6 — публикация результата
- bot: рендер выжимки **в файл-шаблон** (`templates/result_message.html.j2`, Jinja2) +
  документ `transcript.srt` (транскрипция — только SRT); каждый факт
  помечается временем в видео (`MM:SS`, опционально глубокая ссылка по источнику);
  проверка лимитов Telegram (50 MB на документ — SRT обычно кратно меньше).
- Прогресс-статусы (опционально, событие `job.progress`).
- Очистка media после транскрипции; TTL артефактов.

### Этап 7 — observability
- structlog JSON + сквозной trace_id (заголовок rabbit + лог + span);
- healthz/readyz + healthcheck-и docker;
- трассировка LLM (langfuse), custom spans для стадий download/transcribe/analyze;
- опционально prometheus-метрики + grafana (compose overlay).

### Этап 8 — тесты и CI
- unit: контракты/bus, admin-filter, URL-валидатор, рендер выжимки, ffmpeg-команды
  (с mock subprocess), chunking/fallback промпта;
- integration (compose `integration.yml`: rabbitmq + fake-бэкенды): полный конвейер
  admin → результат на диск/в очередь без сети и GPU;
- e2e (mark slow): реальный whisper на 10-сек фикстуре, реальный LLM — локально.

### Этап 9 — полировка
- retry/DLQ мониторинг, лимиты, README, `.env.example`, `seed_prompt.py` для промпта.

**Оценка:** ~7–10 рабочих дней разработчика. Риски — скорость whisper (faster-whisper
int8 на CPU рабочий для small/base; для лекций 1+ часа предпочтительна GPU) и
качество распознавания (см. раздел «Риски»).

---

## 7. Варианты реализации и обоснование выбора

### 7.1 Whisper: openai-whisper vs faster-whisper
faster-whisper (CTranslate2) — до 4× быстрее при том же качестве, int8/fp16,
не требует системный ffmpeg (PyAV внутри), batching, distil-whisper-v3.
openai-whisper — эталон, медленнее, требует ffmpeg в образе.

Решение: **выбирается через env `WHISPER_BACKEND`**, код — один (адаптеры).
По умолчанию: CPU + faster-whisper (small/base для dev, large-v3 int8 или
distil-large-v3 для боевого качества русского). Docker-композ собирает образ под
выбранный бэкенд build-аргументом.

### 7.2 Клиент RabbitMQ
- **aio-pika** (рекомендация): полный asyncio, автопереподключение, confirms, типы.
- aiormq (низкоуровневый) — лишний оверхед.
- pika (asyncuit-кругов) — устаревший подход.

Своя обёртка `MessageBus` (порт) — чтобы fake/docker profeи тестов работали без брокера.

### 7.3 LLM / провайдер
- OpenAI-совместимый endpoint: `ChatOpenAI(base_url=…)` — переменные
  **`OPENAI_COMPATIBLE_API_URL`** (адрес: OpenAI API, Ollama `/v1`, LM Studio, vLLM) и
  **`LLM_MODEL_ID`** (модель) из `.env`; ключ — `OPENAI_COMPATIBLE_API_KEY`.
- Берем только `langchain-core` + `langchain-openai` + `langchain-text-splitters`
  (не полный `langchain`) — меньше зависимостей/конфликтов.
- Трассировка: `CallbackHandler` из `langfuse.langchain` по-вызов: `session_id=task_id`.

### 7.4 Промпт из Langfuse
- Тип `chat`, имя `lecture-summary`, версия с label `production` — правки промпта
  применяются сразу (hot update), без деплоя;
- fallback — локальный промпт (тесты/офлайн);
- для гибернации дефолтного промпта: `scripts/seed_prompt.py`.

### 7.5 Хранение артефактов: volume vs S3
Docker volume (по ТЗ) достаточно для одной машины. Для мульти-узла — тот же
интерфейс `ArtifactStore` (протокол) на S3-compatible (MinIO), без смены логики.

### 7.6 Fake-бэкенды для тестов
`FakeTranscriber` (фиксированный текст), `StubLLM` (структурная «выжимка» без
сети), `FakeBus` (in-memory) — полный конвейер проходит в CI без GPU и API-ключей.

### 7.7 Входные точки и масштабирование
Каждый сервис — один процесс; задачи сериализуются очередью
(prefetch=1). Несколько воркеров транскрайбера/анализатора тоже возможны
(prefetch > 1) — инстанцируются как отдельные контейнеры одного образа.

---

## 8. Конфигурация (`.env.example`)

```
# Telegram
BOT_TOKEN=
ADMIN_ID=12345678
TARGET_CHAT_ID=-1001234567890

# Pipeline
RABBIT_URL=amqp://guest:guest@rabbitmq:5672/
MEDIA_DIR=/data/media
ARTIFACTS_DIR=/data/artifacts
ALLOWED_HOSTS=                    # пусто = любые ссылки (опционный allowlist)
MAX_VIDEO_DURATION_SEC=7200
CLEANUP_MEDIA_KEEP_HOURS=6

# Whisper (transcriber)
WHISPER_BACKEND=faster            # faster | openai | fake (тесты)
WHISPER_MODEL=small               # tiny|base|small|medium|large-v3|distil-large-v3
WHISPER_DEVICE=cpu                # cpu | cuda
WHISPER_COMPUTE_TYPE=int8         # int8 | float16 | int8_float16
WHISPER_LANGUAGE=ru               # пусто = автодетект
WHISPER_BATCH_SIZE=4

# LLM
OPENAI_COMPATIBLE_API_URL=https://api.example.com/v1   # Ollama: http://ollama:11434/v1
OPENAI_COMPATIBLE_API_KEY=
LLM_MODEL_ID=gpt-4o-mini
LLM_TEMPERATURE=0.3
PROMPT_NAME=lecture-summary

# Langfuse (selfhosted)
LANGFUSE_BASE_URL=http://langfuse:3000
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=

# Прочее
REGISTRY_BACKEND=memory           # memory | redis
REDIS_URL=redis://redis:6379/0
LOG_LEVEL=INFO
```

---

## 9. Docker Compose (состав сервисов)

```yaml
services:
  rabbitmq:    image: rabbitmq:4-management   # healthcheck
  redis:       image: redis:7-alpine          # опционально (флаг REGISTRY)
  bot:         build: docker/bot              # aiogram, consumer result
  downloader:  build: docker/downloader       # volume swot_media
  transcriber: build: docker/transcriber      # WHISPER_BACKEND build arg, ffmpeg,
                volumes: swot_cache, swot_media:ro, swot_artifacts
  analyzer:    build: docker/analyzer          # volume swot_artifacts:ro
  # опционально (профиль):
  langfuse:    (postgres + web)               # docker-compose.observability.yml
  prometheus/grafana/otel:                    # (по требованию)
volumes:
  swot_media, swot_artifacts, swot_cache, rabbitmq_data, redis_data
```

GPU-опция для транскрайбера: `deploy.resources.reservations.devices` + nvidia runtime.

---

## 10. Тестирование и инструменты

- **uv**: все зависимости версионируются, `uv sync`, `uv run`.
- **pytest + pytest-asyncio**; протокол-fake для шины; integration-профиль compose
  с реальным rabbitmq.
- **ruff**: `ruff format` и `ruff check --fix`; конфиг в корневом `pyproject.toml`.
- **pre-commit**: `ruff-format`, `ruff`, `trailing-whitespace`, `end-of-file`,
  `check-yaml`, `check-toml`.
- **CI (GitHub Actions)**: `uv sync --all-groups` → `pre-commit run --all-files` →
  `pytest tests/unit` → (опц.) интеграционный прогон с docker.

Примеры unit-тестов: валидатор URL (allowed hosts/scheme/limit), admin-step,
`render_summary` (md → TG HTML, экранирование, длина), `audio_extractor`
(mock subprocess и ffmpeg-команда), `transcribe_*` (fake: файлы, лимит, язык),
`analyze` (chunking, prompt compile, fallback), `bus` (ack/nack на фейке).

Интеграционные e2e: fake-бэкенды + реальный rabbit; финальная отправка в
Telegram — проверкой публикуемых файлов/лога (тест `telegram` собственно требует
реальной сети/успешного бота — помечается как e2e, руками).

---

## 11. Открытые вопросы (уточнить до старта)

1. **LLM-провайдер**: OpenAI API / Яндекс GPT / Gemini / Ollama / LM Studio?
   Определяет `OPENAI_COMPATIBLE_API_URL`, модель (`LLM_MODEL_ID`), стоимость,
   хостинг. Дедолт — OpenAI-совместимый.
2. **Langfuse**: SaaS-cloud или self-host в compose (нужен postgres; еще один образ)?
   Есть ли ключи?
3. **GPU на сервере**: есть ли NVIDIA GPU / покажем доступ? Влияет на дефолтную
   модель whisper (int8 CPU — small/base; GPU — large-v3/distil для WER на русском).
4. **Языки**: лекции в основном русскоязычные — транскрипт автодетект или принудительно
   `ru`? Выжимка — на русском?
5. **Формат публикации**: текст+документ; что в документе — `.txt`, `.srt`, оба?
   Нужна ли структура выжимки с таймкодами (разделы со временными метками)?
6. **Источники**: приоритет адаптеров на старте (Yandex Disk, Google Drive,
   прямые ссылки и т.д.)? Нужны ли cookie/токены для закрытых источников?
   специализированный access? Таймлимит и лимит на размер входа.
7. **Прогресс-статусы** («Скачиваю… → Транскрибирую… → Анализирую…») — нужны или
   достаточно «задача принята» + результат? (дешёвый вариант через событие).
8. **Redis**: делать сразу (дубли/статусы) или первого релиза memory достаточно?
9. **Тесты**: интеграционные CI — только fake-бэкенды, либо требуется и реальный
   whisper-сегмент (медленно, mark)?

Некритичные мелочи (имя бота, в какую группу добавить, приватность) — не блокируют.

---

## 12. Риски

- **Качество распознавания** лекций по-русски: CPU+`small` даёт заметный WER;
  закладывать `large-v3`/`distil` и (лучше) GPU как опцию.
- **Python/ctranslate2 wheels**: под 3.13 могут отсутствовать — образ транскрайбера
  на 3.12 при необходимости.
- **Внешние зависимости**: yt-dlp ломается при изменении upstream; планировать
  обновления и уведомления репозиториев.
- **Telegram лимиты**: документ 50 MB; транскрипт обычно «маленький», но на часовые
  лекции текст может быть сотни КБ — вписаться. При превышении — слайсим.
- **Один админ/одна беседа**: упрощение по ТЗ; при росте → redis + карта task→chat.

---

## 13. Источники

- aiogram 3.x: https://docs.aiogram.dev/en/latest/
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- openai/whisper: https://github.com/openai/whisper
- yt-dlp: https://github.com/yt-dlp/yt-dlp
- aio-pika: https://aio-pika.readthedocs.io/en/latest/
- Langfuse × LangChain: https://langfuse.com/docs/integrations/langchain/python
- Langfuse prompt management: https://langfuse.com/docs/prompts/get-started
- Примеры использования langfuse в OSS: github.com/bytedance/deer-flow,
  github.com/langflow-ai/langflow (tracing/langfuse.py), github.com/doneyli/langfuse-llm-certification-finance