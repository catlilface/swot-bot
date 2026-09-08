"""Analyzer application service: transcript.ready -> summary.json -> analysis.ready."""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from swot_contracts import (
    AnalysisFailed,
    AnalysisReady,
    JobStatus,
    MessageBus,
    TranscriptReady,
)
from swot_contracts.ports import JobRegistry

if TYPE_CHECKING:
    from .domain import PromptProvider, Summarizer

logger = logging.getLogger(__name__)


class AnalyzeService:
    """Handle transcript.ready end-to-end."""

    def __init__(
        self,
        prompt_name: str,
        prompt_provider: "PromptProvider",
        summarizer: "Summarizer",
        bus: MessageBus,
        registry: JobRegistry,
    ) -> None:
        self._prompt_name = prompt_name
        self._prompt_provider = prompt_provider
        self._summarizer = summarizer
        self._bus = bus
        self._registry = registry

    async def handle(self, message: TranscriptReady) -> None:
        await self._registry.set_status(message.task_id, JobStatus.ANALYZING)
        base_dir = Path(message.base_dir)
        try:
            transcript = self._read_transcript(Path(message.srt_path))
            prompt = self._prompt_provider.get(self._prompt_name)
            summary = await self._summarizer.summarize(transcript, prompt)

            summary_path = base_dir / "summary.json"
            summary_path.write_text(
                json.dumps(_summary_to_dict(summary), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            await self._bus.publish(
                AnalysisReady(
                    task_id=message.task_id,
                    trace_id=message.trace_id,
                    source=message.source,
                    base_dir=str(base_dir),
                    summary_path=str(summary_path),
                    title=summary.title,
                )
            )
            await self._registry.set_status(message.task_id, JobStatus.READY)
        except Exception as exc:  # noqa: BLE001
            logger.exception("analyze failed", task_id=str(message.task_id))
            await self._bus.publish(
                AnalysisFailed(
                    task_id=message.task_id,
                    trace_id=message.trace_id,
                    source=message.source,
                    error=str(exc),
                )
            )
            await self._registry.set_status(message.task_id, JobStatus.FAILED)

    @staticmethod
    def _read_transcript(srt: Path) -> str:
        lines = srt.read_text(encoding="utf-8").splitlines()
        text_lines = [
            ln
            for ln in lines
            if ln.strip() and "-->" not in ln and not ln.strip().isdigit()
        ]
        return "\n".join(text_lines)

    async def run(self) -> None:
        await self._bus.consume(self.handle)


def _summary_to_dict(summary) -> dict:
    return {
        "title": summary.title,
        "summary": summary.summary,
        "sections": [
            {
                "heading": s.heading,
                "facts": [
                    {"text": f.text, "start_sec": f.start_sec, "end_sec": f.end_sec}
                    for f in s.facts
                ],
            }
            for s in summary.sections
        ],
    }
