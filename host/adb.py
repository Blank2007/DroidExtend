"""Thin wrapper around the bundled adb.exe."""
import os
import re
import subprocess
import time

import i18n

from paths import TOOLS, adb_exe, popen
from version import REPO_URL

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
        # Kept verbatim for 工具 -> 诊断连接问题…: when the list is empty the
        # raw text is the only thing that says *why* (an hdc takeover writes
        # nothing at all here, a driver problem writes a "no permissions" line).
        self.last_devices_out = out
        rows = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            parts = line.split()
            # stderr is merged into stdout, so the daemon's chatter and the
            # "List of devices attached" banner arrive here too.  1.8.3: a line
            # whose second column is not a state we know (a Windows "no
            # permissions" variant, a half-plugged device, a newer adb's state)
            # used to be dropped, which turned every odd case into the same
            # "no phone detected".  Keep it as state "?" instead; callers only
            # ever look for state == "device", so nothing else changes.
            if len(parts) < 2:
                continue
            state = parts[1] if parts[1] in _DEVICE_STATES else "?"
            rec = {"serial": parts[0], "state": state, "raw": line}
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


# --------------------------------------------------- Huawei / HarmonyOS (1.8.3)
# Huawei phones and tablets on HarmonyOS have one failure mode no other vendor
# has: Huawei's own hdc bridge takes the USB device and listens on port 8710,
# and adb then reports an empty device list even though the cable, the driver,
# the USB mode and the debugging switch are all exactly right.  DevEco Studio,
# Huawei PC Manager and HiSuite each start hdc, and the user usually does not
# know it is running.  Nothing here changes behaviour -- it lets the diagnosis
# name the cause instead of repeating "no phone detected" (the reason 1.8.3
# exists at all; see docs/HUAWEI.md).

HDC_PORT = 8710              # hdc's server port, shared with nothing else of ours
_HUAWEI_VENDORS = ("huawei", "honor", "harmony")


def hdc_processes():
    """["hdc (pid 1234)"] for Huawei's hdc bridge processes running right now."""
    script = ("Get-Process -ErrorAction SilentlyContinue | "
              "Where-Object { $_.ProcessName -match '^(hdc|hdb)' } | "
              "ForEach-Object { " + chr(34) + "$($_.ProcessName)|$($_.Id)" + chr(34) + " }")
    rows = []
    for line in _powershell(script).splitlines():
        parts = line.strip().split("|")
        if len(parts) == 2 and parts[0]:
            rows.append("%s (pid %s)" % (parts[0], parts[1]))
    return rows


def hdc_holder():
    """Who listens on 8710 -- the hdc server -- or "" when the port is free."""
    return port_listeners((HDC_PORT,)).get(HDC_PORT, "")


def hdc_conflict():
    """A sentence naming an hdc takeover, or "" when nothing suggests one.

    Returned text goes straight into the log, so it carries the fix with it.
    """
    procs = hdc_processes()
    holder = hdc_holder()
    if not procs and not holder:
        return ""
    who = "、".join(procs) if procs else holder
    return i18n.t(
        "检测到华为的 hdc（%s），并且端口 %d 被占：hdc 会把设备从 adb 手里抢走，"
        "插着线也看不到手机。先退出 DevEco Studio / 华为电脑管家 / HiSuite，"
        "结束 hdc 进程，再重新插拔数据线。",
        "Huawei's hdc bridge is running (%s) and port %d is taken: hdc takes the "
        "device away from adb, so a plugged-in phone stays invisible.  Quit DevEco "
        "Studio / Huawei PC Manager / HiSuite, end the hdc process, then unplug and "
        "replug the cable.") % (who, HDC_PORT)

def phone_info(adb, serial):
    """Who the phone is: {manufacturer, model, release, sdk, harmony, emui}.

    One `getprop` for the whole list -- a diagnosis that costs ten adb calls per
    device is a diagnosis users stop running.
    """
    info = {}
    try:
        rc, out = adb.run("-s", serial, "shell", "getprop", timeout=25)
    except Exception:
        return info
    want = ("ro.product.manufacturer", "ro.product.model", "ro.product.brand",
            "ro.build.version.release", "ro.build.version.sdk",
            "ro.build.version.harmonyos", "ro.build.version.emui",
            "hw_sc.build.platform.version")
    for line in out.splitlines():
        m = re.match(r"\[(.+?)\]\s*:\s*\[(.*?)\]", line.strip())
        if m and m.group(1) in want and m.group(2):
            info[m.group(1)] = m.group(2)
    return info


def is_huawei(info):
    """True when the properties say Huawei, Honor or HarmonyOS."""
    blob = " ".join((info.get("ro.product.manufacturer", ""),
                     info.get("ro.product.brand", ""),
                     info.get("ro.build.version.harmonyos", ""),
                     info.get("hw_sc.build.platform.version", ""))).lower()
    return any(v in blob for v in _HUAWEI_VENDORS)


def describe_phone(info, serial=""):
    """One line: model, vendor, OS version.  Empty dicts still print something."""
    if not info:
        return serial
    name = " ".join(b for b in (info.get("ro.product.manufacturer", ""),
                                info.get("ro.product.model", "")) if b) or serial
    # Huawei hides the HarmonyOS version under two different properties
    # depending on the model (ro.build.version.harmonyos on some, the vendor
    # one on others), so either counts as "this is HarmonyOS".
    os_name = (info.get("ro.build.version.harmonyos")
               or info.get("hw_sc.build.platform.version") or "")
    if os_name:
        os_bit = os_name if os_name.lower().startswith("harmony") else ("HarmonyOS %s" % os_name)
    elif info.get("ro.build.version.emui"):
        os_bit = "EMUI %s" % info["ro.build.version.emui"]
    else:
        os_bit = "Android %s" % info.get("ro.build.version.release", "?")
    sdk = info.get("ro.build.version.sdk", "")
    return "%s · %s%s" % (name, os_bit, (" (API %s)" % sdk) if sdk else "")


def huawei_hints():
    """The three Huawei-specific things to check, as log lines."""
    return [
        i18n.t("华为设备请先确认三件事：",
               "Huawei devices: three things to check first:"),
        i18n.t("  1. 开发者选项里打开「仅充电时允许 ADB 调试」；插线后通知栏把 USB 用途"
               "改成「传输文件」，停在「仅充电」时 adb 看不到手机。",
               "  1. In Developer options turn on 'Allow ADB debugging in charge only "
               "mode'; then set USB preferences to 'File transfer', because adb "
               "cannot see a phone that stayed in charge-only mode."),
        i18n.t("  2. 关掉 hdc：DevEco Studio、华为电脑管家、HiSuite 都会启动 hdc，"
               "它和 adb 共用 %d 端口，会把设备抢走。",
               "  2. Get hdc out of the way: DevEco Studio, Huawei PC Manager and "
               "HiSuite all start hdc, which shares port %d with adb and takes the "
               "device away.") % HDC_PORT,
        i18n.t("  3. 手机端 App 装不上时，检查「设置 → 系统和更新 → 纯净模式」，它"
               "会拦截应用市场以外来源的安装包。",
               "  3. If the phone-side app will not install, check Settings -> System "
               "and updates -> Pure mode: it blocks APKs from outside AppGallery."),
        i18n.t("  详细排查：%s", "  Full checklist: %s")
        % (REPO_URL + "/blob/main/docs/HUAWEI.md"),
    ]


_INSTALL_CAUSES = (
    ("INSTALL_FAILED_VERIFICATION_FAILURE",
     i18n.t("华为/鸿蒙的「纯净模式」拦下了安装：到 设置 → 系统和更新 → 纯净模式 里"
            "关掉（或允许本次安装），再试一次。",
            "Huawei/HarmonyOS 'Pure mode' blocked the install.  Turn it off under "
            "Settings -> System and updates -> Pure mode (or allow this one "
            "install) and try again.")),
    ("INSTALL_FAILED_USER_RESTRICTED",
     i18n.t("手机上的安装限制（纯净模式 / 家长控制 / 应用锁）拦下了安装，"
            "先解除限制再试。",
            "An install restriction on the phone (Pure mode, parental controls, "
            "app lock) blocked it -- lift the restriction and try again.")),
    ("INSTALL_FAILED_INSUFFICIENT_STORAGE",
     i18n.t("手机存储空间不够，清出几百 MB 再试。",
            "The phone is out of storage -- free a few hundred MB and try again.")),
)


def install_hint(out):
    """A plain-language cause for a failed `adb install`, or "".

    1.8.3: a Huawei phone can refuse the install through no fault of the cable,
    and "Failure [INSTALL_FAILED_VERIFICATION_FAILURE]" means nothing to anyone
    who has not read Android's package manager source.
    """
    text = (out or "").upper()
    for key, msg in _INSTALL_CAUSES:
        if key in text:
            return msg
    return ""

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

