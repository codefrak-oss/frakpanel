# frakpanel

Turn a [Corsair Xeneon Edge](https://www.corsair.com/us/en/p/monitors/cc-9011306-ww/xeneon-edge-14-5-lcd-touchscreen-cc-9011306-ww)
(the 14.5" 2560x720 touch strip) into a shared control surface for several
computers, without iCUE.

The panel stays plugged into one small always-on machine (the **panel host**;
ours is an Intel NUC). That machine runs a kiosk browser on the panel and
nothing else of interest. Every other computer on the desk serves its own
**tiles**: ordinary web pages, sized for one third of the panel, that show
whatever that computer knows and take taps.

```
+---------------------- Xeneon Edge, 2560x720 ----------------------+
|                    |                    |                    | 12 |
|       slot 0       |       slot 1       |       slot 2       | :0 |
|      812x720       |      812x720       |      812x720       | 7  |
|                    |                    |                    |    |
|  a page from the   |  a page from the   |  a page served on  | co |
|  personal laptop   |  work laptop       |  the panel host    | lu |
|                    |                    |                    | mn |
+--------------------------------------------------------------------+
```

- **The host owns the hardware; the meaning stays where the state is.** The
  host is told which URL goes in which slot. It never knows what a button
  means.
- **Touch needs no protocol.** A tap lands in whichever page is under the
  finger, and that page calls back to its own server.
- **Laptops dial out.** A laptop's tile binds `127.0.0.1` and reaches the
  panel through an outbound relay connection, so nothing on the laptop is
  exposed and inbound firewalls and VPNs don't matter. Server-sent events,
  streaming and WebSockets pass through.
- **A control column the tiles can't cover**: clock, connected laptops,
  reload, a tile picker (tap to choose what goes in each slot), and unpin.
- **Python standard library only.** No packages on the host or the laptops.

Not affiliated with or endorsed by Corsair. "Xeneon" is their trademark.

## What's here

| File | Runs on | What |
|---|---|---|
| `edged.py` | panel host | The daemon: shell page and layout API on HTTP 7781, the relay server on TCP 7782, and supervision of the kiosk browser. Its docstring is the API reference. |
| `kiosk_win.py`, `kiosk_linux.py` | panel host | The platform part of `edged`: launching Chrome and keeping its window on top. |
| `edgerelay.py` | both | The relay. `edged` imports the server half; a laptop runs it as a client. Its docstring is the protocol reference. |
| `install-windows.ps1`, `install-linux.sh` | panel host | Install into a per-user folder and start it at logon (Scheduled Task / systemd user unit). |
| `launcher.py`, `updater.py` | panel host | What the installed copy runs: the launcher keeps `edged` running and rolls back a bad update; the updater (opt-in) fetches new releases. |
| `edge-shot.ps1`, `edge-shot.sh` | panel host | Screenshot the panel, for checking it over ssh. |
| `examples/demo_tile.py` | laptop | A complete working tile: a page, an SSE stream, and a timed tap round trip. |
| `examples/uebersicht/` | Mac | Optional [Übersicht](https://tracesof.net/uebersicht/) widget that previews a tile at slot size on the desktop. |
| [`TILES.md`](TILES.md) | | **How to build a tile.** Written so it can be handed, with `edgerelay.py` and `demo_tile.py`, to someone (or a coding agent) on another machine. |
| [`docs/hardware-notes.md`](docs/hardware-notes.md) | | What the Edge is on the wire, panel settings over DDC, and why the panel isn't plugged into a Mac. |

## Quick start

**Try it with no panel**, on any machine with Python 3.9+:

```
python3 edged.py --no-browser &                 # HTTP + relay only
python3 examples/demo_tile.py &                 # a tile on 127.0.0.1:8790
python3 edgerelay.py --host 127.0.0.1 --name demo --target 127.0.0.1:8790 --title "Demo" &
curl -XPOST http://127.0.0.1:7781/slot -d '{"slot":0,"url":"relay://demo/"}'
open http://127.0.0.1:7781/                     # the shell, in a 2560x720 window for the real look
```

**On the panel host** (the Edge plugged in as a display at 2560x720, scale
100%, with Google Chrome installed), download the latest
[release](https://github.com/codefrak-oss/frakpanel/releases), unzip it, and
run the installer from the unzipped folder:

```
powershell -ExecutionPolicy Bypass -File install-windows.ps1     # Windows: frakpanel-<ver>-windows-x64.zip
./install-linux.sh --system && ./install-linux.sh                # Linux (X11): frakpanel-<ver>.zip, Python 3.9+
```

The Windows zip brings its own Python, so nothing else needs installing. Either
installer also works from a git checkout (on Windows, `pythonw.exe` must then
be on PATH). The install lands in `%LOCALAPPDATA%\frakpanel` or
`~/.local/share/frakpanel`, and the panel shows three clocks and the column.

**On each laptop**, copy over `edgerelay.py` (the only file a laptop needs)
and point it at a local web server:

```
python3 edgerelay.py --host <panel-host> --name air-demo --target 127.0.0.1:8790 --title "Demo"
```

Then open the picker on the panel (the column's `tiles` button) and tap the
tile into a slot, or do the same from anywhere on the LAN:

```
curl -XPOST http://<panel-host>:7781/slot -d '{"slot":0,"url":"relay://air-demo/"}'
curl http://<panel-host>:7781/layout      # what is in each slot
curl http://<panel-host>:7781/tiles       # what the picker offers
```

[`TILES.md`](TILES.md) has the tile contract, previewing, and keeping a tile
running with launchd.

## How it works

`edged` serves a **shell page** and keeps a Chrome `--kiosk` window showing it
on the panel. The shell is three iframes and the column. It polls
`GET /layout` every 2 s and rebuilds a slot only when that slot's URL changes.

A slot's URL is one of:

| URL | Meaning |
|---|---|
| `""` | The host's own clock (`/home`). |
| `http://...` | Any page the panel host can reach directly: a tile served on the host itself, Grafana, Home Assistant, Jellyfin. |
| `relay://<name>/<path>` | A laptop connected through `edgerelay.py` under that name. While the laptop is away the slot shows the clock with "waiting for \<name\>", not an error page. |

**The relay.** A laptop opens one control connection to the host on 7782 and
says `hello` with a name and a title. The host gives that name its own
`127.0.0.1`-only port. When the kiosk browser connects to that port, the host
asks the laptop to dial a fresh stream connection and splices the two sockets
together, bytes untouched. Because each name is its own origin on the host,
pages can use absolute paths. The browser and `edged` are on the same machine,
so the relay adds an in-process byte copy to the normal LAN round trip and no
extra hop.

**The picker** lists the clock, the host's own fixed entries, and every relay
name seen since `edged` started (greyed while away). Tiles announce
themselves through their relay title; nothing is registered by hand. To add
fixed entries, copy `local_tiles.example.json` to `local_tiles.json` beside
`edged.py` and restart it.

**Staying on top.** On Windows, touching the panel pops the taskbar up over
the kiosk, and pop-ups can cover it. `edged` keeps the kiosk pinned topmost
and re-checks four times a second. `POST /release` (the column's `unpin`)
lets other windows come forward, and re-pins itself after 5 minutes, because
once unpinned another window can cover the very button that would re-pin.

### Fixed slots

The panel is always three equal slots beside the column. One source per slot,
and nothing spans. This is deliberate:

- Every page is built for one size (812x720 CSS px), the only size it ever
  has to support.
- Choosing what shows is one slot index, with no layout state beyond "which
  URL is in slot i".
- A source that wants more room takes two slots with two pages, which usually
  beats one stretched page.
- It can be loosened later without breaking existing tiles; the reverse isn't
  true.

### Configuration

| What | Where |
|---|---|
| HTTP port (7781) | `EDGED_PORT` |
| Relay port (7782) | `EDGE_RELAY_PORT`, set the same on host and laptops |
| Panel host for the relay client | `--host` or `FRAKPANEL_HOST` |
| Fixed picker entries | `local_tiles.json` |
| Which output is the panel (Linux) | found by its 2560x720 mode in `xrandr`, or `EDGE_POSITION="x,y"` |
| Updates | `update.json` in the data folder (below) |
| State | `edge_layout.json`, `edge_relays.json`, `edged.log`, `launcher.log`, in the install's `data` folder (`%LOCALAPPDATA%\frakpanel\data`, `~/.local/share/frakpanel/data`); beside the scripts when run from a checkout |
| Fixed picker entries, installed | `local_tiles.json` goes in that `data` folder too |

### Releases and updates

Releases are [GitHub Releases](https://github.com/codefrak-oss/frakpanel/releases),
built by `.github/workflows/release.yml`:

- **beta**: every push to `main` publishes `v<VERSION>-beta.<n>` as a
  prerelease.
- **stable**: pushing the tag `v<VERSION>` publishes that version. Bump
  `VERSION` straight after; until then, betas fail on purpose.

An installed copy updates itself only if you opt in. Create `update.json` in
the data folder:

```
{"channel": "stable"}      or      {"channel": "beta"}
```

It checks every 6 hours (`"check_hours"`), installs a newer release beside the
current one, and restarts into it. If the new version keeps dying within its
first two minutes, the launcher rolls back to the previous one and the updater
skips that release. `curl http://<panel-host>:7781/version` shows the running
version, the channel, and the last check. The kiosk browser is untouched by
an update.

## Security

**There is no authentication.** Anything that can reach port 7781 can put any
web page on the panel, and anything that can reach 7782 and says `hello` with
a name gets that name's tile. Run it on a LAN you trust and don't expose
either port beyond it. Don't put a tile on the panel that shouldn't be
readable by whoever is at the desk.

Tiles are loaded over plain http, so they get no secure context (no clipboard
API, no service workers).

## Platform notes

**Windows** is where this runs day to day (Windows 11, Intel NUC). Windows
handles the Edge's touch digitizer natively, with no driver or calibration.

- **RDP steals the panel.** Windows has one interactive session. An RDP
  connection moves it off the console, the panel shows the lock screen, and it
  stays locked after RDP disconnects. A panel with no keyboard can't get past
  that. Before closing RDP, hand the session back from an elevated prompt:
  `tscon ((quser $env:USERNAME | Select -Skip 1) -split '\s+')[2] /dest:console`
  (check `quser` first; the field index shifts if the line starts with `>`).
  The pinned kiosk covers the RDP desktop too, so `POST /release` first.
- **Display queries over ssh lie.** An ssh session has no real display, so
  resolution queries report the wrong mode. Use the `frakpanel-shot` task to
  see what the panel shows.
- **Shell UI outranks topmost.** The Start menu can cover the kiosk and no
  window can cover it back. `edged` logs this and recovers when it closes; if
  the browser is left with no window for 30 s it is killed and relaunched.
- **With a second monitor on the host**, Windows may map touch to the wrong
  screen. Fix it with "Calibrate the screen for pen or touch input" > Setup.
- An unactivated Windows draws its watermark over the panel, above everything.

**Linux** support is X11 only (xdotool, wmctrl, xprop, xrandr) and, as of this
writing, **has not been run on a real panel**: the code is there and the
window handling mirrors the Windows side, but expect to fix things. Under
Wayland the kiosk is launched and relaunched but not placed or pinned; use
your compositor's window rules for that (see `kiosk_linux.py`).

**macOS** is not supported as a panel host: macOS has no native touchscreen
support. [`docs/hardware-notes.md`](docs/hardware-notes.md) has what was
tried. Macs work fine as laptops serving tiles.

## Limitations

- Three slots, fixed. No spanning, no per-slot priorities or claims: the last
  `POST` or picker tap wins.
- A tile can't be told to reload when its server is redeployed. Tap `reload`
  in the column, or clear the slot and set it back.
- Sites that forbid framing (`X-Frame-Options`, `frame-ancestors`) render
  blank in a slot.
- One relay client exposes one local port, so each tile gets its own relay
  name.

## License

MIT. See [LICENSE](LICENSE).
