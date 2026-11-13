"""Windows 系统通知（屏幕右下角的 toast）——纯 stdlib，不引入第三方库。

为什么不用第三方库：这个程序必须"解压即用"，打包机上一个第三方依赖都没有。
Toast 走的是 WinRT，而 Python 里没有 WinRT 投影，所以真正弹通知的那几行交给
PowerShell 跑（和 vdd/adb/update 用的是同一套 ps.exe 探测），Python 这边只做两
件事：注册 AppUserModelId、拼脚本。

为什么还要一条不依赖托盘的通道：托盘图标只在"关闭窗口 = 最小化到托盘"时才
存在。1.9.0 的自动投屏常常是在窗口隐藏、没人看着的时候失败的，那种情况下没
有托盘就没有气泡可弹，于是 app._notify 的顺序是"有托盘用托盘气泡，没有就
用这里的 toast，再不行才退回 messagebox"。

失败一律不抛异常：通知发不出去不能连累投屏本身。
"""
import base64
import os
import subprocess
import sys
import winreg

import ps

# 通知里显示的发送者名字。AppUserModelId 注册在 HKCU 下，不需要管理员。
APPID = "DroidExtend.App"
DISPLAY_NAME = "DroidExtend"
_KEY = r"Software\Classes\AppUserModelId\%s" % APPID

# 标题和正文用 base64 从环境变量传过去：PowerShell 的单引号字符串里放不了
# 任意文本，而通知正文里带引号、& 、换行都很正常（错误信息里什么都有）。
_SCRIPT = r"""
$ErrorActionPreference = "Stop"
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$title = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:DX_TITLE_B64))
$body = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:DX_BODY_B64))
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastText02"><text id="1"></text><text id="2"></text></binding></visual></toast>')
$nodes = $xml.GetElementsByTagName("text")
$nodes.Item(0).AppendChild($xml.CreateTextNode($title)) | Out-Null
$nodes.Item(1).AppendChild($xml.CreateTextNode($body)) | Out-Null
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:DX_APPID).Show($toast)
Write-Output "SHOWN"
"""

_registered = None


def _icon_path():
    """通知上显示的小图标：就是程序自己。"""
    try:
        return sys.executable or ""
    except Exception:
        return ""


def register(log=None):
    """把这个程序的 AppUserModelId 告诉 Windows。只需一次，写在 HKCU 里。

    不注册也能弹（用的就是 PowerShell 自己的身份），但那样通知的发送者是
    "Windows PowerShell"，用户看不出是谁在说话。
    """
    global _registered
    if _registered is not None:
        return _registered
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, DISPLAY_NAME)
            icon = _icon_path()
            if icon:
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, icon)
        _registered = True
    except Exception as e:
        _registered = False
        if log:
            log("注册系统通知身份失败：%s（通知照样能弹，只是署名不对）" % e)
    return _registered


def available():
    """这台机器上能不能弹通知（要有一个能跑的 PowerShell）。"""
    try:
        return ps.exe() is not None
    except Exception:
        return False


def _b64(text):
    return base64.b64encode((text or "").encode("utf-8")).decode("ascii")


def toast(title, text, log=None, timeout=20):
    """弹一条系统通知。成功返回 True；任何失败都只返回 False，不抛异常。"""
    exe = ps.exe() if ps else None
    if not exe:
        return False
    register(log)
    env = dict(os.environ)
    env["DX_APPID"] = APPID
    env["DX_TITLE_B64"] = _b64(title or DISPLAY_NAME)
    env["DX_BODY_B64"] = _b64(text or "")
    flags = 0
    try:
        flags = subprocess.CREATE_NO_WINDOW        # 别闪一个黑框
    except Exception:
        flags = 0
    try:
        p = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _SCRIPT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            env=env, creationflags=flags)
    except Exception as e:
        if log:
            log("系统通知没弹出来：%s" % e)
        return False
    if p.returncode == 0 and b"SHOWN" in (p.stdout or b""):
        return True
    if log:
        try:
            err = (p.stderr or b"").decode("mbcs", "replace").strip()[:300]
        except Exception:
            err = ""
        log("系统通知没弹出来（退出码 %s）%s" % (p.returncode, ("：" + err) if err else ""))
    return False
