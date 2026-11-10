#!/bin/sh
# Installs Fleece for the current user, without root.
set -e

APP_ID=io.github.webhookstudios.Fleece
ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/fleece"

python3 - <<'PY' || { echo "Fleece needs these packages: sudo apt install python3-gi gir1.2-adw-1 python3-markdown-it python3-pygments"; exit 1; }
import gi
gi.require_version('Adw', '1')
from gi.repository import Adw
import markdown_it
PY

rm -rf "$DEST"
mkdir -p "$DEST" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps" "$HOME/.local/bin"
cp -r "$ROOT/fleece" "$DEST/fleece"
find "$DEST" -name __pycache__ -type d -exec rm -rf {} +
install -m 755 "$ROOT/bin/fleece" "$DEST/fleece-launcher"
ln -sf "$DEST/fleece-launcher" "$HOME/.local/bin/fleece"
cp "$ROOT/data/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/"
sed -e "s|@EXEC@|$DEST/fleece-launcher|" -e "s|@ICON@|$DATA/icons/hicolor/scalable/apps/$APP_ID.svg|" \
    "$ROOT/data/$APP_ID.desktop.in" > "$DATA/applications/$APP_ID.desktop"
update-desktop-database "$DATA/applications" 2>/dev/null || true

echo "Fleece is installed. Open it from the apps menu, or run: fleece"
