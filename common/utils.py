"""GoodbyeWindows — Shared utilities."""

import shutil
from pathlib import Path

from .i18n import tr


def copy_directory(src: Path, dst: Path, progress_callback=None) -> int:
    """Copy a directory tree with progress reporting.

    Args:
        src: Source directory.
        dst: Destination directory (created if needed).
        progress_callback: Optional callable(current_file: str, bytes_copied: int, total_bytes: int)

    Returns:
        Total bytes copied.
    """
    if not src.is_dir():
        return 0

    total = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
    copied = 0

    dst.mkdir(parents=True, exist_ok=True)

    for src_file in src.rglob("*"):
        rel = src_file.relative_to(src)
        dst_file = dst / rel

        if src_file.is_dir():
            dst_file.mkdir(parents=True, exist_ok=True)
        elif src_file.is_file():
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            copied += src_file.stat().st_size
            if progress_callback:
                progress_callback(str(rel), copied, total)

    return copied


def safe_name(name: str) -> str:
    """Sanitize a name for use as a directory name."""
    forbidden = '<>:"/\\|?*'
    for ch in forbidden:
        name = name.replace(ch, "_")
    return name.strip(". ")
