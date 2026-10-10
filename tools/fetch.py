#!/usr/bin/env python3
"""Small, dependency-free downloader + extractor used to provision the toolchain.

Usage:
    python fetch.py get  <url> <outfile>
    python fetch.py zip  <url> <outdir>          # download then extract zip
    python fetch.py txz  <url> <outdir>          # download then extract .tar.xz
    python fetch.py text <url>                   # print body to stdout
"""
import os
import shutil
import sys
import tarfile
import time
import urllib.request
import zipfile

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) droid-extend-fetch/1.0"


def _open(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=90)


def download(url, outfile, tries=4):
    os.makedirs(os.path.dirname(os.path.abspath(outfile)), exist_ok=True)
    for attempt in range(1, tries + 1):
        try:
            with _open(url) as r, open(outfile, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                t0 = time.time()
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total and got % (16 << 20) < (1 << 20):
                        pct = got * 100.0 / total
                        speed = got / max(time.time() - t0, 0.01) / 1e6
                        print(f"  {pct:5.1f}%  {got/1e6:8.1f}/{total/1e6:.1f} MB  {speed:5.1f} MB/s", flush=True)
            size = os.path.getsize(outfile)
            if total and size != total:
                raise IOError(f"short read {size} != {total}")
            print(f"[ok] {outfile} ({size/1e6:.1f} MB)", flush=True)
            return outfile
        except Exception as e:  # noqa: BLE001
            print(f"[warn] attempt {attempt}/{tries} failed: {e}", flush=True)
            if attempt == tries:
                raise
            time.sleep(3 * attempt)
    return outfile


def unzip(path, outdir, strip_top=False):
    os.makedirs(outdir, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        top = None
        if strip_top:
            tops = {n.split("/")[0] for n in names if "/" in n}
            if len(tops) == 1:
                top = tops.pop() + "/"
        for m in z.infolist():
            name = m.filename
            if top and name.startswith(top):
                name = name[len(top):]
            if not name or name.endswith("/"):
                continue
            target = os.path.join(outdir, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(m) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            mode = m.external_attr >> 16
            if mode:
                os.chmod(target, mode)
    print(f"[ok] extracted -> {outdir}", flush=True)


def untxz(path, outdir, strip_top=True):
    os.makedirs(outdir, exist_ok=True)
    with tarfile.open(path, "r:xz") as t:
        members = t.getmembers()
        top = None
        if strip_top:
            tops = {m.name.split("/")[0] for m in members if "/" in m.name}
            if len(tops) == 1:
                top = tops.pop() + "/"
        for m in members:
            name = m.name
            if top and name.startswith(top):
                name = name[len(top):]
            if not name:
                continue
            m.name = name
            t.extract(m, outdir, filter="data")
    print(f"[ok] extracted -> {outdir}", flush=True)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd, url = sys.argv[1], sys.argv[2]
    if cmd == "text":
        with _open(url) as r:
            sys.stdout.write(r.read().decode("utf-8", "replace"))
        return 0
    dest = sys.argv[3]
    if cmd == "get":
        download(url, dest)
    elif cmd == "zip":
        tmp = os.path.join(os.environ.get("TEMP", "."), os.path.basename(dest) + ".zip")
        download(url, tmp)
        unzip(tmp, dest, strip_top=True)
        os.remove(tmp)
    elif cmd == "txz":
        tmp = os.path.join(os.environ.get("TEMP", "."), os.path.basename(dest) + ".tar.xz")
        download(url, tmp)
        untxz(tmp, dest, strip_top=True)
        os.remove(tmp)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
