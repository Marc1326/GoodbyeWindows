"""GoodbyeWindows — Source detector (Linux).

Detects:
1. NTFS partitions (Windows dual-boot) with MO2 instances
2. USB drives / external HDDs with exported data
3. .gbw files
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from common.mo2_reader import scan_instance, MO2Instance


@dataclass
class NTFSPartition:
    """A detected NTFS partition."""
    device: str        # /dev/sda2
    mount_point: str   # /mnt/windows
    size: str          # 500G
    label: str = ""
    mo2_instances: list[MO2Instance] = field(default_factory=list)


def find_ntfs_partitions() -> list[NTFSPartition]:
    """Find all mounted NTFS partitions.

    Uses lsblk to find NTFS filesystems and checks
    if they're mounted. Also tries to find MO2 instances on them.
    """
    partitions = []

    try:
        result = subprocess.run(
            ["lsblk", "-o", "NAME,FSTYPE,SIZE,MOUNTPOINT,LABEL", "-J"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return partitions

        import json
        data = json.loads(result.stdout)

        for device in data.get("blockdevices", []):
            _check_device(device, partitions)

    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass

    return partitions


def _check_device(device: dict, partitions: list[NTFSPartition]):
    """Recursively check a device and its children for NTFS partitions."""
    fstype = device.get("fstype", "")
    mountpoint = device.get("mountpoint", "")

    if fstype and "ntfs" in fstype.lower() and mountpoint:
        part = NTFSPartition(
            device=f"/dev/{device['name']}",
            mount_point=mountpoint,
            size=device.get("size", ""),
            label=device.get("label", ""),
        )
        # Scan for MO2 instances
        part.mo2_instances = scan_ntfs_for_mo2(Path(mountpoint))
        partitions.append(part)

    # Check children (partitions of a disk)
    for child in device.get("children", []):
        _check_device(child, partitions)


def scan_ntfs_for_mo2(mount_path: Path) -> list[MO2Instance]:
    """Scan a mounted NTFS partition for MO2 instances.

    Checks common Windows paths where MO2 is typically installed.
    """
    instances = []
    if not mount_path.exists():
        return instances

    # Common paths on Windows
    search_paths = [
        # Direct on drive root
        "Mod Organizer 2",
        "MO2",
        "Modding/MO2",
        "Games/MO2",
        "Modding/Mod Organizer 2",
        # Under Users
        "Users",
        # Program Files
        "Program Files/Mod Organizer 2",
        "Program Files (x86)/Mod Organizer 2",
    ]

    # Check common paths
    for rel in search_paths:
        candidate = mount_path / rel
        if not candidate.exists():
            continue

        if (candidate / "ModOrganizer.ini").exists():
            inst = scan_instance(candidate)
            if inst:
                instances.append(inst)
        else:
            # Check one level deeper (game-specific instances)
            try:
                for sub in candidate.iterdir():
                    if sub.is_dir() and (sub / "ModOrganizer.ini").exists():
                        inst = scan_instance(sub)
                        if inst:
                            instances.append(inst)
            except PermissionError:
                continue

    # Also scan AppData locations for all users
    users_dir = mount_path / "Users"
    if users_dir.exists():
        try:
            for user_dir in users_dir.iterdir():
                if not user_dir.is_dir():
                    continue
                appdata_mo2 = user_dir / "AppData" / "Local" / "ModOrganizer"
                if appdata_mo2.exists():
                    for sub in appdata_mo2.iterdir():
                        if sub.is_dir() and (sub / "ModOrganizer.ini").exists():
                            inst = scan_instance(sub)
                            if inst:
                                instances.append(inst)
        except PermissionError:
            pass

    return instances


def find_usb_drives() -> list[Path]:
    """Find mounted USB drives and external HDDs."""
    drives = []

    # Check /media/<user>/
    media = Path("/media")
    if media.exists():
        for user_dir in media.iterdir():
            if user_dir.is_dir():
                for drive in user_dir.iterdir():
                    if drive.is_dir():
                        drives.append(drive)

    # Check /run/media/<user>/
    run_media = Path("/run/media")
    if run_media.exists():
        for user_dir in run_media.iterdir():
            if user_dir.is_dir():
                for drive in user_dir.iterdir():
                    if drive.is_dir():
                        drives.append(drive)

    # Check /mnt/
    mnt = Path("/mnt")
    if mnt.exists():
        for drive in mnt.iterdir():
            if drive.is_dir():
                drives.append(drive)

    return drives


def find_gbw_on_drive(drive_path: Path) -> list[Path]:
    """Find .gbw files on a drive (non-recursive, top 2 levels only)."""
    gbw_files = []

    try:
        # Level 0
        for f in drive_path.iterdir():
            if f.is_file() and f.suffix.lower() == ".gbw":
                gbw_files.append(f)
            elif f.is_dir():
                # Level 1
                try:
                    for f2 in f.iterdir():
                        if f2.is_file() and f2.suffix.lower() == ".gbw":
                            gbw_files.append(f2)
                except PermissionError:
                    continue
    except PermissionError:
        pass

    return gbw_files


def find_export_folder(drive_path: Path) -> Path | None:
    """Find a GoodbyeWindows full export folder on a drive.

    Looks for a folder containing 'migration.gbw' and 'mods/'.
    """
    try:
        for f in drive_path.iterdir():
            if f.is_dir():
                if (f / "migration.gbw").exists() and (f / "mods").is_dir():
                    return f
                # One level deeper
                try:
                    for f2 in f.iterdir():
                        if f2.is_dir():
                            if (f2 / "migration.gbw").exists() and (f2 / "mods").is_dir():
                                return f2
                except PermissionError:
                    continue
        # Also check root
        if (drive_path / "migration.gbw").exists() and (drive_path / "mods").is_dir():
            return drive_path
    except PermissionError:
        pass

    return None
