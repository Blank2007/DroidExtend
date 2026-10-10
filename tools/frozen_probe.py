"""Why does a PyInstaller-frozen process get ACCESS_DENIED from the display APIs?

Compares, in the same frozen binary:
  * the user32 handle ctypes.windll hands us,
  * an explicit full-path load of C:\\Windows\\System32\\user32.dll,
  * a raw GetProcAddress pointer,
so we can tell a wrong-module problem from a policy problem.
"""
import ctypes
import os
import sys
import time

OUT = r"D:\dsh projects\droid-extend\tools\tmp\probe_out.txt"
lines = []


def say(msg):
    lines.append(str(msg))
    with open(OUT, "w", encoding="utf8") as fh:
        fh.write("\n".join(lines))


say("frozen=%s" % getattr(sys, "frozen", False))
say("exe=%s" % sys.executable)

sys.path.insert(0, r"D:\dsh projects\droid-extend\host")
import win_display as wd
import vdd

k = ctypes.windll.kernel32
buf = ctypes.create_unicode_buffer(512)


def mod_path(handle):
    k.GetModuleFileNameW(handle, buf, 512)
    return buf.value


def mod_of(fnptr):
    h = ctypes.c_void_p()
    if not k.GetModuleHandleExW(0x4, ctypes.byref(ctypes.c_void_p(fnptr)), ctypes.byref(h)):
        return "GetModuleHandleEx failed"
    return mod_path(h.value)


SYSDIR = os.path.join(os.environ["SystemRoot"], "System32")
FULL = os.path.join(SYSDIR, "user32.dll")
say("System32 user32 exists: %s" % os.path.exists(FULL))

name = vdd.status()["adapter"]["name"]
cur = wd.current_mode(name)
say("adapter=%r  current=%s" % (name, cur))

dm = wd.DEVMODEW()
dm.dmSize = ctypes.sizeof(dm)
ctypes.windll.user32.EnumDisplaySettingsExW(name, wd.ENUM_CURRENT_SETTINGS, ctypes.byref(dm), 0)
dm.dmFields = wd.DM_PELSWIDTH | wd.DM_PELSHEIGHT | wd.DM_DISPLAYFREQUENCY
say("devmode: %dx%d @%d size=%d fields=0x%x" %
    (dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency, dm.dmSize, dm.dmFields))


def try_variant(label, lib):
    fn = lib.ChangeDisplaySettingsExW
    addr = ctypes.cast(fn, ctypes.c_void_p).value
    say("  %-28s fn=%#x  in %s" % (label, addr, mod_of(addr)))
    lib.ChangeDisplaySettingsExW.restype = ctypes.c_long
    lib.ChangeDisplaySettingsExW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    rc = lib.ChangeDisplaySettingsExW(name, ctypes.byref(dm), None, wd.CDS_TEST, None)
    say("      CDS_TEST rc=%d" % rc)
    return rc


say("--- via ctypes.windll.user32 ---")
try_variant("windll.user32", ctypes.windll.user32)

say("--- via ctypes.WinDLL(full path) ---")
try:
    u2 = ctypes.WinDLL(FULL)
    try_variant("WinDLL(System32/user32)", u2)
except Exception as e:
    say("  load failed: %s" % e)

say("--- via raw GetProcAddress on the system module ---")
try:
    h = k.LoadLibraryW(FULL)
    say("  LoadLibraryW -> %#x" % h)
    p = k.GetProcAddress(h, b"ChangeDisplaySettingsExW")
    say("  GetProcAddress -> %#x" % p)
    if p:
        WINFUNCTYPE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_wchar_p,
                                         ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_ulong, ctypes.c_void_p)
        fn = WINFUNCTYPE(p)
        rc = fn(name, ctypes.byref(dm), None, wd.CDS_TEST, None)
        say("  CDS_TEST rc=%d" % rc)
except Exception as e:
    say("  EXC %s" % e)

say("--- SetDisplayConfig(SDC_VALIDATE|SDC_TOPOLOGY_EXTEND) via windll ---")
ctypes.windll.user32.SetDisplayConfig.restype = ctypes.c_long
say("  rc=%d" % ctypes.windll.user32.SetDisplayConfig(
    0, None, 0, None, wd.SDC_TOPOLOGY_EXTEND | wd.SDC_VALIDATE))

say("DONE")
