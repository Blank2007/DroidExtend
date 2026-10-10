# DroidExtend — 用数据线把安卓手机变成 Windows 的拓展屏

一根 USB 数据线，手机立刻变成电脑的第二块显示器：**不是投屏镜像，是真的扩展桌面**——
你可以把窗口从笔记本屏幕拖到手机上，鼠标直接在上面操作。

* 画质、帧率、分辨率、刷新率、码率**全部可调**
* 走 USB 数据线（adb），**不依赖 Wi-Fi、不需要同网段、不装任何第三方 App**
* 电脑端：Python + ffmpeg（NVENC 硬件编码）
* 手机端：随包附带的 `dist/DroidExtend.apk`

---

> ### 🤖 这个仓库的代码是 AI 写的
>
> **全部源代码由 AI 编码代理编写**——在 **DeepSeek Harness** 里、用 **deepseek-flash** 模型，
> 于 2026-10-10 的一次会话内从零写完并交付到可安装的 1.1.0。
> 人类（[@Blank2007](https://github.com/Blank2007)）负责提需求、插上真机、在需要管理员权限时点“是”、以及验收。
>
> 整个过程（包括踩过的每一个坑和实测数据）记在 **[开发日志.md](开发日志.md)** 里，
> 里面有两个我觉得挺值得一读的 bug：**一行 `p.stdout.read()` 造成的 100 ms 延迟**，
> 以及**一个只有换目录才能复现、最后发现是 AI 会话沙箱在拦的显示配置问题**。

**它有什么特别的**

| | |
|---|---|
| 🖥️ **真扩展桌面** | 不是镜像投屏。`Win+P` 里会多出一块显示器，窗口能拖过去，鼠标能直接在上面点 |
| 🔌 **纯 USB** | 走 `adb reverse` 隧道，不依赖 Wi-Fi、不用同网段、手机端不装任何第三方 App |
| ⚡ **低延迟** | 端到端实测 **5–11 ms**（从主机抓到帧到手机上解出来），修复前是 31–42 ms |
| 👆 **能反控** | 手机上的点按 / 拖动 / 双指滚动 / 双指轻点=右键，直接注入到 Windows |
| 🧹 **拔线即消失** | 点停止或拔掉数据线，虚拟屏立刻从系统里摘掉，不留幽灵显示器 |
| 🎛️ **全硬件链路** | `ddagrab`(桌面复制) → `h264_nvenc`(GPU 编码) → `MediaCodec`(手机硬解)，采集编码几乎不吃 CPU |
| 📦 **开箱即用** | 34 MB 的安装包，ffmpeg / adb / 驱动 / APK 全在里面，装完不用再准备任何东西 |

> **想直接拿现成的？**
> * 安装程序 ⬇️ **[DroidExtend-Setup-1.1.0.exe](https://github.com/Blank2007/DroidExtend/releases/latest/download/DroidExtend-Setup-1.1.0.exe)**（34 MB）
> * 手机端 App ⬇️ **[DroidExtend.apk](https://github.com/Blank2007/DroidExtend/releases/latest/download/DroidExtend.apk)**（21 KB）
>
> 也可以在 [Releases](https://github.com/Blank2007/DroidExtend/releases) 页面里下载。
> 本仓库只放源码 —— 二进制依赖（ffmpeg、adb、Android SDK、虚拟显示驱动）加起来 370 MB，没有进仓库。

---

## 〇、安装（推荐，双击下一步就行）

双击 **`DroidExtend-Setup-1.1.0.exe`** → 一路“下一步”。
**第一个页面就是“选择安装位置”**，默认装在下面这个目录，想换地方点旁边的“浏览…”。

* 装到 `%LOCALAPPDATA%\Programs\DroidExtend`（默认值，可以改），**全程不请求管理员权限**，装完直接能用
* 自动建开始菜单和桌面快捷方式；想卸载就点开始菜单里的 **卸载 DroidExtend**
* 安装包 34 MB，里面已经打包好 ffmpeg、adb、虚拟显示器驱动文件和手机端 APK，
  **装完不需要再准备任何东西**

> 安装包只往你的用户目录里放文件，**不动系统**。虚拟显示驱动属于系统级组件，
> 由程序界面上的按钮单独装（见下面第 1 步），那一步才会弹 UAC。

> **1.1.0 修了什么**
> * **拓展屏模式不会再传成主屏画面。** ddagrab 只能按 DXGI *输出序号* 抓屏，而 1.0.0 是拿
>   分辨率去认屏的 —— 笔记本屏和虚拟屏分辨率一样时（比如常见的 1920x1080 本子）就会静默
>   选中「0 号输出」＝ 笔记本自己的屏幕。现在多个输出尺寸相同时会抓缩略图逐像素比对，
>   认不出来就退回 gdigrab 按桌面坐标抓（慢一点但永远对），**绝不猜**。
> * **安装程序第一个页面就能自己选安装路径**（原来升级时会静默沿用旧目录、整页被跳过）。
> * 顺手：虚拟屏空着时，手机上看就是同一张壁纸加一条任务栏，很容易被当成主屏；
>   现在日志会直接标出虚拟屏的桌面坐标，告诉你要把窗口往哪个方向拖。

---

## 一、快速开始（三步）

### 1. 装虚拟显示器驱动（只需一次）

**装过安装包的话**：直接打开 DroidExtend，点左上角的
**「安装虚拟显示器驱动（需要管理员）」** → 弹出 UAC 时点“**是**”。

**用免安装版的话**：双击 **`安装虚拟显示器驱动.bat`** → 同样点“**是**”。

大约 10 秒后日志里出现 `device ROOT\DISPLAY\0001  status=OK` 就成功了。

> Windows 不允许任何程序凭空多出一块屏幕，必须装一个内核级的虚拟显示驱动（IDD）。
> 这里用的是开源的 **Virtual Display Driver 25.7.23**，已签名，本机实测通过，
> 不需要关闭安全启动、也不需要开启测试模式。

### 2. 手机插上数据线，打开 USB 调试

* 设置 → 关于手机 → 连点“版本号”7 次开启开发者选项
* 开发者选项 → 打开 **USB 调试** 和 **USB安装**
* 插上数据线，手机上弹出“允许 USB 调试吗？”→ 点**允许**

### 3. 双击 `DroidExtend.exe`

**装过安装包的话**，从开始菜单／桌面打开 DroidExtend 就行，不用管 exe 在哪。

在窗口里选好分辨率和帧率，点 **▶ 开始投屏**。
第一次会自动把 APK 装到手机上并启动，之后几秒手机就变成第二块屏幕了。

> `DroidExtend.exe` 是免安装的主程序（旁边那个 `_internal\` 文件夹是它的运行库，
> 两个要放在一起）。`启动 DroidExtend.bat` 是同一程序的源码启动方式，保留备用：
> 万一 exe 被杀软拦了，双击它效果完全一样。

> ⚠️ **如果不用安装包，别在 `D:\dsh projects` 目录里直接运行 exe。**
> 当前这个 AI 会话的沙箱会拦截「程序镜像位于工作区目录之下」的进程调用显示配置 API：
> 同一个探针程序放进 `D:\dsh projects` 返回 `CDS_TEST rc=-1 / SDC_VALIDATE rc=5`，
> 复制到 `D:\probe_test` 就返回 `rc=0 / rc=0`。表现是日志里反复刷
> `激活显示器 attach rc=-1`、`attach 未生效，改用扩展拓扑 rc=5`，最后状态变 `出错`。
> 安装包和 `D:\DroidExtend\` 都在工作区之外，**没有这个问题**；
> 关掉 AI 会话之后这个限制也不存在了，放哪儿都能跑。
> （源码方式启动同样不受影响，因为解释器在 `C:\Python314`。）

---

## 二、界面说明

| 项目 | 说明 |
| --- | --- |
| **模式** | `拓展屏`：新建一块虚拟屏，桌面变大；`镜像`：直接复制现有某块屏幕 |
| **分辨率 / 刷新率** | 虚拟屏的真实模式。驱动支持 1280×720 ~ 3840×2160 共 8 档 × 30/60/90/120/144 Hz，也可自定义宽高 |
| **帧率 fps** | 编码帧率，24 ~ 144 可选 |
| **码率上限 Mbps** | 编码码率上限，VBR 下是“天花板”而不是恒定值 |
| **码率控制** | `VBR`（质量优先，静止桌面几乎不占带宽）／`CBR`（恒定码率，剧烈运动画面更稳） |
| **画质 Q** | 越小画质越好、码率越高。`18~20` 基本视觉无损，`24` 以上能看出块效应 |
| **编码器** | 自动探测，本机可用：`h264_nvenc`（推荐）、`hevc_nvenc`、`h264_amf`、`libx264` |
| **采集** | `auto` 会自动选 ddagrab（桌面复制 API，零拷贝进 NVENC）；老显卡/异常时改 gdigrab |
| **屏幕位置** | 虚拟屏摆在主屏的右／左／上／下 |
| **测一下数据线速度** | 实测这根线能跑多少 Mbps，并自动把码率上限压到安全值（先停流测更准） |
| **停止** | 停流，并**把虚拟屏从系统里摘掉**（见下） |

窗口底部实时显示：`手机已连接  58.2 fps  编码 2.4 Mbps  送出 1203 帧  丢 0`。

### 触摸控制

手机上的画面**可以直接摸**，触点、滑动、滚轮都会注进 Windows 的真实鼠标：

| 手势 | 效果 |
| --- | --- |
| 单指点击 / 拖动 | 左键点击 / 左键拖拽 |
| 双指上下滑 | 滚轮 |
| 双指快速轻点 | 右键点击 |

走的是**第二条 adb reverse 隧道**（`tcp:27184`），和视频互不干扰。手机端把触点按画面宽高比归一化后
发 7 字节记录过去，主机侧用 `SendInput` 以**整块虚拟桌面的绝对坐标**注入 —— 所以触点落在虚拟屏上，
Windows 就当是那块屏上的真实鼠标。

> 坐标是相对**整块虚拟桌面**归一化的，所以程序启动时就设置了 per-monitor DPI 感知；
> 如果你把 Windows 缩放改到 125% 以上，触点依然对得上。

### 停止之后虚拟屏会消失

按 `停止`（或关掉窗口）时，程序会把虚拟屏**从桌面上摘掉**：Windows 显示设置里不再有那一块屏，
桌面宽度也从 4480 恢复成 2560。

> 这是早期版本最烦人的一点 —— 旧代码只是把虚拟屏"恢复成原来的分辨率"，于是屏幕永远挂在系统里。
> 现在走的是 `SetDisplayConfig(SDC_USE_SUPPLIED_DISPLAY_CONFIG)`，只摘掉虚拟屏那一条路径，
> **不会碰你的真实外接显示器**。驱动本身还在，下次点 `开始投屏` 会自动接回来。

---

## 三、性能与调优（实测数据）

本机（RTX 5070 Laptop + Redmi 24091RPADC，Android 16，1920×1080@60）实测：

```
采集 ddagrab（桌面复制）→ h264_nvenc → adb reverse → 手机 MediaCodec 硬解
server: sent 1482 帧  dropped 0  bytes 7,614,539
手机:   Qin 304  Render 303  Drop 0  Avg 16ms/frame     ← 全部帧都渲染出来了
```

**关键瓶颈是数据线，不是软件。** 用 `测一下数据线速度` 量一下（量的时候最好先停流，
探测和视频会抢同一条线）：

* 同一台手机，换线／换口的差别极大，本机实测过三个阶段：
  * 一根只充电级别的线 → 协商成 **USB 1.1 全速，实测 13.7 Mbps**，1080p60 会被压得很糊；
  * 换成正常数据线 → **实测 154.2 / 147.3 Mbps**；
  * 再换到快口 → **实测 597 Mbps**（用不可压缩的随机数据，手机自己回报收到 56.0 MB / 0.79s）。
    71 MB/s 已超过 USB 2.0 的理论上限 60 MB/s，说明这条线是 USB 3 级别。
* 测速的可信度：程序让**手机自己回报实际收到的字节数**，并且用「总字节 ÷ 总耗时（发送 + 排空）」算速率，
  所以 buffer 骗不了它。反过来看：`adb push` 的数字**不能**当链路速度（全零文件会被压缩，
  它报过 1265 MB/s）；`adb exec-out dd` 也只有 13 MB/s 左右，那是 shell 协议本身的天花板。
* 默认码率上限 **40 Mbps** 是按普通好线定的。它是 VBR 的天花板而不是恒定值——
  静止桌面只跑 1~2 Mbps，只有全屏运动才会去吃这个预算。
* 链路够快时可以直接把上限拉到 80~120 Mbps；实测 1080p60 满屏运动在 `画质 Q=20` 下
  也只花了 2.3~2.7 Mbps（该内容太简单），所以**画质主要由 Q 决定，码率上限只是保险丝**。
* 如果你还在用慢线，程序测速后会自动把上限压到安全值；也可以手动降到 8~10 Mbps。

其它调优建议：

* **优先用 `h264_nvenc`**：ddagrab 的 D3D11 硬件帧能直接喂进编码器，CPU 占用接近 0。
* **画面撕裂/掉帧**：把帧率降到 30，或把码率上限降到链路实测值的 60%。
* **手机不发烫**：分辨率和帧率是主要因素，1080p60 长时间跑手机会温热，正常。
* **第一次黑屏一下**：ddagrab 每路流的第 0 帧是纯黑帧，程序已经在编码前丢掉它。

### 延迟（实测）

手机端每 2 秒会打印一行 `perf fps=… latency_avg_ms=… latency_max_ms=…`（`adb logcat -s DroidExtend:I`），
时间戳由主机在抓帧时打上，手机用握手时的时钟差换算，量的是**从抓到帧到解出来**的全链路：

```
修复前  latency_avg_ms 31~42   latency_max 56~79
修复后  latency_avg_ms  5~11   latency_max 26~45
```

差在哪：`capture.py` 原本用 `p.stdout.read(65536)` 抽 ffmpeg 的输出。Python 的 `BufferedReader`
会一直读到填满 64 KB 才返回，实测**平均每 200.0 ms 才交一次 64 KB**（换成 `os.read` 后是每 13.7 ms 交 4.6 KB）——
也就是说画面先在缓冲里干等约 100 ms 才进编码队列，手机那边则表现为「攒一批再一起渲染」
（logcat 里的 `Render time interval reaches 154~258ms` 就是这个）。
现在改成 `os.read(p.stdout.fileno(), 65536)`，有数据立刻拿走。

---

## 四、目录结构

```
droid-extend/
├─ DroidExtend.exe                ← 平时就双击这个
├─ _internal/                     ← exe 的运行库，必须和 exe 放一起
├─ 启动 DroidExtend.bat           ← 同一程序的源码启动方式（备用）
├─ 安装虚拟显示器驱动.bat         ← 首次运行一次（需要管理员）
├─ 构建手机端 APK.bat             ← 改了手机端 Java 代码才需要
├─ dist/
│   ├─ DroidExtend.apk           ← 手机端安装包
│   └─ DroidExtend-Setup-1.1.0.exe ← Windows 安装程序（34 MB）
├─ tools/installer/
│   └─ DroidExtend.iss           ← 安装程序的 Inno Setup 脚本
├─ host/                          ← Windows 端（Python）
│   ├─ app.py                    GUI 主程序
│   ├─ session.py                一次投屏会话：显示 → 采集 → 编码 → USB → 手机
│   ├─ capture.py                ffmpeg 命令行、ddagrab 输出探测、H.264 访问单元切分
│   ├─ server.py                 TCP 推流服务（握手 + 帧队列 + 丢帧策略）
│   ├─ touch.py                  触摸注入服务（SendInput 绝对坐标）
│   ├─ win_display.py            CCD / ChangeDisplaySettingsEx 封装（cpp 级显示控制）
│   ├─ vdd.py                    虚拟显示驱动的安装与状态检查
│   ├─ adb.py                    adb 封装（reverse 隧道、安装、启动）
│   ├─ linkspeed.py              USB 链路测速
│   └─ paths.py                  定位随包的 adb / ffmpeg / JDK / SDK
├─ android/                       ← 手机端（纯 Java，无 Gradle）
│   ├─ src/com/droidextend/MainActivity.java
│   ├─ src/com/droidextend/StreamClient.java
│   ├─ src/com/droidextend/VideoDecoder.java
│   ├─ src/com/droidextend/InputClient.java
│   ├─ AndroidManifest.xml
│   └─ build.py                  aapt2 → javac → d8 → 打包 → apksigner 一键构建
├─ tools/                         ← 随包工具链与安装脚本
│   ├─ platform-tools/adb.exe
│   ├─ ffmpeg/bin/ffmpeg.exe
│   ├─ sdk/                      Android build-tools 34.0.0 + platform 34
│   ├─ vdd/                      Virtual Display Driver 驱动文件 + nefcon
│   └─ vdd_install.ps1           驱动安装脚本（管理员）
└─ README.md
```

---

## 五、它是怎么工作的

```
┌────────────── Windows ──────────────┐         ┌──── Android ────┐
│ 虚拟显示驱动 (IDD) → 第二块屏幕       │         │                 │
│        ↓ 桌面复制 API (ddagrab)      │         │  SurfaceView    │
│   ffmpeg + NVENC 硬件编码 H.264      │         │      ↑          │
│        ↓ 访问单元 (Annex-B)          │  USB    │  MediaCodec     │
│   TCP 127.0.0.1:27183 ───────────────┼────────→│  硬件解码        │
│   TCP 127.0.0.1:27184 ←──────────────┼─────────│  触摸事件        │
└──────────────────────────────────────┘ adb     └─────────────────┘
                                      reverse
```

1. **`win_display.py`** 用 Windows CCD API 把虚拟屏激活并摆到主屏旁边，
   再用 `ChangeDisplaySettingsEx` 设成你选的分辨率/刷新率；停止时用
   `SetDisplayConfig(SDC_USE_SUPPLIED_DISPLAY_CONFIG)` 只摘掉虚拟屏那一条路径。
2. **`capture.py`** 用 ddagrab 从桌面复制 API 抓那一块矩形——D3D11 纹理直接进 NVENC，
   不经过内存拷贝；启动时会先探测 `output_idx` 到显示器的映射，并丢掉首帧黑帧。
   读 ffmpeg 输出用 `os.read()` 而不是 `read()`，避免 64 KB 缓冲把延迟抬到 ~100 ms。
   ddagrab 只认 DXGI **输出索引**（不认设备名，也没有桌面坐标选项），而分辨率**不是身份**：
   `1920x1080` 既是全世界最常见的笔记本屏，也是虚拟屏的默认值。所以当有多个输出尺寸相同时，
   程序会用 gdigrab 抓虚拟屏所在的桌面矩形、和每个候选输出的缩略图逐像素比对，只有真的那块屏
   才对得上；**比不出来就用 gdigrab 按桌面偏移抓（慢一点，但永远是对的），绝不猜 0 号输出**
   ——猜错的后果就是把笔记本自己的屏幕推给手机。
3. 编码出来的裸 H.264 被按访问单元切开，经 **`adb reverse tcp:27183`** 建立的隧道
   送到手机（手机连的是它自己的 `127.0.0.1:27183`，所以**完全走 USB，不需要 Wi-Fi**）。
   每帧前面带一个主机时间戳，手机据此算出端到端延迟并显示出来。
4. 手机端 **`VideoDecoder.java`** 从码流里自己抓 SPS/PPS 配置 MediaCodec，
   硬解后直接渲染到 `SurfaceView`；一旦积压超过 384 KB 就丢到下一个关键帧，保证低延迟。
5. 触摸事件反向走 **`tcp:27184`**：手机按画面宽高比归一化坐标 → 主机 **`touch.py`**
   用 `SendInput` 以整块虚拟桌面的绝对坐标注入成真实鼠标。

---

## 六、常见问题

**Q：手机画面上只有壁纸和任务栏，看着跟主屏一模一样？**
A：这正是**扩展成功**的样子 —— 新接上的显示器默认就是一张同样的壁纸，
Windows 还会在上面再画一条任务栏，所以空着的拓展屏一眼看去和主屏没区别。
窗口在**主屏**上，手机当然看不到：把任意窗口往拓展屏方向（默认是主屏右侧）拖过去，
手机上立刻就出现那个窗口。日志里的
`拓展屏就绪: \\.\DISPLAY20 -> 1920x1080@60 at (2560,0)` 就是它的桌面坐标，
`投屏已开始: extend …` 表示流的就是这块屏。

**Q：手机黑屏 / 只有状态文字？**
A：手机锁屏了。按一下电源键唤醒、解锁，App 会自动重连（每 700 ms 重试一次）。

**Q：一直显示“等待手机连接…”？**
A：`adb devices` 看一下有没有 `unauthorized`——那是手机上没点允许。也可以在界面里点“刷新”。

**Q：点了开始投屏，桌面没变化？**
A：看日志里 `ChangeDisplaySettingsEx -> SUCCESSFUL` 那行。
如果是 `FAILED`，通常是分辨率不在驱动模式表里，程序会自动选最近的可用模式并记在日志里。

**Q：停止之后桌面布局乱了？**
A：正常情况下 `停止` 会把虚拟屏从系统里摘掉，桌面宽度自动收回。如果中途**强杀**进程
（任务管理器结束、拔线），虚拟屏可能留在系统里 —— 重新跑一次 `DroidExtend.exe`，
点一下 `开始投屏` 再点 `停止` 就能收干净；也可以到「显示设置」里把它设为「断开连接」。

**Q：触摸没反应？**
A：先看手机 App 状态栏有没有显示延迟数字（说明隧道通了）。触摸走的是 `tcp:27184`，
重新插线后 `adb reverse` 会失效，点一下界面上的 `刷新` 会重建两条隧道。

**Q：换手机会不会要重新打包？**
A：不用。协议和分辨率都是运行时握手的，任何 Android 5.0+ 的 arm64/arm 手机都能装。

---

## 七、重新构建手机端（可选）

只有改了 `android/src/**` 里的 Java 代码才需要：

```
双击 构建手机端 APK.bat
```

它会用随包的 `tools/sdk` 里的 aapt2 / javac / d8 / apksigner 走完整条链路，
产物覆盖 `dist/DroidExtend.apk`。不需要装 Android Studio，也不需要 Gradle。

Windows 端的 exe 同理，改了 `host/**` 里的 Python 代码才需要重打（需要 `pyinstaller`）：

```powershell
cd "D:\dsh projects\droid-extend"
python -m PyInstaller --noconfirm --clean --onedir --windowed `
  --name DroidExtend --icon "D:\dsh projects\droid-extend\DroidExtend.ico" `
  --paths "D:\dsh projects\droid-extend\host" `
  --distpath "D:\dsh projects\droid-extend\tools\pybuild\out" `
  --workpath "D:\dsh projects\droid-extend\tools\pybuild\work" `
  --specpath "D:\dsh projects\droid-extend\tools\pybuild" `
  "D:\dsh projects\droid-extend\host\app.py"
# 然后把 out\DroidExtend\DroidExtend.exe 和 out\DroidExtend\_internal\ 复制到项目根目录
```

必须用 `--onedir` 而不是 `--onefile`：单文件版每次启动都要往 `%TEMP%` 自解压，
在受限环境下会直接报 `Could not create temporary directory!`。`--icon` 也要给绝对路径。

---

## 八、重新构建 Windows 安装包

装了 [Inno Setup 6](https://jrsoftware.org/isdl.php)（或者
`winget install --id JRSoftware.InnoSetup`）之后：

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" `
  "D:\dsh projects\droid-extend\tools\installer\DroidExtend.iss"
```

产物直接覆盖 `dist\DroidExtend-Setup-1.1.0.exe`（约 18 秒，lzma2/max）。

脚本在 `tools/installer/DroidExtend.iss`，要点：

* 只打包**运行期**需要的文件：`DroidExtend.exe`、`_internal\`、`tools\ffmpeg`、
  `tools\platform-tools`、`tools\vdd`、`tools\vdd_install.ps1`、`dist\DroidExtend.apk`。
  **`tools\sdk`（251 MB）和 `tools\vdd\*.zip` 不打包** —— 它们只在重建 APK 时用得上，
  正是它们把项目从 34 MB 撑到 391 MB。
* 装到 `%LOCALAPPDATA%\Programs\DroidExtend`、`PrivilegesRequired=lowest`：
  程序运行时要往 `tools\tmp` 写 adb 的临时文件，装进 `Program Files` 普通权限反而写不进去。
* 虚拟显示驱动属于系统级组件，安装包**不代劳**，留给程序界面上的按钮提权安装。
* 中文界面是拿英文 `Default.isl` 打底、在 `[Messages]` 里覆盖文案的
  （官方 `ChineseSimplified.isl` 的下载源当时都拿不到）；脚本文件必须存成**带 BOM 的 UTF-8**，
  否则 Inno 会按 ANSI 解析，中文全乱。
* 改脚本时注意：`[Setup]` 段用 `=`，而 `[Files] / [Icons] / [Run] / [Tasks]` 段的参数用 **`:`** ——
  写成 `Comment="…"` 会直接编译失败（`Unrecognized parameter name`）。

## 九.如果有用到其他作者的开源项目代码请联系我 我会在下一版写明 有侵权也及时联系我

**邮箱：1553008865@qq.com / lsw1553008865@gmail.com(备用)
