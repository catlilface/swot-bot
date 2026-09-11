# Dev stubs for external dependencies (OpenAI-compatible ASR/LLM, Telegram Bot API).
# Stdlib-only Python; see services/fake/*.py.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY services/fake/fake_openai.py services/fake/fake_telegram.py ./

# Executable shims so compose `command: asr|llm|tg` works — docker execs
# the bare word directly, so it must be a real file on PATH (not a Python arg).
RUN printf '#!/bin/sh\nexec python /app/fake_openai.py asr "$@"\n' > /usr/local/bin/asr \
    && printf '#!/bin/sh\nexec python /app/fake_openai.py llm "$@"\n' > /usr/local/bin/llm \
    && printf '#!/bin/sh\nexec python /app/fake_telegram.py "$@"\n' > /usr/local/bin/tg \
    && chmod +x /usr/local/bin/asr /usr/local/bin/llm /usr/local/bin/tg

# Non-root runtime user (T-2.8): the stubs only serve HTTP on high ports.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000 8081

CMD ["python", "fake_openai.py", "asr"]
