import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import ctypes, time
from ctypes import wintypes
u = ctypes.windll.user32
g = ctypes.windll.gdi32
u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
hwnd = u.FindWindowW(None, "DroidExtend \u2014 \u5b89\u5353\u62d3\u5c55\u5c4f")
print("hwnd", hwnd)
r = wintypes.RECT()
u.GetWindowRect(hwnd, ctypes.byref(r))
print("rect", r.left, r.top, r.right, r.bottom)
u.SetForegroundWindow(hwnd)
time.sleep(0.6)
w, h = r.right - r.left, r.bottom - r.top
hdc = u.GetDC(0); mdc = g.CreateCompatibleDC(hdc)
bmp = g.CreateCompatibleBitmap(hdc, w, h)
g.SelectObject(mdc, bmp)
g.BitBlt(mdc, 0, 0, w, h, hdc, r.left, r.top, 0x00CC0020)

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
bi = BITMAPINFOHEADER(); bi.biSize = 40; bi.biWidth = w; bi.biHeight = -h
bi.biPlanes = 1; bi.biBitCount = 32; bi.biCompression = 0
buf = ctypes.create_string_buffer(w * h * 4)
g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
g.DeleteObject(bmp); g.DeleteDC(mdc); u.ReleaseDC(0, hdc)

import struct, zlib
def chunk(t, d):
    c = t + d
    return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
raw = bytearray()
rows = buf.raw
for y in range(h):
    raw.append(0)
    for x in range(w):
        i = (y * w + x) * 4
        raw += bytes((rows[i+2], rows[i+1], rows[i], 255))
png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
       + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
open(os.path.join(REPO, "tools", "shots", "exe2.png"), "wb").write(png)
print("wrote", w, h)
