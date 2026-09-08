"""Dishka providers for the analyzer service."""

from collections.abc import AsyncIterable

from dishka import Provider, Scope, provide
from swot_contracts import MessageBus, Settings
from swot_contracts.ports import JobRegistry

from .domain import PromptProvider, Summarizer
from .prompts import LangfusePromptProvider, LocalPromptProvider
from .service import AnalyzeService
from .summarizer import LlmSummarizer, StubSummarizer


class AnalyzerAdaptersProvider(Provider):
    """Real adapters: Langfuse prompt + OpenAI-compatible LLM."""

    @provide(scope=Scope.APP)
    def local_prompt(self) -> LocalPromptProvider:
        return LocalPromptProvider()

    @provide(scope=Scope.APP)
    def prompt_provider(self, settings: Settings) -> PromptProvider:
        if settings.langfuse_public_key and settings.langfuse_secret_key:
            return LangfusePromptProvider(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                base_url=settings.langfuse_base_url,
                fallback=self.local_prompt(),
            )
        return self.local_prompt()

    @provide(scope=Scope.APP)
    def summarizer(self, settings: Settings) -> Summarizer:
        return LlmSummarizer(
            base_url=settings.openai_compatible_api_url,
            api_key=settings.openai_compatible_api_key,
            model=settings.llm_model_id,
            temperature=settings.llm_temperature,
        )


class AnalyzeServiceProvider(Provider):
    """Provide the analyze application service."""

    @provide(scope=Scope.APP)
    async def service(
        self,
        settings: Settings,
        prompt_provider: PromptProvider,
        summarizer: Summarizer,
        bus: MessageBus,
        registry: JobRegistry,
    ) -> AsyncIterable[AnalyzeService]:
        yield AnalyzeService(
            prompt_name=settings.prompt_name,
            prompt_provider=prompt_provider,
            summarizer=summarizer,
            bus=bus,
            registry=registry,
        )


class StubAnalyzerProvider(Provider):
    """Test provider: stub summarizer + local prompt (no LLM/network)."""

    @provide(scope=Scope.APP)
    def summarizer(self) -> Summarizer:
        return StubSummarizer()

    @provide(scope=Scope.APP)
    def prompt_provider(self) -> PromptProvider:
        return LocalPromptProvider()
