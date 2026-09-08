"""LLM-based Summarizer on OpenAI-compatible endpoint (langchain)."""

import json

from langchain_openai import ChatOpenAI

from .domain import Fact, Section, Summary


class LlmSummarizer:
    """Call an OpenAI-compatible chat model to produce a structured Summary."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        temperature: float = 0.3,
    ) -> None:
        self._client = ChatOpenAI(
            base_url=base_url,
            api_key=api_key or "none",
            model=model,
            temperature=temperature,
        )

    async def summarize(self, transcript: str, prompt: str) -> Summary:
        user_msg = f"Транскрипт:\n{transcript[:12000]}"
        resp = await self._client.ainvoke(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_msg},
            ]
        )
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        return _parse_summary(content)


def _parse_summary(content: str) -> Summary:
    text = content.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    data = json.loads(text)
    sections = [
        Section(
            heading=sec.get("heading", ""),
            facts=[
                Fact(
                    text=f.get("text", ""),
                    start_sec=float(f.get("start_sec", 0)),
                    end_sec=float(f.get("end_sec", 0)),
                )
                for f in sec.get("facts", [])
            ],
        )
        for sec in data.get("sections", [])
    ]
    return Summary(
        title=data.get("title", ""),
        summary=data.get("summary", ""),
        sections=sections,
    )


class StubSummarizer:
    """Deterministic summarizer for tests (no LLM)."""

    async def summarize(self, transcript: str, prompt: str) -> Summary:
        return Summary(
            title="Тест",
            summary="Краткое резюме.",
            sections=[Section(heading="Раздел", facts=[Fact("Факт", 10, 20)])],
        )
