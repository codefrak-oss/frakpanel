r"""
kiosk_win.py -- edged.py's kiosk browser and window handling on Windows.

Google Chrome from Program Files, its own profile under %LOCALAPPDATA%, and a
handful of user32 calls (Win32) to find the kiosk window by its title and keep
it topmost over the taskbar and pop-ups. "Is it running" is the profile's
lockfile, which Chrome holds open without delete sharing.

Interface (shared with kiosk_linux.py): Kiosk(log) with .windows (an object
with KEEPER_INTERVAL, kiosk_window(), on_top(w), pin(w, topmost), try_focus(w),
or None when windows cannot be managed), running(), launch(flags), kill().
"""
from __future__ import annotations

import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "frakpanel-kiosk")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def browser_running() -> bool:
    # Chromium holds <profile>\lockfile open (no delete sharing) while the profile is in use.
    lock = os.path.join(PROFILE_DIR, "lockfile")
    if not os.path.exists(lock):
        return False
    try:
        os.remove(lock)
    except PermissionError:
        return True
    except OSError:
        return False
    return False


class Win32:
    """The few user32 calls edged needs to find and pin the kiosk window."""

    KEEPER_INTERVAL = 0.25  # seconds; touching the panel pops the taskbar up, so hide it again before it is noticed

    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_FLAGS = 0x1 | 0x2 | 0x40  # SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes, self.wintypes = ctypes, wintypes
        u = self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32")
        u.GetForegroundWindow.restype = wintypes.HWND
        u.WindowFromPoint.restype = wintypes.HWND
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.GetAncestor.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        self.enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def kiosk_window(self) -> int | None:
        found: list[int] = []
        u, ctypes = self.user32, self.ctypes

        def visit(hwnd, _lparam):
            n = u.GetWindowTextLengthW(hwnd)
            if n and u.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value.startswith("frakpanel shell"):  # the shell page's <title>
                    found.append(hwnd)
            return True

        u.EnumWindows(self.enum_proc(visit), 0)
        return found[0] if found else None

    def on_top(self, hwnd: int) -> bool:
        """True if hwnd is what's visible at its center and along its bottom edge (where the taskbar sits)."""
        w, u = self.wintypes, self.user32
        rect = w.RECT()
        u.GetWindowRect(hwnd, self.ctypes.byref(rect))
        cx = (rect.left + rect.right) // 2
        for y in ((rect.top + rect.bottom) // 2, rect.bottom - 8):
            under = u.WindowFromPoint(w.POINT(cx, y))
            if not under or u.GetAncestor(under, 2) != hwnd:  # GA_ROOT
                return False
        return True

    def pin(self, hwnd: int, topmost: bool) -> None:
        u = self.user32
        u.ShowWindow(hwnd, 9)  # SW_RESTORE
        u.SetWindowPos(hwnd, self.wintypes.HWND(self.HWND_TOPMOST if topmost else self.HWND_NOTOPMOST), 0, 0, 0, 0, self.SWP_FLAGS)

    def try_focus(self, hwnd: int) -> None:
        # Windows gates SetForegroundWindow from background processes; attaching to
        # the foreground thread's input queue sometimes gets past it. Focus is a
        # bonus here -- being topmost is what keeps the panel covered.
        u = self.user32
        fg = u.GetForegroundWindow()
        fg_thread = u.GetWindowThreadProcessId(fg, None) if fg else 0
        me = self.kernel32.GetCurrentThreadId()
        if fg_thread and fg_thread != me and u.AttachThreadInput(me, fg_thread, True):
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
            u.AttachThreadInput(me, fg_thread, False)


class Kiosk:
    def __init__(self, log):
        self.log = log
        self.windows = Win32()
        self.profile_dir = PROFILE_DIR

    def running(self) -> bool:
        return browser_running()

    def launch(self, flags: list[str]) -> None:
        subprocess.Popen([CHROME, *flags, "--window-position=0,0"], close_fds=True)

    def kill(self) -> None:
        """Kill the kiosk browser -- matched on its profile dir, so a normal Chrome window is never touched."""
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
              f"Where-Object {{ $_.CommandLine -like '*{os.path.basename(PROFILE_DIR)}*' }} | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=20,
                           creationflags=0x08000000 if os.name == "nt" else 0)  # CREATE_NO_WINDOW
            self.log("killed kiosk browser")
        except (OSError, subprocess.SubprocessError) as exc:
            self.log(f"kill_browser: {exc!r}")
