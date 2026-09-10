"""Analyzer service domain: prompt + summarizer ports, summary model."""

from typing import Protocol

from pydantic import BaseModel, Field


class Fact(BaseModel):
    """A single fact from the lecture summary with time in the video (sec)."""

    text: str
    start_sec: float


class Section(BaseModel):
    """A section of the summary holding several facts."""

    heading: str = ""
    # T-2.4: pydantic Field вместо dataclasses.field — единый механизм
    # дефолтов в BaseModel.
    facts: list[Fact] = Field(default_factory=list)


class Summary(BaseModel):
    """Structured lecture summary (targets summary.json)."""

    # T-2.4: заголовок видео (из video.downloaded) — попадает в summary.json
    # и рендерится в сообщении бота; пустой — шаблон подставит фолбэк.
    title: str = ""
    summary: str = ""
    sections: list[Section] = Field(default_factory=list)


class SummarizeError(Exception):
    """Summarization failure (transcript over size limit, LLM call failed)."""


class PromptProvider(Protocol):
    """Fetch the analysis prompt (Langfuse or local fallback)."""

    def get(self, name: str) -> str: ...


class Summarizer(Protocol):
    """Turn a transcript into a structured Summary via an LLM."""

    async def summarize(self, transcript: str, prompt: str) -> Summary: ...
