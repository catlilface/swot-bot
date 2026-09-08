"""Analyzer service domain: prompt + summarizer ports, summary model."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Fact:
    """A single fact from the lecture summary with time in the video (sec)."""

    text: str
    start_sec: float
    end_sec: float = 0.0


@dataclass
class Section:
    """A section of the summary holding several facts."""

    heading: str = ""
    facts: list[Fact] = field(default_factory=list)


@dataclass
class Summary:
    """Structured lecture summary (targets summary.json)."""

    title: str = ""
    summary: str = ""
    sections: list[Section] = field(default_factory=list)


class PromptProvider(Protocol):
    """Fetch the analysis prompt (Langfuse or local fallback)."""

    def get(self, name: str) -> str: ...


class Summarizer(Protocol):
    """Turn a transcript into a structured Summary via an LLM."""

    async def summarize(self, transcript: str, prompt: str) -> Summary: ...
