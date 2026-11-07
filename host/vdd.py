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

import i18n
import ps

from paths import ROOT, TOOLS, data_dir, popen
INSTALL_SCRIPT = os.path.join(TOOLS, "vdd_install.ps1")
VDD_SRC = os.path.join(TOOLS, "vdd", "vdd25", "VirtualDisplayDriver")
VDD_NEF = os.path.join(TOOLS, "vdd", "nefcon", "x64", "nefconw.exe")
VDD_SETTINGS_DIR = r"C:\VirtualDisplayDriver"
INSTALL_CMD = os.path.join(TOOLS, "vdd_install_cmd.bat")
HWID = r"Root\MttVDD"
CLASS_GUID = "4D36E968-E325-11CE-BFC1-08002BE10318"

# Exit codes that mean "the install script never ran", as opposed to "the script
# ran and said no".  vdd_install.ps1 only ever exits 0/2/3/4/5, so anything in
# this set came from the launcher or from PowerShell itself dying.  0xFFFF0000
# is what Start-Process hands back on a Windows image where powershell.exe is
# present but cannot start (seen on a stripped Windows 10 build); subprocess
# reports that as the signed value -65536.  0xC0000142 is DLL init failure.
_NO_SCRIPT = (0xFFFF0000, -65536, 0xC0000142, -1073741502)

# adapter description / device id fragments that mean "this is our virtual display"
HINTS = ("mttvdd", "virtual display driver", "parsec", "idd", "virtual display")

# The driver hands Windows ONE flat list of target modes and builds it as
# (every <resolution>) x (every <g_refresh_rate>).  That list lives in a fixed
# buffer: once the product outgrows it the driver logs
#     [WARNING] Input buffer is too small for target modes.
# and IddCxMonitorArrival fails with STATUS_NOT_SUPPORTED (-1073741637).  The
# adapter still comes up, but with an empty mode table -- so the screen never
# reaches the desktop and no ChangeDisplaySettingsEx / SetDisplayConfig call can
# put it there.  Upstream issue #254 (VirtualDrivers/Virtual-Display-Driver)
# has 108 combinations failing and the same file with 96 working.
# We stay well under that, because the failure mode is "the second screen is
# gone and nothing says why".  vdd_install.ps1 enforces the same ceiling.
MAX_TARGET_MODES = 90


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
    """Ask PnP whether our device node exists at all (works even when the
    adapter is not currently attached to the desktop).

    Matched by hardware id, not by instance id.  A root-enumerated device's
    instance id is `ROOT\\DISPLAY\\0001`, so the old filter
    `InstanceId -like 'ROOT\\MTTVDD*'` never matched anything and this function
    silently returned an empty list on every machine.  Nothing broke because
    `status()` prefers the adapter, but anything that trusted the node list saw
    "no device" on a working install.
    """
    script = ("Get-PnpDevice -Class Display -ErrorAction SilentlyContinue | ForEach-Object { "
          "$h = Get-PnpDeviceProperty -InstanceId $_.InstanceId "
          "-KeyName 'DEVPKEY_Device_HardwareIds' -ErrorAction SilentlyContinue; "
          "$j = ''; if ($h -and $h.Data) { $j = ($h.Data -join ';') }; "
          "if ($j -match 'MttVDD') { "
          "$_.InstanceId + '|' + $_.Status + '|' + $_.FriendlyName } }")
    try:
        out = subprocess.run([ps.exe() or "powershell.exe", "-NoProfile",
                              "-Command", script],
                             capture_output=True, text=True, timeout=60,
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


def _write_elevator(script, args, exe=None):
    """Write the spaceless launcher that re-runs the install script elevated.

    Why a generated file instead of one big inline -Command string: the install
    path can contain spaces (and does, on the machine this was developed on), and
    nesting a quoted path through a -Command string is where quoting bugs breed.
    Writing the paths as PowerShell literals into a file makes the escaping
    trivial and inspectable.

    exe is the PowerShell that host/ps.py probed and found working.  On a
    stripped Windows image the bare name powershell.exe resolves to a binary
    that dies at startup, so the launcher names the working one explicitly and
    turns "nothing happened" into exit 1223 (canceled) instead of a silent 0.
    """
    exe = exe or ps.exe() or "powershell.exe"
    argv = ", ".join(_ps_arg(a) for a in args)
    body = (
        "$ErrorActionPreference = 'Stop'\r\n"
        "try {\r\n"
        "  $p = Start-Process -FilePath %s -Verb RunAs -Wait -PassThru "
        "-WorkingDirectory %s -ArgumentList @(%s)\r\n"
        "} catch {\r\n"
        "  [Console]::Error.WriteLine($_.Exception.Message)\r\n"
        "  exit 1223\r\n"
        "}\r\n"
        "if ($null -eq $p) { exit 1223 }\r\n"
        "exit $p.ExitCode\r\n" % (_ps_single(exe), _ps_single(ROOT), argv)
    )
    path = os.path.join(data_dir(), "tmp", "droidextend_vdd_elevate.ps1")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        pass
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
        return False, i18n.t("驱动文件缺失（tools\\vdd\\vdd25\\VirtualDisplayDriver）",
                             "driver files missing "
                             "(tools\\vdd\\vdd25\\VirtualDisplayDriver)")
    if not os.path.isfile(INSTALL_SCRIPT):
        return False, i18n.t("安装脚本缺失: %s", "install script missing: %s") % INSTALL_SCRIPT
    if not os.path.isfile(VDD_NEF):
        return False, i18n.t("nefcon 缺失: %s", "nefcon missing: %s") % VDD_NEF

    # The log lives in the data directory, which is writable by definition and is
    # where the user is told to look.  An elevated child gets its own console, so
    # its stdout is lost - this file is the only channel back.
    log_path = os.path.join(data_dir(), "vdd_install.log")
    try:
        if os.path.exists(log_path):
            os.remove(log_path)
    except OSError:
        pass

    exe = ps.exe()
    if not exe:
        return False, ps.hint()

    args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", INSTALL_SCRIPT,
            "-Src", VDD_SRC, "-Nef", VDD_NEF,
            "-Dest", VDD_SETTINGS_DIR, "-Log", log_path]

    if is_admin() or not elevate:
        cmd = [exe] + args
    else:
        try:
            boot = _write_elevator(INSTALL_SCRIPT, args, exe)
        except OSError as e:
            return False, i18n.t("无法准备提权脚本: %s",
                                 "could not prepare the elevation script: %s") % e
        cmd = [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", boot]
        log("正在请求管理员权限（请在弹窗中点击“是”）…")

    log("运行: " + " ".join(cmd))
    try:
        p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = p.communicate(timeout=600)
        text = (out or b"").decode("utf-8", "replace")
        code = p.returncode
    except subprocess.TimeoutExpired:
        return False, i18n.t("安装超时", "the install timed out")
    except Exception as e:
        return False, i18n.t("安装启动失败: %s", "could not start the install: %s") % e

    if os.path.isfile(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            pass

    log("安装脚本退出码 = %s" % code)
    if "canceled by the user" in text or code == 1223:
        return False, i18n.t("UAC 被取消 —— 需要点“是”才能安装虚拟显示器驱动。\n\n",
                             "The UAC prompt was cancelled -- you have to click "
                             "\"Yes\" to install the virtual display driver.\n\n") + text
    if code == 2:
        return False, i18n.t("安装脚本检测到没有管理员权限。\n\n",
                             "The install script found no administrator "
                             "rights.\n\n") + text
    if code == 3:
        return False, (i18n.t("安装脚本找不到驱动文件或 nefcon。\n"
                              "这通常意味着安装不完整，重装一次 DroidExtend 即可。\n\n",
                              "The install script cannot find the driver files or "
                              "nefcon.\nThat usually means an incomplete install -- "
                              "installing DroidExtend again fixes it.\n\n") + text)

    done = "=== VDD install done ===" in text
    if not text.strip():
        # Not a single line was written, so the script never got going.  This is
        # the shape a stripped Windows 10 image produces: powershell.exe is
        # present but dies at startup with 0xFFFF0000 (4294901760), and the old
        # code could only say "see the log" about a log that was empty.
        return False, i18n.t(
            "安装脚本一行日志都没有留下（退出码 %s）—— 它根本没跑起来。\n\n"
            "常见的三种原因：\n"
            "  * UAC 窗口没有弹出来，或者被点了“否”；\n"
            "  * 安全软件（360、火绒、电脑管家…）拦下了提权的 PowerShell；\n"
            "  * 这台机器的 Windows PowerShell 组件缺失或损坏。\n\n%s",
            "The install script left no log at all (exit code %s) -- it never ran.\n\n"
            "The three usual reasons:\n"
            "  * the UAC prompt never appeared, or it was declined;\n"
            "  * a security suite (360, Huorong, PC Manager...) blocked the elevated "
            "PowerShell;\n"
            "  * this Windows image has a broken or missing PowerShell component.\n\n%s"
        ) % (code, ps.hint())

    ok = code == 0 and done
    if not ok and done and code in _NO_SCRIPT:
        # The launcher's exit code is unreliable when the child was elevated the
        # other side of UAC: Start-Process -Wait -PassThru hands back 0xFFFF0000
        # even though the script finished cleanly.  Trust what the machine shows
        # instead of the number.
        st = status()
        ok = st["state"] in ("ready", "node")

    if not ok:
        tail = "\n".join([l for l in text.strip().splitlines() if l.strip()][-12:])
        msg = i18n.t(
            "驱动安装未成功（退出码 %s）。日志末尾：\n\n%s\n",
            "The driver was not installed (exit code %s). End of the log:\n\n%s\n"
        ) % (code, tail)
        if code in _NO_SCRIPT:
            # The script wrote something and then the engine died, or the code
            # came from the elevated launcher rather than from the script.
            msg += "\n" + ps.hint()
        return False, msg
    return True, text


def _decode_text(raw):
    """Decode tool output that may be UTF-8 or the console's GBK."""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _read_text_file(path):
    try:
        with open(path, "rb") as f:
            return _decode_text(f.read())
    except OSError:
        return ""


def _shell_exec_elevated(bat, params, timeout=600):
    """Start *bat* elevated through ShellExecuteExW and wait for it.

    Why not "run powershell -Command Start-Process": the whole point of the
    fallback is that PowerShell cannot start on this machine.  ShellExecuteExW
    with the "runas" verb raises the UAC prompt itself, so nothing but Windows
    itself is involved.

    Returns (exit_code, error_text).  exit_code is None when the process could
    not be started at all; error_text then says why (1223 = the UAC prompt was
    declined).
    """
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_SHOWNORMAL = 1
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # A .bat cannot be handed to "runas" directly: Windows lets the shell open
    # it, the arguments are dropped on the way (measured: the script ran with
    # %1 empty and exited 1), and the install looks like it failed for no
    # reason.  Going through cmd.exe with the whole thing inside one pair of
    # quotes passes the four paths through intact.
    lp_file, lp_params = bat, params
    if bat.lower().endswith((".bat", ".cmd")):
        lp_file = os.environ.get("ComSpec") or r"C:\Windows\System32\cmd.exe"
        lp_params = '/c ""%s" %s"' % (bat, params)

    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"
    sei.lpFile = lp_file
    sei.lpParameters = lp_params
    sei.lpDirectory = os.path.dirname(bat)
    sei.nShow = SW_SHOWNORMAL
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.get_last_error()
        if err == 1223:
            return None, "cancelled"
        return None, "ShellExecuteExW failed, error %d" % err
    if not sei.hProcess:
        return None, "no process handle came back"
    try:
        rc = kernel32.WaitForSingleObject(sei.hProcess, int(timeout * 1000))
        if rc == 0x00000102:            # WAIT_TIMEOUT
            return None, "timed out after %d s" % timeout
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code)):
            return None, "could not read the exit code"
        return int(code.value), ""
    finally:
        kernel32.CloseHandle(sei.hProcess)


def install_cmd(log=None):
    """Install the driver with tools/vdd_install_cmd.bat -- no PowerShell.

    The .ps1 install needs Windows PowerShell, which on some stripped Windows
    images cannot start at all.  This path does the same job with certutil and
    nefcon only, and is offered to the user as "try the other way" after a
    failed normal install.  Returns (ok, output) exactly like install().
    """
    log = log or (lambda m: None)
    bat = INSTALL_CMD
    if not os.path.isfile(bat):
        return False, i18n.t("备用安装脚本缺失: %s", "fallback install script missing: %s") % bat
    if not stage_present():
        return False, i18n.t("驱动文件缺失（tools\\vdd\\vdd25\\VirtualDisplayDriver）",
                             "driver files missing "
                             "(tools\\vdd\\vdd25\\VirtualDisplayDriver)")
    if not os.path.isfile(VDD_NEF):
        return False, i18n.t("nefcon 缺失: %s", "nefcon missing: %s") % VDD_NEF

    log_path = os.path.join(data_dir(), "vdd_install_cmd.log")
    try:
        if os.path.exists(log_path):
            os.remove(log_path)
    except OSError:
        pass

    params = '"%s" "%s" "%s" "%s"' % (VDD_SRC, VDD_NEF, VDD_SETTINGS_DIR, log_path)
    if is_admin():
        log("备用方式运行: %s %s" % (bat, params))
        try:
            p = popen([bat, VDD_SRC, VDD_NEF, VDD_SETTINGS_DIR, log_path],
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out, _ = p.communicate(timeout=600)
            code = p.returncode
            console = _decode_text(out or b"")
        except subprocess.TimeoutExpired:
            return False, i18n.t("备用方式安装超时", "the fallback install timed out")
        except Exception as e:
            return False, i18n.t("备用方式启动失败: %s",
                                 "could not start the fallback install: %s") % e
    else:
        log("正在请求管理员权限（备用方式，请在弹窗中点击“是”）…")
        code, err = _shell_exec_elevated(bat, params, timeout=600)
        console = ""
        if code is None:
            if err == "cancelled":
                return False, i18n.t(
                    "UAC 被取消 —— 备用方式同样需要管理员权限。",
                    "The UAC prompt was cancelled -- the fallback needs "
                    "administrator rights too.")
            return False, i18n.t("备用方式启动失败: %s",
                                 "could not start the fallback install: %s") % err

    text = _read_text_file(log_path) or console

    log("备用方式退出码 = %s" % code)
    done = "=== VDD install (cmd fallback) done ===" in text
    if not text.strip():
        return False, i18n.t(
            "备用方式连日志都没写出来（退出码 %s）。",
            "The fallback wrote no log at all (exit code %s).") % code
    if code == 2 or "not running as administrator" in text:
        return False, i18n.t(
            "备用方式没有拿到管理员权限。",
            "The fallback did not get administrator rights.") + chr(10) * 2 + text
    ok = (code == 0) or done
    if not ok:
        tail = chr(10).join([l for l in text.strip().splitlines() if l.strip()][-12:])
        return False, i18n.t(
            "备用方式也没装成功（退出码 %s）。日志末尾：%s%s",
            "The fallback did not install it either (exit code %s). "
            "End of the log:%s%s") % (code, chr(10) * 2, tail)
    log("备用方式安装完成。")
    return True, text
def settings_path():
    return os.path.join(VDD_SETTINGS_DIR, "vdd_settings.xml")


def target_mode_count(text):
    """How many target modes the driver will build from this settings file.

    Every <resolution> is crossed with every <g_refresh_rate>, so it multiplies.
    Returns None when the file does not look like a settings file at all.
    """
    rows = re.findall(r"<resolution(?:\s[^>]*)?>.*?</resolution>", text, re.S)
    if "</resolutions>" not in text:
        return None
    rates = re.findall(r"<g_refresh_rate>\s*(\d+)\s*</g_refresh_rate>", text)
    return len(rows) * max(1, len(rates))


def mode_room(text):
    """(rows we may still add, modes per row)."""
    rates = re.findall(r"<g_refresh_rate>\s*\d+\s*</g_refresh_rate>", text)
    per = max(1, len(rates))
    rows = len(re.findall(r"<resolution(?:\s[^>]*)?>.*?</resolution>", text, re.S))
    return max(0, MAX_TARGET_MODES // per - rows), per


def declared_modes():
    """The modes the driver will build, read straight from its settings file.

    Returns ``(sizes, rates)`` where *sizes* is a sorted list of ``(w, h)`` and
    *rates* a sorted list of ints, or ``(None, None)`` when the file cannot be
    read at all.

    Why this exists: the form used to offer a fixed list of twenty-two sizes
    while the driver only builds a mode table from the eleven rows in this file.
    Every size outside that table is rejected by Windows with
    DISP_CHANGE_BADMODE, so picking one did nothing at all -- which is exactly
    what "改了分辨率还是没变化" was.  ``win_display.available_modes()`` answers
    the same question from the live display, but this one is readable before
    the virtual display has been attached.
    """
    path = settings_path()
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None, None
    sizes = set()
    for row in re.findall(r"<resolution(?:\s[^>]*)?>(.*?)</resolution>", text, re.S):
        w = re.search(r"<width>\s*(\d+)", row)
        h = re.search(r"<height>\s*(\d+)", row)
        if w and h:
            sizes.add((int(w.group(1)), int(h.group(1))))
    rates = sorted({int(r) for r in
                    re.findall(r"<g_refresh_rate>\s*(\d+)", text)})
    if not sizes:
        return None, None
    return sorted(sizes), rates


def add_resolution(width, height, hz=60):
    """List a resolution in the driver's mode table if it is not there yet.

    Returns True when the file changed, False when the size was already listed,
    and None when we could not touch it (no file, read-only, or no room left).

    Why we can do this without elevating: the installer creates
    C:\\VirtualDisplayDriver for the user who ran it, so the settings file stays
    writable.  The driver only reads it while its device node is starting, so a
    new entry goes live after the next reboot - or immediately after
    「安装虚拟显示器驱动」, which rewrites the file and re-creates the node.

    Room is finite and the failure is silent, so when the table is full we drop
    an entry we added ourselves (marked `auto="1"`) rather than let it overflow.
    Entries that came with the driver are never touched.
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

    room, per = mode_room(text)
    while room <= 0:
        old = re.search(r'[ \t]*<resolution\s+auto="1"[^>]*>.*?</resolution>[ \t]*\n', text, re.S)
        if not old:
            # Nothing of ours to drop and no room: leave the file alone.  Better
            # a missing size than a mode table the driver cannot hand to Windows.
            return None
        text = text[:old.start()] + text[old.end():]
        room, per = mode_room(text)

    row = ("        <resolution auto=\"1\"><width>%d</width><height>%d</height>"
           "<refresh_rate>%d</refresh_rate></resolution>\n" % (int(width), int(height), int(hz)))
    text = text.replace("</resolutions>", row + "    </resolutions>", 1)
    if (target_mode_count(text) or 0) > MAX_TARGET_MODES:
        return None
    try:
        tmp = path + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        return None
    return True


# Restart the node so the driver rebuilds its mode table from the file.
#
# Why disable/enable and not "pnputil /restart-device": pnputil answers 0 but
# leaves the node in a not-OK state for several seconds, and _RESTART_PS then
# reports "node not OK afterwards" for a restart that did happen.  A plain
# disable/enable pair is what the app already does when it turns the screen off
# and on again, and it is reliable.  Measured 2026-10-24 on MttVDD 25.7.23:
# the adapter is back 3.7 s later and a size appended to the file a moment
# before is in its mode table.
_RELOAD_PS = r"""$ErrorActionPreference = 'Continue'
$log = __LOG__
function Say($m) { if ($log) { try { Add-Content -LiteralPath $log -Value $m -Encoding UTF8 } catch { } } }
Say 'begin'
$ids = @()
Get-PnpDevice -Class Display -ErrorAction SilentlyContinue | ForEach-Object {
    $h = Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_HardwareIds' -ErrorAction SilentlyContinue
    $j = ''
    if ($h -and $h.Data) { $j = ($h.Data -join ';') }
    if ($j -match 'MttVDD') { $ids += $_.InstanceId }
}
if (-not $ids) { Say 'no-node'; exit 4 }
foreach ($id in $ids) {
    Say ('node ' + $id)
    Disable-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 700
    Enable-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction SilentlyContinue
}
Say 'done'
exit 0
"""


def reload_modes(want=None, log=None, timeout=30.0):
    """Restart the driver's device node so it re-reads vdd_settings.xml.

    Returns (ok, name); *name* is the adapter's new display name (a restart
    always hands out a new \\.\\DISPLAYnn number), or None when the adapter did
    not come back at all.

    Why this exists: the driver builds its whole target-mode list once, while
    its device node starts, so a size written into
    C:\\VirtualDisplayDriver\\vdd_settings.xml stays invisible until that node
    starts again.  The program used to tell the user to reinstall the driver or
    reboot for that -- a lot to ask for what is really one disable/enable pair,
    especially since the program is already elevated (--uac-admin) and needs no
    prompt of its own.

    *want* is (width, height); when given, the wait continues until that size is
    really enumerable, so the caller can go straight on to its own
    EnumDisplaySettingsEx / ChangeDisplaySettingsEx calls.
    """
    import win_display

    log = log or (lambda m: None)
    t0 = time.time()
    code, _text = _run_ps1(_RELOAD_PS, "reload", log=log)
    if code == 1223:
        return False, None
    if code == 4:
        log(i18n.t("找不到虚拟屏的设备节点，驱动可能没装好",
                   "cannot find the virtual display's device node -- the driver "
                   "may not be installed properly"))
        return False, None
    a = wait_adapter(timeout=timeout)
    if a is None:
        return False, None
    name = a.get("name")
    if want is None:
        log(i18n.t("驱动已重启，模式表重新读过了（%.1f 秒）",
                   "the driver was restarted and re-read its mode table (%.1f s)")
            % (time.time() - t0))
        return True, name
    w, h = int(want[0]), int(want[1])
    end = time.time() + timeout
    while time.time() < end:
        try:
            modes = win_display.available_modes(name)
        except Exception:
            modes = []
        if any(m[0] == w and m[1] == h for m in modes):
            log(i18n.t("驱动已重启，现在真的能给出 %dx%d（%.1f 秒）",
                       "the driver restarted and now really offers %dx%d (%.1f s)")
                % (w, h, time.time() - t0))
            return True, name
        a2 = find_vdd_adapter()
        if a2 and a2.get("name") and a2.get("name") != name:
            name = a2["name"]
        time.sleep(0.5)
    return False, name


# --- turning the monitor off for real ---------------------------------------
#
# The driver builds its monitors while its device node starts, from
# <monitors><count> in vdd_settings.xml.  Nothing at runtime can add or remove
# one: the shipped C:\VirtualDisplayDriver folder holds only the DLL, the INF,
# the catalog and this XML -- no control tool, no IOCTL.  So the only lever is
# that file plus a device restart, and a device restart needs admin.
#
# That asymmetry is what the two "off" modes in settings.vdd_off_mode are about.
# Both only apply when the program exits: a plain 停止 (Stop) detaches the
# screen from the desktop and stops there, which is what keeps stopping and
# starting again fast.
#   deep     -- count 0 + restart: nothing exists at boot, remote-desktop tools
#               cannot offer it, and one UAC prompt is spent each way
#   desktop  -- count 1, win_display.detach() only: the device exists but is
#               never part of the desktop, and no prompt is ever shown
#
# A crash in the middle of a stream can leave count at 1 with nobody to lower
# it; the app then degrades to detaching and says so, see app._vdd_off_work.

_RESTART_PS = r"""$ErrorActionPreference = 'Continue'
$log = __LOG__
function Say($m) { if ($log) { try { Add-Content -LiteralPath $log -Value $m -Encoding UTF8 } catch { } } }
Say 'begin'
$ids = @()
Get-PnpDevice -Class Display -ErrorAction SilentlyContinue | ForEach-Object {
    $h = Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_HardwareIds' -ErrorAction SilentlyContinue
    $j = ''
    if ($h -and $h.Data) { $j = ($h.Data -join ';') }
    if ($j -match 'MttVDD') { $ids += $_.InstanceId }
}
if (-not $ids) { Say 'no-node'; exit 4 }
$bad = 0
foreach ($id in $ids) {
    Say ('node ' + $id)
    $out = & pnputil /restart-device $id 2>&1
    Say ('pnputil rc=' + $LASTEXITCODE + ' ' + ($out -join ' '))
    if ($LASTEXITCODE -ne 0) {
        Say 'fallback disable/enable'
        Disable-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 700
        Enable-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction SilentlyContinue
    }
    $d = Get-PnpDevice -InstanceId $id -ErrorAction SilentlyContinue
    if (-not $d -or $d.Status -ne 'OK') { $bad = 1; Say 'node not OK afterwards' }
}
if ($bad) { Say 'failed'; exit 5 }
Say 'done'
exit 0
"""


def declared_count():
    """How many monitors the driver will create the next time it starts.

    None when the settings file is missing or has no <monitors><count>.
    """
    path = settings_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r"<monitors\b[^>]*>.*?<count>\s*(\d+)\s*</count>", text, re.S)
    return int(m.group(1)) if m else None


def set_count(n):
    """Rewrite <monitors><count> in the driver settings file.

    True = the file changed, False = it already said that, None = cannot write.
    Like add_resolution this needs no elevation: the installer leaves
    C:\\VirtualDisplayDriver owned by the user who ran it.  The driver does not
    notice until its device node restarts -- see restart_device().
    """
    n = int(n)
    path = settings_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    block = re.search(r"<monitors\b[^>]*>.*?</monitors>", text, re.S)
    if not block:
        return None
    old = block.group(0)
    new = re.sub(r"(<count>\s*)(\d+)(\s*</count>)", r"\g<1>%d\g<3>" % n, old, count=1)
    if new == old:
        return False if re.search(r"<count>\s*%d\s*</count>" % n, old) else None
    text = text[:block.start()] + new + text[block.end():]
    try:
        tmp = path + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        return None
    return True


def _run_ps1(body, tag, log=None, elevate=True, timeout=180):
    """Run a PowerShell body, elevated unless we already are.  (code, text).

    The elevated child gets its own console, so its stdout is thrown away; the
    body is therefore written to also append to data_dir()\\vdd_off.log and that
    file is what we read back.
    """
    import tempfile

    log = log or (lambda m: None)
    log_path = os.path.join(data_dir(), "vdd_off.log")
    try:
        if os.path.exists(log_path):
            os.remove(log_path)
    except OSError:
        pass
    script = os.path.join(data_dir(), "tmp", "droidextend_vdd_%s.ps1" % tag)
    try:
        os.makedirs(os.path.dirname(script), exist_ok=True)
    except OSError:
        pass
    try:
        # UTF-8 with BOM: Windows PowerShell 5.1 reads a BOM-less file as ANSI.
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write(body.replace("__LOG__", _ps_single(log_path)))
    except OSError as e:
        return 1, "cannot write %s: %s" % (script, e)

    ps_args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script]
    exe = ps.exe()
    if not exe:
        return 1, ps.hint()
    if elevate and not is_admin():
        try:
            launcher = _write_elevator(script, ps_args, exe)
        except OSError as e:
            return 1, "cannot write the elevation script: %s" % e
        cmd = [exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", launcher]
        log(i18n.t("正在请求管理员权限（请在弹窗中点击“是”）…",
                   "asking for administrator rights (please click \"Yes\")..."))
    else:
        cmd = [exe] + ps_args
    try:
        p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = p.communicate(timeout=timeout)
        code = p.returncode
        text = (out or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    except Exception as e:
        return 1, str(e)
    if os.path.isfile(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            pass
    return code, text


def restart_device(log=None):
    """Restart our device node so the driver re-reads vdd_settings.xml.

    Returns (ok, text).  Costs one UAC prompt unless we are already elevated;
    the caller has to expect the user to cancel.
    """
    code, text = _run_ps1(_RESTART_PS, "restart", log=log)
    if code == 1223:
        return False, i18n.t(
            "UAC 被取消 —— 设备没有重启，改过的设置要等下次开机才生效。",
            "The UAC prompt was cancelled -- the device was not restarted, so the "
            "change waits for the next boot.")
    if code == 0:
        # 判成功只看退出码。提权子进程写的 vdd_off.log 有时继承的是管理员 ACL，
        # 父进程读不到；以前拿 "done" in text 当判据，结果重启明明成功了却报失败。
        if not text:
            text = i18n.t(
                "设备已重启（退出码 0；提权进程的日志读不到，不影响结果）",
                "device restarted (exit code 0; the elevated child's log was not "
                "readable, which does not change the result)")
        return True, text
    return False, text


def set_count_and_restart(n, log=None):
    """Write <count> and restart the device so it takes effect now.

    Returns (ok, text).  `ok` is not just "the script exited 0": after the
    restart we actually wait for the adapter to show up (or to be gone), because
    that is the thing the caller cares about.
    """
    log = log or (lambda m: None)
    n = int(n)
    changed = set_count(n)
    if changed is None:
        return False, i18n.t("改不了 %s（文件不存在或不可写）",
                             "cannot write %s (missing or read-only)") % settings_path()
    log(i18n.t("驱动设置里的虚拟屏数量写成 %d", "monitor count in the driver settings set to %d")
        % n)
    ok, text = restart_device(log=log)
    if not ok:
        return False, text
    if n <= 0:
        gone, secs = wait_gone(timeout=15.0)
        if gone:
            log(i18n.t("虚拟屏已从系统里消失（%.1f 秒）", "the virtual display is gone (%.1f s)")
                % secs)
        return gone, text
    a = wait_adapter(timeout=25.0)
    return (a is not None), text


def wait_adapter(timeout=25.0):
    """Poll until our adapter is enumerable again (it disappears momentarily
    while the device restarts).  Returns the adapter row or None."""
    end = time.time() + timeout
    while time.time() < end:
        a = find_vdd_adapter()
        if a is not None:
            return a
        time.sleep(0.3)
    return None


def wait_gone(timeout=15.0):
    """Poll until our adapter is no longer enumerable.  Returns (gone, seconds)."""
    t0 = time.time()
    end = t0 + timeout
    while time.time() < end:
        if find_vdd_adapter() is None:
            return True, time.time() - t0
        time.sleep(0.3)
    return False, time.time() - t0


# ------------------------------------------------------- really off  (1.6.4)
#
# `<count>0</count>` does NOT work.  Measured 2026-10-16 on MttVDD 25.7.23:
# with the count written to 0 and the device node restarted, the adapter still
# enumerated, the PnP monitor was still Status OK, and the only visible change
# was the display number going up by one.  The driver treats 0 as an invalid
# count and falls back to a single monitor -- MttVDD.dll carries the string
# "Loading singular monitor (Monitor Count is not valid)".
#
# What does work is disabling the device node itself: the adapter stops
# enumerating, the monitor drops to Status "Unknown", and Windows keeps a
# disabled device disabled across reboots -- so the screen is not there at boot
# and remote-desktop software cannot offer it.  It costs one UAC prompt to turn
# off and one to turn back on, which is why this is a mode the user chooses
# rather than something we do behind their back.

_TOGGLE_PS = r"""$ErrorActionPreference = 'Continue'
$ids = @()
Get-PnpDevice -Class Display -ErrorAction SilentlyContinue | ForEach-Object {
    $h = Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_HardwareIds' -ErrorAction SilentlyContinue
    $j = ''
    if ($h -and $h.Data) { $j = ($h.Data -join ';') }
    if ($j -match 'MttVDD') { $ids += $_.InstanceId }
}
if (-not $ids) { Write-Output 'no-node'; exit 4 }
$bad = 0
foreach ($id in $ids) {
    if ('__ACTION__' -eq 'disable') {
        Disable-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction SilentlyContinue
    } else {
        Enable-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 500
    $d = Get-PnpDevice -InstanceId $id -ErrorAction SilentlyContinue
    if ('__ACTION__' -eq 'disable') {
        if ($d -and $d.Status -eq 'OK') { $bad++ }
    } else {
        if (-not $d -or $d.Status -ne 'OK') { $bad++ }
    }
    if ($d) { Write-Output ($id + '|' + $d.Status) } else { Write-Output ($id + '|gone') }
}
if ($bad -gt 0) { exit 5 }
exit 0
"""


def device_enabled():
    """True = the node is started, False = disabled, None = cannot tell."""
    rows = device_node_exists()
    if not rows:
        return None
    st = (rows[0].get("status") or "").strip().lower()
    if not st:
        return None
    return st == "ok"


def set_enabled(on, log=None):
    """Enable / disable our device node.  Returns (ok, text).

    Success is judged by the observable outcome, not by the exit code alone:
    after enabling the adapter has to enumerate again, after disabling it has to
    be gone.  Both are polled, because the driver takes a moment either way.
    """
    log = log or (lambda m: None)
    on = bool(on)
    body = _TOGGLE_PS.replace("__ACTION__", "enable" if on else "disable")
    code, text = _run_ps1(body, "toggle", log=log)
    if code == 1223:
        if on:
            return False, i18n.t(
                "UAC 被取消 —— 虚拟屏没有重新启用，投屏起不来。",
                "The UAC prompt was cancelled -- the virtual display was not switched "
                "back on, so streaming cannot start.")
        return False, i18n.t(
            "UAC 被取消 —— 虚拟屏没有关掉，它还在系统里。",
            "The UAC prompt was cancelled -- the virtual display is still there.")
    if code == 4:
        return False, i18n.t(
            "找不到虚拟屏的设备节点，可能驱动还没装好。",
            "Cannot find the virtual display's device node -- the driver may not be "
            "installed properly.")
    if code not in (0, 5):
        return False, text or i18n.t(
            "切换设备状态失败（退出码 %d）", "could not toggle the device (exit code %d)"
        ) % code
    if on:
        if wait_adapter(timeout=25.0) is None:
            return False, i18n.t(
                "设备已启用，但虚拟屏没有重新出现。",
                "The device was enabled but the virtual display did not come back.")
        log(i18n.t("虚拟屏已重新启用", "the virtual display is back on"))
        return True, text
    gone, secs = wait_gone(timeout=20.0)
    if not gone:
        # Disabling the node does not always make the adapter row vanish right
        # away: measured 2026-10-18 on a machine where the display had been
        # attached and the detach had failed, the adapter stayed enumerable for
        # the whole 20 s while the PnP node was already Status "Unknown".  A
        # disabled device does not start at boot, so the screen really is gone by
        # then -- calling that a flat failure is wrong and alarming.
        if device_enabled() is False:
            log(i18n.t(
                "虚拟屏的设备节点已禁用；这块屏要等系统松手（或下次开机）才从列表里消失",
                "the device node is disabled; the screen stays in the list until the "
                "system lets go of it, and is not there at the next boot"))
            return True, text
        return False, i18n.t(
            "设备已禁用，但虚拟屏还在（%.0f 秒内没消失）。",
            "The device was disabled but the virtual display is still there after "
            "%.0f s.") % secs
    log(i18n.t("虚拟屏已彻底关闭（%.1f 秒）：设备节点已禁用，重启也不会自己回来",
               "the virtual display is switched off for real (%.1f s): the device "
               "node is disabled, so it will not come back at the next boot") % secs)
    return True, text


def summary_line(st):
    return {
        "missing": i18n.t("未准备驱动文件", "driver files not staged"),
        "download": i18n.t("驱动文件就绪，尚未安装",
                           "driver files ready, not installed yet"),
        "staged": i18n.t("已复制到 C:\\VirtualDisplayDriver，等待安装设备",
                         "copied to C:\\VirtualDisplayDriver, waiting for the device"),
        "node": i18n.t("设备节点存在，但屏幕未激活",
                       "device node present, but the screen is not active"),
        "ready": i18n.t("虚拟显示器已就绪", "virtual display ready"),
    }.get(st["state"], st["state"])
