"""One streaming session: display -> capture -> encode -> USB -> phone.

The flow, in order:

  1. apply_display()   put the virtual display at the requested mode and place
                       it beside the primary monitor (extend mode), or pick an
                       existing monitor to copy (mirror mode).
  2. prepare_device()  adb reverse so the phone's 127.0.0.1:<port> lands on us,
                       then install/launch the APK.
  3. start_stream()    spawn ffmpeg on the rectangle and push access units into
                       the server as they come out.

Stop() reverses all of it, including removing the extra desktop area.
"""
import threading
import time

import win_display as wd
from capture import Capture
from server import StreamServer, HOST_PORT
from touch import TouchServer, INPUT_PORT
from paths import apk_path

DEFAULTS = {
    "width": 1920,
    "height": 1080,
    "hz": 60,
    "fps": 60,
    # Measured links on this machine: 13.7 Mbps with a charge-grade cable that
    # only negotiated USB 1.1 Full Speed, 150 Mbps after swapping it.  Under VBR
    # this number is a ceiling rather than a target -- a still desktop costs
    # 1-2 Mbps and only motion can borrow the rest -- so 40 is comfortable on a
    # healthy cable.  On a slow one, run 测一下数据线速度 and the UI trims it.
    "bitrate_mbps": 40,
    "rate_control": "vbr",   # vbr (quality target, bitrate = cap) | cbr
    "quality": 20,           # lower is better; NVENC -cq / x264 -crf
    "encoder": "h264_nvenc",
    "preset": "p4",
    "backend": "auto",       # auto | ddagrab | gdigrab
    "draw_mouse": False,
    "spatial_aq": True,
    "gop": 0,                # 0 = 2 seconds
    "mode": "extend",        # extend | mirror
    "position": "right",     # right | left | above | below
    "monitor": "",           # mirror mode: adapter name to copy
    "install_apk": True,
    "auto_launch": True,
    "touch": True,           # phone touches -> real mouse input
    "match_phone": True,     # size the virtual screen like the phone's own panel
}

DISP_CHANGE = {
    0: "SUCCESSFUL", 1: "RESTART", -1: "FAILED", -2: "BADMODE",
    -3: "NOTUPDATED", -4: "BADFLAGS", -5: "BADDUALVIEW", -6: "BADMODE",
}


class VddMissing(RuntimeError):
    pass


class Session(object):
    def __init__(self, log=None):
        self.log = log or (lambda m: None)
        self.cfg = dict(DEFAULTS)
        self.adb = None
        self.serial = None
        self.server = None
        self.capture = None
        self.touch = None
        self.target = None        # adapter name we capture
        self.rect = None          # capture rectangle
        self._pump = None
        self._stop = threading.Event()
        self.stats = {"fps": 0.0, "mbps": 0.0, "dropped": 0, "sent": 0}
        self._last = time.time()
        self._last_frames = 0
        self._last_bytes = 0
        self._display_applied = None
        self._prev_mode = None

    # ------------------------------------------------------------------ state
    @property
    def running(self):
        return self.capture is not None and self.capture.alive()

    def describe(self):
        r = self.rect or {}
        return "%s  %dx%d@%d  %s" % (
            self.cfg["mode"], r.get("width", 0), r.get("height", 0),
            self.cfg.get("fps", 0), self.target or "-")

    # ------------------------------------------------------------- 1. display
    def _position_for(self, primary, w, h):
        pos = self.cfg.get("position", "right")
        gap = 0
        if pos == "left":
            return primary["left"] - w - gap, primary["top"]
        if pos == "above":
            return primary["left"], primary["top"] - h - gap
        if pos == "below":
            return primary["left"], primary["bottom"] + gap
        return primary["right"] + gap, primary["top"]

    def _match_phone(self, name, w, h, hz):
        """Prefer the phone's own panel size over whatever the UI asked for.

        A 3200x2136 phone letterboxed into 1920x1080 throws away most of the
        screen; matching the panel means the picture reaches all four edges.
        The driver can only offer what is in its mode table, so when the exact
        size is missing we take the closest aspect ratio and append the missing
        size to vdd_settings.xml -- the driver reads that file while its device
        node starts, so it goes live on the next reboot or driver reinstall.
        """
        try:
            if self.adb is None:
                self.find_device()
            size = self.adb.physical_size(self.serial) if self.adb else None
        except Exception as e:
            self.log("  读手机分辨率失败: %s" % e)
            size = None
        if not size:
            self.log("  读不到手机分辨率，按 %dx%d 处理" % (w, h))
            return w, h, hz

        # The app is locked to landscape, so the panel's long side is its width.
        pw, ph = max(size), min(size)
        self.log("  手机分辨率 %dx%d" % (pw, ph))

        best = wd.best_mode(name, pw, ph, hz)
        if not best:
            self.log("  驱动模式表读不到，按 %dx%d 处理" % (w, h))
            return w, h, hz
        bw, bh, bhz, exact = best
        if exact:
            self.log("  虚拟屏按手机分辨率 %dx%d@%d 撑满" % (bw, bh, bhz))
            return bw, bh, bhz

        self.log("  驱动模式表里没有 %dx%d，先用最接近的 %dx%d@%d（会有黑边）"
                 % (pw, ph, bw, bh, bhz))
        try:
            import vdd as vd
            added = vd.add_resolution(pw, ph, hz)
        except Exception:
            added = None
        if added:
            self.log("  已把 %dx%d 写进驱动模式表：点「安装虚拟显示器驱动」重装一次"
                     "（或重启电脑）就能完全撑满" % (pw, ph))
        elif added is False:
            # The file already lists it; the driver still does not offer it,
            # because it only reads that file while its device node starts.
            self.log("  驱动暂时给不出 %dx%d（文件里已有，但驱动要重装一次或重启"
                     "电脑才会读到）" % (pw, ph))
        else:
            self.log("  想完全撑满：点「安装虚拟显示器驱动」重装一次驱动")
        return bw, bh, bhz

    def apply_display(self):
        """Make sure a second screen exists at the requested size. Returns rect."""
        import vdd as vd

        if self.cfg["mode"] == "mirror":
            mons = wd.live_monitors()
            if not mons:
                raise RuntimeError("找不到任何活动的显示器")
            want = self.cfg.get("monitor")
            mon = next((m for m in mons if m["name"] == want), None)
            if mon is None:
                mon = next((m for m in mons if m["primary"]), mons[0])
            self.target = mon["name"]
            self.rect = dict(mon)
            self._display_applied = None
            self.log("镜像模式：采集 %s (%s) %dx%d" % (
                mon["name"], mon["monitor"] or mon["desc"], mon["width"], mon["height"]))
            return self.rect

        st = vd.status()
        if st["state"] != "ready":
            raise VddMissing(vd.summary_line(st))
        a = st["adapter"]
        name = a["name"]
        self.log("虚拟显示器: %s (%s)" % (name, a["desc"]))
        self._display_applied = name

        prev = wd.current_mode(name)
        if prev:
            self._prev_mode = dict(prev)

        # Put it back on the desktop.  Windows locks the display configuration
        # for a moment around every topology change, and while it is locked
        # ChangeDisplaySettingsEx returns DISP_CHANGE_FAILED (-1),
        # SetDisplayConfig returns ERROR_INVALID_PARAMETER (5), and even
        # EnumDisplaySettingsEx refuses to answer -- which surfaces as
        # "OSError: EnumDisplaySettingsEx failed for \\.\DISPLAYxx" from
        # set_mode().  Retrying clears all three.  Doing it once made
        # 开始投屏 fail with 出错 the second time the app was opened.
        for attempt in range(6):
            if wd.desktop_rect(name):
                break
            rc = wd.attach(name, True)
            self.log("  激活显示器 attach rc=%s%s"
                     % (rc, "" if attempt == 0 else " (重试 %d)" % attempt))
            time.sleep(0.5)
            if wd.desktop_rect(name):
                break
            # A display a previous run took out of the topology with
            # SetDisplayConfig needs the topology switch, not just a mode
            # change, to come back.
            rc2 = wd.extend_topology()
            self.log("  attach 未生效，改用扩展拓扑 rc=%s" % rc2)
            time.sleep(0.7)
        if not wd.desktop_rect(name):
            raise RuntimeError("虚拟显示器 %s 无法激活（未上桌面）" % name)

        mons = wd.live_monitors()
        primary = next((m for m in mons if m["primary"]), mons[0] if mons else None)
        if primary is None:
            raise RuntimeError("找不到主显示器")

        w = int(self.cfg["width"])
        h = int(self.cfg["height"])
        hz = int(self.cfg["hz"])
        if self.cfg.get("match_phone", True):
            w, h, hz = self._match_phone(name, w, h, hz)
        x, y = self._position_for(primary, w, h)

        modes = wd.available_modes(name)
        if modes and (w, h, hz) not in modes:
            near = [m for m in modes if m[0] == w and m[1] == h]
            if near:
                best = min(near, key=lambda m: abs(m[2] - hz))
                self.log("  %dx%d@%d 不在驱动模式表里，改用 @%d" % (w, h, hz, best[2]))
                hz = best[2]
            else:
                self.log("  警告: 驱动没有 %dx%d 模式，仍尝试设置" % (w, h))
                if modes:
                    self.log("  驱动模式: " + ", ".join("%dx%d@%d" % m for m in modes[:12]))

        # Same display-config lock can reject the mode change, so retry it too.
        rc = None
        for attempt in range(4):
            try:
                rc = wd.set_mode(name, w, h, hz, x, y)
            except OSError as e:
                rc = "OSError: %s" % e
            self.log("  ChangeDisplaySettingsEx -> %s%s"
                     % (DISP_CHANGE.get(rc, rc),
                        "" if attempt == 0 else " (重试 %d)" % attempt))
            time.sleep(1.0)
            if wd.desktop_rect(name):
                break
            time.sleep(0.5)

        rect = wd.desktop_rect(name)
        if not rect:
            raise RuntimeError("虚拟显示器设置后仍然不可用")
        if rect["width"] != w or rect["height"] != h:
            self.log("  实际模式 %dx%d@%d（请求 %dx%d@%d）" % (
                rect["width"], rect["height"], rect["hz"], w, h, hz))
        self.target = name
        self.rect = rect
        self.log("拓展屏就绪: %s -> %dx%d@%d at (%d,%d)" % (
            name, rect["width"], rect["height"], rect["hz"], rect["left"], rect["top"]))
        # Windows paints the *same* wallpaper on a newly attached monitor and
        # (by default) draws a taskbar on it too, so an empty extension screen
        # looks exactly like the main one at a glance.  Say where it is, or the
        # first thing anyone does is assume the stream is the main screen.
        if self.cfg.get("mode") != "mirror":
            self.log("  手机上是桌面 (%d,%d) 往右 %d 像素那块屏；把窗口拖过去就看得见"
                     % (rect["left"], rect["top"], rect["width"]))
        return rect

    # -------------------------------------------------------------- 2. device
    def find_device(self):
        from adb import Adb
        self.adb = Adb()
        rows = self.adb.devices()
        ready = [r for r in rows if r["state"] == "device"]
        if not ready:
            states = ", ".join("%s=%s" % (r["serial"], r["state"]) for r in rows) or "无"
            raise RuntimeError("没有可用的安卓设备（adb devices: %s）" % states)
        self.serial = ready[0]["serial"]
        self.log("设备: %s" % ready[0]["label"])
        return ready[0]

    def prepare_device(self, port=HOST_PORT):
        if self.adb is None:
            self.find_device()
        s = self.serial
        self.adb.reverse_remove_all(s)
        rc, out = self.adb.reverse(s, port, port)
        self.log("adb reverse tcp:%d -> tcp:%d  (rc=%s)" % (port, port, rc))
        if rc != 0:
            self.log("  reverse 失败: " + out.strip())

        # Second tunnel, same cable: the phone's touches come back on this one
        # so they never queue behind a video keyframe.
        rc2, out2 = self.adb.reverse(s, INPUT_PORT, INPUT_PORT)
        self.log("adb reverse tcp:%d -> tcp:%d  (rc=%s)" % (INPUT_PORT, INPUT_PORT, rc2))
        if rc2 != 0:
            self.log("  触摸通道 reverse 失败: " + out2.strip())

        if self.cfg.get("install_apk"):
            apk = apk_path()
            if self.adb.is_installed(s):
                self.log("APK 已安装：跳过安装（改代码后请重新构建并勾选“重新安装”）")
            else:
                self.log("正在安装 %s …" % apk)
                ok, out = self.adb.install(s, apk)
                self.log("  安装%s: %s" % ("成功" if ok else "失败", out.strip().splitlines()[-1] if out.strip() else ""))
                if not ok:
                    raise RuntimeError("APK 安装失败：%s" % out.strip())

        if self.cfg.get("auto_launch"):
            self.adb.force_stop(s)
            time.sleep(0.3)
            rc, out = self.adb.start_app(s)
            self.log("已启动手机端 App (rc=%s)" % rc)
        return True

    # -------------------------------------------------------------- 3. stream
    def start_stream(self, port=HOST_PORT):
        self.server = StreamServer(port=port, log=self.log, on_client=self._on_client)
        self.server.start()
        self.capture = Capture(self.cfg, self.rect, log=self.log)
        backend = self.capture.start()
        self.log("编码器 %s / 采集 %s" % (self.cfg["encoder"], backend))
        if self.cfg.get("touch", True):
            self.touch = TouchServer(self.rect, log=self.log)
            self.touch.start()
            self.log("触摸控制已开启（点按/拖动/双指滚动/双指轻点=右键）")
        self._stop.clear()
        self._pump = threading.Thread(target=self._pump_loop, name="pump", daemon=True)
        self._pump.start()
        self._last = time.time()
        return backend

    def _quality_note(self):
        """One short line describing the picture, for the phone's overlay.

        The phone can measure bitrate, fps and latency on its own, but it has
        no way to know which encoder or quality the PC picked -- and "2400 kbps"
        means very different things at Q18 versus Q30.
        """
        enc = str(self.cfg.get("encoder", ""))
        name = {
            "h264_nvenc": "NVENC", "hevc_nvenc": "NVENC HEVC",
            "h264_amf": "AMF", "hevc_amf": "AMF HEVC",
            "libx264": "x264", "libx265": "x265",
        }.get(enc, enc or "?")
        rc = str(self.cfg.get("rate_control", "vbr")).upper()
        note = "%s %s" % (name, rc)
        if rc == "VBR":
            note += " Q%s" % self.cfg.get("quality", 20)
        note += " · %sMbps" % self.cfg.get("bitrate_mbps", 40)
        if self.capture is not None and getattr(self.capture, "backend_used", None):
            note += " · " + str(self.capture.backend_used)
        return note

    def _on_client(self, addr):
        self.log("客户端接入，发送握手")
        if self.server and self.rect:
            self.server.send_header(self.rect["width"], self.rect["height"],
                                    self.cfg["fps"], self._quality_note())

    def _pump_loop(self):
        while not self._stop.is_set():
            units = self.capture.get_units(0.4) if self.capture else []
            if units and self.server:
                self.server.put(units)
            self._tick()

    def _tick(self):
        now = time.time()
        dt = now - self._last
        if dt < 0.75:
            return
        if self.capture:
            f = self.capture.stats["frames"]
            b = self.capture.stats["bytes"]
            self.stats["fps"] = (f - self._last_frames) / dt
            self.stats["mbps"] = (b - self._last_bytes) * 8 / dt / 1e6
            self._last_frames, self._last_bytes = f, b
        if self.server:
            self.stats["dropped"] = self.server.stats["dropped"]
            self.stats["sent"] = self.server.stats["sent"]
        self._last = now

    # ------------------------------------------------------------------ stop
    def attached_display(self):
        """The virtual display this session put on the desktop, if any."""
        return self._display_applied

    def stop(self, restore_display=True):
        self._stop.set()
        if self.touch:
            try:
                self.touch.stop()
            except Exception:
                pass
            self.touch = None
        if self.capture:
            try:
                self.capture.stop()
            except Exception:
                pass
            self.capture = None
        if self.server:
            try:
                self.server.stop()
            except Exception:
                pass
            self.server = None
        if self.adb and self.serial:
            try:
                self.adb.force_stop(self.serial)
            except Exception:
                pass
        if restore_display and self._display_applied:
            # Take the virtual display back OFF the desktop.  This used to
            # restore its previous *mode* instead, which left a second screen
            # attached forever after pressing 停止 -- Windows still listed it
            # and the desktop stayed 4480 px wide.  The display stays
            # available to the driver; it just is not part of the desktop any
            # more, and apply_display() switches it back on next time.
            name = self._display_applied
            self._display_applied = None
            try:
                if wd.detach(name):
                    self.log("已移除虚拟显示器 %s，桌面恢复为 %s"
                             % (name, wd.desktop_bounds()))
                else:
                    self.log("移除虚拟显示器失败（%s 仍在系统中）" % name)
            except Exception as e:
                self.log("移除虚拟显示器失败: %s" % e)
        self.log("会话已停止")
