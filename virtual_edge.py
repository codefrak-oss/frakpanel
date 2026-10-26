r"""
virtual_edge.py -- a virtual Xeneon Edge on macOS: no panel needed.

Starts `edged.py --no-browser` (no kiosk) and opens a Google Chrome app window
(no tabs, no address bar) on edged's /virtual page: the 2560x720 shell scaled
to fit the window, letterboxed, so the window can be resized freely. Mouse
clicks and drags act as single touches. Without Chrome it falls back to
`open <url>` in the default browser. Quitting (Ctrl-C, or closing the Chrome
window) stops the edged it started.

    python3 virtual_edge.py                     # edged + a 1280x360 window
    python3 virtual_edge.py --size 1920x540     # starting window size
    python3 virtual_edge.py --no-edged          # attach to a running edged
    python3 virtual_edge.py --hub http://nuc.local:7781 --display work
                                                # a remote hub; no local edged
    python3 virtual_edge.py --launcher <root>/launcher.py
                                                # edged through an install's launcher,
                                                # so it self-updates (install-virtual-edge.sh)

Each window shows the layout of its display id (--display, default this
machine's hostname), kept by the hub's edged apart from the panel's and every
other window's. Without --hub it is the local edged's "panel" layout as before.

Python standard library only.
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

PANEL_W, PANEL_H = 2560, 720
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE = os.path.dirname(os.path.abspath(__file__))


def fit_scale(w: float, h: float) -> float:
    """The scale that fits the 2560x720 panel in a w x h window, aspect kept."""
    return min(w / PANEL_W, h / PANEL_H)


def parse_size(s: str) -> tuple[int, int]:
    """'1280x360' -> (1280, 360); argparse.ArgumentTypeError otherwise."""
    w, sep, h = s.lower().partition("x")
    if not sep or not w.isdigit() or not h.isdigit() or int(w) < 1 or int(h) < 1:
        raise argparse.ArgumentTypeError(f"size must be WxH, e.g. 1280x360, not {s!r}")
    return int(w), int(h)


def chrome_args(chrome: str, url: str, size: tuple[int, int], profile: str) -> list[str]:
    return [chrome, f"--app={url}", f"--window-size={size[0]},{size[1]}",
            f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check"]


def default_display() -> str:
    """This machine's hostname as a display id: lowercase [a-z0-9_-], at most 32 chars."""
    name = re.sub(r"[^a-z0-9_-]+", "-", socket.gethostname().split(".")[0].lower()).strip("-")[:32]
    return name or "virtual"


def parse_display(s: str) -> str:
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", s.lower()):
        raise argparse.ArgumentTypeError(f"display must be [a-z0-9_-], 1-32 chars, not {s!r}")
    return s.lower()


def hub_base(a: argparse.Namespace) -> str:
    return a.hub.rstrip("/") if a.hub else f"http://127.0.0.1:{a.port}"


def virtual_url(base: str, display: str | None) -> str:
    return base + "/virtual" + ("?display=" + urllib.parse.quote(display) if display else "")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A resizable virtual Xeneon Edge window (macOS).")
    p.add_argument("--port", type=int, default=int(os.environ.get("EDGED_PORT", 7781)))
    p.add_argument("--size", type=parse_size, default=(1280, 360), help="starting window size, WxH")
    p.add_argument("--no-edged", action="store_true", help="attach to an edged that is already running")
    p.add_argument("--hub", help="a remote edged's base URL, e.g. http://nuc.local:7781; starts no local edged")
    p.add_argument("--launcher", help="run the local edged through this installed launcher.py, which applies "
                                      "updates and restarts edged into them (install-virtual-edge.sh)")
    p.add_argument("--display", type=parse_display,
                   help="this window's display id (its own layout); default: the hostname with --hub, "
                        "else the panel's layout")
    a = p.parse_args(argv)
    if a.hub:
        if not a.hub.startswith(("http://", "https://")):
            p.error(f"--hub must be an http(s):// URL, not {a.hub!r}")
        a.no_edged = True
        a.display = a.display or default_display()
    return a


def edged_cmd(a: argparse.Namespace) -> list[str]:
    if a.launcher:
        return [sys.executable, a.launcher, "--no-browser"]
    return [sys.executable, os.path.join(HERE, "edged.py"), "--no-browser"]


def wait_health(base: str, timeout: float = 15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(base + "/layout", timeout=2):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def main(argv: list[str]) -> int:
    a = parse_args(argv)
    base = hub_base(a)
    env = dict(os.environ, EDGED_PORT=str(a.port))
    edged = None if a.no_edged else subprocess.Popen(edged_cmd(a), env=env)
    try:
        if not wait_health(base):
            print(f"virtual_edge: edged not answering on {base}", file=sys.stderr)
            return 1
        url = virtual_url(base, a.display)
        if os.path.exists(CHROME):
            with tempfile.TemporaryDirectory(prefix="virtual-edge-") as profile:
                subprocess.call(chrome_args(CHROME, url, a.size, profile))
        else:
            subprocess.call(["open", url])
            print(f"virtual_edge: no Google Chrome; opened {url} in the default browser. Ctrl-C to stop.")
            if edged:
                edged.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if edged:
            edged.terminate()
            try:
                edged.wait(15)  # a launcher gives its edged 10 s to stop
            except subprocess.TimeoutExpired:
                edged.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
