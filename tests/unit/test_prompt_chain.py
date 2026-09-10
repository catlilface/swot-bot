"""T-0.4 (P0-4): prompt chain — LOCAL_PROMPT, LlmSummarizer, Langfuse fallback."""

import pytest
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from swot_analyzer import summarizer as summarizer_mod
from swot_analyzer.prompts import (
    LOCAL_PROMPT,
    LangfusePromptProvider,
    LocalPromptProvider,
)
from swot_analyzer.summarizer import LlmSummarizer

TRANSCRIPT = "транскрипт лекции: свот-анализ"


class FakeChatModel:
    """Fake ChatOpenAI: with_structured_output → Runnable, записывающий вход."""

    def __init__(self) -> None:
        self.seen_text: str | None = None

    def with_structured_output(self, schema) -> RunnableLambda:
        async def fake_llm(prompt_value) -> dict:
            self.seen_text = prompt_value.to_string()
            return {
                "summary": "Резюме лекции",
                "sections": [
                    {
                        "heading": "Раздел",
                        "facts": [{"text": "Факт", "start_sec": 42.0}],
                    }
                ],
            }

        return RunnableLambda(fake_llm)


def test_local_prompt_renders_as_chat_template() -> None:
    """Acceptance: from_template(LOCAL_PROMPT) doesn't raise (previously ValueError)."""
    template = ChatPromptTemplate.from_template(LOCAL_PROMPT)
    rendered = template.format(input=TRANSCRIPT)
    assert TRANSCRIPT in rendered  # {input} variable is in the template
    # The JSON example is intact (braces unescaped after rendering)
    assert '"summary": "Краткое резюме лекции"' in rendered
    assert '"start_sec": 123' in rendered
    assert "{{" not in rendered


async def test_summarizer_prompt_contains_transcript_and_parses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeChatModel()
    monkeypatch.setattr(summarizer_mod, "ChatOpenAI", lambda **_kw: fake)
    svc = LlmSummarizer(
        base_url="http://fake.local/v1", api_key=None, model="fake-model"
    )
    result = await svc.summarize(TRANSCRIPT, LOCAL_PROMPT)
    # Response is parsed into Summary
    assert result.summary == "Резюме лекции"
    assert result.sections[0].facts[0].start_sec == 42.0
    # The actual prompt the LLM receives
    seen = fake.seen_text
    assert seen is not None
    assert TRANSCRIPT in seen  # the transcript is not silently discarded
    assert '"sections"' in seen  # the JSON example is intact
    assert "{{" not in seen


def test_langfuse_provider_fallback_on_unreachable_host() -> None:
    """Unreachable Langfuse → LOCAL_PROMPT as str (not ChatPromptTemplate)."""
    provider = LangfusePromptProvider(
        public_key="pk-test",
        secret_key="sk-test",
        base_url="http://127.0.0.1:9",  # port 9/discard: connection refused
        fallback=LocalPromptProvider(),
        fetch_timeout_seconds=1.0,
    )
    try:
        prompt = provider.get("lecture-summary")
    finally:
        provider.close()
    assert isinstance(prompt, str)
    assert not isinstance(prompt, ChatPromptTemplate)
    assert prompt == LOCAL_PROMPT
