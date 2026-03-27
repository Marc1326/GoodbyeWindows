"""GoodbyeWindows — Network transfer server (runs on Windows).

Starts a simple HTTP server that the Linux importer can connect to.
Uses a PIN-based authentication to prevent unauthorized access.

Protocol:
  GET  /api/ping              → {"status": "ok", "tool": "GoodbyeWindows", "version": "1.0.0"}
  POST /api/auth              → body: {"pin": "1234"} → {"token": "abc..."}
  GET  /api/instance          → instance metadata (requires token)
  GET  /api/mods              → list of mods with metadata (requires token)
  GET  /api/mod/<name>/files  → list of files in a mod (requires token)
  GET  /api/mod/<name>/file?path=<rel_path>  → download a specific file (requires token)
  GET  /api/gbw               → download the .gbw metadata file (requires token)
"""

import io
import json
import random
import secrets
import socket
import string
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse, parse_qs

from common.mo2_reader import MO2Instance
from common.migration_format import create_package_from_mo2, save_gbw

DEFAULT_PORT = 9876


def generate_pin() -> str:
    """Generate a 4-digit PIN."""
    return "".join(random.choices(string.digits, k=4))


def get_local_ip() -> str:
    """Get the local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class TransferHandler(BaseHTTPRequestHandler):
    """HTTP request handler for mod transfer."""

    instance: MO2Instance = None
    pin: str = ""
    auth_token: str = ""
    on_event: Callable = None

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path):
        if not file_path.exists():
            self._send_json({"error": "not found"}, 404)
            return
        size = file_path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
        self.end_headers()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                self.wfile.write(chunk)

    def _check_auth(self) -> bool:
        token = self.headers.get("Authorization", "").replace("Bearer ", "")
        if token != TransferHandler.auth_token:
            self._send_json({"error": "unauthorized"}, 401)
            return False
        return True

    def _notify(self, event: str, **kwargs):
        if TransferHandler.on_event:
            TransferHandler.on_event(event, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/ping":
            self._send_json({
                "status": "ok",
                "tool": "GoodbyeWindows",
                "version": "1.0.0",
            })
            return

        if not self._check_auth():
            return

        instance = TransferHandler.instance
        if instance is None:
            self._send_json({"error": "no instance loaded"}, 500)
            return

        if path == "/api/instance":
            self._send_json({
                "game_name": instance.game_name,
                "game_path": instance.game_path,
                "mod_count": instance.mod_count,
                "profile_count": instance.profile_count,
                "profiles": [p.name for p in instance.profiles],
                "path": str(instance.path),
            })
            self._notify("client_info_request")
            return

        if path == "/api/mods":
            mods = []
            for meta in instance.mod_meta.values():
                mods.append({
                    "folder_name": meta.folder_name,
                    "display_name": meta.display_name,
                    "nexus_id": meta.nexus_id,
                    "version": meta.version,
                    "author": meta.author,
                    "is_separator": meta.is_separator,
                    "size_bytes": meta.size_bytes,
                    "enabled": meta.enabled,
                })
            self._send_json({"mods": mods})
            return

        if path == "/api/gbw":
            package = create_package_from_mo2(instance)
            buf = io.BytesIO()
            import zipfile
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                from dataclasses import asdict
                zf.writestr("manifest.json", json.dumps(
                    asdict(package.manifest), indent=2, ensure_ascii=False
                ))
                zf.writestr("mods.json", json.dumps(
                    [asdict(m) for m in package.mods], indent=2, ensure_ascii=False
                ))
                zf.writestr("profiles.json", json.dumps(
                    [asdict(p) for p in package.profiles], indent=2, ensure_ascii=False
                ))
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", 'attachment; filename="migration.gbw"')
            self.end_headers()
            self.wfile.write(data)
            self._notify("gbw_downloaded")
            return

        # /api/mod/<name>/files
        if path.startswith("/api/mod/") and path.endswith("/files"):
            mod_name = path[len("/api/mod/"):-len("/files")]
            mod_dir = instance.mods_dir / mod_name
            if not mod_dir.exists():
                self._send_json({"error": "mod not found"}, 404)
                return
            files = []
            for f in mod_dir.rglob("*"):
                if f.is_file():
                    files.append({
                        "path": str(f.relative_to(mod_dir)),
                        "size": f.stat().st_size,
                    })
            self._send_json({"mod": mod_name, "files": files})
            return

        # /api/mod/<name>/file?path=<rel>
        if path.startswith("/api/mod/") and path.endswith("/file"):
            mod_name = path[len("/api/mod/"):-len("/file")]
            rel_path = params.get("path", [""])[0]
            if not rel_path:
                self._send_json({"error": "path required"}, 400)
                return
            file_path = instance.mods_dir / mod_name / rel_path
            # Prevent path traversal
            try:
                file_path.resolve().relative_to(instance.mods_dir.resolve())
            except ValueError:
                self._send_json({"error": "invalid path"}, 403)
                return
            self._notify("file_transfer", mod=mod_name, file=rel_path)
            self._send_file(file_path)
            return

        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/auth":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            if body.get("pin") == TransferHandler.pin:
                TransferHandler.auth_token = secrets.token_hex(32)
                self._send_json({"token": TransferHandler.auth_token})
                client_ip = self.client_address[0]
                self._notify("authenticated", client=client_ip)
            else:
                self._send_json({"error": "wrong pin"}, 403)
            return

        self._send_json({"error": "not found"}, 404)


class TransferServer:
    """Manages the HTTP transfer server."""

    def __init__(
        self,
        instance: MO2Instance,
        port: int = DEFAULT_PORT,
        on_event: Callable | None = None,
    ):
        self.instance = instance
        self.port = port
        self.pin = generate_pin()
        self.ip = get_local_ip()
        self.on_event = on_event
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        """Start the transfer server in a background thread."""
        TransferHandler.instance = self.instance
        TransferHandler.pin = self.pin
        TransferHandler.auth_token = ""
        TransferHandler.on_event = self.on_event

        self._server = HTTPServer(("0.0.0.0", self.port), TransferHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the transfer server."""
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
