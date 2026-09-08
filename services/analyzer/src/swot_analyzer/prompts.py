"""Prompt providers: Langfuse (self-hosted) with local fallback."""

LOCAL_PROMPT = """Ты — аналитик лекций. На вход дан транскрипт лекции.
Составь структурированную выжимку в формате JSON:

{
  "title": "Название",
  "summary": "Краткое резюме лекции",
  "sections": [
    {"heading": "Тема раздела",
     "facts": [{"text": "утверждение", "start_sec": 123, "end_sec": 141}]}
  ]
}

Каждый факт сопровождается временем start_sec/end_sec в видео (секунды), когда
это было сказано. Ответь ТОЛЬКО JSON без пояснений."""


class LocalPromptProvider:
    """Fixed local prompt (used as fallback / for tests)."""

    def __init__(self, prompt: str = LOCAL_PROMPT) -> None:
        self._prompt = prompt

    def get(self, name: str) -> str:
        return self._prompt


class LangfusePromptProvider:
    """Fetch the prompt from self-hosted Langfuse (label=production)."""

    def __init__(
        self,
        public_key: str,
        secret_key: str,
        base_url: str,
        fallback: LocalPromptProvider,
    ) -> None:
        import langfuse

        langfuse.Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=base_url,
        )
        self._fallback = fallback

    def get(self, name: str) -> str:
        import langfuse

        client = langfuse.get_client()
        try:
            prompt = client.get_prompt(name, label="production", fallback=self._fallback.get(name))
            return prompt.get_langchain_prompt() or prompt.prompt
        except Exception:  # noqa: BLE001
            return self._fallback.get(name)
