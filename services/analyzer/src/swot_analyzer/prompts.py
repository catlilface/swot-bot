"""Prompt providers: Langfuse (self-hosted) with local fallback."""

from typing import Any

import langfuse

LOCAL_PROMPT = """Ты — аналитик лекций. Ниже дан транскрипт лекции:

{input}

Составь структурированную выжимку в формате JSON:

{{
  "summary": "Краткое резюме лекции",
  "sections": [
    {{"heading": "Тема раздела",
      "facts": [{{"text": "утверждение", "start_sec": 123}}]}}
  ]
}}

Каждый факт сопровождается временем start_sec в видео (секунды), когда
это было сказано. Ответь ТОЛЬКО JSON без пояснений."""

REDUCE_PROMPT = """Ты — аналитик лекций. Ниже в JSON-массиве даны частичные выжимки
фрагментов ОДНОЙ И ТОЙ ЖЕ лекции (выжимки фрагментов, а не полный транскрипт):

{input}

Склей их в единую выжимку в том же формате: объедини общее "summary",
сгруппируй разделы по темам и устрани дубликаты фактов — повторяющееся
утверждение должно остаться ОДНО (с минимальным start_sec среди дублей).
Ответь ТОЛЬКО JSON без пояснений."""


class LocalPromptProvider:
    """Fixed local prompt (used as fallback / for tests)."""

    def __init__(self, prompt: str = LOCAL_PROMPT) -> None:
        self._prompt = prompt

    def get(self, name: str) -> str:
        return self._prompt


def _messages_to_text(messages: Any) -> str:
    """Chat-сообщения Langfuse → один текстовый шаблон (с {плейсхолдерами})."""
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, dict):
            msg_type = msg.get("type", "message")
            name = msg.get("name")
            content = msg.get("content")
            role = msg.get("role")
        else:
            msg_type = getattr(msg, "type", "message")
            name = getattr(msg, "name", None)
            content = getattr(msg, "content", None)
            role = getattr(msg, "role", None)
        if msg_type == "placeholder" and name:
            parts.append("{" + str(name) + "}")
        elif content:
            prefix = f"{role}: " if role and role != "user" else ""
            parts.append(prefix + str(content))
    return "\n\n".join(part for part in parts if part)


def _prompt_to_text(prompt_obj: Any) -> str | None:
    """Промпт Langfuse 4.x → str: text — как есть, chat — сообщения склеиваются."""
    if isinstance(prompt_obj, str):
        return prompt_obj
    langchain_prompt = prompt_obj.get_langchain_prompt()
    if isinstance(langchain_prompt, str):
        return langchain_prompt
    raw = getattr(prompt_obj, "prompt", None)
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (list, tuple)):
        return _messages_to_text(raw)
    return None


class LangfusePromptProvider:
    """Fetch the prompt from Langfuse (label=production).

    ``get()`` гарантированно возвращает ``str``: недоступный host или
    отсутствующий промпт → fallback (``LocalPromptProvider``).
    """

    def __init__(
        self,
        public_key: str,
        secret_key: str,
        base_url: str,
        fallback: LocalPromptProvider,
        fetch_timeout_seconds: float = 10.0,
    ) -> None:
        self._client = langfuse.Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=base_url,
            flush_interval=60.0,
        )
        self._fallback = fallback
        self._fetch_timeout_seconds = fetch_timeout_seconds

    def get(self, name: str) -> str:
        try:
            prompt = self._client.get_prompt(
                name,
                label="production",
                fallback=self._fallback.get(name),
                fetch_timeout_seconds=int(self._fetch_timeout_seconds),
            )
            text = _prompt_to_text(prompt)
            return text if text else self._fallback.get(name)
        except Exception:  # noqa: BLE001 — сеть/конфиг не должны ронять анализ
            return self._fallback.get(name)

    def close(self) -> None:
        """Flush pending events and stop the Langfuse client (graceful shutdown)."""
        self._client.shutdown()
