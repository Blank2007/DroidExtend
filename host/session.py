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

import i18n
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
    "position": "right",     # right | left | above | below | custom
    "pos_x": 0,              # position="custom": left edge, in primary's coords
    "pos_y": 0,              # position="custom": top edge,  in primary's coords
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
    def __init__(self, log=None, on_notice=None):
        self.log = log or (lambda m: None)
        self.on_notice = on_notice
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
        # -- capture watchdog ----------------------------------------------
        # ddagrab dies whenever Windows invalidates the desktop duplication:
        # a display-mode change, or a UAC prompt switching to the secure
        # desktop.  ffmpeg exits, the reader thread ends, and the phone keeps
        # showing the last frame forever.  The watchdog restarts the capture
        # instead of leaving the user with a frozen picture.
        self._last_recover = 0.0
        self._recover_tries = 0
        self.fatal = None

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
        if pos == "custom":
            # Absolute coordinates in the primary screen's space, so the screen
            # can sit somewhere other than the four edges -- including partially
            # overlapping the primary one, which is what "not only up/down/left/
            # right" asks for.
            try:
                return int(self.cfg.get("pos_x", 0)), int(self.cfg.get("pos_y", 0))
            except (TypeError, ValueError):
                return primary["right"] + gap, primary["top"]
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

    def _vdd_off_mode(self):
        """How thoroughly to put the virtual display away when we are not using it.

        "deep"    -- disable the driver's device node when the stream stops, so the
                     screen does not exist at all while it is not in use: nothing
                     at boot, nothing for remote-desktop software to list, and
                     Windows keeps a disabled device disabled across reboots.
                     Costs one UAC prompt at each switch.
        "desktop" -- leave the driver alone and just keep the screen off the
                     desktop.  No UAC, but the screen still exists.

        Not `<count>0</count>`: measured 2026-10-16, MttVDD treats 0 as an
        invalid count and falls back to a single monitor, so the screen stays.
        Disabling the device node is what actually makes it disappear.
        """
        return (self.cfg.get("vdd_off_mode") or "deep").lower()

    def _ensure_vdd_on(self):
        """上次用完把设备节点禁用了，现在得先打开它。"""
        import vdd as vd

        if vd.device_enabled() is not False:
            return
        if self._vdd_off_mode() != "deep":
            self.log(i18n.t(
                "虚拟屏的设备节点现在是被禁用的（以前用过「彻底关闭」），先把它打开…",
                "The virtual display's device node is currently disabled (left over "
                "from \"switch it off for real\"); switching it back on..."))
        else:
            self.log(i18n.t(
                "虚拟屏上次是彻底关掉的（设备节点被禁用），先把它打开…",
                "The virtual display was switched off completely last time (its device "
                "node is disabled); switching it back on..."))
        ok, text = vd.set_enabled(True, log=self.log)
        if not ok:
            if text:
                self.log("  " + str(text)[:400])
            raise RuntimeError(i18n.t(
                "虚拟屏没能打开（这一步要管理员权限）。可以在菜单里点「彻底关闭虚拟屏」重试，"
                "或者把「不用时」那一项改成「只移出桌面」。",
                "The virtual display could not be switched on (this step needs "
                "administrator rights). Retry from the menu with \"Shut the virtual "
                "display down for real\", or set the \"when not in use\" choice back to "
                "\"just take it off the desktop\"."))

    def _shut_vdd_down(self):
        """用完按档位把虚拟屏收起来。deep 档要提权，可能弹一次 UAC。"""
        import vdd as vd

        if getattr(self, "skip_vdd_off", False):
            # 正在装更新：这会儿弹一个授权框只会让人以为更新要 UAC，而且新版本
            # 启动时反正会把它重新打开。留着不动，只是不在桌面上。
            self.log(i18n.t("  正在安装更新，这次不切换虚拟屏的状态",
                            "  installing an update, so the virtual display's state is "
                            "left as it is"))
            return
        if self._vdd_off_mode() != "deep":
            return
        if vd.device_enabled() is not True:
            return
        self.log(i18n.t("按设置把虚拟屏彻底关掉（禁用设备节点，会弹一次管理员授权）…",
                        "shutting the virtual display down completely (disabling its "
                        "device node; this asks for administrator rights once)..."))
        ok, text = vd.set_enabled(False, log=self.log)
        if ok:
            self.log(i18n.t(
                "  虚拟屏已彻底关闭：下次开机不会有这块屏，远程软件也看不到它",
                "  the virtual display is gone: it will not come back at boot and "
                "remote software will not see it"))
        else:
            self.log(i18n.t(
                "  没能彻底关掉（%s）。它现在只是不在桌面上，下次开机还会回来。",
                "  could not shut it down completely (%s). It is off the desktop "
                "for now, but it will come back at the next boot.")
                % (str(text)[:200] if text else i18n.t("未知原因", "unknown reason")))

    def apply_display(self):
        """Make sure a second screen exists at the requested size. Returns rect."""
        import vdd as vd

        if self.cfg["mode"] == "mirror":
            mons = wd.live_monitors()
            if not mons:
                raise RuntimeError(i18n.t("找不到任何活动的显示器",
                                          "No active monitor was found"))
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

        self._ensure_vdd_on()
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
            raise RuntimeError(i18n.t("虚拟显示器 %s 无法激活（未上桌面）",
                                      "Virtual display %s could not be activated "
                                      "(not on the desktop)") % name)

        mons = wd.live_monitors()
        primary = next((m for m in mons if m["primary"]), mons[0] if mons else None)
        if primary is None:
            raise RuntimeError(i18n.t("找不到主显示器", "No primary monitor was found"))

        w = int(self.cfg["width"])
        h = int(self.cfg["height"])
        hz = int(self.cfg["hz"])
        if self.cfg.get("match_phone", True):
            w, h, hz = self._match_phone(name, w, h, hz)

        w, h, hz, note, usable = self._resolve_mode(name, w, h, hz)
        if note:
            self.log("  " + note)
        if not usable:
            # Getting a picture up matters more than the exact size, so fall back
            # to one the driver really has instead of failing outright.  The note
            # above already told the user what was asked for and what is on offer.
            fb = wd.best_mode(name, w, h, hz)
            if not fb:
                raise RuntimeError(note)
            self.log("  改用驱动确实有的 %dx%d@%d" % (fb[0], fb[1], fb[2]))
            w, h, hz = fb[0], fb[1], fb[2]

        x, y = self._position_for(primary, w, h)

        # Same display-config lock can reject the mode change, so retry it too.
        self._set_mode_retry(name, w, h, hz, x, y)

        rect = wd.desktop_rect(name)
        if not rect:
            raise RuntimeError(i18n.t("虚拟显示器设置后仍然不可用",
                                  "The virtual display is still unusable after "
                                  "being configured"))
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

    def _set_mode_retry(self, name, w, h, hz, x, y, attempts=4):
        """ChangeDisplaySettingsEx, retried around Windows' display-config lock.

        While Windows is reconfiguring anything it holds the configuration
        locked for a moment, and ChangeDisplaySettingsEx answers
        DISP_CHANGE_FAILED (-1) meanwhile.  Success is judged by the mode that
        actually took effect, not by the return code.

        DISP_CHANGE_BADMODE (-2) is the one failure that is not worth retrying:
        it means the display has no such mode, and asking five more times cannot
        conjure one.  Retrying it anyway cost the user nine seconds of a busy UI
        for a change that was never going to happen.
        """
        rc = None
        for attempt in range(attempts):
            try:
                rc = wd.set_mode(name, w, h, hz, x, y)
            except OSError as e:
                rc = "OSError: %s" % e
            self.log("  ChangeDisplaySettingsEx -> %s%s"
                     % (DISP_CHANGE.get(rc, rc),
                        "" if attempt == 0 else " (重试 %d)" % attempt))
            if rc == -2:
                return rc
            time.sleep(1.0)
            r = wd.desktop_rect(name)
            if r and r["width"] == w and r["height"] == h:
                return rc
            time.sleep(0.5)
        return rc

    def _resolve_mode(self, name, w, h, hz):
        """Fit a wanted mode onto what the display actually offers.

        Returns ``(w, h, hz, note, usable)``.  *note* is None when the request is
        available exactly as asked, and a sentence for the user otherwise;
        *usable* is False when Windows would answer DISP_CHANGE_BADMODE.

        This is the other half of "改了分辨率还是没变化".  The form offered
        twenty-two sizes while the driver builds its mode table from eleven rows
        in vdd_settings.xml, so half the menu could never work -- and nothing
        said so.  A missing size is now reported, and offered to the driver
        (which still has room for a few more rows), so reinstalling the driver or
        rebooting makes it real instead of silently dropping it.
        """
        import vdd as vd

        modes = wd.available_modes(name)
        if not modes:
            # Nothing to compare against: let Windows have the final word.
            return w, h, hz, None, True
        if (w, h, hz) in modes:
            return w, h, hz, None, True

        rates = sorted({m[2] for m in modes if m[0] == w and m[1] == h})
        if rates:
            best = min(rates, key=lambda r: abs(r - hz))
            return w, h, best, i18n.t(
                "%dx%d 没有 %d Hz，改用 %d Hz" % (w, h, hz, best),
                "%dx%d does not offer %d Hz; using %d Hz" % (w, h, hz, best)), True

        sizes = sorted({(m[0], m[1]) for m in modes})
        sep = ", " if i18n.en() else "、"
        near = sep.join("%dx%d" % s for s in sizes[:8])
        try:
            added = vd.add_resolution(w, h, hz)
        except Exception:
            added = None
        if added:
            return w, h, hz, i18n.t(
                "驱动模式表里原本没有 %dx%d，已经写进去了 —— 点「安装虚拟显示器驱动」"
                "重装一次（或重启电脑）之后才切得过去。现在这张表里有：%s" % (w, h, near),
                "The driver had no %dx%d; it has been added to the mode table. "
                "Reinstall the driver (or reboot) before it can be used. "
                "Available now: %s" % (w, h, near)), False
        return w, h, hz, i18n.t(
            "驱动给不出 %dx%d（模式表里没有，也没能写进去）。现在能用的：%s" % (w, h, near),
            "The driver cannot offer %dx%d (not in its mode table, and it could "
            "not be added). Available now: %s" % (w, h, near)), False

    def retarget(self):
        """Apply the current size / refresh / position to a running session.

        Changing the resolution in the form used to save the value and stop
        there: apply_display() only ever ran on 开始投屏, so nothing ever asked
        Windows to switch the mode -- which is most of what "分辨率有时切不了"
        turns out to be.  This is the missing half, and it also covers dragging
        the virtual screen somewhere else without restarting the stream.
        """
        if not self.running or not self._display_applied:
            return False
        name = self._display_applied
        mons = wd.live_monitors()
        primary = next((m for m in mons if m["primary"]), mons[0] if mons else None)
        if primary is None:
            return False

        w = int(self.cfg["width"])
        h = int(self.cfg["height"])
        hz = int(self.cfg["hz"])
        if self.cfg.get("match_phone", True):
            w, h, hz = self._match_phone(name, w, h, hz)
        w, h, hz, note, usable = self._resolve_mode(name, w, h, hz)
        if note:
            self.log("  " + note)
        if not usable:
            # Stop here and say why.  Restarting the capture for a mode Windows
            # never accepted is what made "没有变化" look like "something
            # happened": the picture blinked and came back the same size.
            self.log("显示模式未更改")
            return False
        x, y = self._position_for(primary, w, h)

        cur = wd.desktop_rect(name) or {}
        if (cur.get("width") == w and cur.get("height") == h
                and cur.get("hz") == hz
                and cur.get("left") == x and cur.get("top") == y):
            self.log("显示模式没变，无需切换")
            return False

        self.log("切换虚拟屏到 %dx%d@%d 位置 (%d,%d)…" % (w, h, hz, x, y))
        self._set_mode_retry(name, w, h, hz, x, y)
        got = wd.desktop_rect(name)
        if got:
            self.log("  现在实际是 %dx%d@%d at (%d,%d)" % (
                got["width"], got["height"], got["hz"], got["left"], got["top"]))
        if not got or got["width"] != w or got["height"] != h:
            self.log("切换没有生效，保持原来的显示模式（画面不重启）")
            return False
        # The running ffmpeg was told to grab a rectangle of the old size, and
        # dxgi's duplication is invalidated by the mode change anyway; restarting
        # the capture is also what makes the phone re-handshake for the new size.
        self._recover_capture("显示模式已更改")
        return True

    # -------------------------------------------------------------- 2. device
    def find_device(self):
        from adb import Adb
        self.adb = Adb()
        rows = self.adb.devices()
        ready = [r for r in rows if r["state"] == "device"]
        if not ready:
            states = ", ".join("%s=%s" % (r["serial"], r["state"]) for r in rows) or "无"
            raise RuntimeError(i18n.t("没有可用的安卓设备（adb devices: %s）",
                                  "No usable Android device (adb devices: %s)") % states)
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
                    raise RuntimeError(i18n.t("APK 安装失败：%s",
                                      "APK install failed: %s") % out.strip())

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
            # Hand the injector the display *name*, not just its rectangle: it
            # re-reads the real position on a short timer so that dragging the
            # display somewhere else in 显示设置 cannot leave touches offset.
            self.touch = TouchServer(self.rect, self._display_applied or self.target,
                                     log=self.log, on_notice=self._phone_notice)
            self.touch.start()
            self.log("触摸控制已开启（点按/拖动/双指滚动/双指轻点=右键）")
        self._stop.clear()
        self._pump = threading.Thread(target=self._pump_loop, name="pump", daemon=True)
        self._pump.start()
        self._last = time.time()
        return backend

    def _phone_notice(self, code, msg):
        """The phone reported something about itself (screen off/on, app gone).

        Worth surfacing loudly: a phone with its screen off looks exactly like a
        frozen stream from here, and which one it is decides whether the user
        waits or picks the phone up.
        """
        self.log(msg)
        if self.on_notice is not None:
            try:
                self.on_notice(code, msg)
            except Exception:
                pass

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
            # Nothing in here may kill this thread: it is the only thing moving
            # frames from ffmpeg to the phone, and if it dies the picture
            # freezes while the app still says 投屏中.
            try:
                units = self.capture.get_units(0.4) if self.capture else []
                if units and self.server:
                    self.server.put(units)
                self._tick()
                self._watch_capture()
            except Exception as e:
                self.log("采集循环异常（已忽略）: %r" % (e,))
                time.sleep(0.5)

    # ------------------------------------------------------ capture watchdog
    STALL_LIMIT = 3.0        # seconds of silence before we call it dead
    RECOVER_WAIT = 4.0       # seconds between restart attempts

    def _watch_capture(self):
        cap = self.capture
        if cap is None or self._stop.is_set() or self.fatal:
            return
        now = time.time()
        if cap.dead():
            why = "采集进程已退出"
        elif cap.stalled(self.STALL_LIMIT):
            why = "采集卡住（%.0f 秒没有画面）" % (now - cap.last_data)
        else:
            self._recover_tries = 0
            return
        if now - self._last_recover < self.RECOVER_WAIT:
            return
        self._last_recover = now
        self._recover_capture(why)

    def _recover_capture(self, why):
        """Restart ffmpeg after the desktop duplication was invalidated."""
        cap = self.capture
        self._recover_tries += 1
        n = self._recover_tries
        # Only speak up on the first failure and then once every 30 s, so a
        # machine with the display unplugged does not fill the log.
        quiet = n > 1 and n % 6 != 0
        if not quiet:
            hint = ""
            if cap is not None and cap.access_lost():
                hint = "（DXGI 报 887a0026 ACCESS_LOST：显示模式变了，或者刚弹过 UAC 提权窗口）"
            self.log("采集中断：%s%s，正在重连采集…" % (why, hint))
            if cap is not None and cap.err_tail.strip():
                self.log("  ffmpeg: " + cap.err_tail.strip().splitlines()[-1])
        if cap is not None:
            try:
                cap.stop()
            except Exception:
                pass
        self.capture = None

        # The user may have changed the size while we were away -- that is
        # exactly what Windows' 显示设置 does -- so re-read the real rectangle.
        old = self.rect or {}
        old_wh = (old.get("width"), old.get("height"))
        changed = self._refresh_rect()
        if changed is None:
            self.fatal = i18n.t("虚拟显示器已从桌面消失，投屏停止",
                                "The virtual display vanished from the desktop; "
                                "streaming stopped")
            self.log("采集中断：" + self.fatal)
            return
        try:
            cap = Capture(self.cfg, self.rect, log=self.log)
            backend = cap.start()
        except Exception as e:
            if not quiet:
                self.log("  重连失败: %s" % e)
            return
        self.capture = cap
        self._recover_tries = 0
        self.log("  采集已恢复: %s  %dx%d@%d" % (
            backend, self.rect["width"], self.rect["height"], self.cfg["fps"]))
        if changed and self.server is not None:
            # The phone's decoder was configured for the old size, so the new
            # pictures would be rejected.  Dropping the connection makes the
            # app's retry loop reconnect and pick up a fresh handshake.
            self.log("  画面尺寸变了 %dx%d → %dx%d，让手机重新握手" % (
                old_wh[0], old_wh[1], self.rect["width"], self.rect["height"]))
            self.server.drop_client()
        if self.touch is not None:
            # Always re-aim the injector, moved or resized alike.  (This used to
            # write `self.touch.rect = ...`, which TouchServer never read -- the
            # injector kept the stale rectangle and every touch was offset.)
            self.touch.set_rect(self.rect)
            self.touch.set_device(self._display_applied or self.target)

    def _refresh_rect(self):
        """Re-read the captured screen's rectangle.

        Returns None if the display vanished, True if the size changed, and
        False when nothing moved.
        """
        name = self._display_applied or self.target
        if not name:
            return False
        mon = next((m for m in wd.live_monitors() if m["name"] == name), None)
        if mon is None:
            return None
        old = self.rect or {}
        changed = (old.get("width") != mon["width"]
                   or old.get("height") != mon["height"]
                   or old.get("left") != mon["left"]
                   or old.get("top") != mon["top"])
        self.rect = dict(mon)
        self._display_applied = name
        return True if changed else False

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

    def release_input(self):
        """Let go of whatever the phone is holding.

        Called when the control window goes away -- closing it (or the app
        exiting) mid-gesture used to leave the left button pressed on the PC,
        and Windows answers a stuck button with a modal drag loop that swallows
        every later injection.  The stream keeps running, the screen just stops
        responding to touch.
        """
        if self.touch:
            try:
                self.touch.release("控制窗口已关闭")
            except Exception:
                pass

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
            # deep 档：连驱动里那块屏本身也撤掉，别让它留在系统里等下一次开机。
            try:
                self._shut_vdd_down()
            except Exception as e:
                self.log("彻底关闭虚拟屏时出错: %s" % e)
        self.log("会话已停止")
