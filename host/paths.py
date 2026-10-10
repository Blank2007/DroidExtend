"""Locate the portable toolchain that ships next to this program."""
import os
import shutil
import subprocess
import sys

if getattr(sys, "frozen", False):
    # Packaged as DroidExtend.exe (PyInstaller).  __file__ then points inside the
    # temporary extraction dir, so the portable toolchain has to be anchored next
    # to the executable instead -- the .exe sits in the project root.
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
DIST = os.path.join(ROOT, "dist")
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".droidextend")

_CREATE_NO_WINDOW = 0x08000000
if sys.platform == "win32":
    def _no_window():
        return subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else _CREATE_NO_WINDOW
else:
    def _no_window():
        return 0


def popen(cmd, **kw):
    """subprocess.Popen that never flashes a console window."""
    kw.setdefault("creationflags", _no_window())
    kw.setdefault("stdin", subprocess.DEVNULL)
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kw.setdefault("startupinfo", si)
    return subprocess.Popen(cmd, **kw)


def _first(cands):
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def _on_path(name):
    return shutil.which(name)


def adb_exe():
    return _first([
        os.path.join(TOOLS, "platform-tools", "adb.exe"),
        _on_path("adb"),
        os.path.join(TOOLS, "sdk", "platform-tools", "adb.exe"),
    ])


def ffmpeg_exe():
    return _first([
        os.path.join(TOOLS, "ffmpeg", "bin", "ffmpeg.exe"),
        _on_path("ffmpeg"),
        os.path.join(os.path.expanduser("~"), ".dsh-wallpaper-engine", "ffmpeg", "ffmpeg.exe"),
    ])


def java_home():
    return _first([
        os.environ.get("JAVA_HOME", ""),
        os.path.join(TOOLS, "jdk"),
        os.path.join(os.path.expanduser("~"), "AppData", "Roaming", ".minecraft",
                     "runtime", "java-runtime-delta"),
    ])


def sdk_dir():
    d = os.path.join(TOOLS, "sdk")
    return d if os.path.isdir(d) else None


def apk_path():
    return _first([
        os.path.join(DIST, "DroidExtend.apk"),
        os.path.join(ROOT, "DroidExtend.apk"),
    ])
