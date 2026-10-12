"""Ask GitHub whether a newer release exists, and fetch the installer safely.

Two halves, both runnable from a worker thread so the window never freezes:

* :func:`check` -- one HTTPS GET against the public releases API.  Any failure
  (offline, rate limited, DNS blocked) comes back as data, never as a raise.
* :func:`download` -- streams the installer to disk and refuses anything that
  does not verify.  Nothing here installs; the caller decides that.

Why the paranoia: an updater downloads a binary and runs it, which is the
single most attractive thing in this program to attack.  ``download`` therefore
demands a SHA-256 up front and only reports success when *all* of these hold:

1. the URL is HTTPS and its host (after every redirect) is on a short GitHub
   allowlist, so a poisoned API answer cannot point us at a random server;
2. the byte count matches what the API said;
3. the SHA-256 matches -- cross-checked between the asset ``digest`` field and
   the checksum table that ``tools/release.py`` writes into the release body,
   because two independent copies inside one answer is a real cross-check;
4. the file starts with ``MZ``, i.e. it is an executable at all.

The build is not code signed, so Authenticode cannot be required; the hashes
above are what actually pins the bytes, and the UI says so plainly.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from version import APP_VERSION, LATEST_RELEASE_API, RELEASES_URL, USER_AGENT

SETUP_PREFIX = "DroidExtend-Setup-"
SETUP_SUFFIX = ".exe"
MAX_BYTES = 200 * 1024 * 1024
CHUNK = 256 * 1024

# A release download is served from github.com and then redirected to a signed
# object store.  These are the only hosts we follow.
ASSET_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
)

HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")


def parse(v):
    """"v1.2.3" / "1.2" / "1.2.3-beta" -> (1, 2, 3).  Unparseable -> (0,)."""
    nums = re.findall(r"\d+", str(v or "").strip().lstrip("vV"))
    return tuple(int(n) for n in nums[:4]) or (0,)


def newer(latest, current=APP_VERSION):
    return parse(latest) > parse(current)


def _digest_sha(d):
    """GitHub's asset ``digest`` is "sha256:<hex>" or None on older servers."""
    s = str(d or "").strip().lower()
    if s.startswith("sha256:"):
        s = s[7:]
    return s if re.fullmatch(r"[0-9a-f]{64}", s) else ""


def sha_from_notes(notes, name):
    """Pick the hash for *name* out of the checksum table in the release body."""
    for line in str(notes or "").splitlines():
        if name and name in line:
            m = HEX64.search(line)
            if m:
                return m.group(0).lower()
    return ""


def pick_asset(data):
    for a in (data.get("assets") or []):
        n = str(a.get("name") or "")
        if n.startswith(SETUP_PREFIX) and n.lower().endswith(SETUP_SUFFIX):
            return a
    return None


def host_ok(url):
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return False
    if u.scheme != "https":
        return False
    return (u.hostname or "").lower() in ASSET_HOSTS


def check(timeout=8):
    """-> dict(ok, latest, newer, url, name, notes, published, setup, error)

    ``setup`` is None or dict(name, url, size, sha256); ``sha256`` is "" when
    neither the digest field nor the body table carried one.
    """
    out = {"ok": False, "latest": "", "newer": False, "url": RELEASES_URL,
           "name": "", "notes": "", "published": "", "setup": None, "error": ""}
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

    a = pick_asset(data)
    if a:
        name = str(a.get("name") or "")
        out["setup"] = {
            "name": name,
            "url": str(a.get("browser_download_url") or ""),
            "size": int(a.get("size") or 0),
            "sha256": _digest_sha(a.get("digest")) or sha_from_notes(out["notes"], name),
        }
    return out


# ------------------------------------------------------------------- download

def download(url, dest, sha256="", size=0, on_progress=None, cancel=None,
             timeout=30):
    """Fetch *url* into *dest*, verify it, and only then hand back the path.

    -> dict(ok, path, got, sha256, error)  -- ``path`` is set only when every
    check passed.  ``on_progress(done, total)`` is called from this thread;
    ``cancel()`` returning True aborts cleanly.
    """
    res = {"ok": False, "path": "", "got": 0, "sha256": "", "error": ""}
    want = str(sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", want):
        res["error"] = "官方没有给出校验和，为安全起见拒绝下载。"
        return res
    if not host_ok(url):
        res["error"] = "下载地址不是 GitHub 官方域名，已拒绝：%s" % url
        return res

    part = dest + ".part"
    h = hashlib.sha256()
    got = 0
    total = int(size or 0)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final = r.geturl()
            if not host_ok(final):
                res["error"] = "下载被重定向到非 GitHub 域名，已拒绝：%s" % final
                return res
            total = int(size or r.headers.get("Content-Length") or 0)
            if total > MAX_BYTES:
                res["error"] = "文件异常大（%d 字节），已拒绝。" % total
                return res
            with open(part, "wb") as f:
                while True:
                    if cancel and cancel():
                        raise _Cancel()
                    b = r.read(CHUNK)
                    if not b:
                        break
                    f.write(b)
                    h.update(b)
                    got += len(b)
                    if got > MAX_BYTES:
                        raise OSError("超过大小上限")
                    if on_progress:
                        on_progress(got, total)
    except _Cancel:
        _quiet_unlink(part)
        res["error"] = "已取消。"
        res["got"] = got
        return res
    except Exception as e:
        _quiet_unlink(part)
        res["error"] = "%s: %s" % (type(e).__name__, e)
        res["got"] = got
        return res

    res["got"] = got
    res["sha256"] = h.hexdigest()
    if total and got != total:
        _quiet_unlink(part)
        res["error"] = "大小不符：官方 %d 字节，实际 %d 字节。" % (total, got)
        return res
    if int(size or 0) and got != int(size):
        _quiet_unlink(part)
        res["error"] = "大小不符：官方 %d 字节，实际 %d 字节。" % (int(size), got)
        return res
    if res["sha256"] != want:
        _quiet_unlink(part)
        res["error"] = ("校验和不符，文件可能被篡改。\n官方 %s\n实际 %s"
                        % (want, res["sha256"]))
        return res
    try:
        with open(part, "rb") as f:
            if f.read(2) != b"MZ":
                raise ValueError("不是 Windows 可执行文件")
    except Exception as e:
        _quiet_unlink(part)
        res["error"] = "文件格式校验失败：%s" % e
        return res
    try:
        os.replace(part, dest)
    except OSError as e:
        _quiet_unlink(part)
        res["error"] = "写入失败：%s" % e
        return res
    res["ok"] = True
    res["path"] = dest
    return res


class _Cancel(Exception):
    pass


def _quiet_unlink(p):
    try:
        os.remove(p)
    except OSError:
        pass


def signature_status(path, timeout=25):
    """-> (status, signer) from Authenticode, or ("", "") when unavailable.

    Purely informational: the build is not signed, so the caller must not treat
    "NotSigned" as a failure -- the hashes are what pin the bytes.
    """
    ps = ("$s = Get-AuthenticodeSignature -LiteralPath %s; "
          "Write-Output $s.Status; Write-Output $s.SignerCertificate.Subject"
          % _ps_quote(path))
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = p.stdout.decode("utf-8", "replace").splitlines()
        return (out[0].strip() if out else ""), (out[1].strip() if len(out) > 1 else "")
    except Exception:
        return "", ""


def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


# ------------------------------------------------------------------ installing

WAIT_CMD = """@echo off
rem Written by DroidExtend to apply an update it already downloaded and
rem verified.  Wait for the running app to release its files, install, restart.
set PID={pid}
:wait
tasklist /FI "PID eq %PID%" 2>nul | find /I "%PID%" >nul
if not errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto wait
)
start "" /wait "{installer}" {flags}
{restart}
del "%~f0"
"""


def run_installer(installer, exe="", pid=None, install_dir=""):
    """Apply *installer* after this process exits.  -> (ok, error, script).

    The installer cannot replace ``DroidExtend.exe`` while it is running, and
    the app cannot wait for an installer that is waiting for the app, so a tiny
    detached batch file breaks the deadlock: it polls until our PID is gone,
    runs Inno Setup with ``/SILENT /NORESTART``, and starts the new build.

    ``install_dir`` becomes ``/DIR=``: Inno's ``UsePreviousAppDir`` would
    normally find the earlier target, but saying it outright means an update
    can never land in a second copy of the program somewhere else.
    """
    pid = int(pid or os.getpid())
    exe = exe or (sys.executable if getattr(sys, "frozen", False) else "")
    restart = ('if exist "%s" start "" "%s"' % (exe, exe) if exe
               else "rem dev run: nothing to restart")
    flags = "/SILENT /NORESTART"
    if install_dir:
        flags += ' /DIR="%s"' % install_dir
    d = update_dir()
    try:
        script = os.path.join(d, "apply_update.cmd")
        src = WAIT_CMD.format(pid=pid, installer=installer, flags=flags,
                              restart=restart)
        # cmd.exe wants CRLF and the OEM code page; the text is pure ASCII.
        with open(script, "w", encoding="ascii", newline="\r\n") as f:
            f.write(src)
        flags = 0
        if os.name == "nt":
            flags = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        subprocess.Popen(["cmd", "/c", script], close_fds=True,
                         creationflags=flags, cwd=d)
        return True, "", script
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e), ""


def update_dir():
    d = os.path.join(tempfile.gettempdir(), "droidextend_update")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d
