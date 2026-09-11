# T-0.2: uv sync --frozen (uv.lock) вместо ручной установки зависимостей.
# T-2.8: non-root USER; один uv sync без дублирования (без uv pip install).
# python:3.13-alpine: итоговый образ (<200MB) меньше, чем uv bookworm-slim base (273MB) —
# критерий T-2.8; у остальных сервисов база не менялась.
FROM python:3.13-alpine

ENV UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

# uv ставится один раз (раньше два слоя: uv sync + uv pip install).
RUN pip install --no-cache-dir uv

# appuser (uid 10001, как в других сервисах). /app создаётся WORKDIR ниже (после USER),
# поэтому слоя chown /app не нужно.
RUN addgroup -g 10001 appuser \
    && adduser -D -u 10001 -G appuser -h /home/appuser appuser \
    && mkdir -p /data/media /data/artifacts && chown -R appuser:appuser /data
USER appuser
WORKDIR /app

# Сначала lockfile (кэш слоя по uv.lock), затем исходники.
COPY --chown=appuser:appuser pyproject.toml uv.lock ./
COPY --chown=appuser:appuser packages/ packages/
COPY --chown=appuser:appuser services/bot/ services/bot/
RUN uv sync --frozen --package swot-bot --no-dev

ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "swot_bot"]
