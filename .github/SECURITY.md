# 安全策略 / Security Policy

## 报告漏洞 / Reporting a vulnerability

请用 GitHub 的 [私密漏洞报告](https://github.com/Blank2007/DroidExtend/security/advisories/new)，
不要开公开 issue。
Please use GitHub's [private vulnerability reporting](https://github.com/Blank2007/DroidExtend/security/advisories/new);
do not open a public issue.

## 这个程序在你机器上做了什么 / What this program does on your machine

写清楚这些不是免责声明，是为了让你知道该盯着哪里：
Writing this down is not a disclaimer — it is so that you know where to look:

- **安装虚拟显示驱动**：会调用 `nefcon` 装一个内核模式的显示驱动
  （MTT Virtual Display Driver），需要管理员权限。驱动装完常驻系统，
  在「设备管理器 → 显示适配器」里能看到。
- **Installing the virtual display driver**: it calls `nefcon` to install a kernel-mode display driver
  (MTT Virtual Display Driver) and requires administrator rights. Once installed the driver stays on the
  system and can be seen under "Device Manager → Display adapters".
- **改显示配置**：`SetDisplayConfig` 接上/摘掉虚拟屏那条路径。**只碰虚拟屏**，
  不会动你真实的外接显示器。程序退出时会摘掉。
- **Changing the display configuration**: `SetDisplayConfig` attaches/detaches that virtual-screen path.
  It **only touches the virtual screen** and never your real external monitors. It is detached when the program exits.
- **注入输入**：把手机上的触摸转成 `SendInput` 的鼠标事件打到 Windows。
  只在投屏进行中、且你正在手机上操作时发生。
- **Injecting input**: it turns touches on the phone into `SendInput` mouse events on Windows.
  This only happens while casting is running and while you are operating the phone.
- **ADB**：用随包的 `adb.exe` 建两条 `reverse` 隧道（视频 27183、触摸 27184），
  只会连你自己插着的那台设备。
- **ADB**: it uses the bundled `adb.exe` to open two `reverse` tunnels (video 27183, touch 27184),
  and only connects to the device you have plugged in yourself.
- **联网**：只访问 `api.github.com` 查最新版本号（可在菜单里关掉）。
  不上传任何东西，没有遥测。
- **Network**: it only contacts `api.github.com` to look up the latest version number (this can be turned off in the menu).
  It uploads nothing and there is no telemetry.
- **凭据**：程序本身不读也不存任何 GitHub 凭据。`tools/release.py`（开发用）
  会从环境变量或 `~/.config/droidextend/github.token` 读 token，
  只用在推送上，不写进 git 配置。
- **Credentials**: the program itself neither reads nor stores any GitHub credentials. `tools/release.py` (development only)
  reads a token from an environment variable or `~/.config/droidextend/github.token`,
  and only uses it for pushing; it is never written into the git configuration.
