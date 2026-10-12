"""Virtual Display Driver: detect it, install it, and know when it is ready.

The driver is VirtualDrivers/Virtual-Display-Driver 25.7.23 (MttVDD).  Its INF
root-enumerates a display adapter whose hardware id is `Root\\MttVDD`, and the
driver DLL reads its mode list from `C:\\VirtualDisplayDriver\\vdd_settings.xml`.
Installing it needs an elevated process and a signed catalog the machine trusts,
so the actual work lives in tools/vdd_install.ps1 and we only drive it from
here, surfacing progress and the UAC prompt honestly.
"""
import ctypes
import os
import re
import subprocess
import sys
import time

from paths import ROOT, TOOLS, data_dir, popen

INSTALL_SCRIPT = os.path.join(TOOLS, "vdd_install.ps1")
VDD_SRC = os.path.join(TOOLS, "vdd", "vdd25", "VirtualDisplayDriver")
VDD_NEF = os.path.join(TOOLS, "vdd", "nefcon", "x64", "nefconw.exe")
VDD_SETTINGS_DIR = r"C:\VirtualDisplayDriver"
HWID = r"Root\MttVDD"
CLASS_GUID = "4D36E968-E325-11CE-BFC1-08002BE10318"

# adapter description / device id fragments that mean "this is our virtual display"
HINTS = ("mttvdd", "virtual display driver", "parsec", "idd", "virtual display")


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def driver_files_present():
    return os.path.isdir(VDD_SETTINGS_DIR) and os.path.isfile(
        os.path.join(VDD_SETTINGS_DIR, "vdd_settings.xml"))


def stage_present():
    return os.path.isfile(os.path.join(VDD_SRC, "MttVDD.inf"))


def find_vdd_adapter(adapters=None):
    """Return the enum_adapters() entry for the virtual display, or None.

    Detection is by hardware id first (unambiguous) and by description second,
    because the adapter only appears in the display enum once its device node
    exists - a driver that is installed but has no device yet is invisible here.
    """
    if adapters is None:
        from win_display import enum_adapters
        adapters = enum_adapters()
    for a in adapters:
        ident = (a.get("id") or "").lower()
        if "mttvdd" in ident:
            return a
    for a in adapters:
        desc = (a.get("desc") or "").lower()
        if "virtual display" in desc and "gameviewer" not in desc:
            return a
    return None


def device_node_exists():
    """Ask PnP whether a ROOT\\MTTVDD node exists at all (works even when the
    adapter is not currently attached to the desktop)."""
    ps = ("$d = Get-PnpDevice -ErrorAction SilentlyContinue | "
          "Where-Object { $_.InstanceId -like 'ROOT\\MTTVDD*' }; "
          "if ($d) { $d | ForEach-Object { $_.InstanceId + '|' + $_.Status + '|' + $_.FriendlyName } }")
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=40,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if "|" in line:
            parts = line.split("|")
            if len(parts) >= 3:
                rows.append({"id": parts[0], "status": parts[1], "name": parts[2]})
    return rows


def status():
    """A dict describing where we are, for the GUI to render."""
    adm = is_admin()
    node = device_node_exists()
    adapter = find_vdd_adapter()
    if adapter is not None:
        state = "ready"
    elif node:
        state = "node"
    elif driver_files_present():
        state = "staged"
    elif stage_present():
        state = "download"
    else:
        state = "missing"
    return {
        "admin": adm,
        "state": state,          # missing | download | staged | node | ready
        "node": node,
        "adapter": adapter,
        "files": driver_files_present(),
    }


def _ps_single(s):
    """A PowerShell single-quoted literal."""
    return "'" + s.replace("'", "''") + "'"


def _ps_arg(s):
    """A PowerShell array element that survives Start-Process -ArgumentList.

    Start-Process joins the array with spaces and does NOT add quoting, so any
    element that may contain a space has to carry its own double quotes.
    """
    return "'\"" + s.replace("'", "''") + "\"'"


def _write_elevator(script, args):
    """Write the spaceless launcher that re-runs the install script elevated.

    Why a generated file instead of one big inline -Command string: the install
    path can contain spaces (and does, on the machine this was developed on), and
    nesting a quoted path through `powershell -Command "... Start-Process
    -ArgumentList '...'"` is where quoting bugs breed.  Writing the paths as
    PowerShell literals into a file makes the escaping trivial and inspectable.
    """
    import tempfile

    argv = ", ".join(_ps_arg(a) for a in args)
    body = (
        "$ErrorActionPreference = 'Continue'\r\n"
        "$p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru "
        "-WorkingDirectory %s -ArgumentList @(%s)\r\n"
        "exit $p.ExitCode\r\n" % (_ps_single(ROOT), argv)
    )
    path = os.path.join(tempfile.gettempdir(), "droidextend_vdd_elevate.ps1")
    # UTF-8 with BOM: Windows PowerShell 5.1 would otherwise read it as ANSI.
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(body)
    return path


def install(elevate=True, log=None):
    """Run the installer.  With elevate=True this shows one UAC prompt and
    returns (ok, output).  The caller must be prepared for the user to cancel.

    Every path is passed explicitly on the command line.  The script's own
    defaults derive from $PSScriptRoot and are only a fallback for a hand-run, so
    the installed copy no longer depends on where the project was developed.
    """
    log = log or (lambda m: None)
    if not stage_present():
        return False, "驱动文件缺失（tools\\vdd\\vdd25\\VirtualDisplayDriver）"
    if not os.path.isfile(INSTALL_SCRIPT):
        return False, "安装脚本缺失: %s" % INSTALL_SCRIPT
    if not os.path.isfile(VDD_NEF):
        return False, "nefcon 缺失: %s" % VDD_NEF

    # The log lives in the data directory, which is writable by definition and is
    # where the user is told to look.  An elevated child gets its own console, so
    # its stdout is lost - this file is the only channel back.
    log_path = os.path.join(data_dir(), "vdd_install.log")
    try:
        if os.path.exists(log_path):
            os.remove(log_path)
    except OSError:
        pass

    args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", INSTALL_SCRIPT,
            "-Src", VDD_SRC, "-Nef", VDD_NEF,
            "-Dest", VDD_SETTINGS_DIR, "-Log", log_path]

    if is_admin() or not elevate:
        cmd = ["powershell.exe"] + args
    else:
        try:
            boot = _write_elevator(INSTALL_SCRIPT, args)
        except OSError as e:
            return False, "无法准备提权脚本: %s" % e
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", boot]
        log("正在请求管理员权限（请在弹窗中点击“是”）…")

    log("运行: " + " ".join(cmd))
    try:
        p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = p.communicate(timeout=600)
        text = (out or b"").decode("utf-8", "replace")
        code = p.returncode
    except subprocess.TimeoutExpired:
        return False, "安装超时"
    except Exception as e:
        return False, "安装启动失败: %s" % e

    if os.path.isfile(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            pass

    log("安装脚本退出码 = %s" % code)
    if "canceled by the user" in text or code == 1223:
        return False, "UAC 被取消 —— 需要点“是”才能安装虚拟显示器驱动。\n\n" + text
    if code == 2:
        return False, "安装脚本检测到没有管理员权限。\n\n" + text
    if code == 3:
        return False, ("安装脚本找不到驱动文件或 nefcon。\n"
                       "这通常意味着安装不完整，重装一次 DroidExtend 即可。\n\n" + text)
    ok = code == 0 and "=== VDD install done ===" in text
    return ok, text


def settings_path():
    return os.path.join(VDD_SETTINGS_DIR, "vdd_settings.xml")


def add_resolution(width, height, hz=60):
    """List a resolution in the driver's mode table if it is not there yet.

    Returns True when the file changed, False when the size was already listed,
    and None when we could not touch it at all (no file, or read-only).

    Why we can do this without elevating: the installer creates
    C:\\VirtualDisplayDriver for the user who ran it, so the settings file stays
    writable.  The driver only reads it while its device node is starting, so a
    new entry goes live after the next reboot - or immediately after
    「安装虚拟显示器驱动」, which rewrites the file and re-creates the node.
    """
    path = settings_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    if re.search(r"<width>\s*%d\s*</width>\s*<height>\s*%d\s*</height>" % (int(width), int(height)),
                 text):
        return False
    if "</resolutions>" not in text:
        return None
    row = ("        <resolution><width>%d</width><height>%d</height>"
           "<refresh_rate>%d</refresh_rate></resolution>\n" % (int(width), int(height), int(hz)))
    text = text.replace("</resolutions>", row + "    </resolutions>", 1)
    try:
        tmp = path + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        return None
    return True


def summary_line(st):
    return {
        "missing": "未准备驱动文件",
        "download": "驱动文件就绪，尚未安装",
        "staged": "已复制到 C:\\VirtualDisplayDriver，等待安装设备",
        "node": "设备节点存在，但屏幕未激活",
        "ready": "虚拟显示器已就绪",
    }.get(st["state"], st["state"])
