"""Dishka providers for the transcriber service."""

from collections.abc import AsyncIterable

from dishka import Provider, Scope, provide
from swot_contracts import MessageBus, Settings
from swot_contracts.ports import JobRegistry

from .asr import FakeTranscriber, OpenaiAsrTranscriber
from .domain import AudioExtractor, Segmenter, Transcriber
from .ffmpeg import FfmpegAudioExtractor, FfmpegSegmenter
from .service import TranscribeService


class TranscriberAdaptersProvider(Provider):
    """Real adapters: ffmpeg extractor + OpenAI-compatible ASR endpoint."""

    @provide(scope=Scope.APP)
    def extractor(self) -> AudioExtractor:
        return FfmpegAudioExtractor()

    @provide(scope=Scope.APP)
    def segmenter(self) -> Segmenter:
        return FfmpegSegmenter()

    @provide(scope=Scope.APP)
    def transcriber(self, settings: Settings, segmenter: Segmenter) -> Transcriber:
        return OpenaiAsrTranscriber(
            base_url=settings.transcriber.api_url,
            api_key=settings.transcriber.api_key,
            model=settings.transcriber.model,
            language=settings.transcriber.language,
            segmenter=segmenter,
            segment_duration_sec=settings.transcriber.segment_duration_sec,
            max_audio_mb=settings.transcriber.max_audio_mb,
            timeout_sec=settings.transcriber.asr_timeout_sec,
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
            media_dir=settings.media_dir,
        )


class FakeTranscriberProvider(Provider):
    """Test provider: fake transcriber (no ASR endpoint), real ffmpeg extractor."""

    @provide(scope=Scope.APP)
    def transcriber(self) -> Transcriber:
        return FakeTranscriber()
