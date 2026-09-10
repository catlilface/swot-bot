"""Shared URL validation: bot and downloader check links the same way (T-2.5)."""

from urllib.parse import urlparse

#: Default ports per scheme — "host:80"/"host:443" is the same endpoint as "host".
_DEFAULT_PORTS = {"http": 80, "https": 443}


class UrlValidator:
    """Accept any http(s) URL; optional host allowlist (comma-separated).

    Netloc normalization: the scheme's default port is ignored
    (``https://youtube.com:443`` == ``https://youtube.com``), subdomains match
    by domain suffix (``m.youtube.com`` passes for ``youtube.com``), but the
    suffix boundary and non-default ports are respected (``youtu.be``,
    ``notyoutube.com`` and ``youtube.com:8443`` do not).
    """

    def __init__(self, allowed_hosts: str = "") -> None:
        self._allowed = {
            self._normalize_host(h.strip())
            for h in allowed_hosts.split(",")
            if h.strip()
        }

    @staticmethod
    def _normalize_host(value: str) -> str:
        """Lowercase, strip IPv6 brackets and trailing dot; drop default port."""
        host = value.strip().lower().strip("[]").rstrip(".")
        name, sep, port = host.partition(":")
        if sep and port in ("80", "443"):
            host = name
        return host

    def validate(self, url: str) -> str:
        """Return the url if allowed, otherwise raise ValueError."""
        if len(url) > 2048:
            raise ValueError("URL слишком длинный")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Поддерживаются только http/https ссылки")
        host = self._normalize_host(parsed.hostname or "")
        if not host:
            raise ValueError("Невалидная ссылка")
        # A non-default port is a different endpoint: only an allowlist entry
        # carrying the same explicit port matches it.
        port = parsed.port
        if port is not None and port != _DEFAULT_PORTS.get(parsed.scheme):
            host = f"{host}:{port}"
        if self._allowed and not self._host_allowed(host):
            raise ValueError("Домен не в списке разрешённых")
        return url

    def _host_allowed(self, host: str) -> bool:
        for entry in self._allowed:
            if host == entry or host.endswith("." + entry):
                return True
        return False
