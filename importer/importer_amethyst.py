"""GoodbyeWindows — Amethyst Mod Manager importer.

Creates a mod setup compatible with Amethyst Mod Manager.

Amethyst uses MO2-compatible formats:
- modlist.txt with +/- prefix (same as MO2)
- Staging directory with individual mod folders
- profile_state.json for UI state (separator colors, locks, etc.)

Amethyst data directory (default):
  ~/.local/share/amethyst/
  └── <game_name>/
      ├── staging/           ← Mod folders (like MO2's mods/)
      │   └── ModName/
      │       └── (mod files)
      ├── profiles/
      │   └── Default/
      │       ├── modlist.txt
      │       └── profile_state.json
      └── downloads/

NOTE: Amethyst paths may vary. The importer allows the user to
specify the target directory manually.
"""

import json
from pathlib import Path

from common.migration_format import MigrationPackage, MigrationMod
from common.utils import safe_name


# Default Amethyst data directory
AMETHYST_BASE = Path.home() / ".local" / "share" / "amethyst"

# Game name mapping (MO2 internal → Amethyst directory names)
AMETHYST_GAME_MAP = {
    "Skyrim Special Edition": "SkyrimSE",
    "Skyrim": "Skyrim",
    "Skyrim VR": "SkyrimVR",
    "Fallout 4": "Fallout4",
    "Fallout 4 VR": "Fallout4VR",
    "Fallout 3": "Fallout3",
    "Fallout New Vegas": "FalloutNV",
    "FalloutNV": "FalloutNV",
    "Oblivion": "Oblivion",
    "Morrowind": "Morrowind",
    "Starfield": "Starfield",
    "Enderal Special Edition": "EnderalSE",
    "Cyberpunk 2077": "Cyberpunk2077",
    "The Witcher 3": "Witcher3",
    "Baldur's Gate 3": "BaldursGate3",
}


def get_amethyst_base() -> Path:
    """Return Amethyst's default data directory."""
    return AMETHYST_BASE


def find_amethyst_games() -> list[str]:
    """List games that have Amethyst setups."""
    if not AMETHYST_BASE.exists():
        return []
    return sorted(d.name for d in AMETHYST_BASE.iterdir() if d.is_dir())


def amethyst_game_name(mo2_game: str) -> str:
    """Convert MO2 game name to Amethyst directory name."""
    return AMETHYST_GAME_MAP.get(mo2_game, safe_name(mo2_game))


def import_to_amethyst(
    package: MigrationPackage,
    target_dir: Path | None = None,
    profile_name: str = "Default",
    mod_source_dir: Path | None = None,
    progress_callback=None,
) -> Path:
    """Import a migration package into Amethyst Mod Manager.

    Args:
        package: The migration package to import.
        target_dir: Custom target directory. If None, uses default Amethyst path.
        profile_name: Profile name to create (default: "Default").
        mod_source_dir: Optional directory containing mod files.
        progress_callback: Optional callable(status: str, current: int, total: int).

    Returns:
        Path to the game directory in Amethyst.
    """
    if target_dir is None:
        game_dir_name = amethyst_game_name(package.manifest.game_name)
        target_dir = AMETHYST_BASE / game_dir_name

    target_dir = Path(target_dir)
    staging_dir = target_dir / "staging"
    profiles_dir = target_dir / "profiles"
    profile_dir = profiles_dir / profile_name
    downloads_dir = target_dir / "downloads"

    staging_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(exist_ok=True)

    # 1. Write modlist.txt (MO2-compatible, Amethyst reads this)
    _write_modlist(profile_dir, package)

    # 2. Write profile_state.json (separator colors, etc.)
    _write_profile_state(profile_dir, package)

    # 3. Create mod folders in staging
    total = len(package.mods)
    for i, mod in enumerate(package.mods):
        _create_staging_mod(staging_dir, mod, mod_source_dir)
        if progress_callback:
            progress_callback(mod.display_name, i + 1, total)

    return target_dir


def _write_modlist(profile_dir: Path, package: MigrationPackage):
    """Write modlist.txt in MO2/Amethyst format.

    Amethyst uses the same format as MO2:
      +EnabledMod
      -DisabledMod
      +Name_separator  (separator)
      *LockedMod       (locked/always-on)
    """
    lines = []

    if package.profiles:
        for entry in package.profiles[0].mods:
            prefix = "+" if entry.get("enabled", True) else "-"
            lines.append(f"{prefix}{entry['name']}")
    else:
        for mod in package.mods:
            prefix = "+" if mod.enabled else "-"
            lines.append(f"{prefix}{mod.folder_name}")

    modlist_path = profile_dir / "modlist.txt"
    modlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_profile_state(profile_dir: Path, package: MigrationPackage):
    """Write profile_state.json with separator colors and other state."""
    state = {
        "collapsed_seps": {},
        "separator_locks": {},
        "separator_colors": {},
        "separator_deploy_paths": {},
        "root_folder_state": {},
        "mod_strip_prefixes": {},
        "plugin_locks": {},
        "disabled_plugins": {},
        "excluded_mod_files": {},
        "profile_settings": {
            "imported_from": "GoodbyeWindows",
            "imported_source": package.manifest.source_manager,
        },
        "ignored_missing_requirements": {},
    }

    # Set separator colors from migration data
    for mod in package.mods:
        if mod.is_separator and mod.color:
            state["separator_colors"][mod.folder_name] = mod.color

    state_path = profile_dir / "profile_state.json"
    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _create_staging_mod(
    staging_dir: Path,
    mod: MigrationMod,
    mod_source_dir: Path | None = None,
):
    """Create a mod folder in Amethyst's staging directory."""
    mod_dir = staging_dir / mod.folder_name
    mod_dir.mkdir(exist_ok=True)

    # Amethyst also reads meta.ini (MO2-compatible)
    _write_meta_ini(mod_dir, mod)

    # Copy mod files if source available
    if mod_source_dir and not mod.is_separator:
        src = mod_source_dir / mod.folder_name
        if src.exists() and src.is_dir():
            import shutil
            for item in src.iterdir():
                if item.name == "meta.ini":
                    continue
                dst = mod_dir / item.name
                if item.is_dir():
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(item, dst)
                else:
                    shutil.copy2(item, dst)


def _write_meta_ini(mod_dir: Path, mod: MigrationMod):
    """Write meta.ini (shared with Anvil, MO2-compatible)."""
    import configparser

    ini = configparser.ConfigParser()
    ini.optionxform = str

    ini["General"] = {}
    g = ini["General"]
    g["modid"] = str(mod.nexus_id) if mod.nexus_id > 0 else "-1"
    g["version"] = mod.version
    g["newestVersion"] = ""
    g["category"] = ",".join(str(c) for c in mod.category_ids) if mod.category_ids else ""
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
