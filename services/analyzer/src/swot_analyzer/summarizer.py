"""LLM-based Summarizer on OpenAI-compatible endpoint (langchain).

Long transcripts are summarized with map-reduce: the transcript is split
(`langchain-text-splitters`), each chunk is summarized separately (map), and
the partial summaries are merged into one final ``Summary`` (reduce) so that
the total input never exceeds the model's context window.
"""

import json
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .domain import Fact, Section, SummarizeError, Summary
from .prompts import REDUCE_PROMPT


class LlmSummarizer:
    """Call an OpenAI-compatible chat model to produce a structured Summary.

    A transcript longer than one ``chunk_chars`` chunk triggers map-reduce;
    a transcript longer than ``max_transcript_chars`` is rejected up front
    with a clear error instead of being sent to the LLM.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout_sec: int = 120,
        chunk_chars: int = 8000,
        max_transcript_chars: int = 200000,
        llm: Any | None = None,
        chunker: Any | None = None,
    ) -> None:
        if llm is not None:
            self._model = llm
        else:
            self._model = ChatOpenAI(  # type: ignore[call-arg]  # stubs of langchain_openai lag the runtime API (max_tokens/request_timeout)
                base_url=base_url,
                api_key=api_key or "none",  # type: ignore[arg-type]  # runtime accepts str, stubs want SecretStr
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                request_timeout=timeout_sec,
            )
        self._chunker = chunker or RecursiveCharacterTextSplitter(
            chunk_size=chunk_chars, chunk_overlap=0
        )
        self._max_transcript_chars = max_transcript_chars

    async def summarize(self, transcript: str, prompt: str) -> Summary:
        if len(transcript) > self._max_transcript_chars:
            raise SummarizeError(
                f"transcript too large: {len(transcript)} chars "
                f"(limit {self._max_transcript_chars}); refusing to send to the LLM"
            )
        chunks = self._chunker.split_text(transcript)
        if not chunks:
            return Summary()
        if len(chunks) == 1:
            return await self._invoke(prompt, chunks[0])
        # map: per-chunk partial summaries (sequential: no endpoint hammering)
        partials = [await self._invoke(prompt, chunk) for chunk in chunks]
        # reduce: merge partials into one Summary (dedupe facts)
        combined = json.dumps([p.model_dump() for p in partials], ensure_ascii=False)
        return await self._invoke(REDUCE_PROMPT, combined)

    async def _invoke(self, prompt: str, text: str) -> Summary:
        template = (
            ChatPromptTemplate.from_template(prompt)
            if isinstance(prompt, str)
            else prompt
        )
        structured_llm = self._model.with_structured_output(Summary)
        chain = template | structured_llm
        result = await chain.ainvoke({"input": text})
        return Summary.model_validate(result)


class StubSummarizer:
    """Deterministic summarizer for tests (no LLM)."""

    async def summarize(self, transcript: str, prompt: str) -> Summary:
        return Summary(
            summary="Краткое резюме.",
            sections=[
                Section(heading="Раздел", facts=[Fact(text="Факт", start_sec=10)])
            ],
        )
