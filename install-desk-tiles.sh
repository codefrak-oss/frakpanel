#!/bin/sh
# install-desk-tiles.sh -- build desk_tiles.swift into an app and start it at login (macOS).
#
#   sh install-desk-tiles.sh --hub http://nuc.local:7781
#
# Needs the Xcode command line tools (xcode-select -p prints a path). Uses
# desk_tiles.swift beside this script, or downloads it from GitHub when run on
# its own. Builds "~/Applications/Frakpanel Desk Tiles.app" (ad-hoc signed,
# with the Local Network description macOS asks permission with), and a
# LaunchAgent that starts it at login and restarts it if it crashes, but not
# after Quit. Run again to update; the hub and tile settings are kept.
#
# Uninstall:
#   launchctl bootout gui/$(id -u)/net.codefrak.frakpanel.desk-tiles
#   rm -rf ~/Library/LaunchAgents/net.codefrak.frakpanel.desk-tiles.plist "$HOME/Applications/Frakpanel Desk Tiles.app"
set -eu

ID=net.codefrak.frakpanel.desk-tiles
APP="$HOME/Applications/Frakpanel Desk Tiles.app"
AGENT="$HOME/Library/LaunchAgents/$ID.plist"
SRC_URL=https://raw.githubusercontent.com/codefrak-oss/frakpanel/main/desk_tiles.swift

HUB=""
while [ $# -gt 0 ]; do
    case "$1" in
        --hub) HUB="${2:?--hub needs a URL}"; shift 2 ;;
        *) echo "usage: $0 [--hub http://<hub>:7781]" >&2; exit 2 ;;
    esac
done

xcode-select -p >/dev/null 2>&1 || { echo "needs the Xcode command line tools: xcode-select --install" >&2; exit 1; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
HERE=$(cd "$(dirname "$0")" && pwd)
if [ -f "$HERE/desk_tiles.swift" ]; then
    cp "$HERE/desk_tiles.swift" "$TMP/"
else
    curl -fsSL "$SRC_URL" -o "$TMP/desk_tiles.swift"
fi

echo "building..."
swiftc -O "$TMP/desk_tiles.swift" -o "$TMP/frakpanel-desk-tiles"

launchctl bootout "gui/$(id -u)/$ID" 2>/dev/null || true
pkill -f "Frakpanel Desk Tiles.app/Contents/MacOS/" 2>/dev/null || true

mkdir -p "$APP/Contents/MacOS" "$HOME/Library/LaunchAgents"
cp "$TMP/frakpanel-desk-tiles" "$APP/Contents/MacOS/frakpanel-desk-tiles"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>$ID</string>
  <key>CFBundleName</key><string>Frakpanel Desk Tiles</string>
  <key>CFBundleExecutable</key><string>frakpanel-desk-tiles</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>LSUIElement</key><true/>
  <key>NSLocalNetworkUsageDescription</key><string>Loads frakpanel tiles from your hub on the local network.</string>
</dict></plist>
EOF
codesign --force -s - "$APP"

cat > "$AGENT" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$ID</string>
  <key>ProgramArguments</key><array><string>$APP/Contents/MacOS/frakpanel-desk-tiles</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key><string>Interactive</string>
</dict></plist>
EOF

[ -n "$HUB" ] && defaults write "$ID" hub "$HUB"
launchctl bootstrap "gui/$(id -u)" "$AGENT"
echo "installed: $APP (starts at login; ▦ in the menu bar)"
[ -n "$(defaults read "$ID" hub 2>/dev/null)" ] || echo "no hub set yet: the app asks for it"
echo "if macOS asks to let it find devices on your local network, allow it"
