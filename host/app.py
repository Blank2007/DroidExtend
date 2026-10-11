"""DroidExtend - turn an Android phone into a second monitor for Windows.

Everything is driven from this window: it installs the virtual display driver
if the machine does not have one, sets the extra screen to whatever size and
refresh rate you ask for, encodes it with ffmpeg, and pushes the stream down
the USB cable to the phone through an adb reverse tunnel.
"""
import os
import queue
import re
import shutil
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox, filedialog


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
import applog                                                                     # noqa: E402
import manual as mn                                                               # noqa: E402
import paths                                                                      # noqa: E402
import settings as st                                                             # noqa: E402
import update as upd                                                              # noqa: E402
from version import (APP_NAME, APP_TITLE, APP_VERSION, APP_DATE, AUTHOR, OWNER,   # noqa: E402
                     EMAILS, REPO_URL, RELEASES_URL, ISSUES_URL)

RESOLUTIONS = [
    (1280, 720), (1600, 720), (1600, 900), (1920, 864), (1920, 1080),
    (1920, 1200), (2000, 1200), (2160, 1080), (2280, 1080), (2340, 1080),
    (2400, 1080), (2412, 1080), (2560, 1440), (2560, 1600), (2700, 1224),
    (2712, 1220), (2800, 1260), (3040, 1440), (3120, 1440), (3200, 1440),
    (3200, 2136), (3840, 2160),
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

# (component, licence, where it comes from) -- also mirrored in 使用说明 and in
# the README, because two of these are redistributed inside the installer and
# MIT asks for the notice to travel with the copy.
CREDITS = [
    ("Virtual Display Driver (VDD)", "MIT", "github.com/VirtualDrivers/Virtual-Display-Driver"),
    ("nefcon", "MIT", "github.com/nefarius/nefcon"),
    ("FFmpeg 6.0 (gyan.dev essentials)", "GPL-3.0", "ffmpeg.org"),
    ("Android SDK Platform-Tools / adb", "Apache-2.0", "developer.android.com/tools/releases/platform-tools"),
    ("R8 / D8 (Android build-tools)", "BSD-3-Clause", "r8.googlesource.com/r8"),
    ("Python", "PSF-2.0", "python.org"),
    ("Tcl/Tk", "BSD-style", "tcl.tk"),
    ("PyInstaller", "GPL-2.0 + bootloader exception", "pyinstaller.org"),
    ("Inno Setup（只用于生成安装包）", "Inno Setup License", "jrsoftware.org/isinfo.php"),
]

# Shipped next to DroidExtend.exe by the installer; MIT/Apache/BSD all want the
# notice to travel with the binary copy.
LICENSES = "第三方组件许可.txt"


def _label_for(pairs, key, default=""):
    for v, t in pairs:
        if v == key:
            return t
    return default


def _key_for(pairs, label, default=""):
    for v, t in pairs:
        if t == label:
            return v
    return default


def _safe_geometry(s, fallback="980x760"):
    """Only trust a saved geometry that is a sane size and on this screen."""
    m = re.match(r"^(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?$", str(s or "").strip())
    if not m:
        return fallback
    w, h = int(m.group(1)), int(m.group(2))
    if not (760 <= w <= 8000 and 540 <= h <= 6000):
        return fallback
    if m.group(3) is None:
        return "%dx%d" % (w, h)
    x, y = int(m.group(3)), int(m.group(4))
    if abs(x) > 12000 or abs(y) > 12000:
        return "%dx%d" % (w, h)
    return "%dx%d+%d+%d" % (w, h, x, y)


class App(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        # Read the settings before anything is built: every widget is created
        # with the value you used last time instead of a hard-coded default.
        self.settings = st.load()
        self.upd_busy = False
        self._manual_win = None
        self._manual_txt = None

        self.title("%s — %s   v%s" % (APP_NAME, APP_TITLE, APP_VERSION))
        self.configure(bg=BG)
        self.geometry(_safe_geometry(self.settings.get("window")))
        self.minsize(900, 660)

        self.logq = queue.Queue()
        self.session = Session(log=self.log)
        self.busy = False
        self.refr_busy = False
        self._build_menu()
        self._build()
        self._apply_settings_to_form(self.settings)
        self._load_encoders()
        self.after(120, self._drain_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.log("%s v%s 启动  (DPI: %s)" % (APP_NAME, APP_VERSION, DPI))
        self.log("adb: %s" % (adb_exe() or "缺失"))
        self.log("ffmpeg: %s" % (ffmpeg_exe() or "缺失"))
        self.log("设置与日志目录: %s" % paths.data_dir())
        self.log("设置文件: %s%s" % (st.where(),
                                    "" if os.path.exists(st.where()) else "  （还没有，退出时会创建）"))
        self.log("日志文件: %s" % (applog.path(),))
        apk = apk_path()
        self.log("APK: %s%s" % (apk or "(缺失)", "" if apk else "  ← 先运行 build_apk.bat 构建"))
        if not apk:
            self.log("提示：双击 build_apk.bat 可以重新构建手机端安装包。", "err")
        self.after(300, self.refresh_env)
        if self.settings.get("check_updates", True):
            self.after(2500, lambda: self.on_check_updates(manual=False))

    # ------------------------------------------------------------------- menu
    def _build_menu(self):
        bar = tk.Menu(self, tearoff=0)

        m_file = tk.Menu(bar, tearoff=0)
        m_file.add_command(label="打开数据目录（设置与日志）", command=self.on_open_config_dir)
        m_file.add_command(label="更改数据目录…", command=self.on_change_data_dir)
        m_file.add_command(label="打开程序目录", command=self.on_open_root_dir)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self._on_close)
        bar.add_cascade(label="文件", menu=m_file)

        m_tool = tk.Menu(bar, tearoff=0)
        m_tool.add_command(label="安装虚拟显示器驱动…", command=self.on_install_driver)
        m_tool.add_command(label="仅安装 APK 并启动", command=self.on_install_apk)
        m_tool.add_separator()
        m_tool.add_command(label="测一下数据线速度", command=self.on_linkspeed)
        m_tool.add_separator()
        self.chk_var = tk.BooleanVar(value=bool(self.settings.get("check_updates", True)))
        m_tool.add_checkbutton(label="启动时自动检查更新", variable=self.chk_var,
                               command=self._save_settings)
        m_tool.add_command(label="检查更新…", command=lambda: self.on_check_updates(manual=True))
        m_tool.add_separator()
        m_tool.add_command(label="重置为默认设置", command=self.on_reset_settings)
        m_tool.add_separator()
        m_tool.add_command(label="复制全部日志到剪贴板", command=self.on_copy_log)
        m_tool.add_command(label="日志另存为…", command=self.on_save_log)
        bar.add_cascade(label="工具", menu=m_tool)

        m_help = tk.Menu(bar, tearoff=0)
        m_help.add_command(label="使用说明", command=self.on_manual)
        m_help.add_command(label="常见问题", command=lambda: self.on_manual("【六、常见问题】"))
        m_help.add_command(label="参数怎么选", command=lambda: self.on_manual("【五、参数怎么选】"))
        m_help.add_separator()
        m_help.add_command(label="项目主页（GitHub）", command=lambda: self.open_url(REPO_URL))
        m_help.add_command(label="反馈问题 / 提建议", command=lambda: self.open_url(ISSUES_URL))
        m_help.add_command(label="检查更新…", command=lambda: self.on_check_updates(manual=True))
        m_help.add_separator()
        m_help.add_command(label="第三方组件许可", command=self.on_licenses)
        m_help.add_command(label="关于 " + APP_NAME, command=self.on_about)
        bar.add_cascade(label="帮助", menu=m_help)

        self.configure(menu=bar)

    # --------------------------------------------------------------- settings
    def _apply_settings_to_form(self, s):
        """Push a settings dict into the widgets."""
        res = "%dx%d" % (int(s.get("width", 1920)), int(s.get("height", 1080)))
        known = ["%dx%d" % r for r in RESOLUTIONS]
        custom = bool(s.get("custom_res")) or res not in known
        self.res_var.set("自定义" if custom else res)
        self.w_var.set(str(s.get("width", 1920)))
        self.h_var.set(str(s.get("height", 1080)))
        self.mode_var.set(s.get("mode", "extend"))
        self.hz_var.set(str(s.get("hz", 60)))
        self.fps_var.set(str(s.get("fps", 60)))
        self.br_var.set(str(s.get("bitrate_mbps", 40)))
        self.rc_var.set(_label_for(RATECONTROL, s.get("rate_control", "vbr"), RATECONTROL[0][1]))
        self.q_var.set(str(s.get("quality", 20)))
        self.enc_var.set(s.get("encoder", "h264_nvenc"))
        self.pre_var.set(s.get("preset", "p4"))
        self.be_var.set(_label_for(BACKENDS, s.get("backend", "auto"), BACKENDS[0][1]))
        self.pos_var.set(_label_for(POSITIONS, s.get("position", "right"), POSITIONS[0][1]))
        self.mouse_var.set(bool(s.get("draw_mouse", False)))
        self.inst_var.set(bool(s.get("install_apk", True)))
        self.match_var.set(bool(s.get("match_phone", True)))
        if hasattr(self, "chk_var"):
            self.chk_var.set(bool(s.get("check_updates", True)))
        self._res_changed()
        self._rc_changed()

    def _form_values(self):
        """Snapshot the widgets into a settings dict.  Never raises."""
        s = dict(self.settings)
        try:
            if self.res_var.get() == "自定义":
                s["custom_res"] = "1"
                s["width"] = int(self.w_var.get())
                s["height"] = int(self.h_var.get())
            else:
                s["custom_res"] = ""
                w, h = self.res_var.get().split("x")
                s["width"], s["height"] = int(w), int(h)
        except Exception:
            pass
        for key, var in (("hz", self.hz_var), ("fps", self.fps_var),
                         ("bitrate_mbps", self.br_var), ("quality", self.q_var)):
            try:
                s[key] = int(var.get())
            except Exception:
                pass
        s["rate_control"] = self._rate_control()
        s["encoder"] = self._enc_name()
        s["preset"] = self.pre_var.get()
        s["backend"] = self._backend()
        s["position"] = self._position()
        s["draw_mouse"] = bool(self.mouse_var.get())
        s["install_apk"] = bool(self.inst_var.get())
        s["match_phone"] = bool(self.match_var.get())
        s["mode"] = self.mode_var.get()
        if hasattr(self, "chk_var"):
            s["check_updates"] = bool(self.chk_var.get())
        try:
            if self.state() == "normal":
                s["window"] = self.geometry()
        except Exception:
            pass
        return s

    def _save_settings(self, *_):
        try:
            self.settings = self._form_values()
            st.save(self.settings)
        except Exception as e:            # saving must never break the app
            self.log("保存设置失败: %s" % e, "err")

    def _changed_any(self, *_):
        self._save_settings()

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

        # Row 4 is shared: either "follow the phone's own resolution" or the
        # custom width/height entries, never both.  Following the phone is the
        # default because it is the only way to fill the panel edge to edge.
        self.match_var = tk.BooleanVar(value=bool(DEFAULTS.get("match_phone", True)))
        self.match_chk = ttk.Checkbutton(
            right, text="跟随手机分辨率（撑满整块屏，不留黑边）",
            variable=self.match_var, command=self._match_changed)
        self.match_chk.grid(row=4, column=0, columnspan=4, sticky="w", pady=3)
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
        self.mouse_chk = ttk.Checkbutton(right, text="显示鼠标指针", variable=self.mouse_var)
        self.mouse_chk.grid(row=7, column=1, sticky="w", pady=3)
        self.inst_var = tk.BooleanVar(value=True)
        self.inst_chk = ttk.Checkbutton(right, text="每次重新安装 APK", variable=self.inst_var)
        self.inst_chk.grid(row=7, column=2, columnspan=2, sticky="w", pady=3)

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
        ttk.Button(act, text="使用说明", command=self.on_manual).pack(side="right")
        ttk.Button(act, text="检查更新", command=lambda: self.on_check_updates(manual=True)).pack(
            side="right", padx=6)
        self.perf_var = tk.StringVar(value="")
        ttk.Label(act, textvariable=self.perf_var, style="Mono.TLabel").pack(side="right", padx=10)

        # -- log -------------------------------------------------------------
        logf = ttk.Labelframe(outer, text=" 日志 ", padding=6)
        logf.grid(row=2, column=0, columnspan=2, sticky="nsew")
        logf.rowconfigure(1, weight=1)
        logf.columnconfigure(0, weight=1)
        lbar = ttk.Frame(logf)
        lbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Button(lbar, text="复制全部", width=10, command=self.on_copy_log).pack(side="left")
        ttk.Button(lbar, text="另存为…", width=10, command=self.on_save_log).pack(side="left", padx=6)
        ttk.Button(lbar, text="清空", width=8, command=self.on_clear_log).pack(side="left")
        self.log_hint_var = tk.StringVar(value="")
        ttk.Label(lbar, textvariable=self.log_hint_var, style="Dim.TLabel").pack(side="right")
        self.log_txt = tk.Text(logf, bg="#0f1115", fg="#c8d0da", insertbackground=FG,
                               font=("Consolas", 9), wrap="word", relief="flat", height=12)
        self.log_txt.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logf, command=self.log_txt.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.log_txt.configure(yscrollcommand=sb.set, state="disabled")
        self.log_txt.tag_configure("err", foreground="#ff7b72")
        self.log_txt.tag_configure("ok", foreground="#7ee787")
        self.log_txt.tag_configure("hl", foreground=ACCENT)

        # -- status bar ------------------------------------------------------
        bar = ttk.Frame(outer)
        bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(bar, text="%s v%s" % (APP_NAME, APP_VERSION), style="Dim.TLabel").pack(side="left")
        ttk.Label(bar, text=REPO_URL, style="Dim.TLabel").pack(side="left", padx=12)
        self.file_var = tk.StringVar(value="数据目录: %s" % paths.data_dir())
        ttk.Label(bar, textvariable=self.file_var, style="Dim.TLabel").pack(side="right")

        # Any settings change is written straight out, so even a crash leaves
        # the next start on the settings you picked.  add="+" keeps the existing
        # handlers (which fix up presets and the Q field) running first.
        for cb in (self.res_cb, self.hz_cb, self.fps_cb, self.br_cb, self.rc_cb,
                   self.q_cb, self.enc_cb, self.pre_cb, self.be_cb, self.pos_cb):
            cb.bind("<<ComboboxSelected>>", self._changed_any, add="+")
        for chk in (self.mouse_chk, self.inst_chk):
            chk.configure(command=self._changed_any)

        self.after(900, self._poll_perf)

    # ------------------------------------------------------------------ utils
    def log(self, msg, tag=None):
        stamped = time.strftime("%H:%M:%S ") + str(msg)
        self.logq.put((stamped, tag))
        # The pane is not a record: it dies with the window, and the failures
        # worth reading about (driver refused, adb failed, capture restarted)
        # happen while nobody is looking at it.
        applog.write(stamped)

    def _later(self, fn):
        """Schedule onto the UI thread from a worker.

        Closing the window while a background job is still in flight used to
        raise "main thread is not in main loop" inside that thread, because
        self.after() on a destroyed interpreter is an error.  A closed window is
        not a failure, so swallow it.
        """
        try:
            self.after(0, fn)
        except (RuntimeError, tk.TclError):
            pass

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
        # Keep whatever the settings file asked for, as long as this machine can
        # actually encode with it; only fall back when it cannot.  (The old code
        # always forced h264_nvenc/libx264 and threw a saved choice away.)
        if self.enc_var.get() not in items:
            self.enc_var.set("h264_nvenc" if "h264_nvenc" in items else items[0])
        self.enc_cb["values"] = ["%s — %s" % (k, ENCODERS[k]["label"]) for k in items]
        self._enc_items = items
        self.enc_cb.set("%s — %s" % (self.enc_var.get(), ENCODERS[self.enc_var.get()]["label"]))
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
        custom = self.res_var.get() == "自定义"
        if custom:
            self.custom_frame.grid()
        else:
            self.custom_frame.grid_remove()
        if custom and self.match_var.get():
            # the two are mutually exclusive: picking a custom size means the
            # user wants that size, not whatever the phone reports
            self.match_var.set(False)
        self._sync_match()

    def _match_changed(self):
        if self.match_var.get() and self.res_var.get() == "自定义":
            self.res_var.set("%dx%d" % (RESOLUTIONS[0][0], RESOLUTIONS[0][1]))
        self._sync_match()

    def _sync_match(self):
        """Greying out the size box is the whole point: when the phone decides,
        the dropdown is a lie waiting to happen."""
        follow = self.match_var.get()
        self.res_cb.configure(state="disabled" if follow else "readonly")
        self.match_chk.grid() if not (self.res_var.get() == "自定义") else self.match_chk.grid_remove()

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
        self._later(lambda: self.stat_var.set("测量链路…"))
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
            self._later(lambda: self.link_var.set("链路测速失败: " + r["error"]))
            self._later(lambda: self.stat_var.set("测速失败"))
            return
        v = linkspeed.verdict(r["mbps"])
        # leave a little headroom for protocol overhead and bursts
        cap = max(2, int(r["mbps"] * 0.75))
        text = "链路 %.1f Mbps（%s）→ 建议码率上限不超过 %d Mbps" % (r["mbps"], v, cap)
        self._later(lambda: self.link_var.set(text))
        self._later(lambda: self.stat_var.set("测速完成"))
        if int(self.br_var.get()) > cap:
            self.log("当前码率上限 %s Mbps 高于链路可承载的 %d Mbps，已自动下调。"
                     % (self.br_var.get(), cap), "err")
            self._later(lambda: self.br_var.set(str(cap)))
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
    def run_bg(self, fn, done=None, exclusive=True):
        # An exclusive job owns the start button and the `busy` flag.  The
        # environment refresh must not: it runs 300ms after launch and takes a
        # couple of seconds (adb server + driver status), so a click on 开始投屏
        # during that window used to hit "上一个任务还在进行中" - a modal dialog
        # that blocks the UI and makes the button look broken.
        if exclusive:
            if self.busy:
                messagebox.showinfo("请稍候", "上一个任务还在进行中。")
                return
            self.busy = True
            self.btn_start.configure(state="disabled")
        else:
            if self.refr_busy:
                return
            self.refr_busy = True

        def work():
            err = None
            try:
                try:
                    fn()
                except VddMissing as e:
                    err = ("虚拟显示器未就绪：%s\n\n请先点“安装虚拟显示器驱动（需要管理员）”。" % e)
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    err = "%s: %s" % (type(e).__name__, e)
            finally:
                # Must happen even if handling the error above blew up: this is
                # the only place that clears `busy`, and a stuck `busy` means the
                # start button silently does nothing but say "请稍候" for ever.
                self._later(lambda: self._bg_done(err, done, exclusive))

        threading.Thread(target=work, daemon=True).start()

    def _bg_done(self, err, done, exclusive=True):
        if exclusive:
            self.busy = False
        else:
            self.refr_busy = False
        # run_bg() greys the start button out for the duration of an exclusive
        # job, so it has to be handed back here.  Forgetting this left the very
        # first refresh_env() (fired 300ms after startup) with the button
        # disabled for good, and the UI could never start a stream.
        if exclusive and not (self.session and self.session.running):
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
        self.run_bg(self._refresh_work, done="quiet", exclusive=False)

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
            self._later(lambda: self.dev_var.set(ready[0]["label"]))
        elif rows:
            self._later(lambda: self.dev_var.set(
                "已连接但未授权: " + ", ".join(r["serial"] + "=" + r["state"] for r in rows)))
        else:
            self._later(lambda: self.dev_var.set("未检测到设备（请用数据线连接并打开 USB 调试）"))

        st = vd.status()
        self._later(lambda: self.drv_var.set(vd.summary_line(st)))
        self._later(lambda: self.btn_install.configure(
            state="disabled" if st["state"] == "ready" else "normal"))

        mons = wd.live_monitors()
        text = "\n".join("%-12s %-28s %dx%d@%d  @(%d,%d)%s" % (
            m["name"], (m["monitor"] or m["desc"])[:28], m["width"], m["height"],
            m["hz"], m["left"], m["top"], "  [主]" if m["primary"] else "") for m in mons)
        self._later(lambda: self.mon_var.set("当前桌面显示器:\n" + (text or "（无）")))
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
        self._later(lambda: self.stat_var.set("安装驱动…"))
        ok, out = vd.install(log=self.log)
        for line in (out or "").splitlines()[-40:]:
            self.log("  " + line)
        if not ok:
            raise RuntimeError("驱动安装未成功，详见日志。")
        self.log("虚拟显示器驱动安装完成。", "ok")
        self._later(lambda: self.stat_var.set("驱动已安装"))
        self._refresh_work()

    def on_install_apk(self):
        self.run_bg(self._install_apk_work)

    def _install_apk_work(self):
        self._later(lambda: self.stat_var.set("安装 APK…"))
        self.session.cfg["install_apk"] = self.inst_var.get()
        self.session.find_device()
        self.session.prepare_device()
        self._later(lambda: self.stat_var.set("APK 已就绪"))

    def on_start(self):
        self._apply_form()
        self._save_settings()          # starting is a good moment to persist
        self.run_bg(self._start_work)

    def _start_work(self):
        self._later(lambda: self.stat_var.set("准备拓展屏…"))
        self.session.apply_display()
        self._later(lambda: self.stat_var.set("连接手机…"))
        self.session.find_device()
        self.session.prepare_device()
        self._later(lambda: self.stat_var.set("启动编码…"))
        self.session.start_stream()
        self.log("=== 投屏已开始：%s ===" % self.session.describe(), "ok")
        self._later(self._started)

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
        s = self.session
        if s is not None and getattr(s, "fatal", None):
            # The capture watchdog gave up (the virtual display is gone).
            self.perf_var.set(s.fatal)
        elif s is not None and s.server and not s.running:
            # ffmpeg died and is being restarted -- usually one or two seconds.
            self.perf_var.set("采集中断，正在自动重连…")
        elif s is not None and s.running and s.server:
            st = s.stats
            conn = "手机已连接" if s.server.connected else "等待手机连接…"
            self.perf_var.set("%s   %.1f fps   编码 %.1f Mbps   送出 %d 帧   丢 %d" % (
                conn, st["fps"], st["mbps"], st["sent"], st["dropped"]))
        self.after(700, self._poll_perf)

    def _on_close(self):
        self._save_settings()          # remember the window and the parameters
        try:
            if self.session.running or self.session.server:
                self.session.stop()
        except Exception:
            pass
        self.destroy()

    # ----------------------------------------------------------- log helpers
    def _log_text(self):
        return self.log_txt.get("1.0", "end-1c")

    def _flash_hint(self, msg):
        self.log_hint_var.set(msg)
        self.after(3000, lambda: self.log_hint_var.set(""))

    def on_copy_log(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(self._log_text())
            self._flash_hint("日志已复制到剪贴板")
        except Exception as e:
            messagebox.showerror("复制失败", str(e), parent=self)

    def on_save_log(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="保存日志", defaultextension=".txt",
            initialfile="DroidExtend-%s.txt" % time.strftime("%Y%m%d-%H%M%S"),
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._log_text())
            self._flash_hint("日志已保存到 " + path)
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self)

    def on_clear_log(self):
        self.log_txt.configure(state="normal")
        self.log_txt.delete("1.0", "end")
        self.log_txt.configure(state="disabled")
        self.log("日志已清空。")

    # ------------------------------------------------------- menu glue / help
    def open_url(self, url):
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("打开浏览器失败", "%s\n\n地址：%s" % (e, url), parent=self)

    def on_open_config_dir(self):
        d = paths.data_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception as e:
            messagebox.showerror("打开失败", "%s\n\n%s" % (e, d), parent=self)

    def _refresh_paths_label(self):
        """Status bar shows where settings and logs are being written."""
        try:
            self.file_var.set("数据目录: %s" % paths.data_dir())
        except Exception:
            pass

    def on_change_data_dir(self):
        """Move settings.json and DroidExtend.log somewhere else.

        Kept to one folder on purpose: "send me your log" should not also need
        "and find settings.json in your user profile".
        """
        old = paths.data_dir()
        d = filedialog.askdirectory(
            title="选择保存设置和日志的文件夹", parent=self, initialdir=old,
            mustexist=False)
        if not d:
            return
        d = os.path.abspath(d)
        if os.path.normcase(d) == os.path.normcase(old):
            return
        moved = []
        for name in ("settings.json", applog.NAME):
            src = os.path.join(old, name)
            if not os.path.exists(src):
                continue
            try:
                shutil.copy2(src, os.path.join(d, name))
                moved.append(name)
            except Exception as e:
                self.log("复制 %s 失败: %s" % (name, e), "err")
        try:
            paths.set_data_dir(d)
        except Exception as e:
            messagebox.showerror("切换失败", "%s\n\n%s" % (e, d), parent=self)
            return
        # Reopen the log at the new place so the rest of this run lands there.
        applog.close()
        applog.start("%s v%s" % (APP_NAME, APP_VERSION))
        self.log("数据目录已改为: %s  (已搬移: %s)" % (d, "、".join(moved) or "无"))
        self._refresh_paths_label()
        messagebox.showinfo(
            "数据目录已更改",
            "设置和日志现在保存在：\n%s\n\n已搬移：%s\n以后所有设置和日志都写在这里。"
            % (d, "、".join(moved) or "（没有旧文件）"), parent=self)

    def on_open_root_dir(self):
        try:
            os.startfile(ROOT)
        except Exception as e:
            messagebox.showerror("打开失败", "%s\n\n%s" % (e, ROOT), parent=self)

    def on_licenses(self):
        """Show 第三方组件许可.txt: the MIT/Apache/BSD texts we must ship."""
        path = os.path.join(ROOT, LICENSES)
        if not os.path.exists(path):
            messagebox.showinfo(
                "第三方组件许可",
                "没有找到 %s。\n\n它随安装包一起分发，来源见：\n%s" % (LICENSES, REPO_URL),
                parent=self)
            return
        try:
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("打开失败", "%s\n\n%s" % (e, path), parent=self)

    def on_manual(self, anchor=None):
        if self._manual_win is not None and self._manual_win.winfo_exists():
            win = self._manual_win
            win.deiconify()
            win.lift()
            win.focus_force()
        else:
            win = tk.Toplevel(self)
            self._manual_win = win
            win.title("使用说明 — %s v%s" % (APP_NAME, APP_VERSION))
            win.configure(bg=BG)
            win.geometry("880x720")
            win.minsize(560, 400)
            win.rowconfigure(0, weight=1)
            win.columnconfigure(0, weight=1)
            txt = tk.Text(win, bg="#0f1115", fg="#c8d0da", insertbackground=FG,
                          font=("Microsoft YaHei UI", 10), wrap="word", relief="flat",
                          padx=16, pady=12, spacing1=1, spacing3=1)
            txt.grid(row=0, column=0, sticky="nsew")
            sb = ttk.Scrollbar(win, command=txt.yview)
            sb.grid(row=0, column=1, sticky="ns")
            txt.configure(yscrollcommand=sb.set)
            txt.insert("1.0", mn.TEXT)
            txt.configure(state="disabled")
            self._manual_txt = txt
            foot = ttk.Frame(win, padding=(12, 8))
            foot.grid(row=1, column=0, columnspan=2, sticky="ew")
            ttk.Button(foot, text="项目主页", command=lambda: self.open_url(REPO_URL)).pack(side="left")
            ttk.Button(foot, text="反馈问题", command=lambda: self.open_url(ISSUES_URL)).pack(
                side="left", padx=6)
            ttk.Button(foot, text="关闭", command=win.destroy).pack(side="right")
        if anchor and self._manual_txt is not None:
            txt = self._manual_txt
            try:
                hit = txt.search(anchor, "1.0", stopindex="end")
                if hit:
                    txt.see(hit)
            except tk.TclError:
                pass

    def on_about(self):
        lines = [
            "%s — %s" % (APP_NAME, APP_TITLE),
            "版本 v%s  (%s)" % (APP_VERSION, APP_DATE),
            "",
            "用一根 USB 数据线，把安卓手机/平板变成 Windows 的第二块显示器。",
            "真扩展桌面，不是投屏镜像；端到端延迟实测 5~11 ms，支持触摸反控。",
            "",
            "代码作者：%s" % AUTHOR,
            "人类只负责提需求、给方向和验收。",
            "项目所有者：%s" % OWNER,
            "",
            "仓库：%s" % REPO_URL,
            "反馈：%s" % ISSUES_URL,
            "邮箱：%s" % " / ".join(EMAILS),
            "",
            "—— 用到的第三方组件 ——",
        ]
        lines += ["· %s（%s）  %s" % c for c in CREDITS]
        lines += ["", "完整许可与出处见 帮助 -> 第三方组件许可"]
        lines += ["（程序目录下的《%s》）" % LICENSES]
        messagebox.showinfo("关于 %s" % APP_NAME, "\n".join(lines), parent=self)

    def on_reset_settings(self):
        if not messagebox.askyesno(
                "重置为默认设置",
                "把界面上的参数恢复成默认值，并删掉已保存的设置文件。\n\n继续吗？", parent=self):
            return
        try:
            if os.path.exists(st.path()):
                os.remove(st.path())
        except Exception as e:
            self.log("删除设置文件失败: %s" % e, "err")
        default = st.load()
        self.settings = default
        self._apply_settings_to_form(default)
        self._load_encoders()
        self._save_settings()
        self.log("设置已重置为默认值。", "ok")

    # ---------------------------------------------------------------- updates
    def on_check_updates(self, manual=False):
        if self.upd_busy:
            if manual:
                self.log("上一次检查还没结束，稍等一下。")
            return
        self.upd_busy = True
        if manual:
            self.log("正在向 GitHub 查询最新版本…")

        def work():
            r = upd.check()
            self._later(lambda: self._upd_done(r, manual))

        threading.Thread(target=work, daemon=True).start()

    def _upd_done(self, r, manual):
        self.upd_busy = False
        if not r["ok"]:
            self.log("检查更新失败：%s" % r["error"], "err")
            if manual:
                messagebox.showwarning(
                    "检查更新",
                    "查询 GitHub 失败：\n%s\n\n（离线 / 被墙都不影响正常使用，"
                    "可以手动打开：\n%s）" % (r["error"], RELEASES_URL), parent=self)
            return
        if r["newer"]:
            self.log("发现新版本 v%s（当前 v%s）：%s" % (r["latest"], APP_VERSION, r["url"]), "ok")
            if messagebox.askyesno(
                    "发现新版本",
                    "有新版本 v%s，当前是 v%s。\n\n要打开下载页面吗？\n%s"
                    % (r["latest"], APP_VERSION, r["url"]), parent=self):
                self.open_url(r["url"])
        else:
            self.log("已是最新版本 v%s。" % APP_VERSION, "ok")
            if manual:
                messagebox.showinfo("检查更新", "已经是最新版本 v%s。" % APP_VERSION, parent=self)


def main():
    # The log file is opened before anything can go wrong, so even a failure in
    # the checks below leaves a record of what the process saw.
    applog.start("%s v%s" % (APP_NAME, APP_VERSION))
    applog.line("ROOT %s" % ROOT)
    applog.line("数据目录 %s" % paths.data_dir())
    st.migrate_legacy(log=applog.line)
    missing = []
    if not adb_exe():
        missing.append("adb")
    if not ffmpeg_exe():
        missing.append("ffmpeg")
    if missing:
        applog.line("缺少组件: " + ", ".join(missing))
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("缺少组件", "找不到: " + ", ".join(missing))
        applog.close()
        return 1
    app = App()
    # Tk otherwise prints a background exception to a console that a --windowed
    # build does not have, so the one traceback that mattered was invisible.
    def _oops(exc, val, tb):
        import traceback
        applog.write("".join(traceback.format_exception(exc, val, tb)))
        try:
            app.log("界面回调出错（已写入日志）: %s" % val, "err")
        except Exception:
            pass
    app.report_callback_exception = _oops
    try:
        app.mainloop()
    finally:
        applog.line("退出")
        applog.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
