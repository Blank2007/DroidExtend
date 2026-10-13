---
name: Bug 报告 / Bug report
about: 报告一个可以复现的问题 / Report a reproducible problem
title: "[Bug] "
labels: bug
---

<!--
先看这一条：如果「安装虚拟显示器驱动」点了没反应，请先确认版本。
v1.4.0 及更早的安装包有这个已知问题，v1.4.1 已修复。
First check this one: if "Install virtual display driver" does nothing when clicked, check your version first.
Installers from v1.4.0 and earlier have this known problem; it is fixed in v1.4.1.
-->

## 现象 / What happens
<!-- 发生了什么，你期望发生什么
     What happened, and what did you expect to happen -->

## 复现步骤 / Steps to reproduce
1.
2.
3.

## 环境 / Environment
- DroidExtend 版本（程序标题栏里有）：
- DroidExtend version (it is in the program's title bar):
- Windows 版本（`winver`）：
- Windows version (`winver`):
- 手机型号 / Android 版本：
- Phone model / Android version:
- 数据线是原装还是普通线：
- Is the cable the original one or a generic one:
- 显卡型号（NVIDIA / AMD / Intel）：
- GPU model (NVIDIA / AMD / Intel):

## 日志 / Logs
<!--
Windows 端：`文件 → 打开数据目录（设置与日志）`，把 DroidExtend.log 贴上来。
手机端：设置里打开「把日志写到文件」，然后贴 /sdcard/Android/data/com.droidextend/files/DroidExtend.log。
手机端崩溃日志：adb logcat -s DroidExtend:I AndroidRuntime:E
Windows side: `File → Open data directory (settings and logs)`, then paste DroidExtend.log here.
Phone side: turn on "Write logs to file" in the settings, then paste /sdcard/Android/data/com.droidextend/files/DroidExtend.log.
Phone-side crash log: adb logcat -s DroidExtend:I AndroidRuntime:E
-->
```
在这里贴日志
Paste the log here
```
