"""#1: the terms and privacy notice the user has to accept before first use.

This lives in its own module because it is the one block of text in the program
that is meant to be read instead of clicked past, and because the version string
here -- not a boolean -- is what decides whether it is shown again after an
update changes it.

The body is built from a list of (tag, line) pairs so that both languages
provably say the same thing: same headings, same order, same count.
"""

import tkinter as tk
from tkinter import ttk

from i18n import t
import paths

# Bump this whenever the terms change in a way people should see again.  What
# gets stored in settings.json is this string, so a bump re-asks everyone.
VERSION = "1"

BG = "#14161a"


def needs_accept(settings):
    """True when the terms have not been accepted at the current version."""
    return str(settings.get("privacy_accepted", "") or "") != VERSION


def accept(settings):
    """Record the acceptance.  The caller is responsible for saving."""
    settings["privacy_accepted"] = VERSION
    return settings


def _lines():
    """(tag, text) pairs; tag is 'h' for a heading, 'p' for body, 'w' for the last line."""
    where = paths.data_dir()
    return [
        ("h", t("一、这个程序做什么", "1. What this program does")),
        ("p", t(
            "DroidExtend 用一根 USB 数据线，把安卓手机或平板变成 Windows 的第二块显示器，"
            "并让手机的触摸屏反过来控制 Windows。它全程只在你自己的电脑与手机之间工作，"
            "不连接任何服务器。",
            "DroidExtend turns an Android phone or tablet into a second Windows monitor "
            "over a single USB cable, and lets the phone's touchscreen drive Windows in "
            "return. It works only between your own PC and your own phone, and it never "
            "connects to any server.")),
        ("h", t("二、它不收集任何东西", "2. It collects nothing")),
        ("p", t("· 不收集、不上传、不分享任何个人信息、使用数据或屏幕内容。",
                "· No personal information, usage data or screen content is collected, "
                "uploaded or shared.")),
        ("p", t("· 画面和触摸数据只经 USB 线在电脑与手机之间传输，不出这台机器。",
                "· Video and touch data travel only between the PC and the phone over the "
                "USB cable. They do not leave the machine.")),
        ("p", t("· 手机端只在手机本地保存自己的偏好设置。",
                "· The phone app stores its preferences locally on the phone only.")),
        ("p", t("· 电脑端唯一写盘的是设置文件和日志，位置：%s" % where,
                "· The only files written on the PC are the settings file and the log, in: "
                "%s" % where)),
        ("h", t("三、手机端需要的能力", "3. What the phone app needs")),
        ("p", t("· 录屏（MediaProjection）：把屏幕画面编码后发给电脑。系统会弹窗向你确认。",
                "· Screen capture (MediaProjection): encodes the screen and sends it to the "
                "PC. Android asks you to confirm this.")),
        ("p", t("· 输入注入：把你在手机上的点击、拖动、滚动送到电脑。这是本程序的核心功能。",
                "· Input injection: sends your taps, drags and scrolls to the PC. This is the "
                "core feature, not a side effect.")),
        ("p", t("· 前台服务：在你使用期间保持连接，不被系统回收。",
                "· A foreground service: keeps the connection alive while you use it.")),
        ("h", t("四、风险与免责", "4. Risk and disclaimer")),
        ("p", t("· 本程序按「现状」提供，不附带任何明示或暗示的担保。",
                "· This program is provided \"as is\", without warranty of any kind, express "
                "or implied.")),
        ("p", t("· 虚拟显示器会改动系统显示配置。正常退出时会尽量恢复；异常退出"
                "（断电、强制结束进程）可能留下多余的虚拟屏，用「工具 → 清理残留的虚拟屏」处理。",
                "· The virtual display changes your display configuration. A normal exit "
                "restores it; an abnormal one (power loss, force-kill) can leave an extra "
                "virtual screen behind -- use Tools -> Clean up leftover virtual screens.")),
        ("p", t("· 本程序会安装或卸载显示驱动、修改显示模式，请自行确认这些操作在你的设备上是"
                "被允许的。",
                "· The program installs or removes a display driver and changes display "
                "modes. Make sure that is allowed on your device.")),
        ("p", t("· 因使用或无法使用本程序造成的任何损失，作者不承担责任。",
                "· The author is not liable for any loss caused by using, or being unable to "
                "use, this program.")),
        ("p", t("· 请只在你本人拥有、或已获明确授权的设备上使用。未经许可远程控制他人设备"
                "是违法的。",
                "· Use it only on devices you own or are explicitly authorised to use. "
                "Controlling someone else's device without permission is illegal.")),
        ("h", t("五、许可", "5. Licence")),
        ("p", t("本程序以 MIT 许可开源。第三方组件许可见安装目录下的「第三方组件许可.txt」。",
                "This program is open source under the MIT licence. Third-party notices are "
                "in 第三方组件许可.txt next to the program.")),
        ("w", t("点「同意并继续」表示你已阅读、理解并接受以上全部内容。",
                "Choosing \"Agree and continue\" means you have read, understood and accept "
                "all of the above.")),
    ]


def _height(win):
    """As tall as we would like the window, but never taller than the screen."""
    want = 620
    try:
        room = win.winfo_screenheight() - 140
        if room > 0:
            want = max(360, min(want, int(room)))
    except Exception:
        pass
    return want


def ask(parent):
    """Show the terms modally.  True only when the user accepts.

    Closing the window counts as refusing: there is no "later" answer, because
    later would mean the program runs without them.
    """
    answer = {"ok": False}

    win = tk.Toplevel(parent)
    win.title(t("使用条款与隐私说明", "Terms of use and privacy"))
    win.configure(bg=BG)
    win.transient(parent)
    win.minsize(560, 400)
    win.geometry("760x%d" % _height(win))

    head = ttk.Frame(win, padding=(18, 16, 18, 8))
    head.pack(fill="x")
    ttk.Label(head, text=t("使用条款与隐私说明", "Terms of use and privacy"),
              style="Head.TLabel").pack(anchor="w")
    ttk.Label(head, text=t("第一次使用前请读一遍。同意后不会再问。",
                           "Please read this once. You will not be asked again."),
              style="Dim.TLabel").pack(anchor="w", pady=(4, 0))

    box = ttk.Frame(win, padding=(18, 0, 18, 16))
    box.pack(fill="both", expand=True)
    txt = tk.Text(box, bg="#0f1115", fg="#c8d0da",
                  font=("Microsoft YaHei UI", 9), wrap="word",
                  relief="flat", padx=14, pady=12)
    txt.pack(side="left", fill="both", expand=True)
    sb = ttk.Scrollbar(box, command=txt.yview)
    sb.pack(side="right", fill="y")
    txt.configure(yscrollcommand=sb.set)
    txt.tag_configure("h", foreground="#8ab4f8",
                      font=("Microsoft YaHei UI", 10, "bold"),
                      spacing1=12, spacing3=4)
    txt.tag_configure("p", spacing3=6)
    txt.tag_configure("w", foreground="#ffb86b", spacing1=12)
    for tag, line in _lines():
        txt.insert("end", line + "\n", tag)

    def yes():
        answer["ok"] = True
        win.destroy()

    def no():
        answer["ok"] = False
        win.destroy()

    # The buttons go at the end of the agreement, inside the scrolling area,
    # rather than pinned to the bottom of the window.  A footer cannot be
    # scrolled, so on a screen too short for the window it ends up below the
    # bottom edge with no way to reach it -- which is exactly the report.
    # Inside the text they are always reachable, and they end up after the
    # words they are asking you to accept, which is where they belong.
    btns = ttk.Frame(txt, width=420, height=44)
    btns.pack_propagate(False)          # width is set from the text below
    ttk.Button(btns, text=t("不同意，退出", "Decline and quit"),
               command=no).pack(side="left")
    ttk.Button(btns, text=t("同意并继续", "Agree and continue"),
               style="Go.TButton", command=yes).pack(side="right")
    txt.insert("end", "\n")
    txt.window_create("end", window=btns, pady=8)
    txt.insert("end", "\n")
    txt.configure(state="disabled")

    def fit(event=None):
        """The row spans the text, and the text is only measurable once laid out."""
        try:
            btns.configure(width=max(txt.winfo_width() - 34, 260))
        except Exception:
            pass

    txt.bind("<Configure>", fit)
    win.update_idletasks()
    fit()

    win.protocol("WM_DELETE_WINDOW", no)
    win.grab_set()
    win.focus_set()
    parent.wait_window(win)
    return answer["ok"]
