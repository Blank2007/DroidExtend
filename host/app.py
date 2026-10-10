"""DroidExtend - turn an Android phone into a second monitor for Windows.

Everything is driven from this window: it installs the virtual display driver
if the machine does not have one, sets the extra screen to whatever size and
refresh rate you ask for, encodes it with ffmpeg, and pushes the stream down
the USB cable to the phone through an adb reverse tunnel.
"""
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox


class _NullStream:
    """Stand-in for a missing stdout/stderr."""

    def write(self, *_a):
        return 0

    def flush(self):
        return None

    def isatty(self):
        return False


# Both pythonw.exe (what the launcher uses) and a --windowed PyInstaller build
# leave sys.stdout/sys.stderr as None.  Anything that writes to them would then
# raise AttributeError *inside a worker thread* -- which swallows the real error
# and leaves `busy` stuck on, so the window stops responding.  Give them a sink
# before any of that can happen.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name, None) is None:
        setattr(sys, _name, _NullStream())

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import win_display as wd          # noqa: E402  (needs sys.path first)

DPI = wd.set_dpi_aware()          # must precede Tk: gives us real pixels

from capture import ENCODERS, NVENC_PRESETS, X264_PRESETS, list_hardware_encoders  # noqa: E402
from paths import ROOT, apk_path, adb_exe, ffmpeg_exe                             # noqa: E402
from session import Session, DEFAULTS, VddMissing                                  # noqa: E402

RESOLUTIONS = [
    (1280, 720), (1600, 900), (1920, 1080), (1920, 1200),
    (2160, 1080), (2560, 1440), (2560, 1600), (3840, 2160),
]
HZ_CHOICES = ["30", "60", "75", "90", "120", "144", "165", "240"]
FPS_CHOICES = ["24", "30", "40", "48", "50", "60", "75", "90", "120", "144"]
BITRATE_CHOICES = ["4", "6", "8", "10", "12", "16", "20", "25", "30", "40", "50", "80", "120"]
QUALITY_CHOICES = ["14", "16", "18", "20", "22", "24", "26", "28", "32", "36"]
RATECONTROL = [("vbr", "VBR 质量优先（静止桌面省带宽）"), ("cbr", "CBR 恒定码率（运动画面更稳）")]
BACKENDS = [("auto", "自动（优先 ddagrab）"), ("ddagrab", "ddagrab（桌面复制，最快）"),
            ("gdigrab", "gdigrab（GDI，兼容性最好）")]
POSITIONS = [("right", "右侧"), ("left", "左侧"), ("above", "上方"), ("below", "下方")]

BG = "#14161a"
FG = "#e6e6e6"
ACCENT = "#4c9aff"
PANEL = "#1c1f26"


class App(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title("DroidExtend — 安卓拓展屏")
        self.configure(bg=BG)
        self.geometry("980x720")
        self.minsize(880, 640)

        self.logq = queue.Queue()
        self.session = Session(log=self.log)
        self.busy = False
        self._build()
        self.after(120, self._drain_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.log("DroidExtend 启动  (DPI: %s)" % DPI)
        self.log("adb: %s" % (adb_exe() or "缺失"))
        self.log("ffmpeg: %s" % (ffmpeg_exe() or "缺失"))
        apk = apk_path()
        self.log("APK: %s%s" % (apk or "(缺失)", "" if apk else "  ← 先运行 build_apk.bat 构建"))
        if not apk:
            self.log("提示：双击 build_apk.bat 可以重新构建手机端安装包。", "err")
        self.after(300, self.refresh_env)

    # ------------------------------------------------------------------ build
    def _build(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, fieldbackground=PANEL,
                        bordercolor="#2c313a", lightcolor=BG, darkcolor=BG)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Head.TLabel", font=("Segoe UI", 11, "bold"), foreground=ACCENT)
        style.configure("Dim.TLabel", foreground="#9aa4b2")
        style.configure("Mono.TLabel", font=("Consolas", 9), foreground="#9aa4b2")
        style.configure("Big.TLabel", font=("Segoe UI", 13, "bold"))
        style.configure("TButton", padding=(10, 5))
        style.configure("Go.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.configure("TLabelframe", background=BG, foreground=ACCENT)
        style.configure("TLabelframe.Label", background=BG, foreground=ACCENT)
        style.map("TButton", background=[("active", "#2a3038")])
        # ttk's clam theme paints read-only comboboxes white, which swallows our
        # light text (编码器/预设/采集 all looked like empty boxes).  Force the
        # field and the drop-down list back onto the dark palette.
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                        foreground=FG, arrowcolor=FG, bordercolor="#2c313a",
                        selectbackground=PANEL, selectforeground=FG,
                        lightcolor=PANEL, darkcolor=PANEL)
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL), ("disabled", "#171a1f")],
                  foreground=[("readonly", FG), ("disabled", "#6b7280")],
                  background=[("readonly", PANEL), ("active", "#2a3038")],
                  selectbackground=[("readonly", PANEL)],
                  selectforeground=[("readonly", FG)],
                  arrowcolor=[("disabled", "#6b7280"), ("!disabled", FG)])
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(2, weight=1)

        # -- left: device + driver -----------------------------------------
        left = ttk.Labelframe(outer, text=" 设备与显示器 ", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        left.columnconfigure(0, weight=1)

        row = ttk.Frame(left)
        row.grid(row=0, column=0, sticky="ew")
        ttk.Label(row, text="安卓设备:").pack(side="left")
        self.dev_var = tk.StringVar(value="未检测")
        self.dev_lbl = ttk.Label(row, textvariable=self.dev_var, style="Dim.TLabel")
        self.dev_lbl.pack(side="left", padx=8)
        ttk.Button(row, text="刷新", command=self.refresh_env, width=6).pack(side="right")

        self.drv_var = tk.StringVar(value="检测中…")
        ttk.Label(left, text="虚拟显示器:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(left, textvariable=self.drv_var, style="Dim.TLabel", wraplength=420,
                  justify="left").grid(row=2, column=0, sticky="w")

        btns = ttk.Frame(left)
        btns.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.btn_install = ttk.Button(btns, text="安装虚拟显示器驱动（需要管理员）",
                                      command=self.on_install_driver)
        self.btn_install.pack(side="left")
        ttk.Button(btns, text="仅安装 APK 并启动", command=self.on_install_apk).pack(side="left", padx=6)

        self.mon_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.mon_var, style="Mono.TLabel", justify="left",
                  wraplength=430).grid(row=4, column=0, sticky="w", pady=(8, 0))

        # -- right: quality --------------------------------------------------
        right = ttk.Labelframe(outer, text=" 画质与帧率 ", padding=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))
        right.columnconfigure(1, weight=1)
        right.columnconfigure(3, weight=1)

        def lab(r, c, t):
            ttk.Label(right, text=t).grid(row=r, column=c, sticky="w", pady=3, padx=(0, 4))

        self.mode_var = tk.StringVar(value="extend")
        lab(0, 0, "模式")
        mf = ttk.Frame(right)
        mf.grid(row=0, column=1, columnspan=3, sticky="w")
        for v, t in (("extend", "拓展屏"), ("mirror", "镜像")):
            ttk.Radiobutton(mf, text=t, value=v, variable=self.mode_var,
                            command=self._mode_changed).pack(side="left", padx=(0, 12))

        self.res_var = tk.StringVar(value="1920x1080")
        lab(1, 0, "分辨率")
        self.res_cb = ttk.Combobox(right, textvariable=self.res_var, width=14,
                                   values=["%dx%d" % r for r in RESOLUTIONS] + ["自定义"])
        self.res_cb.grid(row=1, column=1, sticky="w", pady=3)

        lab(1, 2, "刷新率")
        self.hz_var = tk.StringVar(value="60")
        self.hz_cb = ttk.Combobox(right, textvariable=self.hz_var, width=8, values=HZ_CHOICES)
        self.hz_cb.grid(row=1, column=3, sticky="w", pady=3)

        self.fps_var = tk.StringVar(value="60")
        lab(2, 0, "帧率 fps")
        self.fps_cb = ttk.Combobox(right, textvariable=self.fps_var, width=14, values=FPS_CHOICES)
        self.fps_cb.grid(row=2, column=1, sticky="w", pady=3)

        # Widget defaults come from DEFAULTS so there is a single source of
        # truth: _apply_form() pushes these back into cfg on every start.
        self.br_var = tk.StringVar(value=str(DEFAULTS["bitrate_mbps"]))
        lab(2, 2, "码率上限 Mbps")
        self.br_cb = ttk.Combobox(right, textvariable=self.br_var, width=8, values=BITRATE_CHOICES)
        self.br_cb.grid(row=2, column=3, sticky="w", pady=3)

        self.rc_var = tk.StringVar(value=RATECONTROL[0][1])
        lab(3, 0, "码率控制")
        self.rc_cb = ttk.Combobox(right, textvariable=self.rc_var, width=26, state="readonly",
                                  values=[t for _, t in RATECONTROL])
        self.rc_cb.grid(row=3, column=1, columnspan=1, sticky="w", pady=3)
        self.rc_cb.bind("<<ComboboxSelected>>", self._rc_changed)

        self.q_var = tk.StringVar(value="20")
        self.q_hint_var = tk.StringVar(value="")
        lab(3, 2, "画质 Q")
        self.q_cb = ttk.Combobox(right, textvariable=self.q_var, width=8, values=QUALITY_CHOICES)
        self.q_cb.grid(row=3, column=3, sticky="w", pady=3)

        self.custom_frame = ttk.Frame(right)
        self.w_var = tk.StringVar(value="1920")
        self.h_var = tk.StringVar(value="1080")
        ttk.Label(self.custom_frame, text="宽").pack(side="left")
        ttk.Entry(self.custom_frame, textvariable=self.w_var, width=6).pack(side="left", padx=3)
        ttk.Label(self.custom_frame, text="高").pack(side="left")
        ttk.Entry(self.custom_frame, textvariable=self.h_var, width=6).pack(side="left", padx=3)
        self.custom_frame.grid(row=4, column=1, columnspan=3, sticky="w", pady=3)
        self.custom_frame.grid_remove()
        self.res_cb.bind("<<ComboboxSelected>>", self._res_changed)

        self.enc_var = tk.StringVar(value="h264_nvenc")
        lab(5, 0, "编码器")
        self.enc_cb = ttk.Combobox(right, textvariable=self.enc_var, width=14, state="readonly")
        self.enc_cb.grid(row=5, column=1, sticky="w", pady=3)
        self.enc_cb.bind("<<ComboboxSelected>>", self._enc_changed)

        self.pre_var = tk.StringVar(value="p4")
        lab(5, 2, "预设")
        self.pre_cb = ttk.Combobox(right, textvariable=self.pre_var, width=8)
        self.pre_cb.grid(row=5, column=3, sticky="w", pady=3)

        self.be_var = tk.StringVar(value="auto")
        lab(6, 0, "采集")
        self.be_cb = ttk.Combobox(right, textvariable=self.be_var, width=14, state="readonly",
                                  values=[t for _, t in BACKENDS])
        self.be_cb.grid(row=6, column=1, sticky="w", pady=3)

        self.pos_var = tk.StringVar(value="right")
        lab(6, 2, "屏幕位置")
        self.pos_cb = ttk.Combobox(right, textvariable=self.pos_var, width=8, state="readonly",
                                   values=[t for _, t in POSITIONS])
        self.pos_cb.grid(row=6, column=3, sticky="w", pady=3)

        self.mouse_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text="显示鼠标指针", variable=self.mouse_var).grid(
            row=7, column=1, sticky="w", pady=3)
        self.inst_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text="每次重新安装 APK", variable=self.inst_var).grid(
            row=7, column=2, columnspan=2, sticky="w", pady=3)

        self.link_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.link_var, style="Dim.TLabel", wraplength=430,
                  justify="left").grid(row=8, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Button(right, text="测一下数据线速度", command=self.on_linkspeed).grid(
            row=9, column=1, sticky="w", pady=(4, 0))
        ttk.Label(right, textvariable=self.q_hint_var, style="Dim.TLabel", wraplength=430,
                  justify="left").grid(row=10, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # -- actions ---------------------------------------------------------
        act = ttk.Frame(outer)
        act.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.btn_start = ttk.Button(act, text="▶  开始投屏", style="Go.TButton", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(act, text="■  停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        self.stat_var = tk.StringVar(value="就绪")
        ttk.Label(act, textvariable=self.stat_var, style="Big.TLabel").pack(side="left", padx=16)
        self.perf_var = tk.StringVar(value="")
        ttk.Label(act, textvariable=self.perf_var, style="Mono.TLabel").pack(side="right")

        # -- log -------------------------------------------------------------
        logf = ttk.Labelframe(outer, text=" 日志 ", padding=6)
        logf.grid(row=2, column=0, columnspan=2, sticky="nsew")
        logf.rowconfigure(0, weight=1)
        logf.columnconfigure(0, weight=1)
        self.log_txt = tk.Text(logf, bg="#0f1115", fg="#c8d0da", insertbackground=FG,
                               font=("Consolas", 9), wrap="word", relief="flat", height=12)
        self.log_txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logf, command=self.log_txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_txt.configure(yscrollcommand=sb.set, state="disabled")
        self.log_txt.tag_configure("err", foreground="#ff7b72")
        self.log_txt.tag_configure("ok", foreground="#7ee787")
        self.log_txt.tag_configure("hl", foreground=ACCENT)

        self.after(900, self._poll_perf)
        self._load_encoders()
        self._rc_changed()

    # ------------------------------------------------------------------ utils
    def log(self, msg, tag=None):
        self.logq.put((time.strftime("%H:%M:%S ") + str(msg), tag))

    def _drain_log(self):
        try:
            while True:
                text, tag = self.logq.get_nowait()
                self.log_txt.configure(state="normal")
                self.log_txt.insert("end", text + "\n", tag or ())
                if int(self.log_txt.index("end-1c").split(".")[0]) > 800:
                    self.log_txt.delete("1.0", "200.0")
                self.log_txt.see("end")
                self.log_txt.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(120, self._drain_log)

    def _load_encoders(self):
        have = list_hardware_encoders()
        items = [k for k in ENCODERS if k in have] or ["libx264"]
        if "h264_nvenc" not in items and "libx264" in items:
            self.enc_var.set("libx264")
        self.enc_cb["values"] = ["%s — %s" % (k, ENCODERS[k]["label"]) for k in items]
        self._enc_items = items
        self.enc_cb.set("%s — %s" % (self.enc_var.get() if self.enc_var.get() in items else items[0],
                                     ENCODERS[self.enc_var.get() if self.enc_var.get() in items else items[0]]["label"]))
        self.log("可用编码器: " + ", ".join(items))
        self._enc_changed()

    def _enc_name(self):
        return self.enc_cb.get().split(" — ")[0].strip()

    def _enc_changed(self, *_):
        name = self._enc_name()
        self.enc_var.set(name)
        if name in ("libx264",):
            self.pre_cb["values"] = X264_PRESETS
            if self.pre_var.get() not in X264_PRESETS:
                self.pre_var.set("veryfast")
        else:
            self.pre_cb["values"] = NVENC_PRESETS
            if self.pre_var.get() not in NVENC_PRESETS:
                self.pre_var.set("p4")

    def _res_changed(self, *_):
        if self.res_var.get() == "自定义":
            self.custom_frame.grid()
        else:
            self.custom_frame.grid_remove()

    def _rate_control(self):
        label = self.rc_var.get()
        for v, t in RATECONTROL:
            if t == label:
                return v
        return "vbr"

    def _rc_changed(self, *_):
        rc = self._rate_control()
        if rc == "vbr":
            self.q_hint_var.set("Q 越小画质越好、码率越高（18≈视觉无损）")
        else:
            self.q_hint_var.set("CBR 下 Q 只作为上限，码率恒定占满上限")
        self.q_cb.configure(state="normal" if rc == "vbr" else "disabled")

    def on_linkspeed(self):
        self.run_bg(self._linkspeed_work)

    def _linkspeed_work(self):
        import linkspeed
        from adb import Adb
        self.after(0, lambda: self.stat_var.set("测量链路…"))
        adb = Adb()
        rows = [r for r in adb.devices() if r["state"] == "device"]
        if not rows:
            raise RuntimeError("没有可用的安卓设备，先插好数据线")
        serial = rows[0]["serial"]
        if self.session and self.session.running:
            # the stream and the probe fight over the same cable, so the number
            # comes out low for reasons that have nothing to do with the cable
            self.log("注意：投屏正在进行，测速结果会偏低，建议先停止投屏再测。", "err")
        self.log("正在测量 %s 的 USB 链路（约 6 秒）…" % serial)
        r = linkspeed.measure(adb, serial, log=self.log)
        if r["error"]:
            self.after(0, lambda: self.link_var.set("链路测速失败: " + r["error"]))
            self.after(0, lambda: self.stat_var.set("测速失败"))
            return
        v = linkspeed.verdict(r["mbps"])
        # leave a little headroom for protocol overhead and bursts
        cap = max(2, int(r["mbps"] * 0.75))
        text = "链路 %.1f Mbps（%s）→ 建议码率上限不超过 %d Mbps" % (r["mbps"], v, cap)
        self.after(0, lambda: self.link_var.set(text))
        self.after(0, lambda: self.stat_var.set("测速完成"))
        if int(self.br_var.get()) > cap:
            self.log("当前码率上限 %s Mbps 高于链路可承载的 %d Mbps，已自动下调。"
                     % (self.br_var.get(), cap), "err")
            self.after(0, lambda: self.br_var.set(str(cap)))
        self.log(text, "ok")

    def _mode_changed(self):
        pass

    def _backend(self):
        label = self.be_var.get()
        for v, t in BACKENDS:
            if t == label:
                return v
        return "auto"

    def _position(self):
        label = self.pos_var.get()
        for v, t in POSITIONS:
            if t == label:
                return v
        return "right"

    # ------------------------------------------------------------- background
    def run_bg(self, fn, done=None):
        if self.busy:
            messagebox.showinfo("请稍候", "上一个任务还在进行中。")
            return
        self.busy = True
        self.btn_start.configure(state="disabled")

        def work():
            err = None
            try:
                fn()
            except VddMissing as e:
                err = ("虚拟显示器未就绪：%s\n\n请先点“安装虚拟显示器驱动（需要管理员）”。" % e)
            except Exception as e:
                import traceback
                traceback.print_exc()
                err = "%s: %s" % (type(e).__name__, e)
            self.after(0, lambda: self._bg_done(err, done))

        threading.Thread(target=work, daemon=True).start()

    def _bg_done(self, err, done):
        self.busy = False
        # run_bg() greys the start button out for the duration of the job, so it
        # has to be handed back here.  Forgetting this left the very first
        # refresh_env() (fired 300ms after startup) with the button disabled for
        # good, and the UI could never start a stream.
        if not (self.session and self.session.running):
            self.btn_start.configure(state="normal")
        if err:
            self.log(err.replace("\n", " "), "err")
            self.stat_var.set("出错")
            # A start that dies half way (phone unplugged, driver refused the
            # mode) can leave the virtual display sitting on the desktop.  Take
            # it back off so "断开之后系统里还留着一块屏" cannot happen.
            try:
                if self.session and self.session.attached_display():
                    self.session.stop()
            except Exception:
                pass
            if done != "quiet":
                messagebox.showerror("出错了", err)
        if done and done != "quiet" and not err:
            done()

    # ------------------------------------------------------------------ env
    def refresh_env(self):
        self.run_bg(self._refresh_work, done="quiet")

    def _refresh_work(self):
        import vdd as vd
        from adb import Adb
        try:
            rows = Adb().devices()
        except Exception as e:
            rows = []
            self.log("adb 不可用: %s" % e)
        ready = [r for r in rows if r["state"] == "device"]
        if ready:
            self.after(0, lambda: self.dev_var.set(ready[0]["label"]))
        elif rows:
            self.after(0, lambda: self.dev_var.set(
                "已连接但未授权: " + ", ".join(r["serial"] + "=" + r["state"] for r in rows)))
        else:
            self.after(0, lambda: self.dev_var.set("未检测到设备（请用数据线连接并打开 USB 调试）"))

        st = vd.status()
        self.after(0, lambda: self.drv_var.set(vd.summary_line(st)))
        self.after(0, lambda: self.btn_install.configure(
            state="disabled" if st["state"] == "ready" else "normal"))

        mons = wd.live_monitors()
        text = "\n".join("%-12s %-28s %dx%d@%d  @(%d,%d)%s" % (
            m["name"], (m["monitor"] or m["desc"])[:28], m["width"], m["height"],
            m["hz"], m["left"], m["top"], "  [主]" if m["primary"] else "") for m in mons)
        self.after(0, lambda: self.mon_var.set("当前桌面显示器:\n" + (text or "（无）")))
        if st["state"] != "ready":
            self.log("虚拟显示器状态: " + vd.summary_line(st))

    # ---------------------------------------------------------------- actions
    def on_install_driver(self):
        if not messagebox.askyesno(
                "安装虚拟显示器驱动",
                "将安装 Virtual Display Driver 25.7.23（已签名）。\n\n"
                "接下来会弹出 Windows 的 UAC 授权窗口，请点击“是”，"
                "否则虚拟显示器无法创建。\n\n继续吗？"):
            return
        self.run_bg(self._install_driver_work)

    def _install_driver_work(self):
        import vdd as vd
        self.after(0, lambda: self.stat_var.set("安装驱动…"))
        ok, out = vd.install(log=self.log)
        for line in (out or "").splitlines()[-40:]:
            self.log("  " + line)
        if not ok:
            raise RuntimeError("驱动安装未成功，详见日志。")
        self.log("虚拟显示器驱动安装完成。", "ok")
        self.after(0, lambda: self.stat_var.set("驱动已安装"))
        self._refresh_work()

    def on_install_apk(self):
        self.run_bg(self._install_apk_work)

    def _install_apk_work(self):
        self.after(0, lambda: self.stat_var.set("安装 APK…"))
        self.session.cfg["install_apk"] = self.inst_var.get()
        self.session.find_device()
        self.session.prepare_device()
        self.after(0, lambda: self.stat_var.set("APK 已就绪"))

    def on_start(self):
        self._apply_form()
        self.run_bg(self._start_work)

    def _start_work(self):
        self.after(0, lambda: self.stat_var.set("准备拓展屏…"))
        self.session.apply_display()
        self.after(0, lambda: self.stat_var.set("连接手机…"))
        self.session.find_device()
        self.session.prepare_device()
        self.after(0, lambda: self.stat_var.set("启动编码…"))
        self.session.start_stream()
        self.log("=== 投屏已开始：%s ===" % self.session.describe(), "ok")
        self.after(0, self._started)

    def _started(self):
        self.stat_var.set("投屏中")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")

    def _apply_form(self):
        cfg = self.session.cfg
        if self.res_var.get() == "自定义":
            try:
                cfg["width"] = int(self.w_var.get())
                cfg["height"] = int(self.h_var.get())
            except ValueError:
                raise RuntimeError("自定义分辨率必须是整数")
        else:
            w, h = self.res_var.get().split("x")
            cfg["width"], cfg["height"] = int(w), int(h)
        cfg["hz"] = int(self.hz_var.get())
        cfg["fps"] = int(self.fps_var.get())
        cfg["bitrate_mbps"] = int(self.br_var.get())
        cfg["rate_control"] = self._rate_control()
        cfg["quality"] = int(self.q_var.get() or 20)
        cfg["encoder"] = self._enc_name()
        cfg["preset"] = self.pre_var.get()
        cfg["backend"] = self._backend()
        cfg["position"] = self._position()
        cfg["draw_mouse"] = bool(self.mouse_var.get())
        cfg["mode"] = self.mode_var.get()
        cfg["install_apk"] = bool(self.inst_var.get())
        if cfg["mode"] == "extend":
            cfg["bitrate_mbps"] = max(cfg["bitrate_mbps"], 4)

    def on_stop(self):
        self.btn_stop.configure(state="disabled")
        self.run_bg(self.session.stop, done=self._stopped)

    def _stopped(self):
        self.stat_var.set("已停止")
        self.perf_var.set("")
        self.btn_start.configure(state="normal")

    def _poll_perf(self):
        if self.session and self.session.running and self.session.server:
            s = self.session.stats
            conn = "手机已连接" if self.session.server.connected else "等待手机连接…"
            self.perf_var.set("%s   %.1f fps   编码 %.1f Mbps   送出 %d 帧   丢 %d" % (
                conn, s["fps"], s["mbps"], s["sent"], s["dropped"]))
        self.after(700, self._poll_perf)

    def _on_close(self):
        try:
            if self.session.running or self.session.server:
                self.session.stop()
        except Exception:
            pass
        self.destroy()


def main():
    missing = []
    if not adb_exe():
        missing.append("adb")
    if not ffmpeg_exe():
        missing.append("ffmpeg")
    if missing:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("缺少组件", "找不到: " + ", ".join(missing))
        return 1
    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
