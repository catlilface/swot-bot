"""TTL-based cleanup for shared-volume directories (P1-11).

One utility for both ``media_dir`` and ``artifacts_dir``: task directories
older than the retention window are removed so the shared volume does not
grow unbounded.
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def purge_stale_dirs(root: Path, cutoff: float) -> int:
    """Delete direct subdirectories of ``root`` not modified since ``cutoff``.

    ``cutoff`` is a unix timestamp (``time.time()``). Files (not directories)
    directly under the root are left untouched. Returns the number of
    directories removed. Best-effort: I/O errors are logged, not raised.
    """
    if not root.is_dir():
        return 0
    removed = 0
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
            logger.info("cleaned stale task dir: %s", child)
        except OSError as exc:
            logger.warning("cleanup failed: path=%s error=%s", child, exc)
    return removed
