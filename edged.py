r"""
edged.py -- frakpanel's kiosk daemon: runs on the machine a Corsair Xeneon Edge
is plugged into (the "panel host"; Windows or Linux).

The panel host owns the hardware and knows nothing about what is shown. Other
machines serve their own pages; edged is told which URL goes in which slot,
and a kiosk browser on the Edge renders them. Touch lands in whichever slot's
page is under the finger, so there are no input events to forward.

Two jobs in one process:
  1. HTTP on port 7781 (all interfaces; EDGED_PORT):
       GET  /          the shell page: 3 fixed, equal slots (one iframe
                       each, 812x720 on the panel) plus a 120px control
                       column at the right edge that belongs to the shell (no
                       slot can cover it): clock, connected laptop count,
                       reload, tile picker, unpin with a timed re-pin, desktop
                       (close the kiosk, with a timed return). Polls
                       /layout and rebuilds slots when they change.
                       Slots are completely fixed: every page is built for
                       exactly one slot and nothing spans (README, "Fixed
                       slots").
       GET  /layout    {"slots":["<url>", "<url>", "<url>"], "shell":"<hash>",
                        "pinned":bool, "repin_in":seconds|null, "laptops":n,
                        "kiosk":"running"|"stopped", "restart_in":seconds|null}
                       (restart_in is null while running or stopped untimed).
       POST /layout    {"slots":["http://host:port/x", "", "relay://air/"]}
                       sets all slots; fewer than 3 are padded with "".
                       url "" -> /home (the clock);
                       url "relay://<name>/<path>" -> a laptop connected
                       through edgerelay.py (resolved in GET /layout, with a
                       /home fallback while that laptop is disconnected).
       POST /slot      {"slot":0, "url":"relay://air/"} sets one slot (0-2).
       GET  /relays    connected relay clients and their host-local ports.
       GET  /tiles     what the column's tile picker offers: {"tiles":[{"url",
                       "title","online","laptop"?}], "slots":[raw urls]}. The
                       clock and local_tiles.json (fixed URLs) come first,
                       then one entry per relay name seen since edged started
                       (title from the relay's hello; greyed while it is away).
                       "deletable" says whether POST /tiles/delete takes it.
       POST /tiles/delete  {"url":...} what the picker's swipe-right + "yes"
                       sends: removes a tile registered over MCP from
                       mcp_tiles.json, or forgets an away relay name until
                       that laptop reconnects. 404 for an unknown url, 409
                       for the clock, local_tiles.json entries and a
                       connected relay. Slots showing it keep their URL.
       POST /mcp       MCP server (Streamable HTTP, stateless, JSON replies;
                       protocol 2026-07-28, and 2025-06-18 / 2025-03-26
                       through initialize) whose
                       tools register_tile / remove_tile manage URL tiles
                       kept in mcp_tiles.json and listed by /tiles after
                       local_tiles.json, list_tiles returns every tile the
                       picker offers (the /tiles list, clock included), and get_layout / set_slot
                       {slot, url} read and change the slots like GET /layout
                       and POST /slot; see mcp_tiles.py and TILES.md.
       GET  /home      host-local placeholder page (clock, "nothing claimed").
       POST /front     pin the kiosk window above every other window (default).
       POST /release   unpin it, so other windows (e.g. over RDP) can come
                       forward. Lasts until /front or an edged restart, or
                       for {"seconds":N} if given (the column sends 300).
       POST /kiosk/stop  close the kiosk browser so the panel shows the plain
                       desktop; it is not relaunched until /kiosk/start, an
                       edged restart, or {"seconds":N} (0 < N <= 86400) if
                       given. Replies {"stopped":true, "restart_in":N|null}.
       POST /kiosk/start  end a stop: the supervisor relaunches the browser on
                       its next tick, pinned as after a normal launch.
                       Replies {"started":true}.
     Last POST wins. No claims, priorities or heartbeats. No auth: anything
     that can reach the port can put a page on the panel, so LAN only.
  2. Keeps a Google Chrome kiosk window pointed at http://127.0.0.1:7781/,
     with its own profile dir so it never merges with a normal browser window.
     Relaunches it if it goes away. "Is it running" is answered by the
     profile's lockfile (held open by the browser), not by our child handle,
     because chrome.exe can hand off to another process and exit. A browser
     that is running but has had no kiosk window for KIOSK_DEAD_AFTER is
     treated as gone and killed, since the lockfile alone would otherwise
     block the relaunch forever.
     The browser and its window are the platform part: kiosk_win.py (Chrome,
     lockfile, user32 topmost) and kiosk_linux.py (Chrome or Chromium,
     SingletonLock, X11 via xdotool and wmctrl). Their docstrings have the
     details; this file only drives the Kiosk interface they share.

       GET  /version   {"version", "channel", "latest", "checked", "error"}: this
                       build and what the updater last saw (updater.py).

The layout survives restarts in edge_layout.json, in $FRAKPANEL_DATA (set by
launcher.py to the install's data/ dir) or else beside this file.

Standard library only. Runs with:
    pythonw.exe edged.py     (Scheduled Task "frakpanel", install-windows.ps1)
    python3 edged.py         (systemd user unit "frakpanel", install-linux.sh)
    python3 edged.py --no-browser    HTTP and relay only, on any OS (development)
"""
from __future__ import annotations

import hashlib
import html
import http.server
import json
import os
import socket
import sys
import threading
import time

import edgerelay
import mcp_tiles
import updater

if sys.platform == "win32":
    import kiosk_win as kiosk_platform  # Chrome + user32
else:
    import kiosk_linux as kiosk_platform  # Chrome/Chromium + X11 (xdotool, wmctrl)

HERE = os.path.dirname(os.path.abspath(__file__))
# Runtime state. Installed copies run from versions/<ver>/ under launcher.py,
# which points this at the install's data/ dir so state survives updates; a
# plain checkout keeps it beside the scripts.
DATA = os.environ.get("FRAKPANEL_DATA", HERE)
VERSION = updater.read_version(HERE)
HOST = "0.0.0.0"
PORT = int(os.environ.get("EDGED_PORT", "7781"))
LOG_PATH = os.path.join(DATA, "edged.log")
LAYOUT_PATH = os.path.join(DATA, "edge_layout.json")
SLOTS = 3  # completely fixed; see the module docstring
LOCAL_TILES_PATH = os.path.join(DATA, "local_tiles.json")  # optional; see load_local_tiles()
MCP_TILES_PATH = os.path.join(DATA, "mcp_tiles.json")  # tiles registered through POST /mcp (mcp_tiles.py)
RELAUNCH_MIN_INTERVAL = 15.0  # seconds between browser launches, so a crash loop can't spawn windows
REPIN_SECONDS = 300  # the column's unpin re-pins itself, since other windows can cover the re-pin button
SETTLE_SECONDS = 0.4  # after a raise, how long the kiosk must stay on top before we call it raised
KIOSK_DEAD_AFTER = 30.0  # seconds the browser may run with no kiosk window before it is killed and relaunched
KEEPER_LOG_EVERY = 60.0  # seconds; the keeper can re-pin 4x/s, so log at most this often while it is losing

HOME_PAGE = """<!doctype html><meta charset="utf-8"><title>frakpanel home</title>
<style>
html,body{margin:0;height:100%;background:#000;color:#8a8f98;font-family:system-ui,sans-serif;cursor:none;overflow:hidden}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2vh}
#t,#s{white-space:nowrap;max-width:94vw;overflow:hidden;text-overflow:ellipsis}
#t{font-size:min(36vh,17vw);line-height:1;color:#e6e6e6;font-variant-numeric:tabular-nums;font-weight:200}
#s{font-size:min(4vh,3vw)}
</style>
<div id="t"></div><div id="s">__HOSTNAME__ &middot; nothing claimed this slot</div>
<script>
const t=document.getElementById('t');
const waiting=new URLSearchParams(location.search).get('waiting');
if(waiting)document.getElementById('s').textContent='waiting for '+waiting+' to connect';
function tick(){t.textContent=new Date().toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});}
tick();setInterval(tick,1000);
</script>""".replace("__HOSTNAME__", html.escape(socket.gethostname().split(".")[0]))

SHELL_PAGE = """<!doctype html><meta charset="utf-8"><title>frakpanel shell</title>
<style>
html,body{margin:0;width:100%;height:100%;background:#000;overflow:hidden;cursor:none;user-select:none;font-family:system-ui,sans-serif}
#wrap{display:flex;width:100%;height:100%;position:relative}
#row{flex:1;display:flex;min-width:0}
iframe{border:0;height:100%;min-width:0;flex:1;background:#000}
iframe+iframe{border-left:2px solid #1a1a1a}
#col{width:120px;flex:none;background:#0d0f14;border-left:2px solid #22262f;display:flex;flex-direction:column;align-items:center;padding:10px 0;box-sizing:border-box;gap:8px;color:#8a8f98}
#pick{position:absolute;left:0;top:0;bottom:0;width:calc(100% - 120px);display:flex;background:#0d0f14;color:#aab0bb}
#pick[hidden]{display:none}
#pick .slot{flex:1;min-width:0;display:flex;flex-direction:column;gap:10px;padding:14px 18px;box-sizing:border-box;overflow-y:auto;touch-action:pan-y;scrollbar-width:none}
#pick .slot+.slot{border-left:2px solid #22262f}
#pick h2{margin:0 0 4px;font-size:18px;font-weight:400;color:#5c6270}
#pick .card{min-height:88px;flex:none;border-radius:14px;background:#171b24;border:2px solid #171b24;display:flex;flex-direction:column;justify-content:center;padding:10px 22px;box-sizing:border-box;touch-action:manipulation}
#pick .card b{font-size:24px;font-weight:500;color:#e6e6e6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#pick .card small{font-size:14px;color:#8a8f98;display:block;white-space:normal;overflow-wrap:break-word}
#pick .card.on{border-color:#6fcf97}
#pick .card.off b{color:#5c6270}
#pick .card{position:relative;transition:transform .15s}
#pick .card.swiped{transform:translateX(14px);background:#2a3140}
#pick .card .del{display:none;position:absolute;right:8px;top:8px;bottom:8px;width:96px;border-radius:12px;background:#c0392b;color:#fff;font-size:18px;align-items:center;justify-content:center}
#pick .card.swiped .del{display:flex}
#confirm{position:absolute;inset:0;background:rgba(0,0,0,.7);display:flex;align-items:center;justify-content:center}
#confirm[hidden]{display:none}
#confirm div{background:#171b24;border:2px solid #22262f;border-radius:14px;padding:24px 30px;color:#e6e6e6;font-size:22px;text-align:center}
#confirm span{display:inline-block;margin:18px 12px 0;padding:14px 34px;border-radius:12px;background:#2a3140}
#confirm #yes{background:#c0392b}
#tiles.open{background:#2a3140;color:#e6e6e6}
.time{font-size:26px;color:#e6e6e6;font-weight:300;font-variant-numeric:tabular-nums}
.btn{width:100px;height:72px;border-radius:12px;background:#171b24;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;font-size:13px;color:#aab0bb;touch-action:manipulation}
.btn.hit{background:#2f4f8f}
.btn svg{width:38px;height:24px}
.dot{width:8px;height:8px;border-radius:50%;background:#5c6270;display:inline-block;margin-right:5px}
.dot.live{background:#6fcf97}
.spacer{flex:1}
#pin.off{background:#5a3a12;color:#ffd9a0}
#down{color:#e07b6f}
#confirm .dur{display:block;margin:14px auto 0;padding:14px 20px}
#confirm .dur.sel{background:#2f4f8f}
</style>
<div id="wrap">
  <div id="row"></div>
  <div id="pick" hidden></div>
  <div id="confirm" hidden><div><p id="confirmq"></p><span id="yes">yes</span><span id="no">no</span></div></div>
  <div id="col">
    <div class="time" id="t"></div>
    <div style="font-size:13px" id="laptops"><span class="dot"></span><span>0 laptops</span></div>
    <div class="btn" id="reload"><svg viewBox="0 0 34 22"><path d="M24 6a9 9 0 1 0 2 7" fill="none" stroke="currentColor" stroke-width="2"/><path d="M25 1v6h-6" fill="none" stroke="currentColor" stroke-width="2"/></svg>reload</div>
    <div class="btn" id="tiles"><svg viewBox="0 0 34 22"><path d="M3 3h8v7H3zM13 3h8v7h-8zM23 3h8v7h-8zM3 12h8v7H3zM13 12h8v7h-8z" fill="none" stroke="currentColor" stroke-width="2"/></svg>tiles</div>
    <div class="spacer"></div>
    <div id="down" hidden>edged not responding</div>
    <div class="btn" id="pin"><svg viewBox="0 0 34 22"><path d="M17 2v10M11 12h12l-2-6h-8zM17 12v9" fill="none" stroke="currentColor" stroke-width="2"/></svg><span id="pinl">unpin</span></div>
    <div class="btn" id="desk"><svg viewBox="0 0 34 22"><path d="M4 2h26v14H4zM12 20h10M17 16v4" fill="none" stroke="currentColor" stroke-width="2"/></svg>desktop</div>
  </div>
</div>
<script>
const $ = id => document.getElementById(id);
const REPIN_SECONDS = __REPIN_SECONDS__;
let current = null, shell = null, repinAt = 0, fails = 0;
function render(slots){
  $('row').replaceChildren(...slots.map(url => {
    const f = document.createElement('iframe');
    f.src = url || '/home';
    f.allow = 'autoplay; fullscreen; clipboard-read; clipboard-write';
    return f;
  }));
}
function drawPin(){
  const left = repinAt ? Math.max(0, Math.ceil((repinAt - Date.now()) / 1000)) : -1;
  $('pin').classList.toggle('off', left >= 0);
  $('pinl').textContent = left < 0 ? 'unpin' : left === Infinity ? 're-pin' : 're-pin ' + Math.floor(left / 60) + ':' + String(left % 60).padStart(2, '0');
}
function apply(j){
  if (shell && j.shell !== shell) { location.reload(); return; }  // edged redeployed with a new shell
  shell = j.shell;
  const key = JSON.stringify(j.slots);
  if (key !== current) { current = key; render(j.slots); }
  const n = j.laptops || 0;
  $('laptops').firstChild.classList.toggle('live', n > 0);
  $('laptops').lastChild.textContent = n + (n === 1 ? ' laptop' : ' laptops');
  repinAt = j.pinned ? 0 : (j.repin_in == null ? Infinity : Date.now() + j.repin_in * 1000);
  drawPin();
}
async function poll(){
  try {
    const r = await fetch('/layout', {cache: 'no-store'});
    apply(await r.json());
    fails = 0;
  } catch (e) { fails++; }  // edged restarting; keep the current slots and retry
  $('down').hidden = fails < 3;
}
async function post(path, body){
  try {
    await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  } catch (e) {}
  poll();
}
function hit(el){ el.classList.add('hit'); setTimeout(() => el.classList.remove('hit'), 150); }
// The picker covers the three slots (drawn by the shell, so no tile can block it):
// one column per slot listing every tile, the current one outlined. A tap sets that slot.
function showPicker(open){
  $('pick').hidden = !open;
  $('tiles').classList.toggle('open', open);
  if (open) fillPicker();
}
async function fillPicker(){
  let j;
  try { j = await (await fetch('/tiles', {cache: 'no-store'})).json(); } catch (e) { return; }
  $('pick').replaceChildren(...j.slots.map((cur, i) => {
    const col = document.createElement('div');
    col.className = 'slot';
    const h = document.createElement('h2');
    h.textContent = 'slot ' + (i + 1);
    col.append(h, ...j.tiles.map(t => {
      const c = document.createElement('div');
      c.className = 'card' + (t.url === cur ? ' on' : '') + (t.online ? '' : ' off');
      const b = document.createElement('b'); b.textContent = t.title;
      const s = document.createElement('small'); s.textContent = t.laptop ? 'webserver registers itself to frakpanel web server \u00b7 ' + t.laptop + (t.online ? '' : ' \u00b7 away') : 'frakpanel connects to web server';
      c.append(b, s);
      if (t.deletable && t.url) swipeToDelete(c, t);  // never the clock (url ""), whatever /tiles says
      c.addEventListener('click', e => {  // click, not pointerdown: a scroll drag must not pick
        if (e.target.classList.contains('del')) return;
        if (c.dataset.swiped) {  // the click right after a swipe is ignored; a later tap restores the card
          if (c.dataset.swiped === '2') unswipe(c); else c.dataset.swiped = '2';
          return;
        }
        hit(c); showPicker(false); post('/slot', {slot: i, url: t.url});
      });
      return c;
    }));
    return col;
  }));
}
// Swipe right on a deletable card (MCP-registered, or an away relay) to reveal its delete
// button. The column keeps touch-action:pan-y, so a vertical drag is the browser's scroll
// (we get pointercancel); only a mostly-horizontal drag of SWIPE_PX or more counts.
const SWIPE_PX = 60;
function swipeToDelete(c, t){
  let x0 = null, y0 = 0;
  const del = document.createElement('div');
  del.className = 'del'; del.textContent = 'delete';
  del.addEventListener('click', () => confirmDelete(c, t));
  c.append(del);
  c.addEventListener('pointerdown', e => { x0 = e.clientX; y0 = e.clientY; });
  c.addEventListener('pointercancel', () => { x0 = null; });
  c.addEventListener('pointerup', e => {
    if (x0 === null) return;
    const dx = e.clientX - x0, dy = e.clientY - y0;
    x0 = null;
    if (dx >= SWIPE_PX && Math.abs(dx) > 2 * Math.abs(dy)) {
      c.classList.add('swiped');
      c.dataset.swiped = '1';  // the click that follows this pointerup must not pick
    } else if (c.dataset.swiped && dx <= -SWIPE_PX) {
      unswipe(c);
    }
  });
}
function unswipe(c){ c.classList.remove('swiped'); setTimeout(() => delete c.dataset.swiped, 0); }
function confirmDelete(c, t){
  $('confirmq').textContent = 'Delete ' + t.title + '?';
  $('confirm').hidden = false;
  $('no').onclick = () => { $('confirm').hidden = true; unswipe(c); };
  $('yes').onclick = async () => {
    $('confirm').hidden = true;
    try {
      await fetch('/tiles/delete', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url: t.url})});
    } catch (e) {}
    fillPicker();  // the card leaves all three columns; slots showing it keep their URL
  };
}
$('tiles').addEventListener('pointerdown', () => { hit($('tiles')); showPicker($('pick').hidden); });
if (new URLSearchParams(location.search).get('picker')) showPicker(true);  // for screenshots
$('reload').addEventListener('pointerdown', () => { hit($('reload')); current = null; poll(); });
// desktop: close the kiosk browser (this page goes with it), so the confirmation picks
// when edged brings it back; "until I bring it back" means POST /kiosk/start or an edged restart.
const STOP_CHOICES = [['15 minutes', 900], ['1 hour', 3600], ['until I bring it back', null]];
function confirmStop(){
  let pick = 0;
  $('confirmq').textContent = 'Close the kiosk and show the desktop? Bring it back after:';
  const durs = STOP_CHOICES.map(([label], i) => {
    const d = document.createElement('span');
    d.className = 'dur' + (i === pick ? ' sel' : ''); d.textContent = label;
    d.onclick = () => { pick = i; durs.forEach((x, j) => x.classList.toggle('sel', j === i)); };
    return d;
  });
  $('confirmq').after(...durs);
  const close = () => { durs.forEach(d => d.remove()); $('confirm').hidden = true; };
  $('confirm').hidden = false;
  $('no').onclick = close;
  $('yes').onclick = () => { const s = STOP_CHOICES[pick][1]; close(); post('/kiosk/stop', s ? {seconds: s} : {}); };
}
$('desk').addEventListener('pointerdown', () => { hit($('desk')); confirmStop(); });
$('pin').addEventListener('pointerdown', () => { hit($('pin')); repinAt ? post('/front') : post('/release', {seconds: REPIN_SECONDS}); });
function tick(){ $('t').textContent = new Date().toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}).replace(/\\s?[AP]M/, ''); }
tick(); setInterval(tick, 5000); setInterval(drawPin, 1000);
poll(); setInterval(poll, 2000);
document.addEventListener('contextmenu', e => e.preventDefault());  // long-press menu
</script>""".replace("__REPIN_SECONDS__", str(REPIN_SECONDS))

SHELL_HASH = hashlib.sha1((SHELL_PAGE + HOME_PAGE).encode()).hexdigest()[:12]


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def clean_url(url) -> str:
    url = "" if url is None else url
    if not isinstance(url, str) or (url and not url.startswith(("http://", "https://", "/", "relay://"))):
        raise ValueError(f"bad url {url!r}")
    return url


class Layout:
    """Which URL is in each of the SLOTS fixed slots. "" is the /home clock."""

    def __init__(self):
        self.lock = threading.Lock()
        self.slots = self.load()

    @staticmethod
    def clean(obj) -> list[str]:
        slots = obj.get("slots") if isinstance(obj, dict) else None
        if not isinstance(slots, list) or len(slots) > SLOTS:
            raise ValueError(f"slots must be a list of at most {SLOTS} urls")
        return [clean_url(u) for u in slots] + [""] * (SLOTS - len(slots))

    def load(self) -> list[str]:
        try:
            with open(LAYOUT_PATH, encoding="utf-8") as f:
                return self.clean(json.load(f))
        except (OSError, ValueError) as exc:
            if not isinstance(exc, FileNotFoundError):
                log(f"ignoring {LAYOUT_PATH}: {exc}")
            return [""] * SLOTS

    def save(self) -> None:  # caller holds self.lock
        tmp = LAYOUT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"slots": self.slots}, f, indent=2)
        os.replace(tmp, LAYOUT_PATH)

    def set(self, obj) -> list[str]:
        slots = self.clean(obj)
        with self.lock:
            self.slots = slots
            self.save()
        return slots

    def set_slot(self, obj) -> list[str]:
        i = obj.get("slot")
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < SLOTS:
            raise ValueError(f"slot must be an integer 0-{SLOTS - 1}")
        url = clean_url(obj.get("url"))
        with self.lock:
            self.slots[i] = url
            self.save()
            return list(self.slots)

    def get(self) -> list[str]:
        with self.lock:
            return list(self.slots)


def load_local_tiles() -> list[dict]:
    """The picker's fixed entries: the clock, then local_tiles.json if it exists.

    local_tiles.json is a list of {"url", "title"} for pages the panel host can
    reach without a relay (a tile served on the host itself, Grafana, Jellyfin).
    """
    tiles = [{"url": "", "title": "clock"}]
    try:
        with open(LOCAL_TILES_PATH, encoding="utf-8") as f:
            tiles += [{"url": clean_url(t["url"]), "title": str(t["title"])} for t in json.load(f)]
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log(f"ignoring {LOCAL_TILES_PATH}: {exc}")
    return tiles


class Handler(http.server.BaseHTTPRequestHandler):
    layout: Layout
    local_tiles: list[dict]
    mcp: mcp_tiles.McpServer
    relay: edgerelay.RelayServer
    updater: updater.Updater

    @classmethod
    def picker_tiles(cls) -> list[dict]:
        """What the column's tile picker offers, in order: the clock and
        local_tiles.json, MCP-registered tiles, relay tiles; GET /tiles and MCP list_tiles."""
        return ([dict(t, online=True, deletable=False) for t in cls.local_tiles]
                + [dict(t, online=True, deletable=True) for t in cls.mcp.registry.list()]
                + [dict(t, deletable=not t["online"]) for t in cls.relay.tiles()])

    def log_message(self, fmt, *args) -> None:  # quiet: the shell polls every 2s
        pass

    def reply(self, code: int, body: str | bytes, ctype: str) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def json(self, code: int, obj) -> None:
        self.reply(code, json.dumps(obj), "application/json")

    def resolve(self, url: str) -> str:
        if url.startswith("relay://"):
            name, _, rest = url[len("relay://"):].partition("/")
            return self.relay.url_for(name, rest) or f"/home?waiting={name}"
        return url

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.reply(200, SHELL_PAGE, "text/html; charset=utf-8")
        elif path == "/home":
            self.reply(200, HOME_PAGE, "text/html; charset=utf-8")
        elif path == "/layout":
            self.json(200, {
                "slots": [self.resolve(u) for u in self.layout.get()],
                "shell": SHELL_HASH,
                "pinned": pinned,
                "repin_in": None if pinned or repin_at is None else max(0, round(repin_at - time.monotonic())),
                "laptops": len(self.relay.status()),
                "kiosk": "stopped" if kiosk_stopped else "running",
                "restart_in": None if not kiosk_stopped or restart_at is None
                else max(0, round(restart_at - time.monotonic())),
            })
        elif path == "/relays":
            self.json(200, self.relay.status())
        elif path == "/tiles":
            self.json(200, {"tiles": self.picker_tiles(), "slots": self.layout.get()})
        elif path == "/version":
            self.json(200, self.updater.status())
        elif path == "/mcp":  # stateless Streamable HTTP: no server-initiated SSE stream
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.json(404, {"error": "not found"})

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, MCP-Protocol-Version, Mcp-Method, Mcp-Name")
        self.end_headers()

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        obj = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(obj, dict):
            raise ValueError("body must be a JSON object")
        return obj

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        who = self.client_address[0]
        if path == "/mcp":
            status, out = self.mcp.respond(self.rfile.read(int(self.headers.get("Content-Length") or 0)),
                                           self.headers)
            if out is None:
                self.reply(status, b"", "application/json")
            else:
                self.json(status, out)
            return
        try:
            if path == "/front":
                raised = pin()
                log(f"front from {who}: raised={raised}")
                self.json(200, {"raised": raised, "pinned": True})
            elif path == "/release":
                seconds = self.body().get("seconds")
                if seconds is not None and not (isinstance(seconds, (int, float)) and 0 < seconds <= 86400):
                    raise ValueError("seconds must be in (0, 86400]")
                found = release(seconds)
                log(f"release from {who}: window found={found}, " + (f"re-pin in {seconds}s" if seconds else "until /front"))
                self.json(200, {"released": found, "pinned": False, "repin_in": seconds})
            elif path == "/kiosk/stop":
                seconds = self.body().get("seconds")
                if seconds is not None and not (isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
                                                and 0 < seconds <= 86400):
                    raise ValueError("seconds must be in (0, 86400]")
                stop_kiosk(seconds)
                log(f"kiosk stop from {who}: " + (f"restart in {seconds}s" if seconds else "until /kiosk/start"))
                self.json(200, {"stopped": True, "restart_in": seconds})
            elif path == "/kiosk/start":
                start_kiosk()
                log(f"kiosk start from {who}")
                self.json(200, {"started": True})
            elif path in ("/layout", "/slot"):
                body = self.body()
                slots = self.layout.set(body) if path == "/layout" else self.layout.set_slot(body)
                log(f"{path[1:]} from {who}: {[u or '/home' for u in slots]}")
                self.json(200, {"slots": slots})
            elif path == "/tiles/delete":
                url = self.body().get("url")
                if not isinstance(url, str):
                    raise ValueError("url must be a string")
                if any(t["url"] == url for t in self.local_tiles):
                    self.json(409, {"error": "fixed tile (clock or local_tiles.json)"})
                elif self.mcp.registry.remove(url):
                    log(f"tile deleted from {who}: {url}")
                    self.json(200, {"deleted": url})
                elif url.startswith("relay://"):
                    gone = self.relay.forget(url[len("relay://"):].partition("/")[0])
                    if gone is None:
                        self.json(404, {"error": "unknown tile"})
                    elif not gone:
                        self.json(409, {"error": "relay is connected"})
                    else:
                        log(f"relay forgotten from {who}: {url}")
                        self.json(200, {"deleted": url})
                else:
                    self.json(404, {"error": "unknown tile"})
            else:
                self.json(404, {"error": "not found"})
        except (ValueError, TypeError) as exc:
            self.json(400, {"error": str(exc)})


_kiosk = None
pinned = True  # kiosk stays above every window (taskbar, pop-ups) until POST /release
repin_at: float | None = None  # time.monotonic() when a timed /release ends
kiosk_stopped = False  # POST /kiosk/stop: the browser is closed on purpose and not relaunched
restart_at: float | None = None  # time.monotonic() when a timed /kiosk/stop ends


def kiosk():
    """This platform's Kiosk (kiosk_win.py / kiosk_linux.py), made on first use."""
    global _kiosk
    if _kiosk is None:
        _kiosk = kiosk_platform.Kiosk(log)
    return _kiosk


def bring_to_front(timeout: float = 20.0) -> bool:
    """Pin the kiosk window above everything else on the panel host, retrying until it exists or timeout."""
    w = kiosk().windows
    if w is None:
        return False
    deadline = time.monotonic() + timeout
    while True:
        hwnd = w.kiosk_window()
        if hwnd:
            if not w.on_top(hwnd):
                w.pin(hwnd, True)
                w.try_focus(hwnd)
            if w.on_top(hwnd):
                # try_focus can dismiss whatever was covering us just long enough for
                # this check to pass, after which the shell puts it straight back. Only
                # a raise that survives SETTLE_SECONDS counts -- a bare on_top() here
                # reported success while the panel still showed the desktop (2026-09-14).
                time.sleep(SETTLE_SECONDS)
                if w.on_top(hwnd):
                    return True
                log("kiosk raised but something took the top back within "
                    f"{SETTLE_SECONDS}s (shell UI such as the Start menu outranks topmost)")
        if time.monotonic() >= deadline:
            log(f"could not raise kiosk window (window {'found' if hwnd else 'not found'})")
            return False
        time.sleep(1)


def keep_on_top() -> None:
    """While pinned, re-pin whenever something (taskbar, a pop-up) covers the kiosk."""
    w = kiosk().windows
    if w is None:
        return
    last_log = 0.0
    while True:
        time.sleep(w.KEEPER_INTERVAL)
        if not pinned and repin_at is not None and time.monotonic() >= repin_at:
            pin(raise_now=False)  # the check below does the raising
            log("timed release ended; re-pinning")
        if not pinned:
            continue
        try:
            hwnd = w.kiosk_window()
            if hwnd and not w.on_top(hwnd):
                w.pin(hwnd, True)
                # Some windows cannot be covered at all (the Start menu and the rest of
                # the shell outrank topmost), and against those this runs 4x/s forever --
                # so rate-limit, or one stray Win key writes megabytes of identical lines.
                if time.monotonic() - last_log >= KEEPER_LOG_EVERY:
                    last_log = time.monotonic()
                    log(f"re-pinned kiosk window on top (now on_top={w.on_top(hwnd)})")
        except Exception as exc:  # never let the keeper thread die silently
            log(f"keep_on_top: {exc!r}")


def pin(raise_now: bool = True) -> bool:
    global pinned, repin_at
    pinned, repin_at = True, None
    return bring_to_front(timeout=3.0) if raise_now else True


def release(seconds: float | None = None) -> bool:
    global pinned, repin_at
    pinned = False
    repin_at = time.monotonic() + seconds if seconds else None
    w = kiosk().windows
    hwnd = w.kiosk_window() if w else None
    if hwnd:
        w.pin(hwnd, False)
    return bool(hwnd)


def stop_kiosk(seconds: float | None = None) -> None:
    """Close the kiosk browser and hold the supervisor off until start_kiosk() or the timer."""
    global kiosk_stopped, restart_at
    kiosk_stopped = True  # set before the kill, so the supervisor never relaunches in between
    restart_at = time.monotonic() + seconds if seconds else None
    kiosk().kill()


def start_kiosk() -> None:
    """End a stop; the supervisor relaunches on its next tick, and the new window is pinned."""
    global kiosk_stopped, restart_at
    kiosk_stopped, restart_at = False, None
    pin(raise_now=False)  # supervise_browser's bring_to_front raises the relaunched window


def may_run_kiosk(now: float | None = None) -> bool:
    """The supervisor's gate: False while stopped; ends a timed stop whose time has come."""
    if not kiosk_stopped:
        return True
    if restart_at is not None and (time.monotonic() if now is None else now) >= restart_at:
        start_kiosk()
        log("timed kiosk stop ended; relaunching")
        return True
    return False


def supervise_browser() -> None:
    k = kiosk()
    last_launch = 0.0
    windowless_since: float | None = None
    while True:
        if not may_run_kiosk():  # stopped on purpose: no wedge check, no kill, no log
            windowless_since = None
            time.sleep(3)
            continue
        running = k.running()
        if running:
            # A live browser with no kiosk window is wedged, and the relaunch below will
            # not fire while its profile lock is held -- so restarting edged re-attaches
            # to the dead window and changes nothing. Kill it and let the next tick
            # build a fresh one. (2026-09-14: a day-old Chrome whose window never came
            # back after the Start menu took the panel.) A merely minimized window still
            # counts as found; the keeper's SW_RESTORE handles that case.
            w = k.windows
            if w is not None and w.kiosk_window() is None:
                windowless_since = windowless_since or time.monotonic()
                if time.monotonic() - windowless_since >= KIOSK_DEAD_AFTER:
                    log(f"kiosk browser has had no window for {KIOSK_DEAD_AFTER:.0f}s; killing it")
                    k.kill()
                    windowless_since = None
                    time.sleep(3)  # let the profile lock clear before running() is asked again
                    continue
            else:
                windowless_since = None
        if not running and time.monotonic() - last_launch >= RELAUNCH_MIN_INTERVAL:
            last_launch = time.monotonic()
            flags = [
                "--kiosk", f"http://127.0.0.1:{PORT}/",
                f"--user-data-dir={k.profile_dir}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-pinch",
                "--overscroll-history-navigation=0",
                "--autoplay-policy=no-user-gesture-required",
            ]
            try:
                k.launch(flags)
                log("launched kiosk browser")
                bring_to_front()
            except OSError as exc:
                log(f"browser launch failed: {exc}")
        time.sleep(3)


class Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    Handler.layout = Layout()
    Handler.local_tiles = load_local_tiles()
    Handler.mcp = mcp_tiles.McpServer(mcp_tiles.TileRegistry(MCP_TILES_PATH, log), VERSION, log,
                                         Handler.layout, kiosk_platform.screenshot, Handler.picker_tiles)
    Handler.relay = edgerelay.RelayServer(log)
    threading.Thread(target=Handler.relay.serve, daemon=True).start()
    with Server((HOST, PORT), Handler) as srv:
        log(f"frakpanel {VERSION} listening on {HOST}:{PORT}, shell {SHELL_HASH}, slots {Handler.layout.get()}")
        Handler.updater = updater.Updater(HERE, DATA, VERSION, log)
        threading.Thread(target=Handler.updater.run, daemon=True).start()
        if "--no-browser" not in sys.argv:
            threading.Thread(target=supervise_browser, daemon=True).start()
            threading.Thread(target=keep_on_top, daemon=True).start()
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pythonw has no console; make crashes visible in the log
        log(f"fatal: {exc!r}")
        raise
