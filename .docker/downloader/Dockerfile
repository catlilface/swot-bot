FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

# Workspace manifests: only the shared packages + this service (see .dockerignore).
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/downloader/ services/downloader/

# One sync: dependencies + workspace packages into /app/.venv (no system python).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package swot-downloader --no-dev

# `python` resolves to /app/.venv/bin/python.
ENV PATH="/app/.venv/bin:$PATH"

# Non-root runtime user; /data holds media + artifacts volumes.
RUN mkdir -p /data/media /data/artifacts && \
    useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app /data
USER appuser

CMD ["python", "-m", "swot_downloader"]
