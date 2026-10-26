#!/bin/sh
# install-virtual-edge.sh -- install the virtual Edge as an app you can launch
# from Spotlight or the Dock (macOS), updating itself from GitHub Releases.
#
#   sh install-virtual-edge.sh                          # local edged, beta channel
#   sh install-virtual-edge.sh --hub http://nuc.local:7781 [--display work]
#   sh install-virtual-edge.sh --channel stable ...     # stable releases only
#   sh install-virtual-edge.sh --dev ...                # run this checkout as is
#
# Installs the newest release on the channel into
# ~/Library/Application Support/frakpanel-virtual (layout: see launcher.py) and
# builds "~/Applications/Frakpanel Virtual Edge.app" around it, with the
# python3 found now. Each launch first installs any newer release on the
# channel (data/update.json), then starts it. Without --hub, the app runs its
# own edged through launcher.py, so it also picks up releases while it is open,
# like a panel does (README, "Releases and updates"). With --hub the page comes
# from the hub's edged, which updates on its own schedule.
#
# --dev runs virtual_edge.py from this checkout instead, and never updates.
# Any other arguments are passed through to virtual_edge.py. Run again to
# change them, or the channel.
# Log: ~/Library/Logs/frakpanel-virtual-edge.log
#
# Uninstall: rm -rf "$HOME/Applications/Frakpanel Virtual Edge.app" \
#   "$HOME/Library/Application Support/frakpanel-virtual"
set -eu

APP="$HOME/Applications/Frakpanel Virtual Edge.app"
ROOT="$HOME/Library/Application Support/frakpanel-virtual"
LOG="$HOME/Library/Logs/frakpanel-virtual-edge.log"
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"
[ -f "$DIR/virtual_edge.py" ] || { echo "virtual_edge.py not found in $DIR" >&2; exit 1; }

q() { printf "'%s'" "$(printf %s "$1" | sed "s/'/'\\\\''/g")"; }
CHANNEL=beta DEV=0 ARGS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dev) DEV=1 ;;
    --channel) [ $# -ge 2 ] || { echo "--channel needs stable or beta" >&2; exit 2; }; CHANNEL="$2"; shift ;;
    *) ARGS="$ARGS $(q "$1")" ;;
  esac
  shift
done
case "$CHANNEL" in stable|beta) ;; *) echo "--channel must be stable or beta, not $CHANNEL" >&2; exit 2 ;; esac

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
EXE="$APP/Contents/MacOS/frakpanel-virtual-edge"
if [ "$DEV" = 1 ]; then
  cat > "$EXE" <<EOF
#!/bin/sh
cd $(q "$DIR")
exec $(q "$PY") virtual_edge.py$ARGS >>"$LOG" 2>&1
EOF
  WHAT="this checkout ($DIR), no updates"
else
  mkdir -p "$ROOT/data"
  # The channel goes into update.json, keeping anything else there (a github_token, say).
  "$PY" -I - "$ROOT/data/update.json" "$CHANNEL" <<'EOF'
import json, sys
path, channel = sys.argv[1], sys.argv[2]
try:
    cfg = json.load(open(path))
except (OSError, ValueError):
    cfg = {}
cfg["channel"] = channel
json.dump(cfg, open(path, "w"))
EOF
  VER="$("$PY" "$DIR/updater.py" update "$ROOT")"
  [ -f "$ROOT/versions/$VER/virtual_edge.py" ] || { echo "no release installed in $ROOT (got $VER)" >&2; exit 1; }
  # The app updates before it starts, then runs whatever launcher.json calls current.
  cat > "$EXE" <<EOF
#!/bin/sh
R=$(q "$ROOT"); PY=$(q "$PY")
cur() { "\$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["current"])' "\$R/launcher.json"; }
"\$PY" "\$R/versions/\$(cur)/updater.py" update "\$R" >/dev/null 2>>"$LOG" || echo "update check failed; starting \$(cur)" >>"$LOG"
V="\$R/versions/\$(cur)"
cd "\$V"
exec "\$PY" virtual_edge.py --launcher "\$R/launcher.py"$ARGS >>"$LOG" 2>&1
EOF
  WHAT="frakpanel $VER, $CHANNEL channel, in $ROOT"
fi
chmod +x "$EXE"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>net.codefrak.frakpanel.virtual-edge</string>
  <key>CFBundleName</key><string>Frakpanel Virtual Edge</string>
  <key>CFBundleExecutable</key><string>frakpanel-virtual-edge</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
</dict></plist>
EOF
codesign --force -s - "$APP" 2>/dev/null || true
echo "built $APP: $WHAT"
