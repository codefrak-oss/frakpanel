r"""
kiosk_linux.py -- edged.py's kiosk browser and window handling on Linux (X11).

Google Chrome if installed, else Chromium, with its own profile under
$XDG_DATA_HOME (~/.local/share/frakpanel-kiosk). "Is it running" is the
profile's SingletonLock, a symlink to "<host>-<pid>" that Chromium keeps while
the profile is open: running means that pid is alive and is this profile's
browser (a lock left by a crash or a reboot points at a dead or reused pid).

Placement: launched with --window-position on the Edge's output, found in
`xrandr --query` as the connected output whose current mode is EDGE_MODE, or
set with EDGE_POSITION="x,y". --kiosk then makes it fullscreen on that output.

Keeping it on top is _NET_WM_STATE_ABOVE via wmctrl, found by the shell page's
title with xdotool. Unlike Windows' topmost, an X11 window manager keeps
"above" itself once it is set, so the keeper only checks every KEEPER_INTERVAL.

X11 only. Under a Wayland session no client may place or raise another
client's window, so windows is None: the browser is still launched and
relaunched, but not pinned, and never killed for "having no window" (edged
cannot see it). A Wayland host would pin the kiosk with its compositor's own
rules instead (sway: for_window [title="^frakpanel shell"] move to output ...).

Needs: xdotool, wmctrl, xrandr (x11-xserver-utils), and DISPLAY -- the systemd
unit from install-linux.sh sets DISPLAY=:0 and waits for the session's X
server by restarting until it answers.

Interface (shared with kiosk_win.py): see that file's docstring.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

BROWSERS = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")
DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
PROFILE_DIR = os.path.join(DATA_HOME, "frakpanel-kiosk")
EDGE_MODE = (2560, 720)  # the Xeneon Edge's native mode
TITLE = "^frakpanel shell"  # the shell page's <title>
TOOL_TIMEOUT = 5
SHOT_TIMEOUT = 20


def run(*args: str) -> str | None:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=TOOL_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def screenshot() -> bytes:
    """The whole X display edged runs on (every screen, the Edge included) as PNG bytes,
    as edge-shot.sh takes it. Needs ImageMagick's import; OSError when it fails."""
    if not shutil.which("import"):
        raise OSError("ImageMagick's import is not installed (apt install imagemagick)")
    try:
        r = subprocess.run(["import", "-window", "root", "png:-"], capture_output=True, timeout=SHOT_TIMEOUT,
                           env=dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0")))
    except subprocess.SubprocessError as exc:
        raise OSError(f"import failed: {exc}") from exc
    if r.returncode != 0:
        raise OSError(f"import exited {r.returncode}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def edge_position() -> tuple[int, int] | None:
    env = os.environ.get("EDGE_POSITION")
    if env:
        x, _, y = env.partition(",")
        return int(x), int(y)
    out = run("xrandr", "--query") or ""
    for m in re.finditer(r"^\S+ connected (?:primary )?(\d+)x(\d+)\+(\d+)\+(\d+)", out, re.M):
        w, h, x, y = map(int, m.groups())
        if (w, h) == EDGE_MODE:
            return x, y
    return None


class X11:
    """The kiosk window through xdotool (find, map, activate) and wmctrl (above)."""

    KEEPER_INTERVAL = 1.0  # seconds; the window manager holds "above" itself, this only notices it being dropped

    def kiosk_window(self) -> int | None:
        out = run("xdotool", "search", "--name", TITLE)
        ids = [int(line) for line in (out or "").split() if line.isdigit()]
        return ids[0] if ids else None

    def on_top(self, wid: int) -> bool:
        state = run("xprop", "-id", str(wid), "_NET_WM_STATE") or ""
        return "_NET_WM_STATE_ABOVE" in state and "_NET_WM_STATE_HIDDEN" not in state

    def pin(self, wid: int, topmost: bool) -> None:
        if topmost:
            run("xdotool", "windowmap", str(wid))  # un-minimize, as SW_RESTORE does on Windows
        run("wmctrl", "-i", "-r", hex(wid), "-b", ("add" if topmost else "remove") + ",above")

    def try_focus(self, wid: int) -> None:
        run("xdotool", "windowactivate", str(wid))


class Kiosk:
    def __init__(self, log):
        self.log = log
        self.profile_dir = PROFILE_DIR
        missing = [t for t in ("xdotool", "wmctrl", "xprop") if shutil.which(t) is None]
        if os.environ.get("XDG_SESSION_TYPE") == "wayland" or not os.environ.get("DISPLAY"):
            self.windows = None
            log("no X11 display: the kiosk runs but is not pinned (see kiosk_linux.py)")
        elif missing:
            self.windows = None
            log(f"missing {', '.join(missing)}: the kiosk runs but is not pinned")
        else:
            self.windows = X11()

    def running(self) -> bool:
        try:
            target = os.readlink(os.path.join(PROFILE_DIR, "SingletonLock"))
            pid = int(target.rsplit("-", 1)[1])
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return os.path.basename(PROFILE_DIR).encode() in f.read()
        except (OSError, ValueError, IndexError):
            return False

    def launch(self, flags: list[str]) -> None:
        exe = next((b for b in BROWSERS if shutil.which(b)), None)
        if exe is None:
            raise OSError(f"no browser: install one of {', '.join(BROWSERS)}")
        pos = edge_position()
        if pos is None:
            self.log(f"no connected {EDGE_MODE[0]}x{EDGE_MODE[1]} output in xrandr; launching at 0,0")
        x, y = pos or (0, 0)
        subprocess.Popen([exe, *flags, f"--window-position={x},{y}",
                          "--password-store=basic",  # never a keyring prompt on the panel
                          "--class=frakpanel"],
                         close_fds=True, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def kill(self) -> None:
        """Kill the kiosk browser -- matched on its profile dir, so a normal browser window is never touched."""
        try:
            subprocess.run(["pkill", "-f", "--", f"--user-data-dir=.*{os.path.basename(PROFILE_DIR)}"], timeout=20)
            self.log("killed kiosk browser")
        except (OSError, subprocess.SubprocessError) as exc:
            self.log(f"kill_browser: {exc!r}")
