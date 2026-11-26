"""Real maximization and edge resizing for a window Tk made frameless.

overrideredirect(True) leaves a window with no frame at all, and Windows then
treats it as a popup: ShowWindow(SW_MAXIMIZE) does not put it in the maximized
state, every edge is dead (the app had one 16px grip in the corner), dragging a
maximized window does nothing, and Win+Up, Aero Snap and the taskbar's own
"Restore" entry do not exist for it.

This module puts the missing behaviour back by subclassing the window
procedure:

* WM_NCCALCSIZE returns 0, so the system frame keeps a zero thickness and the
  window still looks frameless;
* WM_NCHITTEST hands the edges back to Windows, which then does the eight-way
  resize itself, with its own cursor shapes and its own snap distances;
* the drawn title bar is reported as HTCAPTION, so moving the window is
  Windows' own move -- Aero Snap, drag-off-to-restore, and double-click to
  maximize all come for free;
* WM_GETMINMAXINFO keeps a maximized window inside the work area and applies
  the minimum size.

Everything else is forwarded to the procedure Tk installed, untouched.  The
one thing that has to be remembered: Tk's widgets are not HWNDs, the window has
exactly two -- the wrapper and its TkChild client area -- so both are
subclassed, and the hit test runs on whichever one Windows asks.
"""

import ctypes
import os
from ctypes import wintypes

WM_NCCALCSIZE = 0x0083
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_SYSCOMMAND = 0x0112
WM_NCLBUTTONDBLCLK = 0x00A3
WM_NCHITTEST = 0x0084
WM_GETMINMAXINFO = 0x0024

HTCLIENT = 1
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

# WM_NCHITTEST code -> the WMSZ_ direction that SC_SIZE wants.
HT_SIZING = {
    HTLEFT: 1, HTRIGHT: 2, HTTOP: 3, HTTOPLEFT: 4,
    HTTOPRIGHT: 5, HTBOTTOM: 6, HTBOTTOMLEFT: 7, HTBOTTOMRIGHT: 8,
}

GWL_WNDPROC = -4
GWL_STYLE = -16

WS_POPUP = 0x80000000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000

SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9


class _MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", wintypes.POINT),
                ("ptMaxSize", wintypes.POINT),
                ("ptMaxPosition", wintypes.POINT),
                ("ptMinTrackSize", wintypes.POINT),
                ("ptMaxTrackSize", wintypes.POINT)]


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3),
                ("lppos", ctypes.c_void_p)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


_LRESULT = ctypes.c_ssize_t
_ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)

_user32 = ctypes.windll.user32 if os.name == "nt" else None

# CallWindowProcW has to be told its argument types once: it takes a window
# procedure address, which does not fit in ctypes' default c_int -- without
# this every forwarded message raises OverflowError and Tk stops seeing its own
# input entirely.
_call = None
_defproc = None
_syscmd = None
if _user32:
    _call = _user32.CallWindowProcW
    _call.restype = _LRESULT
    _call.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                      wintypes.WPARAM, wintypes.LPARAM]
    _defproc = _user32.DefWindowProcW

    def _syscmd_impl(hwnd, is_dbl, hit):
        if hit == HTCAPTION:
            if is_dbl:
                # A double click on the caption is the maximize toggle.
                if _user32.IsZoomed(wintypes.HWND(hwnd)):
                    _user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
                else:
                    _user32.ShowWindow(wintypes.HWND(hwnd), SW_MAXIMIZE)
                return 0
            cmd = 0xF012        # SC_MOVE | 2
        elif hit in HT_SIZING:
            # SC_SIZE plus the WMSZ_ direction.  The two enums do not agree:
            # HTTOPLEFT is 13 but WMSZ_TOPLEFT is 4, HTTOPRIGHT is 14 but
            # WMSZ_TOPRIGHT is 8, so they cannot be converted by arithmetic.
            cmd = 0xF000 | HT_SIZING[hit]
        else:
            return 0
        _user32.ReleaseCapture()
        _user32.SendMessageW(wintypes.HWND(hwnd), 0x0112, cmd, 0)
        return 0

    _syscmd = _syscmd_impl


def _get_style(hwnd):
    # Styles are 32-bit values even in a 64-bit process, and the 32-bit pair of
    # calls is the one that behaves -- SetWindowLongPtrW quietly did nothing
    # here, which is how a WS_POPUP window stayed popup and kept maximizing to
    # the whole monitor instead of the work area.
    fn = _user32.GetWindowLongW
    fn.restype = ctypes.c_long
    fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return fn(hwnd, GWL_STYLE) & 0xFFFFFFFF


def _set_style(hwnd, value):
    fn = _user32.SetWindowLongW
    fn.restype = ctypes.c_long
    fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    return fn(hwnd, GWL_STYLE, value & 0xFFFFFFFF)


def _get_ptr(hwnd, index):
    fn = getattr(_user32, "GetWindowLongPtrW", None) or _user32.GetWindowLongW
    fn.restype = _LRESULT
    return fn(hwnd, index)


def _set_ptr(hwnd, index, value):
    fn = getattr(_user32, "SetWindowLongPtrW", None) or _user32.SetWindowLongW
    fn.restype = _LRESULT
    fn.argtypes = [wintypes.HWND, ctypes.c_int, _LRESULT]
    return fn(hwnd, index, value)


if _user32:
    # Monitor handles are pointers; leaving the defaults in place truncates
    # them to 32 bits and GetMonitorInfoW then fails silently, which is how the
    # maximized window ended up covering the taskbar.
    _user32.MonitorFromWindow.restype = ctypes.c_void_p
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(_MONITORINFO)]
    _user32.GetMonitorInfoW.restype = wintypes.BOOL
    _user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                      wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowExW.restype = wintypes.HWND
    _user32.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p,
                                         wintypes.LPARAM]
    _user32.GetDesktopWindow.restype = wintypes.HWND
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
    _user32.DefWindowProcW.restype = _LRESULT
    _user32.GetParent.argtypes = [wintypes.HWND]
    _user32.GetParent.restype = wintypes.HWND


def _top_hwnd(win):
    """The real top-level window Tk put behind the widget.

    winfo_id() is Tk's own child window; its parent is the wrapper that owns
    the title bar, the frame and the maximize state.  Before the window is
    mapped that parent does not exist yet, so callers that run early have to go
    through refresh() once the window is up.
    """
    try:
        base = int(win.winfo_id() or 0)
        if not base:
            return 0
        # Tk knows its own wrapper; asking it is more reliable than walking the
        # Win32 parent chain, which is empty until the window is mapped.
        try:
            frame = win.wm_frame()
        except Exception:
            frame = ""
        if frame:
            hwnd = int(frame, 16)
            if hwnd and hwnd != base:
                return hwnd
        root = _user32.GetAncestor(wintypes.HWND(base), 2)   # GA_ROOT
        desk = int(_user32.GetDesktopWindow() or 0)
        if root and int(root) != desk:
            return int(root)
        parent = _user32.GetParent(wintypes.HWND(base))
        return int(parent or base)
    except Exception:
        return 0


def _client_hwnd(win):
    try:
        return int(win.winfo_id() or 0)
    except Exception:
        return 0


def is_maximized(win):
    if not _user32:
        return False
    hwnd = _top_hwnd(win)
    return bool(hwnd and _user32.IsZoomed(wintypes.HWND(hwnd)))


def maximize(win):
    if not _user32:
        return
    hwnd = _top_hwnd(win)
    if hwnd:
        _user32.ShowWindow(wintypes.HWND(hwnd), SW_MAXIMIZE)


def restore(win):
    if not _user32:
        return
    hwnd = _top_hwnd(win)
    if hwnd:
        _user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)


def minimize(win):
    if not _user32:
        return
    hwnd = _top_hwnd(win)
    if hwnd:
        _user32.ShowWindow(wintypes.HWND(hwnd), SW_MINIMIZE)


def normal_rect(win):
    """Where the window goes back to, as (x, y, w, h), or None."""
    if not _user32:
        return None
    hwnd = _top_hwnd(win)
    if not hwnd:
        return None
    try:
        wp = _WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(wp)
        if _user32.GetWindowPlacement(wintypes.HWND(hwnd), ctypes.byref(wp)):
            r = wp.rcNormalPosition
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        pass
    return None


def toggle_maximize(win):
    if is_maximized(win):
        restore(win)
    else:
        maximize(win)


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT),
                ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT),
                ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]


class Frame(object):
    """What install() returns: the live subclass, holding its own state."""

    def __init__(self, win, min_size, caption_height, caption_skip):
        self.win = win
        self.min_w, self.min_h = min_size
        try:
            self.scale = float(win.winfo_fpixels("1i")) / 96.0
        except Exception:
            self.scale = 1.0
        self.caption = float(caption_height) * self.scale
        self.skip = float(caption_skip) * self.scale
        self.edge = max(6.0, round(7.0 * self.scale))
        self._procs = {}
        self._orig = {}
        self.seen = set()
        self.debug = []
        self.top_hwnd = _top_hwnd(win)
        self.client_hwnd = _client_hwnd(win)
        for hwnd in (self.top_hwnd, self.client_hwnd):
            if hwnd:
                self._hook(hwnd)

    # -- internals ---------------------------------------------------------
    def _hook(self, hwnd):
        if hwnd in self._procs:
            return
        orig = _get_ptr(hwnd, GWL_WNDPROC)
        if not orig:
            return

        def proc(h, msg, wparam, lparam):
            try:
                if msg == WM_NCCALCSIZE and wparam:
                    self._nccalc(h, lparam)
                    # Zero-thickness frame: the client area becomes the whole
                    # window, which is what keeps the drawn border the only
                    # border the user ever sees.
                    return 0
                if msg in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK,
                           WM_NCLBUTTONUP, WM_SYSCOMMAND):
                    try:
                        self.debug.append(("nc", "%04X" % msg, int(wparam),
                                           h == self.top_hwnd))
                        del self.debug[:-80]
                    except Exception:
                        pass
                if msg in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK) and wparam != HTCLIENT:
                    # The frame is zero thickness, so Windows never starts the
                    # move or size loop by itself: ask for it explicitly.  This
                    # is what makes the drawn title bar drag the window, the
                    # edges resize it and a double click maximize it.
                    # SC_MOVE / SC_SIZE only mean anything to the top level
                    # window: sending them to the child that was hit does
                    # nothing at all.
                    return _syscmd(self.top_hwnd or h,
                                   msg == WM_NCLBUTTONDBLCLK, wparam)
                if msg == WM_SYSCOMMAND and (int(wparam) & 0xFFF0) in (0xF010, 0xF000):
                    # Tk's wrapper procedure swallows SC_MOVE, so the move loop
                    # would never start if this were forwarded to it.
                    return _defproc(h, msg, wparam, lparam)
                if msg == WM_NCHITTEST:
                    res = self._nchittest(h, orig, wparam, lparam)
                    try:
                        lp = int(lparam) & 0xFFFFFFFF
                        self.debug.append((
                            "nh", h == self.top_hwnd,
                            ctypes.c_short(lp & 0xFFFF).value,
                            ctypes.c_short((lp >> 16) & 0xFFFF).value, res))
                        del self.debug[:-80]
                    except Exception:
                        pass
                    return res
                if msg == WM_GETMINMAXINFO:
                    # Tk (and DefWindowProc behind it) fill the structure in
                    # first; ours has to come after or it is overwritten.
                    _call(orig, h, msg, wparam, lparam)
                    self._minmax(h, lparam)
                    return 0
            except Exception as exc:
                try:
                    self.debug.append(("err", "%04X" % msg, repr(exc)))
                    del self.debug[:-80]
                except Exception:
                    pass
            return _call(orig, h, msg, wparam, lparam)

        self._procs[hwnd] = _WNDPROC(proc)
        self._orig[hwnd] = orig
        _set_ptr(hwnd, GWL_WNDPROC, ctypes.cast(self._procs[hwnd],
                                                ctypes.c_void_p).value)

    def _monitor(self, hwnd):
        """(monitor rect, work rect) for the window, in screen pixels."""
        try:
            mon = _user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            if _user32.GetMonitorInfoW(mon, ctypes.byref(info)):
                m, w = info.rcMonitor, info.rcWork
                return ((m.left, m.top, m.right, m.bottom),
                        (w.left, w.top, w.right, w.bottom))
        except Exception:
            pass
        return None, None

    def _nccalc(self, hwnd, lparam):
        """Keep a maximized window exactly on the work area.

        Windows sizes a maximized thick-framed window to the monitor plus one
        frame on every side, so that the frame itself lands off-screen.  With
        the frame cancelled that extra ring would be real content and the
        window would cover the taskbar, so while zoomed the client rectangle is
        pinned to the work area instead.
        """
        mon, work = self._monitor(hwnd)
        if not work:
            return
        try:
            params = ctypes.cast(lparam,
                                 ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
            r = params.rgrc[0]
            if not _user32.IsZoomed(wintypes.HWND(hwnd)):
                # IsZoomed is still false while the system is sizing the
                # window for the maximize.  The proposed client area is then
                # the whole monitor, which is the same thing seen from behind;
                # anything smaller is an ordinary resize and is left alone.
                if mon:
                    gets_all_w = r.right - r.left >= (mon[2] - mon[0]) - 4
                    gets_all_h = r.bottom - r.top >= (mon[3] - mon[1]) - 4
                    if not (gets_all_w and gets_all_h):
                        return
                else:
                    return
            self.debug.append((("top" if hwnd == self.top_hwnd else "child"),
                               bool(_user32.IsZoomed(wintypes.HWND(hwnd))),
                               (r.left, r.top, r.right, r.bottom), work))
            cel = params.rgrc[0]
            if os.environ.get("DX_TEST_NCCALC") == "1":
                work = (100, 100, 900, 700)
            cel.left, cel.top, cel.right, cel.bottom = work
            back = params.rgrc[0]
            self.debug.append(("set", (back.left, back.top, back.right, back.bottom)))
        except Exception:
            pass

    def _nchittest(self, hwnd, orig, wparam, lparam):
        # Ask Tk first: whatever it claims (a scrollbar's HTVSCROLL, a
        # message box's buttons) is not ours to take away.
        base = _call(orig, hwnd, WM_NCHITTEST, wparam, lparam)
        if base not in (HTCLIENT, 0):
            return base
        lp = int(lparam) & 0xFFFFFFFF
        px = ctypes.c_short(lp & 0xFFFF).value
        py = ctypes.c_short((lp >> 16) & 0xFFFF).value
        # Every rectangle below belongs to the top level window, never to the
        # window that happened to be asked.  Tk gives each widget its own HWND,
        # so measuring from a child's rect turns the edge of a label sitting
        # deep inside the window into a resize border, and its top row into a
        # drag handle -- which is exactly what "the resize zones are in the
        # wrong place" looked like from the outside.
        host = self.top_hwnd or hwnd
        rect = wintypes.RECT()
        _user32.GetWindowRect(wintypes.HWND(host), ctypes.byref(rect))
        if not (rect.left <= px < rect.right and rect.top <= py < rect.bottom):
            return base
        zoomed = bool(_user32.IsZoomed(wintypes.HWND(host)))
        if not zoomed:
            e = self.edge
            # The corners get a wider band than the edges: a click has to land
            # inside the rounded region before Windows sees it at all, and the
            # region's radius eats most of the little square at the tip.
            c = e * 1.7
            left = px - rect.left < e
            right = rect.right - px <= e
            top = py - rect.top < e
            bottom = rect.bottom - py <= e
            near_left = px - rect.left < c
            near_right = rect.right - px <= c
            near_top = py - rect.top < c
            near_bottom = rect.bottom - py <= c
            if (near_top and near_left) or (near_top and near_right) \
                    or (near_bottom and near_left) or (near_bottom and near_right):
                if near_top:
                    return HTTOPLEFT if near_left else HTTOPRIGHT
                return HTBOTTOMLEFT if near_left else HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            if top:
                return HTTOP
            if bottom:
                return HTBOTTOM
        # The drawn title bar drags the window with Windows' own move: that is
        # what brings Aero Snap, snap layouts and drag-off-to-restore along.
        if self.caption > 0:
            rel_y = py - rect.top
            rel_x = px - rect.left
            if 0 <= rel_y < self.caption and rel_x < (rect.right - rect.left
                                                      - self.skip):
                return HTCAPTION
            self.debug.append(("hit-miss", (px, py), (rel_x, rel_y),
                               self.caption, self.skip,
                               (rect.left, rect.top, rect.right, rect.bottom)))
        return base

    def _minmax(self, hwnd, lparam):
        mi = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO)).contents
        import os as _os
        import sys as _sys
        _dbg = _os.environ.get("DX_FX_DEBUG")
        mi.ptMinTrackSize.x = int(round(self.min_w * self.scale))
        mi.ptMinTrackSize.y = int(round(self.min_h * self.scale))
        try:
            mon = _user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            if _user32.GetMonitorInfoW(mon, ctypes.byref(info)):
                work = info.rcWork
                watch = info.rcMonitor
                mi.ptMaxPosition.x = work.left - watch.left
                mi.ptMaxPosition.y = work.top - watch.top
                mi.ptMaxSize.x = work.right - work.left
                mi.ptMaxSize.y = work.bottom - work.top
                mi.ptMaxTrackSize.x = work.right - work.left
                mi.ptMaxTrackSize.y = work.bottom - work.top
                if _dbg:
                    _sys.stderr.write("minmax work=%r size=%d,%d pos=%d,%d\n" % (
                        (work.left, work.top, work.right, work.bottom),
                        mi.ptMaxSize.x, mi.ptMaxSize.y,
                        mi.ptMaxPosition.x, mi.ptMaxPosition.y))
        except Exception:
            if _dbg:
                _sys.stderr.write("minmax failed %r\n" % (
                    _sys.exc_info()[1],))

    # -- api ---------------------------------------------------------------
    def _windows(self):
        """The wrapper plus every child window Tk put inside it.

        Tk draws everything into one child window, and that child is replaced
        whenever Tk rebuilds the toplevel, so it is enumerated fresh instead of
        being remembered from an earlier winfo_id().
        """
        top = _top_hwnd(self.win)
        if not top:
            return []
        out = [top]
        try:
            def _each(hwnd, _lp):
                out.append(int(hwnd))
                return True
            cb = _ENUMPROC(_each)
            _user32.EnumChildWindows(wintypes.HWND(top), cb, 0)
        except Exception:
            pass
        if not out:
            child = _client_hwnd(self.win)
            if child:
                out.append(child)
        return out

    def rehook(self):
        wins = self._windows()
        top = wins[0] if wins else 0
        child = _client_hwnd(self.win)
        if self._procs and set(wins) == set(self._procs) and top != child:
            return
        self.close()
        self.top_hwnd = top
        self.client_hwnd = child if child in wins else top
        for hwnd in wins:
            self._hook(hwnd)
        if top == child:
            # Still parked on the placeholder: Tk has not made its wrapper yet,
            # so look again shortly instead of staying on the wrong window.
            try:
                self.win.after(250, self.rehook)
            except Exception:
                pass

    def set_caption(self, height, skip=None):
        self.caption = float(height) * self.scale
        if skip is not None:
            self.skip = float(skip) * self.scale

    def close(self):
        for hwnd, proc in list(self._procs.items()):
            try:
                orig = self._orig.get(hwnd)
                if orig:
                    _set_ptr(hwnd, GWL_WNDPROC, orig)
            except Exception:
                pass
        self._procs.clear()
        self._orig.clear()


def apply_style(win):
    """Set the style that makes Windows treat this as a real window.

    Tk keeps a frameless toplevel as WS_POPUP and puts that back whenever it
    maps the window, so this is also called again once the window is up.
    """
    hwnd = _top_hwnd(win) if not isinstance(win, int) else int(win)
    if not hwnd:
        return
    style = _get_style(hwnd)
    style &= ~WS_POPUP
    style |= (WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX | WS_SYSMENU)
    _set_style(hwnd, style)
    _user32.SetWindowPos(wintypes.HWND(hwnd), ctypes.c_void_p(None), 0, 0, 0, 0,
                         0x0027)


def rehook(win):
    """Re-apply only the subclass, cheap enough to poll.

    Tk recreates its child window on some attribute changes, which silently
    leaves the subclass on a dead handle; this puts it back on the live one.
    """
    fx = getattr(win, "_fx_frame", None)
    if fx:
        fx.rehook()


def refresh(win):
    """Re-apply the style after Tk has mapped or remapped the window.

    Mapping is also when the wrapper window shows up, so the subclass has to
    move from the placeholder handle to the real one here.
    """
    apply_style(win)
    rehook(win)


def install(win, min_size=(880, 600), caption_height=44, caption_skip=210):
    """Give a frameless Tk window the parts of a real window it lost."""
    if os.name != "nt" or not _user32:
        return None
    if getattr(win, "_fx_frame", None):
        try:
            win._fx_frame.close()
        except Exception:
            pass
        win._fx_frame = None
    hwnd = _top_hwnd(win)
    if not hwnd:
        return None
    # A thick frame is what tells Windows this window can be resized and
    # maximized; NCCALCSIZE above makes sure the frame itself draws nothing.
    try:
        apply_style(win)
    except Exception:
        return None
    frame = Frame(win, min_size, caption_height, caption_skip)
    win._fx_frame = frame
    # Before the window is mapped Tk has no wrapper yet, so the subclass is
    # parked on the placeholder and moved by this refresh once it exists.
    try:
        win.after(150, lambda: refresh(win) if win.winfo_exists() else None)
    except Exception:
        pass
    return frame
