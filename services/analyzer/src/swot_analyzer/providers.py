"""Dishka providers for the analyzer service."""

from collections.abc import AsyncIterable

from dishka import Provider, Scope, provide
from swot_contracts import MessageBus, Settings
from swot_contracts.ports import JobRegistry

from .domain import PromptProvider, Summarizer
from .prompts import LangfusePromptProvider, LocalPromptProvider
from .service import AnalyzeService
from .summarizer import LlmSummarizer


class AnalyzerAdaptersProvider(Provider):
    """Real adapters: Langfuse prompt + OpenAI-compatible LLM."""

    @provide(scope=Scope.APP)
    def local_prompt(self) -> LocalPromptProvider:
        return LocalPromptProvider()

    @provide(scope=Scope.APP)
    def prompt_provider(self, settings: Settings) -> PromptProvider:
        local_prompt: LocalPromptProvider = self.local_prompt()
        if settings.langfuse.public_key and settings.langfuse.secret_key:
            return LangfusePromptProvider(
                public_key=settings.langfuse.public_key,
                secret_key=settings.langfuse.secret_key,
                base_url=settings.langfuse.base_url,
                fallback=local_prompt,
            )
        return local_prompt

    @provide(scope=Scope.APP)
    def summarizer(self, settings: Settings) -> Summarizer:
        return LlmSummarizer(
            base_url=settings.llm.api_url,
            api_key=settings.llm.api_key,
            model=settings.llm.model_id,
            sampling_parameters=settings.llm.sampling_parameters,
            max_tokens=settings.llm.max_tokens,
            timeout_sec=settings.llm.timeout_sec,
            chunk_chars=settings.llm.chunk_chars,
            max_transcript_chars=settings.llm.max_transcript_chars,
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
            prompt_name=settings.llm.summarization_prompt_name,
            prompt_provider=prompt_provider,
            summarizer=summarizer,
            bus=bus,
            registry=registry,
            artifacts_dir=settings.artifacts_dir,
        )
