# Building a frakpanel tile

The panel shows three tiles side by side. A tile is a web page served from
whatever machine holds its state (usually a laptop). The laptop dials *out*
to the panel host and the host's kiosk browser renders the page through that
connection, so nothing on the laptop is exposed and inbound firewalls don't
matter. `demo_tile.py` is a complete working tile; `edgerelay.py` is the
connection. Nothing else is needed.

**Examples, at a glance:**

| Example | Shows |
|---|---|
| `examples/demo_tile.py` | A complete working tile served from a laptop: a page, an SSE stream, and a timed tap round trip. |
| `examples/self_hosted_tile.py` | A tile that polls a self-hosted HTTP API server-side and renders its data, with a readable error state when the upstream is down. See "Tiles backed by a self-hosted web server" below. |
| `examples/uebersicht/edge-tile-preview.jsx` | An Übersicht widget that previews a tile at slot size on a Mac desktop. |
| `local_tiles.example.json` | Two fixed picker entries (`url`/`title`) for pages the panel host reaches directly, no relay involved. |

**This file travels as a kit** with those two files beside it, for a machine
that doesn't have the repo (a locked-down work laptop, say):

```
mkdir -p /tmp/frakpanel-tiles && cp TILES.md edgerelay.py examples/demo_tile.py /tmp/frakpanel-tiles/
```

Both scripts are standard-library Python, so nothing gets installed on the
other side. Paths below are relative to wherever the kit lives; call that
`$KIT`. Re-send the kit whenever `edgerelay.py` changes: a relay protocol
change without a re-send strands that laptop's tiles.

`<panel-host>` below is the name or address of the machine the panel is
plugged into. Set it once and the relay picks it up:

```
export FRAKPANEL_HOST=<panel-host>
```

## Connectivity

Everything is **outbound from the laptop to the panel host**, plain TCP:

| port | who dials | what | needed |
|---|---|---|---|
| 7782 | laptop -> host | the relay (`edgerelay.py`); carries the tile's bytes | yes |
| 7781 | laptop -> host | edged's HTTP API (`/layout`, `/slot`, `/tiles`) | only for curl; the panel's picker does the same by touch |

Preflight, in this order:
```
python3 -c 'import socket,os;print(socket.gethostbyname(os.environ["FRAKPANEL_HOST"]))'   # 1. does the name resolve here?
nc -vz $FRAKPANEL_HOST 7782                                                             # 2. can we reach the relay?
curl -s http://$FRAKPANEL_HOST:7781/tiles                                               # 3. optional: the API, and what the picker sees
```
- **If 1 fails** (a work VPN's resolver doesn't know your LAN's names), use
  the host's IP address instead.
- **If 2 fails** the laptop isn't on the same LAN or a VPN owns the route.
  Check whether the LAN's subnet is being pulled into the tunnel
  (`netstat -rn`). The relay works over any route that reaches the host, but
  a tunnel detour adds latency to every tap.
- **Names**: prefix the relay name with the machine (`air-`, `work-`). Two
  relays with the same name fight, the newest wins, and the loser retries
  every 30 s, so the tile flickers between them.
- **Away and back**: the relay client pings every 5 s and is dropped after
  15 s silent; it reconnects with backoff (1 s doubling to 30 s), so after
  sleep the tile is back within half a minute. While it's away the slot shows
  the clock with "waiting for <name>" and the picker lists the tile greyed
  "away". Nothing needs restarting on the host.
- **Nothing about the tile itself crosses the network.** It binds
  `127.0.0.1`; only the relay's TCP stream leaves the laptop. Anything the
  tile can reach on the laptop (a corporate API, a VPN-only host, a local
  file) it can show on the panel, which is the point of hosting work tiles
  on the work laptop.
- **No auth anywhere.** edged trusts the LAN. Don't put a tile on the panel
  that shouldn't be readable by whoever is at the desk.

## The tile

A tile is a web page that fills one of the panel's 3 fixed slots. It's served
from whatever machine holds its state and reaches the panel host through the
relay. The host side (`edged.py`, not in the kit) is already running, so a
tile session shouldn't need to touch it.

**The contract**
- **Exactly 812x720 CSS px**, always: 2560 minus the 120px control column
  minus two 2px slot dividers, split three ways. Scale is 100%, so CSS px =
  panel px. Nothing spans slots, so don't build for any other size.
- **Touch, not mouse.** Set `cursor:none; user-select:none` (Windows really
  does draw a mouse pointer on the panel, and it stays where the last tap
  was), and support `?cursor=1` to show it again for previews on a desktop
  (`html.cursor,html.cursor *{cursor:auto}` plus one line of JS, see
  `demo_tile.py`). Use `touch-action:manipulation` on buttons, and use
  `pointerdown` for instant response. Make tap targets big: at least ~100px.
  The panel is about 0.135mm/px, so a 100x72 button is about 13x10mm, and
  ~11mm buttons proved too small. No hover states, since nothing hovers.
- **Framing must be allowed**: no `X-Frame-Options` or `frame-ancestors`
  CSP, or the slot renders blank.
- **Plain http, no secure context**: no clipboard API, service workers,
  etc. Absolute paths (`/api/x`) are fine, because each relay name gets its
  own origin on the host.
- **Bind to 127.0.0.1**, never the LAN. The relay is the only way in.
  SSE, streaming and WebSockets pass through the relay untouched.
- **Ignore the query string when routing** (`self.path.split("?", 1)[0]`).
  Tiles get called with `?cursor=1`, and edged's own fallback uses
  `/home?waiting=<name>`.
- Dark background (the panel sits beside black slots and the column
  `#0d0f14`). `demo_tile.py` is a complete working example of all of
  the above (a page, SSE, and a timed tap round trip).

**Run it**
```
python3 path/to/your_tile.py &                      # serves 127.0.0.1:<port>
python3 $KIT/edgerelay.py --name <relay-name> --target 127.0.0.1:<port> --title "<Picker title>"
curl -XPOST http://$FRAKPANEL_HOST:7781/slot -d '{"slot":0,"url":"relay://<relay-name>/"}'
curl http://$FRAKPANEL_HOST:7781/layout          # see what's in each slot
curl http://$FRAKPANEL_HOST:7781/tiles           # what the panel's picker offers
```
- One relay client exposes one target, so **each tile gets its own relay
  name** (e.g. `air-board`, `air-mixer`). Two sessions using the same name
  fight over it. Prefix the name with the machine (`air-`, `work-`) so two
  laptops never collide.
- `--title` is what the panel's **tile picker** (the column's `tiles`
  button) shows. Once the relay has connected, the tile can be put in any
  slot from the panel itself, so the `curl .../slot` is optional.
- Slots are 0, 1, 2, left to right. There are no claims, so the last
  choice wins (a POST or a tap on the picker): check `/layout` before taking
  a slot someone else is using. If the relay client stops, the slot shows
  "waiting for <name>" on the clock, not an error, and the picker lists the
  tile greyed as "away" until edged restarts.
- The relay client and tile run in the foreground of your session. For
  keeps, see "Staying running" below.

**See it**
Develop in a Chrome app window, the same engine as the panel's kiosk. The
panel is the target that counts.
- **Develop and touch-emulate it** in a Chrome app window (macOS shown):
  ```
  open -na "Google Chrome" --args --app='http://127.0.0.1:<port>/?cursor=1' --window-size=812,720 --user-data-dir=/tmp/frakpanel-tile-chrome
  ```
  `?cursor=1` keeps the pointer visible so you can see where you are about to
  click. Clicks fire `pointerdown` as a mouse. For real touch events (no hover,
  drag scrolls or selects the way a finger would), open DevTools, then the
  device toolbar. Under Dimensions > Edit… > Add custom device, use 812x720,
  device pixel ratio 1, type "Desktop (touch)". Whether targets are big enough
  for a finger (about 74px for a 10mm fingertip) can only be judged on the
  panel.
- **Optionally, watch it** on a Mac desktop: the Übersicht widget
  `examples/uebersicht/edge-tile-preview.jsx` (in the repo, not the kit)
  frames the tile's local URL. Install and settings are in its header
  comment; set `TILE_URL` to your tile. It lays the page out at 812x720 and
  scales it to 0.6. It checks layout and framing, and clicks reach the tile
  (as mouse events). It renders in WebKit, not the panel's Chrome.
- Locally, at true size, as a PNG:
  ```
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars \
    --window-size=812,720 --virtual-time-budget=3000 --screenshot=tile.png http://127.0.0.1:<port>/
  ```
  If the page has a `setInterval` (every live tile does), Chrome writes the PNG
  and then never exits. Run it in the background, wait for the file, and kill
  the process by its `--user-data-dir`. A dedicated `--user-data-dir` also
  keeps it from attaching to your running Chrome.
- On the real panel (whole 2560x720, including the column), with a Windows
  host:
  ```
  ssh <panel-host> 'schtasks /run /tn frakpanel-shot'
  sleep 5; scp '<panel-host>:AppData/Local/frakpanel/edge-shot.png' .
  ```
  (On a Linux host, run `edge-shot.sh` over ssh.) Don't trust it
  while someone is connected over RDP: the capture is of the RDP-sized
  desktop, not the panel.
- The **reload** button in the column reloads all slots after you change a
  page.

**Gotchas that cost time**, so they don't cost it twice:
- **The panel does show a mouse pointer**, contrary to the assumption that
  Windows hides it under touch, hence `cursor:none` in the contract above.
- **Redeploying a tile does not update the panel.** The kiosk's iframe keeps
  the page it has until the slot's URL changes. Tap `reload` in the column,
  or clear the slot and set it back.
- **Headless `--screenshot` hangs on live pages** (see the PNG recipe above).
- **Shrinking an iframe preview with `transform: scale()` silently swallows
  every click** into the framed tile in WebKit. CSS `zoom` on the container
  passes clicks through. That is why the Übersicht widget uses `zoom`.
- **Übersicht doesn't notice a newly symlinked widget** until Übersicht
  itself is restarted. `pkill -x Übersicht` matches nothing (the "Ü" in its
  process name); use its menu bar item or kill the PID from
  `pgrep -lf bersicht`.
- **A JSX fragment (`<>`) in an Übersicht widget needs
  `import { React } from "uebersicht"`**, or the widget shows "Can't find
  variable: React".
- **A port can be silently shared on Windows.** Python's `http.server` sets
  `SO_REUSEADDR`, and on Windows that lets a test tile bind a port a relay
  listener (7790 and up) already holds. Pick ports away from that range for
  tiles served on the host, or set `allow_reuse_address = False`.

**Don't** restart edged, change the layout format, or edit `edged.py` /
`edgerelay.py` from a tile session.


## Tiles backed by a self-hosted web server

The common case the contract above doesn't spell out: the data lives on a
LAN service (a NAS, Grafana, Jellyfin, a home-grown API), not on the laptop
running the relay. Two routes, depending on whether that service already
serves a page you want shown as-is, or you want to build a small tile on top
of its API. (Route M below is Route A without the file edit and restart:
register the URL through edged's MCP server.)

### Route A: frame the page directly, no relay

If the self-hosted service already serves a page that's fine at slot size,
skip the relay entirely and point a slot straight at it. This only works
when the panel host can reach the service directly (same LAN); it's not for
a laptop's tiles.

1. Copy `local_tiles.example.json` to `local_tiles.json` **beside `edged.py`**
   (the install's folder when installed: `%LOCALAPPDATA%\frakpanel` or
   `~/.local/share/frakpanel`; the data subfolder there if running
   installed; beside the scripts when run from a checkout -- see
   `README.md`'s Configuration table).
2. Each entry is `{"url": "...", "title": "..."}`: `url` is anything the
   panel host can reach directly (`http://grafana.lan:3000/d/desk?kiosk`),
   `title` is what the picker shows.
3. Restart `edged` so it re-reads the file. The entry then appears in the
   picker like any other tile.

**If the slot renders blank**, the page is refusing to be framed: check its
response headers for `X-Frame-Options: DENY`/`SAMEORIGIN` or a
`Content-Security-Policy` with `frame-ancestors`. Both block embedding in an
iframe, which is exactly what the shell page does with every slot. Fix it on
the service side (most self-hosted apps have a setting for this, since
they're usually the ones that *want* to be embedded):
- Grafana: `[security] allow_embedding = true` in `grafana.ini`, and drop any
  `frame-ancestors` CSP it sets, or widen it to include the panel host.
- A reverse proxy in front of the service: check it isn't adding
  `X-Frame-Options` itself (nginx's `proxy_hide_header` / a custom `add_header`).
- Something you don't control the config of: it can't be framed; use Route B
  instead, fetching its API from a small tile you do control.

### Route M: register the page through MCP, no file, no restart

Same kind of tile as Route A (a URL the panel host frames directly), but
registered by an MCP client instead of by editing `local_tiles.json`. `edged`
serves an MCP server on its own port:

- **Address:** `http://<panel-host>:7781/mcp`
- **Transport:** Streamable HTTP, stateless: every request is a `POST` of one
  JSON-RPC message answered with `application/json`. No session id, no SSE
  stream (`GET /mcp` answers 405).
- **Protocol versions:** `2026-07-28` (preferred), `2025-06-18` and
  `2025-03-26`. A 2026-07-28 client sends no `initialize`: each request names
  the version in `params._meta["io.modelcontextprotocol/protocolVersion"]` and
  in the `MCP-Protocol-Version` header, with `Mcp-Method` (and `Mcp-Name` for
  `tools/call`) headers matching the body, else `400`; `server/discover`
  lists the versions. An older client's `initialize` negotiates `2025-06-18`
  or `2025-03-26`; a POST with no `MCP-Protocol-Version` header (like the
  curl below) is served as an older client.
- **No authentication.** Like the rest of `edged`'s API, it trusts the LAN:
  anything that can reach port 7781 can add or remove tiles and change the slots. Don't expose
  the port beyond the LAN.

Tools:

| tool | arguments | does |
|---|---|---|
| `register_tile` | `url`, `title` | adds the tile (or retitles it if the `url` is already registered). `url` must start with `http://` or `https://` and `title` must not be empty, else the call returns a tool error and nothing is registered. |
| `frakpanel_guide` | none | returns an intro to frakpanel and its MCP tools, this guide (read from the installed version) and the source of `examples/self_hosted_tile.py`; an MCP client on another machine should call it first |
| `list_tiles` | none | the tiles registered through MCP |
| `remove_tile` | `url` | removes that registered tile |
| `get_layout` | none | `{"slots": [url, url, url]}`: what the panel shows in slots 0-2 (`""` is the clock), as `GET /tiles` reports them |
| `set_slot` | `slot` (0-2), `url` | changes what the panel shows: puts `url` (`""` for the clock, `relay://<name>/<path>` or an http(s) URL) in that slot, exactly like `POST /slot`, and returns the new `{"slots": [...]}`. A bad slot or url returns a tool error and leaves the layout unchanged. |
| `screenshot` | none | a PNG of what the panel host's screen shows right now (every screen, the Edge included), as an MCP image content item (`type` `image`, `mimeType` `image/png`, base64 `data`). On Linux it needs ImageMagick's `import` (`install-linux.sh` installs it); on Windows it uses PowerShell, like `edge-shot.ps1`. A failed capture returns a tool error. |

Connect a client, e.g. Claude Code:

```sh
claude mcp add --transport http frakpanel http://<panel-host>:7781/mcp
```

then ask it to register `https://engine.k8s.lund/frakpanel/tiles/ai-accounts`
titled "AI accounts". Or, by hand:

```sh
curl -XPOST http://<panel-host>:7781/mcp -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
  "params":{"name":"register_tile","arguments":{"url":"https://engine.k8s.lund/frakpanel/tiles/ai-accounts","title":"AI accounts"}}}'
```

The tile is in `GET /tiles` (and the picker) at once, after the clock and the
`local_tiles.json` entries. Registrations are kept in `mcp_tiles.json` in the
data folder (beside `edge_layout.json`), so they survive restarts; MCP only
manages that file, never `local_tiles.json`. The framing caveats of Route A
apply.

### Route B: a small tile that fetches the API server-side

When the service only exposes a JSON/HTTP API (no presentable page), or its
page can't be made embeddable, build a small tile that fetches the API from
Python and renders the result as HTML -- not from the browser. Fetching from
the browser would be a cross-origin request from the tile's own
127.0.0.1-only origin to the LAN service, which CORS blocks unless the
service happens to send the right headers; fetching server-side sidesteps
that, and keeps the service's address and any credentials off the page
`view-source:` would show.

`examples/self_hosted_tile.py` is a complete example of this: stdlib only, a
background thread polls a configurable upstream URL on an interval and
caches the latest result (or the error, if the upstream didn't answer), and
the page polls `/state` on *this* tile and renders whatever it finds --
still following the contract above (812x720, `cursor:none`, query string
ignored when routing, `?cursor=1` honoured).

```
export SELF_HOSTED_TILE_UPSTREAM=http://nas.example.lan:8080/api/status
python3 examples/self_hosted_tile.py &                 # serves 127.0.0.1:8791, polls the upstream
python3 edgerelay.py --host <panel-host> --name air-nas --target 127.0.0.1:8791 --title "NAS"
curl -XPOST http://<panel-host>:7781/slot -d '{"slot":0,"url":"relay://air-nas/"}'
```

- `--upstream`/`$SELF_HOSTED_TILE_UPSTREAM` is the only thing to change for a
  different service; `--port` and `--interval` (poll period) are optional.
- The browser never talks to the upstream: it only ever calls back to this
  tile's own `/state`, which is why there's no CORS problem to work around.
- If the upstream is unreachable, the tile keeps serving 200s and shows a
  readable error on the page instead of going blank -- a down LAN service
  shouldn't make the slot look like a relay/framing problem.


## Staying running (launchd)

On a Mac, the relay and the tile each get a launchd agent (RunAtLoad +
KeepAlive). Two agents, not one script: launchd restarts whichever one dies.
For a tile named `work-cal` on port 8792:

```
KIT=$HOME/frakpanel-tiles; DATA="$HOME/Library/Application Support/frakpanel"; mkdir -p "$DATA"
agent() {  # agent <label> <program> [args...]
    local label=$1; shift; local args=""
    for a in "$@"; do args+="<string>$a</string>"; done
    cat > "$HOME/Library/LaunchAgents/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>$label</string>
    <key>ProgramArguments</key><array>$args</array>
    <key>WorkingDirectory</key><string>$KIT</string>
    <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>StandardOutPath</key><string>$DATA/$label.log</string>
    <key>StandardErrorPath</key><string>$DATA/$label.log</string>
</dict></plist>
PLIST
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$label.plist"
}
agent com.example.frakpanel-work-cal        /usr/bin/python3 $KIT/work_cal_tile.py
agent com.example.frakpanel-work-cal-relay  /usr/bin/python3 $KIT/edgerelay.py --host <panel-host> --name work-cal --target 127.0.0.1:8792 --title "Work calendar"
```
- After editing the tile, `launchctl kickstart -k gui/$(id -u)/com.example.frakpanel-work-cal`,
  then clear and re-set the slot (or tap reload in the column): the kiosk
  keeps the page it has until the slot's URL changes.
- Use a real interpreter path (`/usr/bin/python3`, or a venv's) rather than
  whatever `python3` is on the shell's PATH; launchd's PATH is short. For the
  same reason pass `--host` explicitly: launchd doesn't see your shell's
  `FRAKPANEL_HOST`.
- Order the relay after the tile so the first `open` finds something to dial;
  it doesn't matter much, since a failed stream just shows a blank until the
  next load.

## Handing a tile to a coding agent on another machine

Give it this file and say what the tile should show. The checklist it should
follow:

1. Preflight (above). Pick a machine-prefixed relay name and an unused
   local port (give each machine its own range, e.g. 8790-8799 on one laptop
   and 8800 up on the next).
2. Copy `demo_tile.py` to `<name>_tile.py` beside it, keep its `Handler`
   shape (query string stripped, `Content-Length`, `no-store`), replace the
   page. Keep the contract, especially 812x720 and `cursor:none`.
3. Run tile + relay in the foreground, check `/tiles` lists it, put it in a
   slot from the panel's picker (or the curl), look at it on the panel
   ("See it" above). Adjust tap sizes on the panel, not in Chrome.
4. Two launchd agents (above). Confirm it survives a laptop sleep/wake.
5. Keep the tile with the kit, and tell whoever runs the panel the relay
   name and title.
