"""GoodbyeWindows — Migration format (.gbw).

A .gbw file is a ZIP archive containing:
- manifest.json  — Tool version, source manager, game info
- mods.json      — List of all mods with metadata
- profiles.json  — Profile data (load orders, enabled states)
- mods/          — (optional) actual mod files when full export is used

Full export packs everything into a single .gbw:
  Skyrim [MO2].gbw  ← metadata + all mod files inside
"""

import json
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

FORMAT_VERSION = 2
TOOL_NAME = "GoodbyeWindows"
TOOL_VERSION = "1.0.0"
GBW_EXTENSION = ".gbw"

# Compression levels
COMPRESS_NONE = 0    # ZIP_STORED — fastest, no compression
COMPRESS_LOW = 1     # ZIP_DEFLATED level 1 — fast, some compression
COMPRESS_STRONG = 2  # ZIP_DEFLATED level 9 — smallest, slowest


def _zip_params(compression: int) -> dict:
    """Return zipfile.ZipFile kwargs for the given compression level."""
    if compression == COMPRESS_NONE:
        return {"compression": zipfile.ZIP_STORED}
    elif compression == COMPRESS_LOW:
        return {"compression": zipfile.ZIP_DEFLATED, "compresslevel": 1}
    else:
        return {"compression": zipfile.ZIP_DEFLATED, "compresslevel": 9}


@dataclass
class MigrationMod:
    """A mod entry in the migration format."""
    folder_name: str
    display_name: str = ""
    nexus_id: int = -1
    version: str = ""
    author: str = ""
    url: str = ""
    category_ids: list[int] = field(default_factory=list)
    repository: str = "Nexus"
    game_name: str = ""
    installation_file: str = ""
    description: str = ""
    is_separator: bool = False
    color: str = ""
    enabled: bool = True
    size_bytes: int = 0


@dataclass
class MigrationProfile:
    """A profile in the migration format."""
    name: str
    mods: list[dict] = field(default_factory=list)  # [{name, enabled}]


@dataclass
class MigrationManifest:
    """Top-level manifest for the migration package."""
    tool_name: str = TOOL_NAME
    tool_version: str = TOOL_VERSION
    format_version: int = FORMAT_VERSION
    created: str = ""
    source_manager: str = "MO2"
    source_path: str = ""
    game_name: str = ""
    game_path: str = ""
    nexus_game_slug: str = ""
    mod_count: int = 0
    total_size_bytes: int = 0
    has_mod_files: bool = False

    def __post_init__(self):
        if not self.created:
            self.created = datetime.now(timezone.utc).isoformat()


@dataclass
class MigrationPackage:
    """Complete migration package."""
    manifest: MigrationManifest = field(default_factory=MigrationManifest)
    mods: list[MigrationMod] = field(default_factory=list)
    profiles: list[MigrationProfile] = field(default_factory=list)


def create_package_from_vortex(game) -> MigrationPackage:
    """Create a MigrationPackage from a VortexGame.

    Args:
        game: A vortex_reader.VortexGame object (one game from a VortexInstance).
    """
    from .vortex_reader import nexus_slug_from_vortex

    package = MigrationPackage()

    # Manifest
    package.manifest.source_manager = "Vortex"
    package.manifest.source_path = game.staging_folder
    package.manifest.game_name = game.name
    package.manifest.game_path = game.game_path
    package.manifest.nexus_game_slug = nexus_slug_from_vortex(game.game_id)
    package.manifest.mod_count = game.mod_count
    package.manifest.total_size_bytes = game.total_size

    # Mods
    for mod in game.mods.values():
        package.mods.append(MigrationMod(
            folder_name=mod.folder_name,
            display_name=mod.display_name,
            nexus_id=mod.nexus_id,
            version=mod.version,
            author=mod.author,
            url=mod.url,
            category_ids=list(mod.category_ids),
            repository="Nexus",
            game_name=game.name,
            installation_file=mod.installation_file,
            description=mod.description,
            is_separator=False,
            color="",
            enabled=mod.enabled,
            size_bytes=mod.size_bytes,
        ))

    # Profiles
    for profile in game.profiles:
        package.profiles.append(MigrationProfile(
            name=profile.name,
            mods=[
                {"name": mid, "enabled": enabled}
                for mid, enabled in profile.mod_state.items()
            ],
        ))

    return package


def create_package_from_mo2(instance) -> MigrationPackage:
    """Create a MigrationPackage from an MO2Instance.

    Args:
        instance: An mo2_reader.MO2Instance object.
    """
    from .mo2_reader import nexus_game_slug

    package = MigrationPackage()

    # Manifest
    package.manifest.source_path = str(instance.path)
    package.manifest.game_name = instance.game_name
    package.manifest.game_path = instance.game_path
    package.manifest.nexus_game_slug = nexus_game_slug(instance.game_name)
    package.manifest.mod_count = len(instance.mod_meta)
    package.manifest.total_size_bytes = sum(
        m.size_bytes for m in instance.mod_meta.values()
    )

    # Mods
    for meta in instance.mod_meta.values():
        mod = MigrationMod(
            folder_name=meta.folder_name,
            display_name=meta.display_name,
            nexus_id=meta.nexus_id,
            version=meta.version,
            author=meta.author,
            url=meta.url,
            category_ids=meta.category_ids,
            repository=meta.repository,
            game_name=meta.game_name,
            installation_file=meta.installation_file,
            description=meta.description,
            is_separator=meta.is_separator,
            color=meta.color,
            enabled=meta.enabled,
            size_bytes=meta.size_bytes,
        )
        package.mods.append(mod)

    # Profiles
    for profile in instance.profiles:
        mp = MigrationProfile(
            name=profile.name,
            mods=[
                {"name": name, "enabled": enabled}
                for name, enabled in profile.mods
            ],
        )
        package.profiles.append(mp)

    return package


def save_gbw(
    package: MigrationPackage,
    output_path: Path,
    mods_dir: Path | None = None,
    compression: int = COMPRESS_LOW,
    progress: Callable[[str, int, int], None] | None = None,
) -> Path:
    """Save a migration package as a .gbw file.

    Args:
        package: The migration package.
        output_path: Target .gbw path.
        mods_dir: If provided, include mod files from this directory.
        compression: COMPRESS_NONE, COMPRESS_LOW, or COMPRESS_STRONG.
        progress: Optional callback(status, current_bytes, total_bytes).

    Returns the path to the created file.
    """
    output_path = Path(output_path)
    if not output_path.suffix:
        output_path = output_path.with_suffix(GBW_EXTENSION)

    if mods_dir is not None:
        package.manifest.has_mod_files = True

    zip_kw = _zip_params(compression)

    with zipfile.ZipFile(output_path, "w", **zip_kw) as zf:
        # Metadata (always deflated for small JSON)
        zf.writestr("manifest.json", json.dumps(
            asdict(package.manifest), indent=2, ensure_ascii=False
        ))
        zf.writestr("mods.json", json.dumps(
            [asdict(m) for m in package.mods], indent=2, ensure_ascii=False
        ))
        zf.writestr("profiles.json", json.dumps(
            [asdict(p) for p in package.profiles], indent=2, ensure_ascii=False
        ))

        # Mod files
        if mods_dir is not None and mods_dir.is_dir():
            total_bytes = sum(
                m.size_bytes for m in package.mods if not m.is_separator
            )
            written_bytes = 0

            for mod in package.mods:
                if mod.is_separator:
                    continue
                src_dir = mods_dir / mod.folder_name
                if not src_dir.is_dir():
                    continue

                for src_file in src_dir.rglob("*"):
                    if not src_file.is_file():
                        continue
                    rel = src_file.relative_to(mods_dir)
                    arcname = f"mods/{rel}"
                    zf.write(src_file, arcname)
                    written_bytes += src_file.stat().st_size
                    if progress:
                        progress(
                            f"{mod.display_name}/{src_file.name}",
                            written_bytes,
                            total_bytes,
                        )

    return output_path


def peek_gbw_manifest(gbw_path: Path) -> MigrationManifest:
    """Read only the manifest from a .gbw file (fast, no mod data loaded)."""
    gbw_path = Path(gbw_path)
    with zipfile.ZipFile(gbw_path, "r") as zf:
        manifest_data = json.loads(zf.read("manifest.json"))
        return MigrationManifest(**{
            k: v for k, v in manifest_data.items()
            if k in MigrationManifest.__dataclass_fields__
        })


def load_gbw(gbw_path: Path) -> MigrationPackage:
    """Load a migration package from a .gbw file."""
    gbw_path = Path(gbw_path)
    package = MigrationPackage()

    with zipfile.ZipFile(gbw_path, "r") as zf:
        # Manifest
        manifest_data = json.loads(zf.read("manifest.json"))
        package.manifest = MigrationManifest(**{
            k: v for k, v in manifest_data.items()
            if k in MigrationManifest.__dataclass_fields__
        })

        # Mods
        mods_data = json.loads(zf.read("mods.json"))
        for md in mods_data:
            package.mods.append(MigrationMod(**{
                k: v for k, v in md.items()
                if k in MigrationMod.__dataclass_fields__
            }))

        # Profiles
        profiles_data = json.loads(zf.read("profiles.json"))
        for pd in profiles_data:
            package.profiles.append(MigrationProfile(**{
                k: v for k, v in pd.items()
                if k in MigrationProfile.__dataclass_fields__
            }))

    return package


def gbw_has_mods(gbw_path: Path) -> bool:
    """Quick check whether a .gbw file contains mod files."""
    with zipfile.ZipFile(gbw_path, "r") as zf:
        return any(n.startswith("mods/") for n in zf.namelist())


def extract_mods_from_gbw(
    gbw_path: Path,
    target_dir: Path,
    progress: Callable[[str, int, int], None] | None = None,
) -> Path:
    """Extract mod files from a .gbw archive to a directory.

    Returns the path to the mods directory (target_dir/mods/).
    """
    gbw_path = Path(gbw_path)
    target_dir = Path(target_dir)
    mods_out = target_dir / "mods"
    mods_out.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(gbw_path, "r") as zf:
        mod_entries = [n for n in zf.namelist() if n.startswith("mods/")]
        total_bytes = sum(zf.getinfo(n).file_size for n in mod_entries)
        extracted_bytes = 0

        for name in mod_entries:
            info = zf.getinfo(name)
            if info.is_dir():
                (target_dir / name).mkdir(parents=True, exist_ok=True)
                continue

            target_file = target_dir / name
            target_file.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target_file, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 256)
                    if not chunk:
                        break
                    dst.write(chunk)
                    extracted_bytes += len(chunk)

            if progress:
                short_name = "/".join(name.split("/")[-2:])
                progress(short_name, extracted_bytes, total_bytes)

    return mods_out
