"""structlog configuration: every log line is one JSON object.

Both structlog loggers (``get_logger``) and plain stdlib ``logging`` loggers
(aio_pika, openai, aiohttp, aiogram, ``logging.getLogger(__name__)``) flow
through a single :class:`~structlog.stdlib.ProcessorFormatter`, so one
pipeline run is traceable end-to-end: bound ``trace_id``/``task_id``/``stage``
contextvars land in *every* line — structlog or stdlib alike (T-1.5).
"""

import logging
import sys
import uuid
from typing import Any

import structlog
import structlog.contextvars
import structlog.processors
import structlog.stdlib


def new_trace_id() -> str:
    """Generate a fresh trace id for a pipeline run."""
    return f"t-{uuid.uuid4().hex[:16]}"


def _build_formatter() -> structlog.stdlib.ProcessorFormatter:
    """One formatter for structlog and stdlib records (same JSON shape)."""
    # Runs for *foreign* (stdlib) records before the formatter processors.
    pre_chain: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        foreign_pre_chain=pre_chain,
    )


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog + stdlib logging: one JSON line per event on stdout."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_build_formatter())
    # force=True: reconfiguration is idempotent (tests call this too).
    logging.basicConfig(level=log_level, handlers=[handler], force=True)

    # Route structlog through the stdlib logging module so native and
    # foreign records share the handler above (and its JSON formatter).
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.ExtraAdder(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(*args: Any, **kwargs: Any) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger (JSON, contextvars-aware)."""
    return structlog.get_logger(*args, **kwargs)
