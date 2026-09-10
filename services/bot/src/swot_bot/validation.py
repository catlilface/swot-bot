"""URL validation for bot commands.

T-2.5: the validator itself lives in ``swot_contracts.urls`` — the downloader
re-validates submitted sources with the very same class (defense in depth).
This module re-exports it, keeping ``swot_bot.validation`` import-compatible.
"""

from swot_contracts.urls import UrlValidator

__all__ = ["UrlValidator"]
