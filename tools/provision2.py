"""Fetch what the APK build still needs: android.jar + build-tools.

We do NOT need the Android command line tools (150 MB): dl.google.com serves the
build-tools and platform archives directly and unauthenticated, and that is
everything aapt2 / d8 / apksigner / android.jar live in.

Already satisfied locally, so not downloaded:
  ffmpeg 6.0 with --enable-nvenc  -> C:\\Users\\...\\.dsh-wallpaper-engine\\ffmpeg
  OpenJDK 21.0.7 (full JDK)       -> ...\\.minecraft\\runtime\\java-runtime-delta
"""
import os
import re
import sys
import json
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "tools")
REPO = "https://dl.google.com/android/repository/repository2-3.xml"

WANT = [
    ("build-tools;34.0.0", r"build-tools_r34-windows\.zip"),
    ("platforms;android-34", r"platform-34[^\"<]*\.zip"),
]


def discover():
    """Scrape the SDK repository manifest for the exact archive names."""
    print("reading", REPO, flush=True)
    xml = dl.open_url(REPO, timeout=45).read().decode("utf-8", "replace")
    print("  %d bytes of manifest" % len(xml), flush=True)
    url_base = "https://dl.google.com/android/repository/"
    found = {}
    for label, pattern in WANT:
        # every <archive> block that carries the wanted file name
        hits = re.findall(r"<url>([^<]*%s)</url>" % pattern, xml)
        if not hits:
            continue
        # prefer a windows host-os variant, else the plain one
        win = [h for h in hits if "windows" in h.lower()]
        found[label] = url_base + (win[0] if win else hits[0])
    return found


def main():
    proxy = dl.PROXY if dl.probe(dl.PROXY) else None
    print("proxy:", proxy or "(direct)", flush=True)
    urls = {}
    try:
        urls = discover()
    except Exception as e:
        print("manifest scrape failed (%s); using known-good urls" % e, flush=True)
    urls.setdefault("build-tools;34.0.0", "https://dl.google.com/android/repository/build-tools_r34-windows.zip")
    urls.setdefault("platforms;android-34", "https://dl.google.com/android/repository/platform-34-ext7_r03.zip")

    os.makedirs(OUT, exist_ok=True)
    manifest = {}
    for label, url in urls.items():
        name = url.rsplit("/", 1)[-1]
        dest = os.path.join(OUT, name)
        print("\n== %s\n   %s" % (label, url), flush=True)
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            print("   already have", name, os.path.getsize(dest), flush=True)
        else:
            try:
                n, dt = dl.get(url, dest, proxy=proxy, label=name)
                print("   %s: %.1f MB in %.1fs (%.0f KB/s)" % (name, n / 1e6, dt, n / dt / 1024), flush=True)
            except Exception as e:
                print("   FAILED (proxy): %s -- retrying direct" % e, flush=True)
                n, dt = dl.get(url, dest, proxy=None, label=name)
                print("   %s (direct): %.1f MB in %.1fs" % (name, n / 1e6, dt), flush=True)
        manifest[label] = {"url": url, "file": dest, "size": os.path.getsize(dest)}

    # unpack into an sdk/ tree that looks like a normal ANDROID_HOME
    import zipfile
    sdk = os.path.join(ROOT, "tools", "sdk")
    for label, info in manifest.items():
        target = os.path.join(sdk, *label.split(";"))
        if os.path.isdir(target):
            print("already unpacked:", target, flush=True)
            continue
        print("unpacking %s -> %s" % (os.path.basename(info["file"]), target), flush=True)
        tmp = target + ".tmp"
        os.makedirs(tmp, exist_ok=True)
        with zipfile.ZipFile(info["file"]) as z:
            z.extractall(tmp)
        # android archives wrap everything in one top-level folder
        entries = os.listdir(tmp)
        if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
            os.replace(os.path.join(tmp, entries[0]), target)
            os.rmdir(tmp)
        else:
            os.replace(tmp, target)
        print("   ->", target, flush=True)

    with open(os.path.join(OUT, "sdk_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print("\nALL DONE", flush=True)


if __name__ == "__main__":
    main()
