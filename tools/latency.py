"""Measure capture -> phone-display latency.

The protocol carries a host timestamp on every frame (DXS2), so the phone can
report the real end-to-end figure instead of us guessing.  We drive the worst
case - a full-screen animated pattern on the virtual display - and read the
"perf ... latency_avg_ms=" lines the receiver logs every two seconds.

Run with a phone attached.  Usage:  python tools\\latency.py [seconds]
"""
import os
import subprocess
import sys
import time

ROOT = r"D:\dsh projects\droid-extend"
sys.path.insert(0, os.path.join(ROOT, "host"))

import win_display as wd
import vdd
from capture import Capture
from server import StreamServer, HOST_PORT
from adb import Adb

ADB = os.path.join(ROOT, "tools", "platform-tools", "adb.exe")
RUN_SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 22.0
W, H, HZ, FPS = 1920, 1080, 60, 60

log = lambda m: print("  " + m, flush=True)
wd.set_dpi_aware()

adb = Adb()
rows = [r for r in adb.devices() if r["state"] == "device"]
if not rows:
    print("NO DEVICE")
    sys.exit(1)
SERIAL = rows[0]["serial"]
print("phone: %s (%s)" % (SERIAL, rows[0]["label"]))

# ---------------------------------------------------------------- 1. display
# vdd.find_vdd_adapter matches the Root\MttVDD hardware id first; the generic
# win_display.find_virtual_display() also matches ten GameViewer adapters.
name = vdd.find_vdd_adapter()["name"]
cur = wd.desktop_rect(name) or {}
if (cur.get("width"), cur.get("height"), cur.get("hz")) != (W, H, HZ) or cur.get("left") != 2560:
    print("set_mode rc=%s" % wd.set_mode(name, W, H, HZ, 2560, 0))
    time.sleep(1.5)
rect = wd.desktop_rect(name)
print("virtual display %s -> %s" % (name, rect))

# ------------------------------------------------- 2. worst case on that screen
win = subprocess.Popen([sys.executable,
                        os.path.join(ROOT, "tools", "testwindow.py"), "2560", "--anim"])
time.sleep(2.0)
print("test pattern pid=%s alive=%s" % (win.pid, win.poll() is None))

# ------------------------------------------------------------------ 3. tunnel
adb.reverse_remove_all(SERIAL)
print("adb reverse: %s" % (adb.reverse(SERIAL, HOST_PORT, HOST_PORT),))

# ---------------------------------------------------------------- 4. pipeline
cfg = {"encoder": "h264_nvenc", "backend": "auto", "fps": FPS, "bitrate_mbps": 40,
       "rate_control": "vbr", "quality": 20,
       "gop": FPS * 2, "draw_mouse": False, "preset": "p4", "spatial_aq": True}

adb.force_stop(SERIAL)
time.sleep(0.8)
state = {"t": 0.0}


def on_client(addr):
    state["t"] = time.time()
    print("  CLIENT from %s" % (addr,), flush=True)
    server.send_header(rect["width"], rect["height"], FPS)


server = StreamServer(port=HOST_PORT, log=log, on_client=on_client)
server.start()
cap = Capture(cfg, rect, log=log)
print("capture backend: %s" % cap.start())

print("start_app: %s" % (adb.start_app(SERIAL),))
subprocess.run([ADB, "-s", SERIAL, "shell", "input", "keyevent", "KEYCODE_WAKEUP"],
               capture_output=True)
subprocess.run([ADB, "-s", SERIAL, "shell", "wm", "dismiss-keyguard"], capture_output=True)

deadline = time.time() + RUN_SECONDS
while time.time() < deadline:
    units = cap.get_units(0.4)
    if units:
        server.put(units)

print("FINAL capture: frames=%d bytes=%d" % (cap.stats["frames"], cap.stats["bytes"]))
print("FINAL server : %s" % server.stats)

r = subprocess.run([ADB, "-s", SERIAL, "logcat", "-d", "-s", "DroidExtend:I"],
                   capture_output=True, text=True)
lines = [l for l in r.stdout.splitlines()
         if "latency_avg_ms" in l or "handshake" in l or "dropping" in l]
print("--- receiver reports (%d) ---" % len(lines))
for l in lines:
    print(l)

cap.stop()
win.terminate()
adb.force_stop(SERIAL)
server.stop()
print("done")
