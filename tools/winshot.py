"""Capture a top-level window (by hwnd or by title), even if it is behind others.

PrintWindow(PW_RENDERFULLCONTENT) asks the window to paint itself into our DC,
so z-order and occlusion do not matter.  Importable:

    import winshot
    winshot.capture_by_title("DroidExtend ...", "shot.png")
    winshot.capture(hwnd, "shot.png")

CLI:  python tools\\winshot.py "<title>" <out.png>
"""
import ctypes
import struct
import sys
import time
import zlib
from ctypes import wintypes

u = ctypes.windll.user32
g = ctypes.windll.gdi32

try:
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass

PW_RENDERFULLCONTENT = 2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def _chunk(tag, data):
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def capture(hwnd, out, raise_it=True):
    """PrintWindow the given hwnd into a PNG.  Returns (width, height)."""
    if raise_it:
        u.SetForegroundWindow(hwnd)
        u.ShowWindow(hwnd, 5)
        time.sleep(0.4)

    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top

    hdc = u.GetDC(0)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT)
    time.sleep(0.2)

    bi = BITMAPINFOHEADER()
    bi.biSize, bi.biWidth, bi.biHeight = 40, w, -h
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(0, hdc)

    rows = buf.raw
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        base = y * w * 4
        for x in range(w):
            i = base + x * 4
            raw += bytes((rows[i + 2], rows[i + 1], rows[i], 255))

    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
           + _chunk(b"IEND", b""))
    with open(out, "wb") as fh:
        fh.write(png)
    return w, h


def capture_by_title(title, out, raise_it=True):
    hwnd = u.FindWindowW(None, title)
    if not hwnd:
        raise SystemExit("no window titled %r" % title)
    return capture(hwnd, out, raise_it=raise_it)


if __name__ == "__main__":
    title, out = sys.argv[1], sys.argv[2]
    hwnd = u.FindWindowW(None, title)
    if not hwnd:
        raise SystemExit("no window titled %r" % title)
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    print("hwnd %d rect %d,%d %dx%d" % (hwnd, r.left, r.top, r.right - r.left, r.bottom - r.top))
    print("wrote", out, capture(hwnd, out))
