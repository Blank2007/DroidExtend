"""Ask GitHub whether a newer release exists.

Read-only: one HTTPS GET against the public releases API, run from a worker
thread so the window never freezes.  Any failure (no network, rate limited,
DNS blocked) is reported as data, never raised -- being offline is not an error.
"""
import json
import re
import urllib.request

from version import APP_VERSION, LATEST_RELEASE_API, RELEASES_URL, USER_AGENT


def parse(v):
    """"v1.2.3" / "1.2" / "1.2.3-beta" -> (1, 2, 3).  Unparseable -> (0,)."""
    nums = re.findall(r"\d+", str(v or "").strip().lstrip("vV"))
    return tuple(int(n) for n in nums[:4]) or (0,)


def newer(latest, current=APP_VERSION):
    return parse(latest) > parse(current)


def check(timeout=8):
    """-> dict(ok, latest, newer, url, name, notes, published, error)"""
    out = {"ok": False, "latest": "", "newer": False, "url": RELEASES_URL,
           "name": "", "notes": "", "published": "", "error": ""}
    req = urllib.request.Request(LATEST_RELEASE_API, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out
    if not isinstance(data, dict):
        out["error"] = "返回内容不是 JSON 对象"
        return out
    out["ok"] = True
    out["latest"] = str(data.get("tag_name") or data.get("name") or "").lstrip("vV")
    out["url"] = str(data.get("html_url") or RELEASES_URL)
    out["name"] = str(data.get("name") or "")
    out["notes"] = str(data.get("body") or "")
    out["published"] = str(data.get("published_at") or "")[:10]
    out["newer"] = newer(out["latest"])
    return out
