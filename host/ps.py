"""Which PowerShell can this machine actually run?

Every driver install, device toggle and USB-mode hint goes through
`powershell.exe`.  Stripped-down Windows 10 images ("纯净版", LTSC builds with
the component removed, machines where the whole PowerShell folder is missing)
ship without a working one: the process either cannot start or dies with
0xFFFF0000 (4294901760) before a single line of the script runs, and the user
gets "驱动安装未成功，详见日志" with an empty log.

So: probe once, cache the answer, and fall back to PowerShell 7 (pwsh) when the
built-in one is gone.  Callers pass the same arguments they always did; only
the executable behind them changes.
"""

import os
import subprocess

import i18n

# Cheap, and it proves the engine runs -- a corrupted install exits non-zero or
# prints nothing at all.
_PROBE = "$PSVersionTable.PSVersion.Major"

_CACHE = None
_PROBED = False
# The probe already answers "which version", so remember that instead of paying
# another 0.36 s process start for version().  Measured on this machine: 0.36 s
# for the first probe, ~0 for everything after.
_VERSION = ""


def _candidates():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    yield os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    yield "powershell.exe"
    yield "pwsh.exe"
    yield "pwsh"


def _probe(exe):
    """Run the version probe.  Empty string means "not usable"."""
    if os.path.isabs(exe) and not os.path.exists(exe):
        return ""
    try:
        p = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", _PROBE],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return ""
    if p.returncode != 0:
        return ""
    return (p.stdout or b"").decode("utf-8", "replace").strip()


def exe(refresh=False):
    """Path (or bare name) of a PowerShell that runs here, or None."""
    global _CACHE, _PROBED, _VERSION
    if refresh or not _PROBED:
        _PROBED = True
        _CACHE = None
        _VERSION = ""
        for cand in _candidates():
            v = _probe(cand)
            if v:
                _CACHE = cand
                _VERSION = v
                break
    return _CACHE


def version(refresh=False):
    """Return the major version ("5", "7") of the executable in use."""
    exe(refresh)
    return _VERSION


def available():
    return exe() is not None


def is_pwsh():
    """True when we are running on PowerShell 7 because 5.1 is unusable."""
    ex = exe() or ""
    return "pwsh" in os.path.basename(ex).lower()


def hint():
    """One line for the log when no PowerShell works at all."""
    return i18n.t(
        "这台机器上没有可用的 PowerShell（powershell.exe 启动失败，也没装 pwsh）。"
        "驱动安装、虚拟屏开关、USB 模式检查、程序自带更新都依赖它 —— "
        "请在管理员命令提示符里运行 sfc /scannow 与 "
        "DISM /Online /Cleanup-Image /RestoreHealth 修好组件，"
        "或安装 PowerShell 7（https://aka.ms/powershell）后再试。",
        "There is no usable PowerShell on this machine (powershell.exe fails to "
        "start and pwsh is not installed). Driver install, display toggling, the "
        "USB-mode check and the built-in updater all need it -- repair the "
        "component with sfc /scannow "
        "and DISM /Online /Cleanup-Image /RestoreHealth in an elevated command "
        "prompt, or install PowerShell 7 (https://aka.ms/powershell), then retry.")
