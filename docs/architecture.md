# Архитектура бота «лекция → выжимка + транскрипт»

Канонический архитектурный документ. Этап-план и сроки — в
[docs/plan.md](plan.md).

---

## 1. Обзор

Пользователь-админ присылает боту ссылку на лекцию. Бот прогоняет её по
конвейеру **скачать → транскрибировать → проанализировать LLM** и публикует в
целевую беседу:
- **текст-выжимку**, собранную по промпту из Langfuse, — заполняется в файл-шаблон
  и отправляется как сообщение;
- **файл транскрипции `transcript.srt`** — отправляется как документ.

Факты в выжимке снабжаются **временем в видео** (`MM:SS`), когда был сказан этот факт.

### Ключевые решения (из ТЗ и уточнений)

| Решение | Значение |
|---|---|
| Ссылка на видео | передаётся **только в теле события RabbitMQ** (не в volume) |
| Тяжёлые данные | общий volume (аудио, транскрипция, выжимка); в сообщениях — `task_id` + пути |
| Downloader | **адаптеры по источником** (Yandex Disk, Google Drive, VK Video, rutube, прямая ссылка на файл и т.д.) — роутер по URL + универсальный fallback |
| Транскрибация | **faster-whisper**, GPU; результат — **`.srt`** (+ `segments.json` для анализа) |
| Модель LLM | **`OPENAI_COMPATIBLE_API_URL` + `LLM_MODEL_ID`** из `.env` (любой OpenAI-совместимый endpoint) |
| Промпт | **Langfuse self-hosted** (версионирование, label `production`, fallback локальный) |
| Маппинги | **из `.env`** (`ADMIN_ID`, `TARGET_CHAT_ID`) — **Redis не используется** |
| Топология | микросервисы в одном **docker-compose**; каждый сервис независим и заменяем |
| Публикация | **отдельный файл-шаблон** (Jinja2), в который вставляется выжимка |
| Arch-стиль | CQRS + event-driven (choreography через RabbitMQ) + Ports&Adapters (SOLID) |

---

## 2. Принципы

### 2.1 Микросервисная архитектура
- Каждый сервис — отдельный процесс + отдельный Docker-образ в одном compose.
- Каждый сервис имеет **одну ответственность** (см. §5) и собственную
  зависимую модель данных только через общие контракты.
- Общение — только через **события RabbitMQ**; тяжёлые данные — через **общий volume**
  по соглашению о путях (файловая шина), лёгкие данные (ссылки, метаданные) — только в событии.
- Сервисы **взаимозаменяемы**: интерфейсы (Ports&Adapters, §4) позволяют менять
  реализацию, бэкенд или даже язык программирования сервиса без изменения соседей.

### 2.2 Языковая граница между сервисами
- Контракты (сообщения, файлы, шаблон) — **языконейтральные**: JSON Schema,
  SRT, Markdown/HTML-шаблон. Любой сервис может быть переписан на другой язык.
- Рекомендуемый стек по умолчанию — **Python 3.13** во всех сервисах (единый
  пакет-контракты и быстрый наборный процесс).
- Допустимые альтернативы по сервисам (если захочется «лучше/быстрее»):

| Сервис | Рекомендация (Python) | Варианты | Почему Python по умолчанию |
|---|---|---|---|
| bot | aiogram 3.x | Node.js/TS (grammY/telegraf), Go (telegram-bot-api) | единый контракт-пакет, толстый стек вокруг aiogram |
| downloader | yt-dlp (Python API) | Go/Node-обёртка над yt-dlp-бинарём | yt-dlp лучший именно из Python |
| transcriber | faster-whisper (Python) | C++ whisper.cpp через RPC — избыточно | только Python-бандлы CTranslate2; GPU легко |
| analyzer | langchain-core (Python) | TS/JS langchain.js | единая контракт-генка; langfuse SDK в Python шире |

> Решение: стартуем на Python везде; «языковая граница» (JSON Schema + файлы)
> сохраняет возможность переписать любой сервис позже без прочих изменений.

### 2.3 Модель данных: разделение каналов

```
┌──────────────── Двухканальная передача данных ────────────────┐
│                                                               │
│  RabbitMQ (лёгкий канал)          Общий Docker volume (тяжёлый)│
│  • source URL  • task_id          • /media/{task_id}.m4a      │
│  • метаданные  • пути                    (только до транскрипции)│
│  • статусы     • ошибки           • /artifacts/{task_id}/     │
│                                      transcript.srt           │
│                                      segments.json            │
│                                      summary.json            │
│                                      (выжимка для шаблона)   │
└───────────────────────────────────────────────────────────────┘
```

- **Ссылка на видео** — крошечная: живёт в теле события, никогда не кладётся
  в volume.
- **Аудио/транскрипт/выжимка** — тяжёлые/большие: только на диске общего volume,
  в событии только пути.

---

## 3. Общая схема конвейера

```
                    Telegram
┌───────────────────────────────┐
│ Admin  ──(пост ссылки)─►  Bot │
└──────────────┬────────────────┘
               │ 1. validate (ADMIN_ID, URL)
               │ 2. publish: download.request {task_id, trace_id, source_url}
               ▼
        ┌──────────────┐        event: video media → /data/media/{task_id}.m4a
        │  rabbitmq    │ ──────────────────────► ┌─────────────────────────────────┐
        │  queues  +   │                         │ downloader (адаптеры источников) │
        │  exchanges   │ ◄────────────────────── └─────────────────────────────────┘
        └──────┬───────┘   event: video.downloaded {media_path, resource_id, url}
               │
               │  event: video.downloaded ──────► ┌─────────────────────────────────┐
               │                                  │ transcriber (faster-whisper,GPU) │
               │                                  │  ffmpeg: 16k mono wav            │
               │  event: transcript.ready ◄────── │  → /artifacts/{task_id}/         │
               │   {srt_path, segments_path}      │     transcript.srt, segments.json│
               │                                  └─────────────────────────────────┘
               │
               │  event: transcript.ready ───────►
               │                                  ┌─────────────────────────────────┐
               │                                  │ analyzer (LLM + Langfuse)        │
               │   event: analysis.ready ◄─────── │  prompt: langfuse (self-host)   │
               │  {summary_json_path}             │  LLM: OPENAI_COMPATIBLE_API_URL │
               │                                  │  → /artifacts/{task_id}/         │
               │                                  │     summary.json (facts+times)  │
               │                                  └─────────────────────────────────┘
               │
               │  event: analysis.ready ──────────────────────────────► Bot (consumer)
               ▼
        ┌───────────────────────────────────────────────────────┐
        │  Bot:  ✓ рендерит факты в шаблон templates/result.j2 │
        │        ✓ у каждого факта — время в видео (MM:SS)    │
        │        ✓ шлёт: текст-выжимку + документ transcript.srt│
        └──────────────────────────────────────────────────────┘
                              ▼
              целевая беседа (TARGET_CHAT_ID из .env)
```

Поток событий: `download.request → video.downloaded → transcribe.request →
transcript.ready → analysis.request → analysis.ready → bot: публикация`.
Ошибки на любой стадии → `job.failed` (fanout) → bot шлёт админу сообщение,
тело — в DLQ.

---

## 4. Микросервисы

### 4.1 Карта сервисов

| Микросервис | Ответственность | Зависимости/стек | Входные события | Исходящие события |
|---|---|---|---|---|
| **bot** | прием ссылок, валидация, публикация команд, потребление результата, рендер шаблона, отправка в TG | aiogram 3, Jinja2, aio-pika | `analysis.ready`, `job.failed` | `download.request` |
| **downloader** | скачивание медиа (адаптеры по источнику) в `/data/media/` | yt-dlp (движок платформ) + HTTP-загрузчик, aio-pika | `download.request` | `video.downloaded` / `download.failed` |
| **transcriber** | ffmpeg-извлечение аудио, faster-whisper, запись SRT+segments | faster-whisper (GPU), ffmpeg, aio-pika | `transcribe.request` | `transcript.ready` / `transcript.failed` |
| **analyzer** | получение промпта (langfuse self-host), вызов LLM, структурная выжимка JSON | langchain-core, openai-клиент, langfuse SDK | `analysis.request` | `analysis.ready` / `analysis.failed` |

Все сервисы навигаются в compose; сервис `bot` — единственный с Telegram.

### 4.2 Ports & Adapters (SOLID)

```
ports:                          adapters (внешние бэкенды, меняются через env):
────────────────────────────    ─────────────────────────────────────────────
MediaDownloader (protocol)      YandexDiskAdapter, GoogleDriveAdapter,
      │                        VkVideoAdapter, RutubeAdapter,
      │                        DirectFileAdapter, GenericPageAdapter,
      │                        FakeDownloader
Router: SourceRouter (по URL)   — распределяет по адаптерам
Transcriber (protocol)         FasterWhisperTranscriber (GPU) | FakeTranscriber
Summarizer (protocol)          LlmSummarizer (OpenAI-compatible) через OPENAI_COMPATIBLE_API_URL
PromptProvider (protocol)      LangfusePromptProvider (self-host) | LocalPromptProvider
MessageBus (protocol)          RabbitMessageBus (aio-pika) | FakeBus (in-memory)
```

Все решения по выбору адаптера — в **composition root** каждого сервиса
(`app_factory()`), конфиг — через `.env`.

---

## 5. События и очереди (RabbitMQ)

### 5.1 Контракты (языконейтральные, JSON Schema в `contracts/schemas/`)

Базовое событие:

```json
{
  "schema_version": 1,
  "message_id": "uuid4",
  "msg_type": "download.request",
  "task_id": "uuid4",
  "trace_id": "t-…",
  "created_at": "RFC3339",
  "source": {
    "url": "https://disk.yandex.ru/i/…",    // ССЫЛКА ТОЛЬКО ЗДЕСЬ (не в volume)
    "kind": "yandex_disk",                 // yandex_disk | gdrive | vkvideo | rutube | direct | generic
    "resource_id": "…"                     // id ресурса у источника (заполняет downloader)
  },
  "payload": { /* разное для каждого msg_type */ }
}
```

### 5.2 Каналы (queues/exchanges)

| Тип | Имя | Producer → Consumer | prefetch |
|---|---|---|---|
| work | `video.download` | bot → downloader | 1 |
| content | `video.transcribe` | downloader → transcriber | 1 |
| work | `video.analyze` | transcriber → analyzer | 1 |
| работу | `result.deliver` | analyzer → bot | 1 |
| fanout `job.events` | `job.failed`, `job.progress` | любой → bot (+ DLQ) | 1 |
| DLQ | `*.dlq` (по разным очередям) | — | — |

- **retry**: очередь с TTL → requeue; после N попыток → DLQ. Это штатив RabbitMQ.
- **trace_id** — в заголовке/полях события; проходят сквозь все сервисы и логи.

### 5.3 Описание событий (payload)

```jsonc
// download.request (bot → downloader)
{ "source_url": "…", "task_id": "…" }

// video.downloaded (downloader → transcriber)
{ "media_path": "/data/media/{task_id}.m4a",
  "resource_id": "abc-123", "source_kind": "yandex_disk",
  "title": "…", "duration_sec": 3600 }

// transcript.ready (transcriber → analyzer)
{ "base_dir": "/data/artifacts/{task_id}",
  "srt_path": "…/transcript.srt", "segments_path": "…/segments.json",
  "language": "ru", "duration_sec": 3600 }

// analysis.ready (analyzer → bot)
{ "base_dir": "/data/artifacts/{task_id}",
  "summary_path": "…/summary.json",
  "title": "…", "video_ref": { "url": "…", "kind": "yandex_disk", "resource_id": "…" } }

// job.failed (fanout)
{ "stage": "transcribe", "error": "детали", "task_id": "…", "trace_id": "…" }
```

---

## 6. Общие volume (файловая шина)

| Папка | Кто пишет | Кто читает | Содержимое |
|---|---|---|---|
| `/data/media/{task_id}.m4a` | downloader | transcriber | аудио (только до транскрипции) |
| `/data/artifacts/{task_id}/transcript.srt` | transcriber | bot (+тал.) | **выжимка транскрипции в SRT** |
| `/data/artifacts/{task_id}/segments.json` | transcriber | analyzer | сегменты с таймкодами для LLM |
| `/data/artifacts/{task_id}/metadata.json` | downloader | bot, analyzer | title, duration, video_ref |
| `/data/artifacts/{task_id}/summary.json` | analyzer | bot | **структурированная выжимка**: facts[] c start/end |
| `/data/models_cache/` | transcriber | — | кэш моделей faster-whisper |

Очистка: `media/` удаляется сразу после транскрипции; `artifacts/` — по TTL
(`ARTIFACTS_RETENTION_HOURS`), после удачной отправки результат остаётся кратко.

---

## 7. Downloader: адаптеры по источникам

Ссылкой может быть что угодно: Yandex Disk, Google Drive, VK Video, rutube,
прямая ссылка на файл, произвольный медиа-URL. Поэтому downloader — это
**роутер + набор адаптеров** с универсальным fallback.

```python
class DownloaderAdapter(Protocol):
    kind: str                                          # yandex_disk|gdrive|vkvideo|rutube|direct|generic
    def supports(self, url: str) -> bool: ...
    async def download(self, url: str, dst: Path, opts: Options) -> DownloadedMedia: ...
```

- **`SourceRouter`** определяет `kind` по URL (disk.yandex.ru, drive.google.com,
  vkvideo.ru, rutube.ru; URL, оканчивающийся на медиа-расширение → `direct`) и
  делегирует подходящему адаптеру; неизвестный источник → `generic`.
- Адаптеры:
  - `YandexDiskAdapter` — публичные/авторизованные ссылки Yandex Disk;
  - `GoogleDriveAdapter` — ссылки drive.google.com (в т.ч. `uc?export=download`);
  - `VkVideoAdapter`, `RutubeAdapter` — vkvideo.ru / rutube.ru;
  - `DirectFileAdapter` — любая прямая ссылка на файл (HTTP(S), редиректы, Range,
    докачка, заголовки авторизации/cookie);
  - `GenericPageAdapter` — универсальный: yt-dlp generic extractor для произвольных
    страниц, содержащих видео (fallback по умолчанию);
  - `FakeDownloader` — для тестов.
- Все адаптеры пишут в `/data/media/{task_id}.{ext}` и возвращают
  `DownloadedMedia(kind, resource_id, title, duration_sec, media_path)`.
- Авторизация/особые источники: `COOKIES_FILE`, `OAUTH_TOKEN` (по источнику),
  настраиваются через env; если источник не поддерживается — понятная ошибка
  в `download.failed` (не «висят» тихо).
- Ограничений на источник нет (любая ссылка); длина/размер — только общий лимит
  `MAX_VIDEO_DURATION_SEC`. Ошибка одного адаптера не влияет на другие: своя
  обработка → `download.failed` → `job.failed` → DLQ.

---

## 8. Транскрибация (faster-whisper, GPU)

- **faster-whisper** (CTranslate2), `WHISPER_BACKEND=faster-whisper` обязателен;
  GPU: `WHISPER_DEVICE=cuda`, `WHISPER_COMPUTE_TYPE=float16` (или int8_float16),
  модель по умолчанию `large-v3`/`distil-large-v3` (качество русского WER).
- ffmpeg: извлечение `16kHz mono wav` в tmp, затем транскрипция.
- Выход:**только `transcript.srt`** (формат SubRip с таймкодами) + `segments.json`
  (segment start/end/text — для LLM).
- Параметры: `WHISPER_MODEL`, `WHISPER_LANGUAGE` (auto по умолчанию), `batch_size`,
  `vad_filter`.
- GPU контейнер: `deploy.resources.reservations.devices` (nvidia), см. compose.

**Таймкод факта — это просто время в видео** (`start_sec`/`end_sec` из
`segments.json`), а не обязательный URL. Формат показа по месту публикации может
быть любым и выбирается на этапе рендера (§10):
- по умолчанию — текстовый таймкод `MM:SS` (иногда `HH:MM:SS`), указывающий,
  в какой момент видео был сказан факт;
- если источник позволяет позиционирование по времени и удобно — к времени можно
  добавить глубокую ссылку (`?t={sec}s` / `#t={sec}s`), но это опциональный
  UX-выигрыш, а не требование;
- `direct`/неизвестный источник — всегда только текст `MM:SS` (без URL).

В модели факта хранится только время (`start_sec`, `end_sec`); превращение времени
в ссылку/оформление — ответственность шаблона (см. §10 `TimeLinkPolicy`).

---

## 9. Анализ и выжимка (LLM + Langfuse)

### 9.1 Промпт
- **Langfuse self-hosted** (сервис в compose: `langfuse` + `postgres`).
- Промпт типа `chat`, имя `lecture-summary`, label `production`; правки — живой
  апдейт без деплоя.
- `PromptProvider` по умолчанию — `LangfusePromptProvider` (self-host URL из env);
  fallback — `LocalPromptProvider` (файл `prompts/lecture-summary.jinja`) для тестов.

### 9.2 LLM
- Клиент: OpenAI-совместимый (`langchain-openai` `ChatOpenAI(base_url=…)`),
  адрес и модель — **из `.env`**:
  ```
  OPENAI_COMPATIBLE_API_URL="https://api.my-provider.example/v1"
  LLM_MODEL_ID="gpt-4o-mini"
  OPENAI_COMPATIBLE_API_KEY="…"
  ```
  Подходит для: OpenAI API, Ollama (`http://ollama:11434/v1`), LM Studio, vLLM,
  YandexGPT-совместимый и др.
- Трассировка: `langfuse CallbackHandler(session_id=task_id, tags=["swot"])`
  — всё (промпт/ответ/метаданные) видно в Langfuse UI.

### 9.3 Выжимка (структурированная)
LLM возвращает **JSON** (не просто текст):

```json
{
  "title": "Название лекции",
  "summary": "краткое резюме",
  "sections": [
    {
      "heading": "Введение по теме X",
      "facts": [
        { "text": "автор утверждает, что …", "start_sec": 1243, "end_sec": 1361 },
        { "text": "пример: …", "start_sec": 1480, "end_sec": 1500 }
      ]
    }
  ]
}
```

- `summary.json` пишется в артефакты; `start_sec` — время в видео, в котором
  был сказан факт.
- При рендере (§10) `start_sec` превращается в человекочитаемое время `MM:SS`
  (а при желании — в глубокую ссылку), которым помечается каждый факт.

---

## 10. Публикация в Telegram: файл-шаблон

### 10.1 Шаблон
Выжимка отправляется через **отдельный файл-шаблон** (Jinja2),
`services/bot/templates/result_message.html.j2` — монтируется в bot-образ и
рендерится из `summary.json`. Каждый факт дополняется **временем в видео**
(`chrono(start_sec)` → `MM:SS`), где это было сказано.

```html
{% for section in sections %}
{{ section.heading | safe }}
{% for fact in section.facts %}
{{- chrono(fact.start_sec) }}  {{ fact.text | safe }}
{% endfor %}
{% endfor %}
```

- В шаблоне доступны: `sections[]` (facts с start_sec/end_sec), `video_ref`
  (kind, url, resource_id), `title`, `meta` (duration, lang).
- `chrono(sec)` — функция-фильтр Jinja2: `секунды → MM:SS/HH:MM:SS` (время факта,
  не обязательно URL).
- Необязательно: `link_time(fact, video_ref)` может превратить `chrono` в глубокую
  ссылку по типу источника (`?t=`/`#t=`), если его плеер поддерживает позиционирование
  (`TimeLinkPolicy` в §8); для `direct`/неизвестных — остаётся текст `MM:SS`.
- Если шаблон монтируется volume (не в образ) — правится без пересборки.

### 10.2 Итоговое сообщение
- **Текст сообщения** = рендер шаблона (выжимка, у фактов — время в видео `MM:SS`).
- **Документ** = `/data/artifacts/{task_id}/transcript.srt` (файл-транскрипт).
- Лимиты TG: текст ≤ 4096 символов (пагинация, если длиннее — слайсы);
  документ ≤ 50 MB (SRT длинной лекции — хорошо вписывается).

### 10.3 Маппинги (`env`, без Redis)
- `ADMIN_ID` — единственный, кто может слать ссылки.
- `TARGET_CHAT_ID` — беседа для результата.
- Никакого Redis: id-чатов и статусы (насколько нужно) — в env и в events.

---

## 11. Observability

- Сквозной `trace_id` в каждом событии; логи — JSON (structlog), поля:
  `task_id/trace_id/stage/status`.
- health: `/healthz` и `/readyz` в каждом сервисе; `healthcheck` в compose.
- Langfuse: трассы LLM (input/output/промпт версии) + метрики стадий
  (download/transcribe/analyze) — кастомные span внутри цепочек.
- Метрики-счётчики (опц.): events на стадию, длительность этапов, ошибки,
  размеры файлов — prometheus endpoint (`/metrics`) в сервисах, grafana overlay.

---

## 12. Репозиторий

```
swot-bot/
├── docker-compose.yml            # все микросервисы + rabbit + langfuse(+pg)
├── docker-compose.observability.yml  # prometheus+grafana (opt)
├── contracts/
│   └── schemas/                  # JSON Schema событий (языконейтральные)
├── services/
│   ├── bot/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── src/swot_bot/         # handlers, admin_filter, validator,
│   │   │                         #   publisher, consumer, renderer
│   │   └── templates/result_message.html.j2   # файл-шаблон (Jinja2)
│   ├── downloader/
│   │   ├── Dockerfile
│   │   └── src/swot_downloader/  # SourceRouter + адаптеры
│   ├── transcriber/
│   │   ├── Dockerfile            # ffmpeg, faster-whisper, GPU runtime
│   │   └── src/swot_transcriber/ # ffmpeg extract, whisper adapter
│   └── analyzer/
│       ├── Dockerfile
│       └── src/swot_analyzer/    # Langfuse prompt, LLM, summary.json
├── prompts/
│   └── lecture-summary.jinja     # fallback-промпт
├── tests/
│   ├── unit/                     # на уровне сервисов (fake-адаптеры)
│   └── integration/              # compose: rabbit + fake-бэкенды → e2e
├── scripts/seed_prompt.py        # создать/обновить промпт в langfuse
├── .env.example
└── docs/plan.md, docs/architecture.md
```

Правило композиции: **каждый сервис — самодостаточный каталог** (свой
`pyproject.toml`/Dockerfile); python-сервисы делят пакеты-контракты либо
генерируют модели из JSON Schema. Контракты между сервисами — только Schemas
+ SRT/JSON-файлы + Jinja2-шаблон.

---

## 13. Docker Compose

```yaml
services:
  rabbitmq:
    image: rabbitmq:4-management
    healthcheck: …

  postgres:      # для langfuse
    image: postgres:16-alpine
    environment: { POSTGRES_DB: langfuse, POSTGRES_USER: langfuse, POSTGRES_PASSWORD: … }
    volumes: [langfuse_pg_data:/var/lib/postgresql/data]

  langfuse:      # self-hosted
    image: langfuse/langfuse:latest
    depends_on: [postgres]
    environment:
      DATABASE_URL: postgres://langfuse:…@postgres:5432/langfuse
      LANGFUSE_*: …
    ports: ["3000:3000"]

  bot:
    build: services/bot
    env_file: .env
    depends_on: [rabbitmq]

  downloader:
    build: services/downloader
    env_file: .env
    volumes: [swot_media:/data/media, swot_artifacts:/data/artifacts]
    depends_on: [rabbitmq]

  transcriber:
    build:
      context: services/transcriber
      args: { WHISPER_BACKEND: faster-whisper }
    env_file: .env
    volumes: [swot_media:/data/media, swot_artifacts:/data/artifacts,
              whisper_models:/data/models]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    depends_on: [rabbitmq]

  analyzer:
    build: services/analyzer
    env_file: .env
    volumes: [swot_artifacts:/data/artifacts]
    depends_on: [rabbitmq, langfuse]

  # опциональный профиль: prometheus + grafana (см. observability.yml)

volumes:
  swot_media:
  swot_artifacts:
  whisper_cache:
  langfuse_pg_data:
  rabbitmq_data:

networks:
  default:
    name: swot-net
```

Все сервисы в одном compose; `transcriber` — GPU (nvidia runtime), `langfuse`
само-развёртываемый с `postgres`.

---

## 14. Конфигурация `.env`

```env
# Telegram
BOT_TOKEN=
ADMIN_ID=12345678
TARGET_CHAT_ID=-1001234567890

# Broker
RABBIT_URL=amqp://guest:guest@rabbitmq:5672/

# Паттерны путей
MEDIA_DIR=/data/media
ARTIFACTS_DIR=/data/artifacts

# Downloader
# Ссылка может быть любой: Yandex Disk, Google Drive, прямое скачивание и т.д.
# Список поддерживаемых источников расширяется адаптерами; ограничение их не требуется
ALLOWED_SOURCES=                # пусто = любые ссылки (опциональный allowlist)
MAX_VIDEO_DURATION_SEC=7200

# Whisper (transcriber) — faster-whisper, GPU
WHISPER_BACKEND=faster-whisper
WHISPER_MODEL=distil-large-v3     # или large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=int8_float16 # или float16
WHISPER_LANGUAGE=                 # пусто = автоопределение
WHISPER_BATCH_SIZE=4

# LLM (OpenAI-совместимый endpoint)
OPENAI_COMPATIBLE_API_URL=https://api.example.com/v1   # Ollama: http://ollama:11434/v1
OPENAI_COMPATIBLE_API_KEY=…
LLM_MODEL_ID=gpt-4o-mini
LLM_TEMPERATURE=0.3
PROMPT_NAME=lecture-summary

# Langfuse (selfhosted)
LANGFUSE_BASE_URL=http://langfuse:3000
LANGFUSE_PUBLIC_KEY=…
LANGFUSE_SECRET_KEY=…

# Observability
LOG_LEVEL=INFO
ARTIFACTS_RETENTION_HOURS=168
```

---

## 15. Интеграция и тесты

### 15.1 Юнит (fake-адаптеры)
- URL-валидация приёма, admin-фильтр, роутинг `SourceRouter`
- рендер шаблона (Jinja2: у фактов время `MM:SS`, HTML escape, длинные — split);
- ffmpeg-команда (mock subprocess); whisper-adapter (fake-адаптер) gpt;
- summary.json → conversation по фактам.

### 15.2 Интеграционные (compose `integration`, реальный Rabbit, fake-бэкенды)
- `FakeDownloader` (генерит файл), `FakeTranscriber` (фикс. SRT), `StubLLM`
  (структурная выжимка без сети) — пайплайн от команды до рендера и «отправки».
- Проверки: srt-контент, summary.json-формат, время фактов (`MM:SS`) в тексте,
  трапы значений из .env.

### 15.3 E2E-быстрые (выключены в CI)
- Реальный faster-whisper на 10-сек семпле (GPU опц.), реальный
  OpenAI-совместим. напрямяк — инверсионно, локально.

---

## 16. Открытые вопросы (не критичны)

1. Приоритет источников для адаптеров на старте (Yandex Disk, Google Drive,
   VK Video, rutube, прямые ссылки)? Нужны ли cookie/токены для закрытых источников?
2. Формат выжимки: резюме + секции с фактами достаточно, или же более простое
   (плоский список фактов)? — сейчас задаётся промптом (правится в Langfuse без код-изменён).
3. Показ времени фактов: достаточно ли текстового `MM:SS`, или для части
   источников добавлять глубокую ссылку (`?t=`/`#t=`), если плеер поддерживает
   позиционирование?
4. Слишком длинный transcription SRT для чата — отправлять одним документом
   либо слайсами (> 45 МБ)?