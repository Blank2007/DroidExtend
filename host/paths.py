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

# ---------------------------------------------------------------- data folder
# settings.json and DroidExtend.log live in ONE folder, so "send me your log"
# is a single instruction and nothing is hidden in the user profile.  The
# folder is the program's own directory by default -- the installer's target
# directory, or wherever the portable copy was unzipped -- and the user can
# move it from 工具 -> 更改数据目录.
#
# The pointer that remembers a moved folder cannot live in the folder itself,
# so it is one line under %LOCALAPPDATA%\DroidExtend\.
_POINTER_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "DroidExtend")
_POINTER = os.path.join(_POINTER_DIR, "data_dir.txt")

_DATA_DIR = None


def _usable(d):
    """True if d exists and we may write into it.

    Deliberately does NOT create the folder: a remembered location that has
    been deleted or unplugged must fall back to the program directory rather
    than be silently resurrected somewhere the user no longer expects.
    """
    if not d or not os.path.isdir(d):
        return False
    try:
        probe = os.path.join(d, ".de-write-test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("")
        os.remove(probe)
        return True
    except Exception:
        return False


def data_dir(refresh=False):
    """The folder that holds settings.json and DroidExtend.log.

    Order: %DROIDEXTEND_HOME% (portable builds and tests), then the remembered
    folder, then the program directory.  A location that has become unwritable
    is skipped rather than raising -- a broken setting must not stop the app.
    """
    global _DATA_DIR
    if _DATA_DIR is not None and not refresh:
        return _DATA_DIR
    env = os.environ.get("DROIDEXTEND_HOME")
    if _usable(env):
        _DATA_DIR = os.path.abspath(env)
        return _DATA_DIR
    remembered = ""
    try:
        with open(_POINTER, "r", encoding="utf-8") as f:
            remembered = f.read().strip()
    except Exception:
        remembered = ""
    if remembered and os.path.abspath(remembered) != os.path.abspath(ROOT) \
            and _usable(remembered):
        _DATA_DIR = os.path.abspath(remembered)
        return _DATA_DIR
    _DATA_DIR = ROOT
    return _DATA_DIR


def set_data_dir(path):
    """Remember a new data folder and use it from now on."""
    global _DATA_DIR
    d = os.path.abspath(path)
    os.makedirs(d, exist_ok=True)
    os.makedirs(_POINTER_DIR, exist_ok=True)
    with open(_POINTER, "w", encoding="utf-8") as f:
        f.write(d)
    _DATA_DIR = d
    return d

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
