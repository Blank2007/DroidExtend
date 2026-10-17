"""The text behind Help -> Manual, kept in one place.

Plain text on purpose: it is shown in a Tk text widget, and the same string is
what we paste into the About window when somebody asks "how do I even use this".

Two complete manuals rather than one text run through a translator: a manual is
prose, and prose translated sentence-by-sentence reads like prose translated
sentence-by-sentence.  Pick with text().
"""
import i18n
from version import APP_VERSION, EMAILS, REPO_URL

TEXT_ZH = """\
DroidExtend 使用说明   v{ver}
----------------------------------------------------------

【一、这是什么】
  用一根 USB 数据线，把安卓手机/平板变成 Windows 的第二块显示器。
  是真扩展桌面，不是投屏镜像： Windows 会多出一块屏，窗口可以拖过去，
  鼠标可以移进去，手机上点一下就是在这块屏上点一下。

  延迟实测 5~11 ms，画面走 USB 线，不经过 WiFi、不经过路由器。

【二、手机要先准备两件事】
  1. 打开开发者选项
     设置 -> 关于手机 -> 连续点 7 次「版本号」
  2. 打开 USB 调试（有的机型还要一起打开「USB 安装」）
     设置 -> 系统 -> 开发者选项 -> USB 调试
  3. 插上数据线后手机会弹「允许 USB 调试吗？」-> 点「允许」
     （勾上「一律允许」以后就不用每次点了）

【三、第一次使用】
  第 1 步  安装虚拟显示器驱动
           点左边的「安装虚拟显示器驱动（需要管理员）」，
           会弹 Windows 的 UAC 授权窗口，点「是」。
           只需要装一次，装完这块状态会变成「已经就绪」。

  第 2 步  用数据线把手机连到电脑
           插好后点「刷新」，左边「安卓设备」应该显示机型和序列号。
           如果显示「已连接但未授权」，看手机屏幕，点「允许 USB 调试」。

  第 3 步  选参数
           右边「画质与帧率」里选分辨率、刷新率、帧率、码率上限。
           不知道选什么就用默认值： 1920x1080 / 60Hz / 60fps / 40 Mbps。

  第 4 步  点「▶ 开始投屏」
           第一次会自动把手机端 APK 装上去并打开（约十几秒）。
           手机上出现画面就成功了。

  第 5 步  想停止就点「■ 停止」
           虚拟显示器会从系统里消失，桌面恢复原样，手机上的画面也停掉。

【四、触摸怎么用】
  手机上直接操作，就等于在这块虚拟屏上操作鼠标：

    · 单指点一下            = 鼠标左键单击
    · 单指按住拖动          = 按住左键拖动（选文字、拖窗口）
    · 单指长按半秒          = 鼠标右键（弹出右键菜单）
    · 双指上下/左右滑动     = 鼠标滚轮 / 横向滚动
    · 双指快速轻点一下      = 鼠标右键（弹出右键菜单）

  点一下就是点一下：手指落下时那几个像素的抖动不算拖动，要越过触摸阈值才算。
  拖动时位置最快每 16 毫秒更新一条（正好是一帧），中间过时的位置会被丢掉，
  但按下、抬起、右键、滚动这些事件一个都不会少。

  注意：触摸注入的是「那块虚拟屏」上的坐标，鼠标会真的跑到那块屏去，
  所以主屏上的鼠标位置会跟着跳，这是正常的。
  把手机横过来，画面会自动跟着横。

【五、参数怎么选】
  分辨率      虚拟屏的大小。和手机屏幕比例一致最清楚，1920x1080 最通用。
              选「自定义」可以手动填宽高。
  刷新率      虚拟屏的刷新率，60 就够；越高越吃显卡。
  帧率 fps    每秒编码多少帧。桌面办公 30~60，看视频 60 以上。
  码率上限    画面数据量的上限（Mbps）。40 适合绝大多数 USB 3 口；
              不确定就先点「测一下数据线速度」，程序会告诉你建议值。
  码率控制    VBR 质量优先：静止桌面省带宽（推荐）
              CBR 恒定码率：运动画面更稳
  画质 Q      越小画质越好、码率越高。18 约等于视觉无损，默认 20。
  编码器      h264_nvenc 用显卡编码（推荐），libx264 用 CPU。
  预设        编码速度档位。p4 / veryfast 是常用的平衡点。
  采集        ddagrab 走桌面复制（最快），gdigrab 走 GDI（兼容性最好）。
              默认「自动」：优先 ddagrab，认不出该抓哪块屏时自动退回 gdigrab。
  屏幕位置    虚拟屏放在主屏的哪一边。放右边就是「往右拖窗口」。
  显示鼠标指针  把鼠标画进画面里。
  每次重新安装 APK  手机上已经装过就可以取消勾选，开始会快很多。

【六、常见问题】
  Q: 手机上有画面，但只有壁纸和一条任务栏，看着和主屏一模一样？
  A: 这是正常的。虚拟屏默认没有窗口，Windows 的壁纸和任务栏会画上去，
     所以一眼看去和主屏很像。往右拖一个窗口过去（或者把鼠标移到右边那块屏
     再打开一个窗口），就能看见区别。日志里「拓展屏就绪」那行会写出这块屏
     在桌面坐标里的位置。

  Q: 手机上一片黑 / 一直是「等待手机连接…」
  A: 1) 确认手机屏幕是亮的、已经解锁（锁屏时画面会被截成黑的）
     2) 拔掉数据线重插，再点一次「开始投屏」
     3) 看日志里有没有 adb 报错；「刷新」看看设备还在不在
     4) 手机上如果弹过「允许 USB 调试」，重新点一次「开始投屏」

  Q: 画面卡、延迟大
  A: 打开手机上的「状态浮窗」，它会显示 fps 和 latency，先看是哪一个不对：
     · fps 低、latency 正常 -> 数据线带宽不够。点「测一下数据线速度」，
       码率上限不要超过它给的建议值；换一根好一点的 USB 线，插 USB 3.0 口。
     · fps 正常、latency 高 -> 编码或解码在排队。换个编码器（h264_nvenc 最快），
       或把帧率降到 30。
     · 两个都正常但还是顿 -> 手机在省电降频。降帧率 / 降分辨率，
       或在手机设置里对本程序关掉省电限制。
     · 「采集」保持「自动」或 ddagrab。

  Q: 显卡占用很高，是不是有问题？
  A: 正常，而且和虚拟屏直接有关。虚拟屏不是真屏幕，但 Windows 的桌面合成器
     会把它当成一块真屏去合成——屏幕越大、刷新率越高，每帧要合成的像素越多。
     采集（ddagrab）和编码也都在显卡上跑。所以任务管理器里看到百分之几到
     十几的 GPU 占用是设计使然。因为虚拟屏的缘故，显卡占用可能比你平时高。
     · 想让占用降下来：优先降分辨率，其次降刷新率（两者是乘算的）。
     · 担心影响游戏：选 h264_nvenc / hevc_nvenc / av1_nvenc 这类硬件编码器，
       它们用显卡上的独立编码单元，不和 3D 渲染抢算力；别选 libx264（纯 CPU）。
     · 占用真的接近打满时虚拟屏会掉帧，这时候只能降像素量。

  Q: 改了分辨率 / 刷新率 / 位置，好像没生效？
  A: 现在改这些会立刻应用到正在运行的虚拟屏，不用停掉重开。程序最多重试 4 次，
     并且以「实际生效的模式」为准判断成功——Windows 在切换显示模式前后会短暂
     锁住显示配置，前两次失败是正常的。如果一直不生效，检查「跟随手机分辨率」
     是不是勾着，那个选项会覆盖你的选择。
     另外，分辨率下拉里现在列的**就是虚拟显示器驱动真有的那些模式**（驱动从
     vdd_settings.xml 里的每一行 <resolution> 建模式表，表外的尺寸 Windows 一律
     拒掉，以前点了没反应就是这个原因）。如果某个尺寸不在列表里，就是驱动给不
     出来；日志会说清楚。刷新率同理。

  Q: 停止之后系统里还留着一块屏幕？
  A: 正常情况下「停止」或拔线都会把虚拟屏摘掉。如果电脑上还留着，
     再点一次「开始投屏」然后「停止」即可；程序在启动失败时也会自动摘屏。

  Q: 不投屏的时候，能不能让这块屏彻底不出现？
  A: 能，而且默认就是这样。设置里「不用时」有两档：
     · 彻底关闭（默认）—— 停止投屏后会把驱动的设备节点禁用掉。开机不会有这块屏，
       ToDesk / 向日葵这类远程软件也枚举不到它，重启之后它仍然是关着的；
       代价是开始和停止投屏时各弹一次管理员授权。
     · 只移出桌面 —— 不碰驱动、不弹授权，屏还在系统里，只是不在桌面上。
     想现在就关掉（而不是等下次停止投屏），点工具菜单的「彻底关闭虚拟屏（要授权一次）」。
     注意不能靠把驱动设置里的 <count> 写成 0 来实现：驱动把 0 当成非法值，照样建一块屏。

  Q: 电脑上多出来的那块屏找不到鼠标？
  A: 虚拟屏是按「屏幕位置」贴着主屏放的。放在右边时，鼠标一直往右移就过去了。
     也可以按 Win+P 看当前是「扩展」还是「仅电脑屏幕」。

【七、设置、更新与文件位置】
  · 你改过的参数会自动记住，下次打开软件还是上次的配置。
  · 设置文件： %USERPROFILE%\\.droidextend\\settings.json
    （菜单 文件 -> 打开配置目录 可以直接打开）
  · 菜单 工具 -> 检查更新 会去 GitHub 上查有没有新版本。
  · 程序目录里的 README.md 有更完整的说明；开发日志.md 记录了它是怎么做出来的。

【八、它用到的别人的东西（在此致谢）】
  · Virtual Display Driver (VDD)  —— 虚拟显示器驱动，MIT 许可
    https://github.com/VirtualDrivers/Virtual-Display-Driver
  · nefcon  —— 安装/卸载设备驱动的小工具，MIT 许可
    https://github.com/nefarius/nefcon
  · FFmpeg  —— 采集与硬件编码（GPL 构建，独立进程调用）
    https://ffmpeg.org/  本机用的是 gyan.dev 的 6.0-essentials 构建
  · Android SDK Platform-Tools (adb)  —— Apache License 2.0
  · R8 / D8 (Android SDK build-tools)  —— BSD 3-Clause
  · Python / Tcl-Tk / PyInstaller  —— 见各自的许可文件
  · 安装包由 Inno Setup 生成；图标是用脚本画的，没有用现成素材。

【九、关于这个项目】
  代码作者： AI 编码代理（DeepSeek Harness / deepseek-flash）
  也就是说，这个软件从第一行代码到安装包，都是 AI 写的；
  人类负责提需求、给方向和验收。
  项目所有者： {author}
  仓库： {repo}
  问题反馈： {issues}
  邮箱： {mails}
  如果发现有侵权或需要补充的出处，请联系上面任一邮箱，我会在下一版写明。
"""

TEXT_EN = """\
DroidExtend manual   v{ver}
----------------------------------------------------------

[1] What this is
  One USB cable turns an Android phone or tablet into a second Windows monitor.
  A real extended desktop, not a mirror: Windows gains another screen, you can
  drag windows onto it, move the mouse into it, and a tap on the phone is a
  click on that screen.

  Measured latency is 5-11 ms.  The picture travels over the USB cable -- never
  over WiFi, never through a router.

[2] Two things to set up on the phone first
  1. Turn on developer options
     Settings -> About phone -> tap "Build number" seven times
  2. Turn on USB debugging (some phones also want "Install via USB")
     Settings -> System -> Developer options -> USB debugging
  3. When you plug the cable in the phone asks "Allow USB debugging?" -> Allow
     (tick "Always allow" so it stops asking every time)

[3] First run
  Step 1  Install the virtual display driver
          Press "Install virtual display driver (needs admin)" on the left and
          accept Windows' UAC prompt.  This is a one-time install; the status
          line under it turns into "ready" when it is done.

  Step 2  Connect the phone with the USB cable
          Press "Refresh"; "Android device" on the left should show the model
          and serial number.  If it says "connected but not authorised", look at
          the phone and allow USB debugging.

  Step 3  Choose the settings
          Under "Quality and frame rate" on the right pick resolution, refresh
          rate, fps and bitrate ceiling.  If you have no opinion, keep the
          defaults: 1920x1080 / 60Hz / 60fps / 40 Mbps.

  Step 4  Press "Start"
          The first time it installs and launches the phone app by itself (about
          ten seconds).  You are done when the picture appears on the phone.

  Step 5  Press "Stop" when you want to stop
          The virtual display disappears from Windows, the desktop goes back to
          normal and the phone stops drawing.

[4] How touch works
  Using the phone directly is the same as using a mouse on that virtual screen:

    . one finger tap            = left click
    . one finger drag           = hold the left button and drag (select text,
                                  move windows)
    . one finger, hold 1/2 s    = right click
    . two finger swipe          = wheel / horizontal scroll
    . two finger quick tap      = right click

  A tap is a tap: the few pixels a finger wobbles as it lands do not count as a
  drag -- you have to clear the touch threshold first.  While dragging, the
  position updates at most once every 16 ms (exactly one frame), and the stale
  positions in between are dropped; but not one event is ever lost.

  Note: touch is injected at coordinates on that virtual screen, so the real
  cursor does move over there and the pointer leaves your main screen.  That is
  normal.  Turn the phone sideways and the picture follows.

[5] How to choose the settings
  Resolution     Size of the virtual screen.  Matching the phone's aspect ratio
                 is sharpest; 1920x1080 is the safest general choice.  Pick
                 "Custom" to type a width and height yourself.
  Refresh rate   Refresh rate of the virtual screen.  60 is plenty; higher costs
                 GPU time.
  fps            Frames encoded per second.  30-60 for desktop work, 60+ for
                 video.
  Bitrate        Ceiling on the picture's data rate (Mbps).  40 suits almost
                 every USB 3 port; if unsure press "Measure cable speed" and use
                 what it suggests.
  Rate control   VBR, quality first: an idle desktop costs less bandwidth
                 (recommended).  CBR, constant bitrate: steadier under motion.
  Quality Q      Lower looks better and costs more bitrate.  18 is visually
                 lossless; the default is 20.
  Encoder        h264_nvenc encodes on the GPU (recommended), libx264 on the CPU.
  Preset         Encoder speed step.  p4 / veryfast are the usual balance.
  Capture        ddagrab uses Desktop Duplication (fastest), gdigrab uses GDI
                 (most compatible).  "Auto" prefers ddagrab and falls back to
                 gdigrab when it cannot tell which screen to grab.
  Position       Which side of the main screen the virtual one sits on.  "Right"
                 means you drag windows to the right.
  Draw pointer   Paints the mouse cursor into the picture.
  Reinstall APK  Untick this once the phone already has the app; starting gets
                 much faster.

[6] Common questions
  Q: The phone shows a picture, but it is only wallpaper and a taskbar, and it
     looks exactly like my main screen?
  A: That is expected.  A fresh virtual screen has no windows on it, so Windows
     paints the wallpaper and a taskbar there too.  Drag a window over to the
     right (or move the mouse onto that screen and open something) and the
     difference becomes obvious.  The "extended desktop ready" line in the log
     says where the screen sits in desktop coordinates.

  Q: The phone is black / it just says "waiting for the phone..."
  A: 1) Make sure the phone screen is on and unlocked (a locked screen encodes
        as black)
     2) Unplug the cable, plug it back in, then press Start again
     3) Check the log for adb errors, and press "Refresh" to see whether the
        device is still there
     4) If the phone asked about USB debugging earlier, press Start again

  Q: Stuttering and high latency
  A: Turn on the status overlay on the phone -- it shows fps and latency, so you
     can see which one is wrong:
     . fps low, latency fine  -> the USB cable is out of bandwidth.  Press
       "Measure cable speed" and keep the bitrate at or below what it reports;
       try a better USB cable in a USB 3 port.
     . fps fine, latency high -> the encoder or decoder is queueing.  Try a
       different encoder (h264_nvenc is the fastest), or drop to 30 fps.
     . Both fine but it still stutters -> the phone is throttling.  Lower the
       frame rate or resolution, or exempt this app from battery optimisation
       in the phone's own settings.
     . Leave "Capture" on Auto or ddagrab.

  Q: GPU usage is high -- is something wrong?
  A: No, and it follows directly from the virtual screen.  The virtual screen is
     not a real panel, but the Windows desktop compositor composites it like one
     -- more pixels and a higher refresh rate mean more to composite every
     frame.  Capture (ddagrab) and encoding run on the card as well.  A few
     percent to a low-teens percent of GPU usage in Task Manager is by design.
     Because of the virtual screen, GPU usage may be higher than you are used to.
     . To bring it down: drop the resolution first, then the refresh rate (the
       two multiply).
     . Worried about gaming?  Pick h264_nvenc / hevc_nvenc / av1_nvenc: they use
       the card's dedicated encoder block and do not compete with 3D rendering.
       Avoid libx264, which is pure CPU encoding.
     . Once the GPU really is saturated the virtual screen drops frames, and
       then only fewer pixels will help.

  Q: I changed the resolution / refresh rate / position and nothing happened?
  A: Those now apply immediately to a running virtual screen -- no restart
     needed.  The program retries up to four times and judges success by the
     mode that actually took effect, because Windows briefly locks the display
     configuration around a mode change; the first attempt or two failing is
     normal.  If it never takes effect, check that "Follow the phone's
     resolution" is not ticked -- that option overrides your choice.
     The resolution list now holds **exactly the modes the virtual display
     driver has** (the driver builds its mode table from every <resolution> row
     in vdd_settings.xml, and Windows rejects anything outside that table with
     DISP_CHANGE_BADMODE -- picking one used to do nothing at all).  A size that
     is not in the list is a size the driver cannot produce, and the log says so.
     The same goes for the refresh rates.

  Q: A screen is still there in Windows after I stopped?
  A: Normally Stop, or unplugging, removes the virtual screen.  If one is left
     over, press Start and then Stop once more; a failed start also removes it
     automatically.

  Q: When I am not streaming, can that screen be made to not exist at all?
  A: Yes -- that is the default.  "When not in use" has two modes:
     * Switch it off for real (the default) -- stopping the stream disables the
       driver's device node.  The screen is not there at boot, remote-desktop
       tools such as ToDesk or Sunlogin cannot enumerate it, and it stays
       disabled across a reboot.  The price is one administrator approval when
       you start and one when you stop.
     * Just take it off the desktop -- the driver is left alone and nothing asks
       for approval; the screen still exists, it is simply not on the desktop.
     To do it right now instead of waiting for the next stop, use the menu item
     Tools -> "Shut the virtual display down for real (asks once)".
     Writing <count> 0 into the driver settings does NOT achieve this: the
     driver treats 0 as invalid and creates one monitor anyway.

  Q: I cannot find the mouse on the extra screen?
  A: The virtual screen is placed against the main one according to "Position".
     When it is on the right, keep moving the mouse right and you will reach it.
     Win+P also shows whether Windows is in Extend or PC screen only.

[7] Settings, updates and where the files live
  . Your settings are remembered; the next launch starts where you left off.
  . Settings file: %USERPROFILE%\\.droidextend\\settings.json
    (File -> Open config folder opens it for you)
  . Tools -> Check for updates asks GitHub whether a newer version exists.
  . README.md in the program folder has the longer story, and 开发日志.md records
    how the thing was built.

[8] Other people's work (with thanks)
  . Virtual Display Driver (VDD) -- virtual display driver, MIT licence
    https://github.com/VirtualDrivers/Virtual-Display-Driver
  . nefcon -- small tool for installing and removing device drivers, MIT licence
    https://github.com/nefarius/nefcon
  . FFmpeg -- capture and hardware encoding (GPL build, called as a separate
    process)  https://ffmpeg.org/  this build is gyan.dev's 6.0-essentials
  . Android SDK Platform-Tools (adb) -- Apache License 2.0
  . R8 / D8 (Android SDK build-tools) -- BSD 3-Clause
  . Python / Tcl-Tk / PyInstaller -- see their own licence files
  . The installer is built with Inno Setup; the icon is drawn by a script, no
    stock artwork was used.

[9] About this project
  Written by: an AI coding agent (DeepSeek Harness / deepseek-flash)
  Every line of this program, from the first to the installer, was written by an
  AI.  A human asked for it, set the direction and accepted the result.
  Owner: {author}
  Repository: {repo}
  Issues: {issues}
  Email: {mails}
  If you spot something misattributed or a credit that is missing, write to any
  of the addresses above and it will be fixed in the next release.
"""

_FIELDS = dict(
    ver=APP_VERSION,
    author="Void_Blank (Blank2007)",
    repo=REPO_URL,
    issues=REPO_URL + "/issues",
    mails=" / ".join(EMAILS),
)

TEXT_ZH = TEXT_ZH.format(**_FIELDS)
TEXT_EN = TEXT_EN.format(**_FIELDS)


def text():
    """The manual in whichever language the window is using."""
    return TEXT_EN if i18n.en() else TEXT_ZH


# Kept for anything that imported it before the split; it is the Chinese one.
TEXT = TEXT_ZH
