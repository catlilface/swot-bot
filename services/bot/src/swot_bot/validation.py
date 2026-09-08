"""URL validation for submitted links (any source allowed by default)."""

from urllib.parse import urlparse


class UrlValidator:
    """Accept any http(s) URL; optional host allowlist from env."""

    def __init__(self, allowed_hosts: str = "") -> None:
        self._allowed = {h.strip().lower() for h in allowed_hosts.split(",") if h.strip()}

    def validate(self, url: str) -> str:
        if len(url) > 2048:
            raise ValueError("URL слишком длинный")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Поддерживаются только http/https ссылки")
        if not parsed.netloc:
            raise ValueError("Невалидная ссылка")
        if self._allowed and parsed.netloc.lower() not in self._allowed:
            raise ValueError("Домен не в списке разрешённых")
        return url
