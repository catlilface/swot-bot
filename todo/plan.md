# swot-bot — TODO / план работ

> Источник: `audit.md` (2026-09-10). Идентификаторы P0-x / P1-x / P2-x — ссылки на
> соответствующие разделы audit.md.
> Статус: `[ ]` не начато · `[~]` в работе · `[x]` готово (проверено по чек-листу приёмки).
> Правило: задача считается сделанной только когда **все** пункты её чек-листа выполнены.

## Обзор

| Фаза | Цель | Задачи | Оценка |
|------|------|--------|--------|
| 0 | Стек стартует (P0) | T-0.1 … T-0.6 | 1–2 дня |
| 1 | Надёжность контура (P1) | T-1.1 … T-1.8 | 3–5 дней |
| 2 | Чистота кода и процесса (P2) | T-2.1 … T-2.9 | 1–2 дня |

Порядок: **T-0.1 и T-0.2** первыми (без них остальное невозможно проверить в контейнере,
они независимы друг от друга и могут делаться параллельно) → T-0.3 … T-0.6 →
фаза 1 (задачи внутри фазы независимы, кроме отмеченных зависимостей) → фаза 2.

---

## Фаза 0 — «стек стартует»

### T-0.1 Разрешение `JobRegistry` в dishka-контейнере
- Находки: P0-1. Зависимости: нет.
- Scope: `packages/bus/src/swot_bus/providers.py`, новые тесты.
- Работа: провайдер `registry` регистрировать по протоколу
  (`@provide(..., provides=JobRegistry)` или аннотация `-> JobRegistry`),
  проверить, что ни один сервис не требует конкретный класс.
- Приёмка:
  - [x] `make_async_container(RegistryProvider())` → `container.get(JobRegistry)`
        возвращает `InMemoryJobRegistry` (ранее — `NoFactoryError`).
  - [x] Новый тест-файл `tests/unit/test_dishka_resolution.py`: для **каждого** из
        downloader/transcriber/analyzer — `await request_container.get(<Service>)`
        без ошибок; для бота — `RequestContainer` разрешает `JobRegistry`
        (как `FromDishka` в `handle_link`).
  - [x] `uv run pytest tests/ -q` — зелёные (новые тесты + старые).
  - [x] `uv run ruff check . && uv run ruff format --check .` — чистые.

### T-0.2 Docker-образы: зависимости в runtime
- Находки: P0-2, P2-13. Зависимости: нет.
- Scope: 4 файла `.docker/*/Dockerfile`, `.dockerignore` (новый), `.github/workflows/ci.yml`.
- Работа: runtime-стадия — `uv sync --frozen --package <svc> --no-dev` (в `/app/.venv`),
  убрать `uv pip install --system … --no-deps`, дублирующий `uv sync` и копирование
  всех `services/`; `ENV PATH="/app/.venv/bin:$PATH"`; добавить `.dockerignore`
  (`.git`, `.venv`, `.env`, `*.md` по вкусу); non-root `USER` (T-2.8, сюда же).
- Приёмка:
  - [x] `docker build -f .docker/bot/Dockerfile -t swot-bot-bot .` →
        `docker run --rm swot-bot-bot python -m swot_bot` стартует (падает только на
        отсутствии `TELEGRAM__TOKEN`/подключении к брокеру — **не** на
        `ModuleNotFoundError`).
  - [x] То же для downloader/transcriber/analyzer: `python -m swot_<svc>` стартует.
  - [x] `docker run --rm swot-bot-bot python -c "import aiogram, aio_pika, structlog"` —
        без ошибок.
  - [x] CI-job `docker-build`: собирается каждый образ + smoke-импорт; job падает на
        `ModuleNotFoundError`.

### T-0.3 Роутинг источников: инверсия `SourceRouter`
- Находки: P0-3. Зависимости: нет.
- Scope: `services/downloader/src/swot_downloader/adapters.py`, тесты.
- Работа: (1) хосты платформ (включая `disk.yandex.ru`, `drive.google.com`, `vk.com`,
  `rutube.ru`) → `YtDlpAdapter`; (2) прямые медиа-URL (расширение
  `.mp4/.m4a/.mp3/.wav/.ogg/.flac` **или** HEAD с `Content-Type: video/*|audio/*`) →
  `DirectHttpAdapter`; (3) остальное → `YtDlpAdapter` (yt-dlp как дефолт) либо явная
  ошибка. `route()` возвращает источник, как заявлено в docstring.
- Приёмка:
  - [x] Unit-тесты `route()`: youtube.com / vk.com / rutube.ru / drive.google.com /
        disk.yandex.ru / vimeo.com → `YtDlpAdapter`; `example.com/video.mp4` →
        `DirectHttpAdapter`; `example.com/watch?v=...` → `YtDlpAdapter`.
  - [x] Интеграционный (или unit с фиксами): `YtDlpAdapter` на коротком видео-URL
        (можно через тестовый HTTP-сервер, отдающий .m4a) → файл с медиа, не HTML.
  - [x] `uv run pytest tests/ -q` — зелёные.

### T-0.4 Промпт-цепочка анализатора
- Находки: P0-4. Зависимости: нет.
- Scope: `services/analyzer/src/swot_analyzer/prompts.py`, `summarizer.py`,
  `pyproject.toml` (версия langfuse), тесты.
- Работа: `LOCAL_PROMPT` — шаблон с явной переменной `{input}` и экранированным
  JSON-примером (или `PromptTemplate`/`from_messages`); `PromptProvider.get`
  гарантированно возвращает `str`; зафиксировать `langfuse>=4,<5` (установлен 4.15.1,
  код писался под 2.x — проверить `get_prompt(fallback=)`).
- Приёмка:
  - [x] `ChatPromptTemplate.from_template(LOCAL_PROMPT)` — без исключений.
  - [x] Новый тест: `LlmSummarizer.summarize` с фейковым LLM (ловит `ainvoke`-вход):
        транскрипт присутствует в промпте, JSON-пример не сломан, парсинг ответа
        работает (модель фиксированного JSON).
  - [x] Тест fallback-пути: `LangfusePromptProvider` с недоступным host → возвращает
        `LOCAL_PROMPT` как `str` (не `ChatPromptTemplate`).
  - [x] `uv run pytest tests/ -q` — зелёные.

### T-0.5 docker-compose: рабочий стек
- Находки: P0-5, P2-11. Зависимости: T-0.2 (для приёмки).
- Scope: `docker-compose.yml`, `.env.example`, `README.md`.
- Работа: (1) RabbitMQ — `RABBITMQ_DEFAULT_USER/PASSWORD` (не guest) + соответствующие
  `BROKER__USER/PASSWORD`; (2) `bot` — volume `swot_artifacts:/data/artifacts`;
  (3) `langfuse` — **убран из dev-стека** (явная пометка: анализатор работает по
  fallback `LocalPromptProvider` из T-0.4; ключи `LANGFUSE__*` могут быть пустыми);
  (4) ASR/LLM — контейнеры-заглушки `asr-service`/`llm-service` (fake OpenAI API);
  (5) `restart: unless-stopped` всем; (6) README: quickstart
  (`cp .env.example .env`, `docker compose up`, что и где).
  - Реализовано в T-0.5 дополнительно: fake Telegram-заглушка `fake-tg` (dev-токен +
    `TELEGRAM__API_BASE_URL`, инъекция `/inject`); `ffmpeg` в образе transcriber;
    `swot_media`-том примонтирован и downloader'у и transcriber'у; success-логи с
    `task_id`/`trace_id` во всех 4 сервисах.
- Приёмка:
  - [x] `docker compose config -q` — без ошибок.
  - [x] `docker compose up -d` → все контейнеры `running`, healthchecks `healthy`
        (rabbit, 4 сервиса, fake-asr, fake-llm, fake-tg) в пределах ~2 минут.
  - [x] `docker compose exec bot ls /data/artifacts` — том примонтирован (видны файлы,
        созданные пайплайном).
  - [x] Сквозной прогон (с fake ASR/LLM/Telegram): админ кидает ссылку → в чат
        приходит выжимка + SRT-файл; в логах всех 4 сервисов — один и тот же
        `trace_id` (проверено: `t-4bdf4ab59c9e` в bot/downloader/transcriber/analyzer).
  - [x] `docker compose down && docker compose up -d` — стек восстанавливается
        (restart-политики, 8/8 healthy за ~30 c).

### T-0.6 Path containment для путей из сообщений
- Находки: P0-6. Зависимости: нет.
- Scope: новый helper (в `swot_contracts` или `swot_bus`), `swot_analyzer/service.py`,
  `swot_bot/handlers.py`, `swot_transcriber/service.py`, тесты.
- Работа: `resolve_under(base, candidate)`: запрет абсолютных путей, `..`,
  symlink-эскейпов; возврат `resolved` только внутри `base.resolve()`. Применить:
  analyzer (`base_dir`, `srt_path`), bot (`summary_path`), transcriber (`media_path`).
  До готовности — минимум: суффиксы `*.json/*.srt/*.txt` + reject абсолютных/`..`.
- Приёмка:
  - [x] Тесты helper: `../etc/passwd`, `/etc/passwd`, symlink-ссылка наружу →
        `ValueError` (или `JobFailed` на уровне сервиса); `base/sub/file.txt` — ок.
        (helper в `swot_contracts/paths.py` + тесты в `tests/unit/test_path_containment.py`)
  - [x] Analyzer: событие с `base_dir="../../"` → `summary.json` **не** создается вне
        `artifacts_dir`, задача → FAILED с понятной ошибкой.
  - [x] Bot: событие с `summary_path="/etc/passwd"` → сообщение не падает, логирует
        ошибку, файл не читается.
  - [x] Transcriber: `media_path` вне `media_dir` → файл не удаляется.
  - [x] `uv run pytest tests/ -q` — зелёные. (64 unit-тестов, `ruff check`/`format` — чисто;
        E2E: сквозной прогон через compose с containment-кодом — trace t-dd27cdeecb50)

**Критерий приёмки фазы 0 (все вместе):**
- [x] `docker compose up -d` — стек поднимается, healthchecks зелёные.
- [x] Сквозной прогон от ссылки до результата в чате работает (см. T-0.5).
- [x] `uv run pytest tests/ -q`, `ruff check`, `ruff format --check` — зелёные.

---

## Фаза 1 — «надёжность контура»

### T-1.1 Settings: префиксы и опциональные секции
- Находки: P1-1, P1-2. Зависимости: нет.
- Scope: `packages/contracts/src/swot_contracts/config/*.py`, `.env.example`, тесты.
- Работа: `env_prefix` для каждой под-секции (`BROKER__`, `LLM__`, …); секции —
  опциональны (default-экземпляры); `LangfuseSettings.public_key/secret_key = ""`;
  поправить docstring'ы; `.env.example` привести к новым префиксам.
- Приёмка:
  - [x] `Settings()` в **пустом** env — конструируется без ошибок (все секции по
        умолчанию).
        (секции — `Field(default_factory=...)`; тест `tests/unit/test_settings.py::test_settings_empty_env_uses_defaults`)
  - [x] Тест-регрессия: `USER=macuser PORT=9999 LANGUAGE=en_US:en MODEL=x` в env →
        `broker.user == "guest"`, `broker.port == 5672`, `transcriber.language == ""`,
        `transcriber.model == "whisper-1"`.
        (`test_noisy_env_does_not_leak_into_sections`; у каждой под-секции `env_prefix`
        — `BROKER__`, `LLM__`, `TRANSCRIBER__` и т.д.)
  - [x] Анализатор/бот стартуют без `LANGFUSE__PUBLIC_KEY/SECRET_KEY`.
        (ключи дефолт `""`; compose-прогон: 8/8 healthy, E2E trace t-68c52c26c0ce)
  - [x] `uv run pytest tests/ -q` — зелёные (старые тесты на Settings обновлены).
        (69 unit-тестов, `ruff check`/`format` — чисто; docstring'ы обновлены,
        `.env.example` уже на префиксах)

### T-1.2 DLQ, reaper задач, TTL-очистка
- Находки: P1-5, P1-11. Зависимости: T-0.5 (очереди в compose).
- Scope: `packages/bus/src/swot_bus/rabbit.py`, `swot_contracts/events.py` (при
  необходимости), `swot_downloader/service.py` (TTL-чистка), новые тесты.
- Работа: (1) у всех очередей — `x-dead-letter-exchange`/`-routing-key` + DLQ-очереди
  `<queue>.dlq`; политика попыток (requeue ≤ N, потом DLQ); (2) reaper: периодический
  job (в downloader или отдельный воркер) — задачи старше TTL без финального статуса →
  `JobFailed("timeout")`; (3) TTL-чистка `InMemoryJobRegistry` (TTL после FINAL) и
  каталогов `media_dir`/`artifacts_dir` (один utility, не только artifacts).
- Приёмка:
  - [x] Тест: сообщение с payload-ом, ломающим десериализацию → после N попыток
        попадает в `dlq`-очередь (не теряется: в очереди DLQ есть сообщение).
        (`tests/unit/test_dlq.py` — живая проверка по RabbitMQ из compose;
        миграция legacy-очередей без DLQ-аргументов подтверждена в логах стэка)
  - [x] Тест reaper: задача, «застрявшая» в DOWNLOADING дольше TTL → публикуется
        `JobFailed` со стадией и причиной.
        (`tests/unit/test_reaper.py` — 6 тестов: stage="download", "timeout" в error)
  - [x] Тест чистки: старые каталоги в `media_dir` **и** `artifacts_dir` старше TTL
        удаляются; свежие — нет.
        (`test_downloader.py::test_dirs_ttl_cleanup_media_and_artifacts`; TTL finals —
        `test_reaper.py`)
  - [x] `uv run pytest tests/ -q` — зелёные.
        (76 тестов; `ruff check`/`format --check` — чисто; compose-стек 8/8 healthy)

### T-1.3 Скорость/блокировки скачивания
- Находки: P1-3, P1-4, P2-18. Зависимости: T-0.3.
- Scope: `services/downloader/src/swot_downloader/adapters.py`, `service.py`.
- Работа: `YtDlpAdapter` — `asyncio.to_thread` + `wait_for(timeout=timeout_sec)`,
  `duration` проверять по info-экстракции **до** загрузки, формат
  `bestaudio[acodec=none]/bestaudio`; `DirectHttpAdapter` — таймауты `sock_connect`/
  `sock_read` (не только `total`), проверка `Content-Type`/расширения, лимит размера
  файла.
- Приёмка:
  - [x] Тест: скачивание медленного (> timeout_sec) ресурса → `DownloadError`/
        `JobFailed`, а не зависание; event loop не блокируется (параллельный тик
        healthz во время «скачивания» — проверка через fake-адаптер со сном в
        `to_thread`).
        (`test_download_adapters.py::test_slow_download_times_out_and_loop_stays_free` —
        `wait_for` поверх `to_thread`, тикер ≥3 за 0.1 с, JobFailed(stage="download"),
        registry=FAILED)
  - [x] Тест: `duration` в info > `max_duration_sec` → ошибка **до** скачивания.
        (`test_download_adapters.py::test_duration_rejected_before_download` — фаза
        metadata вызвана, фаза download — нет)
  - [x] DirectHttp: ответ `text/html` на URL `*.mp4` → ошибка, файл не сохраняется.
        (`test_download_adapters.py::test_direct_http_text_html_rejected_no_file` —
        live aiohttp-сервер, каталог после ошибки пуст)
  - [x] Файл > `MAX_FILE_MB` → прерывается, частичный файл удаляется.
        (`test_download_adapters.py::test_direct_http_oversize_aborted_partial_removed` —
        1.5 МБ при лимите 1 МБ, частичный файл удалён)
  - [x] `uv run pytest tests/ -q` — зелёные.
        (82 теста; `ruff check`/`format --check` — чисто)

### T-1.4 ASR-чанкинг и LLM-чанкинг
- Находки: P1-6, P1-7. Зависимости: нет.
- Scope: `services/transcriber/src/swot_transcriber/*`, `services/analyzer/src/swot_analyzer/*`.
- Работа: (1) ASR: разбивка длинного аудио на сегменты 10–15 мин (ffmpeg `segment`),
  постраничное отправка в ASR со сдвигом таймкодов при сборке SRT, лимит на размер
  аудио, `timeout` на вызов; (2) LLM: map-reduce по транскрипту
  (`langchain-text-splitters` уже в зависимостях) + `max_tokens`/`timeout`, лимит на
  размер транскрипта с понятной ошибкой.
- Приёмка:
  - [x] Тест: «аудио» 3 часа (можно эмуляцией списка сегментов) → N ASR-вызовов,
    SRT-таймкоды сдвинуты корректно (первый сегмент = 00:00:00, второй =
    00:10:00:00, …).
        (`test_chunking.py::test_long_audio_splitted_per_segment_with_shifted_srt` —
        18 сегментов по 10 мин → 18 ASR-вызовов, SRT: `00:00:00,000` / `00:10:00,000`
        / `00:20:00,000` / `03:00:00,000`; сегменты.json — сдвинутые таймкоды)
  - [x] Тест: аудио > лимита → `JobFailed` с явной причиной до загрузки в память.
        (`test_chunking.py::test_oversized_audio_publishes_job_failed` —
        `TranscribeService` публикует `job.failed` с причиной "audio too large…
        limit…", сегментация и ASR-клиент не вызывались; проверка размера идёт
        до `open()`/загрузки)
  - [x] Тест: транскрипт > контекста → map-reduce: LLM вызывается несколько раз,
    итоговая `Summary` валидна (Pydantic), факты не дублируются.
        (`test_chunking.py::test_map_reduce_multiple_chunks_no_duplicate_facts` —
        2 map + 1 reduce, дубль `ФАКТ: A` устранён; `::test_long_transcript_splits_into_multiple_map_calls`
        — реальный `RecursiveCharacterTextSplitter`: N чанков + reduce, 3000 фактов
        без потерь; лимит `max_transcript_chars` — `::test_transcript_over_limit_rejected_without_llm_call`;
        `max_tokens`/`request_timeout` на `ChatOpenAI` — `::test_chatopenai_gets_max_tokens_and_timeout`)
  - [x] `uv run pytest tests/ -q` — зелёные.
        (90 тестов; `ruff check`/`format --check` — чисто)

### T-1.5 Сквозной trace_id + единый JSON-лог
- Находки: P1-8, P1-9. Зависимости: нет.
- Scope: `services/bot/src/swot_bot/handlers.py`, `swot_observability/logging_config.py`.
- Работа: бот — `bind_contextvars(trace_id=...)` до первого `publish` (и
  `task_id/stage` при обработке); structlog stdlib integration, чтобы aio_pika/
  aiohttp/aiogram/openai логились в тот же JSON.
- Приёмка:
  - [x] Тест: `bus.publish(...)` после `bind_contextvars(trace_id=X)` → в header
        сообщения `swot-trace-id: X`; consumer биндит его в контекст (round-trip).
        (`tests/unit/test_trace.py::test_roundtrip_header_and_context` — stub aio_pika;
        `::test_roundtrip_live_broker` — живой RabbitMQ из compose; оба зелёные)
  - [x] В логах бота, downloader, transcriber, analyzer одного прогона — одинаковый
        `trace_id` и `task_id` (ручная проверка по compose-логам + тест).
        (E2E `t-1310d76e8433` / task `1310d76e-…`: bot «link received»+«result delivered»,
        downloader «video downloaded», transcriber «transcript ready», analyzer «summary ready» —
        единый trace/task во всех 4; stdlib-логгеры `httpx`/`aiohttp` тоже несут контекст)
  - [x] `docker compose logs` — каждый JSON-лог (включая `aio_pika`, `openai`)
        парсится `json.loads`.
        (проверено скриптом: bot/downloader/transcriber/analyzer — 143 строки, 0 non-JSON;
        stdlib-логгеры идут через тот же `ProcessorFormatter`; fake-заглушки и
        rabbitmq — plain-text по design, не сервисы пайплайна)

### T-1.6 Health: readiness и graceful shutdown
- Находки: P1-9. Зависимости: нет.
- Scope: `swot_observability/health.py`, `swot_bus/rabbit.py`, 4 × `__main__.py`.
- Работа: `HealthServer.stop()`; readiness = «bus подключён» (колбэк из
  `RabbitMessageBus.connect()`); порядок shutdown: SIGTERM → drain consume → close bus
  → stop health → exit 0.
- Приёмка:
  - [x] Тест: до `connect()` `/readyz` → `{"status":"degraded"}`; после — `ok`.
        (`tests/unit/test_health.py::test_readyz_reflects_readiness_and_healthz_stop` —
        до готовности 503/degraded, после 200/ok; `test_rabbit_bus_is_ready_live` —
        `is_ready()` False до connect(), True после, False после close().)
  - [x] Тест: `SIGTERM` в процессе consume → процесс завершается кодом 0 за
        < grace period, очередь ack'нуты/рекею-нуты корректно (тест на FakeBus/
        RabbitMessageBus с локальным брокером в CI).
        (`test_inflight_drained_on_close_live` — close() дожидается in-flight,
        очередь пуста (ack, не потеря); `test_worker_sigterm_exits_zero_live` —
        SIGTERM `python -m swot_downloader` → exit 0 за 30s; `test_bot_sigterm_exits_zero_live` —
        то же для бота с fake-tg; live-тесты skip без брокера.)
  - [x] Compose: `docker compose stop bot` → контейнер уходит без `Killed`.
        (проверено: `docker compose stop bot downloader` → оба `State.ExitCode=0`,
        логи: `shutdown: N work task(s) stopped` → `health server stopped`, без Killed.)

### T-1.7 Доставка результата в Telegram
- Находки: P1-10. Зависимости: T-0.6.
- Scope: `services/bot/src/swot_bot/handlers.py`, `renderer.py`.
- Работа: ретраи с backoff на Telegram-ошибки (network/rate-limit) перед nack;
  `on_failed` — человекочитаемое сообщение + technical detail только в лог;
  `chunk_text` — без потери `\n` на границе чанков, без разрыва HTML-тегов/сущностей.
- Приёмка:
  - [x] Тест: `message.answer` падает 2 раза (rate limit) и succeeds → сообщение
        доставлено, nack не идёт.
  - [x] Тест: `summary_path` не существует → админ получает нормализованное
        «не удалось доставить результат», traceback не уходит в чат.
  - [x] Тест `chunk_text`: на тексте с переносами и HTML-тегами
        `"".join(chunks) == original` и ни один чанк не содержит половинки тега
        (`<b>` … `</b>` не раздельны).
  - [x] `uv run pytest tests/ -q` — зелёные.

### T-1.8 CI: mypy + docker + compose-интеграция
- Находки: P1-12, P1-13. Зависимости: T-0.2 (для docker job).
- Scope: `.github/workflows/ci.yml`, `pyproject.toml` (mypy-конфиг).
- Работа: (1) `mypy` (strict или close-to-strict) на `packages/*` + `services/*`;
  (2) job `docker-build` (T-0.2); (3) job `compose`: testcontainers (RabbitMQ + fake
  ASR/LLM) → поднять 4 сервиса, проверка healthz + сквозного пайплайна.
- Приёмка:
  - [x] CI: мойпы-ошибка блокирует merge (`mypy packages services --strict` или
        согласованный набор). (отдельный CI-джоб `mypy`: `uv run mypy packages services`,
        close-to-strict набор в `pyproject.toml` — полный strict несовместим со
        stub'ами aiogram/aio_pika/langchain_openai)
  - [x] CI: docker-build job зелёный на main. (джоб `docker-build`: сборка всех 4
        образов + smoke-импорт, падает на `ModuleNotFoundError`)
  - [x] CI: compose-integration job зелёный (healthz 4 сервисов + E2E-прогон).
        (джоб `compose-integration`: `docker compose up -d --build` → 8/8 healthy,
        healthz+readyz всех 4 сервисов, E2E /inject → summary + SRT, единый
        `trace_id` в логах 4 сервисов; локально воспроизведено 2026-09-10,
        trace t-f30dcb87b3d0)
  - [x] Локально воспроизводимо: `uv run mypy packages services` — без ошибок.
        (53 source files, no issues)

**Критерий приёмки фазы 1:**
- [x] Потеря сообщения в любом узле пайплайна обнаруживается (DLQ) и не «зависает»
      статус задачи (reaper).
- [x] Лекция 2+ часа проходит до результата без OOM/таймаутов (чанкинг ASR/LLM).
- [x] В логах любого узла виден сквозной `trace_id`; `/readyz` отражает реальное
      состояние брокера; `SIGTERM` — graceful.
- [x] CI ловляет: типы, битые Docker-образы, битый compose-стек.

---

## Фаза 2 — «чистота»

### T-2.1 Семантика статусов задач
- Находки: P2-1. Зависимости: T-1.2 (reaper читает статусы).
- Scope: `swot_contracts/events.py` (`JobStatus`), сервисы, тесты.
- Работа: `READY` — только анализатор; (опционально) отдельные статусы после
  download/transcribe, либо `JobProgress` как источник истины.
- Приёмка:
  - [x] Тесты downloader/transcriber утверждают промежуточные статусы (не READY),
    тест analyzer — READY.
  - [x] Reaper использует финальные статусы (`READY`/`FAILED`) — корректно.
  - [x] `uv run pytest tests/ -q` — зелёные.

### T-2.2 Мёртвый код
- Находки: P2-2. Зависимости: нет.
- Scope: `swot_bus/rabbit.py` (`job.events` exchange), `swot_contracts/ports.py`
  (`Job`), `swot_contracts/events.py` (`TranscribeRequest`/`AnalysisRequest`/
  `payload`/`created_at`), `swot_downloader/adapters.py` (константы расширений),
  `services/analyzer/pyproject.toml` (`openai`), корневой `pyproject.toml`
  (`dishka`), `main.py`.
- Работа: удалить или осознанно использовать: обмен `job.events` (удалить из
  `connect()`, если не публикуется), `Job`, неиспользуемые message types (или
  добавить в `_model_for`), `payload`/`created_at`, неиспользуемые константы,
  `openai`-зависимость анализатора, `main.py`.
- Приёмка:
  - [x] `grep -rn "job.events\|job_events" packages services` — пусто (или
        осознанное использование с тестом).
  - [x] `import swot_contracts.events; TranscribeRequest/AnalysisRequest` — удалены
        **или** поддерживаются `deserialize` (тест round-trip).
  - [x] `uv run pytest tests/ -q`, `ruff check` — зелёные (no unused).

### T-2.3 Тест-дубли вне production-модулей
- Находки: P2-3. Зависимости: нет.
- Scope: `swot_bus/fake.py`, `swot_bus/providers.py`, `swot_analyzer/providers.py`.
- Работа: либо перенести `FakeBus`/`FakeTranscriber`/`StubSummarizer` + их провайдеры
  в тесты, либо задокументировать dev-режим (`SWOT_FAKE_TRANSCRIBER=1`) и его
  семантику.
- Приёмка:
  - [x] Либо: production-модули не содержат `*Fake*`/`Stub*`; тесты используют
        перенесённые двойники — `pytest` зелёные.
  - [x] Либо: README/доки описывают dev-режим; `FakeTranscriberProvider` не
        регистрируется по умолчанию (только при флага).
        (README-секция «Dev-режим (fakes) и их семантика»: dev-фейки — только
        compose-контейнеры asr-service/llm-service/fake-tg (services/fake/);
        in-process-фейки удалены из production-модулей, `FakeTranscriberProvider`
        в коде не существует и ни по умолчанию, ни по флагу не регистрируется)
  - [x] Ревью: ни один production-контейнер не активирует фейки без явного флага.

### T-2.4 Контракты и рендер: точечные исправления
- Находки: P2-4 … P2-10. Зависимости: нет.
- Scope: `swot_analyzer/service.py` (`model_dump`), `swot_contracts/events.py`
  (`Summary.title`), `swot_bot/handlers.py` (SRT из события, не пересборка пути),
  `swot_contracts/events.py` (`Section` — `Field`), `swot_bot/renderer.py` (кэш
  шаблона).
- Приёмка:
  - [x] `Summary` получает `title` из `VideoDownloaded.title`; бот рендерит его
    (тест: заголовок из download появляется в сообщении).
  - [x] `_summary_to_dict` == `summary.model_dump(mode="json")` (тест на равенство).
  - [x] `AnalysisReady` несёт путь SRT; `on_analysis` использует его, не
    пересобирает (тест: изменение `TRANSCRIBER__ARTIFACTS_DIR` не ломает доставку).
  - [x] `Section(facts=...)` — `Field(default_factory=list)`.
  - [x] Рендер кэширует шаблон (тест: повторный `render` не читает файл с диска).

### T-2.5 UrlValidator: нормализация и повторная валидация
- Находки: P2-7. Зависимости: нет.
- Scope: `swot_contracts/urls.py` (общий валидатор, T-2.5: downloader не зависит
  от `swot_bot`), `swot_bot/validation.py` (re-export), `swot_downloader/service.py`,
  `swot_downloader/providers.py`.
- Работа: нормализация netloc (strip порта по дефолту схемы, суффикс-совпадение
  субдоменов); в downloader — повторная проверка `source` через тот же валидатор
  (defense in depth).
- Приёмка:
  - [x] Тесты: `m.youtube.com`, `youtube.com:443` → разрешены; `youtu.be`/
        `malicious.com` → отвергнуты (те же + новые кейсы).
  - [x] Тест: downloader получает событие с `source="https://evil.com/x.mp4"` →
        `JobFailed` (валидация на стороне downloader), даже если бот не проверил.
  - [x] `uv run pytest tests/ -q` — зелёные.

### T-2.6 Документация
- Находки: P2-11, P0-5 (README). Зависимости: T-0.5 (quickstart).
- Scope: `README.md`, `docs/*`, docstring'ы, `reviews/` (gitignore или commit).
- Работа: дописать `docs/architecture.md` (или убрать ссылки из README и
  docstring'ов), quickstart (локально + docker), решить судьбу `reviews/`.
- Приёмка:
  - [x] `grep -rn "docs/architecture.md\|docs/plan.md" README.md packages services` —
        либо файлы существуют, либо ссылок нет.
  - [x] По README с чистого клона: `cp .env.example .env && docker compose up` —
        инструкции совпадают с compose (T-0.5).
  - [x] `git status` — `reviews/` закоммичен или в `.gitignore`.

### T-2.7 Тесты: round-trip, stubs, aiogram-хендлеры
- Находки: P1-12, P2-12. Зависимости: T-1.2 (DLX — тестируется там).
- Scope: `tests/unit/*`.
- Работа: (1) round-trip `serialize/deserialize` для **каждого** message type +
  неизвестный type → ошибка; (2) общий модуль stub'ов (убрать дубли
  `StubRouter`/`StubExtractor`/`StubTranscriber`); (3) тесты aiogram-хендлеров бота
  (ручной `Message`/`aiogram.testing`): `handle_link` (валидация, публикация,
  ответ), `on_analysis/on_failed/on_progress` (с фейком `message.answer`);
  (4) публичный API вместо `svc._cleanup_old_artifacts`.
- Приёмка:
  - [x] Тесты round-trip зелёные; неизвестный `type` → `ValueError`/`JobFailed`.
  - [x] `grep -rn "StubRouter\|StubExtractor\|StubTranscriber" tests` — определение
        встречается один раз (общий модуль).
  - [x] `grep -rn "\._[a-z]" tests/` — обращения к приватным методам отсутствуют.
  - [x] Хендлеры бота покрыты тестами (покрытие `swot_bot/handlers.py` ≥ 80%).

### T-2.8 Docker/compose-гигиена
- Находки: P2-8, P2-13, P2-14. Зависимости: T-0.2, T-0.5.
- Scope: `.docker/*`, `docker-compose.yml`, `swot_contracts/config/broker_settings.py`.
- Работа: non-root `USER` в образах; один `uv sync` (без дубля); healthcheck у
  langfuse; `rabbit_url` — URL-энкодинг user/password.
- Приёмка:
  - [x] `docker run --rm swot-bot-bot id` — не root; `du -sh` образа меньше базового
        (одна стадия sync).
        (uid=10001(appuser); образ 219MB — python:3.13-alpine + один `uv sync`,
        меньше 287MB предшествующего двухстадийного образа и 273MB uv-base)
  - [x] Тест: `BROKER__PASSWORD='p@ss/w ord'` → `rabbit_url` содержит
        `p%40ss%2Fw%20ord`.
  - [x] `docker compose up` — langfuse-healthcheck проходит, analyzer ждёт его.

### T-2.9 Мелочи
- Находки: P2-16 … P2-18. Зависимости: нет.
- Scope: `swot_contracts/config/llm_settings.py`, `swot_analyzer/summarizer.py`,
  `swot_transcriber/pyproject.toml`, `swot_bot/handlers.py`, `swot_bus/serialization.py`,
  сервисы (локальные импорты).
- Работа: `sampling_parameters: dict[str, float | int | str]` (или `dict[str, Any]`)
  + применение всех параметров (не только temperature); единый стиль импортов
  (top-level); удалить неиспользуемый `logger` в `handlers.py`; согласовать
  dev-groups в pyproject'ах.
  (Факт: неиспользуемого `logger` в `handlers.py` в актуальном коде нет —
  находка уже снята рефакторингом; использован вариант `dict[str, Any]`,
  т.к. stubs ChatOpenAI требуют `Mapping[str, Any]`; единый dev-групп =
  корневой `[dependency-groups] dev`, stray `[project.optional-dependencies] dev`
  в transcriber удалён; `serialization.py` — импорты перенесены на top-level.)
- Приёмка:
  - [x] Тест: `sampling_parameters={"temperature": 0.3, "max_tokens": 500}` → оба
        попадают в `ChatOpenAI` (`tests/unit/test_chunking.py::test_sampling_parameters_reach_chatopenai`).
  - [x] `ruff check` без `F401` (unused logger); `uv run pytest tests/ -q` зелёные
        (138 passed).

**Критерий приёмки фазы 2:**
- [ ] Нет мёртвого кода (по результатам T-2.2 … T-2.5).
- [ ] Тесты: round-trip сериализации, хендлеры бота, общий stub-модуль; нет
  обращений к приватным методам.
- [ ] CI (mypy + docker + compose) зелёный; README воспроизводим.
- [ ] `uv run pytest tests/ -q`, `ruff check .`, `ruff format --check .` — зелёные.

---

## Общий чек-лист завершения всего плана

- [ ] Все P0 (T-0.1 … T-0.6) закрыты; сквозной прогон по compose работает.
- [ ] Все P1 (T-1.1 … T-1.8) закрыты; DLQ/reaper/чанкинг/trace/health на месте.
- [ ] Все P2 (T-2.1 … T-2.9) закрыты.
- [ ] `audit.md` пометить как закрытый (сверка: каждая находка → задача → `[x]`).
- [ ] `uv run pytest tests/ -q` · `uv run ruff check .` · `uv run ruff format --check .` ·
      `uv run mypy packages services` · `docker compose up -d` (healthchecks) — все зелёные.
