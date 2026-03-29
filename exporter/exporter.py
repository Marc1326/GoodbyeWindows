"""GoodbyeWindows — Export logic.

Handles export of MO2 and/or Vortex mod setups:
1. Metadata only (.gbw files — small, mods re-downloaded on Linux)
2. Full export  (.gbw + mod files to folder on USB / external drive)
3. Network transfer (handled by server.py)

Supports multi-game export: each game gets its own subfolder.
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from common.mo2_reader import MO2Instance
from common.migration_format import (
    MigrationPackage,
    create_package_from_mo2,
    create_package_from_vortex,
    save_gbw,
)
from common.utils import safe_name

ProgressCallback = Callable[[str, int, int], None]
# callback(status_text, current_bytes, total_bytes)


# ---------------------------------------------------------------------------
# ExportableGame — unified abstraction for MO2 or Vortex games
# ---------------------------------------------------------------------------

@dataclass
class ExportableGame:
    """A game ready for export, regardless of source manager."""

    game_name: str
    source: str               # "MO2" or "Vortex"
    nexus_slug: str
    mod_count: int
    separator_count: int
    with_nexus_id: int
    without_nexus_id: int
    total_size_bytes: int
    profiles: list[str] = field(default_factory=list)
    selected: bool = True     # UI: checkbox state

    # Internal references (one will be set)
    _mo2_instance: MO2Instance | None = field(default=None, repr=False)
    _vortex_game: object | None = field(default=None, repr=False)

    def create_package(self) -> MigrationPackage:
        """Create a MigrationPackage from this game's data."""
        if self._mo2_instance is not None:
            return create_package_from_mo2(self._mo2_instance)
        return create_package_from_vortex(self._vortex_game)

    @property
    def mods_dir(self) -> Path | None:
        """Path to the mod files on disk (staging folder)."""
        if self._mo2_instance is not None:
            return self._mo2_instance.mods_dir
        vg = self._vortex_game
        if vg is not None and vg.staging_folder:
            p = Path(vg.staging_folder)
            return p if p.is_dir() else None
        return None

    @property
    def has_mod_files(self) -> bool:
        d = self.mods_dir
        return d is not None and d.is_dir()

    @property
    def safe_folder_name(self) -> str:
        """Folder name for export: 'Game Name [Source]'."""
        return f"{safe_name(self.game_name)} [{self.source}]"


# ---------------------------------------------------------------------------
# Build ExportableGame lists from scanners
# ---------------------------------------------------------------------------

def games_from_mo2(instances: list[MO2Instance]) -> list[ExportableGame]:
    """Convert a list of MO2 instances to ExportableGame objects."""
    from common.mo2_reader import nexus_game_slug

    games: list[ExportableGame] = []
    for inst in instances:
        mods = list(inst.mod_meta.values())
        total = [m for m in mods if not m.is_separator]
        seps = [m for m in mods if m.is_separator]
        with_nid = [m for m in total if m.has_nexus_id]

        games.append(ExportableGame(
            game_name=inst.game_name or "Unknown Game",
            source="MO2",
            nexus_slug=nexus_game_slug(inst.game_name),
            mod_count=len(total),
            separator_count=len(seps),
            with_nexus_id=len(with_nid),
            without_nexus_id=len(total) - len(with_nid),
            total_size_bytes=sum(m.size_bytes for m in total),
            profiles=[p.name for p in inst.profiles],
            _mo2_instance=inst,
        ))
    return games


def games_from_vortex(vortex_instance) -> list[ExportableGame]:
    """Convert a VortexInstance's games to ExportableGame objects."""
    from common.vortex_reader import nexus_slug_from_vortex

    games: list[ExportableGame] = []
    for game in vortex_instance.games.values():
        mods = list(game.mods.values())
        with_nid = [m for m in mods if m.has_nexus_id]

        games.append(ExportableGame(
            game_name=game.name,
            source="Vortex",
            nexus_slug=nexus_slug_from_vortex(game.game_id),
            mod_count=len(mods),
            separator_count=0,  # Vortex has no separators
            with_nexus_id=len(with_nid),
            without_nexus_id=len(mods) - len(with_nid),
            total_size_bytes=game.total_size,
            profiles=[p.name for p in game.profiles],
            _vortex_game=game,
        ))
    return games


# ---------------------------------------------------------------------------
# Multi-game export
# ---------------------------------------------------------------------------

def export_games(
    games: list[ExportableGame],
    output_dir: Path,
    include_mods: bool = False,
    progress: ProgressCallback | None = None,
) -> Path:
    """Export one or more games to an output directory.

    Creates:
        output_dir/
        ├── Game Name [MO2]/
        │   ├── migration.gbw
        │   └── mods/          (only if include_mods)
        ├── Game Name [Vortex]/
        │   ├── migration.gbw
        │   └── mods/
        ...

    Args:
        games: List of ExportableGame to export.
        output_dir: Target directory (USB drive, external HDD, etc.).
        include_mods: If True, copy mod files alongside metadata.
        progress: Optional callback(status, current_bytes, total_bytes).

    Returns:
        Path to the output directory.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve duplicate folder names
    folder_names: dict[int, str] = {}
    name_count: dict[str, int] = {}
    for i, game in enumerate(games):
        base = game.safe_folder_name
        name_count[base] = name_count.get(base, 0) + 1
        if name_count[base] == 1:
            folder_names[i] = base
        else:
            folder_names[i] = f"{base} ({name_count[base]})"

    total_bytes = sum(g.total_size_bytes for g in games) if include_mods else 0
    copied_bytes = 0

    for i, game in enumerate(games):
        game_dir = output_dir / folder_names[i]
        game_dir.mkdir(parents=True, exist_ok=True)

        # Save metadata (.gbw)
        package = game.create_package()
        package.manifest.has_mod_files = include_mods
        save_gbw(package, game_dir / "migration.gbw")

        if progress:
            progress(f"{game.game_name}: metadata", copied_bytes, total_bytes)

        # Copy mod files if requested
        if include_mods and game.mods_dir and game.mods_dir.is_dir():
            mods_target = game_dir / "mods"
            mods_target.mkdir(exist_ok=True)

            for mod in package.mods:
                if mod.is_separator:
                    continue

                src_dir = game.mods_dir / mod.folder_name
                if not src_dir.is_dir():
                    continue

                dst_dir = mods_target / mod.folder_name
                if dst_dir.exists():
                    shutil.rmtree(dst_dir)

                for src_file in src_dir.rglob("*"):
                    rel = src_file.relative_to(src_dir)
                    dst_file = dst_dir / rel

                    if src_file.is_dir():
                        dst_file.mkdir(parents=True, exist_ok=True)
                    elif src_file.is_file():
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst_file)
                        copied_bytes += src_file.stat().st_size
                        if progress:
                            progress(
                                f"{game.game_name}: {mod.display_name}/{rel}",
                                copied_bytes,
                                total_bytes,
                            )

    return output_dir


# ---------------------------------------------------------------------------
# Single-game export (backward compatibility)
# ---------------------------------------------------------------------------

def export_metadata(instance: MO2Instance, output_path: Path) -> Path:
    """Export metadata only as a .gbw file."""
    package = create_package_from_mo2(instance)
    package.manifest.has_mod_files = False
    return save_gbw(package, output_path)


def export_full(
    instance: MO2Instance,
    output_dir: Path,
    progress: ProgressCallback | None = None,
) -> Path:
    """Export metadata + all mod files to a folder."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    package = create_package_from_mo2(instance)
    package.manifest.has_mod_files = True
    save_gbw(package, output_dir / "migration.gbw")

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
        for src_file in src_dir.rglob("*"):
            rel = src_file.relative_to(src_dir)
            dst_file = dst_dir / rel
            if src_file.is_dir():
                dst_file.mkdir(parents=True, exist_ok=True)
            elif src_file.is_file():
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                copied_bytes += src_file.stat().st_size
                if progress:
                    progress(f"{mod.display_name}/{rel}", copied_bytes, total_bytes)

    return output_dir


def get_export_summary(instance: MO2Instance) -> dict:
    """Get a summary of what would be exported from a single MO2 instance."""
    mods = list(instance.mod_meta.values())
    total_mods = len([m for m in mods if not m.is_separator])
    separators = len([m for m in mods if m.is_separator])
    with_nexus = len([m for m in mods if m.has_nexus_id and not m.is_separator])

    return {
        "total_mods": total_mods,
        "separators": separators,
        "with_nexus_id": with_nexus,
        "without_nexus_id": total_mods - with_nexus,
        "total_size_bytes": sum(m.size_bytes for m in mods if not m.is_separator),
        "game_name": instance.game_name,
        "profiles": [p.name for p in instance.profiles],
    }
