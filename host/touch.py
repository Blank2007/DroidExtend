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

# 1.6.0: z grew from 16 to 32 bits.  The phone used to pack dx into the low
# half and dy into the high half of a uint16, so every vertical scroll arrived
# with dy == 0 -- scrolling vertically never worked at all.  Both ends ship
# together, so the record is 9 bytes now and the high half means what it says.
REC = struct.Struct("<BHHI")
DOWN, MOVE, UP, SCROLL, RCLICK = 1, 2, 3, 4, 5
# 6 is a notice rather than an input event: the phone telling the PC that
# something happened to it.  It rides the same tunnel because that tunnel is
# already open and already ordered against touch.
NOTICE = 6

SCREEN_OFF, SCREEN_ON, APP_GONE = 1, 2, 3

# There is deliberately no "went to the background" notice: Android reports that
# and "the screen went off" through the same onPause/onStop pair, so the phone
# cannot tell them apart and the PC would just get two lines saying one thing.
NOTICE_TEXT = {
    SCREEN_OFF: "手机熄屏了 —— 画面会停在最后一帧，点亮手机即可恢复",
    SCREEN_ON: "手机亮屏了",
    APP_GONE: "手机上的 DroidExtend 被系统关闭了 —— 投屏已中断",
}

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

# If the left button stays down this long with no movement at all, the phone
# died mid-gesture.  Windows would otherwise sit in a modal drag loop with the
# button held and quietly swallow everything we inject afterwards.
STUCK_AFTER = 30.0

# A double click only counts as one when the second press lands inside a small
# rectangle around the first -- SM_CXDOUBLECLK, four pixels by default.  A mouse
# fits in that box; a fingertip never does.  Two taps at the same spot on this
# phone land tens of desktop pixels apart, so Windows dutifully reports two
# separate single clicks and the file never opens.  So when the second press
# arrives close enough in time and space, snap it onto the first one.
#
# 0.03 of the screen is about 58 px of a 1920-wide desktop -- far more than the
# jitter of a finger hitting the same icon twice, far less than the distance
# between two things anyone would click in half a second.  Positions arrive
# normalised, so this is resolution independent.
DOUBLE_CLICK_SLOP = 0.03
DOUBLE_CLICK_FALLBACK = 0.5

_DCLICK_TIME = None


def double_click_time():
    """Seconds allowed between the two presses, straight from Windows.

    Cached: it is a user setting, not something that changes while we run.
    """
    global _DCLICK_TIME
    if _DCLICK_TIME is None:
        _DCLICK_TIME = DOUBLE_CLICK_FALLBACK
        try:
            ms = int(ctypes.windll.user32.GetDoubleClickTime())
            if ms > 0:
                _DCLICK_TIME = max(0.1, min(1.0, ms / 1000.0))
        except Exception:
            pass
    return _DCLICK_TIME


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


def physical_desktop():
    """Virtual desktop bounds in *physical* pixels, from the displays themselves.

    GetSystemMetrics answers in logical pixels for a process that is not
    per-monitor DPI aware -- on this machine it reports 2048x2501 for a desktop
    that is really 3200x3736.  Feeding those numbers to SendInput while the
    target rectangle comes from physical display geometry puts every click in
    the wrong place (or hard against the clamp at the screen edge).  Enumeration
    settings are never virtualised, so they are the ones to trust.
    """
    try:
        import win_display as wd
        mons = wd.live_monitors()
    except Exception:
        mons = None
    if mons:
        left = min(m["left"] for m in mons)
        top = min(m["top"] for m in mons)
        right = max(m["left"] + m["width"] for m in mons)
        bottom = max(m["top"] + m["height"] for m in mons)
        if right > left and bottom > top:
            return {"left": left, "top": top,
                    "width": right - left, "height": bottom - top}
    return virtual_desktop()


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
    """Maps normalised picture coordinates onto one monitor and clicks there.

    The rectangle is cached -- EnumDisplaySettingsEx is far too slow to call for
    every touch event -- but a *stale* rectangle is exactly what makes every
    touch land on the wrong screen once the user drags the displays around in
    Windows' 显示设置.  So the cache has a short TTL and re-reads the display's
    real position behind the scenes; a move heals itself within a second
    without anyone having to tell us it happened.
    """

    TTL = 1.0

    def __init__(self, rect=None, device=None, log=None):
        self.rect = dict(rect) if rect else None
        self.device = device
        self.log = log or (lambda m: None)
        self._down = False
        self._down_at = 0.0
        self._wheel = 0.0
        self._hwheel = 0.0
        self._checked = 0.0
        self._desk = physical_desktop()
        self.frozen = 0
        self.rejected = 0

    def set_rect(self, rect):
        self.rect = dict(rect) if rect else None
        self._checked = time.monotonic()

    def set_device(self, device):
        """Point the injector at a display *name* so it can follow it live."""
        self.device = device
        self._checked = 0.0

    def _sync(self):
        """Re-read the desktop and the target display's real rectangle."""
        now = time.monotonic()
        if now - self._checked < self.TTL:
            return
        self._checked = now
        desk = physical_desktop()
        if (desk["left"] != self._desk.get("left")
                or desk["top"] != self._desk.get("top")
                or desk["width"] != self._desk.get("width")
                or desk["height"] != self._desk.get("height")):
            self._desk = desk
        if not self.device:
            return
        try:
            import win_display as wd
            r = wd.desktop_rect(self.device)
        except Exception:
            return
        if not r:
            return
        old = self.rect or {}
        if (r["left"] != old.get("left") or r["top"] != old.get("top")
                or r["width"] != old.get("width")
                or r["height"] != old.get("height")):
            self.rect = dict(r)
            self.log("触摸坐标已跟随显示器移动到 %d,%d %dx%d"
                     % (r["left"], r["top"], r["width"], r["height"]))

    def release(self, why=""):
        """Drop any button we are still holding and forget the gesture state.

        A finger that lifts while the phone app is dying, or while the window it
        was dragging is being destroyed, never sends its UP.  Windows then sits
        in a modal drag loop with the left button down, and every later injection
        is swallowed -- the stream keeps running but the screen stops answering
        to touch.  Releasing on disconnect is what resets that state.
        """
        if not self._down:
            self._wheel = self._hwheel = 0.0
            return False
        self._down = False
        self._wheel = self._hwheel = 0.0
        try:
            _send(MOUSEEVENTF_LEFTUP)
        except Exception:
            pass
        self.log("触摸手势已被重置（%s）" % (why or "连接结束"))
        return True

    def held_for(self):
        """Seconds the left button has been down, or 0 when it is up."""
        return (time.monotonic() - self._down_at) if self._down else 0.0

    def _screen(self, nx, ny):
        self._sync()
        r = self.rect or virtual_desktop()
        x = r["left"] + nx / 65535.0 * max(r["width"] - 1, 1)
        y = r["top"] + ny / 65535.0 * max(r["height"] - 1, 1)
        return x, y

    def _move_to(self, nx, ny):
        x, y = self._screen(nx, ny)
        d = self._desk
        ax = int(round((x - d["left"]) * 65535.0 / max(d["width"] - 1, 1)))
        ay = int(round((y - d["top"]) * 65535.0 / max(d["height"] - 1, 1)))
        ax = max(0, min(65535, ax))
        ay = max(0, min(65535, ay))
        n = _send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                  | MOUSEEVENTF_VIRTUALDESK, ax, ay)
        if not n:
            # UIPI: a lower-integrity process cannot inject into a higher one,
            # so touches do nothing over an elevated window and SendInput says
            # so by returning 0.  Worth one line in the log, not a flood.
            self.rejected += 1
            if self.rejected == 1:
                self.log("系统拒绝了输入注入（SendInput 返回 0）："
                         "被控窗口可能以管理员身份运行，试试用管理员身份启动本程序")
        else:
            self.rejected = 0
        self._check_frozen(x, y)
        return n

    def _check_frozen(self, x, y):
        """Notice when Windows is quietly swallowing everything we send.

        A button left down by a phone that vanished puts Windows into a modal
        drag loop: SendInput keeps reporting success, the cursor simply never
        moves.  Eight frozen moves in a row is not a coincidence -- let go, and
        the next event gets through.
        """
        try:
            pt = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(pt)):
                return
        except Exception:
            return
        if abs(pt.x - int(x)) <= 2 and abs(pt.y - int(y)) <= 2:
            self.frozen = 0
            return
        self.frozen += 1
        if self.frozen >= 8:
            self.frozen = 0
            if self._down:
                self.release("输入被系统吞掉，可能卡在拖动状态")

    def down(self, nx, ny):
        self._move_to(nx, ny)
        self._down = True
        self._down_at = time.monotonic()
        return _send(MOUSEEVENTF_LEFTDOWN)

    def move(self, nx, ny):
        if self._down:
            self._down_at = time.monotonic()   # a live finger keeps refreshing
        return self._move_to(nx, ny)

    def up(self, nx, ny):
        self._move_to(nx, ny)
        self._down = False
        self._wheel = self._hwheel = 0.0
        return _send(MOUSEEVENTF_LEFTUP)

    def right_click(self, nx, ny):
        self._move_to(nx, ny)
        _send(MOUSEEVENTF_RIGHTDOWN)
        time.sleep(0.02)
        return _send(MOUSEEVENTF_RIGHTUP)

    def scroll(self, dx, dy):
        """dx/dy are the phone's finger travel in video pixels."""
        held = self.held_for()
        if 0 < held < STUCK_AFTER:        # a drag, not a scroll
            return 0
        if held:
            self.release("按住超过 %.0f 秒没有动作" % STUCK_AFTER)
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

    def __init__(self, rect=None, device=None, port=INPUT_PORT, log=None,
                 on_notice=None):
        self.port = port
        self.log = log or (lambda m: None)
        self.on_notice = on_notice
        self.injector = Injector(rect, device, log)
        self.sock = None
        self._stop = threading.Event()
        self._threads = []
        self.stats = {"events": 0, "clients": 0, "notices": 0,
                      "received": 0, "merged": 0, "double": 0}
        # Double-click assembly, all three guarded by nothing because only the
        # reading thread ever touches them.
        #   _last_click -- (monotonic time, x, y) of the last complete click, or
        #                  None when the last thing that happened was not one.
        #   _press      -- None, "clean" (down, no move yet) or "moved".
        #   _snap       -- where the current press was snapped to, if it was.
        self._last_click = None
        self._press = None
        self._snap = None

    def _forget_click(self):
        """The next press must not pair with anything that came before."""
        self._last_click = None
        self._snap = None

    # ------------------------------------------------------------- targeting
    def set_rect(self, rect):
        self.injector.set_rect(rect)

    def set_device(self, device):
        """Follow this display even if Windows moves it under us."""
        self.injector.set_device(device)

    def release(self, why=""):
        # Nothing survives a release: a click that happened before the button
        # was pulled out from under it must not pair with the next press.
        self._forget_click()
        self._press = None
        return self.injector.release(why)

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
        w = threading.Thread(target=self._watchdog, name="touch-watchdog", daemon=True)
        w.start()
        self._threads.append(w)
        self.log("触摸通道监听 127.0.0.1:%d" % self.port)

    def _watchdog(self):
        """Un-stick a button the phone left down when it vanished mid-gesture."""
        while not self._stop.wait(2.0):
            if self.injector.held_for() > STUCK_AFTER:
                self.injector.release("手机长时间没有动作")

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
                recs = []
                while len(buf) >= REC.size:
                    recs.append(REC.unpack_from(buf, 0))
                    buf = buf[REC.size:]
                if recs:
                    self._apply_all(recs)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
            # The phone may have gone away between DOWN and UP -- Android kills
            # the app, the screen locks, the window being dragged is destroyed.
            # Left down, Windows keeps eating input in a modal drag loop.
            self.release("手机断开")
            self.log("触摸通道已断开")

    def _apply_all(self, recs):
        """Apply one batch, collapsing each run of MOVE to its newest record.

        The phone now sends a whole gesture in one write, and even before that
        a burst could arrive in a single recv().  In the middle of a drag the
        intermediate positions are stale the moment they land -- the cursor
        only has to be where the finger is now -- and applying them anyway
        means one SendInput per stale position while the next batch is already
        waiting.  Down, up, scroll, right click and notice are events: every
        one of those is applied, in order.
        """
        self.stats["received"] += len(recs)
        keep = []
        for rec in recs:
            if rec[0] == MOVE and keep and keep[-1][0] == MOVE:
                keep[-1] = rec
            else:
                keep.append(rec)
        self.stats["merged"] += len(recs) - len(keep)
        for action, x, y, z in keep:
            self._apply(action, x, y, z)

    def _down(self, x, y):
        """Press, snapping onto the previous click when this is the second half
        of a double click.  A fingertip cannot hit the same pixel twice, and
        Windows will not call it a double click unless it does."""
        self._press = "clean"
        self._snap = None
        prev = self._last_click
        if prev is not None:
            dt = time.monotonic() - prev[0]
            if (dt <= double_click_time()
                    and abs(prev[1] - x) <= DOUBLE_CLICK_SLOP
                    and abs(prev[2] - y) <= DOUBLE_CLICK_SLOP):
                self._snap = (prev[1], prev[2])
                # Not counted here: a press that turns into a drag is not half
                # of a double click, and only the release tells us which it was.
        if self._snap is not None:
            x, y = self._snap
        self.injector.down(x, y)

    def _move(self, x, y):
        # A press that moves is a drag, so it is not half of a click and it
        # breaks any pairing with one.
        if self._press is not None:
            self._press = "moved"
            self._snap = None
        self._last_click = None
        self.injector.move(x, y)

    def _up(self, x, y):
        if self._snap is not None:
            # It stayed snapped all the way to the release, so this really was
            # the second click of a pair.  A MOVE would have cleared it.
            x, y = self._snap
            self.stats["double"] += 1
        if self._press == "clean":
            # A press and release with nothing in between: that was a click, and
            # a third tap has to be able to chain onto this one's position.
            self._last_click = (time.monotonic(), x, y)
        else:
            self._last_click = None
        self._press = None
        self._snap = None
        self.injector.up(x, y)

    def _apply(self, action, x, y, z):
        self.stats["events"] += 1
        try:
            if action == DOWN:
                self._down(x, y)
            elif action == MOVE:
                self._move(x, y)
            elif action == UP:
                self._up(x, y)
            elif action == RCLICK:
                self._forget_click()
                self.injector.right_click(x, y)
            elif action == NOTICE:
                self._forget_click()
                self._notice(x)
            elif action == SCROLL:
                self._forget_click()
                # z low half = signed dx, high half = signed dy
                dx = struct.unpack("<h", struct.pack("<H", z & 0xFFFF))[0]
                dy = struct.unpack("<h", struct.pack("<H", (z >> 16) & 0xFFFF))[0]
                self.injector.scroll(dx, dy)
        except Exception as e:
            self.log("注入输入失败: %s" % e)

    def _notice(self, code):
        """#8: the phone says it went away, so the PC can say so too.

        Without this the stream simply stops and the user is left looking at a
        frozen last frame with no idea whether the cable, the phone or this
        program is at fault.
        """
        self.stats["notices"] += 1
        msg = NOTICE_TEXT.get(code, "手机发来通知 %d" % code)
        self.log(msg)
        if self.on_notice is not None:
            try:
                self.on_notice(code, msg)
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        if self.injector.held_for():
            self.injector.release("触摸服务已停止")
        s, self.sock = self.sock, None
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
