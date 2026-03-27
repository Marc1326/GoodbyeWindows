"""GoodbyeWindows — Anvil Organizer importer.

Creates a complete Anvil instance from a migration package.

Anvil instance structure:
  ~/.anvil-organizer/instances/<Name>/
  ├── .anvil.ini          ← Instance config (INI)
  ├── .mods/              ← Mod folders
  │   └── ModName/
  │       ├── meta.ini    ← MO2-compatible metadata
  │       └── (mod files)
  ├── .downloads/
  ├── .profiles/
  │   ├── modlist.txt     ← Global load order (v2)
  │   └── Default/
  │       └── active_mods.json
  ├── .overwrite/
  └── categories.json
"""

import configparser
import json
from pathlib import Path

from common.migration_format import MigrationPackage, MigrationMod
from common.utils import safe_name


ANVIL_BASE = Path.home() / ".anvil-organizer" / "instances"


def get_anvil_instances_dir() -> Path:
    """Return Anvil's instances directory."""
    return ANVIL_BASE


def list_anvil_instances() -> list[str]:
    """List existing Anvil instance names."""
    if not ANVIL_BASE.exists():
        return []
    return sorted(d.name for d in ANVIL_BASE.iterdir() if d.is_dir())


def import_to_anvil(
    package: MigrationPackage,
    instance_name: str,
    game_path: str = "",
    mod_source_dir: Path | None = None,
    progress_callback=None,
) -> Path:
    """Import a migration package into Anvil Organizer.

    Args:
        package: The migration package to import.
        instance_name: Name for the new Anvil instance.
        game_path: Path to the game installation on Linux.
        mod_source_dir: Optional directory containing mod files (from full export).
        progress_callback: Optional callable(status: str, current: int, total: int).

    Returns:
        Path to the created instance directory.
    """
    instance_dir = ANVIL_BASE / safe_name(instance_name)
    instance_dir.mkdir(parents=True, exist_ok=True)

    mods_dir = instance_dir / ".mods"
    downloads_dir = instance_dir / ".downloads"
    profiles_dir = instance_dir / ".profiles"
    default_profile = profiles_dir / "Default"
    overwrite_dir = instance_dir / ".overwrite"

    mods_dir.mkdir(exist_ok=True)
    downloads_dir.mkdir(exist_ok=True)
    profiles_dir.mkdir(exist_ok=True)
    default_profile.mkdir(exist_ok=True)
    overwrite_dir.mkdir(exist_ok=True)

    # 1. Write .anvil.ini
    _write_anvil_ini(instance_dir, package, game_path)

    # 2. Write modlist.txt (global, v2 format)
    _write_modlist(profiles_dir, package)

    # 3. Write active_mods.json for default profile
    _write_active_mods(default_profile, package)

    # 4. Create mod folders with meta.ini
    total = len(package.mods)
    for i, mod in enumerate(package.mods):
        _create_mod_folder(mods_dir, mod, mod_source_dir)
        if progress_callback:
            progress_callback(mod.display_name, i + 1, total)

    # 5. Write categories.json (empty, will be populated by Anvil)
    categories_path = instance_dir / "categories.json"
    if not categories_path.exists():
        categories_path.write_text("[]", encoding="utf-8")

    return instance_dir


def _write_anvil_ini(instance_dir: Path, package: MigrationPackage, game_path: str):
    """Write .anvil.ini config file."""
    ini = configparser.ConfigParser()
    ini.optionxform = str

    ini["General"] = {
        "gameName": package.manifest.game_name,
        "gamePath": game_path,
        "nexusGameName": package.manifest.nexus_game_slug,
        "imported_from": "GoodbyeWindows",
        "imported_source": package.manifest.source_manager,
    }

    ini["Paths"] = {
        "path_mods": str(instance_dir / ".mods"),
        "path_downloads": str(instance_dir / ".downloads"),
        "path_profiles": str(instance_dir / ".profiles"),
        "path_overwrite": str(instance_dir / ".overwrite"),
    }

    ini_path = instance_dir / ".anvil.ini"
    with open(ini_path, "w", encoding="utf-8") as f:
        ini.write(f)


def _write_modlist(profiles_dir: Path, package: MigrationPackage):
    """Write global modlist.txt (Anvil v2 format)."""
    lines = ["# Managed by Anvil Organizer v2"]

    # Use first profile's mod order, or fallback to package.mods order
    if package.profiles:
        for entry in package.profiles[0].mods:
            lines.append(f"+{entry['name']}")
    else:
        for mod in package.mods:
            lines.append(f"+{mod.folder_name}")

    modlist_path = profiles_dir / "modlist.txt"
    modlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_active_mods(profile_dir: Path, package: MigrationPackage):
    """Write active_mods.json for a profile."""
    active = []

    if package.profiles:
        for entry in package.profiles[0].mods:
            if entry.get("enabled", True):
                active.append(entry["name"])
    else:
        for mod in package.mods:
            if mod.enabled:
                active.append(mod.folder_name)

    active_path = profile_dir / "active_mods.json"
    active_path.write_text(
        json.dumps(active, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _create_mod_folder(
    mods_dir: Path,
    mod: MigrationMod,
    mod_source_dir: Path | None = None,
):
    """Create a mod folder with meta.ini and optionally copy files."""
    mod_dir = mods_dir / mod.folder_name
    mod_dir.mkdir(exist_ok=True)

    # Write meta.ini (MO2-compatible format)
    _write_meta_ini(mod_dir, mod)

    # Copy mod files if source is available
    if mod_source_dir and not mod.is_separator:
        src = mod_source_dir / mod.folder_name
        if src.exists() and src.is_dir():
            import shutil
            for item in src.iterdir():
                if item.name == "meta.ini":
                    continue  # Already written above
                dst = mod_dir / item.name
                if item.is_dir():
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(item, dst)
                else:
                    shutil.copy2(item, dst)


def _write_meta_ini(mod_dir: Path, mod: MigrationMod):
    """Write meta.ini in MO2-compatible format."""
    ini = configparser.ConfigParser()
    ini.optionxform = str

    ini["General"] = {}
    g = ini["General"]
    g["modid"] = str(mod.nexus_id) if mod.nexus_id > 0 else "-1"
    g["version"] = mod.version
    g["newestVersion"] = ""
    g["category"] = ",".join(str(c) for c in mod.category_ids) if mod.category_ids else ""
    g["nexusFileStatus"] = "1"
    g["installationFile"] = mod.installation_file
    g["repository"] = mod.repository

    if mod.url:
        g["url"] = mod.url
    if mod.game_name:
        g["gameName"] = mod.game_name
    if mod.color:
        g["color"] = mod.color

    ini["installed"] = {
        "name": mod.display_name,
        "author": mod.author,
        "description": mod.description,
        "url": mod.url,
    }

    meta_path = mod_dir / "meta.ini"
    with open(meta_path, "w", encoding="utf-8") as f:
        ini.write(f)
