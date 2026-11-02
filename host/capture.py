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
# Why the last sweep could not open each (adapter, output), so a log can say it
# out loud.  Empty when everything the machine has was openable; missing
# indexes simply were openable.
_DDA_ERRORS = {}
# adapter index -> "NVIDIA GeForce RTX 5070 Laptop GPU" (as DXGI names it)
_DDA_ADAPTERS = {}
_THUMB = 16               # thumbnail edge, in pixels, used to compare outputs
_THUMB_MAX_DIFF = 12.0    # 0..255 mean absolute difference that still counts as "same picture"
_THUMB_MARGIN = 4.0       # how far the winner must beat the runner-up

# ddagrab has no adapter argument: which GPU it duplicates is decided by
# -init_hw_device alone, and without one it always takes adapter 0.  On a
# laptop with both an integrated and a discrete GPU the panel and a virtual
# display can sit on different adapters, so "ddagrab 输出: 未发现" is usually
# "looked at the wrong GPU", not "there is nothing to capture".  Measured on
# this machine: adapter 0 (NVIDIA) and adapter 1 (AMD) both enumerate the
# built-in panel, and an adapter index that does not exist is silently
# answered with the default GPU instead of an error -- so a repeated device id
# is the only way to tell "no such adapter".
# Five outputs per adapter is more than any desktop: a machine with four
# monitors and a virtual display is already unusual, and every extra index is
# another 0.34 s ffmpeg probe on the path to the first frame.
_MAX_ADAPTERS = 4
_MAX_OUTPUTS = 5
_ADAPTER_RE = re.compile(r"Using device ([0-9a-fA-F]{4}:[0-9a-fA-F]{4}) \((.*)$", re.M)


def _dda_reason(stderr):
    """The one line of a failed ddagrab probe that explains it."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    for ln in lines:
        if "ddagrab" in ln and ("ailed" in ln or "rror" in ln or "not" in ln):
            return ln[:160]
    for ln in lines:
        if "iled" in ln or "rror" in ln:
            return ln[:160]
    return lines[-1][:160] if lines else ""


def _adapter_of(stderr):
    """Which GPU ffmpeg really opened: (vendor:device, name) or None.

    ffmpeg answers -init_hw_device with a line of the form
    "Using device 10de:2d18 (NVIDIA GeForce RTX 5070 Laptop GPU)", and that
    line is the only proof that adapter N exists: for an index past the end
    of the list ffmpeg quietly falls back to the default adapter.
    """
    m = _ADAPTER_RE.search(stderr or "")
    if not m:
        return None
    name = m.group(2)
    k = name.rfind(")")
    if k >= 0:
        name = name[:k]
    return m.group(1).lower(), name.strip()


def _gpu_probe(ff, adapter, idx, timeout):
    """One one-frame ddagrab grab on *adapter*, output *idx*."""
    # framerate=30, not 1: ddagrab paces its output, so at 1 fps the first
    # frame costs a whole second (measured: 2.25 s at 1, 0.31 s at 30).
    cmd = [ff, "-hide_banner", "-loglevel", "verbose", "-nostdin",
           "-init_hw_device", "d3d11va=gpu:%d" % adapter,
           "-filter_hw_device", "gpu",
           "-f", "lavfi", "-i",
           "ddagrab=output_idx=%d:framerate=30:draw_mouse=0" % idx,
           "-vf", "hwdownload,format=bgra,showinfo",
           "-frames:v", "1", "-f", "null", "-"]
    return subprocess.run(cmd, capture_output=True, timeout=timeout,
                          encoding="utf-8", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def detect_ddagrab_outputs(ffmpeg=None, limit=None, timeout=25):
    """Return [(adapter, output_idx, width, height)] for every DXGI output.

    All adapters are swept, not just the default one -- see _MAX_ADAPTERS for
    the machine that made this necessary.  Failures are kept in _DDA_ERRORS
    ((adapter, output) -> one line) instead of being swallowed.

    The sweeps run side by side (four ffmpeg probes at a time) because each one
    costs ~0.34 s of startup and this runs every time the capture is restarted:
    sweeping four adapters one after another took 2.7 s, in parallel it takes
    ~0.7 s.  Phase one asks every adapter for output 0, which is also how an
    adapter index past the end of the list is detected; phase two fills in the
    remaining outputs of the adapters that answered.
    """
    global _DDA_ERRORS, _DDA_ADAPTERS
    ff = ffmpeg or ffmpeg_exe()
    if not ff:
        return []
    _DDA_ERRORS = {}
    _DDA_ADAPTERS = {}
    found = []
    limit = limit or _MAX_OUTPUTS
    workers = 4

    def run(pairs):
        out = {}
        if not pairs:
            return out
        lock = threading.Lock()

        def one(pair):
            adapter, idx = pair
            try:
                p = _gpu_probe(ff, adapter, idx, timeout)
            except Exception:
                return
            with lock:
                out[pair] = p

        threads = []
        for pair in pairs:
            if len(threads) >= workers:
                threads.pop(0).join()
            t = threading.Thread(target=one, args=(pair,), name="dda-probe", daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        return out

    # -- phase 1: does each adapter exist, and does its output 0 work? ------
    first = run([(a, 0) for a in range(_MAX_ADAPTERS)])
    alive = []
    seen = {}                 # "10de:2d18" -> the adapter index that first had it
    for adapter in range(_MAX_ADAPTERS):
        p = first.get((adapter, 0))
        if p is None:
            break
        who = _adapter_of(p.stderr)
        if not who:
            break
        if who[0] in seen:
            # ffmpeg answers an index past the end of the adapter list with the
            # default GPU instead of an error, so a repeated device id is the
            # only sign that this adapter does not exist.
            break
        seen[who[0]] = adapter
        _DDA_ADAPTERS[adapter] = who[1]
        alive.append(adapter)

    # -- phase 2: the remaining outputs -------------------------------------
    rest = [(a, i) for a in alive for i in range(1, limit)]
    later = run(rest)

    for adapter in alive:
        misses = 0
        for idx in range(limit):
            p = first.get((adapter, 0)) if idx == 0 else later.get((adapter, idx))
            if p is None:
                break
            if p.returncode != 0:
                # A gap does not mean the list ended: an unavailable output
                # simply disappears and the next index can still be live.
                _DDA_ERRORS[(adapter, idx)] = _dda_reason(p.stderr)
                misses += 1
                if misses >= 3:
                    break
                continue
            misses = 0
            m = re.search(r"s:(\d+)x(\d+)", p.stderr or "")
            if not m:
                break
            found.append((adapter, idx, int(m.group(1)), int(m.group(2))))
    return found


def _norm_gpu(name):
    """A card name reduced to what two sources can agree on.

    The monitor enumerator and the ddagrab device log write the same card
    differently -- "AMD Radeon(TM) 610M" against "AMD Radeon 610M" -- so the
    comparison strips the trademark marks, the case and the spacing.
    """
    n = (name or "").lower()
    for junk in ("(tm)", "(r)", chr(174), chr(8482), "graphics", "inc.", "corp."):
        n = n.replace(junk, " ")
    return " ".join(n.split())


def _label(adapter, idx):
    """How to name an (adapter, output) pair in a log line."""
    if adapter == 0:
        return "#%d" % idx
    return "显卡%d 的 #%d" % (adapter, idx)


def describe_outputs(outs=None):
    """One readable line about a sweep: which GPU, which outputs, what sizes."""
    outs = ddagrab_outputs() if outs is None else outs
    if not outs:
        if not _DDA_ADAPTERS:
            return "无"
        return "、".join('显卡%d "%s"' % (a, n) for a, n in sorted(_DDA_ADAPTERS.items()))
    order = []
    for adapter, _, _, _ in outs:
        if adapter not in order:
            order.append(adapter)
    parts = []
    for adapter in order:
        items = ["#%d=%dx%d" % (i, w, h) for a, i, w, h in outs if a == adapter]
        name = _DDA_ADAPTERS.get(adapter)
        head = "显卡%d" % adapter + (' "%s"' % name if name else "")
        parts.append(head + " " + ", ".join(items))
    return " | ".join(parts)


def ddagrab_adapter_names():
    """adapter index -> GPU name, from the last sweep."""
    return dict(_DDA_ADAPTERS)


def ddagrab_outputs(ffmpeg=None, refresh=False):
    global _DDA_OUTPUTS
    if _DDA_OUTPUTS is None or refresh:
        _DDA_OUTPUTS = detect_ddagrab_outputs(ffmpeg)
    return _DDA_OUTPUTS


def ddagrab_errors():
    """output index -> why the last sweep could not open it (may be empty)."""
    return dict(_DDA_ERRORS)


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


def _thumb_output(adapter, idx, ff, size=_THUMB):
    """size x size grayscale thumbnail of one DXGI output on *adapter*."""
    return _run_thumb([ff, "-hide_banner", "-loglevel", "error", "-nostdin",
                       "-init_hw_device", "d3d11va=gpu:%d" % adapter,
                       "-filter_hw_device", "gpu",
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
    """Which of *outs* really shows the monitor described by *rect*?

    *outs* entries are (adapter, output, width, height); the answer is
    (adapter, output), or None when the sweep cannot decide.
    """
    log = log or (lambda m: None)
    w, h = int(rect["width"]), int(rect["height"])
    same = [o for o in outs if (o[2], o[3]) == (w, h)]
    if not same:
        return None
    if len(same) == 1:
        return same[0][0], same[0][1]
    # The monitor itself knows which card drives it (DeviceString is the
    # adapter name) and the sweep knows which card each output lives on.
    # When those two agree on one candidate there is nothing left to guess;
    # that is what settles a laptop exposing the same panel through both its
    # integrated and its discrete GPU.
    want = _norm_gpu(rect.get("desc"))
    if want:
        by_gpu = [o for o in same if _norm_gpu(_DDA_ADAPTERS.get(o[0])) == want]
        if len(by_gpu) == 1:
            log("ddagrab: 这块屏在 %s（显示器报告的显卡名对上了）"
                % _label(by_gpu[0][0], by_gpu[0][1]))
            return by_gpu[0][0], by_gpu[0][1]
        if by_gpu:
            same = by_gpu
            if len(same) == 1:
                return same[0][0], same[0][1]
            log("ddagrab: 同一张显卡上有 %d 个同尺寸输出，按画面比对" % len(same))
    else:
        log("ddagrab: 显示器没报告显卡名，按画面比对（待选 %d 个）" % len(same))

    ref = _thumb_desktop(rect, ff)
    if ref is None:
        return None
    scored = []
    for adapter, idx, _, _ in same:
        t = _thumb_output(adapter, idx, ff)
        if t is not None:
            scored.append((_meandiff(ref, t), adapter, idx))
    if not scored:
        return None
    scored.sort()
    log("ddagrab: %d 个输出都是 %dx%d，按画面比对 %s" % (
        len(same), w, h,
        ", ".join(_label(a, i) + "=%.1f" % d for d, a, i in scored)))
    best_d, best_a, best_i = scored[0]
    if best_d > _THUMB_MAX_DIFF:
        log("ddagrab: 画面差异过大（%.1f），不猜哪个是这块屏" % best_d)
        return None
    if len(scored) > 1 and scored[1][0] - best_d < _THUMB_MARGIN:
        # Two outputs showing the same picture to within a couple of grey
        # levels: on a laptop with an integrated and a discrete GPU the same
        # panel is enumerated on both, and either answer is right because both
        # are the same picture.  Refusing to choose here used to push every
        # extend-mode session onto gdigrab, so pick the first candidate -- the
        # lowest adapter and output index, which is the old, known-good default.
        log("ddagrab: 两个输出画面几乎一样（%.1f vs %.1f），选 %s"
            % (best_d, scored[1][0], _label(best_a, best_i)))
        return best_a, best_i
    log("ddagrab: 这块屏是 %s（画面差异 %.1f）" % (_label(best_a, best_i), best_d))
    return best_a, best_i


def pick_ddagrab_output(rect, ffmpeg=None, log=None):
    """The (adapter, output) showing the monitor in *rect*, or None if unsure.

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
    pick = identify_output(rect, outs, ff, log)
    if pick is None:
        log("ddagrab: 找不到这块屏对应的输出（探测到 %s）" % describe_outputs(outs))
    return pick


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
        # Which GPU / output ddagrab ended up duplicating (None for gdigrab).
        self.dda_adapter = None
        self.dda_output = None
        self.dda_gpu = None
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
            if override >= 0:
                adapter = int(c.get("dda_adapter", 0))
                idx = override
            else:
                pick = pick_ddagrab_output(self.rect, ff, self.log)
                if pick is None:
                    # Refusing here is the whole point: assuming output 0 would
                    # stream the laptop's own screen whenever it happens to share
                    # the screen's resolution.  start() turns this into a
                    # gdigrab attempt, which is exact.
                    raise RuntimeError(
                        i18n.t("无法确定这块屏对应哪个 ddagrab 输出（%s）",
                               "Cannot tell which ddagrab output is this screen "
                               "(%s)") % describe_outputs(ddagrab_outputs(ff)))
                adapter, idx = pick
            self.dda_adapter = adapter
            self.dda_output = idx
            self.dda_gpu = ddagrab_adapter_names().get(adapter)
            native = None
            for a, i, ww, hh in ddagrab_outputs(ff):
                if (a, i) == (adapter, idx):
                    native = (ww, hh)
            if native and native != (w, h):
                self.log("ddagrab: %s 是 %dx%d，缩放到 %dx%d" %
                         (_label(adapter, idx), native[0], native[1], w, h))
            cmd += ["-init_hw_device", "d3d11va=gpu:%d" % adapter,
                    "-filter_hw_device", "gpu"]
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
            self.dda_adapter = self.dda_output = self.dda_gpu = None
            cmd += ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", mouse,
                    "-offset_x", str(int(self.rect["left"])),
                    "-offset_y", str(int(self.rect["top"])),
                    "-video_size", "%dx%d" % (w, h),
                    "-i", "desktop"]
            # gdigrab's -framerate only asks Windows how often to look, and it
            # does not cap the answer: measured here, asking for 90 fps on a
            # 300 Hz desktop produced 183 fps.  Sending more frames than the
            # phone can show is the exact failure the panel-rate work removed
            # (the decoder saturates, the socket backlogs, the app jumps from
            # keyframe to keyframe), so the rate is enforced downstream too.
            cmd += ["-vf", "fps=%d" % fps]

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
                # Name the GPU, always: "which graphics card is being used" was
                # a question the log could not answer, and on a hybrid laptop it
                # is the difference between working and a black screen.
                known = ddagrab_adapter_names()
                if known:
                    self.log("ddagrab 显卡: " + "、".join(
                        '显卡%d "%s"' % (a, n) for a, n in sorted(known.items())))
                self.log("ddagrab 输出: " + describe_outputs(outs))
                if not outs:
                    self.log("  ddagrab 一个输出也打不开，只能改用 gdigrab 抓桌面（画质与"
                             "延迟会差一些）。原因如下，把这段日志发出来就能定位:")
                    errs = ddagrab_errors()
                    for key in sorted(errs)[:6]:
                        self.log("    显卡%d #%d: %s" % (key[0], key[1], errs[key] or "?"))
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
