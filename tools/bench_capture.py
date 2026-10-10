import os, re, subprocess, sys, time

FFMPEG = r"D:\dsh projects\droid-extend\tools\ffmpeg\bin\ffmpeg.exe"
OUT = r"D:\dsh projects\droid-extend\tools\shots"
os.makedirs(OUT, exist_ok=True)

TAIL_ENC = ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr",
            "-b:v", "20M", "-maxrate", "20M", "-bufsize", "20M", "-g", "120",
            "-bf", "0", "-zerolatency", "1", "-profile:v", "high",
            "-f", "null", "-"]


def bench(label, args, dur=4):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "info", "-nostdin"] + args + \
          ["-t", str(dur)] + TAIL_ENC
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    dt = time.time() - t0
    err = p.stderr or ""
    stat = ""
    for l in err.splitlines():
        if "frame=" in l and "fps=" in l:
            stat = l.strip()
    m = re.search(r"fps=\s*([0-9.]+)", stat)
    print("  %-40s %-6s  %5.1fs  %s" % (label, m.group(1) if m else "ERR", dt,
                                        stat[:70] if stat else (err.strip().splitlines() or [""])[-1][:70]))


def blackcheck(label, args, n=5):
    path = os.path.join(OUT, "probe.rgb")
    if os.path.exists(path):
        os.remove(path)
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"] + args + \
          ["-frames:v", str(n), "-pix_fmt", "rgb24", "-f", "rawvideo", path]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not os.path.exists(path):
        print("  %-40s FAILED %s" % (label, (p.stderr or "").strip().splitlines()[-1][:80]))
        return
    d = open(path, "rb").read()
    print("  %-40s %d frames  mean=%.2f  max=%d" % (label, len(d) // (1920 * 1080 * 3) or len(d) // (2560 * 1600 * 3),
                                                    sum(d) / max(1, len(d)), max(d)))


print("=== gdigrab throughput (1920x1080 region at 2560,0) ===")
for w, h in ((1280, 720), (1920, 1080), (2560, 1600)):
    bench("gdigrab %dx%d" % (w, h),
          ["-f", "gdigrab", "-framerate", "60", "-draw_mouse", "0",
           "-offset_x", "2560", "-offset_y", "0", "-video_size", "%dx%d" % (w, h),
           "-i", "desktop", "-pix_fmt", "yuv420p"])

print()
print("=== gdigrab throughput on primary 1920x1080 region ===")
bench("gdigrab primary 1920x1080",
      ["-f", "gdigrab", "-framerate", "60", "-draw_mouse", "0",
       "-offset_x", "0", "-offset_y", "0", "-video_size", "1920x1080",
       "-i", "desktop", "-pix_fmt", "yuv420p"])

print()
print("=== ddagrab blackness probes (native sizes) ===")
for dev in (0, 1):
    for oi, sz in ((0, (2560, 1600)), (1, (1920, 1080))):
        blackcheck("ddagrab gpu:%d out%d %s" % (dev, oi, sz),
                   ["-init_hw_device", "d3d11va=gpu:%d" % dev, "-filter_hw_device", "gpu",
                    "-f", "lavfi", "-i",
                    "ddagrab=output_idx=%d:framerate=10:video_size=%dx%d" % (oi, sz[0], sz[1]),
                    "-vf", "hwdownload,format=bgra"])
