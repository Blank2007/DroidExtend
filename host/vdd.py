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
import subprocess
import sys
import time

from paths import ROOT, TOOLS, popen

INSTALL_SCRIPT = os.path.join(TOOLS, "vdd_install.ps1")
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
    src = os.path.join(TOOLS, "vdd", "vdd25", "VirtualDisplayDriver")
    return os.path.isfile(os.path.join(src, "MttVDD.inf"))


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


def install(elevate=True, log=None):
    """Run the installer.  With elevate=True this shows one UAC prompt and
    returns (ok, output).  The caller must be prepared for the user to cancel.
    """
    log = log or (lambda m: None)
    if not stage_present():
        return False, "驱动文件缺失（tools\\vdd\\vdd25\\VirtualDisplayDriver）"
    if not os.path.isfile(INSTALL_SCRIPT):
        return False, "安装脚本缺失: %s" % INSTALL_SCRIPT

    log_path = os.path.join(TOOLS, "vdd", "install.log")
    try:
        if os.path.exists(log_path):
            os.remove(log_path)
    except OSError:
        pass

    # The project lives under a path with a space in it, so the elevated child
    # gets a copy of the script from %TEMP% (no spaces).  The script's own
    # defaults still point back at the real driver folder, and those are parsed
    # by PowerShell itself, so spaces there are harmless.
    script = INSTALL_SCRIPT
    if " " in INSTALL_SCRIPT:
        import shutil
        import tempfile
        script = os.path.join(tempfile.gettempdir(), "droidextend_vdd_install.ps1")
        try:
            shutil.copyfile(INSTALL_SCRIPT, script)
            log("已把安装脚本复制到无空格路径: %s" % script)
        except OSError as e:
            return False, "无法准备安装脚本: %s" % e

    inner = "-NoProfile -ExecutionPolicy Bypass -File " + script
    if is_admin() or not elevate:
        cmd = ["powershell.exe"] + inner.split()
    else:
        # Single UAC prompt; -PassThru + exit lets us recover the real code.
        ps = ("$p = Start-Process -FilePath powershell.exe -Verb RunAs -Wait -PassThru "
              "-WorkingDirectory '%s' -ArgumentList '%s'; exit $p.ExitCode" % (ROOT, inner))
        cmd = ["powershell.exe", "-NoProfile", "-Command", ps]
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
    ok = code == 0 and "=== VDD install done ===" in text
    return ok, text


def summary_line(st):
    return {
        "missing": "未准备驱动文件",
        "download": "驱动文件就绪，尚未安装",
        "staged": "已复制到 C:\\VirtualDisplayDriver，等待安装设备",
        "node": "设备节点存在，但屏幕未激活",
        "ready": "虚拟显示器已就绪",
    }.get(st["state"], st["state"])
