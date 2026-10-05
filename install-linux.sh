#!/usr/bin/env bash
# The Linux counterpart of install-windows.ps1: (re)creates and starts
# frakpanel as a systemd user unit, running from the directory this script
# sits in.
#
#   ./install-linux.sh --system   once per machine, with sudo: the X11 tools
#                                 kiosk_linux.py needs.
#   ./install-linux.sh            every deploy, no sudo: unit, restart.
#
# Unit "frakpanel" runs edged.py (kiosk browser, HTTP 7781, relay 7782) and is
# restarted 10 s after any exit, so it keeps retrying until the desktop's X
# server on DISPLAY=:0 answers. X11 only; see kiosk_linux.py for Wayland.
# Logs: edged.log beside the script, and journalctl --user -u frakpanel.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ "${1:-}" == "--system" ]]; then
  if command -v apt-get >/dev/null; then
    sudo apt-get install -y python3 xdotool wmctrl x11-utils x11-xserver-utils imagemagick
  else
    echo "not apt: install python3, xdotool, wmctrl, xprop, xrandr and (for edge-shot.sh) ImageMagick yourself" >&2
  fi
  command -v google-chrome >/dev/null || command -v chromium >/dev/null || command -v chromium-browser >/dev/null \
    || echo "no Chrome or Chromium yet: edged needs one (kiosk_linux.py BROWSERS)" >&2
  exit 0
fi

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
# The kiosk browser outlives an edged restart (edged re-attaches through the
# profile lock), as it does on Windows; so only edged itself is stopped.
cat >"$UNIT_DIR/frakpanel.service" <<UNIT
[Unit]
Description=frakpanel: edged.py
After=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$(command -v python3) $DIR/edged.py
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
