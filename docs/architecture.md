# Архитектура swot-bot

Сквозной пайплайн: **ссылка на лекцию → скачивание → транскрипция →
выжимка (LLM) → сообщение в Telegram**. Связь между сервисами — только
события на RabbitMQ (event-driven, без RPC между сервисами).

## Пайплайн

```
admin ──(Telegram)──▶ bot ──download.request──▶ downloader
                                              │ video.downloaded
                                              ▼
                                           transcriber ──transcript.ready──▶ analyzer
                                                                       │ analysis.ready
bot ◀──result.deliver── (analysis.ready | job.failed | job.progress) ◀──┘
```

## Сервисы

| сервис | очередь | роутинг-ключи | роль |
|---|---|---|---|
| `bot` | `result.deliver` | `analysis.ready`, `job.failed`, `job.progress` | aiogram-бот: валидация ссылки, публикация результата/ошибок, `_kind_for` по источнику |
| `downloader` | `video.download` | `download.request` | скачивание медиа (yt-dlp / прямое HTTP) |
| `transcriber` | `video.transcribe` | `video.downloaded` | извлечение аудио (ffmpeg) + ASR (OpenAI-совместимый endpoint) → SRT + сегменты |
| `analyzer` | `video.analyze` | `transcript.ready` | LLM-выжимка: `summary.json` (резюме + факты с таймкодами) |

Брокер: RabbitMQ; каждый сервис объявляет свою очередь + DLX (см. ниже).

## Контракты и события (`swot_contracts`)

- Все сообщения — Pydantic-модели из `swot_contracts.events` с общим
  `BaseMessage`: `msg_type`, `task_id`, `trace_id`. `trace_id` генерирует
  бот и ставит в AMQP-заголовок — он виден в логах всех сервисов.
- `MessageType` — StrEnum с каноническими ключами: `download.request`,
  `video.downloaded`, `transcript.ready`, `transcript.failed`,
  `analysis.ready`, `analysis.failed`, `job.failed`, `job.progress`
  (+ унаследованный `download.failed`).
- **Лёгкие сообщения**: события несут пути + id, а не содержимое —
  медиа и артефакты лежат в общих томах (`MEDIA_DIR`, `ARTIFACTS_DIR`),
  каждый сервис читает только свой каталог задачи.
- Сериализация на шине — JSON; десериализация знает только свои
  `msg_type`, чужие игнорируются.

### Ссылки на файлы: path-containment (P0-6)

Поля-пути (`base_dir`, `srt_path`, `summary_path`, `media_path`) —
недоверенный ввод: любой сервис мог их выставить. Все чтения/удаления
идут через `swot_contracts.paths.resolve_under(base, path)`: разрешены
только пути внутри ожидаемого корня, иначе — ошибка. Прямых
`open()`/`unlink()` по полям событий нет.

## Жизненный цикл задачи (`JobRegistry`)

Статусы: `pending → downloading → downloaded → transcribing →
transcribed → analyzing → ready` (финальные: `ready`, `failed`).

- **P2-1**: `READY` — только analyzer (готовность результата к
  доставке); `downloaded`/`transcribed` — промежуточные статусы.
- **Reaper** (downloader, раз в 60с): задача без финального статуса
  дольше `JOB_TIMEOUT_HOURS` → `job.failed` ("timeout").
- **TTL-cleanup** (downloader, раз в час): финальные записи реестра +
  устаревшие каталоги задачи в `MEDIA_DIR`/`ARTIFACTS_DIR`
  (`retention_hours`).

## Ошибки и DLQ

- Единый срез ошибок: любой сервис на любой стадии публикует
  `job.failed` (`stage` + человекочитаемый `error`); бот доставляет его
  админу, а target-чат не засоряется.
- Очередь с DLX (`<exchange>.dlx`) → `x-dead-letter-*`; nack'нутые
  сообщения попадают в `<queue>.dlq` — ничего не теряется.
  Отравленное сообщение (повторяющаяся ошибка) уходит в DLQ и не
  зацикливает очередь.

## Конфигурация

Один `Settings` (`swot_contracts.config`) для всех сервисов: скаляры —
из переменных напрямую (`MEDIA_DIR`, `HEALTH_PORT`), вложенные —
`SECTION__FIELD` (`LLM__API_URL`, `BROKER__USER`, ...).
Значения dev-стека — в `.env.example` (они же дефолты compose).

## Источники и адаптеры (downloader)

`SourceRouter` выбирает адаптер по URL:

1. **Платформенные хосты** (youtube, youtu.be, vk, vkvideo, rutube,
   vimeo, dzen, ok, tiktok, disk.yandex.ru, drive.google — суффиксное
   совпадение, т.е. субдомены вкл.) → `YtDlpAdapter`;
2. **Прямые медиа-URL** (расширение `.mp4/.m4a/.mp3/.wav/.ogg/.flac`
   или HEAD `Content-Type: video/*|audio/*`) → `DirectHttpAdapter`;
3. Остальное → `YtDlpAdapter` (по умолчанию).

Защита: yt-dlp в worker-потоке под общим дедлайном (event loop не
зависает, P1-3); длительность проверяется **до** скачивания
(P1-4); прямое HTTP — проверка content-type и жёсткий лимит размера с
очисткой частичного файла.

### Валидация ссылок (T-2.5)

`swot_contracts.urls.UrlValidator` — **общий** валидатор: бот проверяет
ссылку на входе, downloader — **повторно** проверяет `source` из события
(defense in depth: событие могло прийти не от бота). Semantics:
http/https only, ≤2048 символов, опциональный allowlist
(`DOWNLOADER__ALLOWED_SOURCES`, пусто = любые ссылки) с нормализацией
netloc: стандартный порт схемы игнорируется (`youtube.com:443`),
субдомены совпадают по суффиксу с границей (`m.youtube.com` ✓,
`notyoutube.com` ✗), нестандартный порт — другой endpoint (✗ без явного
порта в allowlist).

## Безопасность (решения аудита)

- **P0-6**: path-containment (`resolve_under`) — см. выше.
- **T-2.3**: в production-коде сервисов нет in-process фейков;
  test-двойники (`FakeBus` и пр.) живут только в `tests/unit/fakes.py`.
  Dev-фейки (`asr-service`, `llm-service`, `fake-tg`) — отдельные
  compose-контейнеры, не входят в образы пайплайна.
- **T-2.5**: повторная валидация source в downloader.

## Сообщение админу

Шаблон Jinja2 (`services/bot/src/swot_bot/templates/result_message.html.j2`):
заголовок = `summary.title` (title из заголовка видео, T-2.4), резюме,
факты со ссылками `srt#t=…` в target-чат (в SRT-файле из
`AnalysisReady.srt_path`), чанкование длинных текстов.

## Dev-стек

`docker-compose.yml` — весь пайплайн без API-ключей: фэйки
ASR/LLM (OpenAI-совместимые) и `fake-tg` (заглушка Telegram API с
`POST /inject` для инъекции «сообщения от админа»). Quickstart и
переход на реальные зависимости — в `README.md`.

## Этапный план работ

`todo/plan.md` (исследование — `audit.md`; снимки ревью — `reviews/`).
