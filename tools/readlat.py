"""How much delay does the stdout drain pattern add?

capture.py reads ffmpeg's stdout with ``p.stdout.read(65536)``.  Python's
BufferedReader.read(n) keeps issuing raw reads until it has n bytes, so a
partial access unit sits in user space until 64 KiB have piled up behind it.
At 2.7 Mbps that is ~190 ms of pure added latency; at 20 Mbps ~26 ms.

``read1(n)`` returns as soon as the raw stream yields anything, so a frame
reaches the queue the moment ffmpeg flushes it.

This runs the real capture command twice and reports the gap between
consecutive drains -- that gap is how long a frame waits in the buffer.

    python tools/readlat.py [seconds]
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))

import win_display as wd          # noqa: E402
from capture import Capture       # noqa: E402
from paths import ffmpeg_exe      # noqa: E402

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0


def target_rect():
    for m in wd.live_monitors():
        if not m["primary"]:
            return m
    raise SystemExit("没有第二块屏幕；先把虚拟显示器打开")


def drain(proc, mode, seconds):
    """Return (chunks, bytes, gaps) for one drain pattern."""
    gaps = []
    total = 0
    chunks = 0
    last = None
    end = time.time() + seconds
    while time.time() < end:
        if mode == "read":
            buf = proc.stdout.read(65536)
        else:
            buf = proc.stdout.read1(65536)
        now = time.time()
        if not buf:
            break
        if last is not None:
            gaps.append(now - last)
        last = now
        total += len(buf)
        chunks += 1
    return chunks, total, gaps


def report(mode, seconds, chunks, total, gaps):
    if not gaps:
        print("  %-6s 没有数据" % mode)
        return
    gaps.sort()
    mean = sum(gaps) / len(gaps)
    p50 = gaps[len(gaps) // 2]
    p90 = gaps[int(len(gaps) * 0.9)]
    mbps = total * 8 / seconds / 1e6
    print("  %-6s %5.2f Mbps  %4d 次  %6.1f KB/次 | 间隔 平均 %6.1f ms  "
          "中位 %6.1f ms  p90 %6.1f ms  最大 %6.1f ms"
          % (mode, mbps, chunks, total / 1024.0 / max(chunks, 1),
             mean * 1000, p50 * 1000, p90 * 1000, gaps[-1] * 1000))


def main():
    rect = target_rect()
    print("采集目标 %s  %dx%d@%d @(%d,%d)"
          % (rect["name"], rect["width"], rect["height"], rect["hz"],
             rect["left"], rect["top"]))
    cfg = {
        "width": rect["width"], "height": rect["height"], "fps": 60,
        "encoder": "h264_nvenc", "preset": "p4", "bitrate_mbps": 40,
        "rate_control": "vbr", "quality": 20, "gop": 0, "draw_mouse": False,
        "spatial_aq": True, "backend": "ddagrab",
    }
    cap = Capture(cfg, rect, log=lambda m: None)
    cmd = cap.command("ddagrab")
    print("命令: " + " ".join(cmd[:10]) + " …")

    for mode in ("read", "read1"):
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, bufsize=-1)
        time.sleep(1.5)          # let the pipeline fill
        chunks, total, gaps = drain(proc, mode, SECONDS)
        proc.kill()
        proc.wait()
        report(mode, SECONDS, chunks, total, gaps)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
