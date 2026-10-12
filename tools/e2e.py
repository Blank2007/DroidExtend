"""End-to-end: Windows virtual display -> ffmpeg -> adb reverse -> phone screen.

Run with a phone attached.  Leaves artifacts in tools\\shots\\ so the result can
be inspected visually afterwards.
"""
import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, subprocess, sys, time

ROOT = REPO
SHOTS = os.path.join(ROOT, "tools", "shots")
os.makedirs(SHOTS, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "host"))

import win_display as wd
from capture import Capture
from server import StreamServer, HOST_PORT
from adb import Adb
from paths import ffmpeg_exe

ADB = os.path.join(ROOT, "tools", "platform-tools", "adb.exe")
SERIAL = None
W, H, HZ, FPS = 1920, 1080, 60, 60


def sh(*a, **kw):
    return subprocess.run(list(a), capture_output=True, **kw)


log = lambda m: print("  " + m, flush=True)
wd.set_dpi_aware()

adb = Adb()
rows = adb.devices()
ready = [r for r in rows if r["state"] == "device"]
if not ready:
    print("NO DEVICE:", rows)
    sys.exit(1)
SERIAL = ready[0]["serial"]
print("phone: %s (%s)" % (SERIAL, ready[0]["label"]))

# ---------------------------------------------------------------- 1. display
name = wd.find_virtual_display()[0]["name"]
print("virtual display: %s" % name)
cur = wd.desktop_rect(name)
print("current: %s" % cur)
if (cur["width"], cur["height"], cur["hz"]) != (W, H, HZ) or cur["left"] != 2560:
    rc = wd.set_mode(name, W, H, HZ, 2560, 0)
    print("set_mode rc=%s" % rc)
    time.sleep(1.5)
rect = wd.desktop_rect(name)
print("rect now: %s" % rect)
print("live: %s" % [("%s %dx%d@%d+%d+%d" % (m["name"], m["width"], m["height"], m["hz"],
                                             m["left"], m["top"])) for m in wd.live_monitors()])

# ------------------------------------------------------- 2. what is on screen
ff = ffmpeg_exe()
shot = os.path.join(SHOTS, "desktop_shot.png")
sh(ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
   "-init_hw_device", "d3d11va=gpu", "-filter_hw_device", "gpu",
   "-f", "lavfi", "-i", "ddagrab=output_idx=1:framerate=10:draw_mouse=0",
   "-vf", "hwdownload,format=bgra,select=gt(n\\,0)",
   "-frames:v", "1", shot)
print("desktop shot -> %s (%s bytes)" % (shot, os.path.getsize(shot) if os.path.exists(shot) else "MISSING"))

# ------------------------------------------------------------------ 3. adb
adb.reverse_remove_all(SERIAL)
rc, out = adb.reverse(SERIAL, HOST_PORT, HOST_PORT)
print("adb reverse rc=%s %s" % (rc, out.strip()))
print("reverse list: %s" % adb.reverse_list(SERIAL)[1].strip().replace("\n", " | "))

# ---------------------------------------------------------------- 4. stream
cfg = {"encoder": "h264_nvenc", "backend": "auto", "fps": FPS, "bitrate_mbps": 10,
       "rate_control": "vbr", "quality": 20,
       "gop": FPS * 2, "draw_mouse": False, "preset": "p4", "spatial_aq": True}

# stop any instance left over from an earlier run *before* the server listens,
# otherwise the stale one grabs the single client slot and is then killed by the
# new launch, leaving the fresh instance stuck on "connecting"
adb.force_stop(SERIAL)
time.sleep(0.8)

state = {"t": 0.0, "n": 0}


def on_client(addr):
    state["t"] = time.time()
    state["n"] += 1
    print("  CLIENT #%d from %s" % (state["n"], (addr,)), flush=True)
    server.send_header(rect["width"], rect["height"], FPS)


server = StreamServer(port=HOST_PORT, log=log, on_client=on_client)
server.start()
cap = Capture(cfg, rect, log=log)
backend = cap.start()
print("capture backend: %s" % backend)

rc, out = adb.start_app(SERIAL)
print("start_app rc=%s %s" % (rc, out.strip()))

# a locked/dozing phone composites a black frame over the activity, which is
# indistinguishable from a broken stream in a screenshot
sh(ADB, "-s", SERIAL, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
sh(ADB, "-s", SERIAL, "shell", "wm", "dismiss-keyguard")
time.sleep(1.0)


def focus():
    r = sh(ADB, "-s", SERIAL, "shell", "dumpsys", "window")
    txt = r.stdout.decode("utf8", "replace")
    for l in txt.splitlines():
        if "mCurrentFocus" in l:
            return l.strip()
    return "?"


f = focus()
if "NotificationShade" in f or "StatusBar" in f:
    # keyguard / pulled-down shade covers the activity
    sh(ADB, "-s", SERIAL, "shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(0.6)
    f = focus()
print("focus: %s" % f)

deadline = time.time() + 24
shots_done = 0
phone_shot = os.path.join(SHOTS, "phone_shot.png")
phone_rec = os.path.join(SHOTS, "phone_rec.mp4")
while time.time() < deadline:
    units = cap.get_units(0.4)
    if units:
        server.put(units)
    el = time.time() - state["t"] if state["t"] else 0
    if state["t"] and shots_done == 0 and el > 5:
        shots_done = 1
        print("capture: %s" % cap.stats)
        print("server : %s" % server.stats)
        r = sh(ADB, "-s", SERIAL, "exec-out", "screencap", "-p")
        open(phone_shot, "wb").write(r.stdout)
        print("phone screencap -> %d bytes" % len(r.stdout))
        sh(ADB, "-s", SERIAL, "shell", "screenrecord", "--time-limit", "4",
           "--bit-rate", "8000000", "/sdcard/dx.mp4", timeout=20)
    if state["t"] and shots_done == 1 and el > 13:
        shots_done = 2
        print("capture: %s" % cap.stats)
        print("server : %s" % server.stats)
        sh(ADB, "-s", SERIAL, "pull", "/sdcard/dx.mp4", phone_rec)
        print("phone record -> %s" % (os.path.getsize(phone_rec) if os.path.exists(phone_rec) else "MISSING"))
        r = sh(ADB, "-s", SERIAL, "exec-out", "screencap", "-p")
        open(os.path.join(SHOTS, "phone_shot2.png"), "wb").write(r.stdout)

print("FINAL capture: frames=%d bytes=%d fps=%.1f" %
      (cap.stats["frames"], cap.stats["bytes"], cap.fps()))
print("FINAL server : %s" % server.stats)

r = subprocess.run([ADB, "-s", SERIAL, "logcat", "-d", "-t", "200"], capture_output=True, text=True)
keep = [l for l in r.stdout.splitlines()
        if any(k in l for k in ("droidextend", "DroidExtend", "MediaCodec", "AndroidRuntime",
                                "droidextend", "StreamClient", "VideoDecoder"))]
print("--- logcat (%d lines) ---" % len(keep))
print("\n".join(keep[-60:]))

cap.stop()
adb.force_stop(SERIAL)
server.stop()
print("done")
