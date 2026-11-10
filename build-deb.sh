#!/bin/sh
# Builds dist/fleece_<version>_all.deb. Needs only dpkg-deb.
#
#   /usr/share/fleece/fleece/   the Python package
#   /usr/bin/fleece             launcher
#   /usr/share/applications, /usr/share/icons   apps-menu entry and icon
set -e

APP_ID=io.github.webhookstudios.Fleece
ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(sed -n "s/^__version__ = '\(.*\)'/\1/p" "$ROOT/fleece/__init__.py")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

SHARE="$STAGE/usr/share/fleece"
install -d "$SHARE/fleece" "$STAGE/DEBIAN" "$STAGE/usr/bin" \
    "$STAGE/usr/share/applications" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps" \
    "$STAGE/usr/share/doc/fleece"

install -m 644 "$ROOT"/fleece/*.py "$ROOT/fleece/style.css" "$SHARE/fleece/"
install -m 755 "$ROOT/bin/fleece" "$STAGE/usr/bin/fleece"
install -m 644 "$ROOT/data/$APP_ID.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/"
sed -e 's|@EXEC@|fleece|' -e "s|@ICON@|$APP_ID|" "$ROOT/data/$APP_ID.desktop.in" \
    > "$STAGE/usr/share/applications/$APP_ID.desktop"
chmod 644 "$STAGE/usr/share/applications/$APP_ID.desktop"

cat > "$STAGE/usr/share/doc/fleece/copyright" <<EOT
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Fleece
Source: https://github.com/webhookstudios/fleece

Files: *
Copyright: $(date +%Y) webhookstudios
License: GPL-2+
 On Debian systems, the full text of the GNU General Public License
 version 2 can be found in /usr/share/common-licenses/GPL-2.
EOT
chmod 644 "$STAGE/usr/share/doc/fleece/copyright"

cat > "$STAGE/DEBIAN/control" <<EOT
Package: fleece
Version: $VERSION
Architecture: all
Maintainer: webhookstudios <354619278+webhookstudios@users.noreply.github.com>
Installed-Size: $(du -sk "$STAGE/usr" | cut -f1)
Depends: python3, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.7), python3-markdown-it, gir1.2-gtksource-5, gir1.2-vte-3.91
Recommends: python3-pygments, gir1.2-webkit-6.0
Section: utils
Priority: optional
Homepage: https://github.com/webhookstudios/fleece
Description: A chat window for your local Ollama models
 Fleece is a desktop chat app for Ollama. It lists the models on your
 computer, streams answers as they are written, shows formatted text and
 code, keeps your chats, and can download new models for you.
 .
 Code mode adds a small code editor with a file tree, tabs, a terminal,
 a Run button with live web preview, and an assistant that suggests
 changes you review and apply.
 .
 Ollama itself is installed separately, from https://ollama.com/download.
EOT

find "$STAGE" -type d -exec chmod 755 {} +

mkdir -p "$ROOT/dist"
dpkg-deb --root-owner-group --build "$STAGE" "$ROOT/dist/fleece_${VERSION}_all.deb"
