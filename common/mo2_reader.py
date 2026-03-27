"""GoodbyeWindows — MO2 instance reader.

Reads Mod Organizer 2 portable instances:
- modlist.txt (load order + enabled/disabled)
- meta.ini (per-mod metadata: Nexus ID, version, author, etc.)
- profiles/ (profile list)
- ModOrganizer.ini (instance config)
"""

import configparser
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModMeta:
    """Metadata for a single mod (from meta.ini)."""
    folder_name: str
    display_name: str = ""
    nexus_id: int = -1
    version: str = ""
    newest_version: str = ""
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

    @property
    def has_nexus_id(self) -> bool:
        return self.nexus_id > 0

    def nexus_url(self, nexus_game_name: str = "") -> str:
        """Build Nexus URL from mod ID."""
        if not self.has_nexus_id:
            return ""
        game = nexus_game_name or self.game_name
        if not game:
            return ""
        return f"https://www.nexusmods.com/{game}/mods/{self.nexus_id}"


@dataclass
class MO2Profile:
    """An MO2 profile with its modlist."""
    name: str
    path: Path
    mods: list[tuple[str, bool]] = field(default_factory=list)  # (name, enabled)


@dataclass
class MO2Instance:
    """A complete MO2 instance."""
    path: Path
    game_name: str = ""
    game_path: str = ""
    mods_dir: Path = None
    profiles: list[MO2Profile] = field(default_factory=list)
    mod_meta: dict[str, ModMeta] = field(default_factory=dict)

    def __post_init__(self):
        if self.mods_dir is None:
            self.mods_dir = self.path / "mods"

    @property
    def mod_count(self) -> int:
        return len(self.mod_meta)

    @property
    def profile_count(self) -> int:
        return len(self.profiles)


# --- Readers ---

def read_modlist(modlist_path: Path) -> list[tuple[str, bool]]:
    """Read a modlist.txt file.

    Returns list of (mod_name, enabled) in file order.
    First entry = highest priority.
    """
    if not modlist_path.exists():
        return []

    mods = []
    for line in modlist_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("+"):
            mods.append((line[1:], True))
        elif line.startswith("-"):
            mods.append((line[1:], False))
        elif line.startswith("*"):
            mods.append((line[1:], True))
    return mods


def read_meta_ini(meta_path: Path, folder_name: str) -> ModMeta:
    """Read a mod's meta.ini file."""
    meta = ModMeta(folder_name=folder_name)
    meta.is_separator = folder_name.endswith("_separator")

    if not meta_path.exists():
        meta.display_name = folder_name
        return meta

    cp = configparser.ConfigParser()
    cp.optionxform = str  # preserve case
    try:
        cp.read(str(meta_path), encoding="utf-8-sig")
    except (configparser.Error, UnicodeDecodeError):
        meta.display_name = folder_name
        return meta

    # [General] section
    if cp.has_section("General"):
        g = cp["General"]
        meta.nexus_id = _safe_int(g.get("modid", "-1"))
        meta.version = g.get("version", "")
        meta.newest_version = g.get("newestVersion", "")
        meta.author = g.get("author", "")
        meta.url = g.get("url", "")
        meta.repository = g.get("repository", "Nexus")
        meta.game_name = g.get("gameName", "")
        meta.installation_file = g.get("installationFile", "")
        meta.color = g.get("color", "")

        cat_str = g.get("category", "")
        if cat_str:
            meta.category_ids = [
                int(c.strip()) for c in cat_str.split(",")
                if c.strip().isdigit()
            ]

    # [installed] section
    if cp.has_section("installed"):
        inst = cp["installed"]
        meta.display_name = inst.get("name", folder_name)
        if not meta.author:
            meta.author = inst.get("author", "")
        meta.description = inst.get("description", "")
        if not meta.url:
            meta.url = inst.get("url", "")
    else:
        meta.display_name = meta.display_name or folder_name

    return meta


def read_mo2_ini(ini_path: Path) -> dict[str, str]:
    """Read ModOrganizer.ini and extract key settings."""
    result = {}
    if not ini_path.exists():
        return result

    cp = configparser.ConfigParser()
    cp.optionxform = str
    try:
        cp.read(str(ini_path), encoding="utf-8-sig")
    except configparser.Error:
        return result

    if cp.has_section("General"):
        g = cp["General"]
        result["gameName"] = g.get("gameName", "")
        result["gamePath"] = g.get("gamePath", "").replace("\\\\", "/").replace("\\", "/")
        result["selected_profile"] = g.get("selected_profile", "")

    if cp.has_section("Settings"):
        s = cp["Settings"]
        result["mod_directory"] = s.get("mod_directory", "").replace("\\\\", "/").replace("\\", "/")
        result["download_directory"] = s.get("download_directory", "").replace("\\\\", "/").replace("\\", "/")

    return result


def scan_instance(instance_path: Path) -> MO2Instance | None:
    """Scan a complete MO2 instance directory.

    Returns MO2Instance with all profiles and mod metadata,
    or None if this is not a valid MO2 instance.
    """
    instance_path = Path(instance_path)

    # Check for ModOrganizer.ini
    ini_path = instance_path / "ModOrganizer.ini"
    if not ini_path.exists():
        return None

    ini_data = read_mo2_ini(ini_path)

    # Determine mods directory
    mods_dir_str = ini_data.get("mod_directory", "")
    if mods_dir_str and not mods_dir_str.startswith("%"):
        mods_dir = Path(mods_dir_str)
    else:
        mods_dir = instance_path / "mods"

    if not mods_dir.exists():
        return None

    instance = MO2Instance(
        path=instance_path,
        game_name=ini_data.get("gameName", ""),
        game_path=ini_data.get("gamePath", ""),
        mods_dir=mods_dir,
    )

    # Read profiles
    profiles_dir = instance_path / "profiles"
    if profiles_dir.exists():
        for profile_dir in sorted(profiles_dir.iterdir()):
            if not profile_dir.is_dir():
                continue
            modlist_path = profile_dir / "modlist.txt"
            mods = read_modlist(modlist_path)
            profile = MO2Profile(
                name=profile_dir.name,
                path=profile_dir,
                mods=mods,
            )
            instance.profiles.append(profile)

    # Read mod metadata
    for mod_dir in sorted(mods_dir.iterdir()):
        if not mod_dir.is_dir():
            continue
        meta_path = mod_dir / "meta.ini"
        meta = read_meta_ini(meta_path, mod_dir.name)

        # Calculate folder size
        try:
            meta.size_bytes = sum(
                f.stat().st_size for f in mod_dir.rglob("*") if f.is_file()
            )
        except OSError:
            meta.size_bytes = 0

        instance.mod_meta[mod_dir.name] = meta

    # Apply enabled state from default profile
    if instance.profiles:
        default = instance.profiles[0]
        for mod_name, enabled in default.mods:
            if mod_name in instance.mod_meta:
                instance.mod_meta[mod_name].enabled = enabled

    return instance


def get_dir_size(path: Path) -> int:
    """Get total size of a directory in bytes."""
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return 0


def format_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


# --- Game name mapping (MO2 internal → Nexus slug) ---

GAME_NEXUS_MAP = {
    "Skyrim Special Edition": "skyrimspecialedition",
    "Skyrim SE": "skyrimspecialedition",
    "Skyrim VR": "skyrimspecialedition",
    "Skyrim": "skyrim",
    "Fallout 4": "fallout4",
    "Fallout 4 VR": "fallout4",
    "Fallout 3": "fallout3",
    "Fallout New Vegas": "newvegas",
    "FalloutNV": "newvegas",
    "TTW": "newvegas",
    "Oblivion": "oblivion",
    "Morrowind": "morrowind",
    "Starfield": "starfield",
    "Enderal": "enderal",
    "Enderal Special Edition": "enderalspecialedition",
    "Cyberpunk 2077": "cyberpunk2077",
    "The Witcher 3": "witcher3",
    "Baldur's Gate 3": "baldursgate3",
    "BG3": "baldursgate3",
}


def nexus_game_slug(mo2_game_name: str) -> str:
    """Convert MO2 game name to Nexus Mods URL slug."""
    return GAME_NEXUS_MAP.get(mo2_game_name, mo2_game_name.lower().replace(" ", ""))


# --- Helpers ---

def _safe_int(val: str) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return -1
