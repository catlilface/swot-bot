"""structlog configuration shared across services.

Produces JSON logs with bound ``trace_id`` / ``task_id`` / ``stage`` context so a
single pipeline run is traceable end-to-end.
"""

import logging
import sys
from typing import Any

import structlog


def new_trace_id() -> str:
    """Generate a fresh trace id for a pipeline run."""
    import uuid

    return f"t-{uuid.uuid4().hex[:16]}"


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(*args: Any, **kwargs: Any) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(*args, **kwargs)
