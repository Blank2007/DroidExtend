"""Thin wrapper around the bundled adb.exe."""
import os
import re
import subprocess
import time

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
            raise AdbError("adb.exe 未找到（tools/platform-tools/adb.exe）")

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
            raise AdbError("adb %s 超时" % (args[0] if args else ""))
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
