"""GoodbyeWindows — MO2 Instance Scanner (Windows).

Finds MO2 portable instances by scanning:
1. Windows Registry (HKCU\\Software\\Mod Organizer Team)
2. Common installation paths
3. All drive roots
"""

import os
import sys
from pathlib import Path

from common.mo2_reader import scan_instance, MO2Instance


# Common paths where MO2 is typically installed
COMMON_PATHS = [
    "Mod Organizer 2",
    "MO2",
    "Modding/MO2",
    "Games/MO2",
    "Modding/Mod Organizer 2",
]

# Folders to check inside common paths (game-specific instances)
INSTANCE_INDICATORS = ["ModOrganizer.ini"]


def scan_registry() -> list[Path]:
    """Scan Windows Registry for MO2 installations.

    Returns list of paths found in registry.
    """
    paths = []
    if sys.platform != "win32":
        return paths

    try:
        import winreg
        # MO2 stores instance paths in registry
        key_paths = [
            r"Software\Mod Organizer Team\Mod Organizer",
            r"Software\ModOrganizer",
        ]
        for key_path in key_paths:
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                        if isinstance(value, str) and Path(value).exists():
                            paths.append(Path(value))
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(key)
            except OSError:
                continue
    except ImportError:
        pass

    return paths


def scan_common_paths() -> list[Path]:
    """Scan common installation paths on all drives."""
    paths = []

    if sys.platform == "win32":
        # Check all drive letters
        drives = []
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:\\")
            if drive.exists():
                drives.append(drive)
    else:
        # Linux: check home and common mount points
        drives = [Path.home()]
        for mount in Path("/mnt").iterdir() if Path("/mnt").exists() else []:
            if mount.is_dir():
                drives.append(mount)
        for media in Path("/media").iterdir() if Path("/media").exists() else []:
            if media.is_dir():
                for sub in media.iterdir():
                    if sub.is_dir():
                        drives.append(sub)

    for drive in drives:
        for common in COMMON_PATHS:
            candidate = drive / common
            if candidate.exists() and (candidate / "ModOrganizer.ini").exists():
                paths.append(candidate)

        # Also check direct subfolders of drive root (portable installs)
        try:
            for subdir in drive.iterdir():
                if subdir.is_dir() and (subdir / "ModOrganizer.ini").exists():
                    if subdir not in paths:
                        paths.append(subdir)
        except PermissionError:
            continue

    return paths


def scan_appdata() -> list[Path]:
    """Scan MO2 AppData location for instance references."""
    paths = []
    if sys.platform != "win32":
        return paths

    appdata = Path(os.environ.get("LOCALAPPDATA", ""))
    mo2_appdata = appdata / "ModOrganizer"
    if mo2_appdata.exists():
        # MO2 global instances are registered here
        for ini in mo2_appdata.glob("*.ini"):
            try:
                content = ini.read_text(encoding="utf-8-sig")
                for line in content.splitlines():
                    if "gamePath" in line or "BaseDirectory" in line:
                        _, _, val = line.partition("=")
                        val = val.strip().replace("\\\\", "/").replace("\\", "/")
                        p = Path(val)
                        if p.exists() and p not in paths:
                            paths.append(p)
            except (OSError, UnicodeDecodeError):
                continue

    return paths


def find_all_instances() -> list[MO2Instance]:
    """Find all MO2 instances on the system.

    Combines registry, common paths, and AppData scanning.
    Returns list of valid MO2Instance objects.
    """
    candidate_paths: set[Path] = set()

    # Collect from all sources
    for p in scan_registry():
        candidate_paths.add(p.resolve())
    for p in scan_common_paths():
        candidate_paths.add(p.resolve())
    for p in scan_appdata():
        candidate_paths.add(p.resolve())

    # Scan each candidate
    instances = []
    seen = set()
    for path in sorted(candidate_paths):
        if path in seen:
            continue
        seen.add(path)
        instance = scan_instance(path)
        if instance is not None:
            instances.append(instance)

    return instances


def scan_path(path: Path) -> MO2Instance | None:
    """Scan a specific path for an MO2 instance.

    Use this when the user manually selects a folder.
    """
    path = Path(path).resolve()
    return scan_instance(path)
