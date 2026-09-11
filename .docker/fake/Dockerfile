# Dev stubs for external dependencies (OpenAI-compatible ASR/LLM, Telegram Bot API).
# Stdlib-only Python; see services/fake/*.py.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY services/fake/fake_openai.py services/fake/fake_telegram.py ./

# Non-root runtime user (T-2.8): the stubs only serve HTTP on high ports.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000 8081

CMD ["python", "fake_openai.py", "asr"]
