import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, subprocess, hashlib

FFMPEG = os.path.join(REPO, "tools", "ffmpeg", "bin", "ffmpeg.exe")
OUT = os.path.join(REPO, "tools", "shots")
os.makedirs(OUT, exist_ok=True)
W, H = 1920, 1080


def grab(name, args, tail=("hwdownload,format=bgra",)):
    path = os.path.join(OUT, name + ".rgb")
    if os.path.exists(path):
        os.remove(path)
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"] + args
    if tail:
        cmd += ["-vf", tail[0]]
    cmd += ["-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", path]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0 or not os.path.exists(path):
        msg = (p.stderr or "").strip().splitlines()
        print("%-22s FAILED  %s" % (name, msg[-1][:110] if msg else ""))
        return None
    d = open(path, "rb").read()
    print("%-22s %9d  %s" % (name, len(d), hashlib.md5(d).hexdigest()[:12]))
    return d


print("references (gdigrab):")
grab("ref_primary", ["-f", "gdigrab", "-framerate", "1", "-draw_mouse", "0",
                     "-offset_x", "0", "-offset_y", "0",
                     "-video_size", "%dx%d" % (W, H), "-i", "desktop"], tail=None)
grab("ref_vdd", ["-f", "gdigrab", "-framerate", "1", "-draw_mouse", "0",
                 "-offset_x", "2560", "-offset_y", "0",
                 "-video_size", "%dx%d" % (W, H), "-i", "desktop"], tail=None)
print()
print("ddagrab allow_fallback=0:")
for dev in range(0, 4):
    for oi in range(0, 3):
        grab("nf_g%d_o%d" % (dev, oi),
             ["-init_hw_device", "d3d11va=gpu:%d" % dev, "-filter_hw_device", "gpu",
              "-f", "lavfi", "-i",
              "ddagrab=output_idx=%d:framerate=1:video_size=%dx%d:draw_mouse=0:allow_fallback=0"
              % (oi, W, H)])
