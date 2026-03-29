"""GoodbyeWindows — Vortex Mod Manager reader.

Reads Vortex's LevelDB database (state.v2/) to extract:
- Installed mods per game (with Nexus IDs, versions, authors)
- Profiles with enabled/disabled states
- Game discovery data (paths, staging folders)

Vortex stores its state in a LevelDB at:
  Windows: %APPDATA%\\Vortex\\state.v2\\
  Linux (Wine): ~/.local/share/bottles/.../AppData/Roaming/Vortex/state.v2/

Keys use '###' as separator, values are JSON strings.
Example key: persistent###mods###skyrimspecialedition###SkyUI_5_2_SE
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Vortex game ID → human-readable name
# ---------------------------------------------------------------------------

VORTEX_GAME_NAMES: dict[str, str] = {
    "skyrimspecialedition": "Skyrim Special Edition",
    "skyrim": "Skyrim",
    "skyrimvr": "Skyrim VR",
    "fallout4": "Fallout 4",
    "fallout4vr": "Fallout 4 VR",
    "fallout3": "Fallout 3",
    "falloutnv": "Fallout New Vegas",
    "newvegas": "Fallout New Vegas",
    "oblivion": "Oblivion",
    "morrowind": "Morrowind",
    "starfield": "Starfield",
    "cyberpunk2077": "Cyberpunk 2077",
    "witcher3": "The Witcher 3",
    "baldursgate3": "Baldur's Gate 3",
    "stardewvalley": "Stardew Valley",
    "nomanssky": "No Man's Sky",
    "eldenring": "Elden Ring",
    "monsterhunterworld": "Monster Hunter World",
    "enderalspecialedition": "Enderal Special Edition",
    "dragonage2": "Dragon Age II",
    "mountandblade2bannerlord": "Mount & Blade II: Bannerlord",
}

# Vortex game IDs that differ from Nexus slugs
VORTEX_NEXUS_MAP: dict[str, str] = {
    "skyrimvr": "skyrimspecialedition",
    "fallout4vr": "fallout4",
    "falloutnv": "newvegas",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class VortexMod:
    """A mod from Vortex's database."""
    mod_id: str
    folder_name: str = ""         # installationPath (relative to staging)
    display_name: str = ""
    nexus_id: int = -1
    file_id: int = -1
    version: str = ""
    author: str = ""
    url: str = ""
    category_ids: list[int] = field(default_factory=list)
    description: str = ""
    state: str = ""               # "installed", "downloading", etc.
    mod_type: str = ""            # "", "collection", "dinput", "enb"
    enabled: bool = True
    size_bytes: int = 0
    installation_file: str = ""   # original archive filename

    @property
    def has_nexus_id(self) -> bool:
        return self.nexus_id > 0

    @property
    def is_separator(self) -> bool:
        return False  # Vortex has no MO2-style separators


@dataclass
class VortexProfile:
    """A Vortex profile."""
    profile_id: str
    name: str = ""
    game_id: str = ""
    mod_state: dict[str, bool] = field(default_factory=dict)  # modId → enabled
    last_activated: int = 0


@dataclass
class VortexGame:
    """A discovered game in Vortex with its mods and profiles."""
    game_id: str
    name: str = ""
    game_path: str = ""
    staging_folder: str = ""
    mods: dict[str, VortexMod] = field(default_factory=dict)
    profiles: list[VortexProfile] = field(default_factory=list)

    @property
    def mod_count(self) -> int:
        return len(self.mods)

    @property
    def profile_count(self) -> int:
        return len(self.profiles)

    @property
    def total_size(self) -> int:
        return sum(m.size_bytes for m in self.mods.values())


@dataclass
class VortexInstance:
    """A complete Vortex installation."""
    path: Path                    # Path to Vortex AppData
    db_path: Path = None          # Path to state.v2/
    games: dict[str, VortexGame] = field(default_factory=dict)

    def __post_init__(self):
        if self.db_path is None:
            self.db_path = self.path / "state.v2"


# ---------------------------------------------------------------------------
# LevelDB reading
# ---------------------------------------------------------------------------

def read_vortex_db(db_path: Path) -> dict[str, str]:
    """Read all key-value pairs from Vortex's LevelDB.

    Args:
        db_path: Path to the state.v2/ directory.

    Returns:
        Dict of key → value (both strings).

    Raises:
        ImportError: If plyvel is not installed.
    """
    try:
        import plyvel
    except ImportError:
        raise ImportError(
            "plyvel is required to read Vortex data.\n"
            "Install: pip install plyvel\n"
            "Debian/Ubuntu: sudo apt install libleveldb-dev && pip install plyvel"
        )

    db = plyvel.DB(str(db_path), create_if_missing=False)
    data: dict[str, str] = {}
    try:
        for key_bytes, value_bytes in db:
            try:
                key = key_bytes.decode("utf-8")
                value = value_bytes.decode("utf-8")
                data[key] = value
            except UnicodeDecodeError:
                continue  # skip binary entries
    finally:
        db.close()

    return data


# ---------------------------------------------------------------------------
# State tree building
# ---------------------------------------------------------------------------

def _build_state_tree(raw_data: dict[str, str]) -> dict:
    """Build nested dict from flat LevelDB key-value pairs.

    Keys use '###' as separator.  Values are JSON strings.
    Handles both full-object storage and individual-property storage.
    """
    tree: dict = {}

    for key, value in raw_data.items():
        parts = key.split("###")

        # Parse JSON value
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            parsed = value

        # Navigate / create path
        node = tree
        for part in parts[:-1]:
            if part not in node:
                node[part] = {}
            elif not isinstance(node[part], dict):
                node[part] = {"__value__": node[part]}
            node = node[part]

        last_key = parts[-1]
        existing = node.get(last_key)

        if isinstance(existing, dict) and isinstance(parsed, dict):
            _deep_merge(existing, parsed)
        else:
            node[last_key] = parsed

    return tree


def _deep_merge(target: dict, source: dict) -> None:
    """Merge *source* into *target*, recursively for nested dicts."""
    for k, v in source.items():
        if k in target and isinstance(target[k], dict) and isinstance(v, dict):
            _deep_merge(target[k], v)
        else:
            target[k] = v


# ---------------------------------------------------------------------------
# Mod / profile parsing
# ---------------------------------------------------------------------------

def _parse_mod(mod_id: str, data: dict) -> VortexMod:
    """Parse a single mod entry from Vortex state data."""
    attrs = data.get("attributes", {})
    if not isinstance(attrs, dict):
        attrs = {}

    mod = VortexMod(mod_id=mod_id)
    mod.folder_name = str(data.get("installationPath", mod_id))
    mod.display_name = str(
        attrs.get("modName", "")
        or attrs.get("name", "")
        or attrs.get("customFileName", "")
        or mod_id
    )
    mod.state = str(data.get("state", ""))
    mod.mod_type = str(data.get("type", ""))

    # Nexus IDs
    mod.nexus_id = _safe_int(attrs.get("modId", -1))
    mod.file_id = _safe_int(attrs.get("fileId", -1))

    # Metadata
    mod.version = str(attrs.get("version", ""))
    mod.author = str(attrs.get("author", ""))
    mod.description = str(attrs.get("description", ""))
    mod.url = str(attrs.get("homepage", attrs.get("url", "")))
    mod.size_bytes = _safe_int(attrs.get("fileSize", 0))
    mod.installation_file = str(attrs.get("fileName", ""))

    # Category
    cat = attrs.get("category")
    if isinstance(cat, int) and cat > 0:
        mod.category_ids = [cat]
    elif isinstance(cat, str) and cat.isdigit() and int(cat) > 0:
        mod.category_ids = [int(cat)]

    return mod


def _parse_profile(profile_id: str, data: dict) -> VortexProfile:
    """Parse a single profile entry from Vortex state data."""
    profile = VortexProfile(profile_id=profile_id)
    profile.name = str(data.get("name", profile_id))
    profile.game_id = str(data.get("gameId", ""))
    profile.last_activated = _safe_int(data.get("lastActivated", 0))

    mod_state = data.get("modState", {})
    if isinstance(mod_state, dict):
        for mid, st in mod_state.items():
            if isinstance(st, dict):
                profile.mod_state[mid] = bool(st.get("enabled", False))
            elif isinstance(st, bool):
                profile.mod_state[mid] = st

    return profile


def _apply_profile_state(game: VortexGame) -> None:
    """Apply enabled/disabled state from the most-recently-activated profile."""
    if not game.profiles:
        return

    best = max(game.profiles, key=lambda p: p.last_activated)

    for mod_id, mod in game.mods.items():
        if mod_id in best.mod_state:
            mod.enabled = best.mod_state[mod_id]


def _update_mod_sizes(game: VortexGame) -> None:
    """Update mod sizes by scanning the staging folder on disk."""
    staging = Path(game.staging_folder)
    if not staging.is_dir():
        return

    for mod in game.mods.values():
        mod_dir = staging / mod.folder_name
        if mod_dir.is_dir():
            try:
                mod.size_bytes = sum(
                    f.stat().st_size for f in mod_dir.rglob("*") if f.is_file()
                )
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

def scan_vortex_instance(vortex_path: Path) -> VortexInstance | None:
    """Scan a Vortex installation and extract all game data.

    Args:
        vortex_path: Path to Vortex AppData directory (contains state.v2/).

    Returns:
        VortexInstance with all games, mods, and profiles, or None if invalid.
    """
    vortex_path = Path(vortex_path)
    db_path = vortex_path / "state.v2"

    if not db_path.exists():
        return None

    instance = VortexInstance(path=vortex_path, db_path=db_path)

    # Read database
    try:
        raw_data = read_vortex_db(db_path)
    except Exception:
        return None

    if not raw_data:
        return None

    state = _build_state_tree(raw_data)

    # --- Game discovery (paths) ---
    discovered: dict[str, dict] = {}
    settings = state.get("settings", {})
    game_mode = settings.get("gameMode", {})
    discovered_raw = game_mode.get("discovered", {})

    if isinstance(discovered_raw, dict):
        for gid, gdata in discovered_raw.items():
            if isinstance(gdata, dict) and gdata.get("path"):
                discovered[gid] = gdata

    # --- Staging folder paths ---
    install_paths: dict[str, str] = {}
    mods_settings = settings.get("mods", {})
    ip_raw = mods_settings.get("installPath", {})
    if isinstance(ip_raw, dict):
        for gid, p in ip_raw.items():
            if isinstance(p, str):
                install_paths[gid] = p

    # --- Mods per game ---
    persistent = state.get("persistent", {})
    mods_by_game = persistent.get("mods", {})

    if isinstance(mods_by_game, dict):
        for game_id, game_mods in mods_by_game.items():
            if not isinstance(game_mods, dict):
                continue

            game = VortexGame(game_id=game_id)
            game.name = VORTEX_GAME_NAMES.get(game_id, game_id)

            # Game path
            if game_id in discovered:
                game.game_path = str(discovered[game_id].get("path", ""))

            # Staging folder
            game.staging_folder = install_paths.get(game_id, "")

            # Parse mods – only keep installed ones
            for mid, mdata in game_mods.items():
                if not isinstance(mdata, dict):
                    continue
                mod = _parse_mod(mid, mdata)
                if mod.state == "installed":
                    game.mods[mid] = mod

            if game.mods:
                instance.games[game_id] = game

    # --- Profiles ---
    profiles_raw = persistent.get("profiles", {})
    if isinstance(profiles_raw, dict):
        for pid, pdata in profiles_raw.items():
            if not isinstance(pdata, dict):
                continue
            profile = _parse_profile(pid, pdata)
            if profile.game_id in instance.games:
                instance.games[profile.game_id].profiles.append(profile)

    # --- Apply enabled state & sizes ---
    for game in instance.games.values():
        _apply_profile_state(game)
        if game.staging_folder:
            _update_mod_sizes(game)

    return instance


# ---------------------------------------------------------------------------
# Nexus slug helper
# ---------------------------------------------------------------------------

def nexus_slug_from_vortex(game_id: str) -> str:
    """Convert Vortex game ID to Nexus Mods URL slug.

    Vortex game IDs are usually identical to Nexus slugs.
    """
    return VORTEX_NEXUS_MAP.get(game_id, game_id)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_int(val) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return -1
