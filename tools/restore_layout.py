import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, sys, time, ctypes
sys.path.insert(0, os.path.join(REPO, "host"))
import win_display as wd
wd.set_dpi_aware()

PRIMARY = r"\\.\DISPLAY1"
VD = r"\\.\DISPLAY52"

print("before:", wd.current_mode(PRIMARY), wd.current_mode(VD))
rc = wd.set_mode(PRIMARY, 2560, 1600, 300, x=0, y=0)
print("DISPLAY1 -> 2560x1600@300 rc=%s" % rc)
time.sleep(1.5)
rc = wd.set_mode(VD, 1920, 1080, 60, x=2560, y=0)
print("DISPLAY52 -> 1920x1080@60 @(2560,0) rc=%s" % rc)
time.sleep(1.5)
print("after :", wd.current_mode(PRIMARY), wd.current_mode(VD))
print("desktop=%s" % (wd.desktop_bounds(),))
print("VD rect =%s" % (wd.desktop_rect(VD),))
