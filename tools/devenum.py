"""Enumerate adapters/monitors with EnumDisplayDevices + probe QDC buffer stride."""
import ctypes, sys
from ctypes import wintypes

user32 = ctypes.windll.user32


class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


class POINTL(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


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


FLAGS = {0x1: "ATTACHED", 0x2: "PRIMARY", 0x4: "MIRROR", 0x8: "VGA", 0x10: "REMOVABLE",
         0x20: "MODESPRUNED", 0x40: "REMOTE", 0x80: "DISCONNECT"}


def fs(f):
    return "|".join(v for k, v in FLAGS.items() if f & k) or hex(f)


print("sizeof DEVMODEW", ctypes.sizeof(DEVMODEW), flush=True)

i = 0
while True:
    d = DISPLAY_DEVICE()
    d.cb = ctypes.sizeof(d)
    if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(d), 0):
        break
    print(f"ADAPTER[{i}] {d.DeviceName} {d.DeviceString!r} {fs(d.StateFlags)}", flush=True)
    j = 0
    while True:
        m = DISPLAY_DEVICE()
        m.cb = ctypes.sizeof(m)
        if not user32.EnumDisplayDevicesW(d.DeviceName, j, ctypes.byref(m), 0):
            break
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(dm)
        cur = ""
        if user32.EnumDisplaySettingsExW(m.DeviceName, -1, ctypes.byref(dm), 0):
            cur = f"{dm.dmPelsWidth}x{dm.dmPelsHeight}@{dm.dmDisplayFrequency} at ({dm.dmPosition.x},{dm.dmPosition.y})"
        modes = set()
        k = 0
        while True:
            d2 = DEVMODEW()
            d2.dmSize = ctypes.sizeof(d2)
            if not user32.EnumDisplaySettingsExW(m.DeviceName, k, ctypes.byref(d2), 0):
                break
            modes.add((d2.dmPelsWidth, d2.dmPelsHeight, d2.dmDisplayFrequency))
            k += 1
        top = sorted(modes, key=lambda t: (-t[0] * t[1], -t[2]))[:5]
        print(f"   MON[{j}] {m.DeviceName} {m.DeviceString!r} {fs(m.StateFlags)} cur={cur or '-'} "
              f"modes={len(modes)} top={top}", flush=True)
        j += 1
    i += 1

print("metrics: monitors=", user32.GetSystemMetrics(80),
      "virt=", user32.GetSystemMetrics(78), user32.GetSystemMetrics(79),
      "origin=", user32.GetSystemMetrics(76), user32.GetSystemMetrics(77), flush=True)
print("done", flush=True)
