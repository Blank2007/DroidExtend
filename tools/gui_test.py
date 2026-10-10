"""Drive the real GUI object through a whole session, without a human clicking.

This exists because e2e.py exercises Session directly, which would happily keep
working while `_apply_form()` (the widget -> config translation) was broken.
Here we build the actual App, fill the form the way a user would, press the same
button callbacks, and inspect the live stats.

It runs inside a real mainloop(): tkinter refuses `after()` calls from worker
threads until the main thread is actually in mainloop, so pumping update() by
hand makes the app look broken when it is not.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "host"))

import app as appmod                                                    # noqa: E402

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0

a = appmod.App()
samples = []
started = {"yes": False}
phase = {"name": "settle", "until": time.time() + 12.0}


def finish(code):
    try:
        a.quit()
    except Exception:
        pass
    globals()["_exit"] = code


def tick():
    if started["yes"]:
        return
    if a.busy:
        a.after(250, tick)
        return
    started["yes"] = True

    # a quality-first setup at a bitrate this cable can actually carry
    a.res_var.set("1920x1080")
    a.hz_var.set("60")
    a.fps_var.set("60")
    a.br_var.set("40")
    a.q_var.set("20")
    a.rc_var.set(appmod.RATECONTROL[0][1])
    a.be_var.set("自动（优先 ddagrab）")
    a.pos_var.set("right")
    a.mode_var.set("extend")
    a.inst_var.set(True)
    a._apply_form()
    cfg = a.session.cfg
    print("form -> cfg:")
    for k in ("width", "height", "hz", "fps", "bitrate_mbps", "rate_control",
              "quality", "encoder", "preset", "backend", "position", "mode"):
        print("   %-14s %s" % (k, cfg[k]))
    assert cfg["rate_control"] == "vbr", cfg
    assert cfg["quality"] == 20, cfg
    assert cfg["bitrate_mbps"] == 40, cfg

    print("pressing 开始投屏 …")
    # Click the real widget, not the callback.  Calling a.on_start() directly
    # silently bypasses a disabled button -- which is exactly how a greying-out
    # bug in _bg_done() once shipped: the button was dead but this test passed.
    if a.btn_start.instate(["disabled"]):
        print("RESULT: FAIL -- 开始投屏 按钮是禁用的（点不动）")
        finish(1)
        return
    a.btn_start.invoke()
    phase["name"] = "run"
    phase["until"] = time.time() + SECONDS
    a.after(500, tick)


def sample():
    if a.session.running:
        s = a.session.stats
        samples.append((s["fps"], s["mbps"], s["sent"], s["dropped"]))
    if phase["name"] == "run" and time.time() < phase["until"]:
        a.after(250, sample)
    elif phase["name"] == "run":
        phase["name"] = "stopping"
        print("perf label: %s" % a.perf_var.get())
        print("status    : %s" % a.stat_var.get())
        print("pressing 停止 …")
        a.btn_stop.invoke()
        a.after(400, sample)
    elif phase["name"] == "stopping" and not a.busy:
        ss = a.session.stats
        print("perf samples (fps, mbps, sent, dropped):")
        for row in samples[::6]:
            print("   %.1f fps  %.2f Mbps  sent %d  dropped %d" % row)
        print("FINAL session stats: %s" % ss)
        ok = ss["sent"] > 300 and ss["dropped"] == 0
        print("RESULT:", "PASS" if ok else "FAIL")
        finish(0 if ok else 1)
    else:
        a.after(250, sample)


a.after(1500, tick)
a.after(1800, sample)
a.mainloop()
try:
    a.destroy()
except Exception:
    pass
sys.exit(globals().get("_exit", 1))
