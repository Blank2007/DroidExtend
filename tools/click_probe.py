"""Is the click itself reaching the tkinter window, or just the pointer?

Clicks 刷新 (which appends a log line) and prints the cursor before/after, so a
silent coordinate error is distinguishable from a click that never lands.
"""
import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import ctypes
import os
import sys
import time
from ctypes import wintypes

ROOT = REPO
sys.path.insert(0, os.path.join(ROOT, "host"))

u = ctypes.windll.user32
u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

TITLE = "DroidExtend \u2014 \u5b89\u5353\u62d3\u5c55\u5c4f"


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor():
    p = POINT()
    u.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


hwnd = u.FindWindowW(None, TITLE)
if not hwnd:
    raise SystemExit("window not found")
r = wintypes.RECT()
u.GetWindowRect(hwnd, ctypes.byref(r))
print("hwnd=%d rect=%d,%d %dx%d" % (hwnd, r.left, r.top, r.right - r.left, r.bottom - r.top))
print("WindowFromPoint at 刷新 ->", u.WindowFromPoint(POINT(r.left + 561, r.top + 110)))
print("our hwnd               ->", hwnd)

from touch import Injector

inj = Injector({"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom,
                "width": r.right - r.left, "height": r.bottom - r.top}, log=print)

# 刷新 button lives at about (561, 110) inside the 998x767 window
nx = (561.0 / 998) * 65535
ny = (110.0 / 767) * 65535

print("\ncursor before:", cursor())
inj._move_to(int(nx), int(ny))
time.sleep(0.3)
print("cursor after move:", cursor(), "expected",
      (r.left + 561, r.top + 110))

print("\n-- click 1: SendInput (what touch.py uses) --")
inj.down(int(nx), int(ny))
time.sleep(0.12)
inj.up(int(nx), int(ny))
time.sleep(1.5)

print("-- click 2: SetCursorPos + mouse_event (legacy path) --")
u.SetCursorPos(r.left + 561, r.top + 110)
time.sleep(0.2)
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
u.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
time.sleep(0.12)
u.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
time.sleep(1.5)

print("-- click 3: SetCursorPos + SendInput --")
inj._move_to(int(nx), int(ny))
time.sleep(0.2)
inj.down(int(nx), int(ny))
time.sleep(0.12)
inj.up(int(nx), int(ny))
time.sleep(1.0)
print("done")
