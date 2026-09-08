"""Dishka providers for the transcriber service."""

from collections.abc import AsyncIterable

from dishka import Provider, Scope, provide
from swot_contracts import MessageBus, Settings
from swot_contracts.ports import JobRegistry

from .domain import AudioExtractor, Transcriber
from .ffmpeg import FfmpegAudioExtractor
from .service import TranscribeService
from .whisper import FakeTranscriber, FasterWhisperTranscriber


class TranscriberAdaptersProvider(Provider):
    """Real adapters: ffmpeg extractor + faster-whisper (GPU)."""

    @provide(scope=Scope.APP)
    def extractor(self) -> AudioExtractor:
        return FfmpegAudioExtractor()

    @provide(scope=Scope.APP)
    def transcriber(self, settings: Settings) -> Transcriber:
        return FasterWhisperTranscriber(
            model_size=settings.transcriber.model,
            device=settings.transcriber.device,
            compute_type=settings.transcriber.compute_type,
            language=settings.transcriber.language,
            batch_size=settings.transcriber.batch_size,
            models_dir=settings.transcriber.models_dir,
        )


class TranscribeServiceProvider(Provider):
    """Provide the transcribe application service."""

    @provide(scope=Scope.APP)
    async def service(
        self,
        settings: Settings,
        extractor: AudioExtractor,
        transcriber: Transcriber,
        bus: MessageBus,
        registry: JobRegistry,
    ) -> AsyncIterable[TranscribeService]:
        yield TranscribeService(
            artifacts_dir=settings.artifacts_dir,
            extractor=extractor,
            transcriber=transcriber,
            bus=bus,
            registry=registry,
        )


class FakeTranscriberProvider(Provider):
    """Test provider: fake transcriber (no GPU/model), real ffmpeg extractor."""

    @provide(scope=Scope.APP)
    def transcriber(self) -> Transcriber:
        return FakeTranscriber()
