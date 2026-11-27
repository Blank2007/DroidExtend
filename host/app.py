"""DroidExtend - turn an Android phone into a second monitor for Windows.

Everything is driven from this window: it installs the virtual display driver
if the machine does not have one, sets the extra screen to whatever size and
refresh rate you ask for, encodes it with ffmpeg, and pushes the stream down
the USB cable to the phone through an adb reverse tunnel.
"""
import os
import queue
import ctypes
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
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

from capture import (ENCODERS, NVENC_PRESETS, X264_PRESETS,                 # noqa: E402
                     list_hardware_encoders, available_encoders)
from paths import ROOT, apk_path, adb_exe, ffmpeg_exe                             # noqa: E402
from session import Session, DEFAULTS, VddMissing                                  # noqa: E402
import applog                                                                     # noqa: E402
import anim                                                                       # noqa: E402
import autostart as auto                                                          # noqa: E402
import changelog as cl                                                            # noqa: E402
from credits import credits as _credits, LICENSES                                 # noqa: E402
import i18n                                                                       # noqa: E402
import manual as mn                                                               # noqa: E402
import paths                                                                      # noqa: E402
import privacy as priv                                                            # noqa: E402
import settings as st                                                             # noqa: E402
import frameless as fr                                                           # noqa: E402
import ui_kit as uk                                                               # noqa: E402
import theme                                                                      # noqa: E402
import touch as tu                                                                # noqa: E402
import tray as tray_mod                                                           # noqa: E402
import update as upd                                                              # noqa: E402
from version import (APP_NAME, APP_TITLE, APP_VERSION, APP_DATE, AUTHOR, OWNER,   # noqa: E402
                     EMAILS, REPO_URL, RELEASES_URL, ISSUES_URL, HUAWEI_DOC_URL,
                     app_title)

# #3: 被开机自启动拉起时不把窗口糊到人家脸上，直接进托盘待命。
MINIMIZED = "--minimized" in sys.argv
# 1.9.0: "--stream" ask the program to start streaming all by itself.  It is
# only ever added to the autostart command line, so a normal double-click
# behaves exactly like 1.8.x.  The flag is a request, not a promise: the phone
# often shows up seconds after Windows does, and that waiting happens later
# (_auto_stream_worker), not at import time.
AUTOSTREAM = "--stream" in sys.argv
# A real newline in a dialog needs to be spelled out: a two-newline
# escape inside a wrapped i18n string is a syntax error waiting to happen.
NL = chr(10)
# 关窗口之后最多等这么久让后台把虚拟屏收干净。deep 档实测 10.65 秒，留一倍余量。
QUIT_WAIT = 30.0
# 1.9.0 -- how often the boot-time auto start retries while the phone is still
# missing (Windows comes up long before the phone finishes booting).
AUTO_RETRY = 5.0

# Fallback only.  The virtual display driver builds its mode table from the
# `<resolution>` rows in vdd_settings.xml -- eleven of them on the machine this
# was written on -- so most of the sizes in this list cannot be set at all:
# ChangeDisplaySettingsEx answers DISP_CHANGE_BADMODE (-2) and the picture does
# not change.  `_load_resolutions()` reads the driver's own table at startup and
# offers that instead; this list is what is left when there is no driver to ask.
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
QUALITY_CHOICES = ["14", "16", "17", "18", "20", "22", "23", "24", "26", "28", "32", "36"]
# (value, caption) tables for the three dropdowns whose caption is translated.
# The values are what settings.json stores and what session.py switches on, and
# they never change; only the captions follow the language.  Because of that the
# tables are filled by _refresh_captions() rather than written out here: this
# module is imported long before App.__init__ reads settings.json, so anything
# spelled out at this point would be frozen in whichever language happened to be
# active at import time.  Everything downstream compares the *value* -- never the
# caption -- precisely so a translated caption cannot change behaviour.
RATECONTROL = []
BACKENDS = []
POSITIONS = []
# 1.9.0 -- how long the boot-time auto start keeps waiting for the phone:
# (seconds, caption).  0 means "keep trying until someone presses 停止".
WAITS = []

BG = "#14161a"
FG = "#e6e6e6"
ACCENT = "#4c9aff"
PANEL = "#1c1f26"


# _credits() and LICENSES moved to credits.py: the pywebview shell prints the
# same rows, and a licence notice copied into two windows drifts.


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


def _custom_label():
    """Caption of the "custom" choice, shared by the size and position boxes.

    A function because it has to follow the active language, and because both
    call sites compare the combobox contents against it: one string, so the two
    can never disagree about what "custom" is spelled like.
    """
    return i18n.t("自定义", "Custom")


def _wait_caption(seconds):
    """Caption for an auto-start wait, in the active language.

    Kept next to the table it reads so a caption can never drift from the value
    written to settings.json (the dropdown is compared by caption, the file
    stores seconds).
    """
    try:
        want = int(seconds or 0)
    except (TypeError, ValueError):
        want = 60
    for v, t in WAITS:
        if v == want:
            return t
    return WAITS[1][1] if len(WAITS) > 1 else "60 s"


def _install_driver_label():
    """The install-driver button.

    The error that tells the user to press it quotes this verbatim, so both come
    from here instead of from two literals that could drift apart.
    """
    return i18n.t("安装虚拟显示器驱动（需要管理员）",
                  "Install the virtual display driver (needs admin)")


def _enable_driver_label():
    return i18n.t("启用虚拟显示器驱动（需要管理员）",
                  "Turn the virtual display driver on (needs admin)")


# 1.9.1 -- the kinds of news this program can pop up.  (kind, 中文, English):
# the settings key for each one is "notify_" + kind, so this tuple is the only
# place the menu and the sending code can disagree -- and they do not.
NOTIFY_KINDS = (
    ("stream", "投屏已经开始（开机自动投屏那次）",
     "the stream started (the automatic one)"),
    ("manual", "我手动点「开始投屏」时也通知",
     "also when I press Start myself"),
    ("fail", "投屏失败或者中途断了", "the stream failed or broke off"),
    ("driver", "驱动安装 / 启用 / 卸载", "driver installed / enabled / removed"),
    ("update", "有新的程序版本", "a newer version of the program"),
    ("apk", "手机端 App 需要更新", "the phone app needs updating"),
    ("misc", "其它提示（等着手机、测速之类）", "everything else"),
)
_NOTIFY_KINDS_SET = tuple(k for k, _zh, _en in NOTIFY_KINDS)


def _driver_button(st):
    """What the left panel's driver button should say and do right now.

    1.9.1.  The button used to come back enabled on any machine whose screen
    was not ready, which includes every machine where the driver IS installed:
    quitting with the "off for real" setting disables the device node, so the
    next start reports state "node", not "ready".  A user who had just
    installed the driver saw an enabled "install" button and concluded the
    install had not stuck.  Three answers now:

      ("install", caption)  nothing installed yet -- state staged/download/missing
      ("enable",  caption)  installed, node currently disabled -- offer to turn on
      ("done",    caption)  installed and running -- grey the button out
    """
    state = st.get("state")
    if state in ("staged", "download", "missing"):
        return "install", _install_driver_label()
    if st.get("enabled") is False:
        return "enable", _enable_driver_label()
    return "done", i18n.t("虚拟显示器驱动已安装",
                          "The virtual display driver is installed")


def _refresh_captions():
    """(Re)build the translated (value, caption) tables -- see the note above them."""
    RATECONTROL[:] = [
        ("vbr", i18n.t("VBR 质量优先（静止桌面省带宽）",
                       "VBR -- quality first (an idle desktop costs less bandwidth)")),
        ("cbr", i18n.t("CBR 恒定码率（运动画面更稳）",
                       "CBR -- constant bitrate (steadier under motion)")),
    ]
    BACKENDS[:] = [
        ("auto", i18n.t("自动（优先原生低延迟）", "Auto (prefers native)")),
        ("native", i18n.t("原生低延迟（需 NVIDIA 显卡）",
                          "Native low latency (needs NVIDIA)")),
        ("ddagrab", i18n.t("ddagrab（ffmpeg 桌面复制）",
                           "ddagrab (ffmpeg desktop duplication)")),
        ("gdigrab", i18n.t("gdigrab（GDI，兼容性最好）",
                           "gdigrab (GDI, most compatible)")),
    ]
    POSITIONS[:] = [
        ("right", i18n.t("右侧", "Right")),
        ("left", i18n.t("左侧", "Left")),
        ("above", i18n.t("上方", "Above")),
        ("below", i18n.t("下方", "Below")),
        ("custom", _custom_label()),
    ]
    WAITS[:] = [
        (30, i18n.t("30 秒", "30 s")),
        (60, i18n.t("60 秒", "60 s")),
        (120, i18n.t("120 秒", "120 s")),
        (300, i18n.t("5 分钟", "5 min")),
        (0, i18n.t("一直等", "keep waiting")),
    ]


# Filled once here so the lists are never empty for something that reads them
# without a running App (probes, tools/gui_test.py); at import time i18n has not
# been set yet, so this is the Chinese pair, and App._build() replaces it with
# whichever language the settings file asked for.
_refresh_captions()


def _safe_geometry(s, fallback="1000x640"):
    """Only trust a saved geometry that is a sane size and on this screen."""
    m = re.match(r"^(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?$", str(s or "").strip())
    if not m:
        return fallback
    w, h = int(m.group(1)), int(m.group(2))
    if not (860 <= w <= 8000 and 560 <= h <= 6000):
        return fallback
    if m.group(3) is None:
        return "%dx%d" % (w, h)
    x, y = int(m.group(3)), int(m.group(4))
    if abs(x) > 12000 or abs(y) > 12000:
        return "%dx%d" % (w, h)
    return "%dx%d+%d+%d" % (w, h, x, y)


def _work_area():
    """The desktop area the taskbar does not cover, in pixels."""

    class _Rect(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    rect = _Rect()
    try:
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                   ctypes.byref(rect), 0)
        if rect.right > rect.left and rect.bottom > rect.top:
            return (rect.left, rect.top, rect.right - rect.left,
                    rect.bottom - rect.top)
    except Exception:
        pass
    return (0, 0, 1280, 800)


def _borderless(win):
    """Take the system frame off a window and keep its taskbar button.

    overrideredirect() is the only way to drop the frame in Tk, and it also
    takes the window off the taskbar and out of Alt+Tab.  Windows puts the
    taskbar entry back when the frame window carries WS_EX_APPWINDOW; that has
    to be set on the parent of Tk's own window, and it has to be set again
    after every deiconify, which is why this is called from _show_window() too.
    """
    try:
        win.overrideredirect(True)
    except Exception:
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                              (ex & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
    except Exception:
        pass
    try:
        win.after(150, lambda: _round_win(win))
    except Exception:
        pass


def _round_win(win, maxed=False):
    """Put the rounded corners back on a window that has no system frame.

    Dropping the frame with overrideredirect() also drops the Windows 11
    rounded corners, and the drawing has 12px corners everywhere -- a square
    window around rounded content looks broken.  DWM's corner preference is
    asked for first (it anti-aliases and keeps the shadow); a window region is
    set as well, because a frameless WS_POPUP window on some builds ignores the
    DWM setting outright.
    """
    if os.name != "nt":
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
    except Exception:
        return
    if not hwnd:
        return
    try:
        pref = ctypes.c_int(2)          # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass
    if os.environ.get("DX_ROUND_REGION", "1") == "0":
        return
    try:
        gdi32 = ctypes.windll.gdi32
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        gdi32.CreateRectRgn.restype = ctypes.c_void_p
        user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_bool]
        w = win.winfo_width()
        h = win.winfo_height()
        if w < 16 or h < 16:
            return
        if maxed:
            rgn = gdi32.CreateRectRgn(0, 0, w + 1, h + 1)
        else:
            r = int(round(12.0 * win.winfo_fpixels("1i") / 96.0))
            rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, r * 2, r * 2)
        user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        # Read the settings before anything is built: every widget is created
        # with the value you used last time instead of a hard-coded default.
        self.settings = st.load()
        # Resolve the language before a single widget exists: every label is
        # built through i18n.t(), so the setting has to be applied first or the
        # window comes up half in each language.
        i18n.set_lang(self.settings.get("language", "auto"))
        self.upd_busy = False
        self._manual_win = None
        self._manual_txt = None
        # 1.9.1 -- notifications: the last time each sentence was sent (so a
        # retry loop cannot spam the same balloon), and whether the stream that
        # just started was started by 开机自动投屏 rather than by the user.
        self._notify_seen = {}
        self._auto_streaming = False
        # 赞赏窗口（1.8.3）和图对象一起留在这里：Tk 不会自己保管 PhotoImage
        # 的引用，局部变量一回收图片就变白。
        self._donate_win = None
        self._donate_photos = []
        self._retarget_job = None
        # _build() arms the 700ms performance tick, and _build() runs again on
        # every language switch -- so the job id is kept here and cancelled
        # before re-arming, or each switch would leave one more ticker behind.
        self._perf_job = None
        # The rate-control combobox holds a translated caption, so the value it
        # stands for is remembered here rather than recovered from the string.
        self._rc_value = "vbr"
        # #3: the tray lives on its own thread and talks back through a queue --
        # touching Tk from that thread is how you get random crashes.
        self.tray = None
        self._tray_q = queue.Queue()
        self._tray_poll = None
        self._quitting = False
        # 退出时的收尾在窗口消失之后才跑（彻底关闭驱动要十几秒），这里存那个
        # 线程，好让 main() 在进程真正结束前等它收干净。
        self._quit_worker = None
        # 1.9.1 -- what the left panel's driver button currently means:
        # "install" / "enable" / "done", see _driver_button().
        self._drv_action = "install"

        # 2.2.0 -- theme state.  self.pal is the colour dict that every widget
        # built from here down reads from, so it has to exist before _build()
        # runs; it is also the only thing that changes when the colours do.
        self.theme_mode = str(self.settings.get("theme", "auto") or "").strip().lower()
        if self.theme_mode not in theme.MODES:
            self.theme_mode = "auto"
        self._theme_kind = theme.resolve(self.theme_mode)
        self.pal = theme.PALETTES[self._theme_kind]
        self.theme_var = tk.StringVar(value=self.theme_mode)
        self._theme_job = None
        # The settings window (built hidden by _build(), opened on demand) and
        # the page it was last on -- kept across a rebuild so that changing the
        # language or the colours leaves you on the page you were reading.
        self._settings_win = None
        self._page = "picture"
        self._pages = {}
        self._nav_btns = {}

        # app_title() and not the APP_TITLE constant: the constant was evaluated
        # when version.py was first imported (line 64), which is before the
        # set_lang() call above -- so it would always read as Chinese.
        self.title("%s — %s   v%s" % (APP_NAME, app_title(), APP_VERSION))
        self.configure(bg=self.pal["bg"])
        # 2.2.0 -- the drawn UI is laid out in design pixels: every number in
        # it goes through ui_kit.px(), so the scale has to be known before the
        # first widget exists.  The window is the design's 1000x640 at 100%,
        # times the monitor's DPI.
        uk.set_scale(self.winfo_fpixels("1i") / 96.0)
        self.geometry(_safe_geometry(self.settings.get("window"),
                                     "%dx%d" % (uk.px(1000), uk.px(640))))

        self.logq = queue.Queue()
        # Every line the pane is currently showing, as (text, tag).  Kept
        # because a language switch rebuilds the whole window, and the pane has
        # to come back with its colours intact.
        self._log_lines = []
        self.session = Session(log=self.log, on_notice=self._phone_notice)
        self.session.gpu_cb = self._on_gpu
        self.session.ask_install = self._ask_install_apk
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
        # The driver install is the one thing here that needs Windows
        # PowerShell; saying which one (or that none works) turns a failed
        # install into something the user can act on.
        try:
            import ps
            self.log("PowerShell: %s" % (("%s  %s" % (ps.version(), ps.exe()))
                                         if ps.exe() else i18n.t("没有可用的 PowerShell",
                                                                 "no usable PowerShell")))
        except Exception as e:
            self.log("PowerShell: 探测失败 %s" % e)
        self.log("设置与日志目录: %s" % paths.data_dir())
        self.log("设置文件: %s%s" % (st.where(),
                                    "" if os.path.exists(st.where()) else "  （还没有，退出时会创建）"))
        self.log("日志文件: %s" % (applog.path(),))
        apk = apk_path()
        self.log("APK: %s%s" % (apk or "(缺失)", "" if apk else "  ← 先运行 build_apk.bat 构建"))
        if not apk:
            self.log("提示：双击 build_apk.bat 可以重新构建手机端安装包。", "err")
        # The terms come first, before anything else this program does.  Scheduled
        # rather than called inline so the main window is already on screen
        # behind the dialog.
        self.after(150, self._check_privacy)
        self.after(300, self.refresh_env)
        self.after(1800, self._check_orphans)
        self.after(200, self._poll_tray)
        if self.settings.get("check_updates", True) and not priv.needs_accept(self.settings):
            self.after(2500, lambda: self.on_check_updates(manual=False))
        # 1.9.0 -- 自动投屏的重试循环靠这个事件停下：用户一按开始/停止、或者
        # 程序要退出，都不该继续在后台等手机。
        self._auto_cancel = threading.Event()
        if AUTOSTREAM:
            # 三秒不是仪式：开机时 adb 服务、虚拟屏驱动、手机都还在醒来的路上，
            # 立刻试一次只会拿到"手机不存在"，白白在日志里留一行红字。
            self.after(3000, self._auto_stream_check)
        if MINIMIZED:
            self.withdraw()            # 先藏起来，免得开机时闪一下再收
            self.after(400, self._hide_to_tray)
        else:
            # The first thing anyone sees, so it gets the one animation nobody
            # has to ask for.  Delayed past the initial layout, because a window
            # that is still being resized would fade against a moving edge.
            self.after(90, lambda: anim.fade_in(self, settings=self.settings))

    # ------------------------------------------------------------------- menu
    def _build_menu(self):
        """The window's shared switches, built before anything reads them.

        Everything on the ≡ side of the window -- the notification kinds, the
        update switches, the language radios -- reads these, and all three are
        built once here so a rebuild (a language change tears the window down
        and puts it back) keeps the values.
        """
        self.lang_var = tk.StringVar(value=self._lang_setting())
        # 1.9.1 -- one switch per kind, because "tell me when it fails" and "do
        # not tell me every time it starts" have to be answerable separately.
        self.notify_var = tk.BooleanVar(value=bool(self.settings.get("notify", True)))
        for _kind, _zh, _en in NOTIFY_KINDS:
            var = tk.BooleanVar(
                value=bool(self.settings.get("notify_" + _kind, True)))
            # Kept on self: a Tk variable nothing references can be collected,
            # and its switch would then quietly lose its value.
            setattr(self, "_nvar_" + _kind, var)
        self.chk_var = tk.BooleanVar(
            value=bool(self.settings.get("check_updates", True)))
        self.pre_upd_var = tk.BooleanVar(
            value=bool(self.settings.get("check_prerelease", False)))

    def _show_view(self, name):
        """Exactly one of the two views is packed at any time."""
        for v in (getattr(self, "view_main", None),
                  getattr(self, "view_set", None)):
            if v is None:
                continue
            try:
                v.pack_forget()
            except Exception:
                pass
        if name == "set" and getattr(self, "view_set", None) is not None:
            self.view_set.pack(side="top", fill="both", expand=True)
            return
        self.view_main.pack(side="top", fill="both", expand=True,
                            padx=(uk.px(24), uk.px(24)),
                            pady=(uk.px(4), uk.px(20)))

    # --------------------------------------------------------------- language
    def _lang_setting(self):
        """The stored language setting, normalised to one of auto/zh/en.

        Anything else in settings.json (a hand-edited file, a value from a
        future version) counts as 'follow the system' rather than as a language
        no radiobutton is showing.
        """
        code = str(self.settings.get("language", i18n.AUTO) or "").strip().lower()
        return code if code in (i18n.AUTO, i18n.ZH, i18n.EN) else i18n.AUTO

    def _switch_lang(self, code):
        """Change the interface language and build the window again in it."""
        if code == self._lang_setting():
            return                       # the radiobutton is echoing the setting
        self.settings["language"] = code
        self._save_settings()
        self.log("界面语言已切换，正在重建窗口…")
        # Before anything is created again: every caption below is produced by
        # i18n.t() while the widget is built, so the language has to be in place
        # first or the new window comes up half in each language.
        i18n.set_lang(code)
        page = getattr(self, "_page", "picture")
        self._rebuild_window()
        # The captions in the settings window are stale too, so it went with
        # the rebuild; put it back up where it was instead of closing it.
        try:
            self._open_settings(page)
        except Exception:
            pass

    def _rebuild_window(self):
        """Throw the widget tree away and build it again, in the new language.

        Rebuilding rather than retranslating: Tk widgets cannot have their text
        swapped in place without walking every one of them and knowing what each
        should say, and this file writes each caption where it is used.  Doing
        it this way means every caption -- including any I forgot to translate --
        is produced fresh by the new language, and the ones I did translate
        cannot be missed either.

        What survives: `self.settings`, `self.session`, the tray, the log queue
        and every other non-widget attribute.  Only the children of this window
        go, which includes any open Toplevel (the manual, the update dialogs) --
        they are children of this window in Tk's hierarchy and their captions
        are just as stale, so they go with it.

        The periodic callbacks already in flight (_drain_log, _poll_tray,
        _poll_perf, _check_orphans, an in-flight refresh_env) are deliberately
        left alone.  They all reach their widgets through `self.<attr>`, so
        after the rebuild those names point at the new widgets and the callbacks
        simply carry on -- and a refresh_env that is mid-flight finishes into
        the new labels.  Cancelling and re-arming them would mean tracking five
        job ids and getting the re-arm order right, which is more to get wrong
        for no gain.  The one exception is _poll_perf, which re-arms itself from
        _build() as well; _arm_perf() cancels the old tick first.
        """
        try:
            # The pane dies with the widgets, but the messages in it are the
            # record of what this run has done -- including the "language
            # switched" line just written.  Carry the lines across together
            # with their colours: red is the one people scan for.
            keep_log = list(self._log_lines)
        except Exception:
            keep_log = []
        try:
            self._manual_win = None
            self._manual_txt = None
            self._donate_win = None
            self._donate_photos = []
            self._settings_win = None
            for w in list(self.winfo_children()):
                w.destroy()
            self._build_menu()
            self._build()
            if keep_log:
                self._restore_log_text(keep_log)
            self._apply_settings_to_form(self.settings)
            self._load_encoders()
            # A stream that is running keeps running: the form above was reset
            # to "ready", so put the status line and the two buttons back to
            # where they were, and re-read the device and driver state that the
            # fresh labels are showing as unknown.
            if getattr(self.session, "running", False):
                self._started()
        except Exception as e:
            # Better an empty window with a reason in the log than a crash
            # inside a menu callback.
            self.log("重建窗口失败: %s" % e, "err")
            try:
                messagebox.showerror(
                    i18n.t("界面语言", "Interface language"),
                    i18n.t("切换语言后重建窗口失败：\n%s\n\n日志里有详细信息。",
                           "Rebuilding the window after the language change "
                           "failed:\n%s\n\nThe log has the details.") % e,
                    parent=self)
            except Exception:
                pass
            return
        # Separate from the rebuild: the window is fine either way, and the
        # device and driver labels would otherwise sit on "detecting" for ever.
        try:
            self.refresh_env()
        except Exception as e:
            self.log("刷新设备状态失败: %s" % e, "err")

    def _restore_log_text(self, lines):
        """Put the old log pane contents back into the freshly built one.

        Each line goes back with its own tag, because the tag is the message:
        a red line is "adb failed" or "the virtual display disappeared", and
        that is what someone reads this pane for.  Never raises -- the window
        is up and working by the time this runs, and a missing log line is not
        worth an error dialog.
        """
        try:
            self.log_txt.configure(state="normal")
            for text, tag in lines:
                self.log_txt.insert("end", text + "\n", tag or ())
            self.log_txt.see("end")
            self.log_txt.configure(state="disabled")
        except Exception:
            pass

    # --------------------------------------------------------------- settings
    def _apply_settings_to_form(self, s):
        """Push a settings dict into the widgets."""
        res = "%dx%d" % (int(s.get("width", 1920)), int(s.get("height", 1080)))
        known = ["%dx%d" % r for r in RESOLUTIONS]
        custom = bool(s.get("custom_res")) or res not in known
        self.res_var.set(_custom_label() if custom else res)
        self.w_var.set(str(s.get("width", 1920)))
        self.h_var.set(str(s.get("height", 1080)))
        self.mode_var.set(s.get("mode", "extend"))
        self.hz_var.set(str(s.get("hz", 60)))
        self.fps_var.set(str(s.get("fps", 60)))
        self.br_var.set(str(s.get("bitrate_mbps", 40)))
        self.rc_var.set(_label_for(RATECONTROL, s.get("rate_control", "vbr"), RATECONTROL[0][1]))
        self._rc_value = str(s.get("rate_control", "vbr")) or "vbr"
        self.q_var.set(str(s.get("quality", 20)))
        self.enc_var.set(s.get("encoder", "h264_nvenc"))
        self.pre_var.set(s.get("preset", "p4"))
        self.be_var.set(_label_for(BACKENDS, s.get("backend", "auto"), BACKENDS[0][1]))
        self.pos_var.set(_label_for(POSITIONS, s.get("position", "right"), POSITIONS[0][1]))
        self.mouse_var.set(bool(s.get("draw_mouse", True)))
        self.inst_var.set(bool(s.get("reinstall_apk", False)))
        self.match_var.set(bool(s.get("match_phone", True)))
        # 1.9.0 -- the touch channel, the boot-time stream and its wait.
        if hasattr(self, "touch_var"):
            self.touch_var.set(bool(s.get("touch", DEFAULTS.get("touch", True))))
        if hasattr(self, "stream_auto_var"):
            self.stream_auto_var.set(bool(s.get("auto_stream", False)))
        if hasattr(self, "wait_var"):
            self.wait_var.set(_wait_caption(int(s.get("auto_stream_wait", 60) or 0)))
        if hasattr(self, "chk_var"):
            self.chk_var.set(bool(s.get("check_updates", True)))
        s["theme"] = self.theme_mode
        if hasattr(self, "pre_upd_var"):
            self.pre_upd_var.set(bool(s.get("check_prerelease", False)))
        self.pos_x_var.set(str(s.get("pos_x", 0)))
        self.pos_y_var.set(str(s.get("pos_y", 0)))
        self._res_changed()
        self._rc_changed()
        self._sync_pos()
        self._sync_summary()

    def _form_values(self):
        """Snapshot the widgets into a settings dict.  Never raises."""
        s = dict(self.settings)
        try:
            if self.res_var.get() == _custom_label():
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
        if s["position"] == "custom":
            for key, var in (("pos_x", self.pos_x_var), ("pos_y", self.pos_y_var)):
                try:
                    s[key] = int(var.get())
                except Exception:
                    pass
        s["draw_mouse"] = bool(self.mouse_var.get())
        # 1.8.5 -- the box on the form has always been labelled "每次重新安装
        # APK" (reinstall every time), which is what it now actually controls;
        # installing and updating is no longer optional, because a phone left on
        # an old app looks like a broken program rather than an old one.
        s["install_apk"] = True
        s["reinstall_apk"] = bool(self.inst_var.get())
        s["match_phone"] = bool(self.match_var.get())
        s["mode"] = self.mode_var.get()
        if hasattr(self, "close_action"):
            s["close_action"] = self.close_action
            # Kept in step for older builds that still read the boolean.
            s["tray_on_close"] = self.close_action != "quit"
        if hasattr(self, "auto_var"):
            s["autostart"] = bool(self.auto_var.get())
        if hasattr(self, "touch_var"):
            s["touch"] = bool(self.touch_var.get())
        if hasattr(self, "stream_auto_var"):
            # Without 开机自动启动 there is nothing to hook the stream onto, so
            # the two are saved as one decision.
            s["auto_stream"] = bool(self.stream_auto_var.get() and self.auto_var.get())
        if hasattr(self, "wait_var"):
            s["auto_stream_wait"] = self._wait_value()
        if hasattr(self, "off_mode_var"):
            s["vdd_off_mode"] = self.off_mode_var.get()
        if hasattr(self, "chk_var"):
            s["check_updates"] = bool(self.chk_var.get())
        if hasattr(self, "pre_upd_var"):
            s["check_prerelease"] = bool(self.pre_upd_var.get())
        try:
            if self.state() == "normal":
                g = self.geometry()
                # A maximized window reports the maximized size here, which
                # would make the next start open full screen -- keep the
                # restore rectangle instead.
                if getattr(self, "_fx", None) is not None:
                    if fr.is_maximized(self):
                        nr = fr.normal_rect(self)
                        if nr:
                            g = "%dx%d+%d+%d" % nr
                s["window"] = g
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
        self._sync_summary()

    # ------------------------------------------------------------------ build
    # ------------------------------------------------------------ the frame
    def _install_frameless(self):
        """        Give the drawn window what it lost with the frame.

        Without this the title bar cannot drag the window and the edges
        cannot size it: an overrideredirect window has no caption and no
        frame for Windows to grab.  The subclass hands both back at the
        Win32 level, which is also what makes snap layouts, Win+arrow and a
        real maximize work."""
        self._fx = None
        try:
            self._fx = fr.install(self, min_size=(880, 600),
                                  caption_height=44, caption_skip=214)
        except Exception:
            self._fx = None
        if self._fx is not None:
            self._fx_job = self.after(1200, self._fx_tick)

    def _fx_tick(self):
        """Keep the subclass on the live window.

        Tk replaces its child window when attributes change, and the subclass
        would otherwise stay behind on the dead handle -- which is exactly what
        makes the edges stop resizing after a while.  Re-hooking is a few
        hundred microseconds and does nothing when the handles are unchanged."""
        self._fx_job = None
        try:
            fr.rehook(self)
        except Exception:
            pass
        if getattr(self, "_fx", None) is not None:
            try:
                self._fx_job = self.after(1200, self._fx_tick)
            except Exception:
                self._fx_job = None

    def _drag_from(self, widget):
        """Move the window by dragging this widget -- the drawn title bar."""
        state = {}

        def press(event):
            state["dx"] = event.x_root - self.winfo_x()
            state["dy"] = event.y_root - self.winfo_y()

        def move(event):
            if "dx" not in state or getattr(self, "_maxed", False):
                return
            self.geometry("+%d+%d" % (event.x_root - state["dx"],
                                      event.y_root - state["dy"]))

        widget.bind("<Button-1>", press, add="+")
        widget.bind("<B1-Motion>", move, add="+")

    def _install_resize(self):
        """A resize grip in the corner: without a frame this is the only one."""
        grip = tk.Frame(self, bg=self.pal["bg"], width=uk.px(16),
                        height=uk.px(16), cursor="size_nw_se")
        grip.place(relx=1.0, rely=1.0, anchor="se")
        state = {}

        def press(event):
            state["w"] = self.winfo_width()
            state["h"] = self.winfo_height()
            state["x"] = event.x_root
            state["y"] = event.y_root

        def move(event):
            if "w" not in state:
                return
            w = max(uk.px(880), state["w"] + event.x_root - state["x"])
            h = max(uk.px(600), state["h"] + event.y_root - state["y"])
            self.geometry("%dx%d" % (w, h))

        grip.bind("<Button-1>", press)
        grip.bind("<B1-Motion>", move)
        grip.bind("<ButtonRelease-1>", lambda _e: state.clear())
        self._grip = grip

    def _minimize(self):
        """The — button.

        Tk's iconify() is a request to the window manager, and a window with
        overrideredirect set has none -- the call returns quietly and nothing
        happens.  Ask Windows itself instead; the taskbar entry it creates is
        what brings the window back (its SC_RESTORE comes through the window
        procedure we already installed).
        """
        if getattr(self, "_fx", None) is not None:
            try:
                fr.minimize(self)
                return
            except Exception:
                pass
        try:
            self.iconify()
        except Exception:
            pass

    def _toggle_max(self):
        """The ▢ button: let Windows maximize it, or put it back."""
        if getattr(self, "_fx", None) is not None:
            try:
                if fr.is_maximized(self):
                    fr.restore(self)
                else:
                    fr.maximize(self)
                self._maxed = fr.is_maximized(self)
            except Exception:
                self._maxed = False
            self.after(80, self._fix_corners)
            return
        try:
            if getattr(self, "_maxed", False):
                self._maxed = False
                if getattr(self, "_restore_geo", ""):
                    self.geometry(self._restore_geo)
            else:
                self._restore_geo = self.geometry()
                x, y, w, h = _work_area()
                self._maxed = True
                self.geometry("%dx%d+%d+%d" % (w, h, x, y))
        except Exception:
            self._maxed = False
        self._fix_corners()

    def _on_win_configure(self, event=None):
        if event is not None and event.widget is not self:
            return
        # Double-click on the title bar and Win+Up go straight to Windows, so
        # the drawn state has to follow whatever Windows decided.
        if getattr(self, "_fx", None) is not None:
            try:
                self._maxed = fr.is_maximized(self)
            except Exception:
                pass
        if getattr(self, "_rgn_job", None):
            try:
                self.after_cancel(self._rgn_job)
            except Exception:
                pass
        self._rgn_job = self.after(120, self._fix_corners)

    def _fix_corners(self):
        self._rgn_job = None
        _round_win(self, maxed=getattr(self, "_maxed", False))

    def on_toggle_log(self):
        """展开 / 收起: hand the whole window to the log, or give it back."""
        self._log_big = not getattr(self, "_log_big", False)
        try:
            if self._log_big:
                self._top.pack_forget()
            else:
                self._top.pack(side="top", fill="x", before=self._logcard)
        except Exception:
            pass
        try:
            self._btn_expand.configure(
                text=i18n.t("收起 ▴", "Collapse ▴") if self._log_big
                else i18n.t("展开 ▾", "Expand ▾"))
        except Exception:
            pass

    def _build(self):
        """Build (or rebuild) every widget in the main window.

        The layout is the codex-style one: a drawn title bar, the connection
        line, the two device rows, the four picture fields, the option row and
        the action row, with a black log pane taking whatever height is left.
        The groups are told apart by spacing alone -- no hairlines, which is
        the one place this window deliberately differs from the drawing it was
        built from.
        """
        _refresh_captions()
        self.title("%s — %s   v%s" % (APP_NAME, app_title(), APP_VERSION))
        pal = self.pal
        self.configure(bg=pal["bg"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        theme.apply_styles(style, pal)
        theme.option_add(self, pal)
        # 使用说明 / 关于 / 捐赠那几个窗口是普通 Toplevel，用的是模块级常量而
        # 不是 ttk 样式，所以每次重建都把常量指到当前色板，否则一切到浅色它们
        # 就还留在旧的深色里。
        global BG, FG, ACCENT, PANEL
        BG, FG = pal["bg"], pal["fg"]
        ACCENT, PANEL = pal["green"], pal["field"]

        # This is an attribute, not a widget, but the settings window shows a
        # button for it, and _build() is where it has always been read.
        self.close_action = self._initial_close_action()

        # -- the handful of type sizes the design asks for --------------------
        fonts = {}

        def fnt(size, weight="normal", mono=False):
            key = (size, weight, mono)
            got = fonts.get(key)
            if got is None:
                got = tkfont.Font(family=theme.MONO if mono else theme.UI,
                                  size=-uk.px(size), weight=weight)
                fonts[key] = got
            return got

        self._fnt = fnt
        F17 = fnt(17, "bold")
        F145 = fnt(14.5, "bold")
        F14 = fnt(14, "bold")
        F13 = fnt(13, "bold")
        F125 = fnt(12.5)
        F12 = fnt(12)
        F115 = fnt(11.5)
        F11 = fnt(11)
        F105 = fnt(10.5)
        MONO = fnt(11.5, mono=True)
        LABW = uk.px(92)
        GAP = uk.px(16)

        def lab(master, text="", f=None, fg=None, back=None, **kw):
            return tk.Label(master, text=text, font=f or F12,
                            fg=fg or pal["dim"], bg=back or pal["bg"],
                            bd=0, highlightthickness=0, **kw)

        def row_with(master, caption, pady, pad_top=0, expand=False):
            """One content row: a 92px caption pinned left, the values beside it."""
            row = tk.Frame(master, bg=pal["bg"])
            row.pack(side="top", fill="x", pady=pady)
            value = tk.Frame(row, bg=pal["bg"])
            if expand:
                value.pack(side="left", fill="x", expand=True,
                           padx=(LABW + GAP, 0))
            else:
                value.pack(side="left", anchor="n", padx=(LABW + GAP, 0))
            lab(row, caption, F12, pal["dim"], anchor="nw").place(
                x=0, y=uk.px(pad_top))
            return row, value

        def sheet(title, width, height, parent=None):
            """A secondary window that belongs to this program.

            The main window lost its system frame and every dialog left behind
            with one suddenly came from a different application: grey title
            bar, white body, square corners.  This builds the same shell -- no
            frame, 12px corners, one header row with a title and a close
            button -- and hands back the body frame to fill in.
            """
            win = tk.Toplevel(parent or self)
            win.configure(bg=pal["bg"])
            win.geometry("%dx%d" % (uk.px(width), uk.px(height)))
            win.title(title)
            try:
                win.transient(self)
            except Exception:
                pass
            _borderless(win)
            head = tk.Frame(win, bg=pal["bg"])
            head.pack(side="top", fill="x", padx=uk.px(22),
                      pady=(uk.px(16), uk.px(8)))
            title_lab = lab(head, title, F13, pal["fg"])
            title_lab.pack(side="left")
            uk.RoundButton(head, pal, "✕", command=win.destroy, height=26,
                           width=26, kind="ghost", border=False, font=F115,
                           fg=pal["faint"], back=pal["bg"]).pack(side="right")
            body = tk.Frame(win, bg=pal["bg"])
            body.pack(side="top", fill="both", expand=True,
                      padx=uk.px(22), pady=(uk.px(2), uk.px(18)))

            # Drag by the header, exactly like the main window.
            def _down(event):
                win._drag = (event.x_root - win.winfo_x(),
                             event.y_root - win.winfo_y())

            def _move(event):
                offset = getattr(win, "_drag", None)
                if offset:
                    win.geometry("+%d+%d" % (event.x_root - offset[0],
                                             event.y_root - offset[1]))

            for widget in (head, title_lab):
                widget.bind("<Button-1>", _down)
                widget.bind("<B1-Motion>", _move)
            win.bind("<Escape>", lambda _e: win.destroy())
            win.lift()
            win.after(60, win.lift)
            win.after(120, lambda: _round_win(win))
            return win, body

        self._sheet = sheet
        # 2.2.1 -- the dialogs built by methods (update offer, download, close
        # question, terms) sit outside this function, so the local label helper
        # has to be reachable from there.  It was not, and every one of those
        # windows raised NameError instead of appearing -- which is why the
        # update notice never showed up.
        self._lab = lab

        # -- frame and title bar ---------------------------------------------
        # The system title bar is gone (the drawing has its own) which also
        # takes the window off the taskbar; _borderless() puts it back.
        _borderless(self)
        self._install_frameless()
        self.minsize(uk.px(880), uk.px(600))
        # The corners are cut with a window region, so they have to be cut
        # again every time the window changes size -- throttled, because a
        # drag-resize fires Configure on every pixel.
        self._rgn_job = None
        self.bind("<Configure>", self._on_win_configure, add="+")

        tb = tk.Frame(self, bg=pal["bg"], height=uk.px(44))
        tb.pack(side="top", fill="x")
        tb.pack_propagate(False)
        self._drag_from(tb)

        head = tk.Frame(tb, bg=pal["bg"])
        head.pack(side="left")
        self._drag_from(head)
        name = lab(head, APP_NAME, F14, pal["fg"])
        name.pack(side="left", padx=(uk.px(20), 0))
        self._drag_from(name)
        sub = lab(head, app_title(), F12, pal["faint"])
        sub.pack(side="left", padx=(uk.px(8), 0))
        self._drag_from(sub)
        uk.Pill(head, pal, "v%s" % APP_VERSION, back=pal["bg"]).pack(
            side="left", padx=(uk.px(8), 0))

        right = tk.Frame(tb, bg=pal["bg"])
        right.pack(side="right", padx=(0, uk.px(6)))

        def win_btn(glyph, cmd, wide=36, size=10.5):
            btn = uk.RoundButton(right, pal, glyph, command=cmd, height=28,
                                 width=wide, kind="ghost", border=False,
                                 font=fnt(size), fg=pal["dim"], back=pal["bg"])
            btn.pack(side="right", padx=(uk.px(1), 0))
            return btn

        win_btn("✕", self._on_close)
        win_btn("▢", self._toggle_max)
        win_btn("—", self._minimize)
        self._menu_btn = uk.RoundButton(
            right, pal, "≡", command=self._open_settings, height=28, width=28,
            kind="ghost", border=False, font=fnt(14), fg=pal["dim"],
            back=pal["bg"])
        self._menu_btn.pack(side="right", padx=(0, uk.px(4)))

        # -- body ------------------------------------------------------------
        # Two views share this body: the picture, and the settings.  Exactly
        # one of them is packed at a time, so the settings never become a
        # second window floating over the one the user is watching.
        host = tk.Frame(self, bg=pal["bg"])
        host.pack(side="top", fill="both", expand=True)
        self._host = host
        body = tk.Frame(host, bg=pal["bg"])
        body.pack(side="top", fill="both", expand=True,
                  padx=(uk.px(24), uk.px(24)), pady=(uk.px(4), uk.px(20)))
        self._body = body
        self.view_main = body

        top = tk.Frame(body, bg=pal["bg"])
        top.pack(side="top", fill="x")
        self._top = top

        # -- the connection line ---------------------------------------------
        self.stat_var = tk.StringVar(value=i18n.t("就绪", "Ready"))
        self.dev_var = tk.StringVar(value=i18n.t("未检测", "Not detected"))
        self.note_var = tk.StringVar(value="")

        bigrow = tk.Frame(top, bg=pal["bg"])
        bigrow.pack(side="top", fill="x", pady=(uk.px(14), uk.px(16)))
        # A drawn dot rather than a "●" glyph: it is the only coloured thing
        # left in the window and it has to be able to turn amber and red.
        self.dot_cv = tk.Canvas(bigrow, width=uk.px(8), height=uk.px(8),
                                bg=pal["bg"], bd=0, highlightthickness=0,
                                takefocus=0)
        self.dot_cv.pack(side="left")
        self._dot_id = self.dot_cv.create_oval(0, 0, uk.px(8), uk.px(8),
                                               fill=pal["faint"], outline="")
        self.dot_lbl = self.dot_cv
        lab(bigrow, "", F17, pal["fg"], textvariable=self.stat_var).pack(
            side="left", padx=(uk.px(9), 0))
        lab(bigrow, "", F125, pal["faint"], textvariable=self.dev_var).pack(
            side="left", padx=(uk.px(9), 0))
        lab(bigrow, "", F12, pal["faint"], textvariable=self.note_var,
            wraplength=uk.px(260), justify="left").pack(
                side="left", padx=(uk.px(10), 0))

        self._metric_labels = {}
        met = tk.Frame(bigrow, bg=pal["bg"])
        met.pack(side="right")
        for key, cap, unit, hi in (
                ("cap", i18n.t("采集", "Capture"), "fps", False),
                ("send", i18n.t("发送", "Send"), "Mbps", False),
                ("phone", i18n.t("手机", "Phone"), "fps", False),
                ("lat", i18n.t("上屏", "Shown"), "ms", True)):
            cell = tk.Frame(met, bg=pal["bg"])
            cell.pack(side="left", padx=(uk.px(22), 0))
            lab(cell, cap, F11, pal["faint"]).pack(side="left", anchor="s")
            var = tk.StringVar(value="—")
            lbl = lab(cell, "", F145, pal["green"] if hi else pal["fg"],
                      textvariable=var, anchor="s")
            lbl.pack(side="left", padx=(uk.px(4), 0), anchor="s")
            lab(cell, unit, F105, pal["faint"]).pack(
                side="left", padx=(uk.px(3), 0), anchor="s")
            self._metric_labels[key] = (var, lbl)

        # -- picture: three fields on one row --------------------------------
        # The frame rate used to have a field of its own beside the refresh
        # rate, which read as two answers to the same question: the host runs
        # at min(fps, panel Hz) and the panel has the last word.  One field now
        # sets both.  Quality Q moved into the settings; the bitrate is the
        # number people reach for after a stutter, so it lives here and takes a
        # typed value.
        _frow, fields = row_with(top, i18n.t("画面", "Picture"),
                                 (uk.px(16), uk.px(6)), pad_top=6, expand=True)
        self.res_var = tk.StringVar(value="1920x1080")
        self.hz_var = tk.StringVar(value="60")
        self.fps_var = tk.StringVar(value="60")
        self.q_var = tk.StringVar(value="20")
        self.q_hint_var = tk.StringVar(value="")
        self.br_var = tk.StringVar(value=str(DEFAULTS["bitrate_mbps"]))
        self.w_var = tk.StringVar(value="1920")
        self.h_var = tk.StringVar(value="1080")
        selects = {}
        for col, (cap, var, values, field_name, editable) in enumerate((
                (i18n.t("分辨率", "Resolution"), self.res_var,
                 ["%dx%d" % r for r in RESOLUTIONS] + [_custom_label()],
                 "res", False),
                (i18n.t("刷新率 / 帧率", "Refresh / frame rate"), self.hz_var,
                 list(HZ_CHOICES), "hz", False),
                (i18n.t("码率 Mbps", "Bitrate, Mbps"), self.br_var,
                 list(BITRATE_CHOICES), "br", True))):
            cell = tk.Frame(fields, bg=pal["bg"])
            cell.pack(side="left", fill="x", expand=True,
                      padx=(0, 0 if col == 2 else uk.px(10)))
            lab(cell, cap, F115, pal["faint"]).pack(side="top", anchor="w",
                                                    pady=(0, uk.px(6)))
            sel = uk.RoundSelect(cell, pal, values, var, height=34,
                                 back=pal["bg"], editable=editable)
            sel.pack(side="top", fill="x")
            selects[field_name] = sel
        self.res_cb = selects["res"]
        self.hz_cb = selects["hz"]
        self.br_cb = selects["br"]
        self.res_cb.bind("<<ComboboxSelected>>", self._res_changed)
        self.hz_cb.bind("<<ComboboxSelected>>", self._hz_changed)
        self.br_cb.bind("<<ComboboxSelected>>", self._bitrate_changed)
        self._load_resolutions()

        # The custom size is typed where the pick that asks for it was made.
        # Sending people into the settings to type two numbers, one click after
        # a dropdown that says "custom", was a detour through a door.
        self.custom_row = tk.Frame(top, bg=pal["bg"])
        self.custom_row.pack(side="top", fill="x")
        self.custom_line, cval = row_with(self.custom_row,
                                          i18n.t("自定义尺寸", "Custom size"),
                                          (0, uk.px(6)))
        uk.RoundEntry(cval, pal, textvariable=self.w_var, chars=6,
                      height=30).pack(side="left", padx=(0, uk.px(7)))
        lab(cval, "×", F125, pal["dim"]).pack(side="left", padx=(0, uk.px(7)))
        uk.RoundEntry(cval, pal, textvariable=self.h_var, chars=6,
                      height=30).pack(side="left", padx=(0, uk.px(11)))
        lab(cval, i18n.t("改完立刻生效；跟随手机时以手机为准",
                         "Applies at once; matching the phone takes precedence"),
            F115, pal["faint"]).pack(side="left")
        # A typed-in size changes the screen just as much as the dropdown does.
        self.w_var.trace_add("write", self._layout_changed)
        self.h_var.trace_add("write", self._layout_changed)
        self.custom_line.pack_forget()

        # -- the action row --------------------------------------------------
        arow = tk.Frame(top, bg=pal["bg"])
        arow.pack(side="top", fill="x", pady=(uk.px(2), uk.px(14)))
        self.summary_var = tk.StringVar(value="")
        lab(arow, "", F125, pal["fg"], textvariable=self.summary_var).pack(
            side="left")
        self.btn_stop = uk.RoundButton(arow, pal, i18n.t("停止", "Stop"),
                                       kind="ghost", height=38, font=F125,
                                       icon="■", command=self.on_stop,
                                       back=pal["bg"])
        self.btn_stop.pack(side="right")
        self.btn_stop.set_enabled(False)
        self.btn_start = uk.RoundButton(arow, pal, i18n.t("开始投屏", "Start"),
                                        kind="primary", height=38, font=F13,
                                        icon="▶", command=self.on_start,
                                        back=pal["bg"])
        self.btn_start.pack(side="right", padx=(0, uk.px(8)))

        # -- one quiet detail line -------------------------------------------
        # 2.2.1 -- the display path and the capture card used to be the first
        # two rows under the status line.  They answer a diagnostic question
        # ("why is the picture black?"), not the one this window is opened for,
        # so they share one dim line below the button now.  A driver that needs
        # installing still gets a button here, in the same place as before.
        drow = tk.Frame(top, bg=pal["bg"])
        drow.pack(side="top", fill="x", pady=(0, uk.px(10)))
        self.drv_var = tk.StringVar(value=i18n.t("检测中…", "Detecting..."))
        self.gpu_var = tk.StringVar(value=i18n.t("——", "-"))
        self.detail_var = tk.StringVar(value="")
        lab(drow, "", F105, pal["faint"], textvariable=self.detail_var,
            wraplength=uk.px(780), justify="left").pack(side="left")
        # The install button only appears when there is something to install:
        # on a working setup this is a line of text, exactly as drawn.
        self.btn_install = uk.RoundButton(drow, pal, "", kind="soft",
                                          command=self.on_install_driver,
                                          height=26, font=F115, back=pal["bg"])
        self._drv_action = "install"
        for _dv in (self.drv_var, self.gpu_var):
            _dv.trace_add("write", self._sync_detail)
        self._sync_detail()

        # -- log -------------------------------------------------------------
        logcard = uk.Card(body, pal, fill=pal["mono_bg"], border=True,
                          radius=12, pad=(0, 0))
        logcard.pack(side="top", fill="both", expand=True)
        self._logcard = logcard
        lbar = tk.Frame(logcard.inner, bg=pal["mono_bg"], height=uk.px(34))
        lbar.pack(side="top", fill="x")
        lbar.pack_propagate(False)
        lab(lbar, i18n.t("日志", "Log"), F11, pal["mono_dim"],
            back=pal["mono_bg"]).pack(side="left", padx=(uk.px(14), 0))
        self.log_hint_var = tk.StringVar(value="")
        lab(lbar, "", F115, pal["mono_dim"], back=pal["mono_bg"],
            textvariable=self.log_hint_var).pack(side="left",
                                                 padx=(uk.px(12), 0))

        def log_link(caption, cmd):
            widget = lab(lbar, caption, F115, pal["mono_dim"],
                         back=pal["mono_bg"], cursor="hand2")
            widget.pack(side="right", padx=(0, uk.px(14)))
            widget.bind("<Button-1>", lambda _e: cmd())
            widget.bind("<Enter>",
                        lambda _e: widget.configure(fg=pal["mono_fg"]))
            widget.bind("<Leave>",
                        lambda _e: widget.configure(fg=pal["mono_dim"]))
            return widget

        # Packed right to left, so the drawing's order -- copy, save, clear,
        # expand -- is the order they are read in.
        self._btn_expand = log_link(i18n.t("展开 ▾", "Expand ▾"),
                                    self.on_toggle_log)
        log_link(i18n.t("清空", "Clear"), self.on_clear_log)
        log_link(i18n.t("另存", "Save"), self.on_save_log)
        log_link(i18n.t("复制", "Copy"), self.on_copy_log)

        self.log_txt = tk.Text(logcard.inner, bg=pal["mono_bg"],
                               fg=pal["mono_fg"],
                               insertbackground=pal["mono_fg"],
                               font=MONO, wrap="none", relief="flat", bd=0,
                               highlightthickness=0, padx=uk.px(14),
                               pady=uk.px(8), spacing1=0, spacing2=uk.px(6),
                               insertwidth=0, selectbackground=pal["sel"],
                               selectforeground=pal["sel_fg"])
        self.log_txt.pack(side="top", fill="both", expand=True)
        self.log_txt.configure(state="disabled")
        self.log_txt.tag_configure("err", foreground=pal["red"])
        self.log_txt.tag_configure("ok", foreground=pal["green"])
        self.log_txt.tag_configure("hl", foreground=pal["yellow"])

        # The settings window is built here, hidden, so that every widget name
        # the rest of this file reaches for exists from the first moment -- the
        # form helpers, refresh_env and _sync_match all poke at boxes that now
        # live over there, and none of them should have to ask whether the
        # window has been opened yet.
        self._install_resize()
        self._build_settings_view()
        self._sync_summary()
        self._arm_perf()

    # -------------------------------------------------------------- settings
    # The second window.  It is built together with the main one and kept
    # withdrawn: everything the main window no longer shows has to exist as a
    # widget name anyway (_apply_settings_to_form, _form_values and refresh_env
    # all reach for these boxes), and building it once at start-up costs a few
    # milliseconds and saves a pile of "has the window been opened yet" checks.
    def _build_settings_view(self):
        # 2.2.0 -- one window, not two.  The settings are a view of the same
        # window, so the gear and the "more settings" link swap the body
        # instead of opening a second frame that floats over the picture.
        pal = self.pal
        view = tk.Frame(self._host, bg=pal["bg"])
        self.view_set = view

        head = tk.Frame(view, bg=pal["bg"], height=uk.px(42))
        head.pack(side="top", fill="x", padx=(uk.px(18), uk.px(18)))
        head.pack_propagate(False)
        uk.RoundButton(head, pal, "←", command=self._close_settings,
                       height=28, width=28, kind="ghost", border=False,
                       font=tkfont.Font(family=theme.UI, size=-uk.px(13)),
                       fg=pal["dim"], back=pal["bg"]).pack(
                           side="left", pady=(uk.px(7), 0))
        tk.Label(head, text=i18n.t("设置", "Settings"), bg=pal["bg"],
                 fg=pal["fg"], font=(theme.UI, -uk.px(14), "bold")).pack(
                     side="left", padx=(uk.px(8), 0), pady=(uk.px(7), 0))
        uk.RoundButton(head, pal, i18n.t("完成", "Done"), kind="primary",
                       command=self._close_settings, height=28, pad_x=14,
                       font=tkfont.Font(family=theme.UI, size=-uk.px(12)),
                       back=pal["bg"]).pack(side="right", pady=(uk.px(7), 0))

        body = tk.Frame(view, bg=pal["bg"])
        body.pack(side="top", fill="both", expand=True,
                  padx=(uk.px(24), uk.px(24)), pady=(uk.px(2), uk.px(18)))

        nav = tk.Frame(body, bg=pal["bg"], width=uk.px(150))
        nav.pack(side="left", fill="y", padx=(0, uk.px(10)))
        nav.pack_propagate(False)

        pages = (
            ("picture", i18n.t("画面", "Picture")),
            ("codec", i18n.t("编码与画质", "Encoding")),
            ("device", i18n.t("设备", "Device")),
            ("boot", i18n.t("启动与退出", "Start-up")),
            ("notify", i18n.t("通知", "Notifications")),
            ("look", i18n.t("外观", "Appearance")),
            ("manual", i18n.t("使用说明", "Manual")),
            ("about", i18n.t("关于", "About")),
        )
        self._nav_btns = {}
        self._nav_font = tkfont.Font(family=theme.UI, size=-uk.px(12.5))
        self._nav_font_on = tkfont.Font(family=theme.UI, size=-uk.px(12.5),
                                        weight="bold")
        for i, (key, cap) in enumerate(pages):
            b = uk.RoundButton(nav, pal, cap, kind="ghost", height=32,
                               pad_x=12, border=False, radius=8,
                               font=self._nav_font,
                               command=lambda k=key: self._show_page(k))
            b.pack(side="top", fill="x", pady=(0, uk.px(2)))
            self._nav_btns[key] = b

        colwrap = tk.Frame(body, bg=pal["bg"])
        colwrap.pack(side="left", fill="both", expand=True)
        colcv = tk.Canvas(colwrap, bg=pal["bg"], bd=0, highlightthickness=0,
                          takefocus=0)
        colcv.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(colcv, padding=(uk.px(10), uk.px(4), 0, 0))
        content_window = colcv.create_window((0, 0), window=content,
                                             anchor="nw")
        self._set_cv = colcv
        self._set_cv_win = content_window
        content.bind("<Configure>", lambda _e: colcv.configure(
            scrollregion=colcv.bbox("all")))
        colcv.bind("<Configure>", lambda e: colcv.itemconfigure(
            content_window, width=e.width))
        colcv.bind("<MouseWheel>", self._page_wheel)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self._pages = {}
        for key, _cap in pages:
            f = ttk.Frame(content)
            f.grid(row=0, column=0, sticky="nsew")
            f.grid_remove()
            self._pages[key] = f

        def newpage(key, title):
            page = self._pages[key]
            column = ttk.Frame(page)
            column.pack(fill="both", expand=True, anchor="w")
            return column

        def section(parent, title, gap=16):
            # One titled group.  Groups are told apart by spacing, like the
            # main window -- there is not a hairline anywhere.
            wrap = ttk.Frame(parent)
            wrap.pack(fill="x", anchor="w", pady=(0, uk.px(gap)))
            tk.Label(wrap, text=title, bg=self.pal["bg"],
                     fg=self.pal["dim"],
                     font=(theme.UI, -uk.px(12.5), "bold")).pack(
                anchor="w", pady=(0, uk.px(8)))
            sec = ttk.Frame(wrap)
            sec.pack(fill="x", anchor="w")
            sec.columnconfigure(0, minsize=uk.px(88))
            sec.columnconfigure(1, weight=1)
            return sec

        def caption(parent, r, text):
            ttk.Label(parent, text=text, style="Key.TLabel").grid(
                row=r, column=0, sticky="w", pady=(0, uk.px(9)),
                padx=(0, uk.px(16)))

        def note(parent, text):
            ttk.Label(parent, text=text, style="Dim.TLabel",
                      wraplength=uk.px(560), justify="left").pack(
                          anchor="w", pady=(0, uk.px(14)))

        # -- 画面 ------------------------------------------------------------
        pict = newpage("picture", i18n.t("画面", "Picture"))
        s = section(pict, i18n.t("显示模式", "Display mode"))
        caption(s, 0, i18n.t("模式", "Mode"))
        self.mode_var = tk.StringVar(value="extend")
        mf = ttk.Frame(s)
        mf.grid(row=0, column=1, sticky="w", pady=(0, uk.px(9)))
        for v, t in (("extend", i18n.t("拓展屏", "Extend")),
                     ("mirror", i18n.t("镜像", "Mirror"))):
            uk.RoundRadio(mf, self.pal, text=t, value=v,
                          variable=self.mode_var,
                          command=self._mode_changed).pack(
                              side="left", padx=(0, uk.px(16)))

        self.match_var = tk.BooleanVar(value=bool(DEFAULTS.get("match_phone", True)))
        self.match_chk = uk.RoundCheck(
            s, self.pal, variable=self.match_var, command=self._match_changed,
            text=i18n.t("跟随手机分辨率（撑满整块屏，不留黑边）",
                        "Match the phone's resolution (fills the panel, no bars)"))
        self.match_chk.grid(row=1, column=0, columnspan=2, sticky="w",
                            pady=(0, uk.px(9)))

        # The custom width and height are typed on the main window now, right
        # under the dropdown that asks for them.  Boxes here would be a second
        # place to say the same thing -- and the second place is always the one
        # that is out of date.

        s = section(pict, i18n.t("屏幕位置", "Position"))
        self.pos_var = tk.StringVar(value="right")
        caption(s, 0, i18n.t("位置", "Position"))
        self.pos_cb = uk.RoundSelect(s, self.pal,
                                     [t for _, t in POSITIONS],
                                     self.pos_var, width=12, auto_width=True,
                                     min_width=210)
        self.pos_cb.grid(row=0, column=1, sticky="w", pady=(0, uk.px(9)))
        self.pos_cb.bind("<<ComboboxSelected>>", self._pos_changed)

        self.pos_frame = ttk.Frame(s)
        self.pos_x_var = tk.StringVar(value="0")
        self.pos_y_var = tk.StringVar(value="0")
        ttk.Label(self.pos_frame, text="X").pack(
            side="left", padx=(0, uk.px(7)))
        uk.RoundEntry(self.pos_frame, self.pal, textvariable=self.pos_x_var,
                      chars=5, height=30).pack(
                          side="left", padx=(0, uk.px(11)))
        ttk.Label(self.pos_frame, text="Y").pack(
            side="left", padx=(0, uk.px(7)))
        uk.RoundEntry(self.pos_frame, self.pal, textvariable=self.pos_y_var,
                      chars=5, height=30).pack(
                          side="left", padx=(0, uk.px(11)))
        ttk.Label(self.pos_frame,
                  text=i18n.t("虚拟屏左上角在主屏坐标系里的位置",
                              "top-left corner of the virtual screen, in "
                              "main-screen coordinates"),
                  style="Dim.TLabel").pack(side="left", padx=(uk.px(6), 0))
        self.pos_frame.grid(row=1, column=0, columnspan=2, sticky="w",
                            pady=(0, uk.px(9)))
        self.pos_frame.grid_remove()
        self.pos_x_var.trace_add("write", self._layout_changed)
        self.pos_y_var.trace_add("write", self._layout_changed)

        s = section(pict, i18n.t("触控", "Touch"))
        self.touch_var = tk.BooleanVar(value=bool(DEFAULTS.get("touch", True)))
        self.touch_chk = uk.RoundCheck(
            s, self.pal, variable=self.touch_var, command=self._touch_choice,
            text=i18n.t("手机触摸控制电脑", "Phone touch controls the PC"))
        self.touch_chk.grid(row=1, column=0, columnspan=2, sticky="w",
                            pady=(0, uk.px(9)))
        # 2.2.1 -- the pointer switch moved down here from the main window: it
        # is a small on/off that nobody flips twice a day, and it belongs with
        # the other input option rather than above the button that starts the
        # stream.
        self.mouse_var = tk.BooleanVar(value=False)
        self.mouse_chk = uk.RoundCheck(
            s, self.pal, variable=self.mouse_var,
            text=i18n.t("显示鼠标指针", "Draw the mouse pointer"))
        self.mouse_chk.grid(row=2, column=0, columnspan=2, sticky="w",
                            pady=(0, uk.px(9)))

        note(pict, i18n.t(
            "分辨率、刷新率和码率在主窗口的「画面」一格里，改完立刻生效；"
            "选「自定义」后，宽高就在下面那行直接输入。",
            "Resolution, refresh rate and bitrate sit in the Picture row of the "
            "main window and apply at once; pick \"Custom\" and the width and "
            "height are typed on the line right below it."))

        # -- 编码与画质 ------------------------------------------------------
        enc = newpage("codec", i18n.t("编码与画质", "Encoding and quality"))
        s = section(enc, i18n.t("编码器", "Encoder"))
        self.enc_var = tk.StringVar(value="h264_nvenc")
        self.enc_cb = uk.RoundSelect(s, self.pal, [], self.enc_var,
                                     width=24, auto_width=True,
                                     min_width=260)
        self.enc_cb.grid(row=0, column=1, sticky="w", pady=(0, uk.px(9)))
        self.enc_cb.bind("<<ComboboxSelected>>", self._enc_changed)

        self.pre_var = tk.StringVar(value="p4")
        caption(s, 1, i18n.t("预设", "Preset"))
        self.pre_cb = uk.RoundSelect(s, self.pal, [], self.pre_var,
                                     width=14, auto_width=True,
                                     min_width=210)
        self.pre_cb.grid(row=1, column=1, sticky="w", pady=(0, uk.px(9)))

        s = section(enc, i18n.t("码率", "Bitrate"))
        self.rc_var = tk.StringVar(value=self._rc_caption())
        caption(s, 0, i18n.t("码率控制", "Rate control"))
        self.rc_cb = uk.RoundSelect(s, self.pal,
                                    [t for _, t in RATECONTROL],
                                    self.rc_var, width=30, auto_width=True,
                                    min_width=260)
        self.rc_cb.grid(row=0, column=1, sticky="w", pady=(0, uk.px(9)))
        self.rc_cb.bind("<<ComboboxSelected>>", self._rc_changed)

        # The ceiling itself is typed in the Picture row of the main window; the
        # only thing worth a box here is which rate control bends around it.
        s = section(enc, i18n.t("画质", "Quality"))
        caption(s, 0, i18n.t("画质 Q", "Quality Q"))
        self.q_cb = uk.RoundSelect(s, self.pal, list(QUALITY_CHOICES),
                                   self.q_var, width=96, back=self.pal["bg"])
        self.q_cb.grid(row=0, column=1, sticky="w", pady=(0, uk.px(9)))
        self.q_cb.bind("<<ComboboxSelected>>", self._rc_changed)

        ttk.Label(enc, textvariable=self.q_hint_var, style="Dim.TLabel",
                  wraplength=uk.px(560), justify="left").pack(
                      anchor="w", pady=(0, uk.px(8)))
        note(enc, i18n.t(
            "显卡支持时优先用 HEVC：同样画质省一半带宽。libx264 只用 CPU，"
            "发热大但兼容性最好。",
            "Where the card allows it, HEVC gives the same picture at about half "
            "the bandwidth. libx264 runs on the CPU: hotter, but it always works."))

        # -- 设备 ------------------------------------------------------------
        dev = newpage("device", i18n.t("设备", "Device"))
        s = section(dev, i18n.t("采集", "Capture"))
        self.be_var = tk.StringVar(value="auto")
        caption(s, 0, i18n.t("采集方式", "Capture backend"))
        self.be_cb = uk.RoundSelect(s, self.pal, [t for _, t in BACKENDS],
                                    self.be_var, width=20, auto_width=True,
                                    min_width=210)
        self.be_cb.grid(row=0, column=1, sticky="w", pady=(0, uk.px(9)))


        self.inst_var = tk.BooleanVar(value=True)
        self.inst_chk = uk.RoundCheck(
            s, self.pal, variable=self.inst_var,
            text=i18n.t("每次开始投屏都重新安装 APK", "Reinstall the APK every start"))
        self.inst_chk.grid(row=1, column=0, columnspan=2, sticky="w",
                           pady=(0, uk.px(9)))

        s = section(dev, i18n.t("数据线", "Cable"))
        self.link_var = tk.StringVar(value="")
        capf = ttk.Frame(s)
        capf.grid(row=0, column=0, columnspan=2, sticky="ew")
        uk.RoundButton(capf, self.pal,
                       i18n.t("测一下数据线速度", "Measure cable speed"),
                       command=self.on_linkspeed, kind="soft",
                       height=32).pack(side="left")
        ttk.Label(capf, textvariable=self.link_var, style="Dim.TLabel",
                  wraplength=uk.px(330), justify="left").pack(side="left",
                                                              padx=(uk.px(12), 0))

        s = section(dev, i18n.t("桌面上的显示器", "Displays on the desktop"))
        self.mon_var = tk.StringVar(value="")
        ttk.Label(s, textvariable=self.mon_var, style="Mono.TLabel",
                  justify="left", wraplength=uk.px(460)).grid(
                      row=0, column=0, columnspan=2, sticky="w")

        s = section(dev, i18n.t("虚拟显示器驱动", "Virtual display driver"), gap=0)
        drvf = ttk.Frame(s)
        drvf.grid(row=0, column=0, columnspan=2, sticky="ew",
                  pady=(0, uk.px(6)))
        uk.RoundButton(drvf, self.pal,
                       i18n.t("安装 / 修复虚拟显示器驱动…",
                              "Install / repair the virtual display driver..."),
                       command=self.on_install_driver, kind="soft",
                       height=32).pack(side="left")
        uk.RoundButton(drvf, self.pal,
                       i18n.t("卸载虚拟显示器驱动…",
                              "Remove the virtual display driver..."),
                       command=self.on_uninstall_driver, kind="soft",
                       height=32).pack(side="left", padx=(uk.px(9), 0))

        s = section(dev, i18n.t("诊断与工具", "Diagnosis and tools"), gap=0)
        for _i, (_cap, _cmd) in enumerate((
                (i18n.t("诊断连接问题…", "Diagnose a connection problem..."),
                 self.on_diagnose),
                (i18n.t("安装 USB 驱动…", "Install the USB driver..."),
                 self.on_install_usb_driver),
                (i18n.t("仅安装 APK 并启动", "Install the APK only and start"),
                 self.on_install_apk),
                (i18n.t("清理残留的虚拟屏", "Clean up a leftover virtual screen"),
                 self.on_cleanup_orphans),
                (i18n.t("彻底关闭虚拟屏", "Shut the virtual screen down for real"),
                 self.on_vdd_off))):
            uk.RoundButton(s, self.pal, _cap, kind="soft", height=32,
                           command=_cmd).grid(row=_i // 2, column=_i % 2,
                                              sticky="w", padx=(0, uk.px(9)),
                                              pady=(0, uk.px(8)))

        # -- 启动与退出 ------------------------------------------------------
        boot = newpage("boot", i18n.t("启动与退出", "Start-up and closing"))
        s = section(boot, i18n.t("开机", "At boot"))
        self.auto_var = tk.BooleanVar(value=bool(self.settings.get("autostart", False)))
        self.auto_chk = uk.RoundCheck(
            s, self.pal, variable=self.auto_var, command=self._sync_autostart,
            text=i18n.t("开机自动启动 DroidExtend", "Start DroidExtend with Windows"))
        self.auto_chk.grid(row=0, column=0, columnspan=2, sticky="w",
                           pady=(0, uk.px(9)))

        self.stream_auto_var = tk.BooleanVar(
            value=bool(self.settings.get("auto_stream", False)))
        self.stream_auto_chk = uk.RoundCheck(
            s, self.pal, variable=self.stream_auto_var,
            command=self._auto_stream_choice,
            text=i18n.t("开机后自动开始投屏", "Start streaming after boot"))
        self.stream_auto_chk.grid(row=1, column=0, columnspan=2, sticky="w",
                                  pady=(0, uk.px(9)))

        caption(s, 2, i18n.t("开机后最多等手机", "Wait for the phone up to"))
        self.wait_var = tk.StringVar(value=_wait_caption(
            int(self.settings.get("auto_stream_wait", 60) or 0)))
        wf = ttk.Frame(s)
        wf.grid(row=2, column=1, sticky="w", pady=(0, uk.px(9)))
        self.wait_cb = uk.RoundSelect(wf, self.pal, [t for _, t in WAITS],
                                      self.wait_var, width=12, auto_width=True,
                                      min_width=110)
        self.wait_cb.pack(side="left")
        self.wait_cb.bind("<<ComboboxSelected>>", self._wait_changed)
        ttk.Label(wf, text=i18n.t("等不到会弹一条系统通知",
                                  "a notification if it never shows up"),
                  style="Dim.TLabel").pack(side="left", padx=(uk.px(9), 0))

        s = section(boot, i18n.t("关闭", "Closing"))
        caption(s, 0, i18n.t("点关闭按钮时", "When the close button is pressed"))
        cf = ttk.Frame(s)
        cf.grid(row=0, column=1, sticky="w", pady=(0, uk.px(9)))
        self.close_cap_var = tk.StringVar(value=self._close_action_caption())
        ttk.Label(cf, textvariable=self.close_cap_var, style="Dim.TLabel").pack(
            side="left")
        ttk.Button(cf, text=i18n.t("更改…", "Change..."),
                   style="Link.TButton",
                   command=self.on_close_behavior).pack(side="left", padx=(uk.px(12), 0))

        s = section(boot, i18n.t("退出时对虚拟屏", "The virtual screen on exit"), gap=0)
        self.off_mode_var = tk.StringVar(
            value=self.settings.get("vdd_off_mode", "deep"))
        self.off_deep_btn = uk.RoundRadio(
            s, self.pal, value="deep", variable=self.off_mode_var,
            command=self._off_mode_choice,
            text=i18n.t("彻底关闭（开机也不出现，远程软件看不到）",
                        "Switch it off for real (absent at boot, invisible to "
                        "remote software)"))
        self.off_deep_btn.grid(row=0, column=0, columnspan=2, sticky="w",
                               pady=(0, uk.px(4)))
        self.off_desk_btn = uk.RoundRadio(
            s, self.pal, value="desktop", variable=self.off_mode_var,
            command=self._off_mode_choice,
            text=i18n.t("只移出桌面（不弹授权，但屏还在系统里）",
                        "Just take it off the desktop (no prompt, it still exists)"))
        self.off_desk_btn.grid(row=1, column=0, columnspan=2, sticky="w",
                               pady=(0, uk.px(9)))
        note(boot, i18n.t(
            "彻底关闭会在退出时禁用一次虚拟显示器设备节点，之后远程软件"
            "（Parsec、GameViewer 之类）就看不到多出来的那块屏。",
            "Switching it off for real disables the virtual display device node on "
            "exit, so remote software (Parsec, GameViewer and the like) stops "
            "seeing the extra screen."))

        # -- 外观 ------------------------------------------------------------
        look = newpage("look", i18n.t("外观", "Appearance"))
        s = section(look, i18n.t("颜色", "Colour"))
        caption(s, 0, i18n.t("深浅色", "Colour scheme"))
        thf = ttk.Frame(s)
        thf.grid(row=0, column=1, sticky="w", pady=(0, uk.px(9)))
        for code, cap in (("auto", i18n.t("跟随 Windows", "Follow Windows")),
                          ("light", i18n.t("浅色", "Light")),
                          ("dark", i18n.t("深色", "Dark"))):
            uk.RoundRadio(thf, self.pal, text=cap, value=code,
                          variable=self.theme_var,
                          command=lambda c=code: self._set_theme(c)).pack(
                              side="left", padx=(0, uk.px(16)))

        s = section(look, i18n.t("语言", "Language"))
        lgf = ttk.Frame(s)
        lgf.grid(row=0, column=1, sticky="w", pady=(0, uk.px(9)))
        for code, cap in i18n.choices():
            uk.RoundRadio(lgf, self.pal, text=cap, value=code,
                          variable=self.lang_var,
                          command=lambda c=code: self._switch_lang(c)).pack(
                              side="left", padx=(0, uk.px(16)))

        # -- 通知 ------------------------------------------------------------
        # 2.2.1 -- a page of its own.  Ten switches folded into a grey group
        # under "Appearance" hid the thing people come here for: which of these
        # pop up at all.
        ntf = newpage("notify", i18n.t("通知", "Notifications"))
        s = section(ntf, i18n.t("总开关", "Master switch"))
        nf = ttk.Frame(s)
        nf.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, uk.px(9)))
        uk.RoundCheck(
            nf, self.pal, variable=self.notify_var,
            command=lambda: self._set_notify("notify", self.notify_var),
            text=i18n.t("使用 Windows 通知", "Use Windows notifications")).pack(
                anchor="w")
        nf2 = ttk.Frame(s)
        nf2.grid(row=1, column=0, columnspan=2, sticky="w")
        uk.RoundButton(nf2, self.pal,
                       i18n.t("发一条测试通知", "Send a test notification"),
                       command=self.on_test_notify, kind="soft",
                       height=32).pack(side="left")
        uk.RoundButton(nf2, self.pal,
                       i18n.t("Windows 通知设置…",
                              "Windows notification settings..."),
                       command=lambda: self.open_url("ms-settings:notifications"),
                       kind="soft", height=32).pack(
                           side="left", padx=(uk.px(9), 0))

        s = section(ntf, i18n.t("哪一类要提醒我", "Which of these should tell me"),
                    gap=0)
        for _i, (_kind, _zh, _en) in enumerate(NOTIFY_KINDS):
            _var = getattr(self, "_nvar_" + _kind, None)
            if _var is None:
                continue
            uk.RoundCheck(
                s, self.pal, variable=_var,
                command=lambda k=_kind, v=_var: self._set_notify("notify_" + k, v),
                text=i18n.t(_zh, _en)).grid(
                    row=_i, column=0, columnspan=2, sticky="w",
                    pady=(0, uk.px(7)))

        # -- 使用说明 --------------------------------------------------------
        man = newpage("manual", i18n.t("使用说明", "Manual"))
        s = section(man, i18n.t("文档", "Documents"))
        for _i, (_cap, _anchor) in enumerate((
                (i18n.t("打开使用说明", "Open the manual"), None),
                # The anchors are headings inside manual.py, which ships two
                # complete manuals rather than one translated text -- so the
                # anchor has to be the heading of the language in use.
                (i18n.t("常见问题", "Common questions"),
                 i18n.t("【六、常见问题】", "[6] Common questions")),
                # 1.9.1 -- the questions that arrived with 1.8.4 and later have
                # their own heading: mixing them into the old list made people
                # read eight answers to find the one that applied.
                (i18n.t("最近几版新出现的问题", "Problems new in recent versions"),
                 i18n.t("—— 1.8.4 以后这几版新出现的问题 ——",
                        "-- problems new since 1.8.4 --")),
                (i18n.t("参数怎么选", "How to choose the settings"),
                 i18n.t("【五、参数怎么选】", "[5] How to choose the settings")))):
            uk.RoundButton(
                s, self.pal, _cap, kind="soft", height=32,
                command=(lambda a=_anchor: self.on_manual(a))).grid(
                    row=_i, column=0, columnspan=2, sticky="w",
                    pady=(0, uk.px(7)))
        note(man, i18n.t(
            "华为 / 鸿蒙设备连不上的那一份，是项目主页里的单独一页，"
            "从「关于」页的链接打开。",
            "The Huawei / HarmonyOS checklist is a page of its own on the "
            "project site; the About page links to it."))

        # -- 关于 ------------------------------------------------------------
        abt = newpage("about", i18n.t("关于", "About"))
        s = section(abt, i18n.t("版本", "Version"))
        ttk.Label(s, text="%s  v%s" % (APP_NAME, APP_VERSION),
                  style="Big.TLabel").grid(row=0, column=0, columnspan=2,
                                           sticky="w", pady=(0, uk.px(6)))
        self.file_var = tk.StringVar(
            value=i18n.t("数据目录: %s", "Data folder: %s") % paths.data_dir())
        ttk.Label(s, textvariable=self.file_var, style="Dim.TLabel",
                  wraplength=uk.px(480), justify="left").grid(
                      row=1, column=0, columnspan=2, sticky="w")

        s = section(abt, i18n.t("链接", "Links"), gap=0)
        lf = ttk.Frame(s)
        lf.grid(row=0, column=0, columnspan=2, sticky="w")
        for i, (cap, cmd) in enumerate((
                (i18n.t("项目主页（GitHub）", "Project home (GitHub)"),
                 lambda: self.open_url(REPO_URL)),
                (i18n.t("反馈问题 / 提建议", "Report a problem / suggest something"),
                 lambda: self.open_url(ISSUES_URL)),
                (i18n.t("最近几版改了什么", "What changed recently"),
                 self.on_show_changelog),
                (i18n.t("使用条款与隐私说明", "Terms and privacy"),
                 self.on_show_privacy),
                (i18n.t("第三方组件许可", "Third-party licences"), self.on_licenses),
                (i18n.t("请作者喝杯奶茶…", "Buy the author a milk tea..."),
                 self.on_donate))):
            ttk.Button(lf, text=cap, style="Link.TButton", command=cmd).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, uk.px(18)),
                pady=uk.px(1))

        s = section(abt, i18n.t("更新", "Updates"))
        uk.RoundCheck(
            s, self.pal, variable=self.chk_var, command=self._save_settings,
            text=i18n.t("启动时自动检查更新",
                        "Check for updates on start")).grid(
                            row=0, column=0, columnspan=2, sticky="w",
                            pady=(0, uk.px(6)))
        # 1.9.4 -- GitHub's /releases/latest never returns a pre-release, so
        # looking at betas is a separate, opt-in switch.
        uk.RoundCheck(
            s, self.pal, variable=self.pre_upd_var,
            command=self._save_settings,
            text=i18n.t("也检查测试版（beta）",
                        "Include beta releases")).grid(
                            row=1, column=0, columnspan=2, sticky="w",
                            pady=(0, uk.px(9)))
        uf = ttk.Frame(s)
        uf.grid(row=2, column=0, columnspan=2, sticky="w")
        uk.RoundButton(uf, self.pal, i18n.t("立刻检查一次", "Check now"),
                       kind="soft", height=32,
                       command=lambda: self.on_check_updates(manual=True)).pack(
                           side="left")

        s = section(abt, i18n.t("日志与目录", "Log and folders"), gap=0)
        lg = ttk.Frame(s)
        lg.grid(row=0, column=0, columnspan=2, sticky="w")
        for _i, (_cap, _cmd) in enumerate((
                (i18n.t("复制全部日志到剪贴板", "Copy all logs to the clipboard"),
                 self.on_copy_log),
                (i18n.t("日志另存为…", "Save the log as..."), self.on_save_log),
                (i18n.t("打开数据目录（设置与日志）",
                        "Open the data folder (settings and log)"),
                 self.on_open_config_dir),
                (i18n.t("打开程序目录", "Open the program folder"),
                 self.on_open_root_dir))):
            ttk.Button(lg, text=_cap, style="Link.TButton",
                       command=_cmd).grid(row=_i // 2, column=_i % 2,
                                          sticky="w", padx=(0, uk.px(18)),
                                          pady=uk.px(1))

        rf = ttk.Frame(abt)
        rf.pack(fill="x", anchor="w", pady=(uk.px(18), 0))
        uk.RoundButton(rf, self.pal,
                       i18n.t("把设置恢复成默认值…", "Reset the settings..."),
                       command=self.on_reset_settings, kind="soft",
                       height=32).pack(side="left")
        uk.RoundButton(rf, self.pal,
                       i18n.t("退出 DroidExtend", "Quit DroidExtend"),
                       command=self._on_close, kind="soft",
                       height=32).pack(side="left", padx=(uk.px(9), 0))

        for _f in self._pages.values():
            self._bind_page_wheel(_f)
        self._bind_page_wheel(colcv)

        # Every setting change is written straight out, so even a crash leaves
        # the next start on the settings you picked.  add="+" keeps the existing
        # handlers (which fix up presets and the Q field) running first.
        for cb in (self.res_cb, self.hz_cb, self.br_cb, self.rc_cb,
                   self.q_cb, self.enc_cb, self.pre_cb, self.be_cb, self.pos_cb):
            cb.bind("<<ComboboxSelected>>", self._changed_any, add="+")
        for chk in (self.mouse_chk, self.inst_chk):
            chk.configure(command=self._changed_any)

    def _close_action_caption(self):
        return {"ask": i18n.t("每次都问我", "Ask me every time"),
                "tray": i18n.t("收进托盘", "Minimize to tray"),
                "quit": i18n.t("直接退出", "Exit")}.get(
                    self.close_action, str(self.close_action))

    def _drag_window(self, win, widget):
        # Move a frameless Toplevel by dragging the widget -- its title bar.
        state = {}

        def press(event):
            state["dx"] = event.x_root - win.winfo_x()
            state["dy"] = event.y_root - win.winfo_y()

        def move(event):
            if "dx" not in state:
                return
            win.geometry("+%d+%d" % (event.x_root - state["dx"],
                                     event.y_root - state["dy"]))

        widget.bind("<Button-1>", press, add="+")
        widget.bind("<B1-Motion>", move, add="+")

    def _open_settings(self, page=None):
        if getattr(self, "view_set", None) is None:
            return
        self._show_view("set")
        self._show_page(page or getattr(self, "_page", "picture"))
        self.after(30, self._fit_settings)
        self.after(60, lambda: self.mon_var.set(self._monitor_text()))

    def _fit_settings(self):
        # One height for every page.  A settings view that grows and shrinks as
        # you page through it reads as a different window each time, and the
        # long pages scroll instead (see _page_wheel).
        if getattr(self, "_maxed", False):
            return
        if int(self.winfo_height()) < uk.px(640):
            try:
                self.geometry("%dx%d" % (self.winfo_width(), uk.px(640)))
            except Exception:
                pass

    def _page_wheel(self, event):
        cv = getattr(self, "_set_cv", None)
        if cv is None:
            return
        try:
            cv.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    def _bind_page_wheel(self, widget):
        widget.bind("<MouseWheel>", self._page_wheel, add="+")
        for child in widget.winfo_children():
            self._bind_page_wheel(child)

    def _close_settings(self):
        if getattr(self, "view_set", None) is None:
            return
        self._show_view("main")
        if not getattr(self, "_maxed", False):
            try:
                self.geometry("%dx%d" % (self.winfo_width(), uk.px(640)))
            except Exception:
                pass

    def _show_page(self, key):
        if key not in getattr(self, "_pages", {}):
            key = "picture"
        self._page = key
        for k, f in self._pages.items():
            try:
                f.grid() if k == key else f.grid_remove()
            except Exception:
                pass
        for k, b in self._nav_btns.items():
            try:
                if hasattr(b, "set_selected"):
                    b.set_selected(k == key)
                    b.set_font(getattr(self, "_nav_font_on", None) if k == key
                               else getattr(self, "_nav_font", None))
                else:
                    b.configure(style="NavOn.TButton" if k == key
                                else "Nav.TButton")
            except Exception:
                pass

    # ------------------------------------------------------------ appearance
    def _set_theme(self, mode):
        mode = mode if mode in theme.MODES else "auto"
        self.theme_mode = mode
        try:
            self.theme_var.set(mode)
        except Exception:
            pass
        self.settings["theme"] = mode
        try:
            st.save(self.settings)
        except Exception:
            pass
        kind = theme.resolve(mode)
        if kind != self._theme_kind:
            self._theme_kind = kind
            self.pal = theme.PALETTES[kind]
            self._apply_theme()

    def _apply_titlebar(self, win=None):
        """Give the window frame the same scheme as the page.

        Windows paints a light title bar over a dark page (and the other way
        round) unless the program asks for the immersive dark frame, and Tk
        has no setting for it.  Without this the redesign reads as a dark
        sheet inside a white picture frame.
        """
        if os.name != "nt":
            return
        if win is None:
            # The main window draws its own title bar; there is no frame left
            # to paint.
            try:
                if self.overrideredirect():
                    return
            except Exception:
                pass
        target = self if win is None else win
        hwnd = 0
        try:
            # wm_frame() is the toplevel frame Tk actually draws: winfo_id()
            # is the client child, and its parent is not always the frame.
            hwnd = int(target.wm_frame(), 16)
        except Exception:
            hwnd = 0
        if not hwnd:
            try:
                hwnd = ctypes.windll.user32.GetAncestor(target.winfo_id(), 2)
            except Exception:
                return
        if not hwnd:
            return
        try:
            dark = ctypes.c_int(1 if self._theme_kind == "dark" else 0)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(dark),
                        ctypes.sizeof(dark)) == 0:
                    break
        except Exception:
            pass
        try:
            rgb = self.pal["line"].lstrip("#")
            col = ctypes.c_int(int(rgb[0:2], 16) | (int(rgb[2:4], 16) << 8)
                               | (int(rgb[4:6], 16) << 16))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 34, ctypes.byref(col), ctypes.sizeof(col))
        except Exception:
            pass

    def _apply_theme(self):
        """Rebuild the window in the new colours, then put Settings back up.

        The same path as a language switch: Tk cannot recolour widgets in place,
        and rebuilding means every colour -- including the ones in the manual and
        about windows -- is produced from the current palette.
        """
        page = getattr(self, "_page", "picture")
        try:
            self._rebuild_window()
        except Exception as e:
            self.log("重建窗口失败: %s" % e, "err")
            return
        try:
            self._open_settings(page)
        except Exception:
            pass

    def _watch_theme(self):
        """Windows switching between light and dark while we are running.

        There is no event for this in Tk, so it costs a registry read every
        2.5 seconds -- on a GUI that is already polling the session every 700ms
        that is noise, and it means the window follows the system the way the
        settings say it should.
        """
        try:
            kind = theme.resolve(self.theme_mode)
            if kind != self._theme_kind:
                self._theme_kind = kind
                self.pal = theme.PALETTES[kind]
                self._apply_theme()
        except Exception:
            pass
        try:
            self._theme_job = self.after(2500, self._watch_theme)
        except Exception:
            pass

    # ---------------------------------------------------------------- summary
    def _sync_summary(self):
        """The one-line description of what the next start will use."""
        bits = []
        try:
            name = self._enc_name()
            if name:
                bits.append(name)
        except Exception:
            pass
        try:
            # The raw value, not the caption: the caption is a whole sentence
            # ("VBR, quality first: quiet desktop, smaller stream") and it used
            # to push the Start button off the right edge of the window.
            bits.append(str(self._rc_value).upper())
        except Exception:
            pass
        try:
            res = (self.res_var.get() or "").strip()
            if res == _custom_label():
                w = (self.w_var.get() or "").strip()
                h = (self.h_var.get() or "").strip()
                res = ("%sx%s" % (w, h)) if (w and h) else ""
            hz = (self.hz_var.get() or "").strip()
            if res and hz:
                bits.append("%s@%s" % (res, hz))
            elif res:
                bits.append(res)
        except Exception:
            pass
        text = " · ".join([b for b in bits if b])
        try:
            self.summary_var.set(i18n.t("当前配置: ", "Current: ") + text)
        except Exception:
            pass

    def _set_dot(self, kind):
        col = {"green": self.pal["green"], "yellow": self.pal["yellow"],
               "red": self.pal["red"]}.get(kind, self.pal["faint"])
        try:
            self.dot_cv.itemconfigure(self._dot_id, fill=col)
        except Exception:
            try:
                self.dot_lbl.configure(fg=col, bg=self.pal["bg"])
            except Exception:
                pass

    def _set_metric(self, key, text):
        item = getattr(self, "_metric_labels", {}).get(key)
        if not item:
            return
        var, _lbl = item
        try:
            var.set(text)
        except Exception:
            pass

    def _arm_perf(self):
        """Arm the 700ms performance tick, cancelling the previous one.

        _build() runs again on every language switch; without the cancel each
        switch would leave one more copy of this self-rescheduling callback
        behind, and the window would do more work after the third switch than
        after the first.
        """
        if self._perf_job is not None:
            try:
                self.after_cancel(self._perf_job)
            except Exception:
                pass
            self._perf_job = None
        self._perf_job = self.after(900, self._poll_perf)

    # ------------------------------------------------------------------ utils
    def _ask_install_apk(self, had, want, name):
        """Ask whether to update the app on the phone, on the UI thread.

        Reached from the session's worker thread, so the question is handed to
        the event loop and this waits for the answer.  Waiting is fine: nothing
        else is happening yet, and the alternative -- installing silently --
        restarts the app under the user without a word.
        """
        box = {}
        done = threading.Event()

        # 1.9.1 -- at boot the window can still be sitting in the tray, and a
        # modal question nobody can see would hold the start for five minutes.
        # Say it with a balloon and leave the phone alone this time: the 每次
        # 重新安装 APK box, or one run with the window open, settles it.
        try:
            hidden = self.state() == "withdrawn"
        except Exception:
            hidden = False
        if hidden:
            self._notify(
                "apk",
                i18n.t("手机端 App 可以更新", "The phone app can be updated"),
                i18n.t("手机上是 versionCode %d，这个版本带的是 %d（%s）。"
                       "打开窗口再点一次「开始投屏」就会问你要不要装。",
                       "The phone has versionCode %d; this build ships %d (%s). "
                       "Open the window and press Start again and you will be asked.")
                % (had, want, name))
            return False

        def ask():
            try:
                box["yes"] = messagebox.askyesno(
                    i18n.t("手机上是 versionCode %d，这个版本带的是 %d（%s）。" + NL + NL +
                           "现在更新手机端 App 吗？不更新也能继续投屏，但新功能用不上。",
                           "The phone has versionCode %d, this build ships %d (%s)." + NL + NL +
                           "Update the app on the phone now?  Streaming works either "
                           "way, but the new features will not.") % (had, want, name),
                    parent=self)
            except Exception:
                box["yes"] = False
            finally:
                done.set()

        try:
            self.after(0, ask)
        except Exception:
            return False
        done.wait(300)
        return bool(box.get("yes"))

    def _on_gpu(self, text):
        """Show which GPU the capture is using (called from a worker thread)."""
        if text is None:
            text = i18n.t("——", "-")
        self._later(lambda: self.gpu_var.set(text))

    def log(self, msg, tag=None):
        stamped = time.strftime("%H:%M:%S ") + str(msg)
        self.logq.put((stamped, tag))
        # The pane is not a record: it dies with the window, and the failures
        # worth reading about (driver refused, adb failed, capture restarted)
        # happen while nobody is looking at it.
        applog.write(stamped)

    def _phone_notice(self, code, msg):
        """The phone reported something about itself (screen off/on, app gone).

        A phone whose screen went off shows the last frame forever, which from
        this side is indistinguishable from a crashed capture -- so say which
        one it is instead of leaving the user to guess.
        """
        self.log(msg, "err" if code == tu.APP_GONE else None)

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
                self._log_lines.append((text, tag))
                if int(self.log_txt.index("end-1c").split(".")[0]) > 800:
                    # "201.0" and not "200.0": tkinter deletes the half-open
                    # range [1.0, 201.0), which is 200 whole lines -- the same
                    # 200 the buffer drops, so the two never drift apart.
                    self.log_txt.delete("1.0", "201.0")
                    del self._log_lines[:200]
                self.log_txt.see("end")
                self.log_txt.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(120, self._drain_log)

    def _load_encoders(self):
        """Fill the encoder menu, then correct it with the truth.

        Pass one is instant and only answers "does this ffmpeg build contain the
        encoder?" -- enough to have a usable menu the moment the window opens.

        Pass two runs every one of them for real, in the background, and rebuilds
        the menu from the ones that produced bytes.  The two questions have
        different answers here, and only the second one matters to the user: this
        build lists h264_qsv / hevc_qsv / av1_qsv and the AMD core in this laptop
        lists av1_amf, and not one of those four can encode anything --
        "Error creating a MFX session: -9", and no AV1 encoder block.  They sat
        in the menu looking exactly as available as h264_nvenc, which is why
        "还是有部分视频编码不可用" was a fair complaint.
        """
        built = [k for k in ENCODERS if k in list_hardware_encoders()] or ["libx264"]
        self._set_enc_items(built)
        self.log("可用编码器（检测中）: " + ", ".join(built))

        def work():
            try:
                ok = [k for k in ENCODERS if k in available_encoders()]
            except Exception:
                ok = list(built)
            self._later(lambda: self._encoders_probed(built, ok))

        threading.Thread(target=work, daemon=True).start()

    def _encoders_probed(self, built, ok):
        # If the probing itself failed there is nothing to correct, so keep the
        # menu we already have rather than empty it.
        items = list(ok) if ok else list(built)
        gone = [k for k in built if k not in items]
        self._set_enc_items(items)
        if gone:
            self.log(i18n.t("本机跑不动这些编码器，已从列表里去掉: %s",
                            "Not offered: these cannot encode on this machine: %s")
                     % ", ".join(gone))
        else:
            self.log("编码器检测完成: " + ", ".join(items))

    def _set_enc_items(self, items):
        # Keep whatever the settings file asked for, as long as this machine can
        # actually encode with it; only fall back when it cannot.  (The old code
        # always forced h264_nvenc/libx264 and threw a saved choice away.)
        if self.enc_var.get() not in items:
            self.enc_var.set("h264_nvenc" if "h264_nvenc" in items else items[0])
        self.enc_cb["values"] = ["%s — %s" % (k, ENCODERS[k]["label"]) for k in items]
        self._enc_items = items
        self.enc_cb.set("%s — %s" % (self.enc_var.get(), ENCODERS[self.enc_var.get()]["label"]))
        self._enc_changed()

    def _load_resolutions(self):
        """Offer the sizes and rates the driver will actually accept.

        Read straight out of vdd_settings.xml, which works before the display is
        attached.  Getting this wrong is invisible from the outside: the menu
        looked complete, the click went through, ChangeDisplaySettingsEx quietly
        answered DISP_CHANGE_BADMODE and the only symptom was "改了分辨率还是没
        有变化".  Falls back to the static list when there is no driver to ask.
        """
        import vdd as vd
        try:
            sizes, rates = vd.declared_modes()
        except Exception:
            sizes, rates = None, None
        if sizes:
            values = ["%dx%d" % s for s in sizes]
            hzs = [str(r) for r in rates] or list(HZ_CHOICES)
        else:
            values = ["%dx%d" % r for r in RESOLUTIONS]
            hzs = list(HZ_CHOICES)
        self.res_cb["values"] = values + [_custom_label()]
        self.hz_cb["values"] = hzs

        want = self.res_var.get()
        if want != _custom_label() and want not in values:
            try:
                ww, hh = (int(v) for v in want.split("x"))
                pick = "%dx%d" % min(sizes, key=lambda s: abs(s[0] - ww) + abs(s[1] - hh))
            except Exception:
                pick = values[0]
            self.log(i18n.t("显示模式表里没有 %s，改用 %s", "No %s in the mode table; using %s")
                     % (want, pick))
            self.res_var.set(pick)
        if self.hz_var.get() not in hzs:
            self.hz_var.set(hzs[0])

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
        custom = self.res_var.get() == _custom_label()
        if custom:
            self.custom_line.pack(side="top", fill="x")
        else:
            self.custom_line.pack_forget()
        if custom and self.match_var.get():
            # the two are mutually exclusive: picking a custom size means the
            # user wants that size, not whatever the phone reports
            self.match_var.set(False)
        self._sync_match()
        self._maybe_retarget()

    def _hz_changed(self, *_):
        """One field, two numbers.

        Refresh rate and frame rate were two boxes with the same answer in
        them: the host runs at min(fps, panel Hz) anyway, so picking 120 Hz and
        then 60 fps here only made it 60, and picking 60 Hz with 120 fps only
        made it 60.  Whatever the panel is asked for is what the host aims at.
        """
        self.fps_var.set(self.hz_var.get())
        self._layout_changed()

    def _bitrate_changed(self, *_):
        self._layout_changed()

    def _match_changed(self):
        if self.match_var.get() and self.res_var.get() == _custom_label():
            self.res_var.set("%dx%d" % (RESOLUTIONS[0][0], RESOLUTIONS[0][1]))
        self._sync_match()
        self._maybe_retarget()

    def _sync_match(self):
        """Greying out the size box is the whole point: when the phone decides,
        the dropdown is a lie waiting to happen."""
        follow = self.match_var.get()
        self.res_cb.configure(state="disabled" if follow else "readonly")
        self.match_chk.grid() if not (self.res_var.get() == _custom_label()) else self.match_chk.grid_remove()

    # --------------------------------------------------- live size/position
    def _layout_changed(self, *_):
        """Everything that can move or resize the virtual screen funnels here."""
        self._maybe_retarget()

    def _sync_pos(self):
        """Show the X / Y boxes only when "custom" is the position that was picked.

        Compared against the caption, not against the value, because this is a
        writable-in-theory readonly combobox: what it holds is what the user
        sees.  Nothing here depends on the caption *text* -- `_custom_label()`
        returns whatever the active language calls it -- so switching language
        cannot change which branch is taken.
        """
        if self.pos_var.get() == _custom_label():
            self.pos_frame.grid()
        else:
            self.pos_frame.grid_remove()

    def _pos_changed(self, *_):
        self._sync_pos()
        self._maybe_retarget()

    def _maybe_retarget(self):
        """Apply a size / position change to a stream that is already running.

        apply_display() only ever ran on 开始投屏, so a new resolution picked
        while streaming was saved and then quietly ignored -- nothing ever asked
        Windows to switch the mode.  That is most of what "分辨率有时无法正常切换"
        turns out to be.  Debounced, because one dropdown drag fires several
        events and every retarget restarts ffmpeg.
        """
        s = getattr(self, "session", None)
        if s is None or not s.running:
            return
        if self._retarget_job is not None:
            try:
                self.after_cancel(self._retarget_job)
            except Exception:
                pass
        self._retarget_job = self.after(600, self._do_retarget)

    def _do_retarget(self):
        self._retarget_job = None
        s = self.session
        if s is None or not s.running:
            return
        try:
            self._apply_form()
        except Exception as e:
            self.log("设置未生效: %s" % e)
            return
        self.run_bg(s.retarget)

    def _rate_control_index(self):
        """Which RATECONTROL row the dropdown is showing, by the value stored.

        Matching the value rather than the caption: the caption is translated,
        so after a language switch a caption comparison would silently fall
        through to the default and the user's CBR choice would come back as VBR.
        """
        for i, (v, _t) in enumerate(RATECONTROL):
            if v == self._rc_value:
                return i
        return 0

    def _rc_caption(self):
        return RATECONTROL[self._rate_control_index()][1]

    def _value_for(self, pairs, text, default):
        """Resolve what a combobox holds to the value it stands for.

        The box holds a translated caption, so this accepts the raw value first
        -- which keeps a hand-edited settings.json working after a language
        switch, when the caption it was saved as no longer exists -- and only
        then matches on the caption.  Never the other way round: a caption is
        display text and is allowed to change.
        """
        text = (text or "").strip()
        for v, _t in pairs:
            if v == text:
                return v
        for v, t in pairs:
            if t == text:
                return v
        return default

    def _rate_control(self):
        """The value behind whatever the rate-control box currently shows."""
        return self._value_for(RATECONTROL, self.rc_var.get(), self._rc_value)

    def _rc_changed(self, *_):
        rc = self._rate_control()
        self._rc_value = rc
        if rc == "vbr":
            self.q_hint_var.set(i18n.t(
                "Q 越小画质越好、码率越高（18≈视觉无损）",
                "A lower Q means better quality and a higher bitrate (18 is close to lossless)"))
        else:
            self.q_hint_var.set(i18n.t(
                "CBR 下 Q 只作为上限，码率恒定占满上限",
                "Under CBR, Q is only a ceiling -- the bitrate stays pinned at the ceiling"))
        if getattr(self, "q_cb", None) is not None:
            self.q_cb.configure(state="normal" if rc == "vbr" else "disabled")

    # ------------------------------------------------------ terms and privacy
    def _check_privacy(self):
        """#1: the terms, once, before this program does anything else.

        What is stored is a version string rather than a flag, so changing the
        terms later re-asks everyone instead of silently applying to people who
        agreed to a different text.
        """
        if not priv.needs_accept(self.settings):
            return
        if not priv.ask(self):
            self.log("没有同意使用条款，程序退出。")
            self.destroy()
            return
        priv.accept(self.settings)
        self._save_settings()
        self.log("已接受使用条款与隐私说明（第 %s 版）" % priv.VERSION)

    def on_show_privacy(self):
        """Re-read the terms from the menu.  Answering changes nothing here."""
        priv.ask(self)

    # ------------------------------------------------- orphan virtual screen
    def _check_orphans(self):
        """#10: deal with a virtual screen that is on with nothing streaming.

        Switching the screen on went through ChangeDisplaySettingsEx with
        CDS_UPDATEREGISTRY, which Windows persists; switching it off only ever
        took effect in the current session.  So the switch-on came back at every
        boot and the switch-off did not, and the screen sat there activated with
        DroidExtend never started.  detach() saves to the database now.

        1.6.4: when the user picked "switch it off for real", take a leftover down
        ourselves instead of only saying so -- that whole choice exists because
        they do not want the screen there.  Detaching costs no UAC and is undone
        the moment a stream starts, so there is nothing to ask about.  In "just
        take it off the desktop" mode this stays a nudge.
        """
        try:
            orphans = [m for m in wd.active_virtual_displays() if not m["primary"]]
        except Exception:
            return
        if not orphans:
            return
        names = "、".join(m["name"] for m in orphans)
        if (self.settings.get("vdd_off_mode") or "deep").lower() == "deep":
            self.log("没有在投屏，但虚拟屏 %s 还开着，按设置把它移出桌面" % names)
            self.run_bg(self._cleanup_orphans_work)
            return
        self.log("注意：没有在投屏，但虚拟屏 %s 仍处于激活状态（桌面会多出一块空屏）" % names)
        self.log("      菜单「工具 → 清理残留的虚拟屏」可以关掉它。")

    def on_cleanup_orphans(self):
        self.run_bg(self._cleanup_orphans_work)

    def _cleanup_orphans_work(self):
        if self.session.running:
            self.log("正在投屏，先停止再清理")
            try:
                self.session.stop()
            except Exception as e:
                self.log("停止投屏失败: %s" % e)
        try:
            gone = wd.cleanup_orphans(log=self.log)
        except Exception as e:
            self.log("清理失败: %s" % e)
            return
        if gone:
            self.log("已关闭 %d 块残留的虚拟屏：%s" % (len(gone), "、".join(gone)))
            # The session may still remember that screen as "the one I put on the
            # desktop" (stopping a stream no longer detaches it, 1.7.1).  Forget it
            # so that quitting later does not try to take down something that is
            # already gone.
            self.session._display_applied = None
        else:
            self.log("没有需要清理的虚拟屏")

    # ------------------------------------------------------ really off (1.6.4)
    def _off_mode_choice(self):
        self._save_settings()
        if self.off_mode_var.get() == "deep":
            self.log(i18n.t(
                "「退出程序时」已改成彻底关闭：退出程序时会把虚拟屏的设备节点禁用掉——"
                "下次开机不会有这块屏，远程软件也看不到它。平时的「停止」只是把屏从桌面"
                "上摘掉、不碰驱动，所以再点开始投屏只要一秒多。程序本身已经以管理员"
                "身份运行，所以这一步不会再额外弹授权。",
                "\"When the program exits\" is now switch it off for real: exiting "
                "disables the virtual display's device node, so the screen is not there "
                "at the next boot and remote software cannot see it.  A plain Stop only "
                "takes the screen off the desktop and leaves the driver alone, so "
                "starting again takes about a second.  The program already runs as "
                "administrator, so this step no longer asks for approval."))
        else:
            self.log(i18n.t(
                "「退出程序时」已改成只移出桌面：退出程序时只是把它从桌面上摘掉，"
                "驱动不碰、不弹授权，但这块屏仍然存在于系统里。平时的「停止」也是"
                "只摘桌面，所以两者在这一档上没有区别。",
                "\"When the program exits\" is now just take it off the desktop: exiting "
                "only detaches it, the driver is left alone and nothing asks for "
                "approval, but the screen still exists in the system.  A plain Stop "
                "already detaches the screen, so in this mode the two are the same."))

    def on_vdd_off(self):
        self.run_bg(self._vdd_off_work)

    def _vdd_off_work(self):
        import vdd as vd

        if self.session.running:
            self.log("正在投屏，先停止再关闭")
            try:
                self.session.stop()
            except Exception as e:
                self.log("停止投屏失败: %s" % e)
        st = vd.status()
        if st["state"] not in ("ready", "node"):
            self.log("虚拟屏还没装好，没有可关闭的东西")
            return
        ok, text = vd.set_enabled(False, log=self.log)
        if ok:
            self.session._display_applied = None
            self.log(i18n.t(
                "虚拟屏已彻底关闭：开机不会有这块屏，远程软件也看不到它",
                "The virtual display is switched off for real: it will not appear at "
                "boot and remote software will not see it"))
            return
        self.log(i18n.t("彻底关闭没成功（%s），至少先把它移出桌面",
                        "Could not switch it off for real (%s); at least taking it off "
                        "the desktop") % (str(text)[:200] or i18n.t("未知原因", "unknown reason")))
        try:
            gone = wd.cleanup_orphans(log=self.log)
        except Exception as e:
            self.log("移出桌面也失败了: %s" % e)
            return
        if gone:
            self.log("已关闭 %d 块残留的虚拟屏：%s" % (len(gone), "、".join(gone)))

    # ------------------------------------------------------ connection check
    def on_diagnose(self):
        self.run_bg(self._diagnose_work)

    def _diagnose_work(self):
        """#14: name the dull cause instead of leaving the user to guess at it.

        "It says connected but nothing shows up" has about four causes and all
        of them are readable from here: the phone is in charge-only mode, the
        USB driver is missing, adb never saw the device, or something else on
        this machine holds one of the two ports the tunnels land on.
        """
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
        # 1.8.3: who the phone is, and -- for Huawei/HarmonyOS -- the three
        # things that make a perfectly good cable look like a dead one.  The
        # properties cost one adb round trip per device, not one per question.
        huawei = False
        for d in devs:
            if d["state"] != "device":
                continue
            info = adb.phone_info(a, d["serial"])
            self.log("    %s" % adb.describe_phone(info, d["serial"]))
            if adb.is_huawei(info):
                huawei = True
        if huawei:
            for line in adb.huawei_hints():
                self.log("  " + line)
        if not devs:
            self.log("  没有检测到手机", "err")
            # The raw text is the only evidence left when the list is empty, and
            # it is exactly what an issue report needs -- so print it whole.
            raw = getattr(a, "last_devices_out", "") if a is not None else ""
            body = [l.strip() for l in raw.splitlines()[1:] if l.strip()]
            if body:
                self.log("  adb devices -l 原始输出：")
                for line in body:
                    self.log("    " + line)
            h = adb.usb_mode_hint(devs)
            if h:
                self.log("  " + h)
            for status, cls, name in adb.android_problem_devices():
                self.log("  设备管理器：%s（%s）" % (name, status))
            self.log("  驱动下载：%s" % adb.DRIVER_URL)
            # A Huawei user with DevEco Studio installed has a perfect driver
            # and still no device, so this comes last and says what to quit.
            for line in adb.huawei_hints():
                self.log("  " + line)
        elif not any(d["state"] == "device" for d in devs):
            self.log("  手机在，但 adb 不能对它下命令（看上面的状态）", "err")
            self.log("  unauthorized = 手机上没点「允许 USB 调试」；offline / no permissions = 授权或驱动有问题")
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

    def on_install_usb_driver(self):
        self.run_bg(self._install_usb_driver_work)

    def _install_usb_driver_work(self):
        """#14: install the bundled driver if we ship one, else point at it.

        Google's USB driver cannot be redistributed with the app, so the usual
        answer is the download link -- but the code path is here so a build that
        does carry one works without any further work.
        """
        import adb
        done, msg = adb.install_usb_driver()
        if done:
            self.log(msg)
            return
        self.log(msg, "err")
        self.log("  先试试这个：手机下拉通知栏，把「USB 用途」改成「传输文件 / MTP」，"
                 "Windows 多半会自己把驱动装上。")
        self.open_url(adb.DRIVER_URL)

    def on_linkspeed(self):
        self.run_bg(self._linkspeed_work)

    def _linkspeed_work(self):
        import linkspeed
        from adb import Adb
        self._later(lambda: self.stat_var.set(i18n.t("测量链路…", "Measuring the link...")))
        adb = Adb()
        rows = [r for r in adb.devices() if r["state"] == "device"]
        if not rows:
            raise RuntimeError(i18n.t("没有可用的安卓设备，先插好数据线",
                                      "No Android device available -- plug the cable in first"))
        serial = rows[0]["serial"]
        if self.session and self.session.running:
            # the stream and the probe fight over the same cable, so the number
            # comes out low for reasons that have nothing to do with the cable
            self.log("注意：投屏正在进行，测速结果会偏低，建议先停止投屏再测。", "err")
        self.log("正在测量 %s 的 USB 链路（约 6 秒）…" % serial)
        r = linkspeed.measure(adb, serial, log=self.log)
        if r["error"]:
            self._later(lambda: self.link_var.set(
                i18n.t("链路测速失败: ", "Link speed test failed: ") + r["error"]))
            self._later(lambda: self.stat_var.set(i18n.t("测速失败", "Speed test failed")))
            return
        v = linkspeed.verdict(r["mbps"])
        # leave a little headroom for protocol overhead and bursts
        cap = max(2, int(r["mbps"] * 0.75))
        # `v` is linkspeed's own verdict text and is not translated here -- see
        # the note in the report; everything this file owns is.
        text = i18n.t("链路 %.1f Mbps（%s）→ 建议码率上限不超过 %d Mbps",
                      "Link %.1f Mbps (%s) -> keep the bitrate ceiling at %d Mbps or less") % (
            r["mbps"], v, cap)
        self._later(lambda: self.link_var.set(text))
        self._later(lambda: self.stat_var.set(i18n.t("测速完成", "Speed test done")))
        if int(self.br_var.get()) > cap:
            self.log("当前码率上限 %s Mbps 高于链路可承载的 %d Mbps，已自动下调。"
                     % (self.br_var.get(), cap), "err")
            self._later(lambda: self.br_var.set(str(cap)))
        self.log(text, "ok")

    def _mode_changed(self):
        pass

    def _backend(self):
        return self._value_for(BACKENDS, self.be_var.get(), "auto")

    def _position(self):
        return self._value_for(POSITIONS, self.pos_var.get(), "right")

    # ------------------------------------------------------------- background
    def run_bg(self, fn, done=None, exclusive=True):
        # An exclusive job owns the start button and the `busy` flag.  The
        # environment refresh must not: it runs 300ms after launch and takes a
        # couple of seconds (adb server + driver status), so a click on 开始投屏
        # during that window used to hit "上一个任务还在进行中" - a modal dialog
        # that blocks the UI and makes the button look broken.
        if exclusive:
            if self.busy:
                messagebox.showinfo(
                    i18n.t("请稍候", "One moment"),
                    i18n.t("上一个任务还在进行中。", "The previous task is still running."))
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
                    err = i18n.t(
                        "虚拟显示器未就绪：%s\n\n请先点“%s”。",
                        "The virtual display is not ready: %s\n\n"
                        "Press \"%s\" first.") % (e, _install_driver_label())
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
        # A start or a retarget can write a new row into the driver's mode table
        # (see Session._resolve_mode), so the size menu is rebuilt from it here.
        try:
            self._load_resolutions()
        except Exception:
            pass
        if err:
            self.log(err.replace("\n", " "), "err")
            self.stat_var.set(i18n.t("出错", "Error"))
            # A start that dies half way (phone unplugged, the driver refused
            # the mode) takes the screen back off the desktop: leaving a screen
            # nobody is streaming to is how the mouse ends up lost on an empty
            # 3200 px.  The device node stays enabled, so retrying is quick.
            try:
                if self.session and self.session.attached_display():
                    self.session.stop()
            except Exception:
                pass
            if done != "quiet":
                messagebox.showerror(i18n.t("出错了", "Something went wrong"), err)
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
                i18n.t("已连接但未授权: ", "Connected but not authorised: ")
                + ", ".join(r["serial"] + "=" + r["state"] for r in rows)))
        else:
            self._later(lambda: self.dev_var.set(i18n.t(
                "未检测到设备（请用数据线连接并打开 USB 调试）",
                "No device found (connect the USB cable and turn on USB debugging)")))

        st = vd.status()
        self._later(lambda: self.drv_var.set(vd.summary_line(st)))
        self._later(lambda: self._sync_driver_button(st))

        self._later(lambda: self.mon_var.set(self._monitor_text()))
        if st["state"] != "ready":
            self.log("虚拟显示器状态: " + vd.summary_line(st))

    # Monitor list as text; safe to call from any thread.
    def _monitor_text(self):
        try:
            mons = wd.live_monitors()
        except Exception:
            mons = []
        text = "\n".join("%-12s %-28s %dx%d@%d  @(%d,%d)%s" % (
            m["name"], (m["monitor"] or m["desc"])[:28], m["width"], m["height"],
            m["hz"], m["left"], m["top"],
            i18n.t("  [主]", "  [primary]") if m["primary"] else "") for m in mons)
        return text or i18n.t("（无）", "(none)")

    def _sync_detail(self, *_args):
        """One dim line: where the virtual display is, and which card captures.

        Both strings are already written by the worker (drv_var from vd.status,
        gpu_var from the encoder probe); this only joins them, so the main
        window needs no extra polling.
        """
        parts = []
        drv = str(self.drv_var.get() or "").strip()
        if drv:
            parts.append(drv)
        gpu = str(self.gpu_var.get() or "").strip()
        if gpu and gpu not in ("-", i18n.t("——", "-")):
            parts.append(i18n.t("采集显卡 ", "Capture via ") + gpu)
        self.detail_var.set("  ·  ".join(parts))

    def _sync_driver_button(self, st):
        """Paint install / turn-on / installed on the left panel's button.

        Greyed out is not the whole story: the Tools menu keeps a working
        install entry, because a broken install has to be re-done over itself,
        and the caption names the state so nobody has to read the log to find
        out whether the driver is there.
        """
        action, caption = _driver_button(st)
        self._drv_action = action
        try:
            if action == "done":
                # Nothing to do: this row is a line of text, exactly as drawn.
                self.btn_install.pack_forget()
            else:
                self.btn_install.configure(text=caption, state="normal")
                if not self.btn_install.winfo_ismapped():
                    self.btn_install.pack(side="right", padx=(uk.px(12), 0))
        except Exception:
            pass

    # ---------------------------------------------------------------- actions
    def on_install_driver(self):
        # 1.9.1 -- the same button also covers "installed, but the node is
        # switched off", which is the normal state after an exit with the
        # off-for-real setting.  Turning it on costs one UAC prompt and a few
        # seconds; reinstalling would copy files that are already there.
        if getattr(self, "_drv_action", "install") == "enable":
            if not messagebox.askyesno(
                    i18n.t("启用虚拟显示器驱动",
                           "Turn the virtual display driver on"),
                    i18n.t(
                        "驱动已经装好了，只是现在处于关闭状态"
                        "（退出程序时选了「彻底关闭」，所以它不会自己出现）。"
                        + NL + NL +
                        "现在就把它打开吗？需要管理员权限，会弹一次 UAC，请点“是”。",
                        "The driver is installed; it is only switched off "
                        "(the program disables it on exit when the off-for-real "
                        "setting is selected, so it does not come back by itself)."
                        + NL + NL +
                        "Turn it on now?  It needs administrator rights, so one "
                        "UAC prompt -- click Yes."),
                    parent=self):
                return
            self.run_bg(self._enable_driver_work)
            return
        if not messagebox.askyesno(
                i18n.t("安装虚拟显示器驱动", "Install the virtual display driver"),
                i18n.t(
                    "将安装 Virtual Display Driver 25.7.23（已签名）。\n\n"
                    "接下来会弹出 Windows 的 UAC 授权窗口，请点击“是”，"
                    "否则虚拟显示器无法创建。\n\n继续吗？",
                    "Virtual Display Driver 25.7.23 (signed) will be installed.\n\n"
                    "Windows will ask for UAC permission next -- click Yes, or the "
                    "virtual display cannot be created.\n\nContinue?")):
            return
        self.run_bg(self._install_driver_work)

    def _install_driver_work(self):
        import vdd as vd
        self._later(lambda: self.stat_var.set(i18n.t("安装驱动…", "Installing the driver...")))
        ok, out = vd.install(log=self.log)
        self._log_install_output(out)
        if not ok:
            # vd.install() hands back a human explanation now, not a bare "see
            # the log": on the machine that reported 0xFFFF0000 the log was
            # empty, so pointing at it told the user nothing.  The dialog gets
            # the first paragraph, the log panel keeps the whole thing.
            head = self._first_paragraph(
                out, i18n.t("驱动安装未成功，详见日志。",
                            "The driver was not installed -- see the log."))
            if not self._ask_cmd_fallback(head):
                raise RuntimeError(head)
            # 1.8.7 -- the .ps1 path needs Windows PowerShell.  On the machine
            # that reported it, PowerShell itself could not start (exit code
            # 0xFFFF0000, empty log), so no amount of retrying the same script
            # would have helped.  The fallback does the same work with
            # certutil and nefcon, neither of which needs PowerShell.
            self.log("改用不依赖 PowerShell 的备用方式安装驱动…")
            self._later(lambda: self.stat_var.set(
                i18n.t("安装驱动（备用方式）…", "Installing the driver (fallback)...")))
            ok, out = vd.install_cmd(log=self.log)
            self._log_install_output(out)
            if not ok:
                raise RuntimeError(self._first_paragraph(
                    out, i18n.t("备用方式也没能装上驱动，详见日志。",
                                "The fallback did not install the driver either -- "
                                "see the log.")))
            self.log("备用方式安装驱动完成。", "ok")
        self.log("虚拟显示器驱动安装完成。", "ok")
        self._later(lambda: self.stat_var.set(i18n.t("驱动已安装", "Driver installed")))
        # 1.9.1 -- this can take a minute behind a UAC prompt, and the window
        # may well be in the tray by then.  Say it out loud.
        self._notify("driver",
                     i18n.t("虚拟显示器驱动已装好", "The virtual display driver is installed"),
                     i18n.t("现在可以点「开始投屏」了。", "You can press Start now."))
        self._refresh_work()

    def _enable_driver_work(self):
        """Turn the device node back on instead of reinstalling the driver."""
        import vdd as vd
        self._later(lambda: self.stat_var.set(
            i18n.t("启用虚拟屏…", "Turning the virtual display on...")))
        ok, text = vd.set_enabled(True, log=self.log)
        if not ok:
            raise RuntimeError(self._first_paragraph(
                text, i18n.t("虚拟屏没能启用，详见日志。",
                             "The virtual display could not be turned on -- "
                             "see the log.")))
        self.log("虚拟显示器驱动已启用。", "ok")
        self._later(lambda: self.stat_var.set(i18n.t("驱动已启用", "Driver on")))
        self._notify("driver",
                     i18n.t("虚拟显示器驱动已打开", "The virtual display driver is on"),
                     i18n.t("现在可以点「开始投屏」了。", "You can press Start now."))
        self._refresh_work()

    def on_uninstall_driver(self):
        # 1.9.1 -- a way back out.  Until this existed the only way to remove
        # the driver was Device Manager, and the files left behind in
        # C:\VirtualDisplayDriver made a half-removed install look installed.
        answer = messagebox.askyesno(
            i18n.t("卸载虚拟显示器驱动", "Remove the virtual display driver"),
            i18n.t(
                "将删除驱动包和虚拟屏的设备节点，需要管理员权限（会弹一次 UAC）。"
                + NL + NL +
                "下面这些会保留：" + NL +
                "  * C:\\VirtualDisplayDriver 里的分辨率表"
                "（你自己加过的尺寸都还在）；" + NL +
                "  * 本程序的全部设置。" + NL + NL +
                "卸载之后投屏用不了，直到重新安装驱动。继续吗？",
                "This removes the driver package and the virtual screen's device "
                "node. It needs administrator rights, so one UAC prompt."
                + NL + NL +
                "These are kept:" + NL +
                "  * the resolution table in C:\\VirtualDisplayDriver "
                "(every size you added by hand stays);" + NL +
                "  * all of this program's settings." + NL + NL +
                "Streaming will not work until the driver is installed again. "
                "Continue?"),
            parent=self)
        if not answer:
            return
        self.run_bg(self._uninstall_driver_work)

    def _uninstall_driver_work(self):
        import vdd as vd
        # A screen still attached to the desktop cannot be removed cleanly:
        # detach first, exactly like Stop does, so the desktop is back to one
        # screen before the device node disappears.
        s = self.session
        if s is not None and (s.running or s.attached_display()):
            self._later(lambda: self.stat_var.set(
                i18n.t("先停止投屏…", "Stopping the stream first...")))
            s.stop()
        self._later(lambda: self.stat_var.set(
            i18n.t("卸载驱动…", "Removing the driver...")))
        ok, out = vd.uninstall(log=self.log)
        self._log_install_output(out)
        if not ok:
            raise RuntimeError(self._first_paragraph(
                out, i18n.t("驱动没有卸载干净，详见日志。",
                            "The driver was not fully removed -- see the log.")))
        self.log("虚拟显示器驱动已卸载（分辨率表和设置都留着）。", "ok")
        self._later(lambda: self.stat_var.set(
            i18n.t("驱动已卸载", "Driver removed")))
        self._notify("driver",
                     i18n.t("虚拟显示器驱动已卸载", "The virtual display driver was removed"),
                     i18n.t("分辨率表和本程序的设置都留着；要投屏时再装一次驱动。",
                            "The resolution table and the settings are kept; "
                            "install the driver again when you need to stream."))
        self._refresh_work()

    def _log_install_output(self, out):
        for line in (out or "").splitlines()[-60:]:
            self.log("  " + line)

    @staticmethod
    def _first_paragraph(out, fallback):
        """The first paragraph of a failure message, cut down for a dialog.

        vd.install()/vd.install_cmd() return "what happened" + two newlines +
        the log tail, and the hint() text can run several paragraphs.  A
        messagebox that scrolls off the screen is no better than no message.
        """
        msg = (out or "").strip()
        head = msg.split(chr(10) * 2)[0].strip()
        if len(head) > 600:
            head = head[:600] + "…"
        return head or fallback

    def _ask_cmd_fallback(self, head):
        """Ask, on the UI thread, whether to retry with the cmd installer."""
        box = {}
        done = threading.Event()

        def ask():
            try:
                box["yes"] = messagebox.askyesno(
                    i18n.t("驱动没装上，用备用方式再试一次？",
                           "The driver failed -- retry with the fallback?"),
                    i18n.t(
                        "%s" + NL + NL +
                        "这次的失败和 Windows PowerShell 有关：有些精简过的系统里它根本启动不了。" + NL +
                        "程序里还有一条完全不用 PowerShell 的路：用 Windows 自带的 certutil 和随包的 nefcon 做同样的事，装出来的结果是一样的。" + NL + NL +
                        "现在用备用方式再试一次吗？它同样需要管理员权限，会再弹一次 UAC，请点“是”。",
                        "%s" + NL + NL +
                        "This failure involves Windows PowerShell: on some stripped-down "
                        "Windows images it cannot start at all." + NL +
                        "There is a second path that does not use PowerShell -- it does "
                        "the same steps with Windows' own certutil and the bundled "
                        "nefcon, and the result is identical." + NL + NL +
                        "Try the fallback now?  It needs administrator rights too, so "
                        "one more UAC prompt -- click Yes.") % head,
                    parent=self)
            except Exception:
                box["yes"] = False
            finally:
                done.set()

        try:
            self.after(0, ask)
        except Exception:
            return False
        done.wait(600)
        return bool(box.get("yes"))

    def on_install_apk(self):
        self.run_bg(self._install_apk_work)

    def _install_apk_work(self):
        self._later(lambda: self.stat_var.set(i18n.t("安装 APK…", "Installing the APK...")))
        # 仅安装 APK 并启动 (install only): that button is the manual "do it
        # now" and always installs, whatever the update prompt would say.
        self.session.cfg["install_apk"] = True
        self.session.cfg["reinstall_apk"] = True
        self.session.find_device()
        self.session.prepare_device()
        self._later(lambda: self.stat_var.set(i18n.t("APK 已就绪", "APK ready")))

    def on_start(self):
        # 人不喜欢被机器抢方向盘：手动点了开始，就把自动投屏的重试停掉。
        try:
            self._auto_cancel.set()
        except Exception:
            pass
        self._auto_streaming = False
        self._apply_form()
        self._save_settings()          # starting is a good moment to persist
        self.run_bg(self._start_work)

    def _start_work(self):
        # 2.1.2 -- 先找手机，再把虚拟屏放上桌面。
        # 反过来做的代价是用户实测报的那个顺序问题：手机还没连上就
        # apply_display()，虚拟屏已经挂到桌面上（要改分辨率时还得重启驱动，
        # 四秒多），然后 find_device() 失败，又得把屏摘掉；开机自动投屏每
        # AUTO_RETRY 秒重来一次，于是桌面每隔五秒就多一块没人看的屏、鼠标被
        # 拖到 3200 那一侧、驱动反复重启。现在设备没就位时一块屏都不会出现。
        self._later(lambda: self.stat_var.set(i18n.t("连接手机…",
                                                     "Connecting to the phone...")))
        self.session.find_device()
        self._later(lambda: self.stat_var.set(i18n.t("准备拓展屏…",
                                                     "Preparing the extended display...")))
        self.session.apply_display()
        self.session.prepare_device()
        self._later(lambda: self.stat_var.set(i18n.t("启动编码…", "Starting the encoder...")))
        self.session.start_stream()
        self.log("=== 投屏已开始：%s ===" % self.session.describe(), "ok")
        self._later(self._started)

    def _started(self):
        self.stat_var.set(i18n.t("投屏中", "Streaming"))
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        # 1.9.1 -- a program that started streaming by itself at boot has to
        # say so, or the user only finds out when the picture does not come.
        # The manual press is a separate kind: most people do not want a
        # balloon about something they just did with the window in front of
        # them, and that kind defaults to off.
        try:
            desc = self.session.describe()
        except Exception:
            desc = ""
        self._notify("stream" if self._auto_streaming else "manual",
                     i18n.t("投屏已开始", "Streaming started"), desc)

    def _apply_form(self):
        cfg = self.session.cfg
        if self.res_var.get() == _custom_label():
            try:
                cfg["width"] = int(self.w_var.get())
                cfg["height"] = int(self.h_var.get())
            except ValueError:
                raise RuntimeError(i18n.t("自定义分辨率必须是整数",
                                          "A custom resolution has to be whole numbers"))
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
        if cfg["position"] == "custom":
            try:
                cfg["pos_x"] = int(self.pos_x_var.get())
                cfg["pos_y"] = int(self.pos_y_var.get())
            except ValueError:
                raise RuntimeError(i18n.t("自定义位置的 X / Y 必须是整数",
                                          "A custom position needs whole numbers for X and Y"))
        cfg["draw_mouse"] = bool(self.mouse_var.get())
        # 1.9.0 -- the same trap 1.8.1 hit with match_phone: the box has to copy
        # its value into cfg, or session keeps DEFAULTS["touch"] (always True)
        # and unticking the box does nothing at all.
        cfg["touch"] = bool(self.touch_var.get())
        # 1.8.1 -- the one setting this function used to forget.  "match the
        # phone's panel" is remembered in settings.json and shown on the form,
        # but session.cfg kept the DEFAULTS value (True) no matter what the box
        # said, because nothing ever copied it across.  The result looked like a
        # broken resolution menu: the dropdown was enabled with the box unticked,
        # a new size went into cfg, and apply_display()/retarget() then replaced
        # it with the phone's panel size -- "切换分辨率无效" (the size never
        # changed, and nothing in the log said why).
        cfg["match_phone"] = bool(self.match_var.get())
        cfg["mode"] = self.mode_var.get()
        cfg["install_apk"] = True
        cfg["reinstall_apk"] = bool(self.inst_var.get())
        cfg["tray_on_close"] = bool(self.close_action != "quit")
        cfg["autostart"] = bool(self.auto_var.get())
        cfg["vdd_off_mode"] = self.off_mode_var.get()
        if cfg["mode"] == "extend":
            cfg["bitrate_mbps"] = max(cfg["bitrate_mbps"], 4)
        self._sync_autostart()

    def on_stop(self):
        # 停止 (Stop) stops the stream and takes the screen back off the
        # desktop, so the mouse cannot wander onto an empty 3200 px and Windows
        # stops reporting a 4480 px desktop.  The driver's device node stays
        # enabled, so pressing 开始投屏 again only re-attaches the screen (about
        # a second, no UAC).  Only exiting the program switches the driver off
        # for real -- see _quit_now.
        self.btn_stop.configure(state="disabled")
        try:
            self._auto_cancel.set()
        except Exception:
            pass
        self.run_bg(lambda: self.session.stop(), done=self._stopped)

    def _stopped(self):
        self.stat_var.set(i18n.t("已停止", "Stopped"))
        self.note_var.set("")
        self.btn_start.configure(state="normal")

    # ------------------------------------------- 开机后自动开始投屏（1.9.0）
    def _auto_stream_check(self):
        """被 --stream 拉起来时，先判断这一次该不该自动开始投屏。

        这里只做判断，真正的重试在 _auto_stream_worker 里：开机那一刻 adb 服务、
        虚拟屏驱动、手机都还在醒来的路上，试一次就报错没有任何意义。
        """
        if not AUTOSTREAM:
            return
        if getattr(self, "_auto_begun", False):
            # 只跑一次。__init__ 里的 after 和手动调用撞上时，两个 worker 会同时
            # 去点「开始」，第二个白等一下才看到「已经在投屏了」。
            return
        self._auto_begun = True
        if not self.settings.get("auto_stream", False):
            self.log("命令行带了 --stream，但设置里「开机后自动开始投屏」是关的，这次不自动开始。")
            return
        if priv.needs_accept(self.settings):
            # 条款没同意过就替用户按下开始，比什么都不做更不礼貌。
            self.log("开机自动投屏没有开始：还没看过并同意使用条款。", "err")
            self._notify(i18n.t("自动投屏没有开始", "The automatic start did not begin"),
                         i18n.t("先打开 DroidExtend 看一遍并同意使用条款。",
                                "Open DroidExtend and accept the terms first."))
            return
        self.log("开机自动投屏：开始等手机（%s）。" % self._wait_words())
        # 表单要在主线程里读（Tk 变量不是线程安全的），后台只负责反复试。
        try:
            self._apply_form()
        except Exception as e:
            self.log("开机自动投屏读界面设置失败：%s" % e, "err")
            return
        threading.Thread(target=self._auto_stream_worker, name="auto-stream",
                         daemon=True).start()

    def _auto_stream_worker(self):
        """等手机，等到就开始，等不到就发一条系统通知。

        为什么要循环重试：Windows 登录完成时 adb 服务可能还没起，手机也可能还在
        开机，第一次几乎必然失败。每 AUTO_RETRY 秒再来一次，直到等到或者超时。
        """
        wait = self._wait_value()
        t0 = time.time()
        attempt = 0
        quiet = 0
        fatal = None
        if self.session is None:
            return
        while not self._auto_cancel.is_set():
            if self.busy:
                # 有人在用（手动点了按钮，或者环境刷新在跑）：让开，别抢按钮。
                self._auto_cancel.wait(0.5)
                continue
            if wait > 0 and time.time() - t0 >= wait:
                break
            # 2.1.2 -- 先问一句「手机在吗」，看清楚再动屏幕。
            # 这条探测只跑 adb devices，不装包、不建隧道、不碰虚拟屏，所以
            # 手机还没上线的这几分钟里桌面完全不会变；旧顺序是先把屏挂上
            # 桌面（要改分辨率时还会重启一次驱动），发现没手机再摘掉，五秒
            # 后从头再来一遍。
            row, why = self.session.probe_device()
            if row is None:
                quiet += 1
                # 前两次说清楚，之后每十二次（约一分钟）提一下，别刷屏。
                if quiet <= 2 or quiet % 12 == 0:
                    self.log("开机自动投屏：还没看到手机（%s），虚拟屏先不动。" % why,
                             "err" if quiet <= 2 else None)
                    self._later(lambda: self.stat_var.set(
                        i18n.t("等手机…", "Waiting for the phone...")))
                left = wait - (time.time() - t0)
                if wait > 0 and left <= 0:
                    break
                self._auto_cancel.wait(AUTO_RETRY if wait <= 0
                                       else max(1.0, min(AUTO_RETRY, left)))
                continue
            if quiet:
                self.log("开机自动投屏：手机上线了（%s），开始准备拓展屏。" % row["label"])
                quiet = 0
            attempt += 1
            self.busy = True
            self._later(lambda: self.btn_start.configure(state="disabled"))
            err = None
            try:
                if self.session is not None and self.session.running:
                    self.log("开机自动投屏：已经在投屏了，不重复开始。")
                    return
                self.log("开机自动投屏：第 %d 次尝试。" % attempt)
                self._auto_streaming = True
                self._start_work()
            except VddMissing as e:
                err = i18n.t("虚拟显示器未就绪：%s", "the virtual display is not ready: %s") % e
                fatal = err
            except Exception as e:
                err = "%s: %s" % (type(e).__name__, e)
            finally:
                self.busy = False
            if err is None:
                self.log("开机自动投屏：已经开始。", "ok")
                self._wake_phone()
                return
            self.log("开机自动投屏没有开始：%s" % err.replace(NL, " "), "err")
            # 每次失败都把屏摘掉：留着一块没人看的虚拟屏，鼠标就会飘过去。
            try:
                if self.session is not None and self.session.attached_display():
                    self.session.stop()
            except Exception:
                pass
            if fatal or self._auto_cancel.is_set():
                break
            left = wait - (time.time() - t0)
            if wait > 0 and left <= 0:
                break
            pause = AUTO_RETRY if wait <= 0 else max(1.0, min(AUTO_RETRY, left))
            self._auto_cancel.wait(pause)
        if self._auto_cancel.is_set():
            self.log("开机自动投屏：已取消。")
            return
        self._later(lambda: self.btn_start.configure(state="normal"))
        self._later(lambda: self.stat_var.set(i18n.t("就绪", "Ready")))
        if fatal:
            self.log("开机自动投屏：放弃，虚拟屏没准备好。", "err")
            self._notify("fail",
                         i18n.t("开机自动投屏没有开始", "The automatic start did not begin"),
                         i18n.t("虚拟显示器没准备好，先点「%s」。",
                                "The virtual display is not ready -- press \"%s\" first.")
                         % _install_driver_label())
            return
        self.log("开机自动投屏：%s没等到手机，先放弃（虚拟屏一次都没挂上桌面）。"
                 % ((i18n.t("等了 %d 秒" % wait, "after %d s" % wait) + " ") if wait > 0 else ""),
                 "err")
        self._notify("fail",
                     i18n.t("开机自动投屏没有开始", "The automatic start did not begin"),
                     i18n.t("没等到手机。插上数据线、确认 USB 调试已打开后再打开一次程序。",
                            "The phone never turned up. Check the cable and USB debugging, "
                            "then start DroidExtend once by hand."))

    def _wake_phone(self):
        """把手机屏幕点亮。锁着的屏在流里就是一块黑画面，看着像"没画面"。"""
        try:
            s = self.session
            if s is not None and s.adb and s.serial:
                s.adb.shell(s.serial, "input", "keyevent", "224")   # KEYCODE_WAKEUP
                self.log("已试着点亮手机屏幕（设了锁屏密码的话还需要手动解锁）。")
        except Exception as e:
            self.log("唤醒手机屏幕失败：%s" % e)

    def _notify_on(self, kind):
        """Is this kind of news allowed right now?

        1.9.1.  The master switch wins, then the per-kind box.  An unknown kind
        is allowed rather than silently swallowed: a notification that never
        appears because of a typo in a kind name would be very hard to find.
        """
        s = getattr(self, "settings", None) or {}
        if not s.get("notify", True):
            return False
        if kind not in _NOTIFY_KINDS_SET:
            return True
        return bool(s.get("notify_" + kind, True))

    def _notify(self, kind, title, text):
        """按用户的开关发一条通知；关掉的那一类只写日志。"""
        if not self._notify_on(kind):
            self.log(i18n.t("（这一类通知已关，只写日志）%s：%s",
                            "(this kind of notification is off, log only) %s: %s")
                     % (title, text))
            return False
        # The same sentence twice within half a minute is one event seen twice
        # (a retry loop, two callers), not two events.
        key = (kind, title, text)
        now = time.time()
        last = self._notify_seen.get(key, 0.0)
        if now - last < 30.0:
            return False
        self._notify_seen[key] = now
        if len(self._notify_seen) > 64:
            self._notify_seen = {k: v for k, v in self._notify_seen.items()
                                 if now - v < 300.0}
        return self._send_notify(title, text)

    def _send_notify(self, title, text):
        """真的把一条通知送出去：托盘气泡 → 系统通知 → 窗口还看得见时弹框。"""
        t = getattr(self, "tray", None)
        if t is not None and getattr(t, "running", False):
            try:
                t.balloon(title, text)
                return True
            except Exception:
                pass
        try:
            import notify
            if notify.toast(title, text, log=self.log):
                return True
        except Exception as e:
            self.log("系统通知发不出去：%s" % e)
        # 通知也没弹出来的时候至少留个痕迹。窗口已经藏起来时不弹框：一个藏在
        # 托盘里的程序突然在屏幕正中弹出错误框，比没提示更让人困惑。
        try:
            if self.state() != "withdrawn":
                self._later(lambda: messagebox.showwarning(title, text, parent=self))
        except Exception:
            pass
        return False

    def _set_notify(self, key, var):
        """A notification check box was clicked: remember it, do not argue."""
        try:
            self.settings[key] = bool(var.get())
        except Exception:
            return
        self._save_settings()

    def on_test_notify(self):
        """发一条真通知，绕过开关 —— 用户点这个就是想看它能不能到。"""
        ok = self._send_notify(
            i18n.t("测试通知", "Test notification"),
            i18n.t("能看到这一条，说明 Windows 通知是通的。"
                   "（关闭按钮在这个气泡的右上角。）",
                   "If you can see this, notifications reach you. "
                   "(The close button is at the top right of this balloon.)"))
        if ok:
            self.log("测试通知已发出。", "ok")
        else:
            self.log("测试通知发不出去：Windows 的通知可能被系统设置或"
                     "「专注助手」挡住了，菜单里有「Windows 通知设置…」。", "err")

    def _poll_perf(self):
        """The 700ms tick that keeps the four numbers on the status line.

        Everything here is best-effort: a session that is shutting down can
        take its stats dict with it, and a status line is never worth an
        exception.
        """
        s = self.session
        try:
            if s is not None and getattr(s, "fatal", None):
                # The capture watchdog gave up (the virtual display is gone).
                self.note_var.set(s.fatal)
                self._set_dot("red")
                self._clear_metrics()
            elif s is not None and s.server and not s.running:
                # ffmpeg died and is being restarted -- usually one or two seconds.
                self.note_var.set(i18n.t("采集中断，正在自动重连…",
                                         "Capture interrupted, reconnecting..."))
                self._set_dot("yellow")
                self._clear_metrics()
            elif s is not None and s.running and s.server:
                st = s.stats
                linked = bool(s.server.connected)
                self._set_dot("green" if linked else "idle")
                self.note_var.set("" if linked else i18n.t(
                    "等待手机连接…", "waiting for the phone..."))
                self._set_metric("cap", "%.0f" % st["fps"])
                self._set_metric("send", "%.1f" % st["mbps"])
                # The phone's own numbers, but only while they are fresh: a
                # phone that has just gone away would otherwise leave the last
                # frame rate sitting there as if it were still true.
                cs = getattr(s.server, "client_stats", None)
                age = time.time() - (getattr(s.server, "client_stats_at", 0.0) or 0.0)
                if cs and age < 6.0:
                    self._set_metric("phone", "%d" % cs["shown_fps"])
                    self._set_metric("lat", "%d" % cs["latency_ms"])
                else:
                    self._set_metric("phone", "—")
                    self._set_metric("lat", "—")
            else:
                self._set_dot("idle")
                self._clear_metrics()
        except Exception:
            pass
        try:
            self._perf_job = self.after(700, self._poll_perf)
        except Exception:
            pass

    def _clear_metrics(self):
        for key in ("cap", "send", "phone", "lat"):
            self._set_metric(key, "—")

    def _on_close(self):
        """窗口上的 ×。

        1.6.8: 按「关闭窗口时的行为」分派 —— ask 先问一次（用户可以勾「不再提醒」），
        tray 收进托盘，quit 真的退出。以前这里读的是一个常驻在界面上的勾选框。
        """
        self._save_settings()          # remember the window and the parameters
        if self._quitting:
            return
        action = self.close_action
        if action == "ask":
            action = self._ask_close_action()
            if action is None:
                return
        if action == "tray":
            self._hide_to_tray()
            return
        self._quit_now()

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
            self._flash_hint(i18n.t("日志已复制到剪贴板", "Log copied to the clipboard"))
        except Exception as e:
            messagebox.showerror(i18n.t("复制失败", "Copy failed"), str(e), parent=self)

    def on_save_log(self):
        path = filedialog.asksaveasfilename(
            parent=self, title=i18n.t("保存日志", "Save the log"), defaultextension=".txt",
            initialfile="DroidExtend-%s.txt" % time.strftime("%Y%m%d-%H%M%S"),
            filetypes=[(i18n.t("文本文件", "Text files"), "*.txt"),
                       (i18n.t("所有文件", "All files"), "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._log_text())
            self._flash_hint(i18n.t("日志已保存到 ", "Log saved to ") + path)
        except Exception as e:
            messagebox.showerror(i18n.t("保存失败", "Save failed"), str(e), parent=self)

    def on_clear_log(self):
        self.log_txt.configure(state="normal")
        self.log_txt.delete("1.0", "end")
        self.log_txt.configure(state="disabled")
        del self._log_lines[:]
        self.log("日志已清空。")

    # ------------------------------------------------------- menu glue / help
    def open_url(self, url):
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror(i18n.t("打开浏览器失败", "Could not open the browser"),
                                 i18n.t("%s\n\n地址：%s", "%s\n\nURL: %s") % (e, url),
                                 parent=self)

    def on_open_config_dir(self):
        d = paths.data_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception as e:
            messagebox.showerror(i18n.t("打开失败", "Could not open"), "%s\n\n%s" % (e, d), parent=self)

    def _refresh_paths_label(self):
        """Status bar shows where settings and logs are being written."""
        try:
            self.file_var.set(i18n.t("数据目录: %s", "Data folder: %s") % paths.data_dir())
        except Exception:
            pass

    # on_change_data_dir() is gone as of 1.6.6: settings, logs, updates and
    # scratch files all live in the program folder, which is also the folder the
    # installer created and the uninstaller removes.  An option to move the data
    # somewhere else would re-scatter exactly what this change gathers up.

    def on_open_root_dir(self):
        try:
            os.startfile(ROOT)
        except Exception as e:
            messagebox.showerror(i18n.t("打开失败", "Could not open"), "%s\n\n%s" % (e, ROOT), parent=self)

    def on_licenses(self):
        """Show 第三方组件许可.txt: the MIT/Apache/BSD texts we must ship."""
        path = os.path.join(ROOT, LICENSES)
        if not os.path.exists(path):
            messagebox.showinfo(
                i18n.t("第三方组件许可", "Third-party licences"),
                i18n.t("没有找到 %s。\n\n它随安装包一起分发，来源见：\n%s",
                       "%s was not found.\n\nIt ships with the installer; sources are at:\n%s")
                % (LICENSES, REPO_URL),
                parent=self)
            return
        try:
            os.startfile(path)
        except Exception as e:
            messagebox.showerror(i18n.t("打开失败", "Could not open"), "%s\n\n%s" % (e, path), parent=self)

    def on_manual(self, anchor=None):
        if self._manual_win is not None and self._manual_win.winfo_exists():
            win = self._manual_win
            win.deiconify()
            win.lift()
            win.focus_force()
        else:
            pal = self.pal
            win, body = self._sheet(
                i18n.t("使用说明 — %s v%s", "Manual - %s v%s")
                % (APP_NAME, APP_VERSION), 760, 620)
            self._manual_win = win
            body.rowconfigure(0, weight=1)
            body.columnconfigure(0, weight=1)
            txt = tk.Text(body, bg=pal["mono_bg"], fg=pal["mono_fg"],
                          insertbackground=pal["mono_fg"],
                          font=self._fnt(11.5, mono=True), wrap="word",
                          relief="flat", bd=0, highlightthickness=0,
                          padx=uk.px(14), pady=uk.px(11),
                          spacing1=uk.px(1), spacing3=uk.px(2),
                          selectbackground=pal["sel"],
                          selectforeground=pal["sel_fg"])
            txt.grid(row=0, column=0, sticky="nsew")
            sb = ttk.Scrollbar(body, command=txt.yview)
            sb.grid(row=0, column=1, sticky="ns", padx=(uk.px(8), 0))
            txt.configure(yscrollcommand=sb.set)
            txt.insert("1.0", mn.text())
            txt.configure(state="disabled")
            self._manual_txt = txt
            foot = tk.Frame(body, bg=pal["bg"])
            foot.grid(row=1, column=0, columnspan=2, sticky="ew",
                      pady=(uk.px(12), 0))
            uk.RoundButton(foot, pal, i18n.t("项目主页", "Project home"),
                           kind="soft", height=32,
                           command=lambda: self.open_url(REPO_URL)).pack(
                               side="left", padx=(0, uk.px(8)))
            uk.RoundButton(foot, pal, i18n.t("反馈问题", "Report a problem"),
                           kind="soft", height=32,
                           command=lambda: self.open_url(ISSUES_URL)).pack(
                               side="left")
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
            "%s — %s" % (APP_NAME, app_title()),
            i18n.t("版本 v%s  (%s)", "Version v%s  (%s)") % (APP_VERSION, APP_DATE),
            "",
            i18n.t("用一根 USB 数据线，把安卓手机/平板变成 Windows 的第二块显示器。",
                   "Turns an Android phone or tablet into a second monitor for Windows, "
                   "over a single USB cable."),
            i18n.t("真扩展桌面，不是投屏镜像；端到端延迟实测 5~11 ms，支持触摸反控。",
                   "A real extended desktop, not a mirror; 5-11 ms end-to-end in testing, "
                   "with touch passed back to the PC."),
            "",
            i18n.t("代码作者：%s", "Code by: %s") % AUTHOR,
            i18n.t("人类只负责提需求、给方向和验收。",
                   "The human only asked for it, pointed at it and signed it off."),
            i18n.t("项目所有者：%s", "Project owner: %s") % OWNER,
            "",
            i18n.t("仓库：%s", "Repository: %s") % REPO_URL,
            i18n.t("反馈：%s", "Issues: %s") % ISSUES_URL,
            i18n.t("邮箱：%s", "Email: %s") % " / ".join(EMAILS),
            "",
            i18n.t("—— 用到的第三方组件 ——", "-- Third-party components --"),
        ]
        lines += [i18n.t("· %s（%s）  %s", "- %s (%s)  %s") % c for c in _credits()]
        lines += ["", i18n.t("完整许可与出处见 帮助 -> 第三方组件许可",
                             "Full licences and sources: Help -> Third-party licences")]
        lines += [i18n.t("（程序目录下的《%s》）", "(the file %s in the program folder)") % LICENSES]
        messagebox.showinfo(i18n.t("关于 %s", "About %s") % APP_NAME, "\n".join(lines), parent=self)

    def on_donate(self):
        """Two QR codes in one window -- the only place this program asks for money.

        1.8.3: kept behind 帮助 (Help) on purpose.  No nag screen, no timer and
        nothing in the streaming path -- a free tool that interrupts its own work
        to ask is worse than one that never asks at all.  The text says out loud
        that donating is optional, because it is.
        """
        if self._donate_win is not None and self._donate_win.winfo_exists():
            win = self._donate_win
            win.deiconify()
            win.lift()
            win.focus_force()
            return
        pal = self.pal
        win, body = self._sheet(
            i18n.t("请作者喝杯奶茶", "Buy the author a milk tea"), 700, 690)
        self._donate_win = win
        head = tk.Label(
            body, bg=pal["bg"], fg=pal["dim"], justify="left", anchor="w",
            wraplength=uk.px(620), font=self._fnt(11.5),
            text=i18n.t(
                "DroidExtend 免费、没有广告、没有内购、没有功能锁，代码全在 GitHub 上。\n"
                "如果它帮你省下了一块便携屏，或者只是觉得好用，可以请作者喝一杯奶茶，" ""
                "或者资助一点 token —— 这个项目确实是 AI 写的，token 就是它的电费。\n"
                "不捐也完全没问题，功能一个都不会少。",
                "DroidExtend is free: no ads, no in-app purchases, no locked "" "
                "features, and all of the code is on GitHub.\n"
                "If it saved you the price of a portable monitor -- or you just "" "
                "like it -- you can buy the author a milk tea, or sponsor a few "" "
                "tokens: the project really is written by an AI, and tokens are "" "
                "its electricity bill.\n"
                "Not donating is completely fine.  Nothing is behind a paywall."))
        head.pack(fill="x", pady=(0, uk.px(12)))
        row = tk.Frame(body, bg=pal["bg"])
        row.pack(fill="both", expand=True)
        photos = []
        # grid, not pack: the two source images have different aspect ratios
        # (1200x1459 vs 1080x1620), so packed captions end up at different
        # heights.  Put every code in row 0 and every caption in row 1 and the
        # two captions line up with each other.
        for col_i, (name, caption) in enumerate((
                ("wechat.png", i18n.t("微信支付", "WeChat Pay")),
                ("alipay.png", i18n.t("支付宝", "Alipay")))):
            path = os.path.join(ROOT, "assets", "donate", name)
            if not os.path.exists(path):
                continue
            try:
                img = tk.PhotoImage(file=path)
            except Exception:
                continue
            # 1200 px is wider than the window and Tk can only scale by an
            # integer factor -- nearest is fine for a QR code as long as the
            # factor divides evenly, and 1200 = 4 x 300 does.
            factor = max(1, int(round(img.width() / 260.0)))
            if factor > 1:
                img = img.subsample(factor, factor)
            tk.Label(row, image=img, bg=pal["bg"], bd=0).grid(
                row=0, column=col_i, padx=uk.px(10), sticky="n")
            tk.Label(row, text=caption, bg=pal["bg"], fg=pal["fg"],
                     font=self._fnt(11)).grid(
                row=1, column=col_i, pady=(uk.px(8), 0))
            photos.append(img)
        # Tk keeps no reference to a PhotoImage of its own; the list is what
        # stops the garbage collector from blanking both codes.
        self._donate_photos = photos
        if not photos:
            tk.Label(row, bg=pal["bg"], fg=pal["faint"], wraplength=uk.px(600),
                     font=self._fnt(11.5), justify="left",
                     text=i18n.t(
                         "这个安装包里没有带赞赏码，可以到项目主页看：%s",
                         "This build does not carry the QR codes -- see the "
                         "project page: %s") % REPO_URL).pack(padx=uk.px(18),
                                                              pady=uk.px(20))
        foot = tk.Frame(body, bg=pal["bg"])
        foot.pack(fill="x", side="bottom", pady=(uk.px(12), 0))
        uk.RoundButton(foot, pal, i18n.t("项目主页", "Project home"),
                       kind="soft", height=32,
                       command=lambda: self.open_url(REPO_URL)).pack(side="left")
        uk.RoundButton(foot, pal, i18n.t("关闭", "Close"), kind="primary",
                       height=32, command=win.destroy).pack(side="right")

    def on_reset_settings(self):
        if not messagebox.askyesno(
                i18n.t("重置为默认设置", "Reset to defaults"),
                i18n.t("把界面上的参数恢复成默认值，并删掉已保存的设置文件。\n\n继续吗？",
                       "Puts every setting on this window back to its default and deletes "
                       "the saved settings file.\n\nContinue?"), parent=self):
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
        # 1.9.4 -- the beta channel reads the whole release list (upd.check).
        pre = bool(self.settings.get("check_prerelease", False))
        if manual:
            self.log("正在向 GitHub 查询最新版本%s…" % ("（含测试版）" if pre else ""))

        def work():
            r = upd.check(prerelease=pre)
            self._later(lambda: self._upd_done(r, manual))

        threading.Thread(target=work, daemon=True).start()

    def _upd_done(self, r, manual):
        self.upd_busy = False
        if not r["ok"]:
            self.log("检查更新失败：%s" % r["error"], "err")
            if manual:
                messagebox.showwarning(
                    i18n.t("检查更新", "Check for updates"),
                    i18n.t("查询 GitHub 失败：\n%s\n\n（离线 / 被墙都不影响正常使用，"
                           "可以手动打开：\n%s）",
                           "Could not reach GitHub:\n%s\n\n(Being offline or blocked does not "
                           "stop the app working -- you can open this yourself:\n%s)")
                    % (r["error"], RELEASES_URL), parent=self)
            return
        if r["newer"]:
            tag = i18n.t("（测试版）", " (beta)") if r.get("pre") else ""
            self.log("发现新版本 v%s%s（当前 v%s）：%s"
                     % (r["latest"], tag, APP_VERSION, r["url"]), "ok")
            # 1.9.1 -- the check that runs at startup is not a request for a
            # window: someone who just booted the machine did not ask to read
            # release notes.  It becomes a balloon; the full window is one
            # menu click away (工具 -> 检查更新), and manual=True still gets it.
            #
            # 2.2.1 -- that was too quiet.  Windows notifications are off on a
            # lot of machines (focus assist, the app's notification switched
            # off, a stripped-down Windows build), so people went several
            # versions without being told there was one.  The startup check now
            # opens the same window the menu opens -- once per version, and
            # only when nothing is streaming: a window appearing over a running
            # stream is the one interruption that actually costs something.
            key = str(r["latest"]) + (tag or "")
            if not manual:
                self._notify(
                    "update",
                    i18n.t("DroidExtend v%s 可以用了", "DroidExtend v%s is ready")
                    % (r["latest"] + tag),
                    i18n.t("你现在是 v%s。≡ → 关于 →「立刻检查一次」可以现在就装。",
                           "You are on v%s. ≡ → About → Check now installs it now.")
                    % APP_VERSION)
                streaming = bool(self.session and getattr(self.session, "running", False))
                if not streaming and str(self.settings.get("update_offered", "") or "") != key:
                    self.settings["update_offered"] = key
                    self._save_settings()
                    self.after(900, lambda: self._offer_update(r))
            else:
                self._offer_update(r)
        else:
            self.log("已是最新版本 v%s。" % APP_VERSION, "ok")
            if manual:
                messagebox.showinfo(i18n.t("检查更新", "Check for updates"),
                                    i18n.t("已经是最新版本 v%s。", "You are on the latest version, v%s.")
                                    % APP_VERSION, parent=self)

    # ---------------------------------------------------------- in-app update
    def _offer_update(self, r):
        """Show what is new, then download and install it without a browser.

        The download is verified before anything is run (see update.download),
        so the wording here says exactly what is checked rather than claiming
        the build is signed -- it is not.
        """
        s = r.get("setup") or {}
        if not s.get("url"):
            # An older release with no installer attached: the browser is the
            # only honest option left.
            if messagebox.askyesno(
                    i18n.t("发现新版本", "New version available"),
                    i18n.t("有新版本 v%s，当前是 v%s。\n\n这一版没有附带安装包，"
                           "要打开发布页面吗？\n%s",
                           "v%s is out; you are on v%s.\n\nThis release has no installer "
                           "attached -- open the release page?\n%s")
                    % (r["latest"], APP_VERSION, r["url"]),
                    parent=self):
                self.open_url(r["url"])
            return

        pal = self.pal
        win, body = self._sheet(
            i18n.t("发现新版本 v%s", "Version v%s is available") % r["latest"],
            700, 680)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        mb = (s.get("size") or 0) / 1048576.0
        when = ("  ·  %s" % r["published"]) if r.get("published") else ""
        head = tk.Frame(body, bg=pal["bg"])
        head.grid(row=0, column=0, sticky="ew", pady=(0, uk.px(10)))
        self._lab(head, i18n.t("DroidExtend v%s 可以用了", "DroidExtend v%s is ready")
            % r["latest"], self._fnt(14.5, "bold"), pal["fg"]).pack(anchor="w")
        self._lab(head, i18n.t("你现在是 v%s%s  ·  安装包 %.1f MB",
                         "You are on v%s%s  ·  installer %.1f MB")
            % (APP_VERSION, when, mb), self._fnt(11.5), pal["faint"]).pack(
                anchor="w", pady=(uk.px(5), 0))

        box = tk.Frame(body, bg=pal["bg"])
        box.grid(row=1, column=0, sticky="nsew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        txt = tk.Text(box, bg=pal["mono_bg"], fg=pal["mono_fg"],
                      font=self._fnt(11, mono=True), wrap="word", relief="flat",
                      bd=0, highlightthickness=0, padx=uk.px(12),
                      pady=uk.px(10), height=12,
                      selectbackground=pal["sel"],
                      selectforeground=pal["sel_fg"])
        txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, command=txt.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(uk.px(8), 0))
        txt.configure(yscrollcommand=sb.set)
        self._insert_notes(txt, self._notes_for(r))
        txt.configure(state="disabled")

        # 下载位置与「装完删包」放在这一屏，因为用户正是在这一刻才会想到它们。
        opt = tk.Frame(body, bg=pal["bg"])
        opt.grid(row=2, column=0, sticky="ew", pady=(uk.px(12), 0))
        opt.columnconfigure(1, weight=1)
        self._lab(opt, i18n.t("下载到", "Download to"), self._fnt(11.5), pal["faint"]).grid(
            row=0, column=0, sticky="w")
        dir_var = tk.StringVar(value=upd.update_dir(self.settings.get("update_dir", "")))
        self._lab(opt, "", self._fnt(11.5), pal["dim"], textvariable=dir_var).grid(
            row=0, column=1, sticky="w", padx=uk.px(9))
        uk.RoundButton(opt, pal, i18n.t("更改…", "Change…"), kind="soft",
                       height=30, command=lambda: self._pick_update_dir(dir_var)
                       ).grid(row=0, column=2)
        keep_var = tk.BooleanVar(value=bool(self.settings.get("delete_installer", True)))
        uk.RoundCheck(opt, pal, variable=keep_var,
                      text=i18n.t("安装完成后删掉下载的安装包",
                                  "Delete the downloaded installer after installing")
                      ).grid(row=1, column=0, columnspan=3, sticky="w",
                             pady=(uk.px(9), 0))

        foot = tk.Frame(body, bg=pal["bg"])
        foot.grid(row=3, column=0, sticky="ew", pady=(uk.px(14), 0))
        self._lab(foot, i18n.t(
            "安装包从 GitHub 官方地址下载，安装前会校验大小、SHA256 和文件格式。\n"
            "本程序没有代码签名证书，所以完整性靠这个校验和保证，不靠签名。",
            "The installer comes from the official GitHub release and is checked for "
            "size, SHA256 and file format before anything is run.\n"
            "This project has no code-signing certificate, so integrity rests on that "
            "checksum, not on a signature."),
            self._fnt(11.5), pal["faint"], justify="left").pack(anchor="w", pady=(0, uk.px(11)))
        row = tk.Frame(foot, bg=pal["bg"])
        row.pack(fill="x")
        uk.RoundButton(row, pal, i18n.t("以后再说", "Later"), kind="ghost",
                       height=34, command=win.destroy).pack(side="left")
        uk.RoundButton(row, pal, i18n.t("完整更新日志", "All release notes"),
                       kind="ghost", height=34,
                       command=self.on_show_changelog).pack(
                           side="left", padx=(uk.px(4), 0))
        uk.RoundButton(row, pal, i18n.t("打开发布页", "Open release page"),
                       kind="ghost", height=34,
                       command=lambda: self.open_url(r["url"])).pack(
                           side="left", padx=(uk.px(4), 0))
        uk.RoundButton(row, pal, i18n.t("下载并安装", "Download and install"),
                       kind="primary", height=34,
                       command=lambda: (win.destroy(),
                                        self._start_download(r, dir_var, keep_var))
                       ).pack(side="right")

    def _notes_for(self, r):
        """本地更新日志打头，GitHub 上那份要是更长就接在后面。

        本地的这份是随程序打包的，所以断网、被墙、release 没写说明都还能看到
        一段像样的介绍；GitHub 那份常常带着校验和表，也不该丢掉。
        """
        head = i18n.t("v%s 更新内容" % r["latest"],
                      "What is new in v%s" % r["latest"])
        parts = ["## " + head]
        local = cl.text(r["latest"])
        remote = (r.get("notes") or "").strip()
        if local:
            parts.append(local)
        if remote:
            if local:
                parts.append("")
                parts.append("## " + i18n.t("发布页上的说明", "From the release page"))
            parts.append(remote)
        if not local and not remote:
            parts.append(i18n.t("（这一版没有写说明）", "(no notes for this release)"))
        return "\n".join(parts)

    def _pick_update_dir(self, var):
        d = filedialog.askdirectory(
            parent=self, title=i18n.t("选择更新下载位置", "Where should updates be saved?"),
            initialdir=var.get() or upd.update_dir())
        if not d:
            return
        d = os.path.normpath(d)
        var.set(d)
        self.settings["update_dir"] = d
        self._save_settings()
        self.log("更新下载位置改为：%s" % d)

    def on_show_changelog(self):
        """完整更新日志 —— 更新弹窗里那 12 行放不下所有版本。"""
        pal = self.pal
        win, body = self._sheet("%s — %s" % (cl.translator(), APP_NAME), 720, 660)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)
        self._lab(body, "%s v%s" % (APP_NAME, APP_VERSION), self._fnt(14.5, "bold"), pal["fg"]).grid(
            row=0, column=0, sticky="w", pady=(0, uk.px(10)))
        box = tk.Frame(body, bg=pal["bg"])
        box.grid(row=1, column=0, sticky="nsew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        txt = tk.Text(box, bg=pal["mono_bg"], fg=pal["mono_fg"],
                      font=self._fnt(11, mono=True), wrap="word", relief="flat",
                      bd=0, highlightthickness=0, padx=uk.px(12),
                      pady=uk.px(10), selectbackground=pal["sel"],
                      selectforeground=pal["sel_fg"])
        txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, command=txt.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(uk.px(8), 0))
        txt.configure(yscrollcommand=sb.set)
        self._insert_notes(txt, cl.all_text())
        txt.configure(state="disabled")
        foot = tk.Frame(body, bg=pal["bg"])
        foot.grid(row=2, column=0, sticky="ew", pady=(uk.px(12), 0))
        uk.RoundButton(foot, pal, i18n.t("关闭", "Close"), kind="primary",
                       height=34, command=win.destroy).pack(side="right")

    def _insert_notes(self, txt, notes):
        """Show the release notes as readable text rather than raw Markdown.

        A full renderer would be a dependency and a liability; this only drops
        the syntax that would otherwise be noise in a 12-line box -- heading
        hashes, ``**`` markers, ``[text](url)`` and backticks -- and keeps the
        rest, including the checksum table, exactly as written.
        """
        txt.tag_configure("h", font=("Microsoft YaHei UI", 10, "bold"),
                          foreground=ACCENT, spacing1=10, spacing3=2)
        txt.tag_configure("b", font=("Microsoft YaHei UI", 9, "bold"))
        txt.tag_configure("bullet", lmargin1=8, lmargin2=20)
        src = notes or i18n.t("（这一版没有写说明）", "(no notes for this release)")
        bold = False
        for raw in src.splitlines():
            line = raw.rstrip()
            tag = ()
            if line.startswith("#"):
                line = line.lstrip("#").strip()
                tag = ("h",)
            elif line.lstrip()[:2] in ("* ", "- ", "• "):
                line = "  • " + line.lstrip()[2:]
                tag = ("bullet",)
            line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", line).replace("`", "")
            if not line:
                txt.insert("end", "\n")
                continue
            # ** toggles emphasis and often straddles a line break, so the state
            # is carried; the marker itself never reaches the box.
            for part in line.split("**"):
                if part:
                    txt.insert("end", part, tuple(tag) + (("b",) if bold else ()))
                bold = not bold
            txt.insert("end", "\n", tag)

    def _start_download(self, r, dir_var=None, keep_var=None):
        s = r["setup"]
        name = s["name"] or ("DroidExtend-Setup-%s.exe" % r["latest"])
        where = upd.update_dir(dir_var.get() if dir_var is not None
                               else self.settings.get("update_dir", ""))
        dest = os.path.join(where, name)
        # 默认删：一个每次更新都留下的 40 MB 安装包，就是在替用户攒垃圾。
        remove_after = bool(keep_var.get()) if keep_var is not None else True
        self._dl_cancel = False

        pal = self.pal
        win, body = self._sheet(
            i18n.t("正在下载 DroidExtend %s", "Downloading DroidExtend %s")
            % r["latest"], 520, 270)
        self._lab(body, i18n.t("正在下载 v%s 的安装包…", "Downloading the v%s installer...")
            % r["latest"], self._fnt(14.5, "bold"), pal["fg"]).pack(anchor="w")
        self._lab(body, name, self._fnt(11.5), pal["faint"]).pack(anchor="w",
                                                pady=(uk.px(4), uk.px(13)))
        bar = ttk.Progressbar(body, mode="determinate", length=uk.px(420),
                              maximum=1000)
        bar.pack(fill="x")
        note = self._lab(body, i18n.t("正在连接…", "Connecting..."), self._fnt(11.5), pal["faint"])
        note.pack(anchor="w", pady=(uk.px(9), uk.px(13)))

        def do_cancel():
            self._dl_cancel = True
            try:
                cancel.set_enabled(False)
            except Exception:
                pass
            note.configure(text=i18n.t("正在取消…", "Cancelling..."))

        cancel = uk.RoundButton(body, pal, i18n.t("取消", "Cancel"),
                                kind="soft", height=32, command=do_cancel)
        cancel.pack(anchor="e")
        win.protocol("WM_DELETE_WINDOW", do_cancel)

        total = int(s.get("size") or 0)
        state = {"t": time.time()}

        def prog(done, tot):
            def paint():
                if not win.winfo_exists():
                    return
                # Not `tot = tot or total`: assigning to the name would make it
                # local to paint() and blow up on the closure read.
                full = tot or total
                if full:
                    bar.configure(value=max(1, int(done * 1000 / full)))
                    pct = done * 100.0 / full
                else:
                    pct = 0.0
                dt = max(time.time() - state["t"], 0.001)
                note.configure(text="%.1f / %.1f MB  ·  %.0f%%  ·  %.1f MB/s"
                                    % (done / 1048576.0, full / 1048576.0, pct,
                                       done / 1048576.0 / dt))
            self._later(paint)

        def work():
            res = upd.download(s["url"], dest, sha256=s.get("sha256", ""),
                               size=total, on_progress=prog,
                               cancel=lambda: self._dl_cancel)
            self._later(lambda: self._dl_done(res, r, win, remove_after))

        self.log("开始下载 %s（%d 字节）→ %s" % (name, total, where))
        threading.Thread(target=work, daemon=True).start()

    def _dl_done(self, res, r, win, delete_installer=True):
        if win.winfo_exists():
            win.destroy()
        if not res["ok"]:
            self.log("更新下载未完成：%s" % res["error"].replace("\n", " "), "err")
            messagebox.showwarning(
                i18n.t("下载更新", "Download update"),
                i18n.t("没有拿到可用的安装包：\n\n%s\n\n也可以手动下载：\n%s",
                       "No usable installer was downloaded:\n\n%s\n\n"
                       "You can also download it by hand:\n%s") % (res["error"], r["url"]),
                parent=self)
            return
        self.log("下载完成并通过校验：%s（sha256 %s）"
                 % (res["path"], res["sha256"]), "ok")
        st, signer = upd.signature_status(res["path"])
        if st:
            applog.line("更新安装包签名状态: %s %s" % (st, signer))
        if not messagebox.askyesno(
                i18n.t("安装更新", "Install the update"),
                i18n.t("安装包已下载并通过校验：\n\n"
                       "SHA256  %s\n"
                       "大小    %d 字节\n"
                       "签名    %s\n\n"
                       "现在关闭本程序、安装 v%s 并重新打开吗？",
                       "The installer is downloaded and verified:\n\n"
                       "SHA256  %s\n"
                       "Size    %d bytes\n"
                       "Signature  %s\n\n"
                       "Close DroidExtend now, install v%s and start it again?")
                % (res["sha256"], os.path.getsize(res["path"]),
                   (st or i18n.t("未检查", "not checked"))
                   + (i18n.t("（本项目未购买代码签名证书）",
                             " (this project has no code-signing certificate)")
                      if st and st != "Valid" else ""),
                   r["latest"]), parent=self):
            self.log("用户选择稍后自己安装：%s" % res["path"])
            self.open_path_dir(res["path"])
            return
        ok, err, script = upd.run_installer(
            res["path"], install_dir=(ROOT if getattr(sys, "frozen", False) else ""),
            delete_installer=delete_installer,
            work_dir=self.settings.get("update_dir", ""))
        if not ok:
            self.log("无法启动安装程序：%s" % err, "err")
            messagebox.showerror(i18n.t("安装更新", "Install the update"),
                                 i18n.t("无法启动安装程序：\n%s",
                                        "Could not start the installer:\n%s") % err, parent=self)
            return
        self.settings["delete_installer"] = bool(delete_installer)
        self._save_settings()
        self.log("即将退出，由 %s 安装新版本并重新启动。%s"
                 % (script, "装完会删掉安装包。" if delete_installer
                    else "安装包会保留在原地。"), "ok")
        applog.line("更新: 交给 %s 应用 %s" % (script, res["path"]))
        # Do not toggle the device node on the way out: the installer is what the
        # user is waiting for, and a UAC prompt appearing now would look like the
        # update asking for permission.  The new build re-enables it on start.
        self.session.skip_vdd_off = True
        self._quit_now()

    def open_path_dir(self, path):
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception as e:
            self.log("打不开目录：%s" % e, "err")

    # ------------------------------------------------------------ 托盘 (#3)
    def _center_over(self, win):
        """把一个小对话框摆在本窗口中间，而且一定落在看得见的桌面上。"""
        try:
            self.update_idletasks()
            win.update_idletasks()
            x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
            y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 2
            # 2.3.1 -- 窗口位置是上次记住的，而那块屏可能是已经收起的虚拟显示器。
            # 摆在那里的弹窗谁也看不见，却仍然攥着 grab，于是整个程序像死了一样。
            ax, ay, aw, ah = _work_area()
            w = win.winfo_width() or uk.px(480)
            h = win.winfo_height() or uk.px(270)
            x = max(ax + 8, min(x, ax + aw - w - 8))
            y = max(ay + 8, min(y, ay + ah - h - 8))
            win.geometry("+%d+%d" % (x, y))
        except Exception:
            pass

    def _modal(self, win):
        """让弹窗真的出现，然后把输入交给它。

        2.3.1 -- 以前是先 grab_set() 再等窗口自己画出来。只要窗口被主窗口盖住
        或者落在屏幕外，grab 照样吃掉所有按键和点击：程序看起来死掉，窗口连拖
        都拖不动，也没有按钮可点（用户 12:27 报的就是这个）。现在先 deiconify +
        lift + topmost，确认真的 viewable 才 grab；另加看门狗，万一 grab 落在看
        不见的窗口上就自己松开，绝不让程序变成聋子。
        """
        def step(tries=0):
            try:
                if not win.winfo_exists():
                    return
                viewable = bool(win.winfo_viewable())
            except Exception:
                return
            if not viewable:
                try:
                    win.deiconify()
                    win.lift()
                except Exception:
                    pass
                if tries < 40:
                    win.after(200, lambda: step(tries + 1))
                return
            try:
                win.grab_set()
            except Exception:
                pass
            try:
                win.focus_force()
            except Exception:
                pass

        def watchdog():
            try:
                cur = self.grab_current()
                if cur is not None and not cur.winfo_viewable():
                    cur.grab_release()
            except Exception:
                pass

        try:
            win.deiconify()
            win.lift()
            win.attributes("-topmost", True)
        except Exception:
            pass
        try:
            win.after(30, lambda: step())
            win.after(2500, watchdog)
        except Exception:
            pass

    def _ask_close_action(self):
        """× 被点了、设置还是「每次询问」。返回 "tray" / "quit" / None（取消）。"""
        pal = self.pal
        top, body = self._sheet(i18n.t("关闭窗口", "Close the window"), 480, 270)
        self._lab(body, i18n.t("关闭窗口时想怎样？",
                         "What should closing the window do?"),
            self._fnt(14, "bold"), pal["fg"]).pack(anchor="w")
        self._lab(body, i18n.t(
            "最小化到托盘后程序继续在后台跑，投屏不会断；双击托盘图标可以叫回来。",
            "Minimising keeps the program running in the background, so the stream is not "
            "interrupted. Double-click the tray icon to bring it back."),
            self._fnt(11.5), pal["faint"], wraplength=uk.px(430),
            justify="left").pack(anchor="w", pady=(uk.px(7), uk.px(16)))
        again = tk.BooleanVar(value=False)
        result = {"v": None}

        def pick(value):
            result["v"] = value
            if again.get():
                self.close_action = value
                self._save_settings()
                self.log("关闭窗口的行为已记住：%s" % self._close_action_label(value))
            top.destroy()

        row = tk.Frame(body, bg=pal["bg"])
        row.pack(fill="x")
        uk.RoundButton(row, pal, i18n.t("最小化到托盘", "Minimise to the tray"),
                       kind="primary", height=34,
                       command=lambda: pick("tray")).pack(side="left")
        uk.RoundButton(row, pal, i18n.t("直接退出", "Quit"), kind="soft",
                       height=34, command=lambda: pick("quit")).pack(
                           side="left", padx=(uk.px(8), 0))
        uk.RoundButton(row, pal, i18n.t("取消", "Cancel"), kind="ghost",
                       height=34, command=top.destroy).pack(side="right")
        uk.RoundCheck(body, pal, variable=again,
                      text=i18n.t("不再提醒（记住这次的选择）",
                                  "Do not ask again (remember this choice)")
                      ).pack(anchor="w", pady=(uk.px(16), 0))
        self._center_over(top)
        self._modal(top)
        top.wait_window()
        return result["v"]

    def on_close_behavior(self):
        """工具菜单：改「关闭窗口时的行为」这个默认值。"""
        pal = self.pal
        top, body = self._sheet(
            i18n.t("关闭窗口时的行为", "What closing the window does"), 520, 340)
        self._lab(body, i18n.t("点 × 的时候：", "When the close button is pressed:"),
            self._fnt(14, "bold"), pal["fg"]).pack(anchor="w", pady=(0, uk.px(10)))
        var = tk.StringVar(value=self.close_action)
        for value, label in (("ask", i18n.t("每次问我", "Ask me every time")),
                             ("tray", i18n.t("最小化到托盘", "Minimise to the tray")),
                             ("quit", i18n.t("直接退出", "Quit"))):
            uk.RoundRadio(body, pal, text=label, value=value,
                          variable=var).pack(anchor="w", pady=uk.px(4))
        self._lab(body, i18n.t(
            "选「最小化到托盘」时，开机自启也会跟着缩在托盘里开始。",
            "With \"minimise to the tray\", starting with Windows also starts hidden."),
            self._fnt(11.5), pal["faint"], wraplength=uk.px(430),
            justify="left").pack(anchor="w", pady=(uk.px(12), 0))

        def ok():
            self.close_action = var.get()
            self._save_settings()
            self.log("关闭窗口的行为改成：%s" % self._close_action_label(self.close_action))
            if self.close_action == "tray":
                self._start_tray()
            elif self.auto_var.get():
                self._sync_autostart()
            top.destroy()

        row = tk.Frame(body, bg=pal["bg"])
        row.pack(fill="x", pady=(uk.px(16), 0))
        uk.RoundButton(row, pal, i18n.t("确定", "OK"), kind="primary",
                       height=34, command=ok).pack(side="right")
        uk.RoundButton(row, pal, i18n.t("取消", "Cancel"), kind="ghost",
                       height=34, command=top.destroy).pack(
                           side="right", padx=(0, uk.px(8)))
        self._center_over(top)
        self._modal(top)
        top.wait_window()

    def _close_action_label(self, value):
        for v, label in (("ask", i18n.t("每次问我", "ask me every time")),
                         ("tray", i18n.t("最小化到托盘", "minimise to the tray")),
                         ("quit", i18n.t("直接退出", "quit"))):
            if v == value:
                return label
        return str(value)

    def _initial_close_action(self):
        """读设置；1.6.7 及更早只有 tray_on_close 那个布尔，这里给它换算过来。"""
        v = str(self.settings.get("close_action") or "").strip().lower()
        if v in ("ask", "tray", "quit"):
            return v
        return "tray" if self.settings.get("tray_on_close", True) else "quit"

    def _start_tray(self):
        if self.tray is not None:
            return True
        if not tray_mod.available():
            self.log("这个系统上用不了托盘图标。", "err")
            return False
        icon = None
        for cand in ("icon.ico", "DroidExtend.ico", "app.ico"):
            p = os.path.join(ROOT, cand)
            if os.path.isfile(p):
                icon = p
                break
        self.tray = tray_mod.Tray(
            tip="%s v%s" % (APP_NAME, APP_VERSION),
            on_command=self._tray_q.put,
            icon=icon,
            items=[(tray_mod.CMD_SHOW, i18n.t("显示主窗口", "Show the main window")),
                   (None, None),
                   (tray_mod.CMD_QUIT, i18n.t("完全退出", "Quit completely"))],
            log=self.log)
        if not self.tray.start():
            self.tray = None
            self.log("托盘图标启动失败。", "err")
            return False
        return True

    def _stop_tray(self):
        t, self.tray = self.tray, None
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass

    def _poll_tray(self):
        """把托盘线程丢过来的命令在 UI 线程里消化掉。"""
        try:
            while True:
                cmd = self._tray_q.get_nowait()
                if cmd == tray_mod.CMD_SHOW:
                    self._show_window()
                elif cmd == tray_mod.CMD_QUIT:
                    self._quit_now()
                    return
        except queue.Empty:
            pass
        if not self._quitting:
            self._tray_poll = self.after(250, self._poll_tray)

    def _show_window(self):
        try:
            self.deiconify()
            self.state("normal")
            _borderless(self)
            if getattr(self, "_fx", None) is not None:
                fr.refresh(self)
            self.lift()
            self.focus_force()
        except Exception:
            pass
        # Fade only after the window is back on screen; fading something the
        # window manager has not placed yet animates nothing but the taskbar.
        anim.fade_in(self, settings=self.settings)

    def _hide_to_tray(self):
        """收起窗口，只留托盘图标。托盘起不来就老老实实最小化。"""
        if not self._start_tray():
            self.iconify()
            return
        self.log("已缩到托盘，双击托盘图标可以叫回来。")
        # Withdraw in the callback, not here: hiding first would leave nothing
        # on screen to fade away, and the window would just vanish.
        anim.fade_out(self, settings=self.settings, on_done=self._really_hide)

    def _really_hide(self):
        try:
            self.withdraw()
        except Exception:
            pass

    def _sync_autostart(self):
        """把注册表里的自启动项对齐到界面上的勾。"""
        if not hasattr(self, "auto_var"):
            return
        try:
            if self.auto_var.get():
                extra = []
                # A machine that starts streaming by itself has nobody looking
                # at it, so the window must not appear as well.
                if self.close_action == "tray" or self._auto_stream_on():
                    extra.append("--minimized")
                if self._auto_stream_on():
                    extra.append("--stream")
                cmd = auto.write(extra)
                self.log("已设置开机自动启动：%s" % cmd)
            elif auto.enabled():
                auto.remove()
                self.log("已取消开机自动启动。")
        except Exception as e:
            self.log("设置开机自启动失败：%s" % e, "err")
        # 自动投屏只能挂在"开机自动启动"上：取消后者时前者必须跟着走，否则
        # 界面上会留一个永远不会发生的勾。
        if (hasattr(self, "stream_auto_var") and not self.auto_var.get()
                and self.stream_auto_var.get()):
            self.stream_auto_var.set(False)
            self.log("取消了开机自动启动，「开机后自动开始投屏」也一并取消。")

    def _auto_stream_on(self):
        try:
            return bool(self.stream_auto_var.get())
        except Exception:
            return False

    def _wait_value(self):
        """The wait, in seconds.  0 = keep waiting until someone stops it."""
        try:
            return int(_key_for(WAITS, self.wait_var.get(), 60) or 0)
        except Exception:
            return 60

    def _wait_words(self):
        n = self._wait_value()
        if n <= 0:
            return i18n.t("会一直等到手机接上为止", "it keeps waiting until the phone turns up")
        return i18n.t("最多等 %d 秒", "it waits up to %d s") % n

    def _auto_stream_choice(self):
        """用户动了「开机后自动开始投屏」。它得先有「开机自动启动」。"""
        on = self._auto_stream_on()
        if on and hasattr(self, "auto_var") and not self.auto_var.get():
            self.auto_var.set(True)
            self.log("一并打开了「开机自动启动」——没有它就没有东西拉起本程序。")
        self._sync_autostart()
        self._save_settings()
        if on:
            self.log("开机后会自己开始投屏，%s；等不到就发一条系统通知。" % self._wait_words())
        else:
            self.log("已取消开机后自动开始投屏。")

    def _wait_changed(self, *_):
        self._save_settings()
        self.log("开机后最多等手机：%s。" % self._wait_words())

    def _touch_choice(self):
        """用户动了「手机触摸控制电脑」。投屏中立刻生效，不用停止重来。"""
        on = bool(self.touch_var.get())
        self._save_settings()
        if self.session is None:
            return
        try:
            self.session.set_touch(on)
        except Exception as e:
            self.log("切换触摸控制失败：%s" % e, "err")

    def _quit_now(self):
        """彻底退出：窗口、托盘、采集、虚拟屏、adb 转发，一个都不留。

        应用更新时也走这里 —— 更新不能是个特例，否则会漏下一块幽灵显示器。
        """
        if self._quitting:
            return
        self._quitting = True
        if self._tray_poll:
            try:
                self.after_cancel(self._tray_poll)
            except Exception:
                pass
            self._tray_poll = None
        try:
            self._save_settings()
        except Exception:
            pass
        self._stop_tray()
        try:
            self.session.release_input()
        except Exception:
            pass
        # 窗口先消失：用户按下关闭就该马上看不到它。剩下的收尾（彻底关闭虚拟显示器
        # 驱动要等 PowerShell 轮询确认，十几秒）放到后台线程，main() 会在进程结束
        # 前等它跑完，所以设备不会留在「半关」的状态。
        try:
            self.withdraw()
        except Exception:
            pass
        self._quit_worker = threading.Thread(
            target=self._release_in_background, name="quit-release", daemon=True)
        self._quit_worker.start()
        # 已经排好的定时器（隐私提示、日志刷新、fps 状态）现在都指向一个马上要消失
        # 的窗口，让它们照常跑就是往日志里扔一串 "application has been destroyed"。
        try:
            for aid in self.tk.call("after", "info"):
                self.after_cancel(aid)
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def _release_in_background(self):
        """窗口没了之后要做的那些慢活。

        完全退出才做「彻底摘掉」：停流、确认桌面干净，再按设置里「退出程序时」
        那一项决定要不要禁用设备节点。停止投屏只摘桌面、不碰驱动，所以这里
        必须再走一遍。跑在后台线程里，因此只用能离开界面的途径（日志文件）。
        """
        try:
            self.session.shutdown()
        except Exception as e:
            self.log("退出时停止会话失败：%s" % e, "err")
        try:
            if self.session.adb is not None:
                self.session.adb.reverse_remove_all(None)
                self.log("已清理 adb 端口转发。")
                # The bundled adb runs a server that outlives the window and keeps
                # tools\platform-tools\adb.exe open.  Leaving it running is why an
                # uninstall used to finish with the adb files still in place, so
                # the daemon is shut down whenever the program exits.
                self.session.adb.kill_server()
                self.session.adb = None
        except Exception:
            pass
        applog.line("后台收尾结束")

    def wait_quit_work(self, limit=QUIT_WAIT):
        """等退出时的后台收尾跑完（窗口已经关了，用户看不到这一段）。"""
        w = self._quit_worker
        if w is not None and w.is_alive():
            w.join(limit)


# --------------------------------------------------------------- single copy
#
# Two copies of this program mean two tray icons, two adb servers and two of us
# fighting over the same virtual screen -- and nothing stops a second copy except
# this.  A named mutex is the cheapest arbitration that covers every way the
# program gets started (double-click, shortcut, autostart, the tray icon), because
# the kernel answers instead of us.  "Local\\" keeps it per logon session, which is
# what we want: a second user on the same machine is a different matter.
_INSTANCE_MUTEX = None      # module-level keeps the handle alive for the process
# Overridable so a test can take a mutex of its own instead of reporting "someone
# else is already running" whenever the author happens to have the app open.
MUTEX_NAME = "Local\\DroidExtend.single"


def _already_running():
    """True when another copy of us already holds the single-instance mutex."""
    global _INSTANCE_MUTEX
    if os.name != "nt":
        return False
    if _INSTANCE_MUTEX:
        # We are the owner; asking twice must not leak a second handle (closing
        # one would then leave the mutex alive and confuse a later check).
        return True
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        handle = k32.CreateMutexW(None, 1, MUTEX_NAME)
        if not handle:
            # Could not create one at all -- better to start than to refuse.
            return False
        _INSTANCE_MUTEX = handle
        return ctypes.get_last_error() == 183       # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def _raise_existing_window():
    """Bring the copy that is already running to the front.  Best effort."""
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        u32.FindWindowW.restype = ctypes.c_void_p
        for title in ("%s — %s   v%s" % (APP_NAME, app_title(), APP_VERSION),):
            hwnd = u32.FindWindowW(None, title)
            if hwnd:
                u32.ShowWindow(hwnd, 9)            # SW_RESTORE
                u32.SetForegroundWindow(hwnd)
                return True
    except Exception:
        pass
    return False


def _tell_already_running():
    """Last resort when the running copy has no window to raise (it is in the tray)."""
    text = i18n.t(
        "程序已经在运行了（可能是缩在托盘里）。\n\n看屏幕右下角的托盘区，双击那个图标就能把窗口叫回来。",
        "It is already running -- probably hidden in the tray.\n\n"
        "Double-click its icon in the notification area to bring the window back.")
    try:
        ctypes.windll.user32.MessageBoxW(
            None, text,
            "%s v%s" % (APP_NAME, APP_VERSION), 0x00000040)   # MB_ICONINFORMATION
    except Exception:
        pass


def main():
    # The log file is opened before anything can go wrong, so even a failure in
    # the checks below leaves a record of what the process saw.
    applog.start("%s v%s" % (APP_NAME, APP_VERSION))
    applog.line("ROOT %s" % ROOT)
    applog.line("数据目录 %s" % paths.data_dir())
    # Load the language before anything can show a message: the single-instance
    # notice is the one dialog that happens before the window exists.
    try:
        i18n.set_lang(st.load().get("language", i18n.AUTO))
    except Exception:
        i18n.set_lang(i18n.AUTO)
    if _already_running():
        applog.line("已经有一个实例在运行，本次启动直接退出（不想多出第二个托盘图标）")
        if not _raise_existing_window():
            _tell_already_running()
        applog.close()
        return 0
    # 1.6.6: everything lives in the program folder now.  Bring back anything an
    # older version put elsewhere and delete the old pointer, so "uninstall"
    # really does mean "remove this one folder".
    try:
        paths.migrate_old_data_dir(log=applog.line)
        paths.clean_old_temp(log=applog.line)
    except Exception as e:
        applog.line("迁移旧数据目录失败: %s" % e)
    st.migrate_legacy(log=applog.line)
    missing = []
    if not adb_exe():
        missing.append("adb")
    if not ffmpeg_exe():
        missing.append("ffmpeg")
    if missing:
        applog.line("缺少组件: " + ", ".join(missing))
        # No App() yet, so nothing has read the language setting: without this
        # the one dialog that appears before the window would always be Chinese.
        try:
            i18n.set_lang(st.load().get("language", i18n.AUTO))
        except Exception:
            i18n.set_lang(i18n.AUTO)
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(i18n.t("缺少组件", "Missing components"),
                             i18n.t("找不到: ", "Not found: ") + ", ".join(missing))
        applog.close()
        return 1
    # 1.8.7: the driver installer drives tools/vdd_install.ps1 through Windows
    # PowerShell.  A stripped Windows 10 image ships a powershell.exe that dies
    # at startup with 0xFFFF0000, which used to surface as
    # "安装脚本退出码 = 4294901760" plus an empty log that was supposed to
    # explain it.  Probe once here so the log records which PowerShell we will
    # use, or that there is none, before the user ever presses the button.
    try:
        import ps
        if ps.exe():
            applog.line("PowerShell %s: %s" % (ps.version(), ps.exe()))
        else:
            applog.line("没有可用的 PowerShell: " + ps.hint())
    except Exception as e:
        applog.line("PowerShell 探测失败: %s" % e)
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
        # 窗口已经关了，用户看不到这一段：等后台的那个线程把虚拟屏真正收干净
        # 再结束进程，否则进程一退，深档的禁用就做了一半。
        try:
            app.wait_quit_work()
        except Exception:
            pass
        applog.line("退出")
        applog.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
