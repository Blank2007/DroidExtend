#!/usr/bin/env python3
"""Download the virtual-display-driver candidates into tools/vdd/."""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch  # noqa: E402

OUT = os.path.join(HERE, "vdd")
os.makedirs(OUT, exist_ok=True)

ITEMS = [
    ("nefcon.zip",
     "https://github.com/nefarius/nefcon/releases/download/v1.14.0/nefcon_v1.14.0.zip"),
    ("vdd25.zip",
     "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/download/25.7.23/"
     "VirtualDisplayDriver-x86.Driver.Only.zip"),
    ("parsecvdd.zip",
     "https://github.com/nomi-san/parsec-vdd/releases/download/v0.45.1/"
     "ParsecVDisplay-v0.45-portable.zip"),
]


def main():
    for name, url in ITEMS:
        dest = os.path.join(OUT, name)
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            print("[skip]", name, flush=True)
        else:
            print("=== download", name, flush=True)
            try:
                fetch.download(url, dest)
            except Exception as e:  # noqa: BLE001
                print("[FAIL]", name, e, flush=True)
                continue
        try:
            with zipfile.ZipFile(dest) as z:
                print("    contents:", flush=True)
                for n in z.namelist():
                    print("      ", n, z.getinfo(n).file_size, flush=True)
        except Exception as e:  # noqa: BLE001
            print("    [not a zip]", e, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
