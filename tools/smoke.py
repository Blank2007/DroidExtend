"""Smoke test: import every module and exercise the read-only paths."""
import os, sys, json, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "host"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "host")))

HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "host"))
sys.path.insert(0, HOST)

fails = []
def step(name, fn):
    try:
        r = fn()
        print("OK   %-28s %s" % (name, r if r is not None else ""))
        return r
    except Exception as e:
        traceback.print_exc()
        fails.append((name, e))
        print("FAIL %-28s %s: %s" % (name, type(e).__name__, e))
        return None

import win_display as wd
import paths as p

step("set_dpi_aware", wd.set_dpi_aware)
step("sizeof PATH_INFO", lambda: "= %d" % __import__("ctypes").sizeof(wd.PATH_INFO))
step("sizeof DEVMODEW", lambda: "= %d" % __import__("ctypes").sizeof(wd.DEVMODEW))
step("live_monitors", lambda: json.dumps(wd.live_monitors(), ensure_ascii=False))
step("desktop_bounds", wd.desktop_bounds)
step("adb_exe", p.adb_exe)
step("ffmpeg_exe", p.ffmpeg_exe)
step("java_home", p.java_home)
step("sdk_dir", p.sdk_dir)
step("apk_path", p.apk_path)

import adb
step("Adb()", lambda: adb.Adb().version())
import capture
step("list_hardware_encoders", lambda: sorted(capture.list_hardware_encoders()))
step("split_access_units", lambda: capture.split_access_units(b"\x00\x00\x01\x67xx\x00\x00\x01\x68yy\x00\x00\x01\x65zz"))
import server
step("server.header", lambda: server.header(1920, 1080, 60))
import vdd
step("vdd.status", lambda: json.dumps(vdd.status(), ensure_ascii=False, default=str))
import session
s = step("Session()", session.Session)
if s:
    step("session.describe", s.describe)

# tkinter must be importable and Tk constructible
import tkinter
step("tkinter version", lambda: tkinter.TkVersion)

print()
if fails:
    print("FAILURES:", [f[0] for f in fails])
    sys.exit(1)
print("ALL SMOKE TESTS PASSED")
