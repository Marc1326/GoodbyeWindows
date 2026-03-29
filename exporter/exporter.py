"""GoodbyeWindows — Export logic.

Handles export of MO2 and/or Vortex mod setups:
1. Metadata only (.gbw files — small, mods re-downloaded on Linux)
2. Full export  (single .gbw per game with mod files packed inside)
3. Network transfer (handled by server.py)

Supports multi-game export: each game gets its own .gbw file.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from common.mo2_reader import MO2Instance
from common.migration_format import (
    COMPRESS_NONE,
    COMPRESS_LOW,
    COMPRESS_STRONG,
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
    def instance_path(self) -> str:
        """Short display path of the source instance."""
        home = str(Path.home())
        if self._mo2_instance is not None:
            p = str(self._mo2_instance.path)
        elif self._vortex_game is not None:
            p = str(getattr(self._vortex_game, 'staging_folder', ''))
        else:
            return ""
        if p.startswith(home):
            p = "~" + p[len(home):]
        # Shorten Steam Proton paths
        proton_marker = "/.local/share/Steam/steamapps/compatdata/"
        if proton_marker in p or "/.steam/steam/steamapps/compatdata/" in p:
            # Extract the last directory name (game instance name)
            last_part = Path(p).name
            return f"Steam/Proton: {last_part}"
        return p

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
    compression: int = COMPRESS_LOW,
    progress: ProgressCallback | None = None,
) -> Path:
    """Export one or more games to an output directory.

    Creates one .gbw file per game:
        output_dir/
        ├── Game Name [MO2].gbw      ← metadata (+ mod files if include_mods)
        ├── Game Name [Vortex].gbw
        ...

    Args:
        games: List of ExportableGame to export.
        output_dir: Target directory (USB drive, external HDD, etc.).
        include_mods: If True, pack mod files into the .gbw archive.
        compression: COMPRESS_NONE, COMPRESS_LOW, or COMPRESS_STRONG.
        progress: Optional callback(status, current_bytes, total_bytes).

    Returns:
        Path to the output directory.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve duplicate file names
    file_names: dict[int, str] = {}
    name_count: dict[str, int] = {}
    for i, game in enumerate(games):
        base = game.safe_folder_name
        name_count[base] = name_count.get(base, 0) + 1
        if name_count[base] == 1:
            file_names[i] = base
        else:
            file_names[i] = f"{base} ({name_count[base]})"

    total_bytes = sum(g.total_size_bytes for g in games) if include_mods else 0
    written_bytes = 0

    for i, game in enumerate(games):
        package = game.create_package()
        package.manifest.has_mod_files = include_mods

        mods_dir = None
        if include_mods and game.mods_dir and game.mods_dir.is_dir():
            mods_dir = game.mods_dir

        if progress:
            progress(f"{game.game_name}: metadata", written_bytes, total_bytes)

        def _on_file_progress(status, current, total, _game=game, _base=written_bytes):
            if progress:
                progress(
                    f"{_game.game_name}: {status}",
                    _base + current,
                    total_bytes,
                )

        save_gbw(
            package,
            output_dir / f"{file_names[i]}.gbw",
            mods_dir=mods_dir,
            compression=compression,
            progress=_on_file_progress if include_mods else None,
        )

        if include_mods:
            written_bytes += game.total_size_bytes

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
    output_path: Path,
    compression: int = COMPRESS_LOW,
    progress: ProgressCallback | None = None,
) -> Path:
    """Export metadata + all mod files as a single .gbw archive."""
    package = create_package_from_mo2(instance)
    package.manifest.has_mod_files = True
    return save_gbw(
        package,
        output_path,
        mods_dir=instance.mods_dir,
        compression=compression,
        progress=progress,
    )


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
