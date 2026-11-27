"""The desktop UI, without the window.

The old shell was Tk: every widget was also the place the state lived, so a
redraw meant rebuilding widgets.  This one keeps the state in plain Python
dictionaries and hands the page a snapshot of it; the page owns nothing but
what it is drawing.  Everything that talks to Windows -- the virtual display,
adb, the encoder, the settings file -- is the same code the Tk window used.
"""
import os
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.dirname(HERE)
if HOST not in sys.path:
    sys.path.insert(0, HOST)

import changelog as cl                                                       # noqa: E402
import i18n                                                                  # noqa: E402
import manual as mn                                                          # noqa: E402
import paths as paths_mod                                                    # noqa: E402
from credits import credits, LICENSES                                        # noqa: E402
import settings as st                                                        # noqa: E402
import win_display as wd                                                     # noqa: E402
from capture import (ENCODERS, NVENC_PRESETS, X264_PRESETS,                   # noqa: E402
                     list_hardware_encoders, available_encoders)
from paths import apk_path, adb_exe, ffmpeg_exe                              # noqa: E402
from session import Session, DEFAULTS                                        # noqa: E402
from version import (APP_NAME, APP_VERSION, APP_DATE, AUTHOR, OWNER,          # noqa: E402
                     EMAILS, REPO_URL, RELEASES_URL, ISSUES_URL, HUAWEI_DOC_URL,
                     app_title)

RESOLUTIONS = [
    (1280, 720), (1600, 720), (1600, 900), (1920, 864), (1920, 1080),
    (1920, 1200), (2000, 1200), (2160, 1080), (2280, 1080), (2340, 1080),
    (2400, 1080), (2412, 1080), (2560, 1440), (2560, 1600), (2700, 1224),
    (2712, 1220), (2800, 1260), (3040, 1440), (3120, 1440), (3200, 1440),
    (3200, 2136), (3840, 2160),
]
HZ_CHOICES = [30, 60, 75, 90, 120, 144, 165, 240]
FPS_CHOICES = [24, 30, 40, 48, 50, 60, 75, 90, 120, 144]
BITRATE_CHOICES = [4, 6, 8, 10, 12, 16, 20, 25, 30, 40, 50, 80, 120]
QUALITY_CHOICES = [14, 16, 17, 18, 20, 22, 23, 24, 26, 28, 32, 36]
POSITIONS = [("right", "右侧", "Right"), ("left", "左侧", "Left"),
             ("custom", "自定义", "Custom")]
BACKENDS = [("auto", "自动", "Auto"), ("native", "原生采集", "Native"),
            ("ffmpeg", "ffmpeg", "ffmpeg")]
RATE_CONTROL = [("vbr", "VBR 平均码率", "VBR"), ("cbr", "CBR 固定码率", "CBR")]
OFF_MODES = [("deep", "开机时连设备一起关掉（需要 UAC）",
              "Turn the device itself off (needs UAC)"),
             ("desk", "设备留着，但不放到桌面上（不需要 UAC）",
              "Keep the device, just keep it off the desktop")]
NOTIFY_KINDS = [
    ("stream", "开机自动投屏开始", "The automatic stream started"),
    ("manual", "我按开始投屏", "I pressed Start"),
    ("fail", "投屏失败或中断", "The stream failed or broke off"),
    ("driver", "虚拟显示器驱动事件", "Virtual display driver events"),
    ("update", "有新版本", "A newer version exists"),
    ("apk", "手机端 App 需要更新", "The phone app needs an update"),
    ("misc", "其它", "Everything else"),
]


def custom_label():
    return i18n.t("自定义", "Custom")


def _caption_zh_list(pairs):
    return [{"v": v, "zh": zh, "en": en} for v, zh, en in pairs]


class Core(object):
    """State and actions; the window and the page are its two clients."""

    def __init__(self):
        self.settings = st.load()
        i18n.set_lang(self.settings.get("language", "auto"))
        self.lang = i18n.lang()
        self._lock = threading.RLock()
        self.log_lines = []
        self.log_seq = 0
        self.busy = False
        self.stat = "ready"
        self.note = ""
        self.device = ""
        self.metrics = {"cap": "-", "send": "-", "phone": "-", "lat": "-"}
        self.dot = "idle"
        self.gpu = ""
        self.display = ""
        self.driver = {"state": "unknown", "instance": "", "enabled": False}
        self.streaming = False
        self._probed = []
        self._enc_items = [k for k in ENCODERS if k in list_hardware_encoders()] or ["libx264"]
        self._res_list = ["%dx%d" % r for r in RESOLUTIONS]
        self._hz_list = [str(h) for h in HZ_CHOICES]
        self._closing = False
        self.update_info = None
        self.upd = {}                 # download progress for the page
        self.upd_msg = None           # one answer to a manual check
        self._upd_busy = False
        self._upd_dl_busy = False
        self._upd_auto = False
        self.on_quit = None           # main.py points this at bridge.quit_app
        self._ask_rec = None
        self._ask_ev = None
        self._ask_seq = 0
        self._auto_cancel = threading.Event()
        self.session = Session(log=self.log, on_notice=self._phone_notice)
        self.session.gpu_cb = self._on_gpu
        self.session.ask_install = self._ask_install
        self._say_hello()
        threading.Thread(target=self._probe_env, name="dx-probe", daemon=True).start()
        threading.Thread(target=self._tick_loop, name="dx-tick", daemon=True).start()

    # ------------------------------------------------------------------ log
    def log(self, msg, tag=None):
        line = {"m": "" if msg is None else str(msg), "tag": tag or "", "n": 0,
                "t": time.strftime("%H:%M:%S")}
        with self._lock:
            self.log_seq += 1
            line["n"] = self.log_seq
            self.log_lines.append(line)
            if len(self.log_lines) > 4000:
                del self.log_lines[:1000]
        return line

    def log_since(self, seq):
        with self._lock:
            out = [l for l in self.log_lines if l["n"] > int(seq or 0)]
        return out[-800:]

    def _say_hello(self):
        self.log("%s v%s 启动" % (APP_NAME, APP_VERSION))
        self.log("adb: %s" % (adb_exe() or "缺失"))
        self.log("ffmpeg: %s" % (ffmpeg_exe() or "缺失"))
        self.log("设置文件: %s" % st.where())
        apk = apk_path()
        self.log("APK: %s%s" % (apk or "(缺失)", "" if apk else "  ← 先运行 build_apk.bat 构建"))
        if not apk:
            self.log("提示：双击 build_apk.bat 可以重新构建手机端安装包。", "err")

    # ------------------------------------------------------- session hooks
    def _phone_notice(self, code, msg):
        """The phone said something about itself (screen off, app gone)."""
        self.note = msg if code in ("off", "gone", "error") else ""
        if code in ("off", "gone"):
            self.dot = "yellow"

    def _on_gpu(self, text):
        self.gpu = text or ""

    def _ask_install(self, old_code, new_code, new_name):
        """Ask the page whether to replace the phone's app.

        The Tk build put a messagebox in the middle of prepare_device(); here the
        question goes out in the snapshot and this thread waits for act("answer").
        A page nobody is looking at leaves the question hanging, so it times out
        into "yes" -- the same answer this used to give without asking.
        """
        self.log("手机端 App 是旧的（%s），程序里带的是 %s" % (old_code, new_name))
        return bool(self._ask("installApp", {
            "old": old_code, "new": new_code, "name": new_name}, timeout=300.0))

    def _ask(self, kind, payload, timeout=300.0):
        """Publish a question for the page and block until it answers."""
        ev = threading.Event()
        with self._lock:
            self._ask_seq += 1
            rec = {"id": self._ask_seq, "kind": kind, "payload": payload,
                   "answer": None}
            self._ask_rec = rec
            self._ask_ev = ev
        ev.wait(timeout)
        with self._lock:
            self._ask_rec = None
            self._ask_ev = None
        return rec["answer"]

    def answer_ask(self, aid, ok):
        with self._lock:
            rec = self._ask_rec
            ev = self._ask_ev
            if rec is None or ev is None or int(aid or 0) != rec["id"]:
                return False
            rec["answer"] = bool(ok)
            ev.set()
            return True

    def _probe_env(self):
        """The one-time reads that used to happen during Tk's startup."""
        try:
            import vdd as vd
            sizes, rates = vd.declared_modes()
            if sizes:
                self._res_list = ["%dx%d" % s for s in sizes]
                self._hz_list = [str(r) for r in rates] or list(HZ_CHOICES)
        except Exception:
            pass
        try:
            self._probed = [k for k in ENCODERS if k in available_encoders()]
        except Exception:
            self._probed = list(self._enc_items)
        if self._probed:
            self._enc_items = list(self._probed)
        self.refresh_env()
        try:
            self.log("编码器检测完成: " + ", ".join(self._enc_items))
        except Exception:
            pass

    def refresh_env(self):
        """Which display the virtual screen is on, and which GPU encodes it."""
        try:
            import vdd as vd
            name = vd.live_name() if hasattr(vd, "live_name") else ""
            if name:
                self.display = name
        except Exception:
            pass
        self._refresh_device()

    def _refresh_device(self):
        """The line next to the status dot: the phone, not the session geometry.

        describe() gives 'extend 1920x1080@60 \\\\.\\DISPLAY31', which is an
        implementation detail; what a person wants to see there is which phone
        is plugged in, and that it is authorised.
        """
        try:
            from adb import Adb
            rows = Adb().devices()
        except Exception as exc:
            rows = []
            self.device = i18n.t("adb 不可用：%s", "adb unavailable: %s") % exc
            return
        ready = [r for r in rows if r["state"] == "device"]
        if ready:
            self.device = ready[0].get("label") or ready[0].get("serial", "")
        elif rows:
            self.device = i18n.t("已连接但未授权: ", "Connected but not authorised: ") +                 ", ".join(r["serial"] + "=" + r["state"] for r in rows)
        else:
            self.device = i18n.t(
                "没有检测到设备（用数据线连上，并打开 USB 调试）",
                "No device (plug in the cable and turn on USB debugging)")

    # ------------------------------------------------------- form <-> settings
    def _res_list_full(self):
        cur = "%dx%d" % (int(self.settings.get("width", 1920)),
                         int(self.settings.get("height", 1080)))
        lst = list(self._res_list)
        if cur not in lst:
            lst.insert(0, cur)
        return lst

    def form(self):
        s = self.settings
        w = int(s.get("width", 1920))
        h = int(s.get("height", 1080))
        res = "%dx%d" % (w, h)
        lst = self._res_list_full()
        custom = bool(s.get("custom_res")) or res not in lst
        enc = s.get("encoder", "h264_nvenc")
        if enc not in self._enc_items:
            enc = "h264_nvenc" if "h264_nvenc" in self._enc_items else self._enc_items[0]
        rc = str(s.get("rate_control", "vbr") or "vbr")
        if rc not in [v for v, _z, _e in RATE_CONTROL]:
            rc = "vbr"
        be = str(s.get("backend", "auto") or "auto")
        if be in ("ddagrab", "gdigrab"):
            be = "auto"
        if be not in [v for v, _z, _e in BACKENDS]:
            be = "auto"
        pos = str(s.get("position", "right") or "right")
        if pos not in [v for v, _z, _e in POSITIONS]:
            pos = "right"
        off = str(s.get("vdd_off_mode", "deep") or "deep")
        if off not in [v for v, _z, _e in OFF_MODES]:
            off = "desk"
        presets = X264_PRESETS if enc == "libx264" else NVENC_PRESETS
        preset = s.get("preset", "p4")
        if preset not in presets:
            preset = "veryfast" if enc == "libx264" else "p4"
        notify = {"master": bool(s.get("notify", True))}
        for kind, _zh, _en in NOTIFY_KINDS:
            notify[kind] = bool(s.get("notify_" + kind, kind in ("fail", "driver", "update", "apk")))
        return {
            "mode": s.get("mode", "extend"),
            "res": res,
            "resList": lst + [custom_label()],
            "custom": custom, "w": w, "h": h,
            "hz": int(s.get("hz", 60)), "hzList": self._hz_list,
            "fps": int(s.get("fps", 60)), "fpsList": [str(v) for v in FPS_CHOICES],
            "bitrate": int(s.get("bitrate_mbps", 40)),
            "bitrateList": [str(v) for v in BITRATE_CHOICES],
            "rc": rc, "rcList": _caption_zh_list(RATE_CONTROL),
            "quality": int(s.get("quality", 20)),
            "qualityList": [str(v) for v in QUALITY_CHOICES],
            "encoder": enc,
            "encList": [{"v": k, "label": ENCODERS[k]["label"]} for k in self._enc_items],
            "preset": preset, "presetList": list(presets),
            "backend": be, "backendList": _caption_zh_list(BACKENDS),
            "position": pos, "positionList": _caption_zh_list(POSITIONS),
            "posX": int(s.get("pos_x", 0) or 0), "posY": int(s.get("pos_y", 0) or 0),
            "drawMouse": bool(s.get("draw_mouse", True)),
            "touch": bool(s.get("touch", True)),
            "matchPhone": bool(s.get("match_phone", True)),
            "reinstallApk": bool(s.get("reinstall_apk", False)),
            "autoStream": bool(s.get("auto_stream", False)),
            "autoStreamWait": int(s.get("auto_stream_wait", 60) or 0),
            "autostart": bool(s.get("autostart", False)),
            "closeAction": str(s.get("close_action", "ask") or "ask"),
            "vddOffMode": off, "vddOffModeList": _caption_zh_list(OFF_MODES),
            "theme": str(s.get("theme", "auto") or "auto"),
            "language": str(s.get("language", "auto") or "auto"),
            "notify": notify,
            "checkUpdates": bool(s.get("check_updates", True)),
            "checkPrerelease": bool(s.get("check_prerelease", False)),
        }

    def _set_field(self, key, value):
        """One form field, however the page spells it."""
        s = self.settings
        try:
            if key == "res":
                if value == custom_label():
                    s["custom_res"] = "1"
                else:
                    s["custom_res"] = ""
                    w, h = str(value).split("x")
                    s["width"], s["height"] = int(w), int(h)
            elif key in ("w", "h"):
                n = int(value)
                if not (160 <= n <= 16384):
                    raise ValueError("size out of range")
                s["width" if key == "w" else "height"] = n
                s["custom_res"] = "1"
            elif key in ("hz", "fps", "bitrate", "quality", "posX", "posY",
                         "autoStreamWait"):
                name = {"bitrate": "bitrate_mbps", "posX": "pos_x", "posY": "pos_y",
                        "autoStreamWait": "auto_stream_wait"}.get(key, key)
                s[name] = int(value)
            elif key == "encoder":
                s["encoder"] = str(value)
                presets = X264_PRESETS if value == "libx264" else NVENC_PRESETS
                if s.get("preset") not in presets:
                    s["preset"] = "veryfast" if value == "libx264" else "p4"
            elif key in ("rc", "preset", "backend", "position", "mode", "theme",
                         "language", "closeAction", "vddOffMode"):
                name = {"rc": "rate_control", "closeAction": "close_action",
                        "vddOffMode": "vdd_off_mode"}.get(key, key)
                s[name] = str(value)
            elif key == "notify":
                v = value if isinstance(value, dict) else {}
                s["notify"] = bool(v.get("master", True))
                for kind, _zh, _en in NOTIFY_KINDS:
                    if kind in v:
                        s["notify_" + kind] = bool(v[kind])
            elif key in ("drawMouse", "touch", "matchPhone", "reinstallApk",
                         "autoStream", "autostart", "checkUpdates", "checkPrerelease"):
                name = {"drawMouse": "draw_mouse", "matchPhone": "match_phone",
                        "reinstallApk": "reinstall_apk", "autoStream": "auto_stream",
                        "checkUpdates": "check_updates",
                        "checkPrerelease": "check_prerelease"}.get(key, key)
                s[name] = bool(value)
            else:
                return False
        except (TypeError, ValueError):
            return False
        if key == "autostart" and not s.get("autostart", False) and s.get("auto_stream", False):
            s["auto_stream"] = False
            self.log("取消了开机自动启动，「开机后自动开始投屏」也一并取消。")
        self._save()
        if key in ("autostart", "autoStream", "closeAction"):
            self._sync_autostart()
        return True

    def _save(self):
        try:
            st.save(self.settings)
        except Exception as e:
            self.log("保存设置失败: %s" % e, "err")

    def apply_to_cfg(self):
        """Push the form into session.cfg -- the 1.8.1 match_phone lesson."""
        s = self.settings
        cfg = self.session.cfg
        cfg["width"] = int(s.get("width", 1920))
        cfg["height"] = int(s.get("height", 1080))
        cfg["hz"] = int(s.get("hz", 60))
        cfg["fps"] = int(s.get("fps", 60))
        cfg["bitrate_mbps"] = int(s.get("bitrate_mbps", 40))
        cfg["rate_control"] = str(s.get("rate_control", "vbr") or "vbr")
        cfg["quality"] = int(s.get("quality", 20) or 20)
        cfg["encoder"] = str(s.get("encoder", "h264_nvenc"))
        cfg["preset"] = str(s.get("preset", "p4"))
        cfg["backend"] = str(s.get("backend", "auto") or "auto")
        cfg["position"] = str(s.get("position", "right") or "right")
        cfg["pos_x"] = int(s.get("pos_x", 0) or 0)
        cfg["pos_y"] = int(s.get("pos_y", 0) or 0)
        cfg["draw_mouse"] = bool(s.get("draw_mouse", True))
        cfg["touch"] = bool(s.get("touch", True))
        cfg["match_phone"] = bool(s.get("match_phone", True))
        cfg["mode"] = str(s.get("mode", "extend"))
        cfg["install_apk"] = True
        cfg["reinstall_apk"] = bool(s.get("reinstall_apk", False))
        cfg["tray_on_close"] = bool(s.get("close_action", "ask") != "quit")
        cfg["autostart"] = bool(s.get("autostart", False))
        cfg["vdd_off_mode"] = str(s.get("vdd_off_mode", "deep") or "deep")
        if cfg["mode"] == "extend":
            cfg["bitrate_mbps"] = max(cfg["bitrate_mbps"], 4)
        self._sync_autostart()

    # -------------------------------------------------------------- status line
    def _tick_loop(self):
        tick = 0
        while not self._closing:
            try:
                self._refresh_status()
                tick += 1
                if tick == 6:                       # ~3 s in: the page has painted
                    self.auto_check_update()
                if tick % 8 == 0:                   # every ~4 s: cable plugged in
                    self._refresh_device()
            except Exception:
                pass
            time.sleep(0.5)

    def _refresh_status(self):
        s = self.session
        if s is None:
            return
        if getattr(s, "fatal", None):
            self.dot, self.note = "red", s.fatal
            self._clear_metrics()
            return
        if s.server and not s.running and self.streaming:
            self.dot = "yellow"
            self.note = i18n.t("采集中断，正在自动重连…", "Capture interrupted, reconnecting...")
            self._clear_metrics()
            return
        if s.running and s.server:
            st = s.stats
            linked = bool(s.server.connected)
            self.dot = "green" if linked else "idle"
            self.note = "" if linked else i18n.t("等待手机连接…", "waiting for the phone...")
            self.metrics["cap"] = "%.0f" % st.get("fps", 0.0)
            self.metrics["send"] = "%.1f" % st.get("mbps", 0.0)
            cs = getattr(s.server, "client_stats", None)
            age = time.time() - (getattr(s.server, "client_stats_at", 0.0) or 0.0)
            if cs and age < 6.0:
                self.metrics["phone"] = "%d" % cs.get("shown_fps", 0)
                self.metrics["lat"] = "%d" % cs.get("latency_ms", 0)
            else:
                self.metrics["phone"] = "-"
                self.metrics["lat"] = "-"
            try:
                self.display = s.target or self.display
            except Exception:
                pass
        else:
            self.dot = "idle"
            self._clear_metrics()

    def _clear_metrics(self):
        self.metrics = {"cap": "-", "send": "-", "phone": "-", "lat": "-"}

    def summary(self):
        s = self.settings
        enc = str(s.get("encoder", "h264_nvenc"))
        rc = str(s.get("rate_control", "vbr") or "vbr").upper()
        if rc.startswith("VBR"):
            rc = "VBR"
        return "%s · %s · %dx%d@%d" % (enc, rc, int(s.get("width", 1920)),
                                       int(s.get("height", 1080)),
                                       int(s.get("hz", 60)))

    # ------------------------------------------------------------- identity
    @property
    def app_root(self):
        return HOST

    @property
    def app_name(self):
        return APP_NAME

    @property
    def version(self):
        return APP_VERSION

    # ---------------------------------------------------------- device actions
    def install_driver(self):
        self.busy = True
        threading.Thread(target=self._install_driver_work, name="dx-driver",
                         daemon=True).start()

    def _install_driver_work(self):
        import vdd as vd
        try:
            stt = vd.status() or {}
            if stt.get("state") == "node" and not stt.get("enabled"):
                self.log("驱动已经装好，只是处于关闭状态，现在打开它…")
                r = vd.set_enabled(True, log=self.log)
            else:
                self.log("正在安装虚拟显示器驱动（会弹一次 UAC，请点“是”）…")
                r = vd.install(log=self.log)
            ok, out = self._ok2(r)
            if not ok:
                self.log("驱动操作没有成功：%s" % (out or "").strip().splitlines()[:1], "err")
            self.driver = vd.status() or self.driver
            self.refresh_env()
        except Exception as e:
            self.log("驱动操作出错: %s" % e, "err")
        finally:
            self.busy = False

    def uninstall_driver(self):
        self.busy = True
        threading.Thread(target=self._uninstall_driver_work, name="dx-uninst",
                         daemon=True).start()

    def _uninstall_driver_work(self):
        import vdd as vd
        try:
            self.log("正在卸载虚拟显示器驱动（会弹一次 UAC，请点“是”）…")
            ok, out = self._ok2(vd.uninstall(log=self.log))
            if not ok:
                self.log("卸载没有成功：%s" % (out or "").strip().splitlines()[:1], "err")
            self.driver = vd.status() or self.driver
            self.refresh_env()
        except Exception as e:
            self.log("卸载出错: %s" % e, "err")
        finally:
            self.busy = False

    @staticmethod
    def _ok2(r):
        """vdd 的函数有的返回 (ok, text)，有的只返回 bool。"""
        if isinstance(r, tuple):
            return bool(r[0]), (r[1] if len(r) > 1 else "")
        return bool(r), ""

    def diagnose(self):
        threading.Thread(target=self._diagnose_work, name="dx-diag",
                         daemon=True).start()

    def _diagnose_work(self):
        import adb
        self.log("—— 连接诊断 ——")
        devs = []
        a = None
        try:
            a = adb.Adb()
            self.log("  adb %s" % a.version())
            devs = a.devices()
        except Exception as e:
            self.log("  adb 不可用：%s" % e, "err")
        for d in devs:
            self.log("  手机：%s" % d["label"])
        for d in devs:
            if d["state"] != "device":
                continue
            try:
                info = adb.phone_info(a, d["serial"])
                self.log("    %s" % adb.describe_phone(info, d["serial"]))
            except Exception:
                pass
        if not devs:
            self.log("  没有检测到手机", "err")
            raw = getattr(a, "last_devices_out", "") if a is not None else ""
            for line in [l.strip() for l in raw.splitlines()[1:] if l.strip()]:
                self.log("    " + line)
            h = adb.usb_mode_hint(devs)
            if h:
                self.log("  " + h)
            for status, _cls, name in adb.android_problem_devices():
                self.log("  设备管理器：%s（%s）" % (name, status))
        hdc = adb.hdc_conflict()
        if hdc:
            self.log("  " + hdc, "err")
        busy = adb.port_listeners()
        for port in adb.PORTS:
            who = busy.get(port)
            if who is None:
                self.log("  端口 %d：空闲" % port)
            elif "adb" in who.lower():
                self.log("  端口 %d：%s（adb 的转发，正常）" % (port, who))
            else:
                self.log("  端口 %d 被 %s 占用，投屏起不来，先关掉它" % (port, who), "err")
        self.log("—— 诊断结束 ——")

    def linkspeed(self):
        threading.Thread(target=self._linkspeed_work, name="dx-link",
                         daemon=True).start()

    def _linkspeed_work(self):
        import linkspeed
        from adb import Adb
        try:
            adb = Adb()
            rows = [r for r in adb.devices() if r["state"] == "device"]
            if not rows:
                self.log("没有可用的安卓设备，先插好数据线", "err")
                return
            if self.session and self.session.running:
                self.log("投屏正在进行，测速结果会偏低，建议先停止投屏再测。", "err")
            serial = rows[0]["serial"]
            self.log("正在测量 %s 的 USB 链路（约 6 秒）…" % serial)
            r = linkspeed.measure(adb, serial, log=self.log)
            if r.get("error"):
                self.log("链路测速失败: %s" % r["error"], "err")
                return
            v = linkspeed.verdict(r["mbps"])
            cap = max(2, int(r["mbps"] * 0.75))
            self.log("链路 %.1f Mbps（%s）→ 建议码率上限不超过 %d Mbps"
                     % (r["mbps"], v, cap), "ok")
            if int(self.settings.get("bitrate_mbps", 40) or 40) > cap:
                self.settings["bitrate_mbps"] = cap
                self._save()
                self.log("当前码率上限高于链路可承载，已自动下调到 %d Mbps" % cap, "err")
        except Exception as e:
            self.log("测速出错: %s" % e, "err")

    def test_notify(self):
        try:
            import notify as nf
            self.log("发一条测试通知…")
            nf.toast(APP_NAME, i18n.t("这是一条测试通知。看到它就说明通知能正常工作。",
                                      "This is a test notification."), log=self.log)
        except Exception as e:
            self.log("通知发送失败: %s" % e, "err")

    def check_update(self, manual=True):
        """Ask GitHub.  manual=True is the button; False is the start-up check."""
        if self._upd_busy:
            if manual:
                self.log("上一次检查还没结束，稍等一下。")
            return False
        self._upd_busy = True
        threading.Thread(target=self._check_update_work, args=(bool(manual),),
                         name="dx-upd", daemon=True).start()
        return True

    def auto_check_update(self):
        """The check that used to run 2.5 s after the Tk window appeared.

        Once per run, only with the setting on and the terms already behind us:
        the first start, with a privacy dialog on screen, is not the moment to
        also throw a version window at someone.
        """
        if self._upd_auto or self._closing:
            return
        self._upd_auto = True
        if not bool(self.settings.get("check_updates", True)):
            return
        try:
            import privacy as pv
            if pv.needs_accept(self.settings):
                return
        except Exception:
            pass
        self.check_update(manual=False)

    def _check_update_work(self, manual):
        import update as upd
        pre = bool(self.settings.get("check_prerelease", False))
        self.log("正在向 GitHub 查询最新版本%s…" % ("（含测试版）" if pre else ""))
        try:
            r = upd.check(prerelease=pre)
        except Exception as e:
            r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        self._upd_busy = False
        if not r.get("ok"):
            err = str(r.get("error") or "").replace("\n", " ")
            self.log("检查更新失败：%s" % err, "err")
            if manual:
                self.upd_msg = {
                    "kind": "error", "at": time.time(),
                    "text": "查询 GitHub 失败：%s\n\n（离线或被墙都不影响正常使用，"
                            "可以手动打开：%s）" % (err, RELEASES_URL)}
            return
        if not r.get("newer"):
            self.log("已是最新版本 v%s。" % APP_VERSION, "ok")
            if manual:
                self.upd_msg = {"kind": "latest", "at": time.time(),
                                "text": "已经是最新版本 v%s。" % APP_VERSION}
            return
        tag = i18n.t("（测试版）", " (beta)") if r.get("pre") else ""
        self.log("发现新版本 v%s%s（当前 v%s）：%s"
                 % (r["latest"], tag, APP_VERSION, r["url"]), "ok")
        info = {"latest": r["latest"], "url": r["url"], "pre": bool(r.get("pre")),
                "notes": r.get("notes") or "", "setup": r.get("setup") or None}
        if manual:
            self.update_info = info
            return
        # The start-up check: the balloon always, the window only when nothing
        # is streaming -- a box appearing over a live stream is the one
        # interruption that actually costs something.
        self._update_notify(r, tag)
        key = str(r["latest"]) + tag
        if self.streaming:
            return
        if str(self.settings.get("update_offered", "") or "") == key:
            return
        self.settings["update_offered"] = key
        self._save()
        self.update_info = info

    def _update_notify(self, r, tag):
        if not self.settings.get("notify", True):
            return
        if not self.settings.get("notify_update", True):
            return
        try:
            import notify as nf
            nf.toast(i18n.t("DroidExtend v%s 可以用了", "DroidExtend v%s is ready")
                     % (r["latest"] + tag),
                     i18n.t("你现在是 v%s。「检查更新」可以现在就装。",
                            "You are on v%s. Check for updates installs it now.")
                     % APP_VERSION, log=self.log)
        except Exception:
            pass

    def install_update(self):
        """Download the installer the check found, verify it, then hand over."""
        info = self.update_info or {}
        setup = info.get("setup") or {}
        if self._upd_dl_busy:
            return False
        if not info.get("latest"):
            self.log("还没检查到可用的新版本。", "err")
            return False
        if not setup.get("url"):
            self.log("这一版没有附带安装包，只能打开下载页面。", "err")
            try:
                os.startfile(info.get("url") or RELEASES_URL)
            except Exception:
                pass
            return False
        self._upd_dl_busy = True
        self.upd = {"state": "downloading", "done": 0,
                    "total": int(setup.get("size") or 0), "error": ""}
        threading.Thread(target=self._install_update_work, args=(info, setup),
                         name="dx-dl", daemon=True).start()
        return True

    def _install_update_work(self, info, setup):
        import update as upd
        name = setup.get("name") or ("DroidExtend-Setup-%s.exe" % info.get("latest"))
        total = int(setup.get("size") or 0)
        try:
            where = upd.update_dir(str(self.settings.get("update_dir", "") or ""))
            dest = os.path.join(where, name)
            self.log("开始下载 %s（%d 字节）→ %s" % (name, total, where))

            def prog(done, tot):
                self.upd = {"state": "downloading", "done": int(done or 0),
                            "total": int(tot or total or 0), "error": ""}

            res = upd.download(setup["url"], dest, sha256=setup.get("sha256", ""),
                               size=total, on_progress=prog)
            if not res.get("ok"):
                raise RuntimeError(str(res.get("error") or "").replace("\n", " "))
            self.log("下载完成并通过校验：%s（sha256 %s）"
                     % (res["path"], res["sha256"]), "ok")
            self.upd = {"state": "installing", "done": total, "total": total,
                        "error": ""}
            ok, err, script = upd.run_installer(
                res["path"],
                install_dir=(os.path.dirname(HOST)
                             if getattr(sys, "frozen", False) else ""),
                delete_installer=True,
                work_dir=str(self.settings.get("update_dir", "") or ""))
            if not ok:
                raise RuntimeError(str(err or "安装程序没能启动"))
            self.log("即将退出，由 %s 安装新版本并重新启动。" % script, "ok")
            try:
                self.session.skip_vdd_off = True
            except Exception:
                pass
            self._upd_dl_busy = False
            self.upd = {"state": "quitting", "done": total, "total": total,
                        "error": ""}
            cb = self.on_quit
            if cb is not None:
                try:
                    cb()
                except Exception:
                    pass
        except Exception as e:
            msg = str(e).replace("\n", " ")
            self._upd_dl_busy = False
            self.upd = {"state": "error", "done": 0, "total": 0, "error": msg}
            self.log("更新没有继续下去：%s" % msg, "err")

    def save_log(self):
        try:
            base = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.isdir(base):
                base = os.path.expanduser("~")
            path = os.path.join(base, "DroidExtend-%s.log" % time.strftime("%Y%m%d-%H%M%S"))
            with open(path, "w", encoding="utf-8") as f:
                for l in self.log_lines:
                    f.write("[%s] %s\n" % (l["t"], l["m"]))
            self.log("日志已保存: %s" % path, "ok")
            return path
        except Exception as e:
            self.log("保存日志失败: %s" % e, "err")
            return ""

    # ------------------------------------------------------------------ actions
    # ------------------------------------------------------------ documents
    # ------------------------------------------- leftover screens and tools
    def cleanup_orphans(self):
        threading.Thread(target=self._cleanup_orphans_work, name="dx-orphan",
                         daemon=True).start()

    def _cleanup_orphans_work(self):
        try:
            if self.session is not None and getattr(self.session, "running", False):
                self.log("正在投屏，先停止再清理")
                try:
                    self.session.stop()
                except Exception as e:
                    self.log("停止投屏失败: %s" % e)
            gone = wd.cleanup_orphans(log=self.log)
        except Exception as e:
            self.log("清理失败: %s" % e, "err")
            return
        if gone:
            self.log("已关闭 %d 块残留的虚拟屏：%s" % (len(gone), "、".join(gone)))
            try:
                self.session._display_applied = None
            except Exception:
                pass
        else:
            self.log("没有需要清理的虚拟屏")
        self.refresh_env()

    def vdd_off(self):
        threading.Thread(target=self._vdd_off_work, name="dx-vddoff",
                         daemon=True).start()

    def _vdd_off_work(self):
        import vdd as vd
        try:
            if self.session is not None and getattr(self.session, "running", False):
                self.log("正在投屏，先停止再关闭")
                try:
                    self.session.stop()
                except Exception as e:
                    self.log("停止投屏失败: %s" % e)
            stt = vd.status() or {}
            if stt.get("state") not in ("ready", "node"):
                self.log("虚拟屏还没装好，没有可关闭的东西")
                return
            ok, text = self._ok2(vd.set_enabled(False, log=self.log))
            if ok:
                try:
                    self.session._display_applied = None
                except Exception:
                    pass
                self.log(i18n.t(
                    "虚拟屏已彻底关闭：开机不会有这块屏，远程软件也看不到它",
                    "The virtual display is switched off for real: it will not appear "
                    "at boot and remote software will not see it"))
                self.driver = vd.status() or self.driver
                self.refresh_env()
                return
            self.log(i18n.t(
                "彻底关闭没成功（%s），至少先把它移出桌面",
                "Could not switch it off for real (%s); at least taking it off the "
                "desktop") % (str(text)[:200] or i18n.t("未知原因", "unknown reason")))
            gone = wd.cleanup_orphans(log=self.log)
            if gone:
                self.log("已关闭 %d 块残留的虚拟屏：%s" % (len(gone), "、".join(gone)))
            self.refresh_env()
        except Exception as e:
            self.log("关闭虚拟屏出错: %s" % e, "err")

    def install_usb_driver(self):
        threading.Thread(target=self._install_usb_driver_work,
                         name="dx-usbdrv", daemon=True).start()

    def _install_usb_driver_work(self):
        import webbrowser
        import adb
        try:
            done, msg = adb.install_usb_driver()
            if done:
                self.log(msg)
                return
            self.log(msg, "err")
            self.log("  先试试这个：手机下拉通知栏，把「USB 用途」改成「传输文件 / MTP」，"
                     "Windows 多半会自己把驱动装上。")
            webbrowser.open(adb.DRIVER_URL)
        except Exception as e:
            self.log("安装 USB 驱动出错: %s" % e, "err")

    def install_apk(self):
        threading.Thread(target=self._install_apk_work, name="dx-apk",
                         daemon=True).start()

    def _install_apk_work(self):
        try:
            self.log("正在把手机端 App 装到手机上（会先卸载旧版本）…")
            self.session.cfg["install_apk"] = True
            self.session.cfg["reinstall_apk"] = True
            self.session.find_device()
            self.session.prepare_device()
            self.log("手机端 App 已就绪")
        except Exception as e:
            self.log("安装手机端 App 失败: %s" % e, "err")
        finally:
            self.refresh_env()

    def open_dir(self, which):
        base = os.path.dirname(HOST)
        d = base if which == "root" else paths_mod.data_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
            return True
        except Exception as e:
            self.log("打不开 %s：%s" % (d, e), "err")
            return False

    # ------------------------------------------- 使用条款与隐私（2.4.2）
    @property
    def terms_pending(self):
        """True when this version's terms have not been accepted yet."""
        try:
            import privacy as pv
            return pv.needs_accept(self.settings)
        except Exception:
            return False

    def accept_terms(self):
        """Record the acceptance, the way the Tk dialog used to."""
        try:
            import privacy as pv
            pv.accept(self.settings)
            self._save()
            self.log("已接受使用条款与隐私说明（第 %s 版）。" % pv.VERSION, "ok")
            return True
        except Exception as e:
            self.log("记录条款同意失败: %s" % e, "err")
            return False

    # ------------------------------------------- 开机后自动开始投屏（1.9.0）
    def auto_stream(self):
        """被 --stream 拉起来时，先判断这一次该不该自动开始投屏。

        真正的等待在 _auto_stream_work 里：开机那一刻 adb 服务、虚拟屏驱动、
        手机都还在醒来的路上，试一次就报错没有任何意义。
        """
        if getattr(self, "_auto_begun", False):
            return
        self._auto_begun = True
        if not self.settings.get("auto_stream", False):
            self.log("命令行带了 --stream，但设置里「开机后自动开始投屏」是关的，这次不自动开始。")
            return
        if self.terms_pending:
            self.log("开机自动投屏没有开始：还没看过并同意使用条款。", "err")
            self._auto_notify("自动投屏没有开始", "先打开 DroidExtend 看一遍并同意使用条款。")
            return
        wait = int(self.settings.get("auto_stream_wait", 60) or 0)
        self.log("开机自动投屏：开始等手机（%s）。"
                 % ("会一直等到手机接上为止" if wait <= 0 else "最多等 %d 秒" % wait))
        threading.Thread(target=self._auto_stream_work, args=(wait,),
                         name="dx-auto", daemon=True).start()

    def _auto_stream_work(self, wait):
        """等手机：等到就开始，等不到就发一条系统通知。"""
        t0 = time.time()
        last = ""
        while not self._closing and not self._auto_cancel.is_set():
            if wait > 0 and time.time() - t0 >= wait:
                self.log("开机自动投屏：等了 %d 秒还没等到手机，这次算了。" % wait, "err")
                self._auto_notify("自动投屏没有开始",
                                  "等手机等了 %d 秒。连上数据线后点「开始投屏」。" % wait)
                return
            if self.busy or self.streaming:
                self._auto_cancel.wait(0.5)
                continue
            try:
                row, why = self.session.probe_device()
            except Exception as e:
                row, why = None, str(e)
            if row is not None:
                self.log("开机自动投屏：手机到了（%s），开始投屏。"
                         % (row.get("label") or row.get("serial")), "ok")
                self.start()
                return
            if why and why != last:
                last = why
                self.log("开机自动投屏：还在等 —— %s" % why)
            self._auto_cancel.wait(3.0)

    def _auto_notify(self, title, body):
        if not self.settings.get("notify", True):
            return
        if not self.settings.get("notify_fail", True):
            return
        try:
            import notify as nf
            nf.toast(title, body, log=self.log)
        except Exception:
            pass

    def _sync_autostart(self):
        """Keep the registry task in step with the two ticks on the boot page."""
        s = self.settings
        try:
            import autostart as auto
            want = bool(s.get("autostart", False))
            extra = []
            if want and (str(s.get("close_action", "ask")) == "tray"
                         or bool(s.get("auto_stream", False))):
                extra.append("--minimized")
            if want and bool(s.get("auto_stream", False)):
                extra.append("--stream")
            key = " ".join(extra)
            if want:
                if s.get("auto_extra", "") != key or not auto.enabled():
                    cmd = auto.write(extra)
                    s["auto_extra"] = key
                    self._save()
                    self.log("已设置开机自动启动：%s" % cmd)
            elif auto.enabled():
                auto.remove()
                s["auto_extra"] = ""
                self._save()
                self.log("已取消开机自动启动。")
        except Exception as e:
            self.log("设置开机自启动失败：%s" % e, "err")

    def privacy(self):
        import privacy as pv
        out = []
        for tag, text in pv._lines():
            if tag == "h":
                if out:
                    out.append("")
                out.append(text)
                out.append("")
            else:
                out.append(text)
        return {"title": i18n.t("隐私政策与使用条款", "Privacy and terms"),
                "body": "\n".join(out),
                "links": [[i18n.t("项目主页", "Project home"), REPO_URL],
                          [i18n.t("反馈问题", "Report a problem"), ISSUES_URL]]}

    def doc(self, which):
        """Long text the page shows in its own reader: manual, changelog, ...

        The Tk window opened a separate Toplevel with a Text widget for each of
        these.  The page needs the same words, so it asks for them by name and
        draws them itself.
        """
        which = str(which or "").strip().lower()
        if which == "manual":
            return {"title": i18n.t("使用说明", "Manual"),
                    "body": mn.text(),
                    "links": [[i18n.t("项目主页", "Project home"), REPO_URL],
                              [i18n.t("反馈问题", "Report a problem"), ISSUES_URL]]}
        if which == "changelog":
            return {"title": i18n.t("更新日志", "Changelog"),
                    "body": cl.all_text(), "links": []}
        if which == "about":
            return {"title": i18n.t("关于 %s", "About %s") % APP_NAME,
                    "body": self._about_text(), "links": []}
        if which == "license":
            p = os.path.join(HOST, LICENSES)
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        body = f.read()
                except Exception as e:
                    body = str(e)
                return {"title": i18n.t("第三方组件许可", "Third-party licences"),
                        "body": body, "links": []}
            return {"title": i18n.t("第三方组件许可", "Third-party licences"),
                    "body": i18n.t(
                        "没有找到 %s。\n\n它随安装包一起分发，来源见：\n%s",
                        "%s was not found.\n\nIt ships with the installer; "
                        "sources are at:\n%s") % (LICENSES, REPO_URL),
                    "links": [[i18n.t("项目主页", "Project home"), REPO_URL]]}
        return {"title": "", "body": "", "links": []}

    def _about_text(self):
        """What the Tk build put in its modal About box, as plain text."""
        lines = [
            "%s — %s" % (APP_NAME, app_title()),
            i18n.t("版本 v%s  (%s)", "Version v%s  (%s)") % (APP_VERSION, APP_DATE),
            "",
            i18n.t("用一根 USB 数据线，把安卓手机/平板变成 Windows 的第二块显示器。",
                   "Turns an Android phone or tablet into a second monitor for "
                   "Windows, over a single USB cable."),
            i18n.t("真扩展桌面，不是投屏镜像；支持触摸反控。",
                   "A real extended desktop, not a mirror; touch is passed back "
                   "to the PC."),
            "",
            i18n.t("代码作者：%s", "Code by: %s") % AUTHOR,
            i18n.t("项目所有者：%s", "Project owner: %s") % OWNER,
            i18n.t("仓库：%s", "Repository: %s") % REPO_URL,
            i18n.t("反馈：%s", "Issues: %s") % ISSUES_URL,
            i18n.t("邮箱：%s", "Email: %s") % " / ".join(EMAILS),
            "",
            i18n.t("—— 用到的第三方组件 ——", "-- Third-party components --"),
        ]
        for comp, lic, src in credits():
            lines.append(i18n.t("· %s（%s）  %s", "- %s (%s)  %s") % (comp, lic, src))
        lines += ["",
                  i18n.t("完整许可与出处见「第三方组件许可」（程序目录下的《%s》）。",
                         "Full licences and sources: the file %s in the program "
                         "folder.") % LICENSES]
        return "\n".join(lines)

    def reset_settings(self):
        """Every field back to its default, and the saved file deleted."""
        try:
            p = st.path()
            if os.path.exists(p):
                os.remove(p)
        except Exception as e:
            self.log("删除设置文件失败: %s" % e, "err")
        self.settings = st.load()
        self._save()
        self.refresh_env()
        self.log("设置已重置为默认值。", "ok")
        return True

    def start(self):
        self._auto_cancel.set()
        self.apply_to_cfg()
        self._save()
        self.busy = True
        self.stat = "connecting"
        threading.Thread(target=self._start_work, name="dx-start", daemon=True).start()

    def _start_work(self):
        try:
            self.log("连接手机…")
            self.session.find_device()
            self.log("准备拓展屏…")
            self.session.apply_display()
            self.session.prepare_device()
            self.log("启动编码…")
            self.session.start_stream()
            self.streaming = True
            self.stat = "streaming"
            self.log("=== 投屏已开始：%s ===" % self.session.describe(), "ok")
        except Exception as e:
            self.stat = "ready"
            self.streaming = False
            self.log("开始失败: %s" % e, "err")
            self.log(traceback.format_exc().strip().splitlines()[-1], "err")
        finally:
            self.busy = False

    def stop(self):
        self._auto_cancel.set()
        self.busy = True
        self.stat = "stopping"
        threading.Thread(target=self._stop_work, name="dx-stop", daemon=True).start()

    def _stop_work(self):
        try:
            self.session.stop()
            self.log("已停止：虚拟屏从桌面摘下，驱动保持就绪", "ok")
        except Exception as e:
            self.log("停止时出错: %s" % e, "err")
        finally:
            self.streaming = False
            self.stat = "stopped"
            self.busy = False

    def snapshot(self):
        s = self.settings
        stat_text = {
            "ready": i18n.t("就绪", "Ready"),
            "connecting": i18n.t("连接手机…", "Connecting..."),
            "streaming": i18n.t("投屏中", "Streaming"),
            "stopping": i18n.t("正在停止…", "Stopping..."),
            "stopped": i18n.t("已停止", "Stopped"),
        }.get(self.stat, "")
        return {
            "stat": self.stat,
            "statText": stat_text,
            "dot": self.dot,
            "note": self.note,
            "device": self.device,
            "metrics": dict(self.metrics),
            "gpu": self.gpu,
            "display": self.display,
            "driver": dict(self.driver),
            "streaming": self.streaming,
            "busy": self.busy,
            "summary": self.summary(),
            "form": self.form(),
            "lang": self.lang,
            "version": APP_VERSION,
            "date": APP_DATE,
            "seq": self.log_seq,
            "update": self.update_info,
            "upd": dict(self.upd) if self.upd else None,
            "updMsg": self.upd_msg,
            "terms": self.terms_pending,
            "ask": self._ask_rec,
        }