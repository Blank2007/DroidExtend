"""Screen capture -> H.264 Annex-B, straight off ffmpeg's stdout.

Two capture backends are supported:

  ddagrab   Desktop Duplication API.  Hardware frames go straight into NVENC,
            the desktop is only read when it actually changed (measured 2
            duplicated frames per 4 s vs 83 for gdigrab), and it can grab a
            single monitor by offset/size.  Preferred.
  gdigrab   Plain GDI BitBlt.  Works everywhere, including on machines with no
            NVENC, but burns CPU and duplicates frames when it cannot keep up.

The encoder is picked to match the capture backend: hardware frames must go to
a hardware encoder (nvenc), while gdigrab's system-memory BGRA can go anywhere.
"""
import os
import re
import sys
import subprocess
import threading
import time

from paths import ffmpeg_exe, popen

# codec -> (ffmpeg encoder name, needs nvidia, quality knob)
ENCODERS = {
    "h264_nvenc": {"label": "NVIDIA NVENC (H.264)", "hw": True, "kind": "nvenc"},
    "hevc_nvenc": {"label": "NVIDIA NVENC (HEVC)", "hw": True, "kind": "nvenc"},
    "h264_amf":   {"label": "AMD AMF (H.264)",     "hw": True, "kind": "amf"},
    "libx264":    {"label": "CPU x264 (兼容性最好)", "hw": False, "kind": "x264"},
}

NVENC_PRESETS = ["p1", "p2", "p3", "p4", "p5", "p6", "p7"]
X264_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium"]


def list_hardware_encoders(ffmpeg=None):
    ffmpeg = ffmpeg or ffmpeg_exe()
    if not ffmpeg:
        return set()
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=25,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return set()
    have = set()
    for name in ENCODERS:
        if name in out:
            have.add(name)
    return have


# ---------------------------------------------------------------------------
# DDAGrab output discovery
#
# ddagrab addresses a monitor by DXGI *output index*, not by GDI device name or
# desktop offset, and those indices only mean anything for the topology that was
# up when they were collected.  What the stream needs is the index that really
# is the virtual display:
#
#   * The size alone is NOT an identity.  1920x1080 is both the most common
#     laptop panel in the world and the size we ask the virtual display for by
#     default, so matching on (w, h) silently streams the *laptop screen*
#     whenever the two agree - the phone then shows the desktop the user is
#     already looking at.  Asking for the virtual display's size to equal the
#     panel's triggers it every single time.
#   * So when more than one output matches, they are told apart by *content*:
#     the desktop rectangle where the virtual display sits is grabbed with
#     gdigrab (exact - it works in desktop coordinates) and compared against a
#     thumbnail of every candidate output.  Only the virtual display can match.
#   * When even that cannot decide, the answer is None - never a guess.  The
#     caller then falls back to plain gdigrab: slower, but exact.
#
# The DUPLICATION API HANDS BACK ONE PURE BLACK FRAME (min=0 max=0) BEFORE THE
# REAL DESKTOP APPEARS.  Measured on this machine: frame 0 of every stream is
# 0x000000 while frames 1+ match gdigrab pixel for pixel (meandiff 0.00).  If
# that black frame reached the encoder it would become the phone's first
# keyframe, so it is filtered out with select=gt(n,0) - the comma has to be
# backslash-escaped because ffmpeg splits a filtergraph on bare commas.
# ---------------------------------------------------------------------------

_DDA_OUTPUTS = None
_THUMB = 16               # thumbnail edge, in pixels, used to compare outputs
_THUMB_MAX_DIFF = 12.0    # 0..255 mean absolute difference that still counts as "same picture"
_THUMB_MARGIN = 4.0       # how far the winner must beat the runner-up


def detect_ddagrab_outputs(ffmpeg=None, limit=8, timeout=25):
    """Return [(output_idx, width, height)] for every DXGI output ddagrab can open."""
    ff = ffmpeg or ffmpeg_exe()
    if not ff:
        return []
    found = []
    misses = 0
    for idx in range(limit):
        cmd = [ff, "-hide_banner", "-loglevel", "info", "-nostdin",
               "-init_hw_device", "d3d11va=gpu", "-filter_hw_device", "gpu",
               "-f", "lavfi", "-i",
               "ddagrab=output_idx=%d:framerate=1:draw_mouse=0" % idx,
               "-vf", "hwdownload,format=bgra,showinfo",
               "-frames:v", "1", "-f", "null", "-"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                               encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            break
        if p.returncode != 0:
            # A gap does not mean the list ended: an unavailable output simply
            # disappears and the next index can still be live.  (Measured here:
            # #0 = 2560x1600 panel, #1 = 1920x1080 virtual display, #2+ fail
            # with "Failed to enumerate DXGI output".)
            misses += 1
            if misses >= 3:
                break
            continue
        misses = 0
        m = re.search(r"s:(\d+)x(\d+)", p.stderr or "")
        if not m:
            break
        found.append((idx, int(m.group(1)), int(m.group(2))))
    return found


def ddagrab_outputs(ffmpeg=None, refresh=False):
    global _DDA_OUTPUTS
    if _DDA_OUTPUTS is None or refresh:
        _DDA_OUTPUTS = detect_ddagrab_outputs(ffmpeg)
    return _DDA_OUTPUTS


def _run_thumb(cmd, size, timeout=25):
    """Run a one-frame ffmpeg grab and return its raw thumbnail bytes."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return None
    if p.returncode != 0 or len(p.stdout or b"") != size * size:
        return None
    return p.stdout


def _thumb_output(idx, ff, size=_THUMB):
    """size x size grayscale thumbnail of one DXGI output."""
    return _run_thumb([ff, "-hide_banner", "-loglevel", "error", "-nostdin",
                       "-init_hw_device", "d3d11va=gpu", "-filter_hw_device", "gpu",
                       "-f", "lavfi", "-i",
                       "ddagrab=output_idx=%d:framerate=1:draw_mouse=0" % idx,
                       "-vf", r"hwdownload,format=bgra,select=gt(n\,0),"
                              "scale=%d:%d,format=gray" % (size, size),
                       "-frames:v", "1", "-f", "rawvideo", "-"], size)


def _thumb_desktop(rect, ff, size=_THUMB):
    """size x size grayscale thumbnail of the desktop region the VDD occupies."""
    return _run_thumb([ff, "-hide_banner", "-loglevel", "error", "-nostdin",
                       "-f", "gdigrab", "-framerate", "1", "-draw_mouse", "0",
                       "-offset_x", str(int(rect["left"])),
                       "-offset_y", str(int(rect["top"])),
                       "-video_size", "%dx%d" % (int(rect["width"]), int(rect["height"])),
                       "-i", "desktop",
                       "-vf", "scale=%d:%d,format=gray" % (size, size),
                       "-frames:v", "1", "-f", "rawvideo", "-"], size)


def _meandiff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / float(len(a))


def identify_output(rect, outs, ff, log=None):
    """Which of *outs* really shows the monitor described by *rect*?  None if unsure."""
    log = log or (lambda m: None)
    w, h = int(rect["width"]), int(rect["height"])
    same = [o for o in outs if (o[1], o[2]) == (w, h)]
    if not same:
        return None
    if len(same) == 1:
        return same[0][0]
    ref = _thumb_desktop(rect, ff)
    if ref is None:
        return None
    scored = []
    for idx, _, _ in same:
        t = _thumb_output(idx, ff)
        if t is not None:
            scored.append((_meandiff(ref, t), idx))
    if not scored:
        return None
    scored.sort()
    log("ddagrab: %d 个输出都是 %dx%d，按画面比对 %s" % (
        len(same), w, h, ", ".join("#%d=%.1f" % (i, d) for d, i in scored)))
    best_d, best = scored[0]
    if best_d > _THUMB_MAX_DIFF:
        log("ddagrab: 画面差异过大（%.1f），不猜哪个是虚拟屏" % best_d)
        return None
    if len(scored) > 1 and scored[1][0] - best_d < _THUMB_MARGIN:
        log("ddagrab: 两个输出画面太像（%.1f vs %.1f），无法区分" % (best_d, scored[1][0]))
        return None
    log("ddagrab: 虚拟屏是 output #%d（画面差异 %.1f）" % (best, best_d))
    return best


def pick_ddagrab_output(rect, ffmpeg=None, log=None):
    """The DXGI output index showing the monitor in *rect*, or None if unsure.

    None is a real answer, not a failure: the caller must then capture with
    gdigrab, which addresses the desktop by offset and is therefore always the
    right screen.  Guessing - the old "fall back to output 0" - streams the
    laptop's own screen to the phone.
    """
    log = log or (lambda m: None)
    ff = ffmpeg or ffmpeg_exe()
    if not ff:
        return None
    outs = ddagrab_outputs(ff)
    idx = identify_output(rect, outs, ff, log)
    if idx is None:
        log("ddagrab: 找不到虚拟屏对应的输出（可用 %s）" %
            (", ".join("#%d=%dx%d" % o for o in outs) if outs else "无"))
    return idx


# ---------------------------------------------------------------------------
# Annex-B access unit splitting
# ---------------------------------------------------------------------------

def _is_vcl(nal_type):
    return nal_type in (1, 5)


def _is_key(nal_type):
    return nal_type == 5


def split_access_units(buf):
    """Split an Annex-B byte stream into whole access units.

    Returns (units, remainder).  A unit is one coded picture plus whatever
    parameter sets / SEI preceded it - exactly what MediaCodec wants in one
    input buffer.  Anything after the last complete NAL is left in remainder.
    """
    starts = []
    i = buf.find(b"\x00\x00\x01")
    while i != -1:
        starts.append(i)
        i = buf.find(b"\x00\x00\x01", i + 3)
    if len(starts) < 2:
        return [], buf

    units = []
    cur_start = starts[0]
    cur_end = starts[1]
    cur_vcl = _is_vcl(buf[starts[0] + 3] & 0x1F)
    cur_key = _is_key(buf[starts[0] + 3] & 0x1F)
    for k in range(1, len(starts) - 1):
        s = starts[k]
        e = starts[k + 1]
        t = buf[s + 3] & 0x1F
        if cur_vcl and _is_vcl(t):
            units.append((buf[cur_start:cur_end], cur_key))
            cur_start, cur_end, cur_vcl, cur_key = s, e, True, _is_key(t)
        else:
            cur_end = e
            if _is_vcl(t):
                cur_vcl = True
                cur_key = cur_key or _is_key(t)

    if cur_vcl:
        units.append((buf[cur_start:cur_end], cur_key))
        remainder = buf[starts[-1]:]
    else:
        remainder = buf[cur_start:]
    return units, remainder


# ---------------------------------------------------------------------------
# capture process
# ---------------------------------------------------------------------------

class Capture(object):
    """Runs ffmpeg and hands out encoded access units."""

    def __init__(self, cfg, rect, log=None):
        self.cfg = cfg
        self.rect = rect
        self.log = log or (lambda m: None)
        self.proc = None
        self._stop = threading.Event()
        self._thread = None
        self.unit_queue = []
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self.stats = {"frames": 0, "bytes": 0, "dropped": 0, "t": time.time()}
        self.error = None
        self.backend_used = None

    # -- command line ------------------------------------------------------
    def command(self, backend):
        ff = ffmpeg_exe()
        c = self.cfg
        enc = c.get("encoder", "h264_nvenc")
        kind = ENCODERS.get(enc, ENCODERS["libx264"])["kind"]
        w = int(self.rect["width"])
        h = int(self.rect["height"])
        fps = int(c.get("fps", 60))
        bitrate = "%dM" % int(c.get("bitrate_mbps", 20))
        gop = int(c.get("gop") or max(1, fps * 2))
        mouse = "1" if c.get("draw_mouse") else "0"

        cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostats"]

        if backend == "ddagrab":
            # No video_size: let the output deliver its native size, which is
            # what the virtual display was just set to.  ddagrab knows nothing
            # about desktop offsets - the monitor *is* the output.
            override = int(c.get("dda_output_idx", -1))
            idx = override if override >= 0 else pick_ddagrab_output(self.rect, ff, self.log)
            if idx is None:
                # Refusing here is the whole point: assuming output 0 would
                # stream the laptop's own screen whenever it happens to share
                # the virtual display's resolution.  start() turns this into a
                # gdigrab attempt, which is exact.
                raise RuntimeError(
                    "无法确定虚拟显示器对应哪个 ddagrab 输出（%s）" %
                    (", ".join("#%d=%dx%d" % o for o in ddagrab_outputs(ff)) or "一个都没有"))
            native = dict((o[0], (o[1], o[2])) for o in ddagrab_outputs(ff)).get(idx)
            if native and native != (w, h):
                self.log("ddagrab: output #%d 是 %dx%d，缩放到 %dx%d" %
                         (idx, native[0], native[1], w, h))
            cmd += ["-init_hw_device", "d3d11va=gpu", "-filter_hw_device", "gpu"]
            cmd += ["-f", "lavfi", "-i",
                    "ddagrab=output_idx=%d:framerate=%d:draw_mouse=%s"
                    % (idx, fps, mouse)]
            # drop the duplication API's all-black priming frame, and resize if
            # the picked output is not already the size we asked for (scale
            # cannot touch D3D11 frames, so that fallback downloads first)
            drops = "select=gt(n\\,0)"
            if native and native != (w, h):
                drops = "hwdownload,format=bgra,scale=%d:%d,format=nv12,%s" % (w, h, drops)
            cmd += ["-vf", drops]
        else:
            cmd += ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", mouse,
                    "-offset_x", str(int(self.rect["left"])),
                    "-offset_y", str(int(self.rect["top"])),
                    "-video_size", "%dx%d" % (w, h),
                    "-i", "desktop"]

        cmd += ["-c:v", enc]
        # Desktop content is mostly static, so constant-bitrate encoding wastes
        # almost the whole budget stuffing identical frames - measured that way
        # a still test pattern still burned the full 20 Mbps.  Quality-targeted
        # VBR sends a few kB/s when nothing moves and only approaches the cap on
        # real motion, which is what a USB link can actually carry.
        rc = c.get("rate_control", "vbr")
        q = int(c.get("quality", 20) or 20)
        if rc == "cbr":
            ctl = ["-rc", "cbr", "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate]
        else:
            ctl = ["-rc", "vbr", "-cq", str(q),
                   "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate]
        if kind == "nvenc":
            preset = c.get("preset") or "p4"
            if preset not in NVENC_PRESETS:
                preset = "p4"
            cmd += ["-preset", preset, "-tune", "ll"] + ctl + [
                "-g", str(gop), "-bf", "0", "-zerolatency", "1",
                "-profile:v", "high", "-delay", "0", "-aud", "0",
                "-surfaces", "8"]
            if c.get("spatial_aq"):
                cmd += ["-spatial-aq", "1"]
        elif kind == "amf":
            ctl = ["-rc", "cbr" if rc == "cbr" else "vbr_peak",
                   "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate]
            if rc != "cbr":
                ctl += ["-qp_i", str(q), "-qp_p", str(q)]
            cmd += ["-usage", "ultralowlatency", "-quality", "speed"] + ctl + [
                "-g", str(gop), "-bf", "0"]
        else:
            preset = c.get("preset") or "veryfast"
            if preset not in X264_PRESETS:
                preset = "veryfast"
            ctl = (["-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate]
                   if rc == "cbr" else
                   ["-crf", str(q), "-maxrate", bitrate, "-bufsize", bitrate])
            cmd += ["-preset", preset, "-tune", "zerolatency"] + ctl + [
                "-g", str(gop), "-bf", "0",
                "-x264-params", "repeat-headers=1:scenecut=0:rc-lookahead=0"]

        if backend == "gdigrab":
            cmd += ["-pix_fmt", "yuv420p"]
        cmd += ["-f", "h264", "-flush_packets", "1", "-"]
        return cmd

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        ff = ffmpeg_exe()
        if not ff:
            raise RuntimeError("ffmpeg.exe 未找到")
        want = self.cfg.get("backend", "auto")
        enc = self.cfg.get("encoder", "h264_nvenc")
        hw = ENCODERS.get(enc, {}).get("hw", False)
        order = []
        if want == "auto":
            order = ["ddagrab", "gdigrab"] if hw else ["gdigrab", "ddagrab"]
        else:
            order = [want]

        last = None
        for backend in order:
            # dda cannot feed a software encoder
            if backend == "ddagrab" and not hw:
                self.log("ddagrab 需要硬件编码器，跳过")
                continue
            if backend == "ddagrab":
                # Re-enumerated now rather than reused from an earlier call: the
                # virtual display only exists once the session has attached it,
                # and a list collected before that cannot contain it.
                outs = ddagrab_outputs(ff, refresh=True)
                self.log("ddagrab 输出: " +
                         (", ".join("#%d=%dx%d" % o for o in outs) if outs else "未发现"))
            try:
                cmd = self.command(backend)
            except Exception as e:
                self.log("  %s 命令构造失败: %s" % (backend, e))
                last = str(e)
                continue
            self.log("启动采集: " + backend + " / " + enc)
            p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(1.2)
            if p.poll() is not None:
                err = (p.stderr.read() or b"").decode("utf-8", "replace")
                self.log("  %s 启动失败: %s" % (backend, err.strip().splitlines()[-1] if err.strip() else "?"))
                last = err
                continue
            self.proc = p
            self.backend_used = backend
            self.log("  %s 采集已开始 %dx%d@%d" % (backend, self.rect["width"],
                                                   self.rect["height"], self.cfg.get("fps", 60)))
            self._thread = threading.Thread(target=self._reader, name="ffmpeg-reader", daemon=True)
            self._thread.start()
            return backend
        raise RuntimeError("采集启动失败: %s" % (last or "没有可用后端"))

    def _reader(self):
        p = self.proc
        buf = b""
        while not self._stop.is_set():
            try:
                # os.read, not p.stdout.read(65536).  BufferedReader.read(n)
                # keeps issuing raw reads until it has n bytes, so an access
                # unit that lands mid-buffer waits for the rest of the 64 KiB
                # to arrive.  Measured on this machine (tools/readlat.py, 2.7
                # Mbps animated pattern): read() handed over 64 KiB every
                # 200 ms, read1()/os.read handed over 4.6 KiB every 13.7 ms.
                # That 200 ms was a third of the glass-to-glass delay, and it
                # is also why the phone saw frames in 200 ms bursts
                # ("Render time interval reaches 154-258ms" in logcat).
                chunk = os.read(p.stdout.fileno(), 65536)
            except Exception as e:
                self.error = str(e)
                break
            if not chunk:
                break
            buf += chunk
            units, buf = split_access_units(buf)
            if len(buf) > 16 * 1024 * 1024:
                buf = buf[-1024 * 1024:]
            if units:
                # Stamp each unit with the moment its bytes came out of ffmpeg;
                # the phone turns that into a real end-to-end latency figure.
                stamp = int(time.time() * 1000000)
                with self._cv:
                    for u in units:
                        self.unit_queue.append((u[0], u[1], stamp))
                        self.stats["frames"] += 1
                        self.stats["bytes"] += len(u[0])
                    # bound the backlog; the server drops what it cannot send
                    if len(self.unit_queue) > 240:
                        del self.unit_queue[:len(self.unit_queue) - 240]
                        self.stats["dropped"] += 1
                    self._cv.notify_all()
        with self._cv:
            self._cv.notify_all()

    def get_units(self, timeout=1.0):
        with self._cv:
            if not self.unit_queue:
                self._cv.wait(timeout)
            out = self.unit_queue
            self.unit_queue = []
            return out

    def fps(self):
        dt = time.time() - self.stats["t"]
        return self.stats["frames"] / dt if dt > 0 else 0.0

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        self._stop.set()
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
            for s in (self.proc.stdout, self.proc.stderr):
                try:
                    if s:
                        s.close()
                except Exception:
                    pass
            self.proc = None


def probe_command(cfg, rect):
    return Capture(cfg, rect).command("gdigrab")
