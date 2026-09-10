"""Graceful shutdown orchestration for service entrypoints (T-1.6).

Shutdown order: SIGTERM/SIGINT → stop consume (cancel work tasks) →
close bus (the bus drains in-flight dispatches) → stop health server →
exit code 0. Work errors (crash before the signal) still propagate so the
process exits non-zero.
"""

import asyncio
import logging
import signal
from collections.abc import Coroutine

logger = logging.getLogger(__name__)


def install_shutdown_signal_handlers() -> asyncio.Event:
    """Register SIGTERM/SIGINT on the running loop → set an ``asyncio.Event``.

    Returns the event so callers/tests can pass their own (e.g. to trigger
    shutdown without a real signal).
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            logger.debug("cannot install signal handler for %s", sig.name)
    return stop


async def run_until_shutdown(
    *work: Coroutine, stop: asyncio.Event | None = None
) -> None:
    """Run ``work`` coroutines concurrently until a shutdown signal (or an error).

    On the signal: every unfinished work task is cancelled and awaited; the
    function returns normally (exit code 0). Draining in-flight broker
    messages is the bus's ``close()`` responsibility — the caller invokes it
    right after.

    If a work task raises instead (crash before the signal), the exception is
    re-raised so the process exits non-zero.
    """
    stop = stop or install_shutdown_signal_handlers()
    tasks = [asyncio.ensure_future(coro) for coro in work]
    stop_task = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait(tasks + [stop_task], return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop_task.cancel()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, stop_task, return_exceptions=True)
    for task in tasks:
        if task.cancelled():
            continue  # штатное отменённое завершение (shutdown)
        exc = task.exception()
        if exc is not None:
            raise exc
    logger.info("shutdown: %d work task(s) stopped", len(tasks))
