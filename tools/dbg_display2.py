import ctypes, sys
from ctypes import wintypes
sys.path.insert(0, r"D:\dsh projects\droid-extend\host")
import win_display as wd

u = wd.user32
u.GetDisplayConfigBufferSizes.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
u.GetDisplayConfigBufferSizes.restype = wintypes.LONG
u.QueryDisplayConfig.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wd.PATH_INFO),
                                 ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wd.MODE_INFO), ctypes.c_void_p]
u.QueryDisplayConfig.restype = wintypes.LONG
u.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]
u.DisplayConfigGetDeviceInfo.restype = wintypes.LONG

flags = 1 | wd.QDC_VIRTUAL_MODE_AWARE
np_, nm_ = wintypes.UINT(0), wintypes.UINT(0)
print("bufsizes rc", u.GetDisplayConfigBufferSizes(flags, ctypes.byref(np_), ctypes.byref(nm_)), np_.value, nm_.value, flush=True)
paths = (wd.PATH_INFO * np_.value)()
modes = (wd.MODE_INFO * nm_.value)()
pn, mn = wintypes.UINT(np_.value), wintypes.UINT(nm_.value)
print("query rc", u.QueryDisplayConfig(flags, ctypes.byref(pn), paths, ctypes.byref(mn), modes, None),
      "n=", pn.value, "m=", mn.value, flush=True)

print("--- phase1: raw fields only ---", flush=True)
free = []
for i in range(pn.value):
    p = paths[i]
    act = bool(p.sourceInfo.statusFlags & 1)
    av = bool(p.targetInfo.targetAvailable)
    if not act and av:
        free.append(i)
    if i % 30 == 0:
        print(f"  ..{i}", flush=True)
print("raw scan ok. free target path indices:", free, flush=True)

print("--- phase2: names for free targets ---", flush=True)
for i in free:
    p = paths[i]
    t = wd.TARGET_DEVICE_NAME()
    t.header.type = 2
    t.header.size = ctypes.sizeof(t)
    t.header.adapterId = p.targetInfo.adapterId
    t.header.id = p.targetInfo.id
    rc = u.DisplayConfigGetDeviceInfo(ctypes.byref(t))
    print(f"  free[{i}] adapter={p.targetInfo.adapterId} id={p.targetInfo.id} tech={p.targetInfo.outputTechnology:#x} "
          f"rc={rc} name={t.monitorFriendlyDeviceName!r}", flush=True)

print("--- phase3: active names ---", flush=True)
for i in range(pn.value):
    p = paths[i]
    if not (p.sourceInfo.statusFlags & 1):
        continue
    s = wd.SOURCE_DEVICE_NAME()
    s.header.type = 1
    s.header.size = ctypes.sizeof(s)
    s.header.adapterId = p.sourceInfo.adapterId
    s.header.id = p.sourceInfo.id
    rc = u.DisplayConfigGetDeviceInfo(ctypes.byref(s))
    t = wd.TARGET_DEVICE_NAME()
    t.header.type = 2
    t.header.size = ctypes.sizeof(t)
    t.header.adapterId = p.targetInfo.adapterId
    t.header.id = p.targetInfo.id
    rc2 = u.DisplayConfigGetDeviceInfo(ctypes.byref(t))
    mi = modes[p.sourceInfo.modeInfoIdx] if p.sourceInfo.modeInfoIdx < mn.value else None
    print(f"  active[{i}] {s.viewGdiDeviceName!r} -> {t.monitorFriendlyDeviceName!r} tech={p.targetInfo.outputTechnology:#x} "
          f"mode={mi.u.sourceMode.width if mi else '?'}x{mi.u.sourceMode.height if mi else '?'}", flush=True)
print("done", flush=True)
