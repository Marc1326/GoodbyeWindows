"""Build script for Windows .exe (PyInstaller).

Usage:
    python build/build_exe.py exporter
    python build/build_exe.py importer
"""

import subprocess
import sys
from pathlib import Path


def build(target: str):
    root = Path(__file__).resolve().parent.parent

    if target == "exporter":
        entry = root / "exporter" / "main.py"
        name = "GoodbyeWindows-Exporter"
    elif target == "importer":
        entry = root / "importer" / "main.py"
        name = "GoodbyeWindows-Importer"
    else:
        print(f"Unknown target: {target}")
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        f"--name={name}",
        f"--add-data={root / 'common' / 'locales'}:common/locales",
        str(entry),
    ]

    print(f"Building {name}...")
    print(f"  Command: {' '.join(cmd)}")

    result = subprocess.run(cmd, cwd=str(root))
    if result.returncode == 0:
        print(f"\n  {name} built successfully!")
        print(f"  Output: dist/{name}.exe")
    else:
        print(f"\n  Build failed with code {result.returncode}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python build_exe.py [exporter|importer]")
        sys.exit(1)
    build(sys.argv[1])
