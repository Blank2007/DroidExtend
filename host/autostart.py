"""开机自动启动 —— 写当前用户的 Run 键，不需要管理员权限。

用 HKCU 而不是 HKLM：改 HKLM 要提权，而"我开机想用它"是每个用户自己的事。
卸载时也一并清掉（见 remove()）。
"""

import os
import sys

try:
    import winreg
except ImportError:                                    # 非 Windows
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DroidExtend"


def _host_dir():
    return os.path.dirname(os.path.abspath(__file__))


def command(extra=None):
    """该写进注册表的那条命令行。

    打包成 exe 时就是 exe 自己；从源码跑时用 pythonw.exe，否则每次开机都会
    闪一个黑框，而且关掉它程序也就没了。
    """
    args = list(extra or [])
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        parts = [exe] + args
        return '"%s"' % '" "'.join(parts)
    exe = sys.executable or "python.exe"
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.isfile(pyw):
        exe = pyw
    app = os.path.join(_host_dir(), "app.py")
    parts = [exe, app] + args
    return '"%s"' % '" "'.join(parts)


def read():
    """当前注册表里记的是什么（没记就是 None）。"""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, VALUE_NAME)
            return val or None
    except OSError:
        return None


def enabled():
    return read() is not None


def write(extra=None):
    """打开自启动，返回写进去的命令行。"""
    if winreg is None:
        raise OSError("这个系统没有注册表，无法设置开机启动")
    cmd = command(extra)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, cmd)
    return cmd


def remove():
    """关掉自启动。返回 True 表示确实删掉了一条。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, VALUE_NAME)
        return True
    except OSError:
        return False


def set_enabled(on, extra=None):
    """开关的统一入口，返回 (现在的状态, 说明)。"""
    if on:
        cmd = write(extra)
        return True, cmd
    remove()
    return False, ""
