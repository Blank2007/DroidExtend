"""Locate the portable toolchain that ships next to this program."""
import os
import shutil
import subprocess
import sys
import tempfile

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
# Everything the program writes lives in ONE folder: the directory the program
# itself sits in -- the one the user picked in the installer.  That covers
# settings.json, DroidExtend.log, the downloaded update, the apply script and
# its log, and the short-lived PowerShell scripts that need elevation.  Nothing
# goes to %TEMP%, %LOCALAPPDATA% or %APPDATA%, so "uninstall" can really mean
# "remove one folder", and a cleaned %TEMP% can never take the settings with it.
#
# %DROIDEXTEND_HOME% still overrides the folder, for portable runs and for the
# tests -- a test must never write into the real installation.
#
# 1.6.5 and earlier remembered a *moved* data folder in a pointer file under
# %LOCALAPPDATA%\DroidExtend\data_dir.txt.  That scattered the data, and a test
# that wrote that pointer sent the running program's settings into %TEMP% for a
# day.  The pointer is gone; migrate_old_data_dir() moves whatever it still
# points at back into the program folder and deletes it.

_DATA_DIR = None


def _usable(d):
    """True if d exists and we may write into it.

    Deliberately does NOT create the folder: a broken location must fall back to
    the program directory rather than be resurrected somewhere unexpected.
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
    """The folder holding settings.json, DroidExtend.log, updates and scratch files."""
    global _DATA_DIR
    if _DATA_DIR is not None and not refresh:
        return _DATA_DIR
    env = os.environ.get("DROIDEXTEND_HOME")
    if env and _usable(env):
        _DATA_DIR = os.path.abspath(env)
        return _DATA_DIR
    _DATA_DIR = ROOT
    return _DATA_DIR


def sub_dir(name):
    """A folder inside the data folder, created on demand."""
    d = os.path.join(data_dir(), name)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def set_data_dir(path):
    """Point this process at another data folder.  Tests and portable runs only.

    Nothing is written to disk: the folder is the program's own and is no longer
    movable from the UI, so there is no pointer to maintain.
    """
    global _DATA_DIR
    d = os.path.abspath(path)
    os.makedirs(d, exist_ok=True)
    _DATA_DIR = d
    return d


def forget_data_dir():
    """Go back to the program folder (undo set_data_dir for this process)."""
    global _DATA_DIR
    _DATA_DIR = None
    return True


def _old_pointer_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "DroidExtend", "data_dir.txt")


def migrate_old_data_dir(log=None):
    """Bring data back from a folder 1.6.5's pointer pointed at, then drop it.

    Returns the list of file names that were moved.
    """
    log = log or (lambda m: None)
    pointer = _old_pointer_path()
    moved = []
    try:
        with open(pointer, "r", encoding="utf-8-sig") as f:
            old = f.read().strip()
    except Exception:
        return moved
    target = data_dir()
    try:
        if old and os.path.isdir(old) and os.path.normcase(os.path.abspath(old)) != \
                os.path.normcase(target):
            for name in ("settings.json", "DroidExtend.log", "vdd_off.log",
                         "vdd_install.log"):
                src = os.path.join(old, name)
                dst = os.path.join(target, name)
                if not os.path.exists(src) or os.path.exists(dst):
                    continue
                try:
                    shutil.copy2(src, dst)
                    moved.append(name)
                except Exception as e:
                    log("搬回 %s 失败: %s" % (name, e))
            if moved:
                log("已把数据从旧目录 %s 搬回程序目录：%s" % (old, "、".join(moved)))
    finally:
        try:
            os.remove(pointer)
            log("已删除旧的数据目录指针 %s" % pointer)
        except OSError:
            pass
    return moved


def clean_old_temp(log=None):
    """Remove the scratch files that 1.6.5 and earlier left in %TEMP%.

    Those are the leftovers that make "everything in one folder" false: the old
    update folder and the PowerShell scripts written for elevation.
    """
    log = log or (lambda m: None)
    gone = 0
    try:
        tmp = tempfile.gettempdir()
    except Exception:
        return 0
    for name in ("droidextend_update", "droidextend_vdd_elevate.ps1",
                 "droidextend_vdd_restart.ps1", "droidextend_vdd_toggle.ps1"):
        p = os.path.join(tmp, name)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
                gone += 1
            elif os.path.exists(p):
                os.remove(p)
                gone += 1
        except OSError:
            pass
    if gone:
        log("已清掉 %d 个 TEMP 目录下的旧残留" % gone)
    return gone

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
