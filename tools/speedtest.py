#!/usr/bin/env python3
"""Measure raw vs proxied download speed for the toolchain mirrors."""
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch  # noqa: E402

PROXY = os.environ.get("DE_PROXY", "http://127.0.0.1:7897")

URLS = [
    ("gyan ffmpeg", "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"),
    ("BtbN ffmpeg", "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"),
    ("temurin jdk17", "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse"),
    ("cmdline-tools", "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"),
    ("build-tools34", "https://dl.google.com/android/repository/build-tools_r34-windows.zip"),
]

CHUNK = 262144
LIMIT = 6 * 1024 * 1024


def probe(url, opener, label):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": fetch.UA, "Range": "bytes=0-%d" % (LIMIT - 1)})
        t0 = time.time()
        got = 0
        with opener.open(req, timeout=25) as r:
            while got < LIMIT:
                c = r.read(CHUNK)
                if not c:
                    break
                got += len(c)
        dt = max(time.time() - t0, 0.001)
        print("  %-14s %-14s %7.2f MB in %5.1fs = %7.0f KB/s" % (label, "", got / 1e6, dt, got / dt / 1024), flush=True)
    except Exception as e:  # noqa: BLE001
        print("  %-14s %-14s FAIL %s" % (label, "", str(e)[:90]), flush=True)


def main():
    direct = urllib.request.build_opener()
    proxied = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    print("proxy:", PROXY, flush=True)
    for name, url in URLS:
        print(name, flush=True)
        probe(url, direct, "direct")
        probe(url, proxied, "proxy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
