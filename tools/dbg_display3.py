import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import ctypes, sys
from ctypes import wintypes
sys.path.insert(0, os.path.join(REPO, "host"))
import win_display as wd

u = wd.user32
u.GetDisplayConfigBufferSizes.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
u.GetDisplayConfigBufferSizes.restype = wintypes.LONG
u.QueryDisplayConfig.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.c_void_p,
                                 ctypes.POINTER(wintypes.UINT), ctypes.c_void_p, ctypes.c_void_p]
u.QueryDisplayConfig.restype = wintypes.LONG
u.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]
u.DisplayConfigGetDeviceInfo.restype = wintypes.LONG


class PATH_INFO_PAD(ctypes.Structure):
    _fields_ = [("sourceInfo", wd.PATH_SOURCE_INFO), ("targetInfo", wd.PATH_TARGET_INFO),
                ("_pad", wintypes.DWORD)]


print("sizeof padded", ctypes.sizeof(PATH_INFO_PAD), flush=True)

for STRIDE_STRUCT, label in ((PATH_INFO_PAD, "72-byte padded"),):
    flags = wd.QDC_ALL_PATHS
    np_, nm_ = wintypes.UINT(0), wintypes.UINT(0)
    rc = u.GetDisplayConfigBufferSizes(flags, ctypes.byref(np_), ctypes.byref(nm_))
    npaths, nmodes = np_.value, nm_.value
    print(f"[{label}] bufsizes rc={rc} paths={npaths} modes={nmodes}", flush=True)
    paths = (STRIDE_STRUCT * npaths)()
    modes = (wd.MODE_INFO * nmodes)()
    pn, mn = wintypes.UINT(npaths), wintypes.UINT(nmodes)
    rc = u.QueryDisplayConfig(flags, ctypes.byref(pn), paths, ctypes.byref(mn), modes, None)
    print(f"[{label}] query rc={rc} n={pn.value} m={mn.value}", flush=True)

    act = 0
    free = []
    bad = 0
    for i in range(pn.value):
        p = paths[i]
        is_act = bool(p.sourceInfo.statusFlags & 1)
        avail = bool(p.targetInfo.targetAvailable)
        if is_act:
            act += 1
        elif avail:
            free.append(i)
        if p.sourceInfo.modeInfoIdx != 0xFFFFFFFF and p.sourceInfo.modeInfoIdx >= mn.value and is_act:
            bad += 1
    print(f"[{label}] active={act} free_available={len(free)} badIdx={bad}", flush=True)

    seen = {}
    for i in free:
        p = paths[i]
        t = wd.TARGET_DEVICE_NAME()
        t.header.type = 2
        t.header.size = ctypes.sizeof(t)
        t.header.adapterId = p.targetInfo.adapterId
        t.header.id = p.targetInfo.id
        rc = u.DisplayConfigGetDeviceInfo(ctypes.byref(t))
        key = (t.monitorFriendlyDeviceName, p.targetInfo.outputTechnology)
        seen.setdefault(key, 0)
        seen[key] += 1
    print(f"[{label}] distinct free targets by name/tech:", flush=True)
    for (name, tech), cnt in seen.items():
        print(f"     {cnt:3d} x {name!r} tech={tech:#x}", flush=True)

    for i in range(pn.value):
        p = paths[i]
        if p.sourceInfo.statusFlags & 1:
            s = wd.SOURCE_DEVICE_NAME()
            s.header.type = 1
            s.header.size = ctypes.sizeof(s)
            s.header.adapterId = p.sourceInfo.adapterId
            s.header.id = p.sourceInfo.id
            u.DisplayConfigGetDeviceInfo(ctypes.byref(s))
            t = wd.TARGET_DEVICE_NAME()
            t.header.type = 2
            t.header.size = ctypes.sizeof(t)
            t.header.adapterId = p.targetInfo.adapterId
            t.header.id = p.targetInfo.id
            u.DisplayConfigGetDeviceInfo(ctypes.byref(t))
            mi = modes[p.sourceInfo.modeInfoIdx] if p.sourceInfo.modeInfoIdx < mn.value else None
            print(f"[{label}] ACTIVE {s.viewGdiDeviceName!r} -> {t.monitorFriendlyDeviceName!r} "
                  f"tech={p.targetInfo.outputTechnology:#x} outId={p.targetInfo.id} "
                  f"adapter={p.targetInfo.adapterId} "
                  f"mode={(mi.u.sourceMode.width, mi.u.sourceMode.height) if mi else None}", flush=True)
print("done", flush=True)
