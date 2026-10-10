"""Probe candidate Windows virtual-display drivers for installability facts."""
import json
import re
import sys
import urllib.request

UA = {"User-Agent": "droid-extend-probe/1.0"}
KEY = re.compile(r"sign|test\s*mode|testsigning|pnputil|devcon|install|admin|whql|attestation|"
                 r"secure\s*boot|license|parsec|\.inf|\.cat", re.I)


def get(url, raw=True):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data.decode("utf-8", "replace") if raw else json.loads(data)


def try_get(url, raw=True):
    try:
        return get(url, raw)
    except Exception as e:  # noqa: BLE001
        return f"__ERR__ {e}"


REPOS = [
    ("nomi-san/parsec-vdd", "main"),
    ("itsmikethetech/Virtual-Display-Driver", "master"),
    ("LizardByte/Sunshine", "master"),
]

for repo, branch in REPOS:
    print("=" * 70)
    print("REPO", repo, flush=True)
    readme = None
    for name in ("README.md", "readme.md", "Readme.md"):
        t = try_get(f"https://raw.githubusercontent.com/{repo}/{branch}/{name}")
        if not t.startswith("__ERR__"):
            readme = t
            break
    if readme:
        lines = [l.strip() for l in readme.splitlines() if l.strip()]
        keep = [l for l in lines if KEY.search(l)]
        print(f"  readme {len(readme)} chars, {len(lines)} lines; keyword lines:")
        for l in keep[:45]:
            print("   |", l[:220])
    else:
        print("  readme: NOT FOUND")
    rel = try_get(f"https://api.github.com/repos/{repo}/releases?per_page=5", raw=False)
    if isinstance(rel, str):
        print("  releases:", rel)
    else:
        for r in rel:
            assets = ", ".join(f"{a['name']}({a['size']//1024}KB)" for a in r.get("assets", []))
            print(f"  RELEASE {r.get('tag_name')} {r.get('published_at')} name={r.get('name')!r}")
            print(f"     assets: {assets[:500]}")
    print(flush=True)
