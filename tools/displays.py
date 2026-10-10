#!/usr/bin/env python3
"""Enumerate Windows display adapters / monitors / modes via ctypes.

Purpose: find out whether an installed indirect display driver (IDD) already
exposes extra monitors we can switch on without installing a driver.
"""
import ctypes
from ctypes import wintypes


class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", ctypes.c_short),
        ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short),
        ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short),
        ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short),
        ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


user32 = ctypes.windll.user32

FLAGS = {
    0x1: "ATTACHED_TO_DESKTOP",
    0x2: "PRIMARY_DEVICE",
    0x4: "MIRRORING_DRIVER",
    0x8: "VGA_COMPATIBLE",
    0x10: "REMOVABLE",
    0x20: "MODESPRUNED",
    0x40: "REMOTE",
    0x80: "DISCONNECT",
}


def flag_str(f):
    return "|".join(v for k, v in FLAGS.items() if f & k) or hex(f)


def list_adapters(detail=True):
    i = 0
    out = []
    while True:
        d = DISPLAY_DEVICE()
        d.cb = ctypes.sizeof(d)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(d), 0):
            break
        out.append(d)
        i += 1
    for a in out:
        print(f"ADAPTER [{a.DeviceName}] {a.DeviceString!r} flags={flag_str(a.StateFlags)}")
        print(f"        id={a.DeviceID}")
        j = 0
        while True:
            m = DISPLAY_DEVICE()
            m.cb = ctypes.sizeof(m)
            if not user32.EnumDisplayDevicesW(a.DeviceName, j, ctypes.byref(m), 0):
                break
            print(f"   MONITOR [{m.DeviceName}] {m.DeviceString!r} flags={flag_str(m.StateFlags)}")
            print(f"           id={m.DeviceID}")
            if detail:
                print_modes(m.DeviceName)
            j += 1


def print_modes(mon):
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(dm)
    if user32.EnumDisplaySettingsExW(mon, -1, ctypes.byref(dm), 0):
        print(f"           CURRENT {dm.dmPelsWidth}x{dm.dmPelsHeight}@{dm.dmDisplayFrequency} "
              f"{dm.dmBitsPerPel}bpp pos=({dm.dmPosition.x},{dm.dmPosition.y})" if hasattr(dm, "dmPosition")
              else f"           CURRENT {dm.dmPelsWidth}x{dm.dmPelsHeight}@{dm.dmDisplayFrequency}")
    modes = set()
    idx = 0
    while True:
        dm = DEVMODE()
        dm.dmSize = ctypes.sizeof(dm)
        if not user32.EnumDisplaySettingsExW(mon, idx, ctypes.byref(dm), 0):
            break
        modes.add((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency))
        idx += 1
    if modes:
        top = sorted(modes, key=lambda t: (-t[0] * t[1], -t[2]))[:6]
        print(f"           modes({len(modes)}): " + ", ".join(f"{w}x{h}@{r}" for w, h, r in top))


# dmPosition lives in a union in the real DEVMODE; patch it in after the fact.
class POINTL(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def main():
    list_adapters()
    print()
    print("=== physical desktop metrics ===")
    print("SM_CXVIRTUALSCREEN", user32.GetSystemMetrics(78),
          "SM_CYVIRTUALSCREEN", user32.GetSystemMetrics(79),
          "SM_XVIRTUALSCREEN", user32.GetSystemMetrics(76),
          "SM_YVIRTUALSCREEN", user32.GetSystemMetrics(77),
          "SM_CMONITORS", user32.GetSystemMetrics(80))
    print("GetSystemMetrics(0,1) =", user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


if __name__ == "__main__":
    main()
