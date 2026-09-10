"""Shared observability: structlog config, healthz/readyz, graceful shutdown."""

from .health import HealthServer
from .lifecycle import install_shutdown_signal_handlers, run_until_shutdown
from .logging_config import configure_logging, get_logger, new_trace_id
from .providers import ObservabilityProvider, SettingsProvider

__all__ = [
    "HealthServer",
    "ObservabilityProvider",
    "SettingsProvider",
    "configure_logging",
    "get_logger",
    "install_shutdown_signal_handlers",
    "new_trace_id",
    "run_until_shutdown",
]
