"""Turn the phone's touches into real mouse input on Windows.

This rides a *second* adb-reverse tunnel.  The video travels host -> phone on
27183; touches travel phone -> host on 27184, so a burst of input can never
queue behind a keyframe.

Wire format, one 7-byte record per event, little endian:

    action  uint8    1 down | 2 move | 3 up | 4 scroll | 5 right click
    x       uint16   0..65535 across the picture the phone is showing
    y       uint16   0..65535
    z       uint16   scroll only: signed dx in the low half, dy in the high

Coordinate contract: the phone normalises inside the video rectangle it is
displaying, we map that onto the virtual display's desktop rectangle.  So it
does not matter whether the phone letterboxes the picture or stretches it to
fill - a touch on the top-left of the picture is the top-left of the screen.

Injection is SendInput with MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
which addresses the *whole* desktop, primary screen included.  That only works
in physical pixels if the process is per-monitor DPI aware, which is why
app.py calls win_display.set_dpi_aware() before anything else.
"""
import ctypes
import socket
import struct
import threading
import time
from ctypes import wintypes

INPUT_PORT = 27184

REC = struct.Struct("<BHHH")
DOWN, MOVE, UP, SCROLL, RCLICK = 1, 2, 3, 4, 5

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
WHEEL_DELTA = 120

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32 = ctypes.windll.user32
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def _send(flags, dx=0, dy=0, data=0):
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.u.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def virtual_desktop():
    g = user32.GetSystemMetrics
    return {"left": g(SM_XVIRTUALSCREEN), "top": g(SM_YVIRTUALSCREEN),
            "width": g(SM_CXVIRTUALSCREEN), "height": g(SM_CYVIRTUALSCREEN)}


def _abs(nx, ny):
    """0..65535 over the whole virtual desktop, as SendInput wants it."""
    d = virtual_desktop()
    w = max(d["width"] - 1, 1)
    h = max(d["height"] - 1, 1)
    x = d["left"] + int(round(nx * w / 65535.0))
    y = d["top"] + int(round(ny * h / 65535.0))
    # SendInput's 0..65535 grid is inclusive of the last pixel, so normalise to
    # the desktop box rather than to the pixel count.
    ax = int(round((x - d["left"]) * 65535.0 / w))
    ay = int(round((y - d["top"]) * 65535.0 / h))
    return max(0, min(65535, ax)), max(0, min(65535, ay))


class Injector(object):
    """Maps normalised picture coordinates onto one monitor and clicks there."""

    def __init__(self, rect=None, log=None):
        self.rect = dict(rect) if rect else None
        self.log = log or (lambda m: None)
        self._down = False
        self._wheel = 0.0
        self._hwheel = 0.0

    def set_rect(self, rect):
        self.rect = dict(rect) if rect else None

    def _screen(self, nx, ny):
        r = self.rect or virtual_desktop()
        x = r["left"] + nx / 65535.0 * max(r["width"] - 1, 1)
        y = r["top"] + ny / 65535.0 * max(r["height"] - 1, 1)
        return x, y

    def _move_to(self, nx, ny):
        x, y = self._screen(nx, ny)
        d = virtual_desktop()
        ax = int(round((x - d["left"]) * 65535.0 / max(d["width"] - 1, 1)))
        ay = int(round((y - d["top"]) * 65535.0 / max(d["height"] - 1, 1)))
        ax = max(0, min(65535, ax))
        ay = max(0, min(65535, ay))
        return _send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                     | MOUSEEVENTF_VIRTUALDESK, ax, ay)

    def down(self, nx, ny):
        self._move_to(nx, ny)
        self._down = True
        return _send(MOUSEEVENTF_LEFTDOWN)

    def move(self, nx, ny):
        return self._move_to(nx, ny)

    def up(self, nx, ny):
        self._move_to(nx, ny)
        self._down = False
        return _send(MOUSEEVENTF_LEFTUP)

    def right_click(self, nx, ny):
        self._move_to(nx, ny)
        _send(MOUSEEVENTF_RIGHTDOWN)
        time.sleep(0.02)
        return _send(MOUSEEVENTF_RIGHTUP)

    def scroll(self, dx, dy):
        """dx/dy are the phone's finger travel in video pixels."""
        if self._down:                    # a drag, not a scroll
            return 0
        self._wheel += dy
        self._hwheel += dx
        n = 0
        while abs(self._wheel) >= 40:
            step = WHEEL_DELTA if self._wheel > 0 else -WHEEL_DELTA
            _send(MOUSEEVENTF_WHEEL, data=step & 0xFFFFFFFF)
            self._wheel -= 40 if step > 0 else -40
            n += 1
        while abs(self._hwheel) >= 40:
            step = WHEEL_DELTA if self._hwheel > 0 else -WHEEL_DELTA
            _send(MOUSEEVENTF_HWHEEL, data=step & 0xFFFFFFFF)
            self._hwheel -= 40 if step > 0 else -40
            n += 1
        return n


class TouchServer(object):
    """Listens for the phone's input channel and injects what it sends."""

    def __init__(self, rect=None, port=INPUT_PORT, log=None):
        self.port = port
        self.log = log or (lambda m: None)
        self.injector = Injector(rect, log)
        self.sock = None
        self._stop = threading.Event()
        self._threads = []
        self.stats = {"events": 0, "clients": 0}

    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", self.port))
        s.listen(2)
        s.settimeout(0.5)
        self.sock = s
        t = threading.Thread(target=self._accept, name="touch-accept", daemon=True)
        t.start()
        self._threads.append(t)
        self.log("触摸通道监听 127.0.0.1:%d" % self.port)

    def _accept(self):
        while not self._stop.is_set():
            try:
                conn, addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.stats["clients"] += 1
            self.log("触摸通道已连接: %s:%d" % addr)
            self._threads.append(threading.Thread(
                target=self._read, args=(conn,), name="touch-read", daemon=True))
            self._threads[-1].start()

    def _read(self, conn):
        conn.settimeout(1.0)
        buf = b""
        try:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= REC.size:
                    action, x, y, z = REC.unpack_from(buf, 0)
                    buf = buf[REC.size:]
                    self._apply(action, x, y, z)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self.log("触摸通道已断开")

    def _apply(self, action, x, y, z):
        self.stats["events"] += 1
        try:
            if action == DOWN:
                self.injector.down(x, y)
            elif action == MOVE:
                self.injector.move(x, y)
            elif action == UP:
                self.injector.up(x, y)
            elif action == RCLICK:
                self.injector.right_click(x, y)
            elif action == SCROLL:
                # z low half = signed dx, high half = signed dy
                dx = struct.unpack("<h", struct.pack("<H", z & 0xFFFF))[0]
                dy = struct.unpack("<h", struct.pack("<H", (z >> 16) & 0xFFFF))[0]
                self.injector.scroll(dx, dy)
        except Exception as e:
            self.log("注入输入失败: %s" % e)

    def stop(self):
        self._stop.set()
        s, self.sock = self.sock, None
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
