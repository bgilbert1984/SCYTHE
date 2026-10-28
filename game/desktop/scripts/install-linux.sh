#!/bin/sh
# Install or remove SIGNAL GHOST for the current user. No root needed.
#
#   ./install-linux.sh [--prefix DIR]     install (default prefix: ~/.local)
#   ./install-linux.sh --uninstall [--prefix DIR]
set -eu

PREFIX="${HOME}/.local"
UNINSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

SRC="$(cd "$(dirname "$0")" && pwd)"
APP="$PREFIX/share/signal-ghost"
BIN="$PREFIX/bin/signal-ghost"
DESKTOP="$PREFIX/share/applications/signal-ghost.desktop"

if [ "$UNINSTALL" -eq 1 ]; then
  rm -rf "$APP" "$BIN" "$DESKTOP"
  echo "Removed SIGNAL GHOST from $PREFIX (saved games in your profile are kept)."
  exit 0
fi

[ -x "$SRC/signal-ghost" ] || { echo "signal-ghost binary not found next to this script" >&2; exit 1; }

mkdir -p "$APP" "$PREFIX/bin" "$PREFIX/share/applications"
rm -rf "$APP"
mkdir -p "$APP"
cp -a "$SRC/." "$APP/"
rm -f "$APP/install-linux.sh"
ln -sf "$APP/signal-ghost" "$BIN"
cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=SIGNAL GHOST
Comment=An intelligence-analysis game
Exec="$APP/signal-ghost"
Terminal=false
Categories=Game;
DESK
echo "Installed to $APP"
echo "Run: $BIN"
