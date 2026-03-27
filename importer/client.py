"""GoodbyeWindows — Network transfer client (runs on Linux).

Connects to the Windows exporter's HTTP server to pull mod data.
"""

import json
import shutil
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen
from urllib.error import URLError

from common.migration_format import MigrationPackage, load_gbw


ProgressCallback = Callable[[str, int, int], None]


class TransferClient:
    """Client that connects to the Windows GoodbyeWindows server."""

    def __init__(self, host: str, port: int = 9876):
        self.base_url = f"http://{host}:{port}"
        self.token = ""

    def ping(self) -> bool:
        """Check if the server is reachable."""
        try:
            resp = self._get("/api/ping")
            return resp.get("status") == "ok"
        except (URLError, OSError, json.JSONDecodeError):
            return False

    def authenticate(self, pin: str) -> bool:
        """Authenticate with the server using a PIN."""
        try:
            data = json.dumps({"pin": pin}).encode("utf-8")
            req = Request(
                f"{self.base_url}/api/auth",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if "token" in result:
                    self.token = result["token"]
                    return True
        except (URLError, OSError, json.JSONDecodeError):
            pass
        return False

    def get_instance_info(self) -> dict:
        """Get info about the MO2 instance on the server."""
        return self._get("/api/instance")

    def get_mods_list(self) -> list[dict]:
        """Get list of all mods."""
        resp = self._get("/api/mods")
        return resp.get("mods", [])

    def download_gbw(self, output_path: Path) -> Path:
        """Download the .gbw metadata file."""
        output_path = Path(output_path)
        req = Request(
            f"{self.base_url}/api/gbw",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        with urlopen(req, timeout=60) as resp:
            output_path.write_bytes(resp.read())
        return output_path

    def download_mod(
        self,
        mod_name: str,
        target_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> int:
        """Download all files for a specific mod.

        Returns total bytes downloaded.
        """
        # Get file list
        files = self._get(f"/api/mod/{mod_name}/files")
        file_list = files.get("files", [])
        total_size = sum(f["size"] for f in file_list)
        downloaded = 0

        target_dir = Path(target_dir) / mod_name
        target_dir.mkdir(parents=True, exist_ok=True)

        for file_info in file_list:
            rel_path = file_info["path"]
            req = Request(
                f"{self.base_url}/api/mod/{mod_name}/file?path={rel_path}",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            dst = target_dir / rel_path
            dst.parent.mkdir(parents=True, exist_ok=True)

            with urlopen(req, timeout=300) as resp:
                with open(dst, "wb") as f:
                    while chunk := resp.read(65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress:
                            progress(f"{mod_name}/{rel_path}", downloaded, total_size)

        return downloaded

    def download_all_mods(
        self,
        target_dir: Path,
        progress: ProgressCallback | None = None,
        skip_separators: bool = True,
    ) -> int:
        """Download all mods to target directory.

        Returns total bytes downloaded.
        """
        mods = self.get_mods_list()
        total_size = sum(m["size_bytes"] for m in mods if not (skip_separators and m.get("is_separator")))
        downloaded = 0

        for mod in mods:
            if skip_separators and mod.get("is_separator"):
                continue
            mod_downloaded = self.download_mod(
                mod["folder_name"],
                target_dir,
                progress=lambda f, c, t: progress(f, downloaded + c, total_size) if progress else None,
            )
            downloaded += mod_downloaded

        return downloaded

    def _get(self, path: str) -> dict:
        """Make an authenticated GET request."""
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = Request(f"{self.base_url}{path}", headers=headers)
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
