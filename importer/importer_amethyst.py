"""GoodbyeWindows — Amethyst Mod Manager importer.

Creates a mod setup compatible with Amethyst Mod Manager.

Amethyst uses MO2-compatible formats:
- modlist.txt with +/-/* prefix (same as MO2)
- Staging directory with individual mod folders + meta.ini
- profile_state.json for UI state (separator colors, locks, etc.)

Amethyst data directory (verified from source code):
  ~/.config/AmethystModManager/
  ├── Profiles/
  │   └── <game_name>/
  │       ├── mods/                  ← Mod staging (like MO2's mods/)
  │       │   └── ModName/
  │       │       ├── meta.ini
  │       │       └── (mod files)
  │       ├── overwrite/
  │       ├── Root_Folder/
  │       └── profiles/
  │           └── Default/
  │               ├── modlist.txt
  │               ├── plugins.txt
  │               └── profile_state.json
  └── games/
      └── <game_name>/
          └── paths.json
"""

import configparser
import json
from pathlib import Path

from common.migration_format import MigrationPackage, MigrationMod
from common.utils import safe_name


# Amethyst config root (XDG_CONFIG_HOME or default)
AMETHYST_BASE = Path.home() / ".config" / "AmethystModManager"
AMETHYST_PROFILES = AMETHYST_BASE / "Profiles"

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
    "Oblivion Remastered": "OblivionRemastered",
    "Morrowind": "Morrowind",
    "Starfield": "Starfield",
    "Enderal Special Edition": "EnderalSE",
    "Cyberpunk 2077": "Cyberpunk2077",
    "The Witcher 3": "Witcher3",
    "Baldur's Gate 3": "BaldursGate3",
}


def get_amethyst_base() -> Path:
    """Return Amethyst's config root directory."""
    return AMETHYST_BASE


def get_amethyst_profiles() -> Path:
    """Return Amethyst's Profiles directory (staging root)."""
    return AMETHYST_PROFILES


def find_amethyst_games() -> list[str]:
    """List games that have Amethyst setups."""
    if not AMETHYST_PROFILES.exists():
        return []
    return sorted(d.name for d in AMETHYST_PROFILES.iterdir() if d.is_dir())


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
        target_dir = AMETHYST_PROFILES / game_dir_name

    target_dir = Path(target_dir)
    mods_dir = target_dir / "mods"
    overwrite_dir = target_dir / "overwrite"
    root_folder_dir = target_dir / "Root_Folder"
    profiles_dir = target_dir / "profiles"
    profile_dir = profiles_dir / profile_name

    mods_dir.mkdir(parents=True, exist_ok=True)
    overwrite_dir.mkdir(exist_ok=True)
    root_folder_dir.mkdir(exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write modlist.txt
    _write_modlist(profile_dir, package)

    # 2. Write profile_state.json
    _write_profile_state(profile_dir, package)

    # 3. Create mod folders with meta.ini in mods/
    total = len(package.mods)
    for i, mod in enumerate(package.mods):
        _create_staging_mod(mods_dir, mod, mod_source_dir)
        if progress_callback:
            progress_callback(mod.display_name, i + 1, total)

    return target_dir


def _write_modlist(profile_dir: Path, package: MigrationPackage):
    """Write modlist.txt in Amethyst format.

    Format:
      +EnabledMod
      -DisabledMod
      -Name_separator  (separators always use - prefix)
      *LockedMod       (locked/always-on)

    Line 0 (top) = highest priority.
    Separators don't count toward priority.
    """
    lines = []

    if package.profiles:
        for entry in package.profiles[0].mods:
            name = entry["name"]
            is_sep = name.endswith("_separator")
            if is_sep:
                # Amethyst writes separators with - prefix canonically
                lines.append(f"-{name}")
            else:
                prefix = "+" if entry.get("enabled", True) else "-"
                lines.append(f"{prefix}{name}")
    else:
        for mod in package.mods:
            if mod.is_separator:
                lines.append(f"-{mod.folder_name}")
            else:
                prefix = "+" if mod.enabled else "-"
                lines.append(f"{prefix}{mod.folder_name}")

    modlist_path = profile_dir / "modlist.txt"
    modlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_profile_state(profile_dir: Path, package: MigrationPackage):
    """Write profile_state.json with Amethyst's consolidated format."""
    state = {
        "collapsed_seps": [],
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
        "ignored_missing_requirements": [],
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
    mods_dir: Path,
    mod: MigrationMod,
    mod_source_dir: Path | None = None,
):
    """Create a mod folder in Amethyst's mods/ staging directory."""
    mod_dir = mods_dir / mod.folder_name
    mod_dir.mkdir(exist_ok=True)

    # Write meta.ini (Amethyst format)
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
    """Write meta.ini in Amethyst's format.

    Amethyst uses [General] section with these fields:
    gameName, modid, fileid, version, author, nexusName,
    installationFile, installed, nexusUrl, description,
    categoryId, categoryName, fileCategory, endorsed,
    latestFileId, latestVersion, hasUpdate, ignoreUpdate
    """
    ini = configparser.ConfigParser()
    ini.optionxform = str

    ini["General"] = {}
    g = ini["General"]

    # Nexus metadata
    g["gameName"] = mod.game_name or ""
    g["modid"] = str(mod.nexus_id) if mod.nexus_id > 0 else "-1"
    g["fileid"] = ""
    g["version"] = mod.version
    g["author"] = mod.author
    g["nexusName"] = mod.display_name
    g["installationFile"] = mod.installation_file
    g["installed"] = ""
    g["nexusUrl"] = mod.url
    g["description"] = mod.description

    # Category (Amethyst uses categoryId as single int, not CSV)
    if mod.category_ids:
        g["categoryId"] = str(mod.category_ids[0])
    else:
        g["categoryId"] = ""
    g["categoryName"] = ""
    g["fileCategory"] = ""

    # Update tracking
    g["endorsed"] = "false"
    g["latestFileId"] = ""
    g["latestVersion"] = ""
    g["hasUpdate"] = "false"
    g["ignoreUpdate"] = "false"

    # Separator color (stored in meta.ini for MO2 compat)
    if mod.color:
        g["color"] = mod.color

    meta_path = mod_dir / "meta.ini"
    with open(meta_path, "w", encoding="utf-8") as f:
        ini.write(f)
