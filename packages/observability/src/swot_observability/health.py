"""Health/readiness HTTP server (aiohttp) for docker healthchecks.

``/healthz`` — liveness (always ok while the process runs).
``/readyz`` — readiness: an optional async probe (typically
``MessageBus.is_ready``) decides ``ok`` vs ``degraded`` (HTTP 503).
``start()`` is non-blocking and ``stop()`` tears the server down
gracefully (T-1.6 shutdown order: drain → close bus → stop health).
"""

import logging
from collections.abc import Awaitable, Callable

from aiohttp import web

logger = logging.getLogger(__name__)

LivenessProbe = Callable[[], Awaitable[bool]]


class HealthServer:
    """aiohttp server with ``/healthz`` and ``/readyz`` endpoints."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        readiness: LivenessProbe | None = None,
    ) -> None:
        self._host = host
        self._requested_port = port
        self._readiness = readiness
        self._app = web.Application()
        self._app.router.add_get("/healthz", self._healthz)
        self._app.router.add_get("/readyz", self._readyz)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def _healthz(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _readyz(self, request: web.Request) -> web.Response:
        if self._readiness is None:
            return web.json_response({"status": "ok"})
        try:
            ready = await self._readiness()
        except Exception as exc:  # noqa: BLE001 - a failing probe is "not ready"
            logger.warning("readiness probe failed: %s", exc)
            ready = False
        status = "ok" if ready else "degraded"
        return web.json_response({"status": status}, status=200 if ready else 503)

    async def start(self) -> None:
        """Start the HTTP server (non-blocking)."""
        if self._site is not None:
            msg = "health server already started"
            raise RuntimeError(msg)
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._requested_port)
        await site.start()
        self._runner = runner
        self._site = site
        logger.info("health server started: host=%s port=%d", self._host, self.port)

    @property
    def port(self) -> int:
        """The bound port (useful when started with ``port=0``)."""
        if self._site is None or self._site._server is None:  # noqa: SLF001
            msg = "health server not started"
            raise RuntimeError(msg)
        if not self._site._server.sockets:  # noqa: SLF001
            msg = "health server socket unavailable"
            raise RuntimeError(msg)
        return self._site._server.sockets[0].getsockname()[1]  # noqa: SLF001

    async def stop(self) -> None:
        """Gracefully stop the server; a no-op if not started."""
        if self._site is None:
            return
        site, runner, self._site, self._runner = (
            self._site,
            self._runner,
            None,
            None,
        )
        await site.stop()
        if runner is not None:
            await runner.cleanup()
        logger.info("health server stopped")
