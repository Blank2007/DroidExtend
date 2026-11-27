"""Frameless window behaviour for the pywebview shell.

The window has no system frame (the drawing in the page is the window), so
every part of a normal window has to be given back by hand:

* dragging the title bar      -- JS asks Python, a thread follows the pointer
* eight-way resizing         -- the same loop with a different direction
* double click / the box      -- maximize and restore against the work area
* snap on release             -- top/left/right/corners, like Windows does
* rounded corners            -- SetWindowRgn, redrawn after every move

Everything here is Win32 calls against the top level window that WinForms (the
pywebview backend) created; the class only ever talks about rectangles in
physical pixels.
"""
import ctypes
import ctypes.wintypes as wt
import os
import threading
import time

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_dwm = ctypes.WinDLL("dwmapi")
_gdi = ctypes.WinDLL("gdi32")
_shcore = ctypes.WinDLL("shcore")

_u32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
_u32.GetKeyState.argtypes = [ctypes.c_int]
_u32.GetKeyState.restype = ctypes.c_short
_u32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
_u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_u32.SetWindowPos.restype = ctypes.c_bool
_u32.IsZoomed.argtypes = [wt.HWND]
_u32.IsZoomed.restype = ctypes.c_bool
_u32.IsIconic.argtypes = [wt.HWND]
_u32.IsIconic.restype = ctypes.c_bool
_u32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
_u32.ShowWindow.restype = ctypes.c_bool
_u32.GetWindowTextLengthW.argtypes = [wt.HWND]
_u32.GetWindowTextW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
_u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(ctypes.c_ulong)]
_u32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
_u32.PostMessageW.argtypes = [wt.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_size_t]
_u32.MonitorFromPoint.argtypes = [wt.POINT, ctypes.c_uint]
_u32.MonitorFromPoint.restype = ctypes.c_void_p
_u32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int * 10)]
_gdi.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int]
_gdi.CreateRoundRectRgn.restype = ctypes.c_void_p
_gdi.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
_gdi.CreateRectRgn.restype = ctypes.c_void_p
_u32.SetWindowRgn.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_bool]
_u32.SetForegroundWindow.argtypes = [wt.HWND]
_u32.SetForegroundWindow.restype = ctypes.c_bool

VK_LBUTTON = 0x01
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SW_MINIMIZE = 6
SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9
WM_SYSCOMMAND = 0x0112
SC_RESTORE = 0xF120
ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, ctypes.c_size_t)
MONITOR_DEFAULTTONEAREST = 2

# The eight resize directions, numbered the way the page sends them.
SIZE_LEFT = (10, 13, 16)
SIZE_RIGHT = (11, 14, 17)
SIZE_TOP = (12, 13, 14)
SIZE_BOTTOM = (15, 16, 17)
DRAG = 2
MIN_W = 880
MIN_H = 600


def dpi_aware():
    """Physical pixels everywhere -- must run before the first window exists."""
    try:
        _shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            _u32.SetProcessDPIAware()
        except Exception:
            pass


def find_hwnd(title, pid):
    """The top level window of this process whose caption is exactly title."""
    found = []

    def _enum(h, _lp):
        n = _u32.GetWindowTextLengthW(h)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _u32.GetWindowTextW(h, buf, n + 1)
        p = ctypes.c_ulong()
        _u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and buf.value == title:
            found.append(h)
        return True

    _u32.EnumWindows(ENUMPROC(_enum), 0)
    return found[0] if found else 0


def cursor_pos():
    p = wt.POINT()
    _u32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def work_area(x, y):
    """(left, top, right, bottom) of the monitor nearest a point."""
    mon = _u32.MonitorFromPoint(wt.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
    info = (ctypes.c_int * 10)()
    info[0] = 40                      # sizeof(MONITORINFO), in bytes
    if not mon or not _u32.GetMonitorInfoW(mon, info):
        return (0, 0, 2560, 1540)
    # rcMonitor is info[1..4], rcWork is info[5..8] -- the taskbar lives in the
    # difference, and a maximized window must not cover it.
    return (info[5], info[6], info[7], info[8])


class Win(object):
    """One frameless window, addressed by its top level handle."""

    def __init__(self, hwnd, scale=1.0):
        self.hwnd = wt.HWND(hwnd)
        self.scale = float(scale or 1.0)
        self.radius = int(round(14 * self.scale))
        self.min_w = int(MIN_W * self.scale)
        self.min_h = int(MIN_H * self.scale)
        self.maxed = False
        self._restore = None          # rect to go back to after maximize
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ basics
    def rect(self):
        r = wt.RECT()
        _u32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)

    def set_rect(self, x, y, w, h):
        _u32.SetWindowPos(self.hwnd, None, int(x), int(y), int(w), int(h),
                          SWP_NOZORDER | SWP_NOACTIVATE)

    def round_corners(self):
        """Clip the window to a rounded rectangle; a maximized one stays square."""
        if not self.hwnd:
            return
        x, y, w, h = self.rect()
        if self.maxed or w <= 0 or h <= 0:
            rgn = _gdi.CreateRectRgn(0, 0, max(1, w), max(1, h))
        else:
            r = max(2, self.radius)
            rgn = _gdi.CreateRoundRectRgn(0, 0, w + 1, h + 1, r * 2, r * 2)
        _u32.SetWindowRgn(self.hwnd, ctypes.c_void_p(rgn), True)

    def dark_frame(self):
        """Dark title bar and border, for the frames Windows still draws."""
        v = ctypes.c_int(1)
        for attr in (20, 19):
            _dwm.DwmSetWindowAttribute(self.hwnd, attr, ctypes.byref(v), 4)

    def is_zoomed(self):
        return bool(_u32.IsZoomed(self.hwnd))

    def is_minimized(self):
        return bool(_u32.IsIconic(self.hwnd))

    # ------------------------------------------------------- window commands
    def maximize(self, topmost_edges=True):
        if self.maxed:
            return
        x, y, w, h = self.rect()
        self._restore = (x, y, w, h)
        px, py = cursor_pos()
        l, t, r, b = work_area(px, py)
        self.maxed = True
        self.set_rect(l, t, r - l, b - t)
        self.round_corners()

    def restore(self):
        if not self.maxed:
            self.round_corners()
            return
        self.maxed = False
        x, y, w, h = self._restore or (120, 90, 1250, 800)
        self.set_rect(x, y, w, h)
        self.round_corners()

    def toggle_max(self):
        if self.maxed or self.is_zoomed():
            self.restore()
        else:
            self.maximize()

    def minimize(self):
        _u32.ShowWindow(self.hwnd, SW_MINIMIZE)

    def restore_from_taskbar(self):
        _u32.PostMessageW(self.hwnd, WM_SYSCOMMAND, SC_RESTORE, 0)

    def hide(self):
        """Take the window off the screen and off the taskbar (tray mode)."""
        _u32.ShowWindow(self.hwnd, SW_HIDE)

    def show(self):
        _u32.ShowWindow(self.hwnd, SW_SHOW)
        _u32.SetForegroundWindow(self.hwnd)
        _u32.SetWindowPos(self.hwnd, wt.HWND(-1), 0, 0, 0, 0,
                          SWP_NOMOVE | SWP_NOSIZE)


class Dragger(object):
    """Follows the pointer while a button is held, and snaps on release."""

    def __init__(self, win):
        self.win = win

    def start(self, hit, snap=True):
        if self.win.maxed and hit == DRAG:
            # Dragging a maximized title bar pops the window back out and
            # keeps the pointer at the same fraction of its width -- without
            # this the window would jump to the pointer's top left corner.
            x, y, w, h = self.win.rect()
            px, py = cursor_pos()
            rx, ry, rw, rh = self.win._restore or (120, 90, 1250, 800)
            frac = 0.5 if w <= 0 else max(0.05, min(0.95, (px - x) / float(w)))
            self.win.maxed = False
            self.win.set_rect(int(px - rw * frac), int(py - rh * 0.04), rw, rh)
            self.win.round_corners()
        x0, y0 = cursor_pos()
        box = self.win.rect()
        threading.Thread(target=self._loop, args=(int(hit), x0, y0, box, bool(snap)),
                         name="dx-drag", daemon=True).start()

    def _loop(self, hit, x0, y0, box, snap):
        L, T, W, H = box
        win = self.win
        while _u32.GetKeyState(VK_LBUTTON) & 0x8000:
            px, py = cursor_pos()
            dx, dy = px - x0, py - y0
            nl, nt, nw, nh = L, T, W, H
            if hit == DRAG:
                nl, nt = L + dx, T + dy
            else:
                if hit in SIZE_LEFT:
                    nl = min(L + dx, L + W - win.min_w)
                    nw = W + (L - nl)
                if hit in SIZE_RIGHT:
                    nw = max(win.min_w, W + dx)
                if hit in SIZE_TOP:
                    nt = min(T + dy, T + H - win.min_h)
                    nh = H + (T - nt)
                if hit in SIZE_BOTTOM:
                    nh = max(win.min_h, H + dy)
            with win._lock:
                win.set_rect(nl, nt, nw, nh)
            time.sleep(0.008)
        win.round_corners()
        if hit == DRAG and snap:
            self._snap()

    def _snap(self):
        px, py = cursor_pos()
        win = self.win
        l, t, r, b = work_area(px, py)
        edge_x = max(4, int(10 * win.scale))
        w, h = r - l, b - t
        on_left = px <= l + edge_x
        on_right = px >= r - edge_x
        on_top = py <= t + edge_x
        on_bottom = py >= b - edge_x
        if not (on_left or on_right or on_top or on_bottom):
            return
        x, y, ww, hh = win.rect()
        if on_top and on_left:
            win._restore = (x, y, ww, hh)
            win.set_rect(l, t, w // 2, h // 2)
        elif on_top and on_right:
            win._restore = (x, y, ww, hh)
            win.set_rect(l + w // 2, t, w - w // 2, h // 2)
        elif on_bottom and on_left:
            win._restore = (x, y, ww, hh)
            win.set_rect(l, t + h // 2, w // 2, h - h // 2)
        elif on_bottom and on_right:
            win._restore = (x, y, ww, hh)
            win.set_rect(l + w // 2, t + h // 2, w - w // 2, h - h // 2)
        elif on_top:
            win._restore = (x, y, ww, hh)
            win.maxed = False
            win.maxed = True
            win.set_rect(l, t, w, h)
        elif on_left:
            win._restore = (x, y, ww, hh)
            win.set_rect(l, t, w // 2, h)
        elif on_right:
            win._restore = (x, y, ww, hh)
            win.set_rect(l + w // 2, t, w - w // 2, h)
        win.round_corners()