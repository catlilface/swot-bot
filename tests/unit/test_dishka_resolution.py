"""T-0.1: dishka-контейнер разрешает зависимости по протоколам (P0-1).

`RegistryProvider` регистрирует `InMemoryJobRegistry` по протоколу
`JobRegistry` (`provides=`), а `SettingsProvider` даёт контейнеру
единственный `Settings` — поэтому каждый сервис разрешается
`request_container.get(<Service>)` без `NoFactoryError`.
"""

from uuid import UUID

import pytest
from dishka import make_async_container
from swot_analyzer.providers import AnalyzerAdaptersProvider, AnalyzeServiceProvider
from swot_analyzer.service import AnalyzeService
from swot_bot.providers import BotConsumersProvider, BotHandlersProvider
from swot_bus import InMemoryJobRegistry, RegistryProvider
from swot_bus.providers import FakeBusProvider
from swot_contracts import get_settings
from swot_contracts.ports import JobRegistry
from swot_downloader.providers import (
    DownloaderAdaptersProvider,
    DownloaderServiceProvider,
)
from swot_downloader.service import DownloaderService
from swot_observability import ObservabilityProvider, SettingsProvider
from swot_transcriber.providers import (
    TranscriberAdaptersProvider,
    TranscribeServiceProvider,
)
from swot_transcriber.service import TranscribeService


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Наполнить env всеми секциями Settings (текущая схема: секции required).

    T-1.1 сделает секции опциональными — тогда fixture станет проще.
    """
    env = {
        "BROKER__HOST": "rabbit-test",
        "DOWNLOADER__MAX_VIDEO_DURATION_SEC": "600",
        "LANGFUSE__HOST": "langfuse-test",
        "LANGFUSE__PUBLIC_KEY": "pk-test",
        "LANGFUSE__SECRET_KEY": "sk-test",
        "LLM__API_URL": "http://llm-test:8000",
        "TELEGRAM__TOKEN": "123456:TEST-TOKEN",
        "TRANSCRIBER__API_URL": "http://asr-test:8000/v1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_registry_provider_resolves_by_protocol(settings_env: None) -> None:
    """`container.get(JobRegistry)` возвращает InMemoryJobRegistry (не NoFactoryError)."""
    container = make_async_container(RegistryProvider())
    async with container() as request_container:
        registry = await request_container.get(JobRegistry)
        assert isinstance(registry, InMemoryJobRegistry)
    await container.close()


async def test_downloader_service_resolves(settings_env: None) -> None:
    container = make_async_container(
        SettingsProvider(),
        FakeBusProvider(),
        RegistryProvider(),
        ObservabilityProvider(),
        DownloaderAdaptersProvider(),
        DownloaderServiceProvider(),
    )
    async with container() as request_container:
        service = await request_container.get(DownloaderService)
        assert isinstance(service, DownloaderService)
    await container.close()


async def test_transcriber_service_resolves(settings_env: None) -> None:
    container = make_async_container(
        SettingsProvider(),
        FakeBusProvider(),
        RegistryProvider(),
        ObservabilityProvider(),
        TranscriberAdaptersProvider(),
        TranscribeServiceProvider(),
    )
    async with container() as request_container:
        service = await request_container.get(TranscribeService)
        assert isinstance(service, TranscribeService)
    await container.close()


async def test_analyzer_service_resolves(settings_env: None) -> None:
    container = make_async_container(
        SettingsProvider(),
        FakeBusProvider(),
        RegistryProvider(),
        ObservabilityProvider(),
        AnalyzerAdaptersProvider(),
        AnalyzeServiceProvider(),
    )
    async with container() as request_container:
        service = await request_container.get(AnalyzeService)
        assert isinstance(service, AnalyzeService)
    await container.close()


async def test_bot_request_container_resolves_job_registry(
    settings_env: None,
) -> None:
    """RequestContainer бота разрешает JobRegistry — как FromDishka в handle_link."""
    container = make_async_container(
        SettingsProvider(),
        FakeBusProvider(),
        RegistryProvider(),
        ObservabilityProvider(),
        BotHandlersProvider(),
        BotConsumersProvider(),
    )
    async with container() as request_container:
        registry = await request_container.get(JobRegistry)
        assert isinstance(registry, InMemoryJobRegistry)
        task_id = UUID(int=1)
        await registry.create(task_id, "https://example.com/watch?v=test")
        assert await registry.exists(task_id) is True
    await container.close()
