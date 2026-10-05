"""
updater.py -- frakpanel's opt-in self-update, run as a thread inside edged.

Off unless <data>/update.json says otherwise:
    {"channel": "stable"}          stable releases only
    {"channel": "beta"}            stable and beta (every push to main)
    optional: "check_hours": 6, "github_token": "..." (only needed while the
    repo is private), "repo": "owner/name", "api": "https://api.github.com"
    (tests point this at a fake; FRAKPANEL_UPDATE_DELAY shortens the wait)

Only a copy installed from a release zip updates: one running from
<root>/versions/<ver>/ under launcher.py (see install-windows.ps1,
install-linux.sh). A checkout, or an install made from one ("-dev"), reports
itself and never touches anything.

An update downloads the release asset for this platform (the Windows zip
carries its own embedded Python; the plain zip is source only), unpacks it to
versions/<ver>/, points launcher.json at it with the old version kept as
"previous" and "pending" set, copies the new launcher.py over the root one, and
exits edged with EXIT_UPDATE. launcher.py starts the new version right away
and, if it keeps dying early, rolls back to "previous" by itself.

Releases are GitHub Releases on REPO: tag v<ver>, beta ones marked prerelease.
Versions look like 0.2.0 or 0.2.0-beta.14; 0.2.0 sorts above its betas.
Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import zipfile

REPO = "codefrak-oss/frakpanel"
EXIT_UPDATE = 75  # launcher.py: restart immediately on the new current version
FIRST_CHECK_DELAY = float(os.environ.get("FRAKPANEL_UPDATE_DELAY", 120))  # seconds after start, so a crash-looping build can't also be updating
KEEP_VERSIONS = 3  # unpacked versions kept on disk, current and previous included
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$")


def read_version(code_dir: str) -> str:
    """The VERSION file CI stamps into each release. In a git checkout it is only
    the next planned version, so that reads as "<ver>-dev", which never updates."""
    try:
        with open(os.path.join(code_dir, "VERSION"), encoding="utf-8") as f:
            v = f.read().strip() or "dev"
    except OSError:
        v = "dev"
    return f"{v}-dev" if os.path.exists(os.path.join(code_dir, ".git")) else v


def version_key(v: str) -> tuple | None:
    m = _VERSION_RE.match(v.lstrip("v"))
    if not m:
        return None
    major, minor, patch, beta = m.groups()
    # A stable release outranks every beta of the same number.
    return (int(major), int(minor), int(patch), 1 if beta is None else 0, int(beta or 0))


def asset_name(version: str) -> str:
    return f"frakpanel-{version}-windows-x64.zip" if sys.platform == "win32" else f"frakpanel-{version}.zip"


class Updater:
    def __init__(self, code_dir: str, data_dir: str, version: str, log):
        self.code_dir = code_dir
        self.data_dir = data_dir
        self.version = version
        self.log = log
        versions = os.path.dirname(code_dir)
        self.root = os.path.dirname(versions) if os.path.basename(versions) == "versions" else None
        self.latest: str | None = None
        self.checked: float | None = None
        self.error: str | None = None

    def config(self) -> dict:
        try:
            with open(os.path.join(self.data_dir, "update.json"), encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            self.error = f"update.json: {exc}"
            return {}

    def launcher_state(self) -> dict:
        try:
            with open(os.path.join(self.root, "launcher.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, TypeError):
            return {}

    def status(self) -> dict:
        return {"version": self.version, "channel": self.config().get("channel", "off"),
                "installed": self.root is not None, "latest": self.latest,
                "checked": self.checked and time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.checked)),
                "error": self.error}

    def run(self) -> None:
        time.sleep(FIRST_CHECK_DELAY)
        while True:
            cfg = self.config()
            if cfg.get("channel") in ("stable", "beta") and self.root and version_key(self.version):
                try:
                    self.error = None
                    self.check(cfg)
                except Exception as exc:  # offline, rate-limited, bad asset: try again next round
                    self.error = repr(exc)
                    self.log(f"update check failed: {exc!r}")
            time.sleep(max(0.25, float(cfg.get("check_hours", 6))) * 3600)

    def request(self, url: str, cfg: dict, accept: str) -> urllib.request.Request:
        headers = {"Accept": accept, "User-Agent": f"frakpanel/{self.version}"}
        if cfg.get("github_token"):
            headers["Authorization"] = f"Bearer {cfg['github_token']}"
        return urllib.request.Request(url, headers=headers)

    def check(self, cfg: dict) -> None:
        repo = cfg.get("repo", REPO)
        req = self.request(f"{cfg.get('api', 'https://api.github.com')}/repos/{repo}/releases?per_page=30", cfg,
                           "application/vnd.github+json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            releases = json.load(resp)
        beta = cfg.get("channel") == "beta"
        best, best_key = None, None
        for rel in releases:
            if rel.get("draft") or (rel.get("prerelease") and not beta):
                continue
            key = version_key(rel.get("tag_name", ""))
            if key and (best_key is None or key > best_key):
                best, best_key = rel, key
        self.checked = time.time()
        if best is None:
            return
        target = best["tag_name"].lstrip("v")
        self.latest = target
        if best_key <= version_key(self.version):
            return
        if target == self.launcher_state().get("rolled_back_from"):
            self.error = f"{target} was rolled back after failing to start; waiting for a newer release"
            return
        asset = next((a for a in best.get("assets", []) if a.get("name") == asset_name(target)), None)
        if asset is None:
            raise RuntimeError(f"release {target} has no {asset_name(target)}")
        self.log(f"updating {self.version} -> {target}")
        self.install(target, asset, cfg)

    def install(self, target: str, asset: dict, cfg: dict) -> None:
        versions = os.path.join(self.root, "versions")
        staging = os.path.join(versions, f".staging-{target}")
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging)
        archive = os.path.join(staging, asset["name"])
        req = self.request(asset["url"], cfg, "application/octet-stream")
        sha = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=300) as resp, open(archive, "wb") as f:
            for chunk in iter(lambda: resp.read(1 << 16), b""):
                sha.update(chunk)
                f.write(chunk)
        digest = asset.get("digest")  # "sha256:<hex>" on current GitHub
        if digest and digest != f"sha256:{sha.hexdigest()}":
            raise RuntimeError(f"{asset['name']}: checksum mismatch")
        unpacked = os.path.join(staging, "x")
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                dest = os.path.realpath(os.path.join(unpacked, name))
                if not dest.startswith(os.path.realpath(unpacked) + os.sep):
                    raise RuntimeError(f"{asset['name']}: unsafe path {name!r}")
            z.extractall(unpacked)
        tops = os.listdir(unpacked)
        src = os.path.join(unpacked, tops[0]) if len(tops) == 1 else unpacked
        if read_version(src) != target or not os.path.isfile(os.path.join(src, "edged.py")):
            raise RuntimeError(f"{asset['name']}: does not contain frakpanel {target}")
        final = os.path.join(versions, target)
        shutil.rmtree(final, ignore_errors=True)
        os.replace(src, final)
        shutil.rmtree(staging, ignore_errors=True)

        state = {"current": target, "previous": self.version, "pending": True}
        tmp = os.path.join(self.root, "launcher.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, os.path.join(self.root, "launcher.json"))
        # The running launcher keeps its old code until its own restart; that's fine.
        shutil.copyfile(os.path.join(final, "launcher.py"), os.path.join(self.root, "launcher.py.tmp"))
        os.replace(os.path.join(self.root, "launcher.py.tmp"), os.path.join(self.root, "launcher.py"))
        self.prune(versions, keep={target, self.version})
        self.log(f"installed {target}; restarting into it")
        os._exit(EXIT_UPDATE)  # from a thread: sys.exit would only end the thread

    def prune(self, versions: str, keep: set[str]) -> None:
        found = sorted((v for v in os.listdir(versions) if version_key(v)), key=version_key, reverse=True)
        for v in found:
            if v in keep:
                continue
            if len(keep) >= KEEP_VERSIONS:
                shutil.rmtree(os.path.join(versions, v), ignore_errors=True)
            else:
                keep.add(v)
