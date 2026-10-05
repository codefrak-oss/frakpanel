#!/usr/bin/env bash
# Linux counterpart of edge-shot.ps1: screenshots the panel host's X desktop (every
# screen, the Edge included) to edge-shot.png beside this script. X11 lets an
# ssh session read the display directly, so no scheduled task is needed:
#   ssh <panel-host> '~/.local/share/frakpanel/edge-shot.sh'
#   scp <panel-host>:.local/share/frakpanel/edge-shot.png .
# Needs ImageMagick's import (apt install imagemagick).
set -euo pipefail
DISPLAY="${DISPLAY:-:0}" import -window root "$(cd "$(dirname "$0")" && pwd)/edge-shot.png"
