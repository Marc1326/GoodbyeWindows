#!/bin/bash
# Build GoodbyeWindows Importer as AppImage
#
# Prerequisites:
#   pip install pyinstaller
#   wget https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
#
# Usage:
#   ./build/build_appimage.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$ROOT_DIR/build/AppDir"
DIST_DIR="$ROOT_DIR/dist"

echo "=== GoodbyeWindows AppImage Builder ==="
echo "Root: $ROOT_DIR"

# Clean
rm -rf "$BUILD_DIR" "$DIST_DIR/GoodbyeWindows-Importer"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# Build with PyInstaller
echo "--- Building with PyInstaller ---"
cd "$ROOT_DIR"
python -m PyInstaller \
    --onedir \
    --name=GoodbyeWindows-Importer \
    --add-data="common/locales:common/locales" \
    --distpath="$BUILD_DIR/usr/bin" \
    importer/main.py

# Create AppImage structure
echo "--- Creating AppImage structure ---"
mkdir -p "$BUILD_DIR/usr/share/applications"
mkdir -p "$BUILD_DIR/usr/share/icons/hicolor/256x256/apps"

# Desktop file
cat > "$BUILD_DIR/usr/share/applications/goodbyewindows.desktop" << EOF
[Desktop Entry]
Type=Application
Name=GoodbyeWindows Importer
Comment=Import Windows mod setups into Linux mod managers
Exec=GoodbyeWindows-Importer
Icon=goodbyewindows
Categories=Game;Utility;
Terminal=false
EOF

# Symlink desktop file to root
cp "$BUILD_DIR/usr/share/applications/goodbyewindows.desktop" "$BUILD_DIR/goodbyewindows.desktop"

# Create a simple icon (placeholder — replace with real icon later)
cat > "$BUILD_DIR/usr/share/icons/hicolor/256x256/apps/goodbyewindows.svg" << 'SVGEOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <rect width="256" height="256" rx="32" fill="#2d5aa0"/>
  <text x="128" y="140" font-family="sans-serif" font-size="48" font-weight="bold" fill="white" text-anchor="middle">GBW</text>
  <text x="128" y="190" font-family="sans-serif" font-size="20" fill="#88bbff" text-anchor="middle">Importer</text>
</svg>
SVGEOF
cp "$BUILD_DIR/usr/share/icons/hicolor/256x256/apps/goodbyewindows.svg" "$BUILD_DIR/goodbyewindows.svg"

# AppRun
cat > "$BUILD_DIR/AppRun" << 'RUNEOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/GoodbyeWindows-Importer/GoodbyeWindows-Importer" "$@"
RUNEOF
chmod +x "$BUILD_DIR/AppRun"

# Build AppImage
echo "--- Building AppImage ---"
if command -v appimagetool &> /dev/null; then
    appimagetool "$BUILD_DIR" "$DIST_DIR/GoodbyeWindows-Importer-x86_64.AppImage"
    echo ""
    echo "=== AppImage built: $DIST_DIR/GoodbyeWindows-Importer-x86_64.AppImage ==="
else
    echo ""
    echo "appimagetool not found. AppDir created at: $BUILD_DIR"
    echo "Download appimagetool from: https://github.com/AppImage/AppImageKit/releases"
    echo "Then run: appimagetool $BUILD_DIR $DIST_DIR/GoodbyeWindows-Importer-x86_64.AppImage"
fi
