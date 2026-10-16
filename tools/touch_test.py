"""End-to-end touch test: does a finger on the phone move the PC's pointer?

Drives the real pipeline (capture -> server -> app) and the real input tunnel
(27184), then pokes the phone with `input tap` / `input swipe` and checks where
the Windows cursor ended up and what the host's injector counted.

Run with a phone attached.
"""
import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import ctypes
import os
import subprocess
import sys
import time

ROOT = REPO
sys.path.insert(0, os.path.join(ROOT, "host"))

import win_display as wd
import vdd
from capture import Capture
from server import StreamServer, HOST_PORT
from touch import TouchServer, INPUT_PORT
from adb import Adb

ADB = os.path.join(ROOT, "tools", "platform-tools", "adb.exe")
W, H, HZ, FPS = 1920, 1080, 60, 60

log = lambda m: print("  " + m, flush=True)
wd.set_dpi_aware()
user32 = ctypes.windll.user32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


adb = Adb()
rows = [r for r in adb.devices() if r["state"] == "device"]
if not rows:
    print("NO DEVICE")
    sys.exit(1)
SERIAL = rows[0]["serial"]
print("phone: %s (%s)" % (SERIAL, rows[0]["label"]))

name = vdd.find_vdd_adapter()["name"]
cur = wd.desktop_rect(name) or {}
if (cur.get("width"), cur.get("height"), cur.get("hz")) != (W, H, HZ) or cur.get("left") != 2560:
    print("set_mode rc=%s" % wd.set_mode(name, W, H, HZ, 2560, 0))
    time.sleep(1.5)
rect = wd.desktop_rect(name)
print("virtual display %s -> %s" % (name, rect))

# a static desktop keeps the encoder quiet; nothing here depends on motion
adb.reverse_remove_all(SERIAL)
print("reverse video: %s" % (adb.reverse(SERIAL, HOST_PORT, HOST_PORT),))
print("reverse input: %s" % (adb.reverse(SERIAL, INPUT_PORT, INPUT_PORT),))
print("reverse list: %s" % adb.reverse_list(SERIAL)[1].strip().replace("\n", " | "))

cfg = {"encoder": "h264_nvenc", "backend": "auto", "fps": FPS, "bitrate_mbps": 40,
       "rate_control": "vbr", "quality": 20,
       "gop": FPS * 2, "draw_mouse": False, "preset": "p4", "spatial_aq": True}

adb.force_stop(SERIAL)
time.sleep(0.8)
state = {"t": 0.0}


def on_client(addr):
    state["t"] = time.time()
    print("  client %s" % (addr,), flush=True)
    server.send_header(rect["width"], rect["height"], FPS)


server = StreamServer(port=HOST_PORT, log=log, on_client=on_client)
server.start()
touch = TouchServer(rect=rect, port=INPUT_PORT, log=log)
touch.start()
cap = Capture(cfg, rect, log=log)
print("capture backend: %s" % cap.start())
print("start_app: %s" % (adb.start_app(SERIAL),))
subprocess.run([ADB, "-s", SERIAL, "shell", "input", "keyevent", "KEYCODE_WAKEUP"],
               capture_output=True)
subprocess.run([ADB, "-s", SERIAL, "shell", "wm", "dismiss-keyguard"], capture_output=True)

# ------------------------------------------------------------- pump + probe
size = subprocess.run([ADB, "-s", SERIAL, "shell", "wm", "size"],
                      capture_output=True, text=True).stdout.strip()
print("phone display: %s" % size)
# 2136x3200 physical; the activity is landscape, so probe in that frame
px, py = 1600, 700


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        u = cap.get_units(0.3)
        if u:
            server.put(u)


pump(6)
# dumpsys window spits out GBK-hostile bytes; decode leniently
f = subprocess.run([ADB, "-s", SERIAL, "shell", "dumpsys", "window"],
                   capture_output=True).stdout.decode("utf8", "replace")
for l in f.splitlines():
    if "mCurrentFocus" in l:
        print("focus: %s" % l.strip())
print("tap at phone (%d,%d); cursor before = %s" % (px, py, cursor()))
subprocess.run([ADB, "-s", SERIAL, "shell", "input", "tap", str(px), str(py)],
               capture_output=True)
time.sleep(0.6)
pump(1.5)
print("cursor after tap       = %s" % (cursor(),))
print("touch stats            = %s" % touch.stats)

before = touch.stats["events"]
print("swipe (320,900) -> (1500,400)")
subprocess.run([ADB, "-s", SERIAL, "shell", "input", "swipe",
                "320", "900", "1500", "400", "400"], capture_output=True)
time.sleep(0.6)
pump(1.5)
print("cursor after swipe     = %s" % (cursor(),))
print("touch stats            = %s" % touch.stats)
print("events from swipe      = %d" % (touch.stats["events"] - before))

# the phone's own touch stream should also have reached the host's injector.
# Decode explicitly: the app log is UTF-8 with Chinese in it, and with
# text=True Python would use the console's GBK code page and die on the first
# byte it cannot map -- taking stdout down with it.
r = subprocess.run([ADB, "-s", SERIAL, "logcat", "-d", "-s", "DroidExtend:I"],
                   capture_output=True)
print("--- app log tail ---")
for l in r.stdout.decode("utf8", "replace").splitlines()[-6:]:
    print(l)

cap.stop()
adb.force_stop(SERIAL)
# silence the worker threads before the interpreter shuts down: a daemon thread
# printing to a closing stdout aborts the process with _enter_buffered_busy
touch.log = lambda m: None
server.log = lambda m: None
touch.stop()
server.stop()
sys.stdout.flush()
os._exit(0)
