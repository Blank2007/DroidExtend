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

from clock import now_us

import i18n

# What the phone has to decode.  The host sends this in the handshake so the
# phone can pick the right MediaCodec mime type (video/avc, video/hevc, video/av01).
CODEC_H264 = "avc"
CODEC_HEVC = "hevc"
CODEC_AV1 = "av01"


def _enc(label, kind, codec, hw):
    """One encoder description.

    kind   which family of ffmpeg options to use (nvenc / amf / qsv / software)
    codec  what the phone must be able to decode
    hw     hardware encoder, so it needs hardware frames from ddagrab
    """
    return {"label": label, "kind": kind, "codec": codec, "hw": hw}


# Every encoder we know how to drive.  Which of them this machine actually has
# is answered at runtime by list_hardware_encoders(); the GUI only offers those.
# 1.6.0: the list used to stop at H.264 plus one HEVC entry, and the muxer was
# hard-coded to `-f h264`, so choosing anything else produced a stream the phone
# could not decode.  That is the bug behind "other codecs simply do not work".
ENCODERS = {
    # NVIDIA
    "h264_nvenc":  _enc("NVIDIA NVENC · H.264", "nvenc", CODEC_H264, True),
    "hevc_nvenc":  _enc("NVIDIA NVENC · HEVC",  "nvenc", CODEC_HEVC, True),
    "av1_nvenc":   _enc("NVIDIA NVENC · AV1",   "nvenc", CODEC_AV1,  True),
    # AMD
    "h264_amf":    _enc("AMD AMF · H.264", "amf", CODEC_H264, True),
    "hevc_amf":    _enc("AMD AMF · HEVC",  "amf", CODEC_HEVC, True),
    "av1_amf":     _enc("AMD AMF · AV1",   "amf", CODEC_AV1,  True),
    # Intel Quick Sync
    "h264_qsv":    _enc("Intel QSV · H.264", "qsv", CODEC_H264, True),
    "hevc_qsv":    _enc("Intel QSV · HEVC",  "qsv", CODEC_HEVC, True),
    "av1_qsv":     _enc("Intel QSV · AV1",   "qsv", CODEC_AV1,  True),
    # CPU
    "libx264":     _enc(i18n.t("CPU x264（兼容性最好）",
                               "CPU x264 (best compatibility)"), "x264", CODEC_H264, False),
    "libx265":     _enc("CPU x265", "x265", CODEC_HEVC, False),
    "libsvtav1":   _enc("CPU SVT-AV1", "svtav1", CODEC_AV1, False),
}

# Preference order when the user asks for "auto": hardware first, and H.264
# before HEVC/AV1 because every Android device since 3.0 decodes it.
AUTO_ORDER = (
    "h264_nvenc", "hevc_nvenc", "av1_nvenc",
    "h264_qsv", "hevc_qsv", "av1_qsv",
    "h264_amf", "hevc_amf", "av1_amf",
    "libx264", "libx265", "libsvtav1",
)

NVENC_PRESETS = ["p1", "p2", "p3", "p4", "p5", "p6", "p7"]
X264_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium"]
X265_PRESETS = X264_PRESETS


def codec_of(encoder):
    """The phone-facing codec name for an ffmpeg encoder."""
    return ENCODERS.get(encoder, ENCODERS["libx264"])["codec"]


def encoder_label(encoder):
    return ENCODERS.get(encoder, {}).get("label", encoder)


def pick_encoder(available, want="auto"):
    """Choose an encoder.

    `want` may be "auto" (best available, hardware first) or a specific name.
    Returns None when nothing usable is available.
    """
    avail = set(available or ())
    if want and want != "auto":
        return want if want in avail else None
    for name in AUTO_ORDER:
        if name in avail:
            return name
    return None


def list_hardware_encoders(ffmpeg=None):
    """Encoders this ffmpeg build *contains*.

    Fast, and not the same question as "will it work here": this build lists
    h264_qsv / hevc_qsv / av1_qsv, and on a machine with no Intel GPU every one
    of them dies with "Error creating a MFX session: -9".  Use
    available_encoders() when the answer has to be trustworthy.
    """
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


def _mux_for(codec):
    # AV1 uses `obu`: the `av1` muxer refuses a pipe ("pipe:: Invalid argument").
    return {CODEC_H264: "h264", CODEC_HEVC: "hevc", CODEC_AV1: "obu"}.get(codec, "h264")


def probe_encoder(name, ffmpeg=None, timeout=30):
    """Run one second of test pattern through an encoder and see if it lives.

    This is the only honest check.  A codec can be compiled in, the GPU can
    advertise it, and the encoder can still refuse to initialise -- QSV without
    an Intel GPU, AV1 on a card older than Ada, NVENC when the driver session is
    already saturated.  Returns True only when real bytes came out.
    """
    ff = ffmpeg or ffmpeg_exe()
    if not ff or name not in ENCODERS:
        return False
    cfg = {"encoder": name, "fps": 30, "bitrate_mbps": 2, "quality": 30,
           "rate_control": "vbr", "draw_mouse": False, "gop": 30}
    try:
        probe = Capture(cfg, {"left": 0, "top": 0, "width": 320, "height": 240})
        cmd = probe.command("gdigrab")
        cut = cmd.index("-c:v")
    except Exception:
        return False
    test = ([ff, "-hide_banner", "-loglevel", "error", "-nostats",
             "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30", "-t", "1"]
            + cmd[cut:])
    try:
        p = subprocess.run(test, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return False
    return p.returncode == 0 and len(p.stdout or b"") > 0


_AVAILABLE = None


def available_encoders(ffmpeg=None, refresh=False, log=None):
    """Every encoder that genuinely works on this machine.  Probed once, cached."""
    global _AVAILABLE
    if _AVAILABLE is None or refresh:
        built = list_hardware_encoders(ffmpeg)
        ok = set()
        for name in AUTO_ORDER:
            if name not in built:
                continue
            if probe_encoder(name, ffmpeg):
                ok.add(name)
            elif log:
                log("编码器不可用，已排除 / encoder unusable: " + name)
        _AVAILABLE = ok
    return _AVAILABLE


def best_encoder(cfg, ffmpeg=None, log=None):
    """The encoder to actually use for this config, with silent fallback.

    A saved choice can stop working (driver update, GPU swap, another program
    holding all the NVENC sessions).  Rather than failing the whole stream, fall
    back to the best available one and say so in the log.
    """
    avail = available_encoders(ffmpeg, log=log)
    want = (cfg or {}).get("encoder") or "auto"
    if want in avail:
        return want
    fallback = pick_encoder(avail, "auto")
    if fallback and fallback != want:
        if log:
            log("编码器 %s 当前不可用，改用 %s / falling back" % (want, fallback))
    return fallback


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
        # framerate=30, not 1: ddagrab paces its output, so at 1 fps the first
        # frame costs a whole second and the sweep took 5 s -- five seconds of
        # frozen picture every time the capture had to be restarted, which is
        # exactly what a UAC prompt or a display-mode change forces.  Measured
        # on this machine: 2.25 s at framerate=1, 0.31 s at 30, 0.29 s at 60.
        cmd = [ff, "-hide_banner", "-loglevel", "info", "-nostdin",
               "-init_hw_device", "d3d11va=gpu", "-filter_hw_device", "gpu",
               "-f", "lavfi", "-i",
               "ddagrab=output_idx=%d:framerate=30:draw_mouse=0" % idx,
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


def _run_thumb(cmd, size, timeout=8):
    """Run a one-frame ffmpeg grab and return its raw thumbnail bytes."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return None
    if p.returncode != 0 or len(p.stdout or b"") != size * size:
        return None
    return p.stdout


# The probe runs at 30 fps even though it takes a single frame: ddagrab's first
# frame is the one before the duplication has anything in it, so the filter
# drops frame 0 and keeps frame 1 -- at 1 fps that is a two-second wait, which
# is exactly how "identify the output" turned into a nine-second stall.
_THUMB_FPS = 30


def _thumb_output(idx, ff, size=_THUMB):
    """size x size grayscale thumbnail of one DXGI output."""
    return _run_thumb([ff, "-hide_banner", "-loglevel", "error", "-nostdin",
                       "-init_hw_device", "d3d11va=gpu", "-filter_hw_device", "gpu",
                       "-f", "lavfi", "-i",
                       "ddagrab=output_idx=%d:framerate=%d:draw_mouse=0" % (idx, _THUMB_FPS),
                       "-vf", r"hwdownload,format=bgra,select=gt(n\,0),"
                              "scale=%d:%d,format=gray" % (size, size),
                       "-frames:v", "1", "-f", "rawvideo", "-"], size)


def _thumb_desktop(rect, ff, size=_THUMB):
    """size x size grayscale thumbnail of the desktop region the VDD occupies."""
    return _run_thumb([ff, "-hide_banner", "-loglevel", "error", "-nostdin",
                       "-f", "gdigrab", "-framerate", str(_THUMB_FPS), "-draw_mouse", "0",
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
#
# H.264 and HEVC both arrive as start-code separated NAL units and differ only
# in how the type is packed into the header: H.264 uses one byte with a 5-bit
# type at bit 0, HEVC two bytes with a 6-bit type at bit 1.  Getting this wrong
# is not a subtle degradation -- running HEVC through the H.264 reader finds no
# VCL NAL at all, so every access unit comes back empty and the phone shows
# nothing.  That is why the splitter needs to know the codec.
#
# AV1 is not NAL-based at all; it is split into temporal units further down.
# ---------------------------------------------------------------------------

_NAL_SPECS = {
    CODEC_H264: {
        "hdr": 1,
        "type": lambda b: b[0] & 0x1F,
        "vcl": lambda t: 1 <= t <= 5,
        "key": lambda t: t == 5,            # IDR
    },
    CODEC_HEVC: {
        "hdr": 2,
        "type": lambda b: (b[0] >> 1) & 0x3F,
        "vcl": lambda t: t <= 31,
        "key": lambda t: 16 <= t <= 21,     # BLA_W_LP .. CRA_NUT (all IRAP)
    },
}

# AV1 OBU types that matter here.
_OBU_TEMPORAL_DELIMITER = 2
_OBU_SEQUENCE_HEADER = 1


def _start_codes(buf):
    starts = []
    i = buf.find(b"\x00\x00\x01")
    while i != -1:
        starts.append(i)
        i = buf.find(b"\x00\x00\x01", i + 3)
    return starts


def _obu_stream(buf):
    """Walk a raw OBU stream.

    ffmpeg's `av1` muxer refuses a pipe ("pipe:: Invalid argument"), so AV1 is
    muxed with `-f obu`, which writes bare OBUs with no start codes -- the
    Annex-B scanner finds nothing in it.  Each OBU is a 1-byte header, an
    optional extension byte, and an LEB128 length, which is what this walks.

    Yields (obu_type, start, end) for every *whole* OBU, and stops at the first
    one still arriving.  That is what makes it safe on a live stream: the tail
    is never half-parsed and then re-parsed as if it were a fresh OBU.
    """
    i = 0
    n = len(buf)
    while i < n:
        b0 = buf[i]
        if b0 & 0x80:                                   # forbidden bit set
            i += 1
            continue
        obu_type = (b0 >> 3) & 0x0F
        p = i + 1 + (1 if b0 & 0x04 else 0)             # optional extension byte
        if not (b0 & 0x02) or p >= n:                   # no size field: cannot walk
            return
        size = 0
        shift = 0
        while True:
            if p >= n:
                return
            byte = buf[p]
            p += 1
            size |= (byte & 0x7F) << shift
            shift += 7
            if not (byte & 0x80):
                break
            if shift > 28:
                return
        end = p + size
        if end > n:
            return                                      # still arriving
        yield obu_type, i, end
        i = end


def _split_av1(buf):
    """Split a raw OBU stream into temporal units.

    A temporal unit opens at OBU_TEMPORAL_DELIMITER, which is the boundary
    MediaCodec wants.  The key-frame flag lives inside the OBU frame header and
    is not worth parsing here, so a unit counts as a sync sample when it carries
    a sequence header -- the AV1 encoders repeat that on key frames, the same way
    x264's repeat-headers works for H.264.
    """
    units = []
    cur_start = None
    cur_key = False
    last_end = 0
    for obu_type, s, e in _obu_stream(buf):
        if obu_type == _OBU_TEMPORAL_DELIMITER:
            if cur_start is not None:
                units.append((buf[cur_start:last_end], cur_key))
            cur_start = s
            cur_key = False
        elif cur_start is None:
            cur_start = s                                # stream opened mid-unit
        if obu_type == _OBU_SEQUENCE_HEADER:
            cur_key = True
        last_end = e
    return units, buf[last_end:]


def _nal_info(buf, pos, spec):
    """(is_vcl, is_key, starts_picture) for the NAL unit starting at `pos`."""
    hdr = spec["hdr"]
    p = pos + 3
    if p + hdr >= len(buf):
        return False, False, False
    t = spec["type"](buf[p:p + hdr])
    if not spec["vcl"](t):
        return False, False, False
    sh = p + hdr                     # the slice header starts right after it
    # A new picture begins at the slice whose first field is zero: H.264
    # first_mb_in_slice == 0, HEVC first_slice_segment_in_pic_flag == 1.  Both
    # are the top bit of the first slice-header byte.  Without this test every
    # slice of a multi-slice picture became an access unit of its own -- measured
    # 299 units for a 60-frame libx264 clip that really has 60 pictures.
    first = bool(buf[sh] & 0x80) if sh < len(buf) else False
    return True, spec["key"](t), first


def split_access_units(buf, codec=CODEC_H264):
    """Split a stream into whole access units.

    Returns (units, remainder).  A unit is one coded picture plus whatever
    parameter sets / SEI preceded it - exactly what MediaCodec wants in one
    input buffer.  Anything after the last complete picture is left in
    remainder, so a half-written picture never reaches the decoder.
    """
    if codec == CODEC_AV1:
        return _split_av1(buf)

    spec = _NAL_SPECS.get(codec) or _NAL_SPECS[CODEC_H264]
    starts = _start_codes(buf)
    if len(starts) < 2:
        return [], buf

    units = []
    cur_start = None
    cur_key = False
    pending = None            # parameter sets / SEI waiting for their picture
    for k in range(len(starts) - 1):
        pos = starts[k]
        vcl, key, first = _nal_info(buf, pos, spec)
        if vcl and first:
            if cur_start is not None:
                units.append((buf[cur_start:pos], cur_key))
            cur_start = pending if pending is not None else pos
            cur_key = key
            pending = None
        elif cur_start is None and pending is None:
            pending = pos
    tail = starts[-1]
    if cur_start is not None:
        units.append((buf[cur_start:tail], cur_key))
        remainder = buf[tail:]
    else:
        remainder = buf[pending:] if pending is not None else buf
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
        self.encoder = cfg.get("encoder", "h264_nvenc")
        self.codec = codec_of(self.encoder)
        self.proc = None
        self._stop = threading.Event()
        self._thread = None
        self.unit_queue = []
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self.stats = {"frames": 0, "bytes": 0, "dropped": 0, "t": time.time()}
        self.error = None
        self.backend_used = None
        # -- liveness -------------------------------------------------------
        # ffmpeg's ddagrab gives up as soon as Windows invalidates the desktop
        # duplication: AcquireNextFrame returns 887a0026 (DXGI_ERROR_ACCESS_LOST)
        # and the filter dies with "Generic error in an external library".  That
        # happens on every display-mode change (including one made from Windows'
        # 显示设置 while 投屏 is running) and every time a UAC prompt switches to
        # the secure desktop.  Before the watchdog existed the reader thread just
        # ended, the app kept saying 投屏中, and the phone froze on the last frame
        # until it was stopped by hand.
        self.last_data = time.time()
        self.err_tail = ""
        self.ever_started = False

    # -- liveness ----------------------------------------------------------
    def dead(self):
        """True once ffmpeg has exited (or was never started)."""
        return self.proc is None or self.proc.poll() is not None

    def stalled(self, limit=3.0):
        """True when ffmpeg is alive but has handed over nothing for `limit` s.

        ddagrab duplicates at the requested framerate whether or not the screen
        changes, so a long silence is never just "nothing moved".
        """
        return (not self.dead()) and (time.time() - self.last_data) > limit

    def access_lost(self):
        return "887a0026" in (self.err_tail or "").lower()

    def _drain_err(self, p):
        """Keep the tail of ffmpeg's stderr.

        A pipe nobody reads fills up and blocks ffmpeg, so this runs from the
        moment the process starts -- the tail is what tells us *why* it died.
        """
        try:
            while True:
                chunk = p.stderr.read(4096)
                if not chunk:
                    break
                self.err_tail = (self.err_tail +
                                 chunk.decode("utf-8", "replace"))[-4000:]
        except Exception:
            pass

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
                    i18n.t("无法确定虚拟显示器对应哪个 ddagrab 输出（%s）",
                           "Cannot tell which ddagrab output is the virtual display "
                           "(%s)") %
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
        codec = self.codec or codec_of(enc)
        # Desktop content is mostly static, so constant-bitrate encoding wastes
        # almost the whole budget stuffing identical frames - measured that way
        # a still test pattern still burned the full 20 Mbps.  Quality-targeted
        # VBR sends a few kB/s when nothing moves and only approaches the cap on
        # real motion, which is what a USB link can actually carry.
        rc = c.get("rate_control", "vbr")
        q = int(c.get("quality", 20) or 20)
        cbr = rc == "cbr"
        ceiling = ["-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate]
        # The profile name depends on the codec: "high" is meaningless to HEVC.
        # AV1 gets none at all -- `-profile:v main` makes av1_nvenc refuse to
        # open the encoder ("maybe incorrect parameters such as bit_rate, rate,
        # width or height" in a message that mentions neither profile nor codec),
        # and its default is already the 8-bit 4:2:0 main profile we want.
        profile = {CODEC_H264: "high", CODEC_HEVC: "main"}.get(codec)
        prof_args = ["-profile:v", profile] if profile else []

        if kind == "nvenc":
            preset = c.get("preset") or "p4"
            if preset not in NVENC_PRESETS:
                preset = "p4"
            ctl = (["-rc", "cbr"] if cbr else ["-rc", "vbr", "-cq", str(q)]) + ceiling
            cmd += ["-preset", preset, "-tune", "ll"] + ctl + [
                "-g", str(gop), "-bf", "0", "-zerolatency", "1",
                "-delay", "0"] + prof_args
            if codec == CODEC_H264:
                cmd += ["-aud", "0", "-surfaces", "8"]
            if c.get("spatial_aq"):
                cmd += ["-spatial-aq", "1"]
        elif kind == "amf":
            ctl = ["-rc", "cbr" if cbr else "vbr_peak"] + ceiling
            if not cbr:
                ctl += ["-qp_i", str(q), "-qp_p", str(q)]
            cmd += ["-usage", "ultralowlatency", "-quality", "speed"] + ctl + [
                "-g", str(gop), "-bf", "0"] + prof_args
        elif kind == "qsv":
            preset = c.get("preset") or "veryfast"
            if preset not in X264_PRESETS:
                preset = "veryfast"
            ctl = (["-b:v", bitrate, "-maxrate", bitrate] if cbr else
                   ["-global_quality", str(q)]) + ["-look_ahead", "0"]
            cmd += ["-preset", preset] + ctl + [
                "-g", str(gop), "-bf", "0"] + prof_args
        elif kind == "x265":
            preset = c.get("preset") or "veryfast"
            if preset not in X265_PRESETS:
                preset = "veryfast"
            ctl = (ceiling if cbr else
                   ["-crf", str(q), "-maxrate", bitrate, "-bufsize", bitrate])
            cmd += ["-preset", preset, "-tune", "zerolatency"] + ctl + [
                "-g", str(gop), "-bf", "0"] + prof_args + [
                "-x265-params", "repeat-headers=1:scenecut=0:rc-lookahead=0"]
        elif kind == "svtav1":
            # SVT-AV1's "preset" runs the other way: higher is faster.
            ctl = (ceiling if cbr else ["-crf", str(q)])
            cmd += ["-preset", "10", "-g", str(gop),
                    "-svtav1-params", "rc=1:pred-struct=1"] + ctl
        else:
            preset = c.get("preset") or "veryfast"
            if preset not in X264_PRESETS:
                preset = "veryfast"
            ctl = (ceiling if cbr else
                   ["-crf", str(q), "-maxrate", bitrate, "-bufsize", bitrate])
            cmd += ["-preset", preset, "-tune", "zerolatency"] + ctl + [
                "-g", str(gop), "-bf", "0"] + prof_args + [
                "-x264-params", "repeat-headers=1:scenecut=0:rc-lookahead=0"]

        if backend == "gdigrab":
            cmd += ["-pix_fmt", "yuv420p"]
        # The muxer has to match the codec: `-f h264` on an HEVC bitstream fails
        # outright, and AV1 is not NAL-based at all.  This one word was the
        # reason every encoder except H.264 was unusable.
        # AV1 uses `obu`, not `av1`: the `av1` muxer refuses a pipe outright
        # ("pipe:: Invalid argument"), and `obu` is plain length-delimited OBUs,
        # which the splitter can walk.
        mux = {CODEC_H264: "h264", CODEC_HEVC: "hevc", CODEC_AV1: "obu"}.get(codec, "h264")
        cmd += ["-f", mux, "-flush_packets", "1", "-"]
        return cmd

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        ff = ffmpeg_exe()
        if not ff:
            raise RuntimeError(i18n.t("ffmpeg.exe 未找到", "ffmpeg.exe not found"))
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
            self.ever_started = True
            threading.Thread(target=self._drain_err, args=(p,),
                             name="ffmpeg-stderr", daemon=True).start()
            self.log("  %s 采集已开始 %dx%d@%d" % (backend, self.rect["width"],
                                                   self.rect["height"], self.cfg.get("fps", 60)))
            self._thread = threading.Thread(target=self._reader, name="ffmpeg-reader", daemon=True)
            self._thread.start()
            return backend
        raise RuntimeError(i18n.t("采集启动失败: %s",
                                  "Capture failed to start: %s")
                           % (last or i18n.t("没有可用后端", "no usable backend")))

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
            self.last_data = time.time()
            buf += chunk
            units, buf = split_access_units(buf, self.codec)
            if len(buf) > 16 * 1024 * 1024:
                buf = buf[-1024 * 1024:]
            if units:
                # Stamp each unit with the moment its bytes came out of ffmpeg;
                # the phone turns that into a real end-to-end latency figure.
                # Monotonic, not the wall clock: see host/clock.py.
                stamp = now_us()
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
