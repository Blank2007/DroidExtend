import os, re, subprocess, hashlib

FFMPEG = r"D:\dsh projects\droid-extend\tools\ffmpeg\bin\ffmpeg.exe"
OUT = r"D:\dsh projects\droid-extend\tools\shots"
os.makedirs(OUT, exist_ok=True)
W, H = 1920, 1080
FS = W * H * 3

# does select=gt(n\,0) drop the black first frame?  (raw capture path)
path = os.path.join(OUT, "sel.rgb")
if os.path.exists(path):
    os.remove(path)
cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
       "-init_hw_device", "d3d11va=gpu", "-filter_hw_device", "gpu",
       "-f", "lavfi", "-i", "ddagrab=output_idx=1:framerate=10:draw_mouse=0",
       "-vf", "hwdownload,format=bgra,select=gt(n\\,0)",
       "-frames:v", "4", "-pix_fmt", "rgb24", "-f", "rawvideo", path]
p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
print("select raw rc=%d" % p.returncode, (p.stderr or "").strip()[:200])
if os.path.exists(path):
    d = open(path, "rb").read()
    print("  bytes=%d frames=%d" % (len(d), len(d) // FS))
    for i in range(len(d) // FS):
        f = d[i * FS:(i + 1) * FS]
        print("    frame %d: min=%3d max=%3d mean=%7.2f %s" % (i, min(f), max(f),
              sum(f) / len(f), hashlib.md5(f).hexdigest()[:10]))
else:
    print("  NO FILE")

# does the nvenc path accept select= and produce a decodable stream?
enc = os.path.join(OUT, "sel.h264")
if os.path.exists(enc):
    os.remove(enc)
cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
       "-init_hw_device", "d3d11va=gpu", "-filter_hw_device", "gpu",
       "-f", "lavfi", "-i", "ddagrab=output_idx=1:framerate=60:draw_mouse=0",
       "-vf", "select=gt(n\\,0)",
       "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr",
       "-b:v", "20M", "-maxrate", "20M", "-bufsize", "20M", "-g", "120",
       "-bf", "0", "-zerolatency", "1", "-profile:v", "high", "-delay", "0",
       "-aud", "0", "-surfaces", "8",
       "-t", "2", "-f", "h264", "-flush_packets", "1", enc]
p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
print("nvenc select rc=%d" % p.returncode, (p.stderr or "").strip()[:300])
if os.path.exists(enc):
    d = open(enc, "rb").read()
    print("  h264 bytes=%d" % len(d))
    # decode it back and check the FIRST decoded frame is not black
    dec = os.path.join(OUT, "sel_dec.rgb")
    if os.path.exists(dec):
        os.remove(dec)
    p2 = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                         "-i", enc, "-frames:v", "3", "-pix_fmt", "rgb24",
                         "-f", "rawvideo", dec], capture_output=True, text=True)
    print("  decode rc=%d %s" % (p2.returncode, (p2.stderr or "").strip()[:200]))
    if os.path.exists(dec):
        dd = open(dec, "rb").read()
        for i in range(len(dd) // FS):
            f = dd[i * FS:(i + 1) * FS]
            print("    decoded frame %d: min=%3d max=%3d mean=%7.2f" % (i, min(f), max(f), sum(f) / len(f)))
