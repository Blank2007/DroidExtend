"""Windows display topology via the CCD API (ctypes, no dependencies, no admin).

Used by the host app to:
  * find a spare (inactive but available) target, e.g. a virtual display exposed
    by an already-installed indirect display driver;
  * switch that target on at an arbitrary resolution/refresh rate;
  * switch it off again;
  * learn its desktop rectangle so the capture stage knows what to grab.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

QDC_ALL_PATHS = 0x00000001
QDC_ONLY_ACTIVE_PATHS = 0x00000002
QDC_VIRTUAL_MODE_AWARE = 0x00000010
QDC_VIRTUAL_REFRESH_RATE_AWARE = 0x00000040

SDC_TOPOLOGY_INTERNAL = 0x00000001
SDC_TOPOLOGY_CLONE = 0x00000002
SDC_TOPOLOGY_EXTEND = 0x00000004
SDC_TOPOLOGY_EXTERNAL = 0x00000008
SDC_TOPOLOGY_SUPPLIED = 0x00000010
SDC_USE_SUPPLIED_DISPLAY_CONFIG = 0x00000020
SDC_VALIDATE = 0x00000040
SDC_APPLY = 0x00000080
SDC_NO_OPTIMIZATION = 0x00000100
SDC_SAVE_TO_DATABASE = 0x00000200
SDC_ALLOW_CHANGES = 0x00000400
SDC_PATH_PERSIST_IF_REQUIRED = 0x00000800
SDC_FORCE_MODE_ENUMERATION = 0x00001000
SDC_ALLOW_PATH_ORDER_CHANGES = 0x00002000
SDC_VIRTUAL_MODE_AWARE = 0x00008000
SDC_VIRTUAL_REFRESH_RATE_AWARE = 0x00020000

PATH_ACTIVE = 0x00000001
PATH_TARGET_AVAILABLE = 0x00000002

DISPLAYCONFIG_OUTPUT_TECHNOLOGY_NAMES = {
    0: "VGA", 1: "SVIDEO", 2: "COMPOSITE", 3: "COMPONENT", 4: "DVI", 5: "HDMI",
    6: "LVDS", 8: "D_JPN", 9: "SDI", 10: "DISPLAYPORT_EXTERNAL",
    11: "DISPLAYPORT_EMBEDDED", 12: "UDI_EXTERNAL", 13: "UDI_EMBEDDED",
    14: "SDTV_DONGLE", 0x80000000: "INTERNAL", 0x80000001: "FORCE_UINT",
    0xFFFFFFFF: "INDIRECT_WIRED", 0xFFFFFFFE: "INDIRECT_VIRTUAL",
    0xFFFFFFFD: "DISPLAYPORT_USB_TUNNEL",
}

MODE_INFO_TYPE_SOURCE = 1
MODE_INFO_TYPE_TARGET = 2
MODE_INFO_TYPE_DESKTOP_IMAGE = 3

GET_SOURCE_NAME = 1
GET_TARGET_NAME = 2


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    def __eq__(self, other):
        return (self.LowPart, self.HighPart) == (other.LowPart, other.HighPart)

    def __hash__(self):
        return hash((self.LowPart, self.HighPart))

    def __repr__(self):
        return f"{self.HighPart:08X}:{self.LowPart:08X}"


class RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.UINT), ("Denominator", wintypes.UINT)]


class REGION2D(ctypes.Structure):
    _fields_ = [("cx", wintypes.UINT), ("cy", wintypes.UINT)]


class POINTL(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECTL(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class VIDEO_SIGNAL_INFO(ctypes.Structure):
    _fields_ = [
        ("pixelRate", ctypes.c_ulonglong),
        ("hSyncFreq", RATIONAL),
        ("vSyncFreq", RATIONAL),
        ("activeSize", REGION2D),
        ("totalSize", REGION2D),
        ("videoStandard", wintypes.UINT),
        ("scanLineOrdering", wintypes.UINT),
    ]


class TARGET_MODE(ctypes.Structure):
    _fields_ = [("targetVideoSignalInfo", VIDEO_SIGNAL_INFO)]


class SOURCE_MODE(ctypes.Structure):
    # DISPLAYCONFIG_SOURCE_MODE = { UINT32 width; UINT32 height;
    #                               DISPLAYCONFIG_PIXELFORMAT pixelFormat;
    #                               POINTL position; }   <- position must be here
    # or the source's desktop origin is unreadable.
    _fields_ = [("width", wintypes.UINT), ("height", wintypes.UINT),
                ("pixelFormat", wintypes.UINT), ("position", POINTL)]


class DESKTOP_IMAGE_INFO(ctypes.Structure):
    _fields_ = [("PathSourceSize", POINTL), ("DesktopImageRegion", RECTL),
                ("DesktopImageClip", RECTL)]


class MODE_INFO_UNION(ctypes.Union):
    _fields_ = [("targetMode", TARGET_MODE), ("sourceMode", SOURCE_MODE),
                ("desktopImageInfo", DESKTOP_IMAGE_INFO)]


class MODE_INFO(ctypes.Structure):
    _fields_ = [("infoType", wintypes.UINT), ("id", wintypes.UINT),
                ("adapterId", LUID), ("u", MODE_INFO_UNION)]


class PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("statusFlags", wintypes.UINT)]


class PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID), ("id", wintypes.UINT), ("modeInfoIdx", wintypes.UINT),
        ("outputTechnology", wintypes.UINT), ("rotation", wintypes.UINT),
        ("scaling", wintypes.UINT), ("refreshRate", RATIONAL),
        ("scanLineOrdering", wintypes.UINT), ("targetAvailable", wintypes.BOOL),
        ("statusFlags", wintypes.UINT),
    ]


class PATH_INFO(ctypes.Structure):
    # NOTE: the layout the OS actually writes is 72 bytes - 68 bytes of members
    # plus 4 bytes of tail padding.  Declaring only the 68 bytes of members makes
    # QueryDisplayConfig(QDC_ALL_PATHS) write past the end of our buffer and the
    # process dies with STATUS_HEAP_CORRUPTION (0xC0000374) and no traceback.
    _fields_ = [("sourceInfo", PATH_SOURCE_INFO), ("targetInfo", PATH_TARGET_INFO),
                ("_tail_padding", wintypes.DWORD)]


class DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [("type", wintypes.UINT), ("size", wintypes.UINT),
                ("adapterId", LUID), ("id", wintypes.UINT)]


class TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DEVICE_INFO_HEADER), ("flags", wintypes.UINT),
        ("outputTechnology", wintypes.UINT), ("edidManufactureId", wintypes.WORD),
        ("edidProductCodeId", wintypes.WORD), ("connectorInstance", wintypes.UINT),
        ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
        ("monitorDevicePath", wintypes.WCHAR * 128),
    ]


class SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", DEVICE_INFO_HEADER), ("viewGdiDeviceName", wintypes.WCHAR * 32)]


def _query(flags=QDC_ALL_PATHS):
    n_paths = wintypes.UINT(0)
    n_modes = wintypes.UINT(0)
    rc = user32.GetDisplayConfigBufferSizes(flags, ctypes.byref(n_paths), ctypes.byref(n_modes))
    if rc != 0:
        raise OSError(f"GetDisplayConfigBufferSizes rc={rc}")
    for _ in range(6):
        paths = (PATH_INFO * max(n_paths.value, 1))()
        modes = (MODE_INFO * max(n_modes.value, 1))()
        pn, mn = wintypes.UINT(n_paths.value), wintypes.UINT(n_modes.value)
        rc = user32.QueryDisplayConfig(flags, ctypes.byref(pn), paths,
                                       ctypes.byref(mn), modes, None)
        if rc == 0:
            return list(paths[:pn.value]), list(modes[:mn.value])
        if rc != 122:  # ERROR_INSUFFICIENT_BUFFER
            raise OSError(f"QueryDisplayConfig rc={rc}")
        n_paths, n_modes = pn, mn
    raise OSError("QueryDisplayConfig kept failing")


def target_name(path: PATH_INFO) -> str:
    t = TARGET_DEVICE_NAME()
    t.header.type = GET_TARGET_NAME
    t.header.size = ctypes.sizeof(t)
    t.header.adapterId = path.targetInfo.adapterId
    t.header.id = path.targetInfo.id
    if user32.DisplayConfigGetDeviceInfo(ctypes.byref(t)) == 0:
        return t.monitorFriendlyDeviceName or t.monitorDevicePath
    return ""


def source_name(path: PATH_INFO) -> str:
    s = SOURCE_DEVICE_NAME()
    s.header.type = GET_SOURCE_NAME
    s.header.size = ctypes.sizeof(s)
    s.header.adapterId = path.sourceInfo.adapterId
    s.header.id = path.sourceInfo.id
    if user32.DisplayConfigGetDeviceInfo(ctypes.byref(s)) == 0:
        return s.viewGdiDeviceName
    return ""


def tech_name(v: int) -> str:
    return DISPLAYCONFIG_OUTPUT_TECHNOLOGY_NAMES.get(v & 0xFFFFFFFF, f"tech_{v:#x}")


def describe(paths=None, modes=None):
    """Human readable dump of every path (active and not)."""
    if paths is None:
        paths, modes = _query()
    rows = []
    for i, p in enumerate(paths):
        m = modes[p.sourceInfo.modeInfoIdx] if p.sourceInfo.modeInfoIdx < len(modes) else None
        tm = modes[p.targetInfo.modeInfoIdx] if p.targetInfo.modeInfoIdx < len(modes) else None
        active = bool(p.sourceInfo.statusFlags & PATH_ACTIVE)
        row = {
            "idx": i,
            "active": active,
            "source": source_name(p),
            "target": target_name(p),
            "tech": tech_name(p.targetInfo.outputTechnology),
            "available": bool(p.targetInfo.targetAvailable),
            "target_id": p.targetInfo.id,
            "adapter": repr(p.targetInfo.adapterId),
            "size": (m.u.sourceMode.width, m.u.sourceMode.height) if m else None,
            "hz": (tm.u.targetMode.targetVideoSignalInfo.vSyncFreq.Numerator /
                   max(tm.u.targetMode.targetVideoSignalInfo.vSyncFreq.Denominator, 1)) if tm else None,
            "adapterId": p.targetInfo.adapterId,
        }
        rows.append(row)
    return rows


def free_targets(paths=None):
    """Paths that are available but not currently driving a source."""
    if paths is None:
        paths, _ = _query()
    return [p for p in paths
            if p.targetInfo.targetAvailable and not (p.sourceInfo.statusFlags & PATH_ACTIVE)]


def active_paths(paths=None):
    if paths is None:
        paths, _ = _query()
    return [p for p in paths if p.sourceInfo.statusFlags & PATH_ACTIVE]


# ---------------------------------------------------------------------------
# GDI level helpers - the ones we actually drive the virtual monitor with.
# ChangeDisplaySettingsEx is far simpler (and far more forgiving) than handing
# Windows a hand-built CCD topology, so mode switching goes through it.
# ---------------------------------------------------------------------------

ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
CDS_UPDATEREGISTRY = 0x00000001
CDS_TEST = 0x00000002
CDS_FULLSCREEN = 0x00000004
CDS_GLOBAL = 0x00000008
CDS_RESET = 0x40000000
DISP_CHANGE_SUCCESSFUL = 0
DM_BITSPERPEL = 0x00040000
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000
DM_POSITION = 0x00000020


class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
        ("dmPosition", POINTL), ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD), ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32), ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD), ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD), ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD), ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD)]


DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x4
DISPLAY_DEVICE_MIRRORING_DRIVER = 0x8


def enum_adapters():
    """Every display adapter Windows knows about, active or not."""
    out = []
    i = 0
    while True:
        d = DISPLAY_DEVICE()
        d.cb = ctypes.sizeof(d)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(d), 0):
            break
        mon = []
        j = 0
        while True:
            m = DISPLAY_DEVICE()
            m.cb = ctypes.sizeof(m)
            if not user32.EnumDisplayDevicesW(d.DeviceName, j, ctypes.byref(m), 0):
                break
            mon.append(m)
            j += 1
        out.append({"name": d.DeviceName, "desc": d.DeviceString,
                    "flags": d.StateFlags, "id": d.DeviceID,
                    "attached": bool(d.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP),
                    "primary": bool(d.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE),
                    "modes": [{"name": x.DeviceName, "desc": x.DeviceString,
                               "id": x.DeviceID} for x in mon]})
        i += 1
    return out


def _devname(x):
    """Accept a GDI device name or one of enum_adapters()' dicts."""
    if isinstance(x, dict):
        return x.get("name") or x.get("device")
    if isinstance(x, (list, tuple)):
        return _devname(x[0]) if x else None
    return x


def current_mode(device_name):
    device_name = _devname(device_name)
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(dm)
    if not user32.EnumDisplaySettingsExW(device_name, ENUM_CURRENT_SETTINGS,
                                         ctypes.byref(dm), 0):
        return None
    return {"width": dm.dmPelsWidth, "height": dm.dmPelsHeight,
            "hz": dm.dmDisplayFrequency, "x": dm.dmPosition.x, "y": dm.dmPosition.y,
            "bpp": dm.dmBitsPerPel}


def available_modes(device_name):
    """Every (width, height, hz) the display reports."""
    device_name = _devname(device_name)
    modes = set()
    i = 0
    while True:
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(dm)
        if not user32.EnumDisplaySettingsExW(device_name, i, ctypes.byref(dm), 0):
            break
        modes.add((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency))
        i += 1
    return sorted(modes, key=lambda t: (-t[0] * t[1], -t[2]))


def best_mode(device_name, width, height, hz=60):
    """The advertised mode that best matches a wanted size.

    Returns (width, height, hz, exact), or None when the display reports no
    modes at all.

    An exact size always wins.  Otherwise we judge by *aspect ratio*, because
    that is what decides whether the picture fills the tablet or sits inside
    black bars; among equally-close aspects the one closest in area wins, so a
    phone asking for 3200x2136 gets 2560x1600 rather than 3840x2160.
    """
    modes = available_modes(device_name)
    if not modes:
        return None
    sizes = sorted({(m[0], m[1]) for m in modes})
    want = (int(width), int(height))
    if want in sizes:
        return (want[0], want[1],
                min((m[2] for m in modes if (m[0], m[1]) == want),
                    key=lambda r: abs(r - hz)),
                True)
    want_aspect = want[0] / float(want[1] or 1)

    def score(size):
        aspect = size[0] / float(size[1] or 1)
        return (abs(aspect - want_aspect),
                abs(size[0] * size[1] - want[0] * want[1]))

    size = min(sizes, key=score)
    return (size[0], size[1],
            min((m[2] for m in modes if (m[0], m[1]) == size),
                key=lambda r: abs(r - hz)),
            False)


def desktop_rect(device_name):
    """The display's rectangle in physical desktop coordinates (capture origin)."""
    m = current_mode(device_name)
    if not m:
        return None
    return {"left": m["x"], "top": m["y"],
            "right": m["x"] + m["width"], "bottom": m["y"] + m["height"],
            "width": m["width"], "height": m["height"], "hz": m["hz"]}


def set_mode(device_name, width, height, hz=0, x=None, y=None, apply_=True):
    """Change resolution/refresh (and optionally position) of a display."""
    device_name = _devname(device_name)
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(dm)
    if not user32.EnumDisplaySettingsExW(device_name, ENUM_CURRENT_SETTINGS,
                                         ctypes.byref(dm), 0):
        raise OSError(f"EnumDisplaySettingsEx failed for {device_name}")
    dm.dmPelsWidth = width
    dm.dmPelsHeight = height
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    if hz:
        dm.dmDisplayFrequency = hz
        dm.dmFields |= DM_DISPLAYFREQUENCY
    if x is not None and y is not None:
        dm.dmPosition.x = x
        dm.dmPosition.y = y
        dm.dmFields |= DM_POSITION
    flags = CDS_UPDATEREGISTRY | CDS_GLOBAL if apply_ else CDS_TEST
    rc = user32.ChangeDisplaySettingsExW(device_name, ctypes.byref(dm), None, flags, None)
    return rc


def _blank_mode():
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(dm)
    return dm


def attach(device_name, enable=True):
    """Tell Windows to (de)activate a display without changing its mode."""
    dm = _blank_mode()
    if enable:
        cur = current_mode(device_name)
        dm.dmPelsWidth = cur["width"] if cur else 1920
        dm.dmPelsHeight = cur["height"] if cur else 1080
        dm.dmDisplayFrequency = cur["hz"] if cur else 60
        dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    else:
        dm.dmPelsWidth = 0
        dm.dmPelsHeight = 0
        dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    return user32.ChangeDisplaySettingsExW(device_name, ctypes.byref(dm), None,
                                           CDS_UPDATEREGISTRY | CDS_GLOBAL, None)


def detach(device_name):
    """Take one display off the desktop and leave every other one alone.

    ChangeDisplaySettingsEx cannot switch this indirect display off: the
    documented 0x0 DEVMODE returns DISP_CHANGE_BADMODE (-2) and a NULL
    DEVMODE returns 0 but changes nothing (both verified against MttVDD
    25.7.23 on this machine).  Rebuilding the CCD topology without that
    source does work.

    SDC_TOPOLOGY_INTERNAL was the other candidate, but it switches off every
    non-internal display, so a real second monitor would go down with it.
    Returns True when the display is gone (or already was).
    """
    paths, modes = _query(QDC_ONLY_ACTIVE_PATHS)
    keep = [p for p in paths if source_name(p) != device_name]
    if len(keep) == len(paths):
        return True
    if not keep:
        raise RuntimeError("不能把最后一块屏幕也关掉")
    parr = (PATH_INFO * len(keep))(*keep)
    if modes:
        marr = (MODE_INFO * len(modes))(*modes)
        rc = user32.SetDisplayConfig(len(keep), parr, len(modes), marr,
                                     SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_APPLY
                                     | SDC_ALLOW_CHANGES)
    else:
        rc = user32.SetDisplayConfig(len(keep), parr, 0, None,
                                     SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_APPLY
                                     | SDC_ALLOW_CHANGES)
    return rc == 0


def extend_topology():
    """Win+P 'Extend': switch on every display that is attached but unused."""
    return user32.SetDisplayConfig(0, None, 0, None, SDC_TOPOLOGY_EXTEND | SDC_APPLY)


def find_virtual_display(hints=("mttvdd", "virtual display", "parsec", "idd", "spacedesk")):
    """Best guess at the indirect-display adapter's active output, best first.

    Ranked rather than merely filtered: this machine also carries ten GameViewer
    (NetEase UU) adapters that match the "idd" hint but never expose a monitor
    subdevice, so a plain "first hit wins" pick hands back \\\\.\\DISPLAY10 - an
    adapter nothing can be shown on.  Sorting on (attached, has monitors, hint
    order) keeps a foreign or stale adapter from beating the real one.
    """
    ranked = []
    for a in enum_adapters():
        blob = (a["id"] + " " + a["desc"] + " " +
                " ".join(m["desc"] + " " + m["id"] for m in a["modes"])).lower()
        for rank, h in enumerate(hints):
            if h in blob:
                ranked.append((0 if a["attached"] else 1,
                               0 if a["modes"] else 1,
                               rank, a))
                break
    ranked.sort(key=lambda t: t[:3])
    return [t[3] for t in ranked]


def set_dpi_aware():
    """Make this process see real pixels.

    Without this, a 125%-scaled desktop reports 2048x1280 while the panel is
    really 2560x1600, and every coordinate we hand to gdigrab lands in the
    wrong place (or off-screen).  MUST be called before any capture starts.
    """
    try:
        # PER_MONITOR_AWARE_V2 = -4, the only mode that also virtualises nothing
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        shcore = ctypes.windll.shcore
        if shcore.SetProcessDpiAwareness(2) == 0:   # PROCESS_PER_MONITOR_DPI_AWARE
            return "per-monitor"
    except Exception:
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "none"


def live_monitors():
    """Every display currently attached to the desktop, with its rect."""
    rows = []
    for a in enum_adapters():
        if not a["attached"]:
            continue
        rect = desktop_rect(a["name"])
        if not rect:
            continue
        rect.update({"name": a["name"], "desc": a["desc"], "id": a["id"],
                     "primary": a["primary"],
                     "monitor": a["modes"][0]["desc"] if a["modes"] else ""})
        rows.append(rect)
    rows.sort(key=lambda r: (r["left"], r["top"]))
    return rows


def desktop_bounds():
    mons = live_monitors()
    if not mons:
        return None
    return {"left": min(m["left"] for m in mons),
            "top": min(m["top"] for m in mons),
            "right": max(m["right"] for m in mons),
            "bottom": max(m["bottom"] for m in mons)}


def scan(target_hint=None):
    """Snapshot every adapter + the mode of the ones that are live."""
    rows = []
    for a in enum_adapters():
        blob = (a["id"] + " " + a["desc"] + " " +
                " ".join(m["desc"] + " " + m["id"] for m in a["modes"])).lower()
        rows.append({
            "name": a["name"], "desc": a["desc"], "id": a["id"],
            "attached": a["attached"], "primary": a["primary"],
            "virtual": any(h in blob for h in
                           ("mttvdd", "virtual display", "gameviewer", "parsec", "idd", "spacedesk")),
            "monitor": a["modes"][0]["desc"] if a["modes"] else "",
            "current": current_mode(a["name"]) if a["attached"] else None,
        })
    return rows


if __name__ == "__main__":
    import json
    rows = describe()
    for r in rows:
        r.pop("adapterId", None)
        print(json.dumps(r, ensure_ascii=False))
    print()
    print("free targets:", len(free_targets()))
    print("sizeof PATH_INFO:", ctypes.sizeof(PATH_INFO), "(must be 72)")
    print("sizeof DEVMODEW:", ctypes.sizeof(DEVMODEW), "(must be 220)")
    print()
    for a in enum_adapters():
        print(f"{a['name']:12s} {a['desc'][:40]:40s} attached={a['attached']} "
              f"primary={a['primary']} monitors={len(a['modes'])}")
        for m in a["modes"]:
            print(f"    {m['name']:22s} {m['desc'][:40]:40s}")
        if a["attached"]:
            print(f"    current={current_mode(a['name'])}")
    print()
    print("virtual display candidates:")
    for a in find_virtual_display():
        print("   ", a["name"], a["desc"], a["id"][:80])
