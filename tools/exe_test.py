"""Drive the *frozen* DroidExtend.exe through a full start/stop cycle.

This is the only test that exercises the artifact the user actually double-clicks:
it clicks the real 开始投屏 button with SendInput, waits for the phone to connect,
talks to the bundled TouchServer on :27184, then clicks 停止 and checks that the
virtual display is gone from the desktop again.
"""
import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import ctypes
import os
import socket
import sys
import time
import winreg
from ctypes import wintypes

ROOT = REPO
sys.path.insert(0, os.path.join(ROOT, "host"))

import win_display as wd
import vdd

TITLE = "DroidExtend \u2014 \u535a\u5c55\u5c4f".replace("\u535a\u5c55\u5c4f", "\u5b89\u5353\u62d3\u5c55\u5c4f")
TITLE = "DroidExtend \u2014 \u5b89\u5353\u62d3\u5c55\u5c4f"

u = ctypes.windll.user32
try:
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass

user32 = u


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def live_names():
    return [m["name"] for m in wd.live_monitors()]


def find_vdd():
    st = vdd.status()
    return st["adapter"]["name"] if st["state"] == "ready" else None


def click(inj, fx, fy, label):
    """fx/fy are 0..1 fractions of the window; Injector wants 0..65535.

    (Passing the fractions straight through was the bug that made the first
    version of this test click the title-bar corner at (32,32) three times and
    look like "the button does not work".)
    """
    nx, ny = int(fx * 65535), int(fy * 65535)
    inj.down(nx, ny)
    time.sleep(0.10)
    inj.up(nx, ny)
    print("  clicked %s at fraction (%.3f, %.3f) -> pixel (%d, %d)"
          % (label, fx, fy,
             inj.rect["left"] + int(fx * (inj.rect["width"] - 1)),
             inj.rect["top"] + int(fy * (inj.rect["height"] - 1))))


def raise_window(hwnd):
    """SetForegroundWindow alone is a no-op from a background process, and a
    click only reaches a window that is actually on top.  HWND_TOPMOST +
    SetWindowPos is allowed without foreground rights, so use that first.
    NOTE: never SW_RESTORE here -- it moves the window, and the caller has to
    read GetWindowRect *after* this runs or every click lands in the wrong
    place (which is exactly how the first version of this test silently
    clicked the log pane instead of the button)."""
    HWND_TOPMOST = -1
    SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
    user32.ShowWindow(hwnd, 5)  # SW_SHOW, never SW_RESTORE
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    time.sleep(0.5)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    print("  foreground=%d ours=%d match=%s"
          % (user32.GetForegroundWindow(), hwnd, user32.GetForegroundWindow() == hwnd))


def main():
    hwnd = user32.FindWindowW(None, TITLE)
    if not hwnd:
        raise SystemExit("DroidExtend window not found -- start DroidExtend.exe first")

    raise_window(hwnd)
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    rect = {"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom,
            "width": r.right - r.left, "height": r.bottom - r.top}
    print("window rect (after raising) %s" % rect)

    # reuse the already-verified injector: map normalised coords onto the window
    from touch import Injector
    inj = Injector(rect, log=lambda m: None)

    print("\n=== before ===")
    print("live: %s" % live_names())
    print("virtual desktop: %s" % wd.desktop_bounds())

    # 开始投屏 button sits at ~ (103, 447) inside a 998x767 window
    click(inj, 103.0 / 998, 447.0 / 767, "开始投屏")

    print("\n--- waiting for the session to come up ---")
    name = None
    for i in range(40):
        time.sleep(1.0)
        names = live_names()
        if len(names) > 1:
            name = [n for n in names if n != "\\\\.\\DISPLAY1"]
            print("  t+%02ds live: %s" % (i + 1, names))
            break
    if not name:
        print("  !! virtual display never appeared")
    else:
        print("  virtual display is %s" % name)

    print("\n--- touch through the *frozen* exe (port 27184) ---")
    try:
        s = socket.create_connection(("127.0.0.1", 27184), 3.0)
    except OSError as e:
        print("  !! cannot reach touch server: %s" % e)
        s = None
    if s:
        from touch import REC
        before = cursor()
        s.sendall(REC.pack(1, int(0.15 * 65535), int(0.70 * 65535), 0))
        time.sleep(0.35)
        after = cursor()
        s.sendall(REC.pack(3, int(0.15 * 65535), int(0.70 * 65535), 0))
        time.sleep(0.3)
        print("  cursor %s -> %s" % (before, after))
        if after != before:
            print("  OK 触摸注入生效")
        else:
            print("  !! 光标没动")
        s.close()

    print("\n--- clicking 停止 ---")
    click(inj, 218.0 / 998, 447.0 / 767, "停止")
    for i in range(20):
        time.sleep(1.0)
        names = live_names()
        if len(names) == 1:
            print("  t+%02ds live: %s  <-- 虚拟屏已移除" % (i + 1, names))
            break
    else:
        print("  !! 虚拟屏仍在: %s" % live_names())
    print("virtual desktop: %s" % wd.desktop_bounds())
    print("remaining vdd adapter: %s" % find_vdd())


main()
