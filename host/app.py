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
import subprocess
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

from capture import (ENCODERS, NVENC_PRESETS, X264_PRESETS,                 # noqa: E402
                     list_hardware_encoders, available_encoders)
from paths import ROOT, apk_path, adb_exe, ffmpeg_exe                             # noqa: E402
from session import Session, DEFAULTS, VddMissing                                  # noqa: E402
import applog                                                                     # noqa: E402
import anim                                                                       # noqa: E402
import autostart as auto                                                          # noqa: E402
import changelog as cl                                                            # noqa: E402
import i18n                                                                       # noqa: E402
import manual as mn                                                               # noqa: E402
import paths                                                                      # noqa: E402
import privacy as priv                                                            # noqa: E402
import settings as st                                                             # noqa: E402
import touch as tu                                                                # noqa: E402
import tray as tray_mod                                                           # noqa: E402
import update as upd                                                              # noqa: E402
from version import (APP_NAME, APP_TITLE, APP_VERSION, APP_DATE, AUTHOR, OWNER,   # noqa: E402
                     EMAILS, REPO_URL, RELEASES_URL, ISSUES_URL, app_title)

# #3: 被开机自启动拉起时不把窗口糊到人家脸上，直接进托盘待命。
MINIMIZED = "--minimized" in sys.argv

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
QUALITY_CHOICES = ["14", "16", "18", "20", "22", "24", "26", "28", "32", "36"]
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

BG = "#14161a"
FG = "#e6e6e6"
ACCENT = "#4c9aff"
PANEL = "#1c1f26"


def _credits():
    """(component, licence, where it comes from) rows for the About window.

    Also mirrored in 使用说明 and in the README, because two of these are
    redistributed inside the installer and MIT asks for the notice to travel
    with the copy.

    A function rather than a constant: the names are proper nouns and read the
    same in both languages except for Inno Setup, whose row carries a note, and
    a table built at import time would freeze the language before the settings
    file has even been read.
    """
    return [
        ("Virtual Display Driver (VDD)", "MIT", "github.com/VirtualDrivers/Virtual-Display-Driver"),
        ("nefcon", "MIT", "github.com/nefarius/nefcon"),
        ("FFmpeg 6.0 (gyan.dev essentials)", "GPL-3.0", "ffmpeg.org"),
        ("Android SDK Platform-Tools / adb", "Apache-2.0", "developer.android.com/tools/releases/platform-tools"),
        ("R8 / D8 (Android build-tools)", "BSD-3-Clause", "r8.googlesource.com/r8"),
        ("Python", "PSF-2.0", "python.org"),
        ("Tcl/Tk", "BSD-style", "tcl.tk"),
        ("PyInstaller", "GPL-2.0 + bootloader exception", "pyinstaller.org"),
        (i18n.t("Inno Setup（只用于生成安装包）",
                "Inno Setup (used only to build the installer)"),
         "Inno Setup License", "jrsoftware.org/isinfo.php"),
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


def _custom_label():
    """Caption of the "custom" choice, shared by the size and position boxes.

    A function because it has to follow the active language, and because both
    call sites compare the combobox contents against it: one string, so the two
    can never disagree about what "custom" is spelled like.
    """
    return i18n.t("自定义", "Custom")


def _install_driver_label():
    """The install-driver button.

    The error that tells the user to press it quotes this verbatim, so both come
    from here instead of from two literals that could drift apart.
    """
    return i18n.t("安装虚拟显示器驱动（需要管理员）",
                  "Install the virtual display driver (needs admin)")


def _refresh_captions():
    """(Re)build the translated (value, caption) tables -- see the note above them."""
    RATECONTROL[:] = [
        ("vbr", i18n.t("VBR 质量优先（静止桌面省带宽）",
                       "VBR -- quality first (an idle desktop costs less bandwidth)")),
        ("cbr", i18n.t("CBR 恒定码率（运动画面更稳）",
                       "CBR -- constant bitrate (steadier under motion)")),
    ]
    BACKENDS[:] = [
        ("auto", i18n.t("自动（优先 ddagrab）", "Auto (prefers ddagrab)")),
        ("ddagrab", i18n.t("ddagrab（桌面复制，最快）",
                           "ddagrab (desktop duplication, fastest)")),
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


# Filled once here so the lists are never empty for something that reads them
# without a running App (probes, tools/gui_test.py); at import time i18n has not
# been set yet, so this is the Chinese pair, and App._build() replaces it with
# whichever language the settings file asked for.
_refresh_captions()


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
        # Resolve the language before a single widget exists: every label is
        # built through i18n.t(), so the setting has to be applied first or the
        # window comes up half in each language.
        i18n.set_lang(self.settings.get("language", "auto"))
        self.upd_busy = False
        self._manual_win = None
        self._manual_txt = None
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

        # app_title() and not the APP_TITLE constant: the constant was evaluated
        # when version.py was first imported (line 64), which is before the
        # set_lang() call above -- so it would always read as Chinese.
        self.title("%s — %s   v%s" % (APP_NAME, app_title(), APP_VERSION))
        self.configure(bg=BG)
        self.geometry(_safe_geometry(self.settings.get("window")))
        self.minsize(900, 660)

        self.logq = queue.Queue()
        # Every line the pane is currently showing, as (text, tag).  Kept
        # because a language switch rebuilds the whole window, and the pane has
        # to come back with its colours intact.
        self._log_lines = []
        self.session = Session(log=self.log, on_notice=self._phone_notice)
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
        # The terms come first, before anything else this program does.  Scheduled
        # rather than called inline so the main window is already on screen
        # behind the dialog.
        self.after(150, self._check_privacy)
        self.after(300, self.refresh_env)
        self.after(1800, self._check_orphans)
        self.after(200, self._poll_tray)
        if self.settings.get("check_updates", True) and not priv.needs_accept(self.settings):
            self.after(2500, lambda: self.on_check_updates(manual=False))
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
        bar = tk.Menu(self, tearoff=0)

        m_file = tk.Menu(bar, tearoff=0)
        m_file.add_command(label=i18n.t("打开数据目录（设置与日志）",
                                        "Open data folder (settings and logs)"),
                           command=self.on_open_config_dir)
        m_file.add_command(label=i18n.t("更改数据目录…", "Change data folder..."),
                           command=self.on_change_data_dir)
        m_file.add_command(label=i18n.t("打开程序目录", "Open program folder"),
                           command=self.on_open_root_dir)
        m_file.add_separator()
        m_file.add_command(label=i18n.t("退出", "Exit"), command=self._on_close)
        bar.add_cascade(label=i18n.t("文件", "File"), menu=m_file)

        m_tool = tk.Menu(bar, tearoff=0)
        m_tool.add_command(label=i18n.t("安装虚拟显示器驱动…",
                                        "Install virtual display driver..."),
                           command=self.on_install_driver)
        m_tool.add_command(label=i18n.t("仅安装 APK 并启动", "Install APK only and start"),
                           command=self.on_install_apk)
        m_tool.add_separator()
        m_tool.add_command(label=i18n.t("清理残留的虚拟屏", "Clean up a leftover virtual screen"),
                           command=self.on_cleanup_orphans)
        m_tool.add_command(label=i18n.t("诊断连接问题…", "Diagnose connection problems..."),
                           command=self.on_diagnose)
        m_tool.add_command(label=i18n.t("安装 USB 驱动…", "Install USB driver..."),
                           command=self.on_install_usb_driver)
        m_tool.add_separator()
        m_tool.add_command(label=i18n.t("测一下数据线速度", "Measure cable speed"),
                           command=self.on_linkspeed)
        m_tool.add_separator()
        self.chk_var = tk.BooleanVar(value=bool(self.settings.get("check_updates", True)))
        m_tool.add_checkbutton(label=i18n.t("启动时自动检查更新", "Check for updates on start"),
                               variable=self.chk_var, command=self._save_settings)
        m_tool.add_command(label=i18n.t("检查更新…", "Check for updates..."),
                           command=lambda: self.on_check_updates(manual=True))
        m_tool.add_command(label=i18n.t("更新日志", "Release notes"),
                           command=self.on_show_changelog)
        m_tool.add_separator()
        m_tool.add_command(label=i18n.t("重置为默认设置", "Reset to defaults"),
                           command=self.on_reset_settings)
        m_tool.add_separator()
        m_tool.add_command(label=i18n.t("复制全部日志到剪贴板", "Copy all logs to clipboard"),
                           command=self.on_copy_log)
        m_tool.add_command(label=i18n.t("日志另存为…", "Save logs as..."),
                           command=self.on_save_log)
        bar.add_cascade(label=i18n.t("工具", "Tools"), menu=m_tool)

        # Between Tools and Help rather than before File: File stays first
        # because that is where every Windows user looks for Exit, and the
        # language picker is a preference like the ones behind Tools -- it does
        # not belong in front of the things people open the menu bar for.
        m_lang = tk.Menu(bar, tearoff=0)
        self.lang_var = tk.StringVar(value=self._lang_setting())
        for code, caption in i18n.choices():
            m_lang.add_radiobutton(label=caption, value=code, variable=self.lang_var,
                                   command=lambda c=code: self._switch_lang(c))
        bar.add_cascade(label=i18n.t("语言", "Language"), menu=m_lang)

        m_help = tk.Menu(bar, tearoff=0)
        m_help.add_command(label=i18n.t("使用说明", "Manual"), command=self.on_manual)
        # The anchors are headings inside manual.py, which ships two complete
        # manuals rather than one translated text -- so the anchor has to be the
        # heading of the language in use, not the Chinese one.
        m_help.add_command(label=i18n.t("常见问题", "Common questions"),
                           command=lambda: self.on_manual(
                               i18n.t("【六、常见问题】", "[6] Common questions")))
        m_help.add_command(label=i18n.t("参数怎么选", "How to choose the settings"),
                           command=lambda: self.on_manual(
                               i18n.t("【五、参数怎么选】", "[5] How to choose the settings")))
        m_help.add_separator()
        m_help.add_command(label=i18n.t("项目主页（GitHub）", "Project home (GitHub)"),
                           command=lambda: self.open_url(REPO_URL))
        m_help.add_command(label=i18n.t("反馈问题 / 提建议", "Report a problem / suggest something"),
                           command=lambda: self.open_url(ISSUES_URL))
        m_help.add_command(label=i18n.t("检查更新…", "Check for updates..."),
                           command=lambda: self.on_check_updates(manual=True))
        m_help.add_separator()
        m_help.add_command(label=i18n.t("使用条款与隐私说明", "Terms and privacy"),
                           command=self.on_show_privacy)
        m_help.add_command(label=i18n.t("第三方组件许可", "Third-party licences"),
                           command=self.on_licenses)
        m_help.add_command(label=i18n.t("关于 ", "About ") + APP_NAME, command=self.on_about)
        bar.add_cascade(label=i18n.t("帮助", "Help"), menu=m_help)

        self.configure(menu=bar)

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
        self._rebuild_window()

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
        self.mouse_var.set(bool(s.get("draw_mouse", False)))
        self.inst_var.set(bool(s.get("install_apk", True)))
        self.match_var.set(bool(s.get("match_phone", True)))
        if hasattr(self, "chk_var"):
            self.chk_var.set(bool(s.get("check_updates", True)))
        self.pos_x_var.set(str(s.get("pos_x", 0)))
        self.pos_y_var.set(str(s.get("pos_y", 0)))
        self._res_changed()
        self._rc_changed()
        self._sync_pos()

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
        s["install_apk"] = bool(self.inst_var.get())
        s["match_phone"] = bool(self.match_var.get())
        s["mode"] = self.mode_var.get()
        if hasattr(self, "tray_var"):
            s["tray_on_close"] = bool(self.tray_var.get())
        if hasattr(self, "auto_var"):
            s["autostart"] = bool(self.auto_var.get())
        if hasattr(self, "anim_var"):
            s[anim.KEY] = bool(self.anim_var.get())
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
        # The dropdown captions are language-dependent and are read from here
        # down, so they are rebuilt before the first widget asks for one.
        _refresh_captions()
        # Same reason as the captions: app_title() reads the language as it is
        # *now*, and _build() runs again on every language switch -- so this is
        # what makes the title bar follow the switch.
        self.title("%s — %s   v%s" % (APP_NAME, app_title(), APP_VERSION))
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
        style.configure("TProgressbar", background=ACCENT, troughcolor=PANEL,
                        bordercolor="#2c313a", lightcolor=ACCENT, darkcolor=ACCENT,
                        thickness=14)
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
        left = ttk.Labelframe(outer, text=i18n.t(" 设备与显示器 ", " Device and display "),
                              padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        left.columnconfigure(0, weight=1)

        row = ttk.Frame(left)
        row.grid(row=0, column=0, sticky="ew")
        ttk.Label(row, text=i18n.t("安卓设备:", "Android device:")).pack(side="left")
        self.dev_var = tk.StringVar(value=i18n.t("未检测", "Not detected"))
        self.dev_lbl = ttk.Label(row, textvariable=self.dev_var, style="Dim.TLabel")
        self.dev_lbl.pack(side="left", padx=8)
        ttk.Button(row, text=i18n.t("刷新", "Refresh"), command=self.refresh_env,
                   width=6).pack(side="right")

        self.drv_var = tk.StringVar(value=i18n.t("检测中…", "Detecting..."))
        ttk.Label(left, text=i18n.t("虚拟显示器:", "Virtual display:")).grid(
            row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(left, textvariable=self.drv_var, style="Dim.TLabel", wraplength=420,
                  justify="left").grid(row=2, column=0, sticky="w")

        btns = ttk.Frame(left)
        btns.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.btn_install = ttk.Button(btns, text=_install_driver_label(),
                                      command=self.on_install_driver)
        self.btn_install.pack(side="left")
        ttk.Button(btns, text=i18n.t("仅安装 APK 并启动", "Install APK only and start"),
                   command=self.on_install_apk).pack(side="left", padx=6)

        self.mon_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.mon_var, style="Mono.TLabel", justify="left",
                  wraplength=430).grid(row=4, column=0, sticky="w", pady=(8, 0))

        # -- right: quality --------------------------------------------------
        right = ttk.Labelframe(outer, text=i18n.t(" 画质与帧率 ", " Quality and frame rate "),
                               padding=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))
        right.columnconfigure(1, weight=1)
        right.columnconfigure(3, weight=1)

        def lab(r, c, t):
            ttk.Label(right, text=t).grid(row=r, column=c, sticky="w", pady=3, padx=(0, 4))

        self.mode_var = tk.StringVar(value="extend")
        lab(0, 0, i18n.t("模式", "Mode"))
        mf = ttk.Frame(right)
        mf.grid(row=0, column=1, columnspan=3, sticky="w")
        # "拓展屏" is the extended desktop, not a mirror -- the English has to
        # keep that distinction, so it never becomes "mirror".
        for v, t in (("extend", i18n.t("拓展屏", "Extend")),
                     ("mirror", i18n.t("镜像", "Mirror"))):
            ttk.Radiobutton(mf, text=t, value=v, variable=self.mode_var,
                            command=self._mode_changed).pack(side="left", padx=(0, 12))

        self.res_var = tk.StringVar(value="1920x1080")
        lab(1, 0, i18n.t("分辨率", "Resolution"))
        self.res_cb = ttk.Combobox(right, textvariable=self.res_var, width=14,
                                   values=["%dx%d" % r for r in RESOLUTIONS]
                                   + [_custom_label()])
        self.res_cb.grid(row=1, column=1, sticky="w", pady=3)

        lab(1, 2, i18n.t("刷新率", "Refresh rate"))
        self.hz_var = tk.StringVar(value="60")
        self.hz_cb = ttk.Combobox(right, textvariable=self.hz_var, width=8, values=HZ_CHOICES)
        self.hz_cb.grid(row=1, column=3, sticky="w", pady=3)
        self.hz_cb.bind("<<ComboboxSelected>>", self._layout_changed)

        self.fps_var = tk.StringVar(value="60")
        lab(2, 0, i18n.t("帧率 fps", "Frame rate fps"))
        self.fps_cb = ttk.Combobox(right, textvariable=self.fps_var, width=14, values=FPS_CHOICES)
        self.fps_cb.grid(row=2, column=1, sticky="w", pady=3)

        # Widget defaults come from DEFAULTS so there is a single source of
        # truth: _apply_form() pushes these back into cfg on every start.
        self.br_var = tk.StringVar(value=str(DEFAULTS["bitrate_mbps"]))
        lab(2, 2, i18n.t("码率上限 Mbps", "Bitrate ceiling Mbps"))
        self.br_cb = ttk.Combobox(right, textvariable=self.br_var, width=8, values=BITRATE_CHOICES)
        self.br_cb.grid(row=2, column=3, sticky="w", pady=3)

        self.rc_var = tk.StringVar(value=self._rc_caption())
        lab(3, 0, i18n.t("码率控制", "Rate control"))
        self.rc_cb = ttk.Combobox(right, textvariable=self.rc_var, width=26, state="readonly",
                                  values=[t for _, t in RATECONTROL])
        self.rc_cb.grid(row=3, column=1, columnspan=1, sticky="w", pady=3)
        self.rc_cb.bind("<<ComboboxSelected>>", self._rc_changed)

        self.q_var = tk.StringVar(value="20")
        self.q_hint_var = tk.StringVar(value="")
        lab(3, 2, i18n.t("画质 Q", "Quality Q"))
        self.q_cb = ttk.Combobox(right, textvariable=self.q_var, width=8, values=QUALITY_CHOICES)
        self.q_cb.grid(row=3, column=3, sticky="w", pady=3)

        self.custom_frame = ttk.Frame(right)
        self.w_var = tk.StringVar(value="1920")
        self.h_var = tk.StringVar(value="1080")
        ttk.Label(self.custom_frame, text=i18n.t("宽", "W")).pack(side="left")
        ttk.Entry(self.custom_frame, textvariable=self.w_var, width=6).pack(side="left", padx=3)
        ttk.Label(self.custom_frame, text=i18n.t("高", "H")).pack(side="left")
        ttk.Entry(self.custom_frame, textvariable=self.h_var, width=6).pack(side="left", padx=3)
        # a typed-in size changes the screen just as much as the dropdown does
        self.w_var.trace_add("write", self._layout_changed)
        self.h_var.trace_add("write", self._layout_changed)
        self.custom_frame.grid(row=4, column=1, columnspan=3, sticky="w", pady=3)
        self.custom_frame.grid_remove()

        # Row 4 is shared: either "follow the phone's own resolution" or the
        # custom width/height entries, never both.  Following the phone is the
        # default because it is the only way to fill the panel edge to edge.
        self.match_var = tk.BooleanVar(value=bool(DEFAULTS.get("match_phone", True)))
        self.match_chk = ttk.Checkbutton(
            right, text=i18n.t("跟随手机分辨率（撑满整块屏，不留黑边）",
                               "Match the phone's resolution (fills the panel, no bars)"),
            variable=self.match_var, command=self._match_changed)
        self.match_chk.grid(row=4, column=0, columnspan=4, sticky="w", pady=3)
        self.res_cb.bind("<<ComboboxSelected>>", self._res_changed)
        self._load_resolutions()

        self.enc_var = tk.StringVar(value="h264_nvenc")
        lab(5, 0, i18n.t("编码器", "Encoder"))
        self.enc_cb = ttk.Combobox(right, textvariable=self.enc_var, width=14, state="readonly")
        self.enc_cb.grid(row=5, column=1, sticky="w", pady=3)
        self.enc_cb.bind("<<ComboboxSelected>>", self._enc_changed)

        self.pre_var = tk.StringVar(value="p4")
        lab(5, 2, i18n.t("预设", "Preset"))
        self.pre_cb = ttk.Combobox(right, textvariable=self.pre_var, width=8)
        self.pre_cb.grid(row=5, column=3, sticky="w", pady=3)

        self.be_var = tk.StringVar(value="auto")
        lab(6, 0, i18n.t("采集", "Capture"))
        self.be_cb = ttk.Combobox(right, textvariable=self.be_var, width=14, state="readonly",
                                  values=[t for _, t in BACKENDS])
        self.be_cb.grid(row=6, column=1, sticky="w", pady=3)

        self.pos_var = tk.StringVar(value="right")
        lab(6, 2, i18n.t("屏幕位置", "Position"))
        self.pos_cb = ttk.Combobox(right, textvariable=self.pos_var, width=8, state="readonly",
                                   values=[t for _, t in POSITIONS])
        self.pos_cb.grid(row=6, column=3, sticky="w", pady=3)
        self.pos_cb.bind("<<ComboboxSelected>>", self._pos_changed)

        # "自定义" gets its own X/Y row -- the same trick the custom resolution
        # uses: hidden until the mode that needs it is actually picked.
        self.pos_frame = ttk.Frame(right)
        self.pos_x_var = tk.StringVar(value="0")
        self.pos_y_var = tk.StringVar(value="0")
        ttk.Label(self.pos_frame, text="X").pack(side="left")
        ttk.Entry(self.pos_frame, textvariable=self.pos_x_var, width=7).pack(side="left", padx=3)
        ttk.Label(self.pos_frame, text="Y").pack(side="left")
        ttk.Entry(self.pos_frame, textvariable=self.pos_y_var, width=7).pack(side="left", padx=3)
        ttk.Label(self.pos_frame, text=i18n.t("虚拟屏左上角在主屏坐标系里的位置",
                                              "Top-left corner of the virtual screen, in "
                                              "main-screen coordinates"),
                  style="Dim.TLabel").pack(side="left")
        self.pos_frame.grid(row=11, column=1, columnspan=3, sticky="w", pady=3)
        self.pos_frame.grid_remove()
        self.pos_x_var.trace_add("write", self._layout_changed)
        self.pos_y_var.trace_add("write", self._layout_changed)

        self.mouse_var = tk.BooleanVar(value=False)
        self.mouse_chk = ttk.Checkbutton(right, text=i18n.t("显示鼠标指针", "Draw the mouse pointer"),
                                         variable=self.mouse_var)
        self.mouse_chk.grid(row=7, column=1, sticky="w", pady=3)
        self.inst_var = tk.BooleanVar(value=True)
        self.inst_chk = ttk.Checkbutton(right, text=i18n.t("每次重新安装 APK",
                                                           "Reinstall the APK every time"),
                                        variable=self.inst_var)
        self.inst_chk.grid(row=7, column=2, columnspan=2, sticky="w", pady=3)

        self.tray_var = tk.BooleanVar(value=bool(self.settings.get("tray_on_close", True)))
        self.tray_chk = ttk.Checkbutton(right, text=i18n.t("关闭窗口时缩到托盘",
                                                           "Shrink to tray when the window closes"),
                                        variable=self.tray_var, command=self._tray_choice)
        self.tray_chk.grid(row=12, column=1, sticky="w", pady=3)
        self.auto_var = tk.BooleanVar(value=bool(self.settings.get("autostart", False)))
        self.auto_chk = ttk.Checkbutton(right, text=i18n.t("开机自动启动", "Start with Windows"),
                                        variable=self.auto_var,
                                        command=self._sync_autostart)
        self.auto_chk.grid(row=12, column=2, columnspan=2, sticky="w", pady=3)

        self.anim_var = tk.BooleanVar(value=bool(self.settings.get(anim.KEY, True)))
        self.anim_chk = ttk.Checkbutton(right, text=anim.label(), variable=self.anim_var,
                                        command=self._anim_choice)
        self.anim_chk.grid(row=13, column=1, sticky="w", pady=3)

        self.link_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.link_var, style="Dim.TLabel", wraplength=430,
                  justify="left").grid(row=8, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Button(right, text=i18n.t("测一下数据线速度", "Measure cable speed"),
                   command=self.on_linkspeed).grid(row=9, column=1, sticky="w", pady=(4, 0))
        ttk.Label(right, textvariable=self.q_hint_var, style="Dim.TLabel", wraplength=430,
                  justify="left").grid(row=10, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # -- actions ---------------------------------------------------------
        act = ttk.Frame(outer)
        act.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.btn_start = ttk.Button(act, text=i18n.t("▶  开始投屏", "▶  Start"),
                                    style="Go.TButton", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(act, text=i18n.t("■  停止", "■  Stop"),
                                   command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        self.stat_var = tk.StringVar(value=i18n.t("就绪", "Ready"))
        ttk.Label(act, textvariable=self.stat_var, style="Big.TLabel").pack(side="left", padx=16)
        ttk.Button(act, text=i18n.t("使用说明", "Manual"), command=self.on_manual).pack(side="right")
        ttk.Button(act, text=i18n.t("检查更新", "Check for updates"),
                   command=lambda: self.on_check_updates(manual=True)).pack(side="right", padx=6)
        self.perf_var = tk.StringVar(value="")
        ttk.Label(act, textvariable=self.perf_var, style="Mono.TLabel").pack(side="right", padx=10)

        # -- log -------------------------------------------------------------
        logf = ttk.Labelframe(outer, text=i18n.t(" 日志 ", " Log "), padding=6)
        logf.grid(row=2, column=0, columnspan=2, sticky="nsew")
        logf.rowconfigure(1, weight=1)
        logf.columnconfigure(0, weight=1)
        lbar = ttk.Frame(logf)
        lbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Button(lbar, text=i18n.t("复制全部", "Copy all"), width=10,
                   command=self.on_copy_log).pack(side="left")
        ttk.Button(lbar, text=i18n.t("另存为…", "Save as..."), width=10,
                   command=self.on_save_log).pack(side="left", padx=6)
        ttk.Button(lbar, text=i18n.t("清空", "Clear"), width=8,
                   command=self.on_clear_log).pack(side="left")
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
        self.file_var = tk.StringVar(value=i18n.t("数据目录: %s", "Data folder: %s")
                                     % paths.data_dir())
        ttk.Label(bar, textvariable=self.file_var, style="Dim.TLabel").pack(side="right")

        # Any settings change is written straight out, so even a crash leaves
        # the next start on the settings you picked.  add="+" keeps the existing
        # handlers (which fix up presets and the Q field) running first.
        for cb in (self.res_cb, self.hz_cb, self.fps_cb, self.br_cb, self.rc_cb,
                   self.q_cb, self.enc_cb, self.pre_cb, self.be_cb, self.pos_cb):
            cb.bind("<<ComboboxSelected>>", self._changed_any, add="+")
        for chk in (self.mouse_chk, self.inst_chk):
            chk.configure(command=self._changed_any)

        self._arm_perf()

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
            self.custom_frame.grid()
        else:
            self.custom_frame.grid_remove()
        if custom and self.match_var.get():
            # the two are mutually exclusive: picking a custom size means the
            # user wants that size, not whatever the phone reports
            self.match_var.set(False)
        self._sync_match()
        self._maybe_retarget()

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
        """#10: warn about a virtual screen that is on with nothing streaming.

        Switching the screen on went through ChangeDisplaySettingsEx with
        CDS_UPDATEREGISTRY, which Windows persists; switching it off only ever
        took effect in the current session.  So the switch-on came back at every
        boot and the switch-off did not, and the screen sat there activated with
        DroidExtend never started.  detach() saves to the database now, but a
        leftover from before still needs taking down once -- hence the menu
        entry, and this nudge rather than doing it behind the user's back.
        """
        try:
            orphans = [m for m in wd.active_virtual_displays() if not m["primary"]]
        except Exception:
            return
        if not orphans:
            return
        names = "、".join(m["name"] for m in orphans)
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
        else:
            self.log("没有需要清理的虚拟屏")

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
        try:
            a = adb.Adb()
            self.log("  adb %s" % a.version())
            devs = a.devices()
        except Exception as e:
            self.log("  adb 不可用：%s" % e, "err")
        for d in devs:
            self.log("  手机：%s" % d["label"])
        if not devs:
            self.log("  没有检测到手机", "err")
            h = adb.usb_mode_hint(devs)
            if h:
                self.log("  " + h)
            for status, cls, name in adb.android_problem_devices():
                self.log("  设备管理器：%s（%s）" % (name, status))
            self.log("  驱动下载：%s" % adb.DRIVER_URL)
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
            # A start that dies half way (phone unplugged, driver refused the
            # mode) can leave the virtual display sitting on the desktop.  Take
            # it back off so "断开之后系统里还留着一块屏" cannot happen.
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
        self._later(lambda: self.btn_install.configure(
            state="disabled" if st["state"] == "ready" else "normal"))

        mons = wd.live_monitors()
        text = "\n".join("%-12s %-28s %dx%d@%d  @(%d,%d)%s" % (
            m["name"], (m["monitor"] or m["desc"])[:28], m["width"], m["height"],
            m["hz"], m["left"], m["top"],
            i18n.t("  [主]", "  [primary]") if m["primary"] else "") for m in mons)
        self._later(lambda: self.mon_var.set(
            i18n.t("当前桌面显示器:\n", "Current desktop monitors:\n")
            + (text or i18n.t("（无）", "(none)"))))
        if st["state"] != "ready":
            self.log("虚拟显示器状态: " + vd.summary_line(st))

    # ---------------------------------------------------------------- actions
    def on_install_driver(self):
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
        for line in (out or "").splitlines()[-40:]:
            self.log("  " + line)
        if not ok:
            raise RuntimeError(i18n.t("驱动安装未成功，详见日志。",
                                      "The driver was not installed -- see the log."))
        self.log("虚拟显示器驱动安装完成。", "ok")
        self._later(lambda: self.stat_var.set(i18n.t("驱动已安装", "Driver installed")))
        self._refresh_work()

    def on_install_apk(self):
        self.run_bg(self._install_apk_work)

    def _install_apk_work(self):
        self._later(lambda: self.stat_var.set(i18n.t("安装 APK…", "Installing the APK...")))
        self.session.cfg["install_apk"] = self.inst_var.get()
        self.session.find_device()
        self.session.prepare_device()
        self._later(lambda: self.stat_var.set(i18n.t("APK 已就绪", "APK ready")))

    def on_start(self):
        self._apply_form()
        self._save_settings()          # starting is a good moment to persist
        self.run_bg(self._start_work)

    def _start_work(self):
        self._later(lambda: self.stat_var.set(i18n.t("准备拓展屏…",
                                                     "Preparing the extended display...")))
        self.session.apply_display()
        self._later(lambda: self.stat_var.set(i18n.t("连接手机…", "Connecting to the phone...")))
        self.session.find_device()
        self.session.prepare_device()
        self._later(lambda: self.stat_var.set(i18n.t("启动编码…", "Starting the encoder...")))
        self.session.start_stream()
        self.log("=== 投屏已开始：%s ===" % self.session.describe(), "ok")
        self._later(self._started)

    def _started(self):
        self.stat_var.set(i18n.t("投屏中", "Streaming"))
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")

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
        cfg["mode"] = self.mode_var.get()
        cfg["install_apk"] = bool(self.inst_var.get())
        cfg["tray_on_close"] = bool(self.tray_var.get())
        cfg["autostart"] = bool(self.auto_var.get())
        self.settings[anim.KEY] = bool(self.anim_var.get())
        if cfg["mode"] == "extend":
            cfg["bitrate_mbps"] = max(cfg["bitrate_mbps"], 4)
        self._sync_autostart()

    def on_stop(self):
        self.btn_stop.configure(state="disabled")
        self.run_bg(self.session.stop, done=self._stopped)

    def _stopped(self):
        self.stat_var.set(i18n.t("已停止", "Stopped"))
        self.perf_var.set("")
        self.btn_start.configure(state="normal")

    def _poll_perf(self):
        s = self.session
        if s is not None and getattr(s, "fatal", None):
            # The capture watchdog gave up (the virtual display is gone).
            self.perf_var.set(s.fatal)
        elif s is not None and s.server and not s.running:
            # ffmpeg died and is being restarted -- usually one or two seconds.
            self.perf_var.set(i18n.t("采集中断，正在自动重连…",
                                     "Capture interrupted, reconnecting..."))
        elif s is not None and s.running and s.server:
            st = s.stats
            conn = (i18n.t("手机已连接", "phone connected") if s.server.connected
                    else i18n.t("等待手机连接…", "waiting for the phone..."))
            self.perf_var.set(i18n.t(
                "%s   %.1f fps   编码 %.1f Mbps   送出 %d 帧   丢 %d",
                "%s   %.1f fps   encoded %.1f Mbps   sent %d   dropped %d") % (
                conn, st["fps"], st["mbps"], st["sent"], st["dropped"]))
        self._perf_job = self.after(700, self._poll_perf)

    def _on_close(self):
        """窗口上的 ×。开了托盘就只是收起来，不然就真的退出。（日志按项目约定保持中文）"""
        self._save_settings()          # remember the window and the parameters
        if self.tray_var.get() and not self._quitting:
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

    def on_change_data_dir(self):
        """Move settings.json and DroidExtend.log somewhere else.

        Kept to one folder on purpose: "send me your log" should not also need
        "and find settings.json in your user profile".
        """
        old = paths.data_dir()
        d = filedialog.askdirectory(
            title=i18n.t("选择保存设置和日志的文件夹",
                         "Pick the folder for settings and logs"),
            parent=self, initialdir=old, mustexist=False)
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
            messagebox.showerror(i18n.t("切换失败", "Could not switch"),
                                 "%s\n\n%s" % (e, d), parent=self)
            return
        # Reopen the log at the new place so the rest of this run lands there.
        applog.close()
        applog.start("%s v%s" % (APP_NAME, APP_VERSION))
        self.log("数据目录已改为: %s  (已搬移: %s)" % (d, "、".join(moved) or "无"))
        self._refresh_paths_label()
        messagebox.showinfo(
            i18n.t("数据目录已更改", "Data folder changed"),
            i18n.t("设置和日志现在保存在：\n%s\n\n已搬移：%s\n以后所有设置和日志都写在这里。",
                   "Settings and logs now live in:\n%s\n\nMoved: %s\n"
                   "Everything from now on is written there.")
            % (d, "、".join(moved) or i18n.t("（没有旧文件）", "(no old files)")), parent=self)

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
            win = tk.Toplevel(self)
            self._manual_win = win
            win.title(i18n.t("使用说明 — %s v%s", "Manual - %s v%s")
                      % (APP_NAME, APP_VERSION))
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
            txt.insert("1.0", mn.text())
            txt.configure(state="disabled")
            self._manual_txt = txt
            foot = ttk.Frame(win, padding=(12, 8))
            foot.grid(row=1, column=0, columnspan=2, sticky="ew")
            ttk.Button(foot, text=i18n.t("项目主页", "Project home"),
                       command=lambda: self.open_url(REPO_URL)).pack(side="left")
            ttk.Button(foot, text=i18n.t("反馈问题", "Report a problem"),
                       command=lambda: self.open_url(ISSUES_URL)).pack(side="left", padx=6)
            ttk.Button(foot, text=i18n.t("关闭", "Close"),
                       command=win.destroy).pack(side="right")
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
                    i18n.t("检查更新", "Check for updates"),
                    i18n.t("查询 GitHub 失败：\n%s\n\n（离线 / 被墙都不影响正常使用，"
                           "可以手动打开：\n%s）",
                           "Could not reach GitHub:\n%s\n\n(Being offline or blocked does not "
                           "stop the app working -- you can open this yourself:\n%s)")
                    % (r["error"], RELEASES_URL), parent=self)
            return
        if r["newer"]:
            self.log("发现新版本 v%s（当前 v%s）：%s" % (r["latest"], APP_VERSION, r["url"]), "ok")
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

        win = tk.Toplevel(self)
        win.title(i18n.t("发现新版本 v%s", "Version v%s is available") % r["latest"])
        win.configure(bg=BG)
        win.transient(self)
        win.minsize(560, 420)
        win.rowconfigure(2, weight=1)
        win.columnconfigure(0, weight=1)

        head = ttk.Frame(win, padding=(16, 14, 16, 6))
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text=i18n.t("DroidExtend v%s 可以用了",
                                    "DroidExtend v%s is ready") % r["latest"],
                  style="Head.TLabel").pack(anchor="w")
        mb = (s.get("size") or 0) / 1048576.0
        when = ("  ·  %s" % r["published"]) if r.get("published") else ""
        ttk.Label(head, text=i18n.t("你现在是 v%s%s  ·  安装包 %.1f MB",
                                    "You are on v%s%s  ·  installer %.1f MB")
                  % (APP_VERSION, when, mb), style="Dim.TLabel").pack(anchor="w", pady=(4, 0))

        box = ttk.Frame(win, padding=(16, 0, 16, 0))
        box.grid(row=2, column=0, sticky="nsew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        txt = tk.Text(box, bg="#0f1115", fg="#c8d0da", font=("Microsoft YaHei UI", 9),
                      wrap="word", relief="flat", padx=12, pady=10, height=12)
        txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, command=txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        txt.configure(yscrollcommand=sb.set)
        self._insert_notes(txt, self._notes_for(r))
        txt.configure(state="disabled")

        # 下载位置与「装完删包」放在这一屏，因为用户正是在这一刻才会想到它们。
        opt = ttk.Frame(win, padding=(16, 10, 16, 0))
        opt.grid(row=3, column=0, sticky="ew")
        opt.columnconfigure(1, weight=1)
        ttk.Label(opt, text=i18n.t("下载到", "Download to"),
                  style="Dim.TLabel").grid(row=0, column=0, sticky="w")
        dir_var = tk.StringVar(value=upd.update_dir(self.settings.get("update_dir", "")))
        ttk.Label(opt, textvariable=dir_var, style="Dim.TLabel").grid(
            row=0, column=1, sticky="w", padx=8)
        ttk.Button(opt, text=i18n.t("更改…", "Change…"), width=9,
                   command=lambda: self._pick_update_dir(dir_var)).grid(row=0, column=2)
        keep_var = tk.BooleanVar(value=bool(self.settings.get("delete_installer", True)))
        ttk.Checkbutton(opt, variable=keep_var,
                        text=i18n.t("安装完成后删掉下载的安装包",
                                    "Delete the downloaded installer after installing")
                        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        foot = ttk.Frame(win, padding=(16, 12, 16, 14))
        foot.grid(row=4, column=0, sticky="ew")
        ttk.Label(foot, text=i18n.t(
            "安装包从 GitHub 官方地址下载，安装前会校验大小、SHA256 和文件格式。\n"
            "本程序没有代码签名证书，所以完整性靠这个校验和保证，不靠签名。",
            "The installer comes from the official GitHub release and is checked for "
            "size, SHA256 and file format before anything is run.\n"
            "This project has no code-signing certificate, so integrity rests on that "
            "checksum, not on a signature."),
                  style="Dim.TLabel", justify="left").pack(anchor="w", pady=(0, 10))
        row = ttk.Frame(foot)
        row.pack(fill="x")
        ttk.Button(row, text=i18n.t("以后再说", "Later"),
                   command=win.destroy).pack(side="left")
        ttk.Button(row, text=i18n.t("完整更新日志", "All release notes"),
                   command=self.on_show_changelog).pack(side="left", padx=8)
        ttk.Button(row, text=i18n.t("打开发布页", "Open release page"),
                   command=lambda: self.open_url(r["url"])).pack(side="left")
        ttk.Button(row, text=i18n.t("下载并安装", "Download and install"),
                   style="Go.TButton",
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
        win = tk.Toplevel(self)
        win.title("%s — %s" % (cl.translator(), APP_NAME))
        win.configure(bg=BG)
        win.transient(self)
        win.minsize(560, 460)
        win.rowconfigure(1, weight=1)
        win.columnconfigure(0, weight=1)
        ttk.Label(win, text="%s v%s" % (APP_NAME, APP_VERSION),
                  style="Head.TLabel", padding=(16, 14, 16, 6)).grid(row=0, column=0, sticky="w")
        box = ttk.Frame(win, padding=(16, 0, 16, 0))
        box.grid(row=1, column=0, sticky="nsew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        txt = tk.Text(box, bg="#0f1115", fg="#c8d0da", font=("Microsoft YaHei UI", 9),
                      wrap="word", relief="flat", padx=12, pady=10)
        txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, command=txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        txt.configure(yscrollcommand=sb.set)
        self._insert_notes(txt, cl.all_text())
        txt.configure(state="disabled")
        foot = ttk.Frame(win, padding=(16, 12, 16, 14))
        foot.grid(row=2, column=0, sticky="ew")
        ttk.Button(foot, text=i18n.t("关闭", "Close"),
                   command=win.destroy).pack(side="right")

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

        win = tk.Toplevel(self)
        win.title(i18n.t("正在下载 DroidExtend %s", "Downloading DroidExtend %s") % r["latest"])
        win.configure(bg=BG)
        win.transient(self)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=i18n.t("正在下载 v%s 的安装包…",
                                    "Downloading the v%s installer...") % r["latest"],
                  style="Head.TLabel").pack(anchor="w")
        ttk.Label(body, text=name, style="Dim.TLabel").pack(anchor="w", pady=(3, 12))
        bar = ttk.Progressbar(body, mode="determinate", length=420, maximum=1000)
        bar.pack(fill="x")
        note = ttk.Label(body, text=i18n.t("正在连接…", "Connecting..."), style="Dim.TLabel")
        note.pack(anchor="w", pady=(8, 12))

        def do_cancel():
            self._dl_cancel = True
            cancel.configure(state="disabled")
            note.configure(text=i18n.t("正在取消…", "Cancelling..."))

        cancel = ttk.Button(body, text=i18n.t("取消", "Cancel"), command=do_cancel)
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
            delete_installer=delete_installer)
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
        self._quit_now()

    def open_path_dir(self, path):
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception as e:
            self.log("打不开目录：%s" % e, "err")

    # ------------------------------------------------------------ 托盘 (#3)
    def _tray_choice(self):
        """用户动了「关闭窗口时缩到托盘」这个勾。"""
        self._save_settings()
        if self.tray_var.get():
            self._start_tray()
        else:
            self._stop_tray()

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
            self.tray_var.set(False)
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
                cmd = auto.write(["--minimized"] if self.tray_var.get() else [])
                self.log("已设置开机自动启动：%s" % cmd)
            elif auto.enabled():
                auto.remove()
                self.log("已取消开机自动启动。")
        except Exception as e:
            self.log("设置开机自启动失败：%s" % e, "err")

    def _anim_choice(self):
        """用户动了「界面动画」这个勾。立刻生效，不用重启。"""
        self.settings[anim.KEY] = bool(self.anim_var.get())
        self._save_settings()
        if self.anim_var.get():
            self.log("界面动画已打开。")
        else:
            self.log("界面动画已关闭。")

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
        try:
            # 没在投屏也要走一遍：上次异常退出可能把虚拟屏留在了桌面上。
            if self.session.running or self.session.server or self.session._display_applied:
                self.session.stop()
        except Exception as e:
            self.log("退出时停止会话失败：%s" % e, "err")
        try:
            if self.session.adb is not None:
                self.session.adb.reverse_remove_all(None)
                self.log("已清理 adb 端口转发。")
        except Exception:
            pass
        try:
            applog.close()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


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
