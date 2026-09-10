"""LLM-based Summarizer on OpenAI-compatible endpoint (langchain)."""

from langchain_core.prompts import ChatPromptTemplate
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
        self._model = ChatOpenAI(
            base_url=base_url,
            api_key=api_key or "none",
            model=model,
            temperature=temperature,
        )

    async def summarize(self, transcript: str, prompt: str) -> Summary:
        template = (
            ChatPromptTemplate.from_template(prompt)
            if isinstance(prompt, str)
            else prompt
        )
        structured_llm = self._model.with_structured_output(Summary)
        chain = template | structured_llm
        result = await chain.ainvoke({"input": transcript})
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
