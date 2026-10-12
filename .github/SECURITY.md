# 安全策略 / Security Policy

## 报告漏洞

请用 GitHub 的 [私密漏洞报告](https://github.com/Blank2007/DroidExtend/security/advisories/new)，
不要开公开 issue。

## 这个程序在你机器上做了什么

写清楚这些不是免责声明，是为了让你知道该盯着哪里：

- **安装虚拟显示驱动**：会调用 `nefcon` 装一个内核模式的显示驱动
  （MTT Virtual Display Driver），需要管理员权限。驱动装完常驻系统，
  在「设备管理器 → 显示适配器」里能看到。
- **改显示配置**：`SetDisplayConfig` 接上/摘掉虚拟屏那条路径。**只碰虚拟屏**，
  不会动你真实的外接显示器。程序退出时会摘掉。
- **注入输入**：把手机上的触摸转成 `SendInput` 的鼠标事件打到 Windows。
  只在投屏进行中、且你正在手机上操作时发生。
- **ADB**：用随包的 `adb.exe` 建两条 `reverse` 隧道（视频 27183、触摸 27184），
  只会连你自己插着的那台设备。
- **联网**：只访问 `api.github.com` 查最新版本号（可在菜单里关掉）。
  不上传任何东西，没有遥测。
- **凭据**：程序本身不读也不存任何 GitHub 凭据。`tools/release.py`（开发用）
  会从环境变量或 `~/.config/droidextend/github.token` 读 token，
  只用在推送上，不写进 git 配置。
