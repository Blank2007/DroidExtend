# 参与开发 / Contributing

## 这是个什么项目 / What this project is

把安卓手机或平板用**数据线**变成 Windows 的第二块显示器，支持触摸。
没有无线投屏、没有虚拟网卡、不依赖厂商协议——就是一根线。
Turn an Android phone or tablet into a second Windows monitor over a **USB cable**, with touch support.
No wireless casting, no virtual network adapter, no vendor protocol — just one cable.

仓库里只有源码。ffmpeg、adb、Android SDK、虚拟显示驱动这些二进制要自己按
README 的「从源码跑」一节放好。
The repository contains source code only. The binaries — ffmpeg, adb, the Android SDK and the virtual display driver — must be put in place by yourself, following the "Running from source" section of the README.

## 提 Issue / Filing an issue

用模板。**日志一定要贴**，没有日志的问题基本没法查：
Use the templates. **Always attach logs** — an issue without logs is basically impossible to investigate:

- Windows 端：程序里 `文件 → 打开数据目录（设置与日志）`，`DroidExtend.log`
- Windows side: in the program, `File → Open data directory (settings and logs)`, then `DroidExtend.log`
- 手机端：设置里打开「把日志写到文件」，再看
  `/sdcard/Android/data/com.droidextend/files/DroidExtend.log`
- Phone side: turn on "Write logs to file" in the settings, then look at `/sdcard/Android/data/com.droidextend/files/DroidExtend.log`

## 改代码 / Changing the code

- 环境：Windows + Python 3.11+（开发用的是 `C:\Python314\python.exe`），
  手机端只需要一个 JDK 17+（`python android/build.py` 会自己找）。
- Environment: Windows + Python 3.11+ (development uses `C:\Python314\python.exe`); the phone side only needs a JDK 17+ (`python android/build.py` finds it on its own).
- 动手前先 `python -m compileall host tools`。
- Run `python -m compileall host tools` before you start.
- 提交信息写清楚**改了什么、为什么**，不要只写 "update"。
- Write commit messages that make **what changed and why** clear; do not just write "update".

### 几条硬规矩 / A few hard rules

1. **不要写死自己机器的路径。** 脚本里用
   `REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`
   推导，PowerShell 用 `$PSScriptRoot`。这条是有代价的——issue #1 的
   P0 就是 `tools/vdd_install.ps1` 里写死了开发机的路径，导致 v1.1.0 到
   v1.4.0 四个版本的「安装虚拟显示器驱动」对所有用户都失效。
1. **Never hard-code paths from your own machine.** Derive them in scripts with
   `REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`,
   and use `$PSScriptRoot` in PowerShell. This rule has a price: the P0 in
   issue #1 was a developer-machine path hard-coded in `tools/vdd_install.ps1`,
   which broke "Install virtual display driver" for every user across all four
   releases from v1.1.0 to v1.4.0.
2. **文档和日志要中英双份。** 程序界面和**代码注释**保持中文；但 README、开发日志、
   release notes 和任何给别人看的说明，都必须有英文版。中文那份照写，英文版跟着走
   （`README.md` ↔ `README.en.md`，`开发日志.md` ↔ `CHANGELOG.en.md`）。
   文档里引用界面上的中文按钮或提示时，保留中文并在括号里给出英文含义。
2. **Documentation and logs come in both languages.** The program's UI and its
   **code comments** stay Chinese; but the README, the development log, release
   notes and any explanation meant for other people must also exist in English.
   Write the Chinese as usual and keep the English in step
   (`README.md` ↔ `README.en.md`, `开发日志.md` ↔ `CHANGELOG.en.md`).
   When a document quotes a Chinese button label or message from the UI, keep the
   Chinese and add the English meaning in parentheses.
3. **用户能看到的行为变了，就写进 `开发日志.md`。** README 不记版本改动。
3. **If user-visible behaviour changes, write it into `开发日志.md`.** The README does not record version changes.
4. **发布走 `python tools/release.py --bump patch --notes <文件>`。**
   它负责改版本号、提交、打 tag、推、建 release、传安装包。不要手动强推。
4. **Releases go through `python tools/release.py --bump patch --notes <文件>`.**
   It bumps the version, commits, tags, pushes, creates the release and uploads
   the installer. Do not force-push by hand.

## 发布检查单 / Release checklist

- [ ] `python -m compileall host tools` 通过
- [ ] `python -m compileall host tools` passes
- [ ] `python android/build.py` 能出 `dist/DroidExtend.apk`
- [ ] `python android/build.py` produces `dist/DroidExtend.apk`
- [ ] PyInstaller 重建 `DroidExtend.exe`，拷到安装包会装的位置（**不要在工作区里跑**，
      会话沙箱会拒绝显示配置修改，见 README「已知问题」）
- [ ] Rebuild `DroidExtend.exe` with PyInstaller and copy it to the place the installer installs from (**do not run this inside the workspace** — the session sandbox rejects display-configuration changes; see "Known issues" in the README)
- [ ] Inno Setup 重建安装包，`.iss` 的 `AppVer` 已跟上
- [ ] Rebuild the installer with Inno Setup, with `AppVer` in the `.iss` updated to match
- [ ] 安装包和 APK 都装一遍真机验证
- [ ] Install both the installer and the APK on a real device to verify them
- [ ] `tools/release.py` 发版，release notes 里带上两个产物的 SHA256
- [ ] Cut the release with `tools/release.py`, including the SHA256 of both artifacts in the release notes
