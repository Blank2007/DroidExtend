import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, sys, time, ctypes
sys.path.insert(0, os.path.join(REPO, "host"))
import win_display as wd
wd.set_dpi_aware()

CDS_UPDATEREGISTRY = 0x00000001
CDS_TEST = 0x00000002
CDS_GLOBAL = 0x00000008
CDS_NORESET = 0x10000000

NAMES = {0: "SUCCESSFUL", -1: "FAILED", -2: "BADMODE", -3: "NOTUPDATED",
         -4: "BADFLAGS", -5: "BADPARAM", -6: "BADDUALVIEW", -7: "RESTART"}


def try_mode(dev, w, h, hz, flags, label, position=None):
    dm = wd.DEVMODEW()
    dm.dmSize = ctypes.sizeof(dm)
    if not wd.user32.EnumDisplaySettingsExW(dev, wd.ENUM_CURRENT_SETTINGS, ctypes.byref(dm), 0):
        print("  %-24s ENUM FAILED" % label)
        return
    cur = (dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency)
    dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency = w, h, hz
    dm.dmFields = wd.DM_PELSWIDTH | wd.DM_PELSHEIGHT | wd.DM_DISPLAYFREQUENCY
    if position:
        dm.dmPosition.x, dm.dmPosition.y = position
        dm.dmFields |= wd.DM_POSITION
    rc = wd.user32.ChangeDisplaySettingsExW(dev, ctypes.byref(dm), None, flags, None)
    print("  %-24s cur=%s -> want=(%d,%d,%d) rc=%d %s"
          % (label, cur, w, h, hz, rc, NAMES.get(rc, "?")))


print("=== control: DISPLAY1 (NVIDIA internal) ===")
try_mode(r"\\.\DISPLAY1", 1920, 1080, 60, CDS_TEST, "DISPLAY1 test 1080p60")
try_mode(r"\\.\DISPLAY1", 1920, 1080, 60, CDS_UPDATEREGISTRY | CDS_NORESET, "DISPLAY1 noreset")
rc = wd.user32.ChangeDisplaySettingsExW(None, None, None, 0, None)
print("  apply-all rc=%d %s" % (rc, NAMES.get(rc, "?")))
time.sleep(1)
print("  DISPLAY1 now:", wd.current_mode(r"\\.\DISPLAY1"))

print("=== target: DISPLAY52 (VDD) ===")
VD = r"\\.\DISPLAY52"
print("  DISPLAY52 now:", wd.current_mode(VD))
try_mode(VD, 1920, 1080, 60, CDS_UPDATEREGISTRY | CDS_NORESET, "VDD noreset")
rc = wd.user32.ChangeDisplaySettingsExW(None, None, None, 0, None)
print("  apply-all rc=%d %s" % (rc, NAMES.get(rc, "?")))
time.sleep(1.5)
print("  DISPLAY52 now:", wd.current_mode(VD))
print("  desktop now  :", wd.desktop_bounds())
