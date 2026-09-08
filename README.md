# swot-bot

Бот: ссылка на лекцию → транскрипция (faster-whisper) → выжимка (LLM, факты
со ссылками на таймкоды) → публикация в Telegram.

**Документация:**
- [docs/architecture.md](docs/architecture.md) — каноническая архитектура
  (микросервисы, CQRS/event-driven, адаптеры источников, шаблон сообщения,
  docker-compose).
- [docs/plan.md](docs/plan.md) — этапный план работ и исследование стека.