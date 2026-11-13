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

  不想让手机控制电脑？右栏「手机触摸控制电脑」的勾随时取消、随时勾回来
  （1.9.0 起），画面照旧，不重连也不中断。

  注意：触摸注入的是「那块虚拟屏」上的坐标，鼠标会真的跑到那块屏去，
  所以主屏上的鼠标位置会跟着跳，这是正常的。
  手机转过来，画面会跟着转：手机端「显示设置」里的「屏幕方向」有三档
  （跟随平板 / 一直横屏 / 一直竖屏）。平板默认「跟随平板」，手机默认
  「一直横屏」。竖屏时主机端也按竖的方向做虚拟屏，桌面右侧会多出一块
  竖着的屏，不是把横向画面旋转 90 度。

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
  手机触摸控制电脑  勾掉之后手机上的触摸不再控制电脑，画面照旧（1.9.0 起）。
  开机自动启动  登录 Windows 后自动在后台起来（托盘图标）。
  开机后自动开始投屏  开机自己开始投屏，不用手点；勾它会自动勾上「开机自动启动」。
  开机后最多等手机  等到手机为止的最长等待：30 秒 / 60 秒 / 2 分钟 / 5 分钟 /
              一直等，默认 60 秒。等不到、或者哪一步出错，会发一条 Windows
              系统通知（有托盘图标时用托盘气泡）；任何时候手动点「开始投屏」
              或「停止」，它就退开不再等。

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

  Q: 浮窗上的延迟数字用久了会慢慢变大，是不是越来越卡？
  A: 不是。1.6.7 之前那个数字会把两台机器时钟的细微差别也算进去
     （大约 3 ms/分钟），所以看起来一直涨，而画面其实没变慢。
     现在手机端会自己把这种漂移修掉，两端的时间戳也换成了单调时钟。
     如果数字真的变大并且画面也跟不上，那才是上面那条「画面卡、延迟大」。

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

  Q: 平板竖起来能当竖着的副屏吗？右上角那个按钮很难点开？
  A: 两件都是 1.8.6 起做好的。手机端「显示设置」里：
     · 「屏幕方向」三档：跟随平板 / 一直横屏 / 一直竖屏。平板默认「跟随
       平板」，手机默认「一直横屏」。选跟随以后把平板竖起来，画面和虚拟屏
       都跟着变竖；投屏过程中转屏要重新挂一次屏（约 3 秒），面板里会提示。
     · 「设置按钮大小」四档：小 / 中 / 大 / 特大，默认中，点一下切一档。
       旁边还有「把按钮放回右上角」，拖偏了不用再一路拖回来。
     以前点不开不是按钮太小：程序量的是「手指到按钮中心的距离」，手指落在
     按钮边缘抖一两像素就会被当成拖动。现在量的是「手指离开按下点多远」。

  Q: 停止之后那块屏去哪了？为什么鼠标不会滑到右边去了？
  A: 「停止」会把虚拟屏从桌面上摘掉，所以桌面上不再多出一块空屏，鼠标也不会飘过去。
     驱动本身没有关，所以再点「开始投屏」时只要把它重新挂上（一秒左右），
     不用重新启用设备、也不会弹授权。

  Q: 不投屏的时候，能不能让这块屏彻底不出现？
  A: 能。设置里「退出程序时」有两档，管的是「完全退出程序」那一下：
     · 彻底关闭（默认）—— 退出程序时把驱动的设备节点禁用掉。开机不会有这块屏，
       ToDesk / 向日葵这类远程软件也枚举不到它，重启之后它仍然是关着的。
     · 只移出桌面 —— 退出时不碰驱动、不弹授权，屏还在系统里，只是不在桌面上。
     这两档都只影响退出时；平时的「停止」只摘桌面、不碰驱动。
     想马上关掉：工具菜单「彻底关闭虚拟屏」。程序本身已经以管理员身份运行，
     所以这一步不会额外弹授权。
     注意不能靠把驱动设置里的 <count> 写成 0 来实现：驱动把 0 当成非法值，照样建一块屏。

  Q: 为什么「更新目录」会自己变回默认值？
  A: 程序保存设置的位置（数据目录）以前可能被指到 %TEMP% 下面——那是测试留下的坑，
     %TEMP% 一被系统清理，程序就只能退回程序目录，所有设置一起变回默认值。
     1.6.5 起，落在 %TEMP% 下的记忆目录会被忽略。而且「更新目录」现在全程生效：
     下载的安装包、更新脚本、它的日志都放在你选的那个目录里。

  Q: 卸载的时候程序还在托盘里，会不会卸不干净？
  A: 1.6.5 起不会了：安装和卸载都会先强制关掉 DroidExtend.exe（最多重试 8 次）再动文件。
     更早的版本确实会留下几个删不掉的文件（tools 目录下的 adb 相关文件最典型），
     重启之后手动删掉就行。

  Q: 浮窗上那个 fps 是我的手机刷新率吗？
  A: 不是。它是「流帧率」——电脑发了多少帧、手机解出来多少帧。1.6.8 起后面会再跟一句
     「（屏 120 Hz）」，那个才是屏幕自己的刷新率。60 fps 的画面在 120 Hz 屏上是正常的。

  Q: 点 × 之后窗口去哪了？想改怎么改？
  A: 1.6.8 起第一次点 × 会问你一次：最小化到托盘（后台继续跑，投屏不断）还是直接退出，
     旁边有「不再提醒（记住这次的选择）」。以后想改，走 工具 -> 关闭窗口时的行为…，
     随时能切回「每次问我」。

  Q: 电脑上多出来的那块屏找不到鼠标？
  A: 虚拟屏是按「屏幕位置」贴着主屏放的。放在右边时，鼠标一直往右移就过去了。
     也可以按 Win+P 看当前是「扩展」还是「仅电脑屏幕」。

  —— 1.8.4 以后这几版新出现的问题 ——

  Q: 驱动明明装过了，左边那个按钮怎么还是亮着的？（1.9.1）
  A: 以前按钮只认「屏幕已经就绪」这一种状态，而选了「退出程序时彻底关闭」的人
     一退出设备节点就被禁用，下次打开必然「没就绪」，于是按钮一直是亮的。
     1.9.1 起按钮会说清自己在哪一步：
     · 「安装虚拟显示器驱动（需要管理员）」= 真的没装；
     · 「启用虚拟显示器驱动（需要管理员）」= 装了，但设备被关着，点一下打开就行
       （不用重装，也不会白等一次 UAC 再复制一堆本来就在的文件）；
     · 「虚拟显示器驱动已安装」= 装好了、正开着，这时按钮是灰的。

  Q: 想彻底把虚拟显示器驱动从系统里去掉？（1.9.1）
  A: 菜单 工具 -> 卸载虚拟显示器驱动…。它会删掉驱动包和虚拟屏的设备节点，
     但保留 C:\\VirtualDisplayDriver 里的分辨率表（自己加过的尺寸都还在）
     和本程序的全部设置。卸载后投屏用不了，要投屏时再装一次。
     正在投屏会先自动「停止」，不会留下一块挂在桌面上的空屏。

  Q: 通知能关吗？能只通知失败、不通知成功？（1.9.1）
  A: 能。顶部菜单「通知」里是一个总开关加七类分开勾：投屏开始（自动那次）/
     手动点开始也通知 / 失败或中断 / 驱动安装启用卸载 / 有新版本 / 手机端
     App 需要更新 / 其它提示。默认除了「手动点开始也通知」之外都是开的——
     自己刚点完的按钮再弹一条，多数人嫌吵。
     「发一条测试通知」可以立刻验证通不通。发不出来最常见的原因是 Windows
     设置里关掉了本程序的通知，或者开着「专注助手」，菜单里有「Windows 通知设置…」。

  Q: 开机自动开始投屏，结果一直没开始 / 没画面？（1.9.0）
  A: 两个勾都要在：右栏「开机自动启动」和「开机后自动开始投屏」。
     「开机后最多等手机」默认 60 秒，可以改 30 秒 / 2 分钟 / 5 分钟 / 一直等。
     等到会自己开始并点亮手机屏幕；等不到会弹一条 Windows 通知
     （窗口在托盘里也看得见）。手动点「开始投屏」或「停止」会立刻让这套自动
     流程退开，不跟人抢按钮。通知里如果说虚拟屏没准备好，先点左栏那个「启用…」
     按钮——自动流程碰到这一类错不会反复试。

  Q: 装驱动时只弹一句「详见日志」，日志却是空的？（1.8.7）
  A: 那是 Windows PowerShell 起不来了（退出码 0xFFFF0000 就是它的特征），
     脚本一行都没跑到，日志当然是空的。1.8.7 起会直接说清原因，并且问你要不要
     用「不依赖 PowerShell 的备用方式」（certutil + nefcon，纯命令行）再来一次。
     两次都失败就按日志里的提示修 PowerShell：管理员命令行跑 sfc /scannow 和
     DISM /Online /Cleanup-Image /RestoreHealth，或者装一个 PowerShell 7。
     程序启动时日志里有一行「PowerShell: 5 …」，那行就是它现在在用的那个。

  Q: 左边「采集显卡」那一行是什么？为什么有时是「——」？（1.8.5）
  A: 它写的是画面到底从哪块显卡上抄下来的、以及用谁编码，例如
     「采集显卡: 显卡0 "NVIDIA GeForce RTX 5070 Laptop GPU"（输出 #1）／编码: NVIDIA 显卡的 NVENC」。
     没在投屏时是「——」。双显卡机器上这行很有用：1.8.5 之前 ddagrab 只看得见
     一张显卡的输出，虚拟屏在另一张卡上时会找不到，白白掉回 gdigrab。

  Q: 装了 MuMu / Parsec / GameViewer 之后，扩展屏开了但手机没画面？（1.8.8）
  A: 1.8.8 修的就是这个。以前程序按名字模糊匹配「virtual display」，会把 MuMu 等
     软件自己的虚拟显示适配器当成本程序的屏，于是"找到了屏"、其实一块也没挂上。
     现在只认本程序自己的驱动（MttVDD / MIT1337），日志里也会写出它看到的外来适配器。

  Q: 华为 / 鸿蒙设备投屏没画面，从哪儿查？（1.8.4）
  A: 1.8.4 起两端都会留证据：主机每 10 秒一行
     「采集 120 fps · 发送 19.8 Mbps · 手机 收 121 fps / 显示 122 fps · 8 ms」，
     手机端也会把解码器事件回给主机（配置成功、第一帧上屏、Surface 被替换等）。
     把这几行一起发到 issue 里，就能分清是「主机没发」还是「手机收了没显示」。
     README 里「华为 / 鸿蒙设备」一节有更完整的排查顺序。

【七、设置、更新与文件位置】
  · 你改过的参数会自动记住，下次打开软件还是上次的配置。
  · 设置文件： 程序目录里的 settings.json（1.6.6 起所有数据都在安装目录里）
    （菜单 文件 -> 打开数据目录 可以直接打开）
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

  Do not want the phone driving the PC?  The "phone touch controls the PC" box
  on the right can be cleared and ticked again mid-stream (since 1.9.0); the
  picture keeps running and nothing reconnects.

  Note: touch is injected at coordinates on that virtual screen, so the real
  cursor does move over there and the pointer leaves your main screen.  That is
  normal.  Turn the phone and the picture follows: "Screen direction" in the
  phone's display settings has three choices (follow the tablet / always
  landscape / always portrait).  A tablet defaults to "follow the tablet", a
  phone to "always landscape".  In portrait the host builds the virtual screen
  tall as well, so the desktop gains a tall screen -- it is not a landscape
  picture rotated by 90 degrees.

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
  Phone touch    Unticking this stops the phone from driving the PC; the picture
                 is unaffected (since 1.9.0).
  Start with Windows     Comes up in the background (tray icon) at logon.
  Stream after boot      Starts streaming by itself after boot; ticking it also
                         ticks "Start with Windows".
  Wait for the phone     Longest wait for the phone: 30 s / 60 s / 2 min / 5 min
                         / keep waiting -- 60 s by default.  If it never turns
                         up, or a step fails, a Windows notification says so (a
                         tray balloon when there is an icon); pressing Start or
                         Stop by hand makes it stand down.

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

  Q: The latency number on the overlay slowly grows.  Is it getting laggier?
  A: No.  Before 1.6.7 that figure also counted the tiny difference between the two
     machines' clocks (about 3 ms per minute), so it looked like it kept climbing
     while the picture was never getting slower.  The phone now corrects that drift
     itself and both ends stamp frames from a monotonic clock.  If the number grows
     AND the picture cannot keep up, that is the "stuttering and high latency" case
     above instead.

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

  Q: Where did that screen go after I stopped, and why does the mouse stop at
     the right edge now?
  A: Stop takes the virtual screen back off the desktop, so the desktop does not
     grow a spare empty screen and the mouse cannot wander onto it.  The driver
     itself is left running, so pressing Start again only re-attaches the screen
     (about a second) -- no driver switch, no approval prompt.

  Q: When the program is not running, can that screen be made to not exist at all?
  A: Yes.  "When the program exits" has two modes:
     * Switch it off for real (the default) -- exiting disables the driver's
       device node.  The screen is not there at boot, remote-desktop tools such
       as ToDesk or Sunlogin cannot enumerate it, and it stays disabled across
       a reboot.
     * Just take it off the desktop -- the driver is left alone and nothing asks
       for approval; the screen still exists, it is simply not on the desktop.
     Both choices only apply when the program exits; a plain Stop just takes the
     screen off the desktop and touches neither.  To do it right now, use Tools
     -> "Shut the virtual display down for real".  The program already runs as
     administrator, so that step does not ask for approval.
     Writing <count> 0 into the driver settings does NOT achieve this: the
     driver treats 0 as invalid and creates one monitor anyway.

  Q: Why does the "update folder" keep reverting to its default?
  A: The folder the program keeps its settings in (the data folder) could end up
     pointed under %TEMP% -- a hole left by a test.  As soon as Windows cleaned
     that folder the program could only fall back to its own folder and every
     setting reverted at once.  Since 1.6.5 a remembered folder under %TEMP% is
     ignored.  "Update folder" also applies to the whole update now: the
     downloaded installer, the update script and its log all go to the folder
     you picked.

  Q: If the program is still in the tray, will uninstalling leave files behind?
  A: Not since 1.6.5: both install and uninstall force-close DroidExtend.exe
     first (retrying up to eight times) before touching files.  Earlier versions
     did leave a few undeletable files behind (most typically the adb files under
     the tools directory); delete them by hand after a reboot.

  Q: Is the fps on the overlay my phone's refresh rate?
  A: No. It is the stream frame rate -- how many frames the PC sent and the
     phone decoded. Since 1.6.8 it is followed by a "screen 120 Hz" figure, which
     is the panel's own refresh rate. 60 fps of video on a 120 Hz screen is
     normal.

  Q: Where did the window go after I clicked ×, and how do I change it?
  A: Since 1.6.8 the first × asks once: minimize to the tray (the program keeps
     running in the background, the stream stays up) or quit, with a
     "do not ask again (remember this choice)" box next to them. To change it
     later use Tools -> Closing behaviour..., where "ask me every time" is always
     available.

  Q: Can a tablet standing up act as a tall second screen, and why is the
     top-right button so hard to open?
  A: Both were done in 1.8.6.  In the phone's display settings:
     . "Screen direction" has three choices: follow the tablet / always
       landscape / always portrait.  A tablet defaults to "follow the tablet",
       a phone to "always landscape".  With "follow", standing the tablet up
       turns both the picture and the virtual screen tall; rotating while
       streaming re-mounts the screen once (about 3 s) and the sheet says so.
     . "Button size" has four steps (small / medium / large / extra large),
       medium by default, one tap cycles it.  Next to it, "put the button back
       in the top right".
     It was never too small: the code measured the distance from the finger to
     the centre of the button, so a finger landing near the edge plus a pixel
     of jitter counted as a drag.  It now measures how far the finger moved
     from where it went down.

  Q: I cannot find the mouse on the extra screen?
  A: The virtual screen is placed against the main one according to "Position".
     When it is on the right, keep moving the mouse right and you will reach it.
     Win+P also shows whether Windows is in Extend or PC screen only.

  -- problems new since 1.8.4 --

  Q: The driver is definitely installed, so why is the left button still lit up? (1.9.1)
  A: The button used to mean one thing only -- "the screen is ready" -- and anyone who
     quits with the "off for real" setting has the device node disabled, so the next
     launch is never "ready" and the button stayed lit.  Since 1.9.1 it says which of
     three situations you are in:
     . "Install the virtual display driver (needs admin)" -- it really is missing;
     . "Turn the virtual display driver on (needs admin)" -- installed but the node is
       off; press it to switch the node back on (no reinstall, no waiting through a
       second UAC prompt to copy files that are already there);
     . "The virtual display driver is installed" -- installed and running, greyed out.

  Q: How do I remove the virtual display driver completely? (1.9.1)
  A: Tools -> Remove the virtual display driver...  It deletes the driver package and
     the device node, and keeps both the resolution table in C:\\VirtualDisplayDriver
     (every size you added is still there) and all of this program's settings.
     Streaming will not work until the driver is installed again.  A running stream is
     stopped first, so no empty screen is left on the desktop.

  Q: Can notifications be turned off, or limited to failures? (1.9.1)
  A: Yes.  The "Notifications" menu at the top has a master switch and one box per
     kind: the stream started by itself / also when I press Start myself / failed or
     broke off / driver installed, turned on or removed / a newer version / the phone
     app needs updating / everything else.  All are on by default except "also when I
     press Start myself", which is noise for most people.
     "Send a test notification" says straight away whether it works.  When it does not,
     the usual reason is Windows itself: notifications for this app turned off, or
     Focus Assist on.  The menu links to the Windows settings.

  Q: The stream should start by itself at boot, but nothing happens? (1.9.0)
  A: Both boxes have to be ticked: "Start with Windows" and "Stream after boot".
     "Wait for the phone" defaults to 60 s and can be 30 s / 2 min / 5 min / forever.
     When the phone turns up it starts and wakes the screen; when it does not you get a
     Windows notification, even with the window in the tray.  Pressing Start or Stop by
     hand calls the automatic attempt off at once -- it never fights the user for the
     button.  If the notification says the virtual display is not ready, press the
     "turn on ..." button first: the automatic attempt does not retry that kind of error.

  Q: Installing the driver only says "see the log", and the log is empty? (1.8.7)
  A: That is Windows PowerShell failing to start (exit code 0xFFFF0000 is its
     signature): the script never ran a line, so of course the log is empty.  Since
     1.8.7 the program says so, and offers the fallback that does not use PowerShell at
     all (certutil + nefcon, plain command line).  If both fail, repair PowerShell as the
     log suggests: sfc /scannow and DISM /Online /Cleanup-Image /RestoreHealth in an
     admin prompt, or install PowerShell 7.  The log line "PowerShell: 5 ..." at startup
     says which one is in use.

  Q: What is that "Capture GPU" line on the left, and why does it say "-"? (1.8.5)
  A: It names the graphics card the picture is actually copied from, and what encodes
     it, for example "Capture GPU: GPU0 'NVIDIA ...' (output #1) / encoding: NVIDIA
     NVENC".  It shows "-" while nothing is streaming.  On a dual-GPU machine this is
     the line to read: before 1.8.5 ddagrab only ever saw the outputs of one card, so a
     virtual screen sitting on the other one was not found and capture fell back to
     gdigrab for no visible reason.

  Q: With MuMu / Parsec / GameViewer installed, the extended screen starts but the phone stays black? (1.8.8)
  A: That is what 1.8.8 fixed.  The program used to match adapters by a fuzzy
     "virtual display" substring, so another program's virtual adapter was taken for its
     own -- "found the screen" while nothing had actually been attached.  It now
     recognises only its own driver (MttVDD / MIT1337) and logs the foreign adapters it
     noticed.

  Q: A Huawei / HarmonyOS device shows no picture.  Where do I look? (1.8.4)
  A: Since 1.8.4 both ends leave evidence.  The host writes one line every 10 s:
     "capture 120 fps / sending 19.8 Mbps / phone receiving 121 fps / showing 122 fps / 8 ms",
     and the phone reports decoder events back (configured, first frame on screen,
     surface replaced).  Post those lines in the issue and it becomes obvious whether the
     host never sent or the phone received and never showed.  The "Huawei / HarmonyOS"
     section of the README has the full order to try.

[7] Settings, updates and where the files live
  . Your settings are remembered; the next launch starts where you left off.
  . Settings file: settings.json in the program folder (since 1.6.6 every file the
    program writes lives in the install folder)
    (File -> Open data folder opens it for you)
  . Tools -> Check for updates asks GitHub whether a newer version exists.
  . README.md in the program folder has the longer story, and the development log
    (Chinese) records how the thing was built.

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
