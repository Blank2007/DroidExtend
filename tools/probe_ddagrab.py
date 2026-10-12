import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, subprocess, hashlib

FFMPEG = os.path.join(REPO, "tools", "ffmpeg", "bin", "ffmpeg.exe")
OUT = os.path.join(REPO, "tools", "shots")
W, H = 1920, 1080
FS = W * H * 3


def frames(oi, n=8):
    path = os.path.join(OUT, "o%d.rgb" % oi)
    if os.path.exists(path):
        os.remove(path)
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-init_hw_device", "d3d11va=gpu", "-filter_hw_device", "gpu",
           "-f", "lavfi", "-i", "ddagrab=output_idx=%d:framerate=10:draw_mouse=0" % oi,
           "-vf", "hwdownload,format=bgra", "-frames:v", str(n),
           "-pix_fmt", "rgb24", "-f", "rawvideo", path]
    subprocess.run(cmd, capture_output=True)
    d = open(path, "rb").read() if os.path.exists(path) else b""
    out = []
    for i in range(len(d) // FS):
        f = d[i * FS:(i + 1) * FS]
        out.append((min(f), max(f), sum(f) / len(f), hashlib.md5(f).hexdigest()[:10]))
    return out


print("ddagrab output_idx=1 per-frame stats (min,max,mean,md5):")
for i, s in enumerate(frames(1)):
    print("  frame %d: min=%3d max=%3d mean=%8.2f %s" % (i, s[0], s[1], s[2], s[3]))

# reference from gdigrab
ref = os.path.join(OUT, "ref_vdd.rgb")
cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
       "-f", "gdigrab", "-framerate", "1", "-draw_mouse", "0",
       "-offset_x", "2560", "-offset_y", "0", "-video_size", "%dx%d" % (W, H),
       "-i", "desktop", "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", ref]
subprocess.run(cmd, capture_output=True)
r = open(ref, "rb").read()
print("gdigrab ref_vdd: min=%d max=%d mean=%.2f %s" % (min(r), max(r), sum(r) / len(r),
                                                       hashlib.md5(r).hexdigest()[:10]))


def meandiff(a, b):
    n = min(len(a), len(b))
    step = max(1, n // 300000)
    tot = cnt = 0
    for i in range(0, n, step):
        tot += abs(a[i] - b[i])
        cnt += 1
    return tot / max(1, cnt)


d = open(os.path.join(OUT, "o1.rgb"), "rb").read()
for i in range(len(d) // FS):
    f = d[i * FS:(i + 1) * FS]
    print("  dd idx1 frame %d vs gdigrab@2560,0  meandiff=%.2f" % (i, meandiff(f, r)))
