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
import os
import threading
import time

import apkver
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
    "reinstall_apk": False,
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
        self._last_stat_log = 0.0
        self._stat_seen = None
        self._orientation_hinted = None
        self._display_applied = None
        # The adapter the driver last gave us.  Stopping a stream takes the
        # screen off the desktop but leaves the device node enabled, so the next
        # start can reuse this name instead of asking the driver twice (two
        # PowerShell round trips of about three seconds each).
        self._vdd_ready_name = None
        # Whether this run ever put the screen on the desktop; quitting uses it
        # to decide whether the "switch it off for real" step is worth the wait.
        self._display_used = False
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
        # -- stream start-up ------------------------------------------------
        # The encoder is started once we know how fast the phone can actually
        # present frames, so these track "what ffmpeg was told" separately
        # from "what the user picked".
        self._capture_lock = threading.Lock()
        self._capture_cfg = None
        self._stream_fps = 0
        self._stream_t0 = 0.0
        self._last_boot_try = 0.0
        self._last_cfg_check = 0.0
        # Called with a one-line description of the GPU doing the capture, so
        # the window can show it.  1.8.5: "which graphics card is it using?"
        # had no answer anywhere in the program.
        self.gpu_cb = None
        # Called as ask_install(old_code, new_code, new_name) -> True/False when
        # the phone's app is older than the one shipped here.  None (headless
        # tests) means "do not touch the phone".
        self.ask_install = None

    # ------------------------------------------------------------------ state
    @property
    def running(self):
        # A session counts as running once the listener is up: the capture now
        # starts a second or two after the phone connects (it waits for the
        # panel rate), and reporting "not running" in that window would make
        # the window decide the stream had stopped.
        return self.server is not None and not self.fatal

    def describe(self):
        r = self.rect or {}
        return "%s  %dx%d@%d  %s" % (
            self.cfg["mode"], r.get("width", 0), r.get("height", 0),
            self._stream_fps or self.cfg.get("fps", 0), self.target or "-")

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

        The panel size is returned even when the driver does not list it yet:
        _fit_size() then appends it to vdd_settings.xml and restarts the driver
        once (about 4 s).  It used to fall back to the closest size the driver
        happened to have and suggest reinstalling the driver -- which is why a
        panel size outside the stock table looked like "it picked 3200x2136
        again".
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

        # Which way round the panel is being held decides which number is the
        # width.  The app used to be locked to landscape, so the long side was
        # always the width; since 1.8.6 it can stand up, and a tablet on a stand
        # then wants a tall screen instead of a wide desktop with bars above and
        # below it (issue #4).
        portrait = None
        try:
            if self.adb is not None:
                portrait = self.adb.screen_portrait(self.serial)
        except Exception as e:
            self.log("  读手机方向失败: %s" % e)
        if portrait is True:
            pw, ph = min(size), max(size)
        else:
            pw, ph = max(size), min(size)
        self.log("  手机分辨率 %dx%d（%s）" % (
            pw, ph, "竖屏" if portrait else ("横屏" if portrait is False
                                            else "方向未知，按横屏处理")))

        best = wd.best_mode(name, pw, ph, hz)
        if not best:
            self.log("  驱动模式表读不到，按 %dx%d 处理" % (w, h))
            return w, h, hz
        bw, bh, bhz, exact = best
        if exact:
            self.log("  虚拟屏按手机分辨率 %dx%d@%d 撑满" % (bw, bh, bhz))
            return bw, bh, bhz

        self.log("  驱动模式表里还没有 %dx%d，先加进去再重启一次驱动" % (pw, ph))
        return pw, ph, hz

    def _vdd_off_mode(self):
        """How thoroughly to put the virtual display away when we are not using it.

        "deep"    -- disable the driver's device node when the program exits, so the
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
        """程序退出时按档位把虚拟屏收起来。deep 档要提权。"""
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
        self.log(i18n.t("按设置把虚拟屏彻底关掉（禁用设备节点）…",
                        "shutting the virtual display down completely (disabling its "
                        "device node)..."))
        ok, text = vd.set_enabled(False, log=self.log)
        if ok:
            self._vdd_ready_name = None
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

        # An adapter that still answers needs no interview.  Two cases land here:
        # the program was just opened again (the screen is still on the desktop
        # from last time), and 停止 (Stop) a minute ago (the screen is off the
        # desktop but the driver is still up on purpose).  Asking the driver
        # costs two PowerShell round trips of about three seconds each
        # (device_enabled plus status), which is most of a slow start.
        prev = self._display_applied
        adapter = None
        try:
            adapter = vd.find_vdd_adapter()
        except Exception:
            adapter = None
        known = prev or self._vdd_ready_name
        fast = bool(adapter is not None and known and adapter.get("name") == known)
        if fast:
            st = {"state": "ready", "adapter": adapter}
        else:
            self._ensure_vdd_on()
            st = vd.status()
            if st["state"] != "ready":
                raise VddMissing(vd.summary_line(st))
        a = st["adapter"]
        name = a["name"]
        if fast:
            self.log("虚拟显示器: %s (%s)（驱动还开着，没再问它）"
                     % (name, a["desc"]))
        else:
            self.log("虚拟显示器: %s (%s)" % (name, a["desc"]))
        self._display_applied = name
        self._vdd_ready_name = name
        self._display_used = True

        # Work out the size *before* the screen reaches the desktop.  Learning a
        # new size may need the driver restarted (_fit_size does that, about
        # 4 s), and such a restart must not happen while the screen is in use --
        # it also hands the adapter a new display number, so the attach below
        # has to be aimed at whatever name comes back.
        w = int(self.cfg["width"])
        h = int(self.cfg["height"])
        hz = int(self.cfg["hz"])
        if self.cfg.get("match_phone", True):
            w, h, hz = self._match_phone(name, w, h, hz)
        name, w, h, hz, note = self._fit_size(name, w, h, hz)
        if note:
            self.log("  " + note)

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
        # Nothing to do when the screen is already exactly where it should be.
        # Started from a stopped stream this is the common case now, because
        # the display is deliberately left in place; skipping the call saves
        # the one-second settle below and a pointless desktop reconfigure.
        r0 = wd.desktop_rect(name)
        if (r0 and r0["width"] == w and r0["height"] == h
                and int(r0.get("hz") or 0) == int(hz or 0)
                and r0["left"] == x and r0["top"] == y):
            self.log("  模式与位置都没变，跳过 ChangeDisplaySettingsEx")
            return 0
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

        Returns ``(w, h, hz, note, usable, added)``.  *note* is None when the
        request is available exactly as asked, and a sentence for the user
        otherwise; *usable* is False when Windows would answer
        DISP_CHANGE_BADMODE; *added* says whether the size was just appended to
        the driver's settings file (True), was already in it (False), or could
        not be written (None) -- _fit_size() needs that to know whether a
        driver restart is worth its four seconds.

        This is the other half of "改了分辨率还是没变化".  The form offered
        twenty-two sizes while the driver builds its mode table from a handful
        of rows in vdd_settings.xml, so half the menu could never work -- and
        nothing said so.  A missing size is now reported and offered to the
        driver, and _fit_size() restarts the driver so it becomes real in one
        go instead of silently dropping it or waiting for a reboot.
        """
        import vdd as vd

        modes = wd.available_modes(name)
        if not modes:
            # Nothing to compare against: let Windows have the final word.
            return w, h, hz, None, True, None
        if (w, h, hz) in modes:
            return w, h, hz, None, True, None

        rates = sorted({m[2] for m in modes if m[0] == w and m[1] == h})
        if rates:
            best = min(rates, key=lambda r: abs(r - hz))
            return w, h, best, i18n.t(
                "%dx%d 没有 %d Hz，改用 %d Hz" % (w, h, hz, best),
                "%dx%d does not offer %d Hz; using %d Hz" % (w, h, hz, best)), True, None

        sizes = sorted({(m[0], m[1]) for m in modes})
        sep = ", " if i18n.en() else "、"
        near = sep.join("%dx%d" % s for s in sizes[:8])
        try:
            added = vd.add_resolution(w, h, hz)
        except Exception:
            added = None
        if added:
            return w, h, hz, i18n.t(
                "驱动模式表里原本没有 %dx%d，已经写进去了。现在这张表里有：%s"
                % (w, h, near),
                "The driver had no %dx%d; it has been added to the mode table. "
                "Available now: %s" % (w, h, near)), False, True
        if added is False:
            return w, h, hz, i18n.t(
                "驱动还没读过 %dx%d（文件里已有，但它只在设备启动时读一次）。"
                "现在能用的：%s" % (w, h, near),
                "The driver has not read %dx%d yet (it is in the settings file, "
                "but the driver only reads that while its device starts). "
                "Available now: %s" % (w, h, near)), False, False
        return w, h, hz, i18n.t(
            "驱动给不出 %dx%d（模式表里没有，也没能写进去）。现在能用的：%s" % (w, h, near),
            "The driver cannot offer %dx%d (not in its mode table, and it could "
            "not be added). Available now: %s" % (w, h, near)), False, None

    def _fit_size(self, name, w, h, hz):
        """Resolve a wanted mode, restarting the driver once when it has to.

        Returns ``(name, w, h, hz, note)``.  *name* changes when the driver was
        restarted, because Windows then hands the adapter a new display number
        and every later call has to be aimed at the new one.

        The driver builds its mode table once, while its device node starts, so
        a size that was just appended to vdd_settings.xml is not in it yet.
        Restarting the node (about 4 s, no prompt -- the program is already
        elevated with --uac-admin) makes it real straight away, which is what
        this project used to ask the user to do by reinstalling the driver or
        rebooting.
        """
        import vdd as vd

        w, h, hz, note, usable, added = self._resolve_mode(name, w, h, hz)
        if usable or added is None:
            # Either it is there, or the file could not be written at all: in
            # both cases restarting the driver cannot help.
            return name, w, h, hz, note

        self.log(i18n.t(
            "  重启一次驱动，让它读到刚写进模式表的 %dx%d（约 4–7 秒，不会弹窗）"
            % (w, h),
            "  restarting the driver once so it reads the %dx%d that was just "
            "added to its mode table (about 4-7 s, no prompt)" % (w, h)))
        ok, new = vd.reload_modes((w, h), log=self.log)
        if not ok or not new:
            self.log(i18n.t(
                "  驱动没能重启成功，先用这张模式表里有的尺寸",
                "  the driver did not come back, so a size it does have is used "
                "instead"))
            fb = wd.best_mode(name, w, h, hz)
            if fb:
                return name, fb[0], fb[1], fb[2], note
            return name, w, h, hz, note
        self._display_applied = new
        self._vdd_ready_name = new
        w2, h2, hz2, note2, usable2, _added = self._resolve_mode(new, w, h, hz)
        if usable2:
            return new, w2, h2, hz2, note2
        self.log(i18n.t("  驱动重启后还是给不出 %dx%d", 
                        "  the driver still cannot offer %dx%d") % (w, h))
        fb = wd.best_mode(new, w, h, hz)
        if fb:
            return new, fb[0], fb[1], fb[2], note2
        return new, w2, h2, hz2, note2

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
        w, h, hz, note, usable, added = self._resolve_mode(name, w, h, hz)
        if note:
            self.log("  " + note)
        if not usable:
            # Stop here and say why.  Restarting the capture for a mode Windows
            # never accepted is what made "没有变化" look like "something
            # happened": the picture blinked and came back the same size.
            if added is not None:
                # A driver restart would make this size real, but it would also
                # yank the screen that is streaming right now and hand it back
                # under a new display number.  apply_display() does that cleanly,
                # before the screen is attached, so point the user there.
                self.log(i18n.t(
                    "显示模式未更改：驱动还没读过这个尺寸。点「停止」再「开始投屏」，"
                    "程序会自动重启一次驱动把它用上（约 4–7 秒）",
                    "Display mode unchanged: the driver has not read this size "
                    "yet. Stop and start casting again and the program restarts "
                    "the driver once to pick it up (about 4-7 s)."))
            else:
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

        self._prepare_apk(s)

        if self.cfg.get("auto_launch"):
            self.adb.force_stop(s)
            time.sleep(0.3)
            rc, out = self.adb.start_app(s)
            self.log("已启动手机端 App (rc=%s)" % rc)
        return True

    def _prepare_apk(self, s):
        """Make sure the phone runs this build, and say which version it has.

        1.8.5: "installed" used to mean "done".  A phone could sit on 1.6.9 for
        months while the PC ran 1.8.4 and the only symptom was a missing
        feature nobody could explain.  The APK's own versionCode is read out of
        the shipped file (apkver) and compared with the one on the phone; an
        older app is *offered*, never silently replaced and never silently
        skipped, because the whole point of the update is that the person
        holding the phone gets a say.  "每次重新安装 APK" still forces it.
        """
        apk = apk_path()
        # A source checkout without a build has no DroidExtend.apk; say so
        # instead of failing later inside adb.install with a bare path.
        if not apk or not os.path.exists(apk):
            self.log(i18n.t("找不到随程序附带的 APK（DroidExtend.apk），这次不改手机端",
                            "No DroidExtend.apk next to the program; leaving the phone alone"))
            return
        code, name = apkver.read_apk_version(apk)
        ver = name or "?"
        if not self.adb.is_installed(s):
            self.log(i18n.t("手机上没有 App，正在安装 %s（%s）…",
                            "The app is not on the phone; installing %s (%s)...")
                     % (apk, ver))
            self._install_apk(s, apk)
            return
        if self.cfg.get("reinstall_apk"):
            self.log(i18n.t("按设置重新安装 APK（%s）…",
                            "Reinstalling the APK as configured (%s)...") % ver)
            self._install_apk(s, apk)
            return
        had, had_name = self.adb.installed_version(s)
        if not code or not had:
            self.log(i18n.t("APK 已安装：跳过安装（查不到手机上的版本号，勾“每次重新安装 APK”可强制重装）",
                            "APK installed: skipping (could not read the phone's "
                            "version; tick Reinstall to force it)"))
            return
        if code <= had:
            self.log(i18n.t("APK 已是最新（手机 %s / versionCode %d，这里 %s）",
                            "APK up to date (phone %s / versionCode %d, here %s)")
                     % (had_name or "?", had, ver))
            return
        self.log(i18n.t("手机上的 App 是旧的：手机 %s（versionCode %d），这里是 %s（%d）",
                        "The phone's app is older: %s (versionCode %d) on the phone, "
                        "%s (%d) here")
                 % (had_name or "?", had, ver, code))
        ask = self.ask_install
        yes = False
        if ask is not None:
            try:
                yes = bool(ask(had, code, ver))
            except Exception as e:
                self.log(i18n.t("  询问安装时出错: %s", "  Asking about the update failed: %s") % e)
        if not yes:
            self.log(i18n.t("  这次不更新（新旧的界面与协议可能对不上，出问题先更新手机端）",
                            "  Not updating this time (old and new can disagree; "
                            "update the phone app first if anything looks odd)"))
            return
        self._install_apk(s, apk)

    def _install_apk(self, s, apk):
        ok, out = self.adb.install(s, apk)
        last = out.strip().splitlines()[-1] if out.strip() else ""
        self.log(i18n.t("  安装%s: %s", "  Install %s: %s")
                 % (i18n.t("成功", "ok") if ok else i18n.t("失败", "failed"), last))
        if ok:
            return True
        # 1.8.3: Huawei's 纯净模式 refuses the APK with an error code nobody can
        # read, and the user is left thinking the cable is broken.
        from adb import install_hint
        hint = install_hint(out)
        if hint:
            self.log("  " + hint, "err")
        raise RuntimeError(i18n.t("APK 安装失败：%s", "APK install failed: %s")
                           % (hint or out.strip()))

    # -------------------------------------------------------------- 3. stream
    def start_stream(self, port=HOST_PORT):
        self.server = StreamServer(port=port, log=self.log, on_client=self._on_client)
        self.server.start()
        # The encoder is *not* started here.  Its frame rate has to match what
        # the phone can really show, and only the phone knows that: it sends its
        # panel rate in a one-line hello the moment it connects (see
        # server.StreamServer._read_hello).  Starting ffmpeg now at the
        # configured rate and restarting it 1.5 s later would show the user a
        # picture, a freeze and a second handshake for no gain, while waiting
        # for the hello costs nothing -- ffmpeg needs 1.2 s to spin up anyway.
        # _on_client starts it; the pump thread starts it after
        # CAPTURE_BOOT_WAIT if no phone ever turns up.
        self.capture = None
        self._capture_cfg = None
        self._stream_fps = 0
        self._stream_t0 = time.time()
        # Every session gets its first stats line back: the fingerprint of the
        # last one belongs to a stream that is already over.
        self._stat_seen = None
        self._last_stat_log = 0.0
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
        return "waiting for the phone"

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
        fps, hz = self._wanted_fps()
        self._stream_fps = fps or self.cfg["fps"]
        if self.server and self.rect:
            self.server.send_header(self.rect["width"], self.rect["height"],
                                    self._stream_fps, self._quality_note())
        # ffmpeg needs 1.2 s to come up; doing that on the accept thread would
        # make the app's 700 ms reconnect loop queue behind it.
        threading.Thread(target=self._ensure_capture, name="capture-boot",
                         daemon=True).start()

    def _orientation_hint(self):
        """Say it out loud when the phone and the virtual screen disagree.

        The size of the virtual screen is decided before the phone connects, so
        a rotation in the middle of a session cannot be followed live: Windows
        would have to move the screen and the encoder would have to restart.
        Saying so beats leaving the user to wonder why the picture is lying on
        its side -- and stopping and starting again takes about three seconds.
        """
        if not self.cfg.get("match_phone", True) or self.server is None:
            return
        if not self.rect:
            return
        upright = getattr(self.server, "client_portrait", None)
        w = int(self.rect.get("width", 0) or 0)
        h = int(self.rect.get("height", 0) or 0)
        if upright is None or w <= 0 or h <= 0:
            return
        if upright == (h > w):
            return
        # Once per direction, not once per second: the phone says which way up
        # it is a moment after the handshake, so testing only at connect time
        # would check an empty answer, and repeating it every second would bury
        # the rest of the log.
        if getattr(self, "_orientation_hinted", None) == upright:
            return
        self._orientation_hinted = upright
        self.log("手机现在是%s，虚拟屏是%s；点「停止」再「开始投屏」就按手机的方向重排"
                 % ("竖屏" if upright else "横屏", "竖屏" if h > w else "横屏"))

    def _wanted_fps(self):
        """The frame rate to encode at, and the panel rate it came from.

        The user picks the fps, but the phone is the limit.  Frames the phone
        cannot decode are not extra smoothness: they pile up in the socket, the
        app hits its backlog limit after ~77 ms of growth
        (StreamClient.BACKLOG_DROP_BYTES) and throws away everything until the
        next keyframe -- up to two seconds at the default GOP -- so the picture
        freezes.  Measured here on a 3200x2136 desktop into a 120 Hz panel:
        144 fps froze every ~4 s, 120 fps did not freeze once in four runs
        (capture managed 131 fps at 144 and 109 fps at 120; the phone decodes
        about 134 fps, so 144 is over budget no matter how it is sliced).

        So: min(user setting, panel rate).  A phone that stays silent (older
        app) leaves the configured fps untouched.
        """
        want = int(self.cfg.get("fps", 60) or 60)
        hz = int(getattr(self.server, "client_hz", 0) or 0) if self.server is not None else 0
        if hz <= 0 or want <= 0:
            return want, hz
        return min(want, hz), hz

    CAPTURE_BOOT_WAIT = 6.0   # start ffmpeg anyway if no phone turns up
    HELLO_GRACE = 2.5         # wait this long for a late panel rate first

    def _ensure_capture(self):
        """Start the encoder for the phone we are talking to, if it is not up.

        Runs on its own thread (ffmpeg start blocks for 1.2 s) and is safe to
        call any number of times: the lock keeps one start at a time, and it
        only touches a running capture when the wanted frame rate changed --
        which happens when a late hello (server._hello_watch) corrects what the
        phone first told us.
        """
        with self._capture_lock:
            if self._stop.is_set():
                return
            fps, hz = self._wanted_fps()
            if self.capture is None and hz <= 0 and self.server is not None:
                # The phone's line can arrive after the handshake window (see
                # server._hello_watch).  Starting ffmpeg at the user's fps now
                # and restarting it the moment the real rate lands costs the
                # user a picture, a freeze and a second encoder start; waiting
                # here costs a second of "connecting" that ffmpeg needs anyway.
                self._wait_hello(self.HELLO_GRACE)
                fps, hz = self._wanted_fps()
            if self.capture is not None:
                if fps == self._stream_fps:
                    return
                self.log("编码帧率 %d → %d fps（对齐手机面板 %dHz）"
                         % (self._stream_fps, fps, hz))
                try:
                    self.capture.stop()
                except Exception:
                    pass
                self.capture = None
            elif hz and fps != int(self.cfg.get("fps", 60) or 60):
                self.log("手机面板 %dHz，编码帧率 %d → %d fps（多出来的帧只会积压成卡顿）"
                         % (hz, int(self.cfg.get("fps", 60) or 60), fps))
            cfg = dict(self.cfg)
            cfg["fps"] = fps
            cap = Capture(cfg, self.rect, log=self.log)
            try:
                backend = cap.start()
            except Exception as e:
                # Not fatal: the pump thread tries again on the next tick, and
                # _watch_capture covers the case where a running capture dies.
                self.log("采集启动失败: %s" % e)
                return
            self.capture = cap
            self._capture_cfg = cfg
            self._stream_fps = fps
            self.log("编码器 %s / 采集 %s" % (cfg["encoder"], backend))
            self._log_gpu(cap)

    def _log_gpu(self, cap):
        """Name the card that grabs the picture, and the one that encodes it.

        ddagrab has no adapter argument: without -init_hw_device it always
        duplicates GPU 0.  On a laptop where the screen sits on the other card
        that is the whole reason one mode works and the other shows nothing, so
        the answer belongs in the log and in the window, not in a support reply.
        The encoder is a separate card decision: gdigrab reads the desktop through
        Windows, but NVENC still runs on the NVIDIA card.
        """
        adapter = getattr(cap, "dda_adapter", None)
        enc = str(self.cfg.get("encoder", ""))
        if "nvenc" in enc:
            enc_txt = i18n.t("NVIDIA 显卡的 NVENC", "NVIDIA GPU (NVENC)")
        elif "amf" in enc:
            enc_txt = i18n.t("AMD 显卡的 AMF", "AMD GPU (AMF)")
        elif enc.startswith("libx"):
            enc_txt = i18n.t("CPU（%s）", "CPU (%s)") % enc
        else:
            enc_txt = enc or i18n.t("未知", "unknown")
        if adapter is None:
            grab = i18n.t("桌面拷贝（gdigrab，不经过显卡）", "desktop copy (gdigrab, no GPU)")
            short = i18n.t("gdigrab 抓桌面 · 编码 %s", "gdigrab · encode %s") % enc_txt
        else:
            name = getattr(cap, "dda_gpu", None) or i18n.t("名字未知", "unknown")
            idx = getattr(cap, "dda_output", 0)
            grab = i18n.t('显卡%d "%s"（输出 #%d）', 'GPU %d "%s" (output #%d)') % (
                adapter, name, idx)
            short = i18n.t('显卡%d "%s" · 编码 %s', 'GPU %d "%s" · encode %s') % (
                adapter, name, enc_txt)
        self.log(i18n.t("采集显卡: %s ／ 编码: %s", "Capture card: %s / encode: %s")
                 % (grab, enc_txt))
        self._notify_gpu(short)

    def _notify_gpu(self, text):
        cb = getattr(self, "gpu_cb", None)
        if cb is None:
            return
        try:
            cb(text)
        except Exception:
            pass

    def _wait_hello(self, limit):
        """Block until the phone's panel rate is known, but never past limit."""
        deadline = time.time() + limit
        while time.time() < deadline and not self._stop.is_set():
            if int(getattr(self.server, "client_hz", 0) or 0) > 0:
                return
            time.sleep(0.05)

    def _pump_loop(self):
        while not self._stop.is_set():
            # Nothing in here may kill this thread: it is the only thing moving
            # frames from ffmpeg to the phone, and if it dies the picture
            # freezes while the app still says 投屏中.
            try:
                if self.capture is None:
                    # No capture yet: _on_client starts it as soon as the phone
                    # says how fast its panel is.  If no phone turns up -- or it
                    # never sends its hello -- start anyway after the grace
                    # period, so a session is never left with no picture.
                    now = time.time()
                    if (now - self._stream_t0 > self.CAPTURE_BOOT_WAIT
                            and now - self._last_boot_try > 5.0):
                        self._last_boot_try = now
                        self._ensure_capture()
                    time.sleep(0.05)
                    continue
                # The panel rate can still change under a running capture: the
                # phone may send its hello late (server._hello_watch) or come
                # back from Doze at a different rate.  Check once a second and
                # let _ensure_capture restart ffmpeg if the answer changed.
                now = time.time()
                if now - self._last_cfg_check > 1.0:
                    self._last_cfg_check = now
                    if self._wanted_fps()[0] != self._stream_fps:
                        threading.Thread(target=self._ensure_capture,
                                         name="capture-boot", daemon=True).start()
                units = self.capture.get_units(0.4)
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
    # The shortest gap between two stats lines.  A line every five seconds is
    # 120 lines in a ten-minute session, all but the first saying the same
    # thing; _stat_key() has to change as well for a second line to be worth
    # printing, so a steady stream logs one line and a stream that falls apart
    # logs whenever it falls apart (at most once per STATS_EVERY).
    STATS_EVERY = 10.0
    # Do not say anything during the first seconds of a stream: capture climbs
    # from 30 to 120 fps, the decoder has shown nothing yet, and each of those
    # numbers would be a line.  STATS_LATE is the escape hatch -- a stream that
    # never settles (the phone shows nothing at all) still gets one line.
    STATS_QUIET = 12.0
    STATS_LATE = 20.0

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
            cap = Capture(self._capture_cfg or self.cfg, self.rect, log=self.log)
            backend = cap.start()
        except Exception as e:
            if not quiet:
                self.log("  重连失败: %s" % e)
            return
        self.capture = cap
        self._recover_tries = 0
        self.log("  采集已恢复: %s  %dx%d@%d" % (
            backend, self.rect["width"], self.rect["height"],
            self._stream_fps or self.cfg["fps"]))
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
        # One line that says where the frames went.  A phone
        # that shows nothing leaves the user (and any bug report) guessing
        # between four very different causes: the host stopped producing
        # frames, the socket is not carrying them, the phone receives them but
        # never decodes them, or it decodes them and never puts them on screen.
        # The phone reports its own numbers (Server.client_stats), so this one
        # line answers that question without a cable and a logcat.  It is
        # printed once, and then only when the numbers move into another
        # bucket: a line every few seconds trains people to stop reading.
        if self.server is not None and self.server.conn is not None:
            self._orientation_hint()
            key = self._stat_key()
            if (key is not None and self._stat_moved(key)
                    and now - self._last_stat_log >= self.STATS_EVERY):
                self._stat_seen = key
                self._last_stat_log = now
                self.log(self._stat_line())

    def _stat_key(self):
        """What a stats line would say, as numbers.  None means "not yet".

        The line is printed when this changes enough (_stat_moved); comparing
        the numbers rather than bucketing them is what keeps 119 fps from
        looking like a change from 120 fps.
        """
        since = time.time() - self._stream_t0
        if since < self.STATS_QUIET:
            return None
        srv = self.server
        st = getattr(srv, "client_stats", None) if srv is not None else None
        age = time.time() - (getattr(srv, "client_stats_at", 0.0) or 0.0)
        if not st or age >= 6.0:
            return ("手机没回报", self.stats["fps"])
        if (st["shown_fps"] < 0.6 * (self._stream_fps or 1)
                and since < self.STATS_LATE):
            # Still ramping towards the configured rate.  A picture that stays
            # under this line past STATS_LATE is the case worth printing.
            return None
        return ("手机有回报", float(st["recv_fps"]), float(st["shown_fps"]),
                float(st["latency_ms"]))

    def _stat_moved(self, key):
        """Is this worth another line?

        25 % on a frame rate (at least 15 fps) or 50 ms on latency.  Anything
        finer turns a healthy stream into a wall of near-identical lines --
        119 then 120 then 119 again.
        """
        old = self._stat_seen
        if old is None or old[0] != key[0] or len(old) != len(key):
            return True
        for i, (a, b) in enumerate(zip(old[1:], key[1:])):
            span = 50.0 if i == 2 else max(15.0, 0.25 * abs(a))
            if abs(b - a) > span:
                return True
        return False

    def _stat_line(self):
        line = "采集 %.0f fps · 发送 %.1f Mbps" % (self.stats["fps"],
                                                self.stats["mbps"])
        srv = self.server
        st = getattr(srv, "client_stats", None) if srv is not None else None
        age = time.time() - (getattr(srv, "client_stats_at", 0.0) or 0.0)
        if st and age < 6.0:
            line += " · 手机 收 %d fps / 显示 %d fps · %d ms" % (
                st["recv_fps"], st["shown_fps"], st["latency_ms"])
        else:
            line += " · 手机暂无回报（1.8.4 之前的 App 不会回话）"
        return line

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

    def stop(self, restore_display=True, vdd_off=False):
        """Stop the stream.

        restore_display takes the virtual screen back off the desktop (the
        default).  A screen nobody is streaming to is just somewhere for the
        mouse to wander off to, and it keeps the desktop 4480 px wide.  The
        driver's device node is left enabled on purpose: pressing 开始投屏 again
        then only has to re-attach the screen, with no four-second driver
        switch and no UAC prompt.

        vdd_off is passed only when the program really exits
        (Session.shutdown): it also disables the device node in "deep" mode, so
        the screen is not there at the next boot either.
        """
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
        self._capture_cfg = None
        self._stream_fps = 0
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
            # restore the previous *mode* instead, which left a second screen
            # attached forever after pressing 停止 -- Windows still listed it
            # and the desktop stayed 4480 px wide.  The display stays available
            # to the driver; it just is not part of the desktop any more, and
            # apply_display() attaches it again in about a second.
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
        if vdd_off:
            # 只有程序真的退出时才走到这里：按设置里「退出程序时」那一项，决定要
            # 不要连驱动里那块屏本身也撤掉，别让它留在系统里等下一次开机。
            try:
                self._shut_vdd_down()
            except Exception as e:
                self.log("彻底关闭虚拟屏时出错: %s" % e)
        self._notify_gpu(None)
        self.log("会话已停止")

    def shutdown(self):
        """完全退出程序：先停流、把屏从桌面摘掉，再按设置彻底关掉设备节点。"""
        if not (self.running or self.server or self._display_applied
                or self._display_used):
            return
        self.stop(restore_display=True, vdd_off=True)
