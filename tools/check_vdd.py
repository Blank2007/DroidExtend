import os, sys, json, time
sys.path.insert(0, r"D:\dsh projects\droid-extend\host")
import win_display as wd
import ctypes
wd.set_dpi_aware()
u = ctypes.windll.user32

VD = r"\\.\DISPLAY52"
print("current:", wd.current_mode(VD))
print("rect   :", wd.desktop_rect(VD))
modes = wd.available_modes(VD)
print("modes  : %d" % len(modes))
for m in modes:
    print("   ", m)
