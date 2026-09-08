"""Renders a summary.json into a Telegram message via a Jinja2 template."""

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

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
        )
        self._env.filters["chrono"] = _fmt_sec

    def render(self, summary_path: Path) -> str:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        template = self._env.get_template("result_message.html.j2")
        return template.render(summary=data)
