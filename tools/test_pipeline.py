import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, sys, time

ROOT = REPO
sys.path.insert(0, os.path.join(ROOT, "host"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import win_display as wd
import capture as cap

wd.set_dpi_aware()

vd = wd.find_virtual_display()[0]
print("virtual display:", vd["name"], vd["desc"], "attached=%s" % vd["attached"])
rect = wd.desktop_rect(vd)
print("rect:", rect)
print("live monitors:", [("%s %dx%d@%d" % (m["name"], m["width"], m["height"], m["hz"]))
                         for m in wd.live_monitors()])

cfg = {"encoder": "h264_nvenc", "backend": "auto", "fps": 60,
       "bitrate_mbps": 20, "gop": 120, "draw_mouse": True,
       "preset": "p4", "spatial_aq": False}

c = cap.Capture(cfg, rect, log=lambda m: print("  [log]", m))
print("--- ddagrab outputs:", cap.ddagrab_outputs())
t0 = time.time()
used = c.start()
print("backend used = %s  (start took %.2fs)" % (used, time.time() - t0))

n = 0
keyed = 0
total = 0
first_type = None
deadline = time.time() + 3.0
while time.time() < deadline:
    units = c.get_units(timeout=0.5)
    for data, is_key in units:
        if first_type is None:
            # print the NAL types of the first unit
            types = []
            i = data.find(b"\x00\x00\x01")
            while i != -1 and len(types) < 8:
                types.append(data[i + 3] & 0x1F)
                i = data.find(b"\x00\x00\x01", i + 3)
            first_type = types
        n += 1
        keyed += 1 if is_key else 0
        total += len(data)

dt = time.time() - t0
print("units=%d keyframes=%d bytes=%d (%.2f Mbps) over %.2fs" %
      (n, keyed, total, total * 8 / dt / 1e6, dt))
print("first unit NAL types:", first_type)
print("capture.fps() = %.1f   alive=%s" % (c.fps(), c.alive()))
c.stop()
print("stopped")
