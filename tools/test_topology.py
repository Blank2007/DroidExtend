import os, sys, json, time, ctypes
sys.path.insert(0, r"D:\dsh projects\droid-extend\host")
import win_display as wd
wd.set_dpi_aware()
u = ctypes.windll.user32


def snap(tag):
    paths, modes = wd._query(wd.QDC_ALL_PATHS)
    print("--- %s ---" % tag)
    print("  desktop=%s  monitors=%d" % (wd.desktop_bounds(), u.GetSystemMetrics(80)))
    print("  live=%s" % json.dumps(
        [{k: m[k] for k in ("name", "left", "top", "width", "height", "hz")}
         for m in wd.live_monitors()], ensure_ascii=False))
    for i, p in enumerate(paths):
        if not (p.sourceInfo.statusFlags & wd.PATH_ACTIVE):
            continue
        mi = p.sourceInfo.modeInfoIdx
        m = modes[mi] if mi != 0xFFFFFFFF and mi < len(modes) else None
        pos = (m.u.sourceMode.position.x, m.u.sourceMode.position.y) if m else None
        print("  path[%d] src=%r tgt=%r pos=%s" % (i, wd.source_name(p), wd.target_name(p), pos))


action = sys.argv[1] if len(sys.argv) > 1 else "extend"
snap("before")
if action == "extend":
    rc = wd.extend_topology()
elif action == "internal":
    rc = u.SetDisplayConfig(0, None, 0, None, wd.SDC_TOPOLOGY_INTERNAL | wd.SDC_APPLY)
elif action == "clone":
    rc = u.SetDisplayConfig(0, None, 0, None, wd.SDC_TOPOLOGY_CLONE | wd.SDC_APPLY)
else:
    raise SystemExit("unknown action")
print("SetDisplayConfig rc=%s" % (rc,))
time.sleep(2.5)
snap("after " + action)
