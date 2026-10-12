# 参与开发 / Contributing

## 这是个什么项目

把安卓手机或平板用**数据线**变成 Windows 的第二块显示器，支持触摸。
没有无线投屏、没有虚拟网卡、不依赖厂商协议——就是一根线。

仓库里只有源码。ffmpeg、adb、Android SDK、虚拟显示驱动这些二进制要自己按
README 的「从源码跑」一节放好。

## 提 Issue

用模板。**日志一定要贴**，没有日志的问题基本没法查：

- Windows 端：程序里 `文件 → 打开数据目录（设置与日志）`，`DroidExtend.log`
- 手机端：设置里打开「把日志写到文件」，再看
  `/sdcard/Android/data/com.droidextend/files/DroidExtend.log`

## 改代码

- 环境：Windows + Python 3.11+（开发用的是 `C:\Python314\python.exe`），
  手机端只需要一个 JDK 17+（`python android/build.py` 会自己找）。
- 动手前先 `python -m compileall host tools`。
- 提交信息写清楚**改了什么、为什么**，不要只写 "update"。

### 几条硬规矩

1. **不要写死自己机器的路径。** 脚本里用
   `REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`
   推导，PowerShell 用 `$PSScriptRoot`。这条是有代价的——issue #1 的
   P0 就是 `tools/vdd_install.ps1` 里写死了开发机的路径，导致 v1.1.0 到
   v1.4.0 四个版本的「安装虚拟显示器驱动」对所有用户都失效。
2. **中文注释和日志是刻意的。** 这个项目的用户界面、日志、文档都是中文，
   不要翻译成英文。
3. **用户能看到的行为变了，就写进 `开发日志.md`。** README 不记版本改动。
4. **发布走 `python tools/release.py --bump patch --notes <文件>`。**
   它负责改版本号、提交、打 tag、推、建 release、传安装包。不要手动强推。

## 发布检查单

- [ ] `python -m compileall host tools` 通过
- [ ] `python android/build.py` 能出 `dist/DroidExtend.apk`
- [ ] PyInstaller 重建 `DroidExtend.exe`，拷到安装包会装的位置（**不要在工作区里跑**，
      会话沙箱会拒绝显示配置修改，见 README「已知问题」）
- [ ] Inno Setup 重建安装包，`.iss` 的 `AppVer` 已跟上
- [ ] 安装包和 APK 都装一遍真机验证
- [ ] `tools/release.py` 发版，release notes 里带上两个产物的 SHA256
