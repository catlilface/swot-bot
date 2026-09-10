"""Path containment helpers for untrusted paths arriving in bus messages.

Service-to-service messages carry filesystem paths produced by upstream
services (``base_dir``, ``srt_path``, ``summary_path``, ``media_path``).  A
buggy or malicious producer must not be able to make a service read, write or
delete files outside its own directories.  :func:`resolve_under` is the single
place that enforces that rule.
"""

from pathlib import Path

__all__ = ["resolve_under"]


def resolve_under(base: str | Path, candidate: str | Path) -> Path:
    """Resolve *candidate* and ensure the result stays inside *base*.

    *candidate* may be absolute or relative; relative paths are anchored to
    *base*.  Escapes are rejected and reported as :class:`ValueError`:

    - raw ``..`` path components;
    - absolute paths that resolve outside *base*;
    - symlinks (anywhere on the candidate chain) pointing outside *base*.

    Returns the resolved (normalized, absolute) path on success.  The result
    may be *base* itself or any path strictly inside it.
    """
    base_resolved = Path(base).resolve()
    cand = Path(candidate)
    if ".." in cand.parts:
        raise ValueError(f"refusing path with '..' component: {cand}")
    raw = cand if cand.is_absolute() else base_resolved / cand
    try:
        resolved = raw.resolve()
    except (OSError, RuntimeError) as exc:  # pragma: no cover - rare platform issue
        raise ValueError(f"unresolvable path: {cand}") from exc
    if not _is_within(base_resolved, resolved):
        raise ValueError(f"path escapes base: {cand!r} is outside {base_resolved}")
    return resolved


def _is_within(base: Path, candidate: Path) -> bool:
    """True if *candidate* equals *base* or is located below it.

    Walks ``candidate.parents`` instead of ``Path.ancestors`` because the
    latter is not available in every Python build we run on.
    """
    if candidate == base:
        return True
    return any(parent == base for parent in candidate.parents)
