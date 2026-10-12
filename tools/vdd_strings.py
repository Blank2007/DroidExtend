import os as _os
REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
#!/usr/bin/env python3
"""Extract interesting UTF-16 strings (paths, config names) from the VDD driver DLL."""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else \
    os.path.join(REPO, "tools", "vdd", "vdd25", "VirtualDisplayDriver", "MttVDD.dll")
data = open(path, "rb").read()
text = data.decode("utf-16-le", "ignore")

seen = []
found = set()
for pat in (r"[A-Za-z]:\\[^\x00\"<>|]{0,90}", r"[\w.\-]{1,40}\.(?:xml|txt|bin|log|edid)"):
    for m in re.findall(pat, text):
        if m not in found:
            found.add(m)
            seen.append(m)
for s in seen:
    print(repr(s))
