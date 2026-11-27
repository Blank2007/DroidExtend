"""The window frame, in the one way Windows likes it.

The page paints everything, but the window underneath is a real window: the
system keeps its frame, its shadow and its rounded corners, and every native
gesture keeps working -- Aero Snap, Win+arrow, double click on the title bar,
Alt+Space, the taskbar menu, dragging between monitors, eight-way resizing.

How that is done:

* the WinForms window is given back WS_THICKFRAME | WS_CAPTION | WS_MAXIMIZEBOX
  | WS_MINIMIZEBOX | WS_SYSMENU, so the system believes in a frame again;
* WM_NCCALCSIZE then shrinks the client area by the border only, not by the
  title bar, so the caption is never drawn and the page fills what is left;
* the client area is inset by the real border width, which leaves the system's
  own resize band outside it -- that is why resizing, snapping and the shadow
  behave exactly like they do for every other window;
* dragging and double clicking come from the page: it asks for WM_NCLBUTTONDOWN
  with HTCAPTION, and from there Windows itself runs the drag loop (snap
  previews included).

Coordinates are physical pixels; the caller passes the DPI scale of the window.
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time

import os as _os

_TRACE = _os.environ.get("DX_TRACE") or None

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_dwm = ctypes.WinDLL("dwmapi")
_shcore = ctypes.WinDLL("shcore")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, ctypes.c_uint, ctypes.c_size_t,
                             ctypes.c_ssize_t)

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_POPUP = 0x80000000
WS_THICKFRAME = 0x00040000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_CLIPCHILDREN = 0x02000000

WM_GETMINMAXINFO = 0x0024
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONDBLCLK = 0x00A3
WM_SYSCOMMAND = 0x0112
WM_SIZE = 0x0005
WM_DPICHANGED = 0x02E0
WM_SETTINGCHANGE = 0x001A

HTCAPTION = 2
HTCLIENT = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SW_HIDE = 0
SW_SHOW = 5
SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9
SM_CXSIZEFRAME = 32
SM_CYSIZEFRAME = 33
SM_CXPADDEDBORDER = 92
MONITOR_DEFAULTTONEAREST = 2
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWCP_DONOTROUND = 1
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19

# The page's own palette (web/style.css): the frame DWM draws must match it,
# otherwise the eight pixels above the client area show up in the accent colour.
FRAME_BG_DARK = 0x0E0F12
FRAME_BG_LIGHT = 0xF5F7FA
FRAME_LINE_DARK = 0x26282C
FRAME_LINE_LIGHT = 0xE7E9ED

VK_LBUTTON = 0x01
VK_ESCAPE = 0x1B
MIN_W = 880
MIN_H = 600
CAPTION_H = 44        # the page's title bar, in CSS pixels
BUTTONS_W = 140       # the window buttons on its right, in CSS pixels
ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, ctypes.c_size_t)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", RECT * 3), ("lppos", ctypes.c_void_p)]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", wt.POINT), ("ptMaxSize", wt.POINT),
                ("ptMaxPosition", wt.POINT), ("ptMinTrackSize", wt.POINT),
                ("ptMaxTrackSize", wt.POINT)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint), ("flags", ctypes.c_uint),
                ("showCmd", ctypes.c_uint), ("ptMinPosition", wt.POINT),
                ("ptMaxPosition", wt.POINT), ("rcNormalPosition", RECT)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wt.DWORD)]


_u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_u32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
_u32.DefWindowProcW.restype = LRESULT
_u32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, ctypes.c_size_t,
                                ctypes.c_ssize_t]
_u32.SendMessageW.restype = LRESULT
_u32.SendMessageW.argtypes = [wt.HWND, ctypes.c_uint, ctypes.c_size_t,
                              ctypes.c_ssize_t]
_u32.CallWindowProcW.restype = LRESULT
_u32.CallWindowProcW.argtypes = [ctypes.c_void_p, wt.HWND, ctypes.c_uint,
                                 ctypes.c_size_t, ctypes.c_ssize_t]
_u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
_u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_u32.SetWindowPos.restype = ctypes.c_bool
_u32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
_u32.ShowWindow.restype = ctypes.c_bool
_u32.IsZoomed.argtypes = [wt.HWND]
_u32.IsZoomed.restype = ctypes.c_bool
_u32.IsIconic.argtypes = [wt.HWND]
_u32.IsIconic.restype = ctypes.c_bool
_u32.GetDpiForWindow.argtypes = [wt.HWND]
_u32.GetDpiForWindow.restype = ctypes.c_uint
_u32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, ctypes.c_uint]
_u32.GetSystemMetricsForDpi.restype = ctypes.c_int
_u32.MonitorFromWindow.argtypes = [wt.HWND, ctypes.c_uint]
_u32.MonitorFromWindow.restype = ctypes.c_void_p
_u32.MonitorFromPoint.argtypes = [wt.POINT, ctypes.c_uint]
_u32.MonitorFromPoint.restype = ctypes.c_void_p
_u32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
_u32.PostMessageW.argtypes = [wt.HWND, ctypes.c_uint, ctypes.c_size_t,
                              ctypes.c_ssize_t]
_u32.SendMessageW.restype = LRESULT
_u32.SendMessageW.argtypes = [wt.HWND, ctypes.c_uint, ctypes.c_size_t,
                              ctypes.c_ssize_t]
_u32.ReleaseCapture.argtypes = []
_u32.ReleaseCapture.restype = ctypes.c_bool
_u32.SetForegroundWindow.argtypes = [wt.HWND]
_u32.SetForegroundWindow.restype = ctypes.c_bool
_u32.GetWindowTextLengthW.argtypes = [wt.HWND]
_u32.GetWindowTextW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
_u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(ctypes.c_ulong)]
_u32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_size_t]


def _set_long(hwnd, index, value):
    try:
        return _u32.SetWindowLongPtrW(hwnd, index, value)
    except AttributeError:                                            # 32 bit
        return _u32.SetWindowLongW(hwnd, index, value)


def _get_long(hwnd, index):
    try:
        return _u32.GetWindowLongPtrW(hwnd, index)
    except AttributeError:                                            # 32 bit
        return _u32.GetWindowLongW(hwnd, index)


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


def _env_no(name):
    import os
    return str(os.environ.get(name, "")).strip() not in ("", "0", "no", "false")


def _monitor_info(handle):
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if handle and _u32.GetMonitorInfoW(handle, ctypes.byref(info)):
        return info
    return None


def work_area(x, y):
    """(left, top, right, bottom) of the monitor nearest a point."""
    info = _monitor_info(_u32.MonitorFromPoint(wt.POINT(int(x), int(y)),
                                               MONITOR_DEFAULTTONEAREST))
    if info is None:
        return (0, 0, 2560, 1540)
    w = info.rcWork
    return (w.left, w.top, w.right, w.bottom)


class Win(object):
    """One real window, wearing the page as its client area."""

    def __init__(self, hwnd, scale=1.0, on_state=None):
        self.hwnd = wt.HWND(hwnd)
        self.scale = float(scale or 1.0)
        self.on_state = on_state
        self.maxed = bool(_u32.IsZoomed(self.hwnd))
        self.min_w = int(MIN_W * self.scale)
        self.min_h = int(MIN_H * self.scale)
        self._proc = WNDPROC(self._wndproc)
        self._old = None
        self._lock = threading.Lock()
        self._stop = False
        self._normal = self.rect()
        self._before = self._normal
        self._iconic = bool(_u32.IsIconic(self.hwnd))
        self.dpi = _u32.GetDpiForWindow(self.hwnd) or 96
        self.border = self._border_width()
        self._fix_style()
        self._hook()
        self._dwm()
        # The style landed before the hook did, so the very first non-client
        # calculation still went through the old procedure and left room for a
        # caption. Ask for one more frame change now that we answer the message.
        self._reframe()
        if not _env_no("DX_NO_WATCH"):
            threading.Thread(target=self._watch, name="dx-frame",
                             daemon=True).start()

    # ------------------------------------------------------------- the frame
    def _border_width(self):
        x = _u32.GetSystemMetricsForDpi(SM_CXSIZEFRAME, self.dpi)
        px = _u32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, self.dpi)
        return max(1, x + px)

    def _fix_style(self):
        style = int(_get_long(self.hwnd, GWL_STYLE))
        want = (WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX |
                WS_MAXIMIZEBOX | WS_CLIPCHILDREN)
        style = (style | want) & ~int(WS_POPUP)
        _set_long(self.hwnd, GWL_STYLE, ctypes.c_ssize_t(style).value)
        _u32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                          SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER |
                          SWP_NOACTIVATE | SWP_FRAMECHANGED)

    def _hook(self):
        old = _set_long(self.hwnd, -4, ctypes.cast(self._proc, ctypes.c_void_p).value)
        self._old = old or None

    def _reframe(self):
        _u32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                          SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER |
                          SWP_NOACTIVATE | SWP_FRAMECHANGED)

    def _dwm(self):
        """Rounded corners and a border in the page's own colour, from DWM."""
        round_pref = ctypes.c_int(DWMWCP_ROUND)
        _dwm.DwmSetWindowAttribute(self.hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                                   ctypes.byref(round_pref),
                                   ctypes.sizeof(round_pref))

    def dark_frame(self, dark=True):
        v = ctypes.c_int(1 if dark else 0)
        for attr in (DWMWA_USE_IMMERSIVE_DARK_MODE,
                     DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            _dwm.DwmSetWindowAttribute(self.hwnd, attr, ctypes.byref(v), 4)

    @staticmethod
    def _colorref(rgb):
        if rgb is None:
            return 0xFFFFFFFF           # DWMWA_COLOR_DEFAULT
        r = (rgb >> 16) & 0xFF
        g = (rgb >> 8) & 0xFF
        b = rgb & 0xFF
        return (b << 16) | (g << 8) | r       # COLORREF is 0x00BBGGRR

    def _set_color(self, attr, rgb):
        c = ctypes.c_uint(self._colorref(rgb))
        try:
            return _dwm.DwmSetWindowAttribute(self.hwnd, attr,
                                              ctypes.byref(c), ctypes.sizeof(c))
        except Exception:
            return -1

    def border_color(self, rgb):
        """The outer 1 px hair line around the window, in 0xRRGGBB."""
        return self._set_color(DWMWA_BORDER_COLOR, rgb)

    def frame_colors(self, bg, border=None):
        """Paint the frame DWM owns in the page's own colours.

        Measured on 26100: the eight pixels above the client area take their
        colour from DWMWA_CAPTION_COLOR -- left, right and bottom already show
        the page background -- so a dark window with the default caption gets a
        bright blue strip along its top edge.  Setting the caption colour to the
        page background makes the frame disappear into the page while every
        pixel of it stays a real, resizable system border.
        """
        self._set_color(DWMWA_CAPTION_COLOR, bg)
        self._set_color(DWMWA_BORDER_COLOR, border)

    def round_corners(self):
        """Kept for callers: DWM owns the corners now, nothing to clip."""
        self._dwm()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if _TRACE:
            try:
                with open(_TRACE, "a") as f:
                    f.write("%04x %d %d\n" % (msg, wparam, lparam))
            except Exception:
                pass
        try:
            if msg == WM_NCCALCSIZE and wparam:
                self._calc_client(lparam)
                return 0
            if msg == WM_GETMINMAXINFO:
                self._minmax(lparam)
                return 0
            if msg in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK) and wparam == HTCAPTION:
                # Straight to DefWindowProc: the toolkit's own procedure treats a
                # borderless form as having no caption and ignores this, but the
                # default one starts the system drag loop (snap previews and all).
                return _u32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception:
            pass
        return _u32.CallWindowProcW(self._old, hwnd, msg, wparam, lparam)

    def _in_caption(self, lparam):
        """Is this screen point on the band the page draws as its title bar?"""
        sx = ctypes.c_short(lparam & 0xFFFF).value
        sy = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        x, y, w, _h = self.rect()
        cap = int(round(CAPTION_H * self.scale))
        btns = int(round(BUTTONS_W * self.scale))
        return (y <= sy < y + cap) and (sx - x) < (w - btns)

    def _calc_client(self, lparam):
        """The client area is the whole window minus the resize border: no caption."""
        params = ctypes.cast(ctypes.c_void_p(lparam),
                             ctypes.POINTER(NCCALCSIZE_PARAMS)).contents
        r = params.rgrc[0]
        b = self.border
        # A maximized window is placed at work area minus the border on every
        # side, so the same inset lands exactly on the work area.
        r.left += b
        r.top += b
        r.right -= b
        r.bottom -= b
        params.rgrc[0] = r

    def _minmax(self, lparam):
        mmi = ctypes.cast(ctypes.c_void_p(lparam),
                          ctypes.POINTER(MINMAXINFO)).contents
        mmi.ptMinTrackSize.x = self.min_w
        mmi.ptMinTrackSize.y = self.min_h
        info = _monitor_info(_u32.MonitorFromWindow(self.hwnd,
                                                    MONITOR_DEFAULTTONEAREST))
        if info is None:
            return
        wa, mon = info.rcWork, info.rcMonitor
        b = self.border
        mmi.ptMaxPosition.x = wa.left - mon.left - b
        mmi.ptMaxPosition.y = wa.top - mon.top - b
        mmi.ptMaxSize.x = (wa.right - wa.left) + 2 * b
        mmi.ptMaxSize.y = (wa.bottom - wa.top) + 2 * b

    # ------------------------------------------------------------ the state
    def _watch(self):
        """Follow the system's state, and keep its remembered size honest."""
        last = self.maxed
        while not self._stop:
            try:
                zoomed = bool(_u32.IsZoomed(self.hwnd))
                iconic = bool(_u32.IsIconic(self.hwnd))
                visible = bool(_u32.IsWindowVisible(self.hwnd))
            except Exception:
                return
            came_back = (self._iconic and not iconic) or (last and not zoomed)
            if came_back and self._normal:
                x, y, w, h = self.rect()
                nx, ny, nw, nh = self._normal
                if abs(nw - w) > 4 or abs(nh - h) > 4:
                    self.set_rect(nx, ny, nw, nh)
            if (not zoomed and not iconic and visible and not came_back
                    and zoomed == last):
                # A settled, visible window: this is the size to come back to.
                self._normal = self.rect()
                self.sync_placement()
            self._iconic = iconic
            if zoomed != last:
                last = zoomed
                self.maxed = zoomed
                cb = self.on_state
                if cb is not None:
                    try:
                        cb(zoomed)
                    except Exception:
                        pass
            threading.Event().wait(0.25)

    # ----------------------------------------------------------------- basics
    def rect(self):
        r = RECT()
        _u32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)

    def client_rect(self):
        x, y, w, h = self.rect()
        b = self.border
        return (x + b, y + b, max(1, w - 2 * b), max(1, h - 2 * b))

    def set_rect(self, x, y, w, h):
        _u32.SetWindowPos(self.hwnd, None, int(x), int(y), int(w), int(h),
                          SWP_NOZORDER | SWP_NOACTIVATE)
        self.sync_placement()

    def sync_placement(self):
        """Teach Windows the rectangle it should come back to.

        Without this it restores to the size it computed before our frame code
        existed, which is 18 px narrower and 3 px shorter every trip through the
        taskbar or the maximize button.
        """
        if self.is_zoomed() or self.is_minimized():
            return
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not _u32.GetWindowPlacement(self.hwnd, ctypes.byref(wp)):
            return
        info = _monitor_info(_u32.MonitorFromWindow(self.hwnd,
                                                    MONITOR_DEFAULTTONEAREST))
        offx = info.rcMonitor.left if info else 0
        offy = info.rcMonitor.top if info else 0
        x, y, w, h = self.rect()
        wp.showCmd = 1                      # SW_SHOWNORMAL
        wp.rcNormalPosition = RECT(int(x - offx), int(y - offy),
                                   int(x - offx + w), int(y - offy + h))
        _u32.SetWindowPlacement(self.hwnd, ctypes.byref(wp))

    def is_zoomed(self):
        return bool(_u32.IsZoomed(self.hwnd))

    def is_minimized(self):
        return bool(_u32.IsIconic(self.hwnd))

    # ------------------------------------------------------ window commands
    def maximize(self):
        _u32.ShowWindow(self.hwnd, SW_MAXIMIZE)

    def restore(self):
        _u32.ShowWindow(self.hwnd, SW_RESTORE)

    def toggle_max(self):
        if self.is_zoomed():
            self.restore()
        else:
            self.maximize()

    def minimize(self):
        _u32.ShowWindow(self.hwnd, SW_MINIMIZE)

    def restore_from_taskbar(self):
        _u32.PostMessageW(self.hwnd, WM_SYSCOMMAND, 0xF120, 0)

    def hide(self):
        """Take the window off the screen and off the taskbar (tray mode)."""
        _u32.ShowWindow(self.hwnd, SW_HIDE)

    def show(self):
        _u32.ShowWindow(self.hwnd, SW_SHOW)
        _u32.SetForegroundWindow(self.hwnd)
        _u32.SetWindowPos(self.hwnd, wt.HWND(-1), 0, 0, 0, 0,
                          SWP_NOMOVE | SWP_NOSIZE)

    def browse(self):
        """Let Windows run its own drag loop: snap, monitor hopping, all of it."""
        _u32.ReleaseCapture()
        _u32.SendMessageW(self.hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)

    def drag(self, hit=None):
        """Move the window with the pointer, then snap like the shell does.

        The shell's own move loop is entered through WM_NCLBUTTONDOWN, but the
        press reaches it from inside a child WebView, and such an injected move
        loop drops out on the first tick. Doing it here keeps the gesture honest:
        the window follows the pointer, and letting go at an edge snaps it.
        """
        if self.is_zoomed():
            # A maximized window comes back to its normal size under the pointer,
            # exactly like the shell does when a title bar is dragged.
            self.restore()
            x, y, w, h = self.rect()
            pt = wt.POINT()
            _u32.GetCursorPos(ctypes.byref(pt))
            self.grab_x = max(1, min(w - 2, pt.x - x))
            self.grab_y = max(1, min(CAPTION_H - 4, pt.y - y))
        else:
            x, y, w, h = self.rect()
            pt = wt.POINT()
            _u32.GetCursorPos(ctypes.byref(pt))
            self.grab_x = max(0, pt.x - x)
            self.grab_y = max(0, pt.y - y)
        self.grab_x = int(self.grab_x)
        self.grab_y = int(self.grab_y)
        _u32.SetCapture(self.hwnd)
        moved = False
        n = 0
        try:
            while True:
                n += 1
                lb = _u32.GetAsyncKeyState(VK_LBUTTON)
                es = _u32.GetAsyncKeyState(VK_ESCAPE)
                if _TRACE:
                    try:
                        with open(_TRACE, "a") as f:
                            f.write("drag tick %d lb=%#x es=%#x\n" % (n, lb, es))
                    except Exception:
                        pass
                if not (lb & 0x8000):
                    break
                if es & 0x8000:
                    break
                _u32.GetCursorPos(ctypes.byref(pt))
                nx, ny = int(pt.x - self.grab_x), int(pt.y - self.grab_y)
                cur = self.rect()
                if nx != cur[0] or ny != cur[1]:
                    _u32.SetWindowPos(self.hwnd, None, nx, ny, 0, 0,
                                      SWP_NOSIZE | SWP_NOZORDER |
                                      SWP_NOACTIVATE)
                    moved = True
                time.sleep(0.012)
        finally:
            _u32.ReleaseCapture()
        if moved:
            try:
                self._snap_after_drag(pt.x, pt.y)
            except Exception:
                pass

    def _snap_after_drag(self, px, py):
        """Landed against a screen edge? Take that half, like the shell does."""
        x, y, w, h = self.rect()
        left, top, right, bottom = work_area(x + w // 2, y + 10)
        edge = int(round(16 * self.scale))
        span = right - left
        half = span // 2
        b = self.border
        # The window itself is what the shell looks at, not the pointer: a title
        # bar grabbed off centre puts the window's edge well past the pointer.
        if y <= top + edge:
            self.maximize()
            return
        if x <= left + edge:
            self.set_rect(left - b, top - b, half + 2 * b, (bottom - top) + 2 * b)
            return
        if x + w >= right - edge:
            self.set_rect(left + half - b, top - b, half + 2 * b,
                          (bottom - top) + 2 * b)

    def double_click(self):
        _u32.SendMessageW(self.hwnd, WM_NCLBUTTONDBLCLK, HTCAPTION, 0)

    def system_menu(self):
        # SC_KEYMENU with VK_SPACE as the parameter: the very same thing the
        # shell posts when the user presses Alt+Space.
        _u32.PostMessageW(self.hwnd, WM_SYSCOMMAND, 0xF100, 0x20)

    def stop(self):
        self._stop = True
