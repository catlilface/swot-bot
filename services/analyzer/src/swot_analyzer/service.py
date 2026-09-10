"""Analyzer application service: transcript.ready -> summary.json -> analysis.ready."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from swot_contracts import (
    AnalysisReady,
    BaseMessage,
    JobFailed,
    JobProgress,
    JobStatus,
    MessageBus,
    TranscriptReady,
    resolve_under,
)
from swot_contracts.ports import JobRegistry

if TYPE_CHECKING:
    from .domain import PromptProvider, Summarizer, Summary

logger = logging.getLogger(__name__)


class AnalyzeService:
    """Handle transcript.ready end-to-end."""

    def __init__(
        self,
        prompt_name: str,
        prompt_provider: PromptProvider,
        summarizer: Summarizer,
        bus: MessageBus,
        registry: JobRegistry,
        artifacts_dir: str,
    ) -> None:
        self._prompt_name = prompt_name
        self._prompt_provider = prompt_provider
        self._summarizer = summarizer
        self._bus = bus
        self._registry = registry
        self._artifacts_dir = Path(artifacts_dir)

    async def handle(self, message: TranscriptReady) -> None:
        await self._registry.set_status(message.task_id, JobStatus.ANALYZING)
        await self._bus.publish(
            JobProgress(
                task_id=message.task_id, trace_id=message.trace_id, stage="analyzing"
            )
        )
        try:
            # Path containment: upstream-supplied paths must stay inside the
            # analyzer's own artifacts dir (P0-6).
            base_dir = resolve_under(self._artifacts_dir, message.base_dir)
            srt_path = resolve_under(base_dir, message.srt_path)
            transcript = self._read_transcript(srt_path)
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
                )
            )
            await self._registry.set_status(message.task_id, JobStatus.READY)
            logger.info(
                "summary ready: task_id=%s trace_id=%s path=%s",
                message.task_id,
                message.trace_id,
                summary_path,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "analyze failed: task_id=%s trace_id=%s",
                message.task_id,
                message.trace_id,
            )
            await self._registry.set_status(message.task_id, JobStatus.FAILED)
            await self._bus.publish(
                JobFailed(
                    task_id=message.task_id,
                    trace_id=message.trace_id,
                    stage="analyze",
                    error=str(exc),
                )
            )

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
        async def dispatch(message: BaseMessage) -> None:
            if isinstance(message, TranscriptReady):
                await self.handle(message)

        await self._bus.consume(dispatch)


def _summary_to_dict(summary: Summary) -> dict[str, Any]:
    return {
        "summary": summary.summary,
        "sections": [
            {
                "heading": s.heading,
                "facts": [{"text": f.text, "start_sec": f.start_sec} for f in s.facts],
            }
            for s in summary.sections
        ],
    }
