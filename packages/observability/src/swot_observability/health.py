"""Minimal health/readiness HTTP server (aiohttp) for docker healthchecks."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiohttp import web

logger = logging.getLogger(__name__)

LivenessProbe = Callable[[], Awaitable[bool]]


class HealthServer:
    """Serves ``/healthz`` (liveness) and ``/readyz`` (readiness)."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        readiness: LivenessProbe | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._readiness = readiness
        self._app = web.Application()
        self._app.router.add_get("/healthz", self._healthz)
        self._app.router.add_get("/readyz", self._readyz)

    async def _healthz(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _readyz(self, request: web.Request) -> web.Response:
        if self._readiness is None:
            return web.json_response({"status": "ok"})
        try:
            ok = await self._readiness()
        except Exception as exc:  # noqa: BLE001
            logger.exception("readiness check failed", error=str(exc))
            ok = False
        return web.json_response(
            {"status": "ok" if ok else "degraded"}, status=200 if ok else 503
        )

    async def start(self) -> None:
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._port)
        await site.start()
        logger.info("health server started", host=self._host, port=self._port)
        # Keep the runner alive (caller usually runs this as a task).
        await asyncio.Event().wait()
