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
  (3) `langfuse` — healthcheck + `depends_on: service_healthy` у analyzer
  (либо убрать langfuse из compose с явной пометкой, что анализатор работает по
  fallback — тогда T-0.4 обязателен); (4) ASR/LLM: либо добавить контейнеры-заглушки
  (fake ASR/LLM для dev), либо в `.env.example` — реальные внешние URL **со схемой**
  `http://`; (5) `restart: unless-stopped` всем; (6) README: quickstart
  (`cp .env.example .env`, `docker compose up`, что и где).
- Приёмка:
  - [ ] `docker compose config -q` — без ошибок.
  - [ ] `docker compose up -d` → все контейнеры `running`, healthchecks `healthy`
        (rabbit, postgres, langfuse, 4 сервиса) в пределах ~2 минут.
  - [ ] `docker compose exec bot ls /data/artifacts` — том примонтирован (видны файлы,
        созданные пайплайном).
  - [ ] Сквозной прогон (с fake ASR/LLM или внешними): админ кидает ссылку → в чат
        приходит выжимка + SRT-файл; в логах всех 4 сервисов — один и тот же `trace_id`.
  - [ ] `docker compose down && docker compose up -d` — стек восстанавливается
        (restart-политики).

### T-0.6 Path containment для путей из сообщений
- Находки: P0-6. Зависимости: нет.
- Scope: новый helper (в `swot_contracts` или `swot_bus`), `swot_analyzer/service.py`,
  `swot_bot/handlers.py`, `swot_transcriber/service.py`, тесты.
- Работа: `resolve_under(base, candidate)`: запрет абсолютных путей, `..`,
  symlink-эскейпов; возврат `resolved` только внутри `base.resolve()`. Применить:
  analyzer (`base_dir`, `srt_path`), bot (`summary_path`), transcriber (`media_path`).
  До готовности — минимум: суффиксы `*.json/*.srt/*.txt` + reject абсолютных/`..`.
- Приёмка:
  - [ ] Тесты helper: `../etc/passwd`, `/etc/passwd`, symlink-ссылка наружу →
        `ValueError` (или `JobFailed` на уровне сервиса); `base/sub/file.txt` — ок.
  - [ ] Analyzer: событие с `base_dir="../../"` → `summary.json` **не** создается вне
        `artifacts_dir`, задача → FAILED с понятной ошибкой.
  - [ ] Bot: событие с `summary_path="/etc/passwd"` → сообщение не падает, логирует
        ошибку, файл не читается.
  - [ ] Transcriber: `media_path` вне `media_dir` → файл не удаляется.
  - [ ] `uv run pytest tests/ -q` — зелёные.

**Критерий приёмки фазы 0 (все вместе):**
- [ ] `docker compose up -d` — стек поднимается, healthchecks зелёные.
- [ ] Сквозной прогон от ссылки до результата в чате работает (см. T-0.5).
- [ ] `uv run pytest tests/ -q`, `ruff check`, `ruff format --check` — зелёные.

---

## Фаза 1 — «надёжность контура»

### T-1.1 Settings: префиксы и опциональные секции
- Находки: P1-1, P1-2. Зависимости: нет.
- Scope: `packages/contracts/src/swot_contracts/config/*.py`, `.env.example`, тесты.
- Работа: `env_prefix` для каждой под-секции (`BROKER__`, `LLM__`, …); секции —
  опциональны (default-экземпляры); `LangfuseSettings.public_key/secret_key = ""`;
  поправить docstring'ы; `.env.example` привести к новым префиксам.
- Приёмка:
  - [ ] `Settings()` в **пустом** env — конструируется без ошибок (все секции по
        умолчанию).
  - [ ] Тест-регрессия: `USER=macuser PORT=9999 LANGUAGE=en_US:en MODEL=x` в env →
        `broker.user == "guest"`, `broker.port == 5672`, `transcriber.language == ""`,
        `transcriber.model == "whisper-1"`.
  - [ ] Анализатор/бот стартуют без `LANGFUSE__PUBLIC_KEY/SECRET_KEY`.
  - [ ] `uv run pytest tests/ -q` — зелёные (старые тесты на Settings обновлены).

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
  - [ ] Тест: сообщение с payload-ом, ломающим десериализацию → после N попыток
        попадает в `dlq`-очередь (не теряется: в очереди DLQ есть сообщение).
  - [ ] Тест reaper: задача, «застрявшая» в DOWNLOADING дольше TTL → публикуется
        `JobFailed` со стадией и причиной.
  - [ ] Тест чистки: старые каталоги в `media_dir` **и** `artifacts_dir` старше TTL
        удаляются; свежие — нет.
  - [ ] `uv run pytest tests/ -q` — зелёные.

### T-1.3 Скорость/блокировки скачивания
- Находки: P1-3, P1-4, P2-18. Зависимости: T-0.3.
- Scope: `services/downloader/src/swot_downloader/adapters.py`, `service.py`.
- Работа: `YtDlpAdapter` — `asyncio.to_thread` + `wait_for(timeout=timeout_sec)`,
  `duration` проверять по info-экстракции **до** загрузки, формат
  `bestaudio[acodec=none]/bestaudio`; `DirectHttpAdapter` — таймауты `sock_connect`/
  `sock_read` (не только `total`), проверка `Content-Type`/расширения, лимит размера
  файла.
- Приёмка:
  - [ ] Тест: скачивание медленного (> timeout_sec) ресурса → `DownloadError`/
        `JobFailed`, а не зависание; event loop не блокируется (параллельный тик
        healthz во время «скачивания» — проверка через fake-адаптер со сном в
        `to_thread`).
  - [ ] Тест: `duration` в info > `max_duration_sec` → ошибка **до** скачивания.
  - [ ] DirectHttp: ответ `text/html` на URL `*.mp4` → ошибка, файл не сохраняется.
  - [ ] Файл > `MAX_FILE_MB` → прерывается, частичный файл удаляется.
  - [ ] `uv run pytest tests/ -q` — зелёные.

### T-1.4 ASR-чанкинг и LLM-чанкинг
- Находки: P1-6, P1-7. Зависимости: нет.
- Scope: `services/transcriber/src/swot_transcriber/*`, `services/analyzer/src/swot_analyzer/*`.
- Работа: (1) ASR: разбивка длинного аудио на сегменты 10–15 мин (ffmpeg `segment`),
  постраничное отправка в ASR со сдвигом таймкодов при сборке SRT, лимит на размер
  аудио, `timeout` на вызов; (2) LLM: map-reduce по транскрипту
  (`langchain-text-splitters` уже в зависимостях) + `max_tokens`/`timeout`, лимит на
  размер транскрипта с понятной ошибкой.
- Приёмка:
  - [ ] Тест: «аудио» 3 часа (можно эмуляцией списка сегментов) → N ASR-вызовов,
    SRT-таймкоды сдвинуты корректно (первый сегмент = 00:00:00, второй =
    00:10:00:00, …).
  - [ ] Тест: аудио > лимита → `JobFailed` с явной причиной до загрузки в память.
  - [ ] Тест: транскрипт > контекста → map-reduce: LLM вызывается несколько раз,
    итоговая `Summary` валидна (Pydantic), факты не дублируются.
  - [ ] `uv run pytest tests/ -q` — зелёные.

### T-1.5 Сквозной trace_id + единый JSON-лог
- Находки: P1-8, P1-9. Зависимости: нет.
- Scope: `services/bot/src/swot_bot/handlers.py`, `swot_observability/logging_config.py`.
- Работа: бот — `bind_contextvars(trace_id=...)` до первого `publish` (и
  `task_id/stage` при обработке); structlog stdlib integration, чтобы aio_pika/
  aiohttp/aiogram/openai логились в тот же JSON.
- Приёмка:
  - [ ] Тест: `bus.publish(...)` после `bind_contextvars(trace_id=X)` → в header
        сообщения `swot-trace-id: X`; consumer биндит его в контекст (round-trip).
  - [ ] В логах бота, downloader, transcriber, analyzer одного прогона — одинаковый
        `trace_id` и `task_id` (ручная проверка по compose-логам + тест).
  - [ ] `docker compose logs` — каждый JSON-лог (включая `aio_pika`, `openai`)
        парсится `json.loads`.

### T-1.6 Health: readiness и graceful shutdown
- Находки: P1-9. Зависимости: нет.
- Scope: `swot_observability/health.py`, `swot_bus/rabbit.py`, 4 × `__main__.py`.
- Работа: `HealthServer.stop()`; readiness = «bus подключён» (колбэк из
  `RabbitMessageBus.connect()`); порядок shutdown: SIGTERM → drain consume → close bus
  → stop health → exit 0.
- Приёмка:
  - [ ] Тест: до `connect()` `/readyz` → `{"status":"degraded"}`; после — `ok`.
  - [ ] Тест: `SIGTERM` в процессе consume → процесс завершается кодом 0 за
        < grace period, очередь ack'нуты/рекею-нуты корректно (тест на FakeBus/
        RabbitMessageBus с локальным брокером в CI).
  - [ ] Compose: `docker compose stop bot` → контейнер уходит без `Killed`.

### T-1.7 Доставка результата в Telegram
- Находки: P1-10. Зависимости: T-0.6.
- Scope: `services/bot/src/swot_bot/handlers.py`, `renderer.py`.
- Работа: ретраи с backoff на Telegram-ошибки (network/rate-limit) перед nack;
  `on_failed` — человекочитаемое сообщение + technical detail только в лог;
  `chunk_text` — без потери `\n` на границе чанков, без разрыва HTML-тегов/сущностей.
- Приёмка:
  - [ ] Тест: `message.answer` падает 2 раза (rate limit) и succeeds → сообщение
        доставлено, nack не идёт.
  - [ ] Тест: `summary_path` не существует → админ получает нормализованное
        «не удалось доставить результат», traceback не уходит в чат.
  - [ ] Тест `chunk_text`: на тексте с переносами и HTML-тегами
        `"".join(chunks) == original` и ни один чанк не содержит половинки тега
        (`<b>` … `</b>` не раздельны).
  - [ ] `uv run pytest tests/ -q` — зелёные.

### T-1.8 CI: mypy + docker + compose-интеграция
- Находки: P1-12, P1-13. Зависимости: T-0.2 (для docker job).
- Scope: `.github/workflows/ci.yml`, `pyproject.toml` (mypy-конфиг).
- Работа: (1) `mypy` (strict или close-to-strict) на `packages/*` + `services/*`;
  (2) job `docker-build` (T-0.2); (3) job `compose`: testcontainers (RabbitMQ + fake
  ASR/LLM) → поднять 4 сервиса, проверка healthz + сквозного пайплайна.
- Приёмка:
  - [ ] CI: мойпы-ошибка блокирует merge (`mypy packages services --strict` или
        согласованный набор).
  - [ ] CI: docker-build job зелёный на main.
  - [ ] CI: compose-integration job зелёный (healthz 4 сервисов + E2E-прогон).
  - [ ] Локально воспроизводимо: `uv run mypy packages services` — без ошибок.

**Критерий приёмки фазы 1:**
- [ ] Потеря сообщения в любом узле пайплайна обнаруживается (DLQ) и не «зависает»
      статус задачи (reaper).
- [ ] Лекция 2+ часа проходит до результата без OOM/таймаутов (чанкинг ASR/LLM).
- [ ] В логах любого узла виден сквозной `trace_id`; `/readyz` отражает реальное
      состояние брокера; `SIGTERM` — graceful.
- [ ] CI ловляет: типы, битые Docker-образы, битый compose-стек.

---

## Фаза 2 — «чистота»

### T-2.1 Семантика статусов задач
- Находки: P2-1. Зависимости: T-1.2 (reaper читает статусы).
- Scope: `swot_contracts/events.py` (`JobStatus`), сервисы, тесты.
- Работа: `READY` — только анализатор; (опционально) отдельные статусы после
  download/transcribe, либо `JobProgress` как источник истины.
- Приёмка:
  - [ ] Тесты downloader/transcriber утверждают промежуточные статусы (не READY),
    тест analyzer — READY.
  - [ ] Reaper использует финальные статусы (`READY`/`FAILED`) — корректно.
  - [ ] `uv run pytest tests/ -q` — зелёные.

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
  - [ ] `grep -rn "job.events\|job_events" packages services` — пусто (или
        осознанное использование с тестом).
  - [ ] `import swot_contracts.events; TranscribeRequest/AnalysisRequest` — удалены
        **или** поддерживаются `deserialize` (тест round-trip).
  - [ ] `uv run pytest tests/ -q`, `ruff check` — зелёные (no unused).

### T-2.3 Тест-дубли вне production-модулей
- Находки: P2-3. Зависимости: нет.
- Scope: `swot_bus/fake.py`, `swot_bus/providers.py`, `swot_analyzer/providers.py`.
- Работа: либо перенести `FakeBus`/`FakeTranscriber`/`StubSummarizer` + их провайдеры
  в тесты, либо задокументировать dev-режим (`SWOT_FAKE_TRANSCRIBER=1`) и его
  семантику.
- Приёмка:
  - [ ] Либо: production-модули не содержат `*Fake*`/`Stub*`; тесты используют
        перенесённые двойники — `pytest` зелёные.
  - [ ] Либо: README/доки описывают dev-режим; `FakeTranscriberProvider` не
        регистрируется по умолчанию (только при флага).
  - [ ] Ревью: ни один production-контейнер не активирует фейки без явного флага.

### T-2.4 Контракты и рендер: точечные исправления
- Находки: P2-4 … P2-10. Зависимости: нет.
- Scope: `swot_analyzer/service.py` (`model_dump`), `swot_contracts/events.py`
  (`Summary.title`), `swot_bot/handlers.py` (SRT из события, не пересборка пути),
  `swot_contracts/events.py` (`Section` — `Field`), `swot_bot/renderer.py` (кэш
  шаблона).
- Приёмка:
  - [ ] `Summary` получает `title` из `VideoDownloaded.title`; бот рендерит его
    (тест: заголовок из download появляется в сообщении).
  - [ ] `_summary_to_dict` == `summary.model_dump(mode="json")` (тест на равенство).
  - [ ] `AnalysisReady` несёт путь SRT; `on_analysis` использует его, не
    пересобирает (тест: изменение `TRANSCRIBER__ARTIFACTS_DIR` не ломает доставку).
  - [ ] `Section(facts=...)` — `Field(default_factory=dict)`.
  - [ ] Рендер кэширует шаблон (тест: повторный `render` не читает файл с диска).

### T-2.5 UrlValidator: нормализация и повторная валидация
- Находки: P2-7. Зависимости: нет.
- Scope: `swot_bot/validation.py`, `swot_downloader/service.py`.
- Работа: нормализация netloc (strip порта по дефолту схемы, суффикс-совпадение
  субдоменов); в downloader — повторная проверка `source` через тот же валидатор
  (defense in depth).
- Приёмка:
  - [ ] Тесты: `m.youtube.com`, `youtube.com:443` → разрешены; `youtu.be`/
        `malicious.com` → отвергнуты (те же + новые кейсы).
  - [ ] Тест: downloader получает событие с `source="https://evil.com/x.mp4"` →
        `JobFailed` (валидация на стороне downloader), даже если бот не проверил.
  - [ ] `uv run pytest tests/ -q` — зелёные.

### T-2.6 Документация
- Находки: P2-11, P0-5 (README). Зависимости: T-0.5 (quickstart).
- Scope: `README.md`, `docs/*`, docstring'ы, `reviews/` (gitignore или commit).
- Работа: дописать `docs/architecture.md` (или убрать ссылки из README и
  docstring'ов), quickstart (локально + docker), решить судьбу `reviews/`.
- Приёмка:
  - [ ] `grep -rn "docs/architecture.md\|docs/plan.md" README.md packages services` —
        либо файлы существуют, либо ссылок нет.
  - [ ] По README с чистого клона: `cp .env.example .env && docker compose up` —
        инструкции совпадают с compose (T-0.5).
  - [ ] `git status` — `reviews/` закоммичен или в `.gitignore`.

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
  - [ ] Тесты round-trip зелёные; неизвестный `type` → `ValueError`/`JobFailed`.
  - [ ] `grep -rn "StubRouter\|StubExtractor\|StubTranscriber" tests` — определение
        встречается один раз (общий модуль).
  - [ ] `grep -rn "\._[a-z]" tests/` — обращения к приватным методам отсутствуют.
  - [ ] Хендлеры бота покрыты тестами (покрытие `swot_bot/handlers.py` ≥ 80%).

### T-2.8 Docker/compose-гигиена
- Находки: P2-8, P2-13, P2-14. Зависимости: T-0.2, T-0.5.
- Scope: `.docker/*`, `docker-compose.yml`, `swot_contracts/config/broker_settings.py`.
- Работа: non-root `USER` в образах; один `uv sync` (без дубля); healthcheck у
  langfuse; `rabbit_url` — URL-энкодинг user/password.
- Приёмка:
  - [ ] `docker run --rm swot-bot-bot id` — не root; `du -sh` образа меньше базового
        (одна стадия sync).
  - [ ] Тест: `BROKER__PASSWORD='p@ss/w ord'` → `rabbit_url` содержит
        `p%40ss%2Fw%20ord`.
  - [ ] `docker compose up` — langfuse-healthcheck проходит, analyzer ждёт его.

### T-2.9 Мелочи
- Находки: P2-16 … P2-18. Зависимости: нет.
- Scope: `swot_contracts/config/llm_settings.py`, `swot_analyzer/summarizer.py`,
  `swot_transcriber/pyproject.toml`, `swot_bot/handlers.py`, `swot_bus/serialization.py`,
  сервисы (локальные импорты).
- Работа: `sampling_parameters: dict[str, float | int | str]` (или `dict[str, Any]`)
  + применение всех параметров (не только temperature); единый стиль импортов
  (top-level); удалить неиспользуемый `logger` в `handlers.py`; согласовать
  dev-groups в pyproject'ах.
- Приёмка:
  - [ ] Тест: `sampling_parameters={"temperature": 0.3, "max_tokens": 500}` → оба
        попадают в `ChatOpenAI`.
  - [ ] `ruff check` без `F401` (unused logger); `uv run pytest tests/ -q` зелёные.

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
