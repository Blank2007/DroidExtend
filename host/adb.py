"""Thin wrapper around the bundled adb.exe."""
import os
import re
import subprocess
import time

import i18n

from paths import TOOLS, adb_exe, popen

# adb's forked daemon always opens "<temp>\adb.log" before it does anything
# else.  Inside a sandboxed shell the inherited TEMP is a directory the daemon
# may not be allowed to write, and then it dies with
#   F adb : main.cpp:54 cannot open C:\...\Temp\adb.log: Permission denied
# followed by "failed to check server version: cannot connect to daemon".
# Handing adb a temp directory we know we own removes that whole failure mode.
# TOOLS comes from paths.py so this still resolves once packaged as an .exe,
# where __file__ points inside the temporary extraction directory.
_ADB_TMP = os.path.join(TOOLS, "tmp")


def _env():
    e = dict(os.environ)
    try:
        os.makedirs(_ADB_TMP, exist_ok=True)
        e["TMP"] = _ADB_TMP
        e["TEMP"] = _ADB_TMP
    except Exception:
        pass
    return e


class AdbError(RuntimeError):
    pass


_DEVICE_STATES = {"device", "offline", "unauthorized", "bootloader", "recovery",
                  "sideload", "connecting", "authorizing", "unknown", "no", "host",
                  "rescue", "fastboot"}


class Adb(object):
    def __init__(self, exe=None):
        self.exe = exe or adb_exe()
        if not self.exe:
            raise AdbError(i18n.t("adb.exe 未找到（tools/platform-tools/adb.exe）",
                                  "adb.exe not found (tools/platform-tools/adb.exe)"))

    # -- low level ---------------------------------------------------------
    def run(self, *args, **kw):
        timeout = kw.pop("timeout", 20)
        cmd = [self.exe] + list(args)
        p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_env())
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            out, _ = p.communicate()
            raise AdbError(i18n.t("adb %s 超时", "adb %s timed out")
                           % (args[0] if args else ""))
        text = (out or b"").decode("utf-8", "replace")
        return p.returncode, text

    def run_async(self, *args):
        return popen([self.exe] + list(args), stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, env=_env())

    # -- api ---------------------------------------------------------------
    def version(self):
        try:
            rc, out = self.run("version", timeout=10)
            m = re.search(r"version ([\d.]+)", out)
            return m.group(1) if m else out.strip().splitlines()[0]
        except Exception as e:
            return "?"

    def start_server(self):
        return self.run("start-server", timeout=25)

    def kill_server(self):
        return self.run("kill-server", timeout=10)

    def devices(self):
        """[{'serial','state','model','product','device','transport'}]"""
        rc, out = self.run("devices", "-l", timeout=25)
        rows = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            parts = line.split()
            # stderr is merged into stdout, so the daemon's chatter and the
            # "List of devices attached" banner arrive here too - only a real
            # device line carries a known state in column two.
            if len(parts) < 2 or parts[1] not in _DEVICE_STATES:
                continue
            rec = {"serial": parts[0], "state": parts[1], "raw": line}
            for tok in parts[2:]:
                if ":" in tok:
                    k, v = tok.split(":", 1)
                    rec[k] = v
            rec["label"] = "%s  [%s]  %s" % (rec.get("model", rec["serial"]),
                                             rec["state"], rec["serial"])
            rows.append(rec)
        return rows

    def wait_for_device(self, serial, timeout=20):
        rc, out = self.run("-s", serial, "wait-for-device", timeout=timeout)
        return rc == 0

    def shell(self, serial, *args, **kw):
        return self.run(*(["-s", serial, "shell"] + list(args)), **kw)

    def reverse(self, serial, remote, local=None):
        local = local if local is not None else remote
        return self.run("-s", serial, "reverse", "tcp:%d" % remote, "tcp:%d" % local, timeout=20)

    def reverse_list(self, serial):
        return self.run("-s", serial, "reverse", "--list", timeout=15)

    def reverse_remove_all(self, serial):
        return self.run("-s", serial, "reverse", "--remove-all", timeout=15)

    def install(self, serial, apk, reinstall=True):
        args = ["-s", serial, "install"]
        if reinstall:
            args.append("-r")
        args.append(apk)
        rc, out = self.run(*args, timeout=180)
        ok = rc == 0 and "Success" in out
        if not ok and "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in out:
            # The APK that is on the phone was signed with a different key
            # (that happens whenever the build keystore is regenerated), and
            # Android refuses to replace it in place.  Drop the old package and
            # install cleanly rather than leaving the user with a stale app.
            self.uninstall(serial)
            rc, out = self.run(*args, timeout=180)
            ok = rc == 0 and "Success" in out
        return ok, out

    def uninstall(self, serial, package="com.droidextend"):
        rc, out = self.run("-s", serial, "uninstall", package, timeout=90)
        return rc == 0 and "Success" in out, out

    def is_installed(self, serial, package="com.droidextend"):
        rc, out = self.run("-s", serial, "shell", "pm", "path", package, timeout=20)
        return "package:" in out

    def start_app(self, serial, component="com.droidextend/.MainActivity"):
        return self.run("-s", serial, "shell", "am", "start", "-n", component, timeout=25)

    def force_stop(self, serial, package="com.droidextend"):
        return self.run("-s", serial, "shell", "am", "force-stop", package, timeout=20)

    def display_info(self, serial):
        rc, out = self.run("-s", serial, "shell", "wm", "size", timeout=20)
        return out.strip()

    def physical_size(self, serial):
        """The panel's real resolution as (width, height), or None.

        `wm size` prints an override as well as the physical size when they
        differ; the physical one is the panel, which is what we want the
        virtual screen to match.  The pair comes back in the panel's natural
        (usually portrait) orientation - the caller decides what to do with
        that.
        """
        rc, out = self.run("-s", serial, "shell", "wm", "size", timeout=20)
        if rc != 0:
            return None
        m = (re.search(r"Physical size:\s*(\d+)\s*x\s*(\d+)", out)
             or re.search(r"Override size:\s*(\d+)\s*x\s*(\d+)", out)
             or re.search(r"(\d+)\s*x\s*(\d+)", out))
        if not m:
            return None
        w, h = int(m.group(1)), int(m.group(2))
        if w <= 0 or h <= 0:
            return None
        return w, h


# ------------------------------------------------------------- diagnostics
# "Connected, but no picture" has a short list of dull causes, and every one of
# them is visible from here.  Naming the one that applies beats letting the user
# guess -- and by far the most common is the phone still sitting in charge-only
# mode, which no amount of driver surgery will ever fix.

_PORTS = (27183, 27184)      # video first, then touch; see server.py / touch.py
PORTS = _PORTS               # public alias, the UI walks it

DRIVER_URL = "https://developer.android.com/studio/run/win-usb"


def _powershell(script, timeout=30):
    root = os.environ.get("SystemRoot", r"C:\Windows")
    exe = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.exists(exe):
        exe = "powershell"
    try:
        p = popen([exe, "-NoProfile", "-NonInteractive", "-Command", script],
                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = p.communicate(timeout=timeout)
        return (out or b"").decode("utf-8", "replace")
    except Exception:
        return ""


def port_listeners(ports=_PORTS):
    """{port: "name (pid)"} for the local TCP ports we hand to adb reverse.

    adb reverse makes the *adb server* listen on the PC side, so anything else
    squatting on 27183 stops the tunnel from carrying a single byte -- the phone
    connects, and the picture simply never arrives.
    """
    lst = ",".join(str(p) for p in ports)
    script = (
        "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
        "Where-Object { @(%s) -contains $_.LocalPort } | "
        "ForEach-Object { $n = (Get-Process -Id $_.OwningProcess "
        "-ErrorAction SilentlyContinue).ProcessName; "
        "\"$($_.LocalPort)|$($_.OwningProcess)|$n\" }" % lst)
    found = {}
    for line in _powershell(script).splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3 and parts[0].isdigit():
            found[int(parts[0])] = "%s (pid %s)" % (parts[2] or "?", parts[1])
    return found


def problem_devices():
    """[(status, class, name)] for every present PnP device Windows dislikes."""
    script = (
        "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Status -ne 'OK' } | "
        "ForEach-Object { \"$($_.Status)|$($_.Class)|$($_.FriendlyName)\" }")
    rows = []
    for line in _powershell(script).splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3 and parts[0]:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


_ANDROID_HINT = re.compile(
    r"android|adb|\bphone\b|mtp|composite|便携设备|未知设备|unknown device", re.I)


def android_problem_devices():
    """The subset of problem_devices() that looks like a phone we cannot talk to.

    An Android phone with no usable USB driver shows up as "Unknown device", or
    as an MTP / composite device carrying an error -- that shape is the tell.
    """
    return [r for r in problem_devices() if _ANDROID_HINT.search(r[2] or "")]


def usb_mode_hint(devices):
    """What to tell the user when `adb devices` comes back empty.

    Every Android phone defaults a freshly plugged cable to charge-only: USB
    debugging is on, the cable is fine, the driver is fine, and adb still sees
    nothing at all.  Pulling down the notification shade and picking "File
    transfer" is the fix, so it gets said first.
    """
    if devices:
        return ""
    probs = android_problem_devices()
    if probs:
        return (i18n.t("设备管理器里有 %d 个无法识别的设备，看着像这台手机：%s。"
                       "多半是缺 USB 驱动。",
                       "Device Manager lists %d unrecognised devices that look like "
                       "this phone: %s.  Most likely the USB driver is missing.")
                % (len(probs), "、".join(p[2] for p in probs[:3])))
    return i18n.t("没有检测到手机。最常见的原因是手机插上后停在「仅充电」模式："
                  "下拉通知栏，把「USB 用途」改成「传输文件 / MTP」，adb 才能看到它。",
                  "No phone detected.  The usual reason is that the phone is still in "
                  "\"Charge only\" mode: pull down the notification shade and change "
                  "\"USB preferences\" to \"File transfer / MTP\" so adb can see it.")


def bundled_driver_dir():
    """A driver shipped inside tools/, if this build carries one."""
    d = os.path.join(TOOLS, "usb_driver")
    return d if os.path.isdir(d) else None


def _runas(exe, args):
    """ShellExecuteW with the UAC prompt -- driver work needs elevation."""
    import ctypes
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 1)
        return rc > 32
    except Exception:
        return False


def install_usb_driver():
    """Install the bundled USB driver, if this build carries one.

    Google's driver may not be redistributed with the app, so most builds ship
    without one and the honest answer is to hand over the download link -- which
    is what the caller falls back to.
    """
    d = bundled_driver_dir()
    if not d:
        return False, (i18n.t("本程序没有随包提供 USB 驱动（授权原因），"
                              "请从这里下载安装：%s",
                              "This build does not bundle the USB driver (licensing), "
                              "so download and install it from here: %s") % DRIVER_URL)
    inf = os.path.join(d, "android_winusb.inf")
    if not os.path.exists(inf):
        return False, i18n.t("随包驱动目录里没有 android_winusb.inf：%s",
                             "The bundled driver folder has no android_winusb.inf: %s") % d
    if _runas("pnputil.exe", '/add-driver "%s" /install' % inf):
        return True, i18n.t("已请求管理员权限安装驱动，装完请重新插拔数据线",
                            "Asked for administrator rights to install the driver -- "
                            "unplug and replug the cable when it finishes")
    return False, i18n.t("提权被取消，驱动没有安装",
                         "Elevation was cancelled, so the driver was not installed")


def open_device_manager():
    """Open Device Manager so the user can see the phone the way Windows does."""
    try:
        os.startfile("devmgmt.msc")
        return True
    except Exception:
        return False

