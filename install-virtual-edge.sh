#!/bin/sh
# install-virtual-edge.sh -- wrap virtual_edge.py in an app you can launch from
# Spotlight or the Dock (macOS).
#
#   sh install-virtual-edge.sh                          # local edged
#   sh install-virtual-edge.sh --hub http://nuc.local:7781 [--display work]
#
# Builds "~/Applications/Frakpanel Virtual Edge.app", which runs
# virtual_edge.py from this checkout with the python3 found now. Any arguments
# are passed through to virtual_edge.py. Run again to change them.
# Log: ~/Library/Logs/frakpanel-virtual-edge.log
#
# Uninstall: rm -rf "$HOME/Applications/Frakpanel Virtual Edge.app"
set -eu

APP="$HOME/Applications/Frakpanel Virtual Edge.app"
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"
[ -f "$DIR/virtual_edge.py" ] || { echo "virtual_edge.py not found in $DIR" >&2; exit 1; }

ARGS=""
for a in "$@"; do ARGS="$ARGS '$(printf %s "$a" | sed "s/'/'\\\\''/g")'"; done

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/MacOS/frakpanel-virtual-edge" <<EOF
#!/bin/sh
cd '$DIR'
exec '$PY' virtual_edge.py$ARGS >>"\$HOME/Library/Logs/frakpanel-virtual-edge.log" 2>&1
EOF
chmod +x "$APP/Contents/MacOS/frakpanel-virtual-edge"
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
echo "built $APP"
