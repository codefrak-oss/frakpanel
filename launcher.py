"""
launcher.py -- the stable entry point of an installed frakpanel. The Scheduled
Task (Windows) or systemd user unit (Linux) runs this from the install root:

    <root>/launcher.py       this file (updater.py replaces it on update)
    <root>/launcher.json     {"current": "0.2.0", "previous": "0.1.0", "pending": bool}
    <root>/versions/<ver>/   one unpacked release each (the Windows ones carry a
                             python/ that only the installer uses)
    <root>/runtime/          (Windows) the embedded Python that runs this file
                             and edged. One fixed path on purpose: Windows
                             Firewall rules are per program, and a new path
                             per version meant a new firewall prompt on the
                             panel at every update, which nobody answers, so
                             it turns into a block rule. A new Python reaches
                             runtime/ only by rerunning the installer.
    <root>/data/             state shared by every version: layout, relays,
                             local_tiles.json, update.json, edged.log, launcher.log

It runs versions/<current>/edged.py as a child with FRAKPANEL_DATA=<root>/data,
on this same interpreter, and starts it again whenever it exits: at once after an update (exit EXIT_UPDATE),
after RESTART_DELAY otherwise.

Rollback: while "pending" (set by an update), a child that dies within
HEALTHY_AFTER seconds counts as a failed start. After MAX_FAILED_STARTS of
those, current goes back to previous. Surviving HEALTHY_AFTER clears pending.

Standard library only; must keep working on whatever older Python an install
bootstrapped runtime/ from, so keep it plain.
"""
import json
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(ROOT, "launcher.json")
DATA = os.path.join(ROOT, "data")
EXIT_UPDATE = 75  # matches updater.EXIT_UPDATE
HEALTHY_AFTER = 120.0
MAX_FAILED_STARTS = 3
RESTART_DELAY = 10.0


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n"
    try:
        with open(os.path.join(DATA, "launcher.log"), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    if sys.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_PATH + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(STATE_PATH + ".tmp", STATE_PATH)


child = None


def stop(signum, frame):
    # systemd (KillMode=process, so the kiosk browser survives) signals only
    # this process; take edged down with it, or a restart finds the port taken.
    if child and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
    sys.exit(0)


def main():
    global child
    signal.signal(signal.SIGTERM, stop)
    os.makedirs(DATA, exist_ok=True)
    env = dict(os.environ, FRAKPANEL_DATA=DATA, PYTHONUNBUFFERED="1")
    failed = 0
    while True:
        state = load_state()
        current = state.get("current")
        vdir = os.path.join(ROOT, "versions", current or "")
        if not current or not os.path.isfile(os.path.join(vdir, "edged.py")):
            log(f"launcher.json names no runnable version ({current!r}); reinstall")
            time.sleep(60)
            continue
        log(f"starting {current}")
        started = time.monotonic()
        # Through -c, not the script path: Windows' embedded Python ignores both
        # PYTHONPATH and the script's own dir (its ._pth fixes sys.path), so
        # edged.py could not import its sibling modules.
        boot = ("import runpy, sys; sys.path.insert(0, sys.argv[1]); "
                "sys.argv = sys.argv[2:]; runpy.run_path(sys.argv[0], run_name='__main__')")
        child = subprocess.Popen([sys.executable, "-c", boot, vdir, os.path.join(vdir, "edged.py")] + sys.argv[1:],
                                 cwd=vdir, env=env)
        code = None
        while code is None:
            try:
                code = child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if state.get("pending") and time.monotonic() - started >= HEALTHY_AFTER:
                    state["pending"] = False
                    save_state(state)
                    failed = 0
                    log(f"{current} looks healthy; update confirmed")
        ran = time.monotonic() - started
        log(f"{current} exited with {code} after {ran:.0f}s")
        if code == EXIT_UPDATE:
            failed = 0
            continue
        if state.get("pending") and ran < HEALTHY_AFTER:
            failed += 1
            if failed >= MAX_FAILED_STARTS and state.get("previous"):
                log(f"{current} failed {failed} starts; rolling back to {state['previous']}")
                save_state({"current": state["previous"], "previous": current, "pending": False,
                            "rolled_back_from": current})
                failed = 0
                continue
        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    main()
