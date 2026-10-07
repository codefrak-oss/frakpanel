#!/usr/bin/env bash
# The Linux counterpart of install-windows.ps1: installs frakpanel from the
# directory this script sits in (an unpacked frakpanel-<ver>.zip or a source
# checkout) into ${XDG_DATA_HOME:-~/.local/share}/frakpanel (layout: see
# launcher.py), and (re)creates and starts it as a systemd user unit.
#
#   ./install-linux.sh --system   once per machine, with sudo: the X11 tools
#                                 kiosk_linux.py needs.
#   ./install-linux.sh            install or reinstall, no sudo: unit, restart.
#
# Unit "frakpanel" runs launcher.py, which runs the current version's edged.py
# (kiosk browser, HTTP 7781, relay 7782), restarts it, and applies updates;
# the unit itself is restarted 10 s after any exit, so it keeps retrying until
# the desktop's X server on DISPLAY=:0 answers. X11 only; see kiosk_linux.py
# for Wayland. Updates are off until data/update.json picks a channel
# (README, "Releases and updates").
# Logs: data/edged.log, data/launcher.log, and journalctl --user -u frakpanel.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ "${1:-}" == "--system" ]]; then
  if command -v apt-get >/dev/null; then
    sudo apt-get install -y python3 xdotool wmctrl x11-utils x11-xserver-utils imagemagick
  else
    echo "not apt: install python3, xdotool, wmctrl, xprop, xrandr and (for edge-shot.sh and the MCP screenshot tool) ImageMagick yourself" >&2
  fi
  command -v google-chrome >/dev/null || command -v chromium >/dev/null || command -v chromium-browser >/dev/null \
    || echo "no Chrome or Chromium yet: edged needs one (kiosk_linux.py BROWSERS)" >&2
  exit 0
fi

ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/frakpanel"
VER="$(cat "$DIR/VERSION" 2>/dev/null || echo 0.0.0)"
# A checkout's VERSION is only the next planned release: install it as a dev
# build, which never self-updates (updater.py).
[[ -e "$DIR/.git" ]] && VER="$VER-dev"
VDIR="$ROOT/versions/$VER"
mkdir -p "$ROOT/versions" "$ROOT/data"
systemctl --user stop frakpanel 2>/dev/null || true
if [[ "$DIR" != "$VDIR" ]]; then
  rm -rf "$VDIR"
  mkdir -p "$VDIR"
  (cd "$DIR" && tar --exclude=.git --exclude=__pycache__ --exclude=edge_layout.json \
     --exclude=edge_relays.json --exclude=edged.log --exclude=edge-shot.png \
     --exclude=local_tiles.json -cf - .) | (cd "$VDIR" && tar -xf -)
  echo "$VER" >"$VDIR/VERSION"
fi
cp "$DIR/launcher.py" "$DIR/edge-shot.sh" "$ROOT/"
if [[ -f "$DIR/local_tiles.json" && ! -f "$ROOT/data/local_tiles.json" ]]; then
  cp "$DIR/local_tiles.json" "$ROOT/data/"
fi
printf '{"current": "%s", "pending": false}' "$VER" >"$ROOT/launcher.json"

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
# The kiosk browser outlives an edged restart (edged re-attaches through the
# profile lock), as it does on Windows; so only launcher.py is signalled, and
# it stops edged itself.
cat >"$UNIT_DIR/frakpanel.service" <<UNIT
[Unit]
Description=frakpanel: launcher.py (edged.py)
After=network-online.target

[Service]
WorkingDirectory=$ROOT
ExecStart=$(command -v python3) $ROOT/launcher.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
Environment=DISPLAY=:0
KillMode=process

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --quiet frakpanel
systemctl --user restart frakpanel
sleep 8
systemctl --user --no-pager status frakpanel | head -n 12
echo "frakpanel $VER installed in $ROOT"
