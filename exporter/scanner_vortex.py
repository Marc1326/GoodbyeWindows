"""GoodbyeWindows — Vortex installation scanner (Windows).

Finds Vortex Mod Manager installations by checking:
1. Standard AppData location  (%APPDATA%\\Vortex)
2. ProgramData location       (C:\\ProgramData\\vortex)  — multi-user mode
3. Wine / Proton prefixes     (Linux development / dual-boot)
"""

import os
import sys
from pathlib import Path

from common.vortex_reader import VortexInstance, scan_vortex_instance


def find_vortex_paths() -> list[Path]:
    """Find all candidate Vortex AppData directories on the system.

    Returns:
        List of existing paths that contain a state.v2/ sub-directory.
    """
    candidates: set[Path] = set()

    if sys.platform == "win32":
        # Standard user location
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.add(Path(appdata) / "Vortex")

        # Multi-user location (Vortex --shared flag)
        candidates.add(Path("C:/ProgramData/vortex"))

        # LocalAppData fallback
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates.add(Path(local) / "Vortex")

    else:
        # Linux: native and Wine/Proton paths
        home = Path.home()
        user = os.environ.get("USER", "user")

        candidates.add(home / ".config" / "Vortex")

        # Standard Wine prefix
        wine_appdata = (
            home / ".wine" / "drive_c" / "users" / user
            / "AppData" / "Roaming" / "Vortex"
        )
        candidates.add(wine_appdata)

        # Bottles (Flatpak / native)
        bottles_dirs = [
            home / ".local" / "share" / "bottles" / "bottles",
            home / ".var" / "app" / "com.usebottles.bottles" / "data" / "bottles" / "bottles",
        ]
        for bottles_dir in bottles_dirs:
            if bottles_dir.is_dir():
                try:
                    for bottle in bottles_dir.iterdir():
                        if bottle.is_dir():
                            appdata_path = (
                                bottle / "drive_c" / "users" / user
                                / "AppData" / "Roaming" / "Vortex"
                            )
                            candidates.add(appdata_path)
                except PermissionError:
                    continue

        # Lutris Wine prefixes
        lutris_dir = home / ".local" / "share" / "lutris" / "runners" / "wine"
        if lutris_dir.is_dir():
            try:
                for prefix in lutris_dir.iterdir():
                    if prefix.is_dir():
                        appdata_path = (
                            prefix / "drive_c" / "users" / user
                            / "AppData" / "Roaming" / "Vortex"
                        )
                        candidates.add(appdata_path)
            except PermissionError:
                pass

    # Filter: must exist and have state.v2/
    return [
        p for p in candidates
        if p.is_dir() and (p / "state.v2").is_dir()
    ]


def scan_all_vortex() -> list[VortexInstance]:
    """Find and scan all Vortex installations.

    Returns:
        List of VortexInstance objects that contain at least one game.
    """
    instances: list[VortexInstance] = []

    for path in find_vortex_paths():
        instance = scan_vortex_instance(path)
        if instance is not None and instance.games:
            instances.append(instance)

    return instances


def scan_vortex_path(path: Path) -> VortexInstance | None:
    """Scan a user-selected path for a Vortex installation.

    Use this when the user manually selects a Vortex AppData folder.
    """
    return scan_vortex_instance(Path(path).resolve())
