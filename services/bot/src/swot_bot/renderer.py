"""Renders a summary.json into a Telegram message via a Jinja2 template."""

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, Template, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _fmt_sec(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class MessageRenderer:
    """Render summary.json -> text using result_message.html.j2."""

    def __init__(self, template_dir: Path = TEMPLATE_DIR) -> None:
        self._env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml", "j2"]),
            # T-2.4: шаблоны — код пакета, а не пользовательские данные:
            # компилируем один раз и не пересобираем на каждое сообщение.
            auto_reload=False,
        )
        self._env.filters["chrono"] = _fmt_sec
        # T-2.4: кэш скомпилированных шаблонов по имени (без повторной
        # компиляции на каждое сообщение).
        self._templates: dict[str, Template] = {}

    def _template(self, name: str) -> Template:
        """Return the compiled template, compiling it at most once."""
        template = self._templates.get(name)
        if template is None:
            template = self._env.get_template(name)
            self._templates[name] = template
        return template

    def render(self, summary_path: Path) -> str:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        return self._template("result_message.html.j2").render(summary=data)
