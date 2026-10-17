"""更新日志，中英各一份，随程序一起打包。

为什么不用 GitHub 的 release notes？因为那份是英文优先、而且离线时看不到。
更新弹窗是用户唯一会认真读的一段字，所以它得在本地、得是用户的语言、得在
断网时也拿得到；release notes 里如果有更详细的内容，弹窗会接在后面显示。

每一版加一条：``{"v": "1.6.0", "date": "2025-..", "zh": [...], "en": [...]}``。
行顺序就是显示顺序，中英两条列表必须一一对应。
"""

import i18n

# 新版本写在最前面。
ENTRIES = [
    {
        "v": "1.6.4",
        "date": "2026-10-17",
        "zh": [
            "新增「不用时」选项，默认是彻底关闭虚拟屏：停止投屏后会把驱动的设备节点禁用掉，"
            "开机不会有这块屏，远程桌面类软件也看不到它。代价是开始和停止时各弹一次管理员授权。",
            "也可以改成「只移出桌面」：不碰驱动、不弹授权，但屏还留在系统里。",
            "工具菜单新增「彻底关闭虚拟屏（要授权一次）」，现在就能关掉，不用等下次停止投屏。",
            "顺手修了一个一直没生效的检查：以前判断虚拟屏的设备节点在不在用的是实例 ID 前缀，"
            "而那台设备的实例 ID 是 ROOT\\DISPLAY\\0001，所以它从来没找到过东西——现在按硬件 ID 找。",
            "还修了一个更严重的：程序内更新从来没能真正装上去——下载和校验都正常，点「是」之后"
            "却什么都没发生。原因是启动更新脚本用了 DETACHED_PROCESS，那个旗子下子进程退出码 0 "
            "却一行都不执行；现在换成 PowerShell 脚本加 CREATE_NO_WINDOW，实测 1.5 秒装好。",
        ],
        "en": [
            "New \"when not in use\" choice, defaulting to switching the virtual display off for real: "
            "stopping the stream disables the driver's device node, so the screen is not there at boot "
            "and remote-desktop software cannot see it either. It costs one administrator approval when "
            "you start and one when you stop.",
            "It can be set to \"just take it off the desktop\" instead: the driver is left alone and "
            "nothing asks for approval, but the screen stays in the system.",
            "New menu item \"Shut the virtual display down for real (asks once)\", so it can be done right "
            "now instead of waiting for the next stream to stop.",
            "Fixed a check that had never worked: it looked for the virtual display's device node by "
            "instance-id prefix, but that device's instance id is ROOT\\DISPLAY\\0001, so it never found "
            "anything. It matches on the hardware id now.",
            "And a worse one: the in-app update never actually installed. The download and the checks "
            "were fine, and after you said yes nothing happened, because the update script was launched "
            "with DETACHED_PROCESS -- under which the child exits 0 without running a single line. It is "
            "a PowerShell script launched with CREATE_NO_WINDOW now, and a measured install takes 1.5 s.",
        ],
    },
    {
        "v": "1.6.3",
        "date": "2026-10-16",
        "zh": [
            "分辨率下拉现在列的就是虚拟显示器驱动真有的那些模式——以前 22 个写死的选项里有 11 个点下去什么都不会发生。",
            "驱动给不出的分辨率会明说，不再白等 9 秒反复重试，也不会为了一个不存在的模式把画面重启一遍。",
            "驱动模式表还有空位时，缺的那个尺寸会被写进 vdd_settings.xml，并告诉你要重装驱动（或重启）之后才能用。",
            "编码器菜单改成逐个实测：编译进去了、但这台机器上跑不起来的（qsv 系列、av1_amf）不再排在菜单里假装能用。",
        ],
        "en": [
            "The resolution menu now lists exactly the modes the virtual display driver has -- eleven of the "
            "twenty-two hard-coded sizes it used to offer had nothing behind them and did nothing at all.",
            "A size the driver cannot produce is reported as such, instead of nine seconds of retries and a "
            "restarted picture for a mode that never existed.",
            "When the driver's mode table still has room, the missing size is written into vdd_settings.xml, "
            "and you are told to reinstall the driver (or reboot) before it can be used.",
            "The encoder menu is built by test-encoding each one: entries that are compiled in but cannot run "
            "here (the qsv family and av1_amf) are no longer offered as if they worked.",
        ],
    },
    {
        "v": "1.6.2",
        "date": "2026-10-16",
        "zh": [
            "更新（以及安装程序本身）会装回程序现在所在的目录，不再每次都问你要装到哪里。",
            "Windows 的使用条款窗口把「同意并继续」放在了正文最后一行，窗口没被拉大也能滚下去找到它，不会再被切掉。",
            "手机端连点两下现在真的是双击：第二次点击会对齐到第一次的位置，不然 Windows 只会把它当成两次单击，文件也就打不开。",
            "手机端设置里现在把六种手势写全了。",
        ],
        "en": [
            "Updating -- and the installer itself -- now lands in the folder the program is already in, instead of asking where to put it every time.",
            "The terms window on Windows puts \"Agree and continue\" at the end of the text, where it can be scrolled to even in a window that was never stretched, instead of being cut off.",
            "Two taps on the phone are now a real double click: the second one is lined up with the first, which is the only way Windows will call it one. Without that it read as two unrelated clicks and the file never opened.",
            "The phone's settings panel now spells out all six gestures.",
        ],
    },
    {
        "v": "1.6.1",
        "date": "2026-10-16",
        "zh": [
            "手机端新增触摸死区：手指落下时那一点点抖动不再被当成拖动，点一下就是点一下。",
            "手机端长按 0.5 秒等于鼠标右键，和常见远程控制软件的触控习惯一致。",
            "手机端拖动改为按画面帧率节流（约每秒 60 次位置），拖动更跟手，也不再越拖越滞后。",
            "拖动时只发送最新位置，中间那些已经过时的位置不再逐个发出去，电脑端也同样只注入最新的一个。",
        ],
        "en": [
            "New on the phone: a touch dead zone, so the tiny wobble of a finger landing is no longer treated as a drag.",
            "Hold on the phone for half a second for a right click, the way every other remote-desktop client does it.",
            "Dragging on the phone is now paced to the frame rate (about 60 positions a second): it tracks the finger and no longer falls further behind the longer you drag.",
            "Only the newest position is sent and injected while dragging; the stale ones in between are dropped on both ends.",
        ],
    },
    {
        "v": "1.6.0",
        "date": "2026-10-16",
        "zh": [
            "首次启动会显示使用条款与隐私说明，同意之后才能继续使用。",
            "界面、安装程序和更新日志都支持中英文，默认跟随系统语言，也可以手动切换。",
            "新增托盘图标：关掉窗口只是缩到托盘，托盘菜单里可以显示主窗口或彻底退出。",
            "新增「开机自动启动」开关，开机拉起时直接缩在托盘里，不弹窗口。",
            "彻底退出时会摘掉虚拟屏、清掉 adb 端口转发，不再留下重启后才消失的幽灵显示器。",
            "手机端新增后台保活引导：关掉省电限制、打开自启动管理、在最近任务里锁定本应用。",
            "手机熄屏或被系统关掉时，电脑端会立刻收到提示，不再是画面无声无息地停住。",
            "修复触控坐标偏移：显示器位置或分辨率变化后，触摸坐标会自动跟上。",
            "修复关掉前台窗口后触控失灵：手指卡在按下状态时会自动松手，不再需要重启。",
            "修复垂直滚轮从来没生效过（触摸协议里纵向滚动量被截成了 0）。",
            "修复别的投屏/串流软件（网易 UU 远程等）的虚拟屏被误认成本程序的虚拟屏。",
            "修复「开机后没启动本程序，虚拟屏却自己出现」：摘屏时会同步清理系统的显示配置。",
            "虚拟屏的位置不再只有上下左右四个方向，可以直接填写坐标。",
            "投屏过程中改分辨率或刷新率会立刻生效，不用再停止投屏重来。",
            "视频编码支持 H.264 / H.265 / AV1；能不能用改成真实试跑一帧来判断，选中的编码器不可用时会自动降级。",
            "新增连接诊断（端口占用、USB 驱动缺失）和 USB 驱动安装引导。",
            "说明为什么投屏时显卡占用偏高，以及界面动画会跟随系统设置、低性能设备自动降级。",
            "修复设置文件带 BOM 时全部设置被静默丢弃（用记事本改过 settings.json 就会中招）。",
        ],
        "en": [
            "A terms and privacy notice is shown on first launch; nothing runs until you agree.",
            "The app, the installer and this changelog all speak Chinese and English, following the system by default.",
            "New tray icon: closing the window just hides it, and the tray menu can show it again or quit for real.",
            "New \"start with Windows\" switch; when Windows starts it, the app tucks itself into the tray.",
            "Quitting now detaches the virtual display and clears the adb port forwards, so no ghost monitor survives a reboot.",
            "The phone app now walks you through staying alive: battery whitelist, auto-start manager, lock it in recents.",
            "If the phone screen turns off or the app is killed, the PC is told straight away instead of just freezing.",
            "Fixed touch coordinates drifting after the display is moved or resized.",
            "Fixed touch dying after the focused window closes: a stuck finger is now released automatically.",
            "Fixed vertical scrolling, which never worked (the protocol truncated the scroll delta to zero).",
            "Fixed other streaming tools' virtual displays (NetEase UU Remote and friends) being mistaken for ours.",
            "Fixed the virtual display reappearing after a reboot even when the app was never started.",
            "The virtual display can now be placed at any coordinates, not just left/right/above/below.",
            "Changing resolution or refresh rate while streaming takes effect immediately, no restart needed.",
            "H.264, H.265 and AV1 are supported; encoders are tested by actually encoding a frame, with automatic fallback.",
            "New connection diagnostics (port conflicts, missing USB driver) and a driver install shortcut.",
            "Explained why GPU usage looks high while streaming; animations now follow the system setting and scale down on slow devices.",
            "Fixed every setting being silently discarded when settings.json has a byte-order mark (editing it in Notepad is enough).",
        ],
    },
    {
        "v": "1.5.1",
        "date": "",
        "zh": [
            "修复虚拟屏用着用着凭空消失：显示驱动的模式表是分辨率与刷新率的乘积，"
            "选项给多了会把缓冲区撑爆，现在只保留有意义的组合。",
            "修复安装程序里写死的开发机路径，它让 1.4.1 之前的版本在别人电脑上装不上驱动。",
        ],
        "en": [
            "Fixed the virtual display vanishing mid-session: the driver's mode table is the product of "
            "resolutions and refresh rates, and too many combinations overflowed its buffer.",
            "Fixed a developer-machine path hard-coded in the installer, which broke driver setup on other people's PCs.",
        ],
    },
    {
        "v": "1.5.0",
        "date": "",
        "zh": [
            "更新改为在程序里直接完成：下载、校验、静默安装、自动重开，不用再去浏览器。",
            "安装前会校验文件大小、SHA256 和 PE 文件头，任何一条不过就不装。",
        ],
        "en": [
            "Updates now happen inside the app: download, verify, silent install, restart. No browser needed.",
            "Before anything is run, the size, the SHA256 and the PE header are all checked.",
        ],
    },
    {
        "v": "1.4.0",
        "date": "",
        "zh": [
            "画面卡死后会自己恢复，不再需要重启程序。",
            "设置与日志统一放进同一个数据目录，可以在界面里直接打开。",
            "手机端界面重画：浅色小字，状态浮窗不再挡着画面。",
        ],
        "en": [
            "A frozen picture now recovers on its own instead of needing a restart.",
            "Settings and logs live in one data folder that the app can open for you.",
            "The phone UI was redrawn: lighter text, and a status overlay that stays out of the way.",
        ],
    },
    {
        "v": "1.3.0",
        "date": "",
        "zh": [
            "手机端有了自己的界面：状态浮窗、设置面板、可选的文件日志。",
            "虚拟屏可以跟随手机屏幕分辨率撑满。",
            "认屏时间从 9 秒缩短到 1 秒左右。",
        ],
        "en": [
            "The phone app got a real UI: status overlay, settings sheet, optional file logging.",
            "The virtual display can follow the phone's own resolution.",
            "Picking the display went from about 9 seconds to about 1.",
        ],
    },
    {
        "v": "1.2.0",
        "date": "",
        "zh": [
            "设置会记住了：分辨率、帧率、码率、编码器、位置。",
            "新增检查更新、程序内使用说明，界面重新整理。",
        ],
        "en": [
            "Settings are remembered: resolution, frame rate, bitrate, encoder, position.",
            "Added update checking and in-app help; the window was reorganised.",
        ],
    },
]


def versions():
    """所有有记录的版本号，最新的在前。"""
    return [e["v"] for e in ENTRIES]


def entry(version):
    """按版本号找一条记录，找不到返回 None。"""
    v = str(version or "").strip().lstrip("vV")
    for e in ENTRIES:
        if e["v"] == v:
            return e
    return None


def lines(version, lang=None):
    """-> 该版本在当前语言下的条目列表；没有记录就是空列表。"""
    e = entry(version)
    if not e:
        return []
    return list(e.get(i18n.lang() if lang is None else lang) or [])


def text(version, lang=None):
    """-> 该版本的条目拼成一段文本，没有记录就是空串。"""
    return "\n".join(lines(version, lang))


def all_text(lang=None, limit=0):
    """-> 所有版本的完整更新日志；limit > 0 时只取最近这么多版。"""
    lang = i18n.lang() if lang is None else lang
    out = []
    src = ENTRIES[:limit] if limit else ENTRIES
    for e in src:
        head = "v%s" % e["v"]
        if e.get("date"):
            head += "  (%s)" % e["date"]
        out.append(head)
        for line in (e.get(lang) or []):
            out.append("  • " + line)
        out.append("")
    return "\n".join(out).strip()


def translator():
    """给「更新日志」窗口用的标题。"""
    return i18n.t("更新日志", "Release notes")
