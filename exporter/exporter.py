"""GoodbyeWindows — Export logic.

Handles three export modes:
1. Metadata only (.gbw file)
2. Full export (metadata + mod files to folder)
3. Network transfer (handled by server.py)
"""

import shutil
from pathlib import Path
from typing import Callable

from common.mo2_reader import MO2Instance
from common.migration_format import (
    MigrationPackage,
    create_package_from_mo2,
    save_gbw,
)


ProgressCallback = Callable[[str, int, int], None]
# callback(status_text, current_bytes, total_bytes)


def export_metadata(instance: MO2Instance, output_path: Path) -> Path:
    """Export metadata only as a .gbw file.

    Args:
        instance: The MO2 instance to export.
        output_path: Where to save the .gbw file.

    Returns:
        Path to the created .gbw file.
    """
    package = create_package_from_mo2(instance)
    package.manifest.has_mod_files = False
    return save_gbw(package, output_path)


def export_full(
    instance: MO2Instance,
    output_dir: Path,
    progress: ProgressCallback | None = None,
) -> Path:
    """Export metadata + all mod files to a folder.

    Creates:
        output_dir/
        ├── migration.gbw
        └── mods/
            ├── ModName1/
            ├── ModName2/
            └── ...

    Args:
        instance: The MO2 instance to export.
        output_dir: Target directory (e.g., USB drive).
        progress: Optional progress callback.

    Returns:
        Path to the output directory.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save metadata
    package = create_package_from_mo2(instance)
    package.manifest.has_mod_files = True
    save_gbw(package, output_dir / "migration.gbw")

    # Copy mod files
    mods_target = output_dir / "mods"
    mods_target.mkdir(exist_ok=True)

    total_bytes = sum(m.size_bytes for m in package.mods if not m.is_separator)
    copied_bytes = 0

    for mod in package.mods:
        if mod.is_separator:
            continue

        src_dir = instance.mods_dir / mod.folder_name
        if not src_dir.exists():
            continue

        dst_dir = mods_target / mod.folder_name
        if dst_dir.exists():
            shutil.rmtree(dst_dir)

        # Copy with progress
        for src_file in src_dir.rglob("*"):
            rel = src_file.relative_to(src_dir)
            dst_file = dst_dir / rel

            if src_file.is_dir():
                dst_file.mkdir(parents=True, exist_ok=True)
            elif src_file.is_file():
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                file_size = src_file.stat().st_size
                copied_bytes += file_size
                if progress:
                    progress(
                        f"{mod.display_name}/{rel}",
                        copied_bytes,
                        total_bytes,
                    )

    return output_dir


def get_export_summary(instance: MO2Instance) -> dict:
    """Get a summary of what would be exported.

    Returns dict with counts and sizes for the preview screen.
    """
    mods = list(instance.mod_meta.values())

    total_mods = len([m for m in mods if not m.is_separator])
    separators = len([m for m in mods if m.is_separator])
    with_nexus = len([m for m in mods if m.has_nexus_id and not m.is_separator])
    without_nexus = total_mods - with_nexus
    total_size = sum(m.size_bytes for m in mods if not m.is_separator)

    return {
        "total_mods": total_mods,
        "separators": separators,
        "with_nexus_id": with_nexus,
        "without_nexus_id": without_nexus,
        "total_size_bytes": total_size,
        "game_name": instance.game_name,
        "profiles": [p.name for p in instance.profiles],
    }
