"""开机自动启动。

1.6.9 起 `DroidExtend.exe` 带 `requireAdministrator` 清单（为了触摸注入和虚拟屏设备节点，
见 tools/release.py 的 --uac-admin）。这带来一个副作用：**HKCU 的 Run 键不再合适**——
登录时从 Run 键启动一个需要提权的程序，要么弹一次 UAC，要么被用户点「否」而静默不启动。

所以首选「计划任务 + 最高权限」：`schtasks /SC ONLOGON /RL HIGHEST` 在登录时直接以管理员
身份启动，不弹框。计划任务建不出来（被策略禁用等）才退回原来的 Run 键：那时的行为是
「弹一次 UAC」，虽然不理想，但至少不是「什么都没发生」。

`enabled()` / `remove()` 两边都认，卸载和开关都不会留下孤儿。
"""

import os
import subprocess
import sys

try:
    import winreg
except ImportError:                                    # 非 Windows
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DroidExtend"
TASK_NAME = "DroidExtend"
CNW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _host_dir():
    return os.path.dirname(os.path.abspath(__file__))


def command(extra=None):
    """该写进注册表 / 计划任务的那条命令行。

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


# --------------------------------------------------------------- 计划任务

def _run(args):
    try:
        p = subprocess.run(args, capture_output=True, creationflags=CNW)
        return p.returncode, (p.stdout + p.stderr).decode("mbcs", "replace")
    except Exception as e:                             # schtasks 不在、权限不够
        return -1, "%s: %s" % (type(e).__name__, e)


def task_exists():
    code, out = _run(["schtasks", "/Query", "/TN", TASK_NAME])
    return code == 0


def task_command():
    """计划任务里现在记的那条命令（没有就是 None）。"""
    code, out = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"])
    if code != 0:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("<Command>") and line.endswith("</Command>"):
            return line[len("<Command>"):-len("</Command>")]
    return None


def _write_task(extra=None):
    cmd = command(extra)
    # /TR takes the whole command line as one argument; schtasks on a Chinese
    # Windows reports errors in the OEM code page, hence mbcs above.
    code, out = _run(["schtasks", "/Create", "/F", "/SC", "ONLOGON",
                      "/TN", TASK_NAME, "/RL", "HIGHEST", "/TR", cmd])
    if code != 0:
        return None, out
    return cmd, out


# ---------------------------------------------------------------- Run 键

def read():
    """Run 键里记的是什么（没记就是 None）。"""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, VALUE_NAME)
            return val or None
    except OSError:
        return None


def _write_run(extra=None):
    if winreg is None:
        raise OSError("这个系统没有注册表，无法设置开机启动")
    cmd = command(extra)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, cmd)
    return cmd


def _remove_run():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, VALUE_NAME)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 对外

def enabled():
    return task_exists() or read() is not None


def write(extra=None):
    """打开自启动，返回写进去的命令行。"""
    cmd, out = _write_task(extra)
    if cmd:
        # 成功了就把可能残留的 Run 键清掉，避免两条都生效时起两个实例
        _remove_run()
        return cmd
    cmd = _write_run(extra)                            # 计划任务不可用时的退路
    return cmd


def remove():
    """关掉自启动。返回 True 表示确实删掉了一条。"""
    code, _ = _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
    gone = (code == 0)
    if _remove_run():
        gone = True
    return gone


def set_enabled(on, extra=None):
    """开关的统一入口，返回 (现在的状态, 说明)。"""
    if on:
        cmd = write(extra)
        return True, cmd
    remove()
    return False, ""
