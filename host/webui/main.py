"""Entry point of the pywebview shell.

Run it with pythonw.exe (no console) exactly like the old app.py; the only
difference is that the window is a WebView2 page with no system frame, and the
window behaviour comes from window.py.
"""
import ctypes
import os
import shutil
import sys
import threading
import time


def _boot_log():
    """Keep a traceback somewhere findable: a windowed build has no console."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        path = os.path.join(os.environ.get("TEMP") or os.path.expanduser("~"),
                            "DroidExtend-boot.log")
        f = open(path, "w", encoding="utf-8", buffering=1)
        f.write("DroidExtend boot: %s\n" % sys.executable)
        f.flush()
        # A windowed build starts with no stdout/stderr handles at all, and the
        # CLR and WebView2 both write to them; point them at the log file.
        try:
            os.dup2(f.fileno(), 1)
            os.dup2(f.fileno(), 2)
        except Exception:
            pass
    except Exception:
        return None
    sys.stdout = sys.stderr = f

    def hook(kind, value, tb):
        import traceback
        f.write("".join(traceback.format_exception(kind, value, tb)))
        f.flush()
        raise SystemExit(1)

    sys.excepthook = hook
    return f


_BOOT = _boot_log()


def _trace(msg):
    if _BOOT is not None:
        try:
            _BOOT.write("%s\n" % (msg,))
        except Exception:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.dirname(HERE)
if HOST not in sys.path:
    sys.path.insert(0, HOST)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import window as fx                                                        # noqa: E402

_trace("window imported")
fx.dpi_aware()
_trace("dpi aware")

def _no_space_dir():
    """A writable folder whose path has no spaces; "" when there is none."""
    for key in ("LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "TEMP"):
        base = os.environ.get(key)
        if base and " " not in base:
            return os.path.join(base, "DroidExtend", "clr")
    return ""


def _clr_assembly_dir(pythonnet_path):
    """Folder pythonnet should treat as home, so the CLR can load its runtime.

    clr_loader hands Python.Runtime.dll's path to .NET Framework and the load
    fails when that path contains a space (8.3 aliases do not help; .NET
    normalises them back).  So when the build sits under "D:\\dsh projects\\..."
    or the installer's "C:\\Program Files\\DroidExtend", the runtime directory is
    copied to a space-free folder and pythonnet is pointed at that copy.
    """
    folder = os.path.dirname(os.path.abspath(pythonnet_path))
    dll = os.path.join(folder, "runtime", "Python.Runtime.dll")
    if " " not in folder or not os.path.isfile(dll):
        return folder
    target = _no_space_dir()
    if not target:
        return folder
    try:
        dst = os.path.join(target, "runtime")
        if not os.path.isdir(dst):
            os.makedirs(dst)
        dst_dll = os.path.join(dst, "Python.Runtime.dll")
        if (not os.path.isfile(dst_dll)
                or os.path.getsize(dst_dll) != os.path.getsize(dll)):
            shutil.copy2(dll, dst_dll)
        _trace("clr copy: %s" % (dst_dll,))
        return target
    except Exception as exc:
        _trace("clr copy failed %r" % (exc,))
        return folder


def _fix_webview_dlls():
    """Point pywebview's native interop DLLs at a space-free copy of its lib folder.

    .NET refuses to load them from a path containing a space ("attempt to load an
    assembly from a network location"), and both the repository checkout and the
    installer's default folder have one.
    """
    try:
        from webview import util as _util
    except Exception:
        return
    lib = os.path.join(os.path.dirname(os.path.abspath(_util.__file__)), "lib")
    if " " not in lib or not os.path.isdir(lib):
        return
    target = _no_space_dir()
    if not target:
        return
    dst = os.path.join(target, "webview-lib")
    try:
        core = os.path.join(lib, "Microsoft.Web.WebView2.Core.dll")
        if not os.path.isfile(os.path.join(dst, "Microsoft.Web.WebView2.Core.dll")):
            if os.path.isdir(dst):
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(lib, dst)
        _trace("webview lib: %s" % (dst,))
    except Exception as exc:
        _trace("webview lib failed %r" % (exc,))
        return

    def _path(name):
        first = os.path.join(dst, name)
        if os.path.exists(first):
            return first
        second = os.path.join(dst, "runtimes", name, "native")
        if os.path.exists(second):
            return second
        return os.path.join(lib, name)

    _util.interop_dll_path = _path


_CLR_CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <startup useLegacyV2RuntimeActivationPolicy="true">
    <supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8" />
  </startup>
  <runtime>
    <loadFromRemoteSources enabled="true" />
  </runtime>
</configuration>
"""


def _clr_config(home):
    """AppDomain config that lets .NET load assemblies from paths it distrusts.

    pywebview's WebView2 interop DLLs live next to the executable, and when that
    folder contains a space .NET refuses them ("attempt to load an assembly from
    a network location").  loadFromRemoteSources is the documented switch.
    """
    path = os.path.join(home, "DroidExtend.exe.config")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(_CLR_CONFIG)
        if " " not in path:
            return path
    except Exception as exc:
        _trace("clr config failed %r" % (exc,))
    return None


def _load_clr():
    """Load pythonnet by hand, through a path .NET can actually open.

    pythonnet resolves Python.Runtime.dll from its own __file__; clr_loader then
    hands that path to .NET Framework, which fails on paths containing a space
    ("D:\\dsh projects\\...") with

        Failed to resolve Python.Runtime.Loader.Initialize

    and WebView2 cannot start until the CLR is up.  Pointing __file__ at the 8.3
    short form of the same folder fixes it, and the installer's default folder
    ("C:\\Program Files\\DroidExtend") needs it too.
    """
    try:
        import pythonnet
    except Exception:
        return
    try:
        home = _clr_assembly_dir(pythonnet.__file__)
        pythonnet.__file__ = os.path.join(home, "__init__.py")
        _trace("clr: %s" % (pythonnet.__file__,))
        config = _clr_config(home)
        if config:
            _trace("clr config: %s" % (config,))
            pythonnet.load("netfx", config_file=config)
        else:
            pythonnet.load()
        _trace("clr ok")
    except Exception as exc:
        _trace("clr fail %r" % (exc,))
        ctypes.windll.user32.MessageBoxW(
            None,
            "无法初始化 .NET 运行时，界面起不来。\n\n"
            "%r\n\n请把程序放到不含空格的目录，或安装 .NET Framework 4.8。"
            % (exc,),
            "DroidExtend", 0x10)
        raise SystemExit(1)


_load_clr()

try:
    import webview                                                         # noqa: E402
except Exception as exc:                                                   # pragma: no cover
    ctypes.windll.user32.MessageBoxW(
        None,
        "缺少 pywebview，界面起不来。请先装一次：\n\n"
        "    pip install pywebview\n\n(%s)" % exc,
        "DroidExtend", 0x10)
    raise SystemExit(1)

_trace("webview imported")
_fix_webview_dlls()
import core as core_mod                                                    # noqa: E402
from bridge import Bridge                                                  # noqa: E402
_trace("core imported")


# WebView2 is not part of Windows 10 by default -- Win11 and updated Win10 boxes
# have it, a fresh Win10 does not.  Without it pywebview cannot create the window
# at all, and a windowed build would just vanish, so ask before it gets that far.
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
_WEBV2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _webview2_installed():
    """Look for the Evergreen runtime.  Unknown answers count as installed."""
    try:
        import winreg
    except Exception:
        return True
    sep = chr(92)
    roots = [
        (winreg.HKEY_LOCAL_MACHINE,
         "SOFTWARE" + sep + "WOW6432Node" + sep + "Microsoft" + sep
         + "EdgeUpdate" + sep + "Clients" + sep + _WEBV2_CLIENT),
        (winreg.HKEY_LOCAL_MACHINE,
         "SOFTWARE" + sep + "Microsoft" + sep + "EdgeUpdate" + sep
         + "Clients" + sep + _WEBV2_CLIENT),
        (winreg.HKEY_CURRENT_USER,
         "Software" + sep + "Microsoft" + sep + "EdgeUpdate" + sep
         + "Clients" + sep + _WEBV2_CLIENT),
    ]
    found = False
    for root, path in roots:
        try:
            with winreg.OpenKey(root, path) as k:
                pv, _ = winreg.QueryValueEx(k, "pv")
        except OSError:
            continue
        found = True
        if pv and pv != "0.0.0.0":
            return True
    # Entries exist but every version is 0.0.0.0 -> not really installed.
    return False if found else True


def _need_webview2():
    """Say what is missing, offer the download page, then stop."""
    msg = ("这台机器上没有 Microsoft Edge WebView2 运行时，界面起不来。\n\n"
           "Win11 一般自带；Win10 需要装一次（很小，一次就好）。\n\n"
           "点「确定」打开微软的下载页。")
    _trace("webview2 missing")
    try:
        if ctypes.windll.user32.MessageBoxW(None, msg, "DroidExtend", 0x01) == 1:
            os.startfile(WEBVIEW2_URL)
    except Exception:
        pass
    raise SystemExit(1)


def _web_dir():
    """index.html lives next to the sources, or inside the PyInstaller bundle."""
    here = [os.path.join(HERE, "web")]
    frozen = [os.path.join(getattr(sys, "_MEIPASS", ""), "web"),
              os.path.join(os.path.dirname(sys.executable), "web")]
    for cand in (frozen if getattr(sys, "frozen", False) else []) + here:
        if os.path.isfile(os.path.join(cand, "index.html")):
            return cand
    return here[0]


WEB = _web_dir()
TITLE = "DroidExtend"
DESIGN_W, DESIGN_H = 1000, 640
MIN_W, MIN_H = 880, 600

# What the autostart entry passes in (host/autostart.py writes the command).
MINIMIZED = "--minimized" in sys.argv     # keep the window out of the way
AUTOSTREAM = "--stream" in sys.argv       # start streaming by itself


def _scale():
    try:
        import win_display as wd
        return float(wd.set_dpi_aware()) / 96.0
    except Exception:
        return 1.0


def _geometry(settings, scale):
    """Where the window goes: the rectangle saved last time, in real pixels."""
    try:
        spec = str(settings.get("window") or "")
        if "x" in spec and "+" in spec:
            size, rest = spec.split("+", 1)
            _w, _h = size.split("x")
            _x, _y = rest.split("+")
            w, h = int(_w), int(_h)
            if 400 <= w <= 8000 and 300 <= h <= 6000:
                return (int(_x), int(_y), w, h)
    except Exception:
        pass
    w, h = int(DESIGN_W * scale), int(DESIGN_H * scale)
    return (None, None, w, h)


def _logical(px_size, scale):
    """create_window() takes CSS pixels, Win32 takes real ones."""
    return max(1, int(round(px_size / (scale or 1.0))))


def main():
    scale = _scale()
    if not _webview2_installed():
        _need_webview2()
    core = core_mod.Core()
    _trace("core ready")
    bridge = Bridge(core)
    x, y, w, h = _geometry(core.settings, scale)
    lw, lh = _logical(w, scale), _logical(h, scale)
    url = "file:///" + os.path.join(WEB, "index.html").replace("\\", "/")
    window = webview.create_window(
        TITLE, url=url,
        width=lw, height=lh,
        min_size=(MIN_W, MIN_H),
        frameless=True,
        easy_drag=False,
        background_color="#0E0F12",
        js_api=bridge,
    )

    def after_start(window):
        bridge._view = window
        time.sleep(1.2)
        hwnd = fx.find_hwnd(TITLE, os.getpid())
        if hwnd:
            win = fx.Win(hwnd, scale)
            bridge._win = win
            win.dark_frame()
            if x is not None:
                win.set_rect(x, y, w, h)
            win.round_corners()
        bridge.start_tray()
        core.log("界面已就绪")
        if MINIMIZED:
            # Long enough for the page to paint once, so the tray icon has a
            # window to hide; the stream keeps running either way.
            threading.Timer(2.0, bridge.hide_to_tray).start()
        if AUTOSTREAM:
            core.auto_stream()

    _trace("window created, starting event loop")
    try:
        webview.start(after_start, window, gui="edgechromium")
    except Exception as exc:                                               # pragma: no cover
        _trace("start failed %r" % (exc,))
        ctypes.windll.user32.MessageBoxW(
            None,
            "界面没能启动：\n\n%r\n\n"
            "如果反复出现，装一次 Microsoft Edge WebView2 运行时再试：\n%s"
            % (exc, WEBVIEW2_URL),
            "DroidExtend", 0x10)
        raise SystemExit(1)


if __name__ == "__main__":
    main()