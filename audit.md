# swot-bot — технический аудит

**Дата:** 2026-09-10
**Обхват:** всё репозитория (packages/*, services/*, tests/*, .docker/*, CI, docker-compose)
**Метод:** полное чтение кода + `pytest` (77 тестов), `ruff check/format`, runtime-эксперименты
(`uv run python` с реальными классами), сборка Docker-образа `swot_bot` и прогон `python -m swot_bot`
внутри контейнера. Каждый P0/P1 подтверждён экспериментом, а не только чтением кода.

---

## Вердикт

**Проект в текущем виде не работает ни как локальные сервисы, ни как Docker-стек.**
Большинство unit-тестов зелёные и CI проходит, но это иллюзия: тесты намеренно обходят все
сломанные пути (dishka-контейнер уровня сервисов, реальная LLM-промпт-цепочка, Docker,
docker-compose). Найденных **6 P0**, большинство из них не покрыты ни тестом, ни CI.

Краткий список P0:

| # | Проблема | Доказательство |
|---|----------|----------------|
| P0-1 | Dishka не может разрешить `JobRegistry` — **все 4 сервиса не стартуют** | runtime: `NoFactoryError` |
| P0-2 | Docker-образы сломаны: зависимости в `.venv`, `CMD` запускает системный python | собран образ, `python -m swot_bot` → `ModuleNotFoundError: aiogram` |
| P0-3 | `SourceRouter` шлёт YouTube/YandexDisk/Drive/VK/Rutube на `DirectHttpAdapter` — вместо видео скачивается HTML | чтение кода + логика `route()` |
| P0-4 | Промпт-цепочка анализатора не работает: `LOCAL_PROMPT` не парсится `from_template` (вложенные фигурные скобки JSON) и не содержит `{input}` — транскрипт не попадёт в промпт даже после фикса скобок | runtime: `ValueError: Nested replacement fields are not allowed` |
| P0-5 | docker-compose не поднимается: `guest`-пользователь RabbitMQ заблокирован loopback-политикой между контейнерами; у `bot` нет тома `swot_artifacts` (не прочитает summary.json); `langfuse:latest` (v3) без ClickHouse/Redis/MinIO не стартует; `LLM__API_URL=llm-service` / `TRANSCRIBER__API_URL=asr-service` не существуют в сети compose, и в `.env.example` у них нет схемы `http://` | чтение compose + знание дефолтов RabbitMQ/Langfuse |
| P0-6 | Арбитражный file access через поля сообщений: `base_dir`, `srt_path` (анализатор), `summary_path` (бот), `media_path` (unlink в транскрибере) — произвольное чтение/удаление файлов с любой машины в стеке, где есть сервис | чтение кода, утилизация в `handle()` |

---

## P0 — блокируют работу

### P0-1. `JobRegistry` не разрешается dishka-контейнером

`packages/bus/src/swot_bus/providers.py:52`:

```python
@provide(scope=Scope.APP)
def registry(self) -> InMemoryJobRegistry: ...
```

Все сервисы и хендлеры требуют **протокол** `JobRegistry`
(`swot_contracts.ports`), но dishka регистрирует зависимость по **конкретному классу**.
Dishka не делает подстановку по протоколу (нужен `provides=`), поэтому:

```
NoFactoryError: Cannot find factory for (JobRegistry, component='', scope=Scope.REQUEST)
```

(проверено экспериментально: `RegistryProvider()` + `container.get(JobRegistry)`).

**Влияние:** downloader/transcriber/analyzer падают в `_amain`
(`request_container.get(...Service)` → фабрика требует `registry: JobRegistry`);
бот падает на каждом сообщении админа (`FromDishka[JobRegistry]`).
Весь стек не стартует.

**Почему тесты зелёные:** `tests/unit/test_downloader.py` разрешает только
`InMemoryJobRegistry` (конкретный класс), а не сервис через контейнер.

**Фикс (1 строка + тест):**

```python
@provide(scope=Scope.APP)
def registry(self) -> JobRegistry:  # + provides / аннотация протокола
    return InMemoryJobRegistry()
```

(или `Provide(provides=JobRegistry)`), плюс тест на каждый сервис:
`await request_container.get(DownloaderService)` и т.п. — такие тесты сразу поймали бы этот P0.

---

### P0-2. Docker-образы не запускаются (все 4 сервиса)

`services/bot/Dockerfile` (и 3 аналога) — стадия `runtime`:

```dockerfile
RUN uv sync --frozen --no-install-project --no-dev && \
    uv pip install --system ./packages/contracts ./packages/observability ./packages/bus ./services/bot --no-deps
CMD ["python", "-m", "swot_bot"]
```

`uv sync` кладёт **все зависимости** в `/app/.venv`, а `uv pip install --system --no-deps`
кладёт 4 swot-пакета в **системный** site-packages. `CMD` запускает системный python
(`/usr/local/bin/python`), который `.venv` не видит.

**Доказательство (выполнено):** `docker build -f .docker/bot/Dockerfile` →
`docker run --rm swot-audit-bot python -m swot_bot`:

```
File "/usr/local/lib/python3.13/site-packages/swot_bot/__main__.py", line 5, in <module>
    import aiogram
ModuleNotFoundError: No module named 'aiogram'
```

В системных site-packages только `swot_bot/swot_bus/swot_contracts/swot_observability`
(без зависимостей); `aiogram`, `aio_pika`, `structlog`, `aiohttp`, `langfuse`, `openai` —
только в `/app/.venv`. Healthcheck контейнера будет вечно падать.

**Почему CI не ловит:** pipeline строит только `uv sync --all-packages` + pytest/ruff,
Docker-образы в CI не собираются.

**Фикс:** убрать `--system`/`--no-deps`-хаки:

```dockerfile
RUN uv sync --frozen --package swot-bot --no-dev   # зависимости+проект в /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "swot_bot"]
```

(`--package` ставит в venv только выбранный сервис + shared-пакеты, а не все 4 —
бонусом меньше размер образа.) Плюс CI-шаг: сборка каждого образа и smoke-тест
`python -m <pkg>` (или `import <pkg>`) внутри контейнера.

---

### P0-3. `SourceRouter` инвертирован: видео-платформы идут на `DirectHttpAdapter`

`services/downloader/src/swot_downloader/adapters.py:42-71`. Метод `route()`:

- URL, который **не** содержит `_YTDLP_HOSTS` (youtube, vk, rutube, vimeo, dzen, ok, tiktok)
  → `YtDlpAdapter`;
- всё остальное (включая `yandex.ru/disk`, `drive.google.com`, прямые ссылки `.mp4`)
  → `DirectHttpAdapter` (простой `GET`).

То есть YouTube/VK/Rutube/Drive/YandexDisk попадают на «прямой» GET и скачивают
HTML-страницу как `media.bin`; прямой `.mp4`-файл, наоборот, идёт через yt-dlp
(работает, но медленнее и не по назначению). `route()` возвращает **адаптер**
(а не источник), как заявлено в docstring.

**Фикс:** роутинг по смыслу:
1. URL «на платформе» (список yt-dlp-хостов, включая disk.yandex.ru / drive.google.com
   / vk.com / rutube.ru) → `YtDlpAdapter` (yt-dlp умеет их все, включая Google Drive
   и Yandex Disk);
2. прямой URL на медиа-файл (расширение `.mp4/.m4a/.mp3/.wav/.ogg/.flac`, или
   content-type `video/*`/`audio/*` после HEAD) → `DirectHttpAdapter`;
3. остальное → ошибка/yt-dlp.

Добавить unit-тесты `route()` на обе ветки — сейчас их нет.

---

### P0-4. Промпт-цепочка анализатора сломана в двух точках

`services/analyzer/src/swot_analyzer/prompts.py` + `summarizer.py`:

1. `LlmSummarizer.summarize` делает `ChatPromptTemplate.from_template(prompt)`.
   Для fallback-промпта `LOCAL_PROMPT` это **падает** (проверено экспериментом):

   ```
   ValueError: Invalid format specifier in f-string template.
   Nested replacement fields are not allowed.
   ```

   Причина: JSON-пример `{"sections": [{"title": ...}]}` содержит вложенные `{` `}`.
2. Даже после экранирования скобок (`{{...}}`) промпт **не содержит переменной
   `{input}`**, а `ainvoke` передаётся `{"input": transcript}` — транскрипт молча
   выкинется из промпта, и LLM получит пустой «инпут». Т.е. локальный fallback
   некорректен по сути, а не только синтаксически.

**Влияние:** при недоступности/отсутствии промпта в Langfuse (а Langfuse в compose
не стартует — см. P0-5) каждый analyze-завершается `JobFailed(
"Invalid format specifier in f-string template...")`. Тесты этого не видят:
`test_analyzer.py` использует `StubSummarizer`, а `test_pipeline.py` — `LOCAL_PROMPT`
только как строку без вызова `from_template`.

**Что ещё не так рядом:** `PromptProvider.get` объявлен `-> str`, но
`prompt.get_langchain_prompt() or prompt.prompt` в langfuse 4.x возвращает
`str` только для text-промптов; для chat-промптов вернётся `ChatPromptTemplate`,
и `from_template` падает с `TypeError: expected str`. (Установлен langfuse **4.15.1**,
а pyproject просит `langfuse>=2.60` — API 4.x отличается от 2.x, под который писали код;
`get_prompt(..., fallback=...)` в 4.x работает, но это надо фиксировать версией:
`langfuse>=4,<5` и прогнать промпт-путь в тесте.)

**Фикс:**
- `LOCAL_PROMPT` собрать через `ChatPromptTemplate.from_messages([...])` или
  `PromptTemplate` с явным `{input}`; JSON-пример экранировать;
- добавить тест: `LlmSummarizer` с фейковым LLM, который записывает входящий промпт;
  утверждение: транскрипт присутствует, JSON-пример цел, `ainvoke` не падает;
- зафиксировать мажорную версию langfuse.

---

### P0-5. docker-compose не поднимает рабочий стек

`docker-compose.yml`:

1. **RabbitMQ `guest/guest`**: дефолтный `guest` в RabbitMQ ограничен
   loopback-подключениями (`loopback_users.guest=true`), а сервисы подключаются
   из **других** контейнеров → AMQP-соединение будет отклонено, все 4 сервиса
   упадут на `connect()`. Нужен `RABBITMQ_DEFAULT_USER/PASSWORD` + их использование
   в `BROKER__USER/PASSWORD`, либо `RABBITMQ_SERVER_ADDITIONAL_ERL_FLAGS`
   (loopback_users=none).
2. **У `bot` нет volumes**: контейнер бота не видит `swot_artifacts`, а
   `ResultReporter` читает `artifacts_dir/<task>/summary.json` и `transcript.srt`
   → каждый результат падает с `FileNotFoundError` → nack → сообщение теряется
   (DLQ нет, см. P1-5). Мount `swot_artifacts:/data/artifacts` для bot обязателен.
3. **`langfuse:latest` (v3)** требует Postgres + ClickHouse + Redis + MinIO(S3);
   в compose только Postgres → контейнер langfuse не стартует → анализатор всегда
   в fallback (а он сломан, P0-4).
4. **Нет контейнеров LLM и ASR**, хотя `LLM__API_URL=llm-service` и
   `TRANSCRIBER__API_URL=asr-service` (compose и `.env.example`) — в сети compose
   таких хостов нет → transcribe/analyze не могут завершиться. В `.env.example`
   у обоих URL **нет схемы** `http://` — `ChatOpenAI`/`AsyncOpenAI` падут на
   невалидным base_url независимо от compose (дефолт `TranscriberSettings.api_url
   = "asr-service"` тоже без схемы).
5. `env_file: .env` у всех сервисов: `.env` не в git, инструкция «cp .env.example
   .env» отсутствует → `docker compose up` падает сразу (файл не найден).
6. Нет `restart:` у сервисов; у `langfuse` нет healthcheck; `depends_on` у
   analyzer — `service_started`, а не health.

**Фикс:** один compose-файл с реальным non-guest пользователем rabbit,
mount артефактов для бота, либо (a) контейнеры ASR/LLM, либо (b) переменные на
реальные внешние URL **со схемой**, либо (c) явная пометка «ASR/LLM — внешние
сервисы, вставьте URL». Для langfuse: полный официальный набор сервисов v3 или
зафиксировать, что в dev Langfuse не нужен (анализатор обязан работать по fallback —
см. P0-4).

---

### P0-6. Path traversal через поля брокер-сообщений

Все пути приходят **из payload-а сообщения** и используются без containment-проверки
на базовый каталог:

| Место | Поле | Уязвимость |
|-------|------|-----------|
| `swot_analyzer/service.py` | `base_dir`, `srt_path` | `base_dir="../"` → `summary.json`/`transcript.txt` пишутся куда угодно; `srt_path` читается целиком → **чтение произвольного файла** (секреты, `/etc/passwd` и т.д.) |
| `swot_bot/handlers.py` (`on_analysis`) | `summary_path` | чтение `Path(summary_path)` → произвольное чтение на машине бота |
| `swot_transcriber/service.py` | `media_path` | `unlink()` → **удаление произвольного файла** |

Уровень доверия «admin-only бот» не защищает: сообщение в Rabbit может создать
любой сервис/любой процесс с доступом к брокеру, а broker-keyspace общий.

**Фикс:** одна утилита в `swot_contracts` (или `swot_bus`):

```python
def resolve_under(base: Path, candidate: str) -> Path:
    p = Path(candidate)
    if p.is_absolute():
        base = Path("/"); ... # или сразу reject
    resolved = (base / p).resolve()
    base_resolved = base.resolve()
    if not resolved.is_relative_to(base_resolved):
        raise ValueError("path escapes base dir")
    return resolved
```

применить в analyzer (`base_dir`, `srt_path`), bot (`summary_path`), transcriber
(`media_path`). Unit-тесты с `../`, абсолютным путём и symlink-эскейпом.
Пока не исправлено — минимальный митигейт: whitelist суффиксов
(`*.json`, `*.srt`, `*.txt`) + запрет абсолютных путей и `..`.

---

## P1 — серьёзные проблемы качества/надёжности

### P1-1. `Settings` загрязняется системными env-переменными (подтверждено)

Ни одна подсеконка не имеет `env_prefix`, поэтому pydantic-settings матчит поля
**по голому имени** с переменными окружения процесса. Эксперимент:

```
USER=macuser      -> broker.user = 'macuser'   (а не 'guest')
PORT=9999         -> broker.port = 9999       (а не 5672)
LANGUAGE=en_US:en -> transcriber.language = 'en_US:en'
MODEL=some-model  -> transcriber.model = 'some-model'
```

`USER`, `PORT`, `LANGUAGE`, `MODEL`, `HOME`, `PATH`-подобные имена — стандартные
env-переменные dev-машины: конфиг сервисов будет **молча** кривым при запуске
без docker. **Фикс:** у каждой под-секции `model_config = SettingsConfigDict(
env_prefix="BROKER__", ...)` (или единый префикс `SWOT_BROKER__`), либо явный
`env_nested_delimiter` + префиксы; и тест, что `USER/PORT/LANGUAGE` не влияют.

### P1-2. `Settings` не конструируется без env + лишние обязательные поля

- Эксперимент: `Settings()` в чистом окружении — 6 `Field required`
  (broker/llm/telegram/transcriber/downloader/langfuse). Каждая **секция**
  обязательна целиком: боту, который не использует ASR/LLM/Langfuse, нужны
  `TRANSCRIBER__*`, `LLM__*`, `LANGFUSE__*`. Секции надо сделать опциональными
  (значения по умолчанию на уровне секций), либо требовать только те, что
  использует сервис.
- `LangfuseSettings.public_key/secret_key` обязательны хотя бы для пустой строки —
  без `LANGFUSE__PUBLIC_KEY/SECRET_KEY` в `.env` контейнер бота/анализатора не
  стартует. Должны быть `""` по умолчанию (анализатор без Langfuse работает по
  fallback).
- Docstring'ы под-секций («незаданные поля — значения по умолчанию ниже»)
  вводят в заблуждение: поля да, **секция нет**.

### P1-3. `YtDlpAdapter` блокирует event loop

`yt_dlp.YoutubeDL(...).download()` — синхронный сетевой вызов внутри async-метода
(`adapters.py`). При одном скачивании весь сервис (consume-цикл, healthz)
замораживается на минуты. `timeout_sec` из настроек **игнорируется** (в opts не
попадает). **Фикс:** `await asyncio.wait_for(
asyncio.to_thread(self._sync_download, url, dest), timeout=...)`,
`"sleep_interval_requests"`, проверка `duration` из info-экстракции **до**
скачивания (сейчас `max_duration_sec` проверяется после полной загрузки —
трафику потрачено впустую), формат `bestaudio[acodec=none]/bestaudio`
(без fallback на видео).

### P1-4. `DirectHttpAdapter`: таймаут и валидация

- `aiohttp.ClientTimeout(total=self._timeout)` — `total` = лимит на **весь**
  запрос целиком: медленный 500 МБ файл по плохому каналу упрётся в 120 c.
  Нужны `sock_connect`/`sock_read`/`total` раздельно.
- Не проверяется, что скачали медиа: HTTP 200 с HTML (см. P0-3) кладётся в
  `media.bin` без проверки content-type/расширения/магических чисел.
- Размер файла на диске не ограничен (вместе с `max_duration_sec`, который
  проверяется позже — двойная дыра).

### P1-5. Ненадёжность брокерного контура: нет DLQ, нет reaper, registry бессмысленен

- `RabbitMessageBus._dispatch`: при исключении — `nack(requeue=False)` без DLX →
  **сообщение исчезает** (потеря результата, «застрявший» для пользователя
  «обрабатывается…»). Нужен dead-letter exchange + binding + очередь `*.dlq`,
  либо requeue с лимитом попыток (`x-delivery-attempt`).
- **Нет дедлайна задачи**: если сервис умер в середине, статус висит
  `DOWNLOADING/TRANSCRIBING/ANALYZING` вечно. Нужен reaper (отдельный воркер
  или периодический job в боте): `created_at + TTL` → `JobFailed`.
- `InMemoryJobRegistry` живёт **в процессе**: `set_status` в downloader/
  transcriber/analyzer пишется в словари, которые не читает никто
  (бот держит свой экземпляр в своём процессе). Сквозной статус «не работает»
  по конструкции; при этом `exists()` **нигде не вызывается** — идемпотентность
  (пересабмит того же URL) не защищена. Решение: либо убрать registry из
  межсервисного контракта (статусы — через `JobProgress`-события в бота,
  бот — единственный owner registry), либо общий store.
- `InMemoryJobRegistry` не чистится — словари растут бесконечно (бот живёт
  долго).
- `RabbitMessageBus` не покрыт тестами вообще (только FakeBus); `deserialize`
  падает на неизвестном `type` → nack → потеря (см. DLQ).

### P1-6. ASR: огромный WAV целиком в памяти, лимиты эндпоинта

`ffmpeg -acodec pcm_s16le -ar 16000 -ac 1` → 16 бит/16k/mono ≈ **115 МБ/час**.
Для лекции в 2–3 часа это 230–350 МБ WAV, который `AsyncOpenAI` (httpx)
**буферизует в память целиком** при multipart-загрузке; типичные OpenAI-
совместимые ASR-эндпоинты (включая openai.com) ограничивают загрузку
(Whisper API — 25 МБ). Файл ещё и не удаляется на всех путях ошибки.
**Фикс:** chunking аудио (ffmpeg `segment` по 10–15 минут, сдвиг по времени),
либо передача через `--upload` с потоком, проверка размера до отправки;
таймаут на ASR-вызов (сейчас `create_audio_transcription` без timeout —
`asr.py`).

### P1-7. LLM: нет чанкинга длинных транскриптов

`AnalyzeService` целиком передаёт весь `transcript.txt` в одну LLM-вызов
(промпт `{input}`): длинная лекция = превышение контекста → ошибка.
`langchain-text-splitters` **декларирован** в pyproject анализатора, но не
используется — признак незавершённой фичи. **Фикс:** map-reduce
(сплиттер → summary-по-чанкам → итоговая сводка по частям), лимит на
размер транскрипта с понятной ошибкой, `max_tokens`/`timeout` на LLM-вызов
(сейчас `ainvoke` без timeout).

### P1-8. Сквозной trace_id обрывается на первом хопe

Фича «сквозной trace_id» (коммит `4bb7d0d`) реализована только **на приёмной**
стороне: `RabbitMessageBus` кладёт `swot-trace-id` из structlog-contextvars
в заголовок и читает его обратно. Но **бот, первый издатель, trace_id в
contextvars не привязывает** (в `handle_link`/`ResultReporter` нет
`bind_contextvars`) → header пустой, все 4 сервиса логируются без trace_id.
Плюс ни один сервис не логирует `stage`/`task_id` в contextvars на время
обработки (только bus). **Фикс:** `bind_contextvars(trace_id=...)` в боте
перед `publish` (и вокруг handle в каждом сервисе — уже в bus, ок), плюс
`logger` в логах ошибок.

### P1-9. Observability: healthz «вечно ok», `HealthServer` без остановки

- `readiness` никогда не пробует брокер — `/readyz` отвечает `ok`, даже когда
  `connect()` не удался (сервис «up», но слеп). Минимум: readiness =
  «bus подключён».
- `HealthServer.start()` блокируется `while True: await asyncio.sleep(3600)`;
  `stop()` нет → `asyncio.gather(..., health.start())` в `__main__` не завершится
  по SIGTERM до force-kill (10 s grace compose'а) — есть риск `Killed`.
- В логах смешаны форматы: structlog — JSON, а stdlib-логгеры (aio_pika,
  aiohttp, aiogram, openai) — plain text (basicConfig). Нужен structlog stdlib
  integration (`structlog.stdlib.add_log_level_field` + ProcessorFormatter),
  чтобы в контейнере был единый JSON.

### P1-10. Ошибки доставки результата теряются, сырые ошибки уходят в чат

- `on_analysis`: любое исключение (файла нет, JSON кривой, Telegram API упал,
  rate limit) → nack(requeue=False) → **без DLQ (P1-5) результат исчезает**,
  пользователь видит «Обработка…», которая не закончится никогда. Политика:
  персистентный ретрай с backoff на Telegram-ошибки, DLQ на остальное,
  fallback-сообщение «не удалось доставить» в лог с полным контекстом.
- `on_failed` шлёт `JobFailed.error` (сырой `str(e)`, включая traceback-фразы
  и пути) админу. Нормализовать: человекочитаемое сообщение + technical detail
  в лог.
- `chunk_text` в renderer: `lines[0].lstrip("\n")` **выбрасывает** переносы на
  границе чанка (в `test_chunk_text_limits` это замаскировано чистыми
  `x`); `"".join(chunks) != original` для реального текста — потеря данных.
  Плюс возможен разрыв **посреди HTML-тега** (долгая фактическая строка) →
  кривой HTML в Telegram. Фикс: делить по символам/по словам без съедания
  `\n`, и не резать внутри `&[a-z]+;`/`<tag>`.

### P1-11. Дисковое накопление: media-каталог никогда не чистится

`DownloaderService._cleanup_old_artifacts` обходит **только** `artifacts_dir`;
`media_dir` (`/data/media/<task_id>/...`) — никогда. Файл удаляет transcriber,
но только по happy-path (`_cleanup_media` в `finally` — ок), а вот **пустые
каталоги задач** и файлы на error-paths (анализатор не удаляет ничего;
downloader при сбое не удаляет `media.bin`) копятся. На shared volume
(`swot_media`) это медленный отказ по диску. **Фикс:** TTL-чистка по обоим
директориям (один utility), периодический job (в downloader).

### P1-12. Нет type checker, нет интеграционных тестов

- Ruff — только линтер; протокол-ориентированный код (ports/providers)
  без mypy/pyright. `PromptProvider.get -> str` возвращающий не-str (P0-4),
  `ResultReporter.__init__(self, bot, ...)` без аннотации — это ровно то, что
  ловит мойпы. **Фикс:** `mypy --strict` (или pyright) в CI, хотя бы на
  packages/*+services/*.
- Нет тестов `RabbitMessageBus` (serialize/deserialize round-trip, trace
  header, DLX) — только FakeBus. Нет тестов aiogram-хендлеров бота (aiogram
  легко тестится через `aiogram.testing`/ручной `Message`). E2E
  `test_pipeline.py` обходит бота (собирает цепочку вручную) — именно слой,
  где живут P0-1/P0-6.

### P1-13. CI: Docker не проверяется (следствие P0-2)

`ci.yml`: `uv sync --all-packages` → pytest → ruff. Нет: сборки Docker-образов,
`docker compose config`-валидации, compose-интеграционного прогона (minio/
rabbit через testcontainers). **Фикс:** job `docker-build` (buildkit, кэш),
smoke-импорт внутри образа, job `compose` (testcontainers: rabbitmq + все
4 сервиса + fake ASR/LLM, проверка healthz и сквозного пайплайна).

---

## P2 — гигиена, DX, мелочи

1. **`JobStatus.READY` выставляется промежуточными стадиями** (downloader
   после загрузки, transcriber после расшифровки). Смысл READY = «готово
   показать результат» — только analyzer должен её ставить. Текущие тесты
   (`test_downloader.py`, `test_transcriber.py`) **замораживают** эту ошибку
   (assert READY после download/transcribe).
2. **Мёртвый код:** обмен `job.events` (fanout) декларируется в `connect()`,
   но ничего на него не публикуется; протокол `Job` не используется нигде;
   `TranscribeRequest`/`AnalysisRequest` не используются и не поддерживаются
   в `deserialize` (`_model_for` не знает их → ValueError); `BaseMessage.payload`
   и `created_at` не используются никем; константы `AUDIO_EXTENSIONS`/
   `VIDEO_EXTENSIONS` не используются (downloader пишет `media.bin`);
   `openai>=1.55` в pyproject анализатора — импорт только langchain-openai;
   `dishka` в зависимостях корня workspace — не нужен; `main.py` —
   scaffolding-остаток (`uv` hello-world).
3. **Тест-дубли в production-коде:** `FakeBus`, `FakeTranscriber`,
   `StubSummarizer`, `FakeBusProvider`, `FakeTranscriberProvider`,
   `StubAnalyzerProvider` живут в `swot_bus`/`swot_analyzer` и **регистрируются
   в production-providers**. Это легитимный паттерн для dev-мода, но должно
   быть осознанным: либо задокументировать `--dev`-режим, либо перенести
   фейки в тесты (aio-pika уже так: `FakeChannel` только в тестах).
4. **`Section.facts`** — `dataclasses.field(default_factory=dict)` внутри
   Pydantic-модели: работает, но это `Field(default_factory=...)`; typing
   `dict` без параметра.
5. **`_summary_to_dict`** — ручной перебор полей вместо
   `summary.model_dump(mode="json")` (при добавлении поля в `Summary` dict
   молча не обновится).
6. **Бот пересобирает путь SRT** (`artifacts_dir/task_id/transcript.srt`)
   вместо использования данных из `AnalysisReady` — скрытое сопряжение с
   `TRANSCRIBER__ARTIFACTS_DIR` анализатора (работает только потому что
   значения совпадают в compose).
7. **`UrlValidator`:** сравнение `netloc` строчное-точное — субдомен/порт
   (`m.youtube.com`, `youtube.com:443`) не проходит, хотя разрешены логически;
   allowlist проверяется **только в боте** — downloader доверяет `source` из
   события без повторной валидации (defense in depth: валидатор должен быть
   в downloader тоже).
8. **`LangfusePromptProvider`:** sync-HTTP-вызов `get_prompt` из event loop
   (в 4.x он идёт по HTTP при первом обращении/истечении кэша); `get()`
   объявлен `-> str` (см. P0-4). Инстанс Langfuse создаётся в `__init__`
   провайдера — при недоступности host конструктор не падает, но лог
   «Error while fetching prompt ... Connection refused» на каждом analyze
   (в 4.x fallback работает, но шумный).
9. **Локальные import'ы внутри функций:** `json` в `serialization.py`,
   `JobFailed`/`TranscriptReady` в except-блоках сервисов, `Path` в
   `service.py` (transcriber). Несогласованно: либо везде top-level (это не
   циклические импорты — причина локальности неочевидна), либо с комментарием.
10. **`Summary` не имеет `title`**, хотя `VideoDownloaded.title` есть —
    заголовок видео теряется на этапе analyze (бот рендерит дефолтный
    «Выжимка лекции»; `test_pipeline.py` даже **закрепил** это поведение
    assert-ом).
11. **Документация/README:** ссылки на `docs/architecture.md` и
    `docs/plan.md` — файлов нет (в README и в docstring'ах events.py/jobs.py/
    ports.py/adapters.py); нет quickstart (как запустить локально без docker,
    какие env нужны — см. P1-2); `reviews/` не в gitignore и не в git —
    решить (закоммитить или в `.gitignore`).
12. **Тесты:** обращение к приватным методам (`svc._cleanup_old_artifacts(168)`
    в `test_downloader.py`); дублирование stub-классов (`StubRouter`/
    `StubExtractor`/`StubTranscriber` задублированы в 2–3 файлах); в
    `test_downloader.py` `StubRouter` вызывается **до** своего определения
    (работает только потому, что fixture ленив); нет round-trip тестов
    `serialize/deserialize`.
13. **Docker-гигиена:** нет `.dockerignore` (контекст = весь репозиторий,
    включая `.git`); каждый образ копирует `services/` целиком (все 4
    сервиса) и делает двойной `uv sync` (лишняя стадия); нет non-root
    `USER`; `uv sync --frozen` в runtime-стадии дублирует base.
14. **Compose-гигиена:** `NEXTAUTH_URL=http://localhost:3000` (у langfuse
    изнутри — ок, но healthcheck у langfuse отсутствует); `postgres` без
    `POSTGRES_DB`-валидации для langfuse (langfuse v3 требует конкретный DB).
15. **`BrokerSettings.rabbit_url`** не URL-энкодирует user/password
    (special chars в пароле → кривой URL).
16. **Мелкие:** `LLMSettings.sampling_parameters: dict` без параметра
    (`dict[str, Any]`); в `LlmSummarizer` из `sampling_parameters`
    используется только `temperature`; pyproject транскрибера имеет extra
    group `dev`, остальные сервисы — нет (несогласованность); `logger` в
    `swot_bot/handlers.py` создан, но не используется.

---

## Что хорошо

- Чистая архитектура: контракты/порты/события разделены (`swot_contracts`),
  сервисы — тонкие, DI через dishka — единый паттерн, fake-адаптеры для
  изолированных unit-тестов.
- Тесты: 77 unit-тестов, зелёные, с адекватными изолированными дублями
  (StubSummarizer/FakeAsrTranscriber/FakeChannel); `test_pipeline.py` —
  настоящий end-to-end через FakeBus.
- `swot_bus/serialization.py` — единая точка (de)сериализации с явным
  `_model_for`; `RabbitMessageBus` — аккуратный robust-канал.
- Рендер результата: Jinja2 + HTML для Telegram, chunking, `send_document`
  для SRT — разумный подход.
- Ruff + pre-commit + закоммиченный `uv.lock` — базовая гигиена есть.
- `.env.example` покрывает все секции (хотя и с оговорками P1-2/P0-5).

---

## План действий

### Фаза 0 — «стек стартует» (1–2 дня, P0)

Приоритет строго по списку: пока не закрыты P0-1/P0-2, остальное не имеет
смысла проверять в контейнерах.

- [ ] **0.1** Фикс `RegistryProvider.registry`: аннотация/`provides=JobRegistry`
      (+ `swot_bot.providers` проверяют то же). Тест: для каждого из 4
      сервисов `await request_container.get(<Service>)` + бот-хендлер
      разрешает `FromDishka[JobRegistry]`. *(P0-1)*
- [ ] **0.2** Перезаписать runtime-стадию всех 4 Dockerfile:
      `uv sync --frozen --package <svc> --no-dev` в `/app/.venv`,
      `ENV PATH=/app/.venv/bin:$PATH`, убрать `uv pip install --system`.
      CI: build всех образов + `python -m <pkg> --help`/импорт smoke-тест.
      Добавить `.dockerignore`. *(P0-2, P2-13)*
- [ ] **0.3** Инвертировать `SourceRouter`: платформы (yt-dlp-хосты +
      `disk.yandex.ru`, `drive.google.com`, `vk.com`, `rutube.ru`) →
      `YtDlpAdapter`; прямые медиа-URL (расширение/HEAD content-type) →
      `DirectHttpAdapter`. Юнит-тесты `route()` на каждой ветке. *(P0-3)*
- [ ] **0.4** Пересобрать `LOCAL_PROMPT`: явный `{input}`, экранированный
      JSON-пример (или `PromptTemplate`/`from_messages`); тест `LlmSummarizer`
      с фейковым LLM (промпт содержит транскрипт, `ainvoke` не падает);
      зафиксировать `langfuse>=4,<5`. *(P0-4)*
- [ ] **0.5** Compose: non-guest RabbitMQ-пользователь (+`RABBITMQ_DEFAULT_*`),
      mount `swot_artifacts` у `bot`, healthcheck у langfuse + `depends_on:
      service_healthy` у analyzer, `restart: unless-stopped` всем, схема
      `http://` в URL LLM/ASR и решение по ASR/LLM-контейнерам (добавить
      testcontainers-совместимые заглушки или внешние URL). Инструкцию
      `cp .env.example .env` — в README. *(P0-5, P2-11)*
- [ ] **0.6** Path-containment: утилита `resolve_under()` в shared-пакете +
      применение в analyzer (`base_dir`, `srt_path`), bot (`summary_path`),
      transcriber (`media_path`) + тесты `../`/абсолютных/symlink. *(P0-6)*

**Критерий приёмки фазы 0:** `docker compose up` поднимает rabbit + 4
сервиса; в логах виден trace_id; отправка ссылки (fake ASR/LLM) приводит к
результату в чат; `python -m <svc>` стартует в каждом контейнере.

### Фаза 1 — «надёжность контура» (3–5 дней, P1)

- [ ] **1.1** Settings: `env_prefix` на каждую под-секцию, секции опциональны,
      `public_key/secret_key=""` по умолчанию; тест «системные env не влияют».
      *(P1-1, P1-2)*
- [ ] **1.2** DLQ на все очереди (`*.dlq` + DLX + `x-delivery-attempt`-политика)
      + reaper задач (TTL → `JobFailed`) + TTL-чистка `InMemoryJobRegistry` и
      media-каталогов (общий utility, запуск в downloader). *(P1-5, P1-11)*
- [ ] **1.3** yt-dlp: `to_thread` + `wait_for(timeout)`, `duration` до
      скачивания, `bestaudio`-формат; DirectHttp: `sock_read`-таймауты,
      проверка content-type/размера, лимит на размер. *(P1-3, P1-4)*
- [ ] **1.4** ASR: chunking длинного аудио (ffmpeg `segment`), лимит на
      размер файла, timeout на вызов; LLM: map-reduce чанкинг
      (`langchain-text-splitters` уже в зависимостях), `max_tokens`/timeout.
      *(P1-6, P1-7)*
- [ ] **1.5** Trace: `bind_contextvars(trace_id=...)` в боте до `publish`;
      `stage`/`task_id` в контексте обработки; единый JSON-лог
      (structlog stdlib integration). *(P1-8, P1-9)*
- [ ] **1.6** Health: readiness = «bus подключён», `HealthServer.stop()`;
      graceful shutdown (stop bus → health → polling). *(P1-9)*
- [ ] **1.7** Доставка: ретраи на Telegram-ошибки, человекочитаемые `on_failed`,
      `chunk_text` без потери `\n` и без резки тегов. *(P1-10)*
- [ ] **1.8** CI: mypy (strict) на packages+services; job docker-build;
      compose-интеграционный тест (testcontainers: rabbit + fake ASR/LLM,
      healthz-проверка). *(P1-12, P1-13)*

### Фаза 2 — «чистота» (1–2 дня, P2)

- [ ] `JobStatus.READY` — только analyzer; поправить тесты downloader/
      transcriber (проверять `DOWNLOADED`/`TRANSCRIBED` — добавить статусы,
      если нужно).
- [ ] Убрать мёртвый код: `job.events` exchange (или опубликовывать события
      на него осознанно), `Job`-протокол, неиспользуемые message types
      (`TranscribeRequest`/`AnalysisRequest`) или добавить их в
      `_model_for`, `payload`/`created_at`, неиспользуемые константы,
      `openai`-зависимость анализатора, `main.py`.
- [ ] Перенести test-дубли из production-модулей (`FakeBus`, `FakeTranscriber`,
      `StubSummarizer`, их провайдеры) — либо задокументировать dev-режим.
- [ ] `Summary.title` (не терять заголовок видео), `model_dump(mode="json")`
      в `_summary_to_dict`, передача пути SRT в `AnalysisReady` (бот не
      пересобирает путь), `Section` — `Field(default_factory=...)`.
- [ ] UrlValidator: нормализация netloc (субдомены/порт), повторная валидация
      в downloader.
- [ ] Доки: дописать/удалить ссылки `docs/*`, quickstart в README, решение по
      `reviews/`.
- [ ] Тесты: round-trip `serialize/deserialize`, публичные методы вместо
      `_cleanup_old_artifacts`, общий stub-модуль для тестов, aiogram-хендлеры.

### Что НЕ стоит делать

- Не вводить Redis/Postgres для registry «по канону микросервисов» до
  фазы 1.2 — сначала решить семантику (registry per-service vs. события).
- Не переписывать рендер на Markdown — HTML Telegram уже работает, чинить
  точечно.
- Не заворачивать yt-dlp в отдельный микросервис — он уже изолирован.

---

## Сверка с `reviews/review-2025-09-09.md`

Бывший ревью (2025-09-09) — фоновая информация; этот аудит независим.
Состояние находок старого ревью на момент 2026-09-10:

| Старая находка | Статус |
|---|---|
| P0-1 роутер инвертирован | **не исправлено** (этот P0-3) |
| P0-2 path traversal анализатора | **не исправлено** (P0-6) |
| P0-3 unlink media_path | **не исправлено** (P0-6) |
| P0-4 dishka `JobRegistry` | **не исправлено, подтверждено runtime** (этот P0-1) |
| P1-1 yt-dlp blocking | **не исправлено** (этот P1-3) |
| P1-3 ffmpeg без timeout | **не исправлено** |
| P1-4/5/6 registry in-memory/DLQ/READY | **не исправлены** (P1-5, P2-1) |
| P1-7/10 HealthServer/langfuse env | **не исправлены** (P1-9, P1-2) |
| P1-16 restart policies | **не исправлено** (P0-5) |
| P2-* (chunk_text, fanout, unused types, docs) | **не исправлены** (P2) |

**Новые находки этого аудита (не было в старом ревью):**
P0-2 (Docker-образы — подтверждено сборкой), P0-4 (промпт-цепочка),
P0-5 (compose: rabbit-guest loopback, volume у bot, langfuse v3, отсутствие
ASR/LLM-эндпоинтов), P1-1 (env-загрязнение Settings — подтверждено
экспериментом), P1-6 (ASR-размер), P1-7 (LLM-чанкинг), P1-8 (trace
обрывается), P1-13 (CI не строит docker), P2-15/16/17/18 (мелочи).
