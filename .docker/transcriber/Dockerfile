FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

# Workspace manifests: only the shared packages + this service (see .dockerignore).
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/transcriber/ services/transcriber/

# One sync: dependencies + workspace packages into /app/.venv (no system python).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package swot-transcriber --no-dev

# ffmpeg: FfmpegAudioExtractor shells out to it (audio extraction for the ASR call).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

# `python` resolves to /app/.venv/bin/python.
ENV PATH="/app/.venv/bin:$PATH"

# Non-root runtime user; /data holds the media volume, /artifacts the artifacts volume.
RUN mkdir -p /data /artifacts && \
    useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app /data /artifacts
USER appuser

CMD ["python", "-m", "swot_transcriber"]
