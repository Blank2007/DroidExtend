"""Host-side injector check, with no phone involved.

Connects to the touch server exactly the way the app does and sends a few
records, then confirms the Windows cursor moved and the events were counted.
Isolates "is the injector broken" from "is the phone sending".
"""
import ctypes
import os
import socket
import sys
import time

ROOT = r"D:\dsh projects\droid-extend"
sys.path.insert(0, os.path.join(ROOT, "host"))

import win_display as wd
import vdd
from touch import TouchServer, REC, DOWN, MOVE, UP, SCROLL, RCLICK

wd.set_dpi_aware()
user32 = ctypes.windll.user32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


name = vdd.find_vdd_adapter()["name"]
rect = wd.desktop_rect(name)
print("virtual display %s -> %s" % (name, rect))
print("virtual desktop   -> %s" % (wd.live_monitors(),))

ts = TouchServer(rect=rect, port=27184, log=lambda m: print("  " + m))
ts.start()
time.sleep(0.3)

s = socket.create_connection(("127.0.0.1", 27184), 3)
print("connected; cursor before = %s" % (cursor(),))

# 20% across, 50% down
s.sendall(REC.pack(DOWN, int(0.20 * 65535), int(0.50 * 65535), 0))
time.sleep(0.4)
print("after DOWN (20%%,50%%) = %s" % (cursor(),))

s.sendall(REC.pack(MOVE, int(0.80 * 65535), int(0.25 * 65535), 0))
time.sleep(0.4)
print("after MOVE (80%%,25%%) = %s" % (cursor(),))

s.sendall(REC.pack(UP, int(0.80 * 65535), int(0.25 * 65535), 0))
time.sleep(0.3)

n = ts.stats["events"]
s.sendall(REC.pack(SCROLL, 0, 0, (3 & 0xFFFF)))
time.sleep(0.3)
print("after SCROLL = %s (events %d -> %d)" % (cursor(), n, ts.stats["events"]))

s.sendall(REC.pack(RCLICK, int(0.5 * 65535), int(0.5 * 65535), 0))
time.sleep(0.3)
print("final stats = %s" % ts.stats)
print("expected cursor for 80%%,25%% on %s = %d,%d" % (
    rect, rect["left"] + int(0.80 * rect["width"]), rect["top"] + int(0.25 * rect["height"])))

s.close()
ts.stop()
print("done")
