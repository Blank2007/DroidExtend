"""Why does re-attaching the virtual display fail?

Walks the CCD path database before/after each recovery attempt so we can see
what state the driver is actually in, instead of guessing from return codes.
"""
import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import ctypes
import os
import sys
import time

ROOT = REPO
sys.path.insert(0, os.path.join(ROOT, "host"))

import win_display as wd
import vdd

NAME = r"\\.\DISPLAY20"


def dump(label):
    print("\n== %s ==" % label)
    print("live      : %s" % [m["name"] for m in wd.live_monitors()])
    print("bounds    : %s" % wd.desktop_bounds())
    try:
        print("modes     : %s" % (wd.available_modes(NAME)[:4],))
    except Exception as e:
        print("modes     : !! %s" % e)
    try:
        print("cur mode  : %s" % wd.current_mode(NAME))
    except Exception as e:
        print("cur mode  : !! %s" % e)
    paths, modes = wd._query(0x1)  # QDC_ALL_PATHS
    hits = [(wd.source_name(p), wd.target_name(p)) for p in paths
            if wd.source_name(p) == NAME or wd.target_name(p) == NAME]
    print("ccd paths involving %s: %d" % (NAME, len(hits)))
    for s, t in hits[:8]:
        print("   %s -> %s" % (s, t))
    print("vdd.status: %s" % {k: v for k, v in vdd.status().items() if k != "adapter"})


dump("current")

for label, fn in (
    ("attach(True)", lambda: wd.attach(NAME, True)),
    ("extend_topology", lambda: wd.extend_topology()),
    ("set_mode 1920x1080@60", lambda: wd.set_mode(NAME, 1920, 1080, 60, 2560, 0)),
):
    try:
        rc = fn()
        print("\n>> %s -> rc=%s" % (label, rc))
    except Exception as e:
        print("\n>> %s -> !! %s: %s" % (label, type(e).__name__, e))
    time.sleep(1.0)
    dump("after " + label)

dump("final")
