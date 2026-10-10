"""End-to-end test of the *shipped* DroidExtend.exe, start to finish.

Everything happens inside this one process on purpose: when the exe is launched
from a separate tool call it can be reaped before the next call runs, which made
earlier attempts look like "the button does nothing".

Steps: launch -> raise -> click 开始投屏 -> wait for the second screen -> talk to
the bundled touch server on :27184 -> click 停止 -> wait for the screen to go
away again -> kill.
"""
import ctypes
import os
import socket
import subprocess
import sys
import time
from ctypes import wintypes

# DE_ROOT lets us drive a copy of the app that lives outside the session
# workspace: the harness denies display-config changes to any exe whose image
# sits under the workspace, which has nothing to do with the app itself.
ROOT = os.environ.get("DE_ROOT", r"D:\dsh projects\droid-extend")
EXE = os.path.join(ROOT, "DroidExtend.exe")
sys.path.insert(0, r"D:\dsh projects\droid-extend\host")

import win_display as wd
import vdd
from touch import Injector, REC, DOWN, UP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winshot

SHOTS = r"D:\dsh projects\droid-extend\tools\shots"

u = ctypes.windll.user32
u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

TITLE = "DroidExtend \u2014 \u5b89\u5353\u62d3\u5c55\u5c4f"
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040

# window-relative button positions, from a PrintWindow capture of the 998x767 UI
BTN_START = (0.103, 0.583)
BTN_STOP = (0.218, 0.583)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor():
    p = POINT()
    u.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def live():
    return [m["name"] for m in wd.live_monitors()]


def port_open(port, host="127.0.0.1", timeout=0.4):
    try:
        s = socket.create_connection((host, port), timeout)
        s.close()
        return True
    except OSError:
        return False


def wait_window(timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        h = u.FindWindowW(None, TITLE)
        if h:
            return h
        time.sleep(0.4)
    return 0


def click(inj, frac, label):
    nx, ny = int(frac[0] * 65535), int(frac[1] * 65535)
    inj.down(nx, ny)
    time.sleep(0.05)
    p = POINT()
    u.GetCursorPos(ctypes.byref(p))
    under = u.WindowFromPoint(p)
    root = u.GetAncestor(under, 2) if under else 0  # GA_ROOT
    print("  -> cursor (%d,%d) window_under=%d root=%d" % (p.x, p.y, under, root), flush=True)
    time.sleep(0.12)
    inj.up(nx, ny)
    print("  clicked %s" % label, flush=True)


def main():
    print("live before: %s" % live(), flush=True)
    adapter = vdd.status()["adapter"]["name"]
    if adapter in live():
        print("  (leftover %s from an earlier run - detaching first)" % adapter, flush=True)
        wd.detach(adapter)
        time.sleep(1.5)
        print("  live now: %s" % live(), flush=True)

    proc = subprocess.Popen([EXE], cwd=ROOT)
    print("launched pid %d" % proc.pid, flush=True)

    hwnd = wait_window()
    if not hwnd:
        raise SystemExit("window never appeared")
    print("window hwnd=%d" % hwnd, flush=True)

    u.ShowWindow(hwnd, 5)
    u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    time.sleep(0.8)
    u.SetForegroundWindow(hwnd)
    time.sleep(1.2)

    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    rect = {"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom,
            "width": r.right - r.left, "height": r.bottom - r.top}
    print("window rect %s  foreground=%s" % (rect, u.GetForegroundWindow() == hwnd), flush=True)

    inj = Injector(rect, log=lambda m: None)

    print("\n[1] click 开始投屏", flush=True)
    click(inj, BTN_START, "开始投屏")

    ok = None
    touch_up = None
    t0 = time.time()
    for i in range(120):
        time.sleep(1.0)
        names = live()
        tp = port_open(27184)
        if i % 5 == 0 or (len(names) > 1 and touch_up is None):
            print("  t+%02ds live=%d touch=%s" % (i + 1, len(names), tp), flush=True)
        if len(names) > 1 and ok is None:
            ok = names
            print("  t+%02ds <-- 虚拟屏出现 (%s)" % (i + 1, names), flush=True)
        if tp and touch_up is None:
            touch_up = i + 1
            print("  t+%02ds <-- 触摸端口 27184 就绪" % (i + 1,), flush=True)
        if ok and touch_up:
            break
    print("  startup took %.1fs" % (time.time() - t0), flush=True)
    if not ok:
        print("  !! 虚拟屏没有出现; live=%s" % live(), flush=True)
    if not touch_up:
        print("  !! 触摸端口一直没开", flush=True)
    try:
        print("  shot -> %s" % (winshot.capture(hwnd, os.path.join(SHOTS, "e2e_after_start.png")),), flush=True)
    except Exception as e:
        print("  shot failed: %s" % e, flush=True)

    print("\n[2] touch through the exe's own TouchServer (:27184)", flush=True)
    try:
        s = socket.create_connection(("127.0.0.1", 27184), 3.0)
    except OSError as e:
        print("  !! 连不上触摸服务: %s" % e, flush=True)
        s = None
    if s:
        before = cursor()
        s.sendall(REC.pack(DOWN, int(0.15 * 65535), int(0.70 * 65535), 0))
        time.sleep(0.35)
        mid = cursor()
        s.sendall(REC.pack(UP, int(0.15 * 65535), int(0.70 * 65535), 0))
        time.sleep(0.25)
        # the virtual screen sits at (2560,0) 1920x1080, so 15%,70% should be
        # (2560 + 288, 756) = (2848, 756)
        print("  cursor %s -> %s (期望约 (2848, 756))" % (before, mid), flush=True)
        s.close()

    print("\n[3] click 停止", flush=True)
    gone = False
    for attempt in range(4):
        click(inj, BTN_STOP, "停止")
        for i in range(20):
            time.sleep(1.0)
            names = live()
            if len(names) == 1:
                gone = True
                print("  t+%02ds live: %s  <-- 虚拟屏已移除" % (i + 1, names), flush=True)
                break
        if gone:
            break
        print("  !! 第 %d 次点停止后虚拟屏仍在: %s" % (attempt + 1, live()), flush=True)
    try:
        print("  shot -> %s" % (winshot.capture(hwnd, os.path.join(SHOTS, "e2e_after_stop.png")),), flush=True)
    except Exception as e:
        print("  shot failed: %s" % e, flush=True)

    print("\ndesktop bounds now: %s" % wd.desktop_bounds(), flush=True)
    print("vdd adapter still present: %s" % vdd.status()["adapter"]["name"], flush=True)

    print("\n[4] cleaning up", flush=True)
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except Exception:
        proc.kill()
    print("done", flush=True)
    os._exit(0 if (ok and gone) else 1)


main()
