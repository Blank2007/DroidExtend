import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import ctypes, sys, json
from ctypes import wintypes
sys.path.insert(0, os.path.join(REPO, "host"))
import win_display as wd

print("sizeof PATH_INFO", ctypes.sizeof(wd.PATH_INFO), flush=True)
print("sizeof PATH_SOURCE_INFO", ctypes.sizeof(wd.PATH_SOURCE_INFO), flush=True)
print("sizeof PATH_TARGET_INFO", ctypes.sizeof(wd.PATH_TARGET_INFO), flush=True)
print("sizeof MODE_INFO", ctypes.sizeof(wd.MODE_INFO), flush=True)
print("sizeof TARGET_DEVICE_NAME", ctypes.sizeof(wd.TARGET_DEVICE_NAME), flush=True)
print("sizeof VIDEO_SIGNAL_INFO", ctypes.sizeof(wd.VIDEO_SIGNAL_INFO), flush=True)
print("sizeof MODE_INFO_UNION", ctypes.sizeof(wd.MODE_INFO_UNION), flush=True)

u = wd.user32
u.GetDisplayConfigBufferSizes.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
u.GetDisplayConfigBufferSizes.restype = wintypes.LONG

for flags in (1, 2):
    a, b = wintypes.UINT(0), wintypes.UINT(0)
    rc = u.GetDisplayConfigBufferSizes(flags, ctypes.byref(a), ctypes.byref(b))
    print(f"flags={flags} rc={rc} paths={a.value} modes={b.value}", flush=True)

flags = 2
np_, nm_ = wintypes.UINT(0), wintypes.UINT(0)
u.GetDisplayConfigBufferSizes(flags, ctypes.byref(np_), ctypes.byref(nm_))
paths = (wd.PATH_INFO * np_.value)()
modes = (wd.MODE_INFO * nm_.value)()
pn, mn = wintypes.UINT(np_.value), wintypes.UINT(nm_.value)
u.QueryDisplayConfig.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wd.PATH_INFO),
                                 ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wd.MODE_INFO), ctypes.c_void_p]
u.QueryDisplayConfig.restype = wintypes.LONG
rc = u.QueryDisplayConfig(flags, ctypes.byref(pn), paths, ctypes.byref(mn), modes, None)
print("QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS) rc=", rc, "n=", pn.value, "m=", mn.value, flush=True)

u.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]
u.DisplayConfigGetDeviceInfo.restype = wintypes.LONG

for i in range(pn.value):
    p = paths[i]
    print(f"path{i} src.adapter={p.sourceInfo.adapterId} src.id={p.sourceInfo.id} "
          f"src.modeIdx={p.sourceInfo.modeInfoIdx} src.flags={p.sourceInfo.statusFlags:#x} "
          f"tgt.id={p.targetInfo.id} tgt.modeIdx={p.targetInfo.modeInfoIdx} "
          f"tgt.tech={p.targetInfo.outputTechnology:#x} avail={p.targetInfo.targetAvailable} "
          f"tgt.flags={p.targetInfo.statusFlags:#x}", flush=True)
    s = wd.SOURCE_DEVICE_NAME()
    s.header.type = 1
    s.header.size = ctypes.sizeof(s)
    s.header.adapterId = p.sourceInfo.adapterId
    s.header.id = p.sourceInfo.id
    rc = u.DisplayConfigGetDeviceInfo(ctypes.byref(s))
    print("   source rc=", rc, repr(s.viewGdiDeviceName), flush=True)
    t = wd.TARGET_DEVICE_NAME()
    t.header.type = 2
    t.header.size = ctypes.sizeof(t)
    t.header.adapterId = p.targetInfo.adapterId
    t.header.id = p.targetInfo.id
    rc = u.DisplayConfigGetDeviceInfo(ctypes.byref(t))
    print("   target rc=", rc, repr(t.monitorFriendlyDeviceName), repr(t.monitorDevicePath[:60]), flush=True)
    mi = modes[p.sourceInfo.modeInfoIdx] if p.sourceInfo.modeInfoIdx < mn.value else None
    if mi is not None:
        print("   srcMode", mi.infoType, mi.u.sourceMode.width, mi.u.sourceMode.height, hex(mi.u.sourceMode.pixelFormat), flush=True)
print("done", flush=True)
