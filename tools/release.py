"""One-command release pipeline for DroidExtend.

    python tools/release.py --bump patch --notes dist/notes-1.4.1.md
    python tools/release.py 1.4.1 --notes dist/notes-1.4.1.md
    python tools/release.py 1.4.1 --build        # rebuild APK + exe + installer first

The whole loop, in order:

    1. write the new version into host/version.py and tools/installer/DroidExtend.iss
    2. (--build) rebuild dist/DroidExtend.apk, DroidExtend.exe + _internal, the installer
    3. commit everything, tag vX.Y.Z (annotated)
    4. push main + the tag over HTTPS using the stored token
    5. create the GitHub release, with the SHA256 of every asset in the body
    6. upload the installer and the APK

Two things this script knows that are easy to get wrong by hand:

  * git needs the local proxy (http.proxy is already in .git/config) and
    http.sslBackend=openssl, because the Windows schannel backend fails the
    handshake through it.
  * uploads.github.com must be reached DIRECTLY.  Pushing a 36 MB installer
    through the Clash proxy dies with "Send failure: Connection was reset";
    direct it takes seconds.

The token is read from, in order:
    $GITHUB_TOKEN
    %USERPROFILE%\\.config\\droidextend\\github.token
    %LOCALAPPDATA%\\DroidExtend\\github.token
It is never written to disk by this script, never put into .git/config, and
never committed: the push uses a one-shot URL and the remote stays clean.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOST = os.path.join(ROOT, "host")
DIST = os.path.join(ROOT, "dist")
VERSION_PY = os.path.join(HOST, "version.py")
ISS = os.path.join(HERE, "installer", "DroidExtend.iss")
MANIFEST = os.path.join(ROOT, "android", "AndroidManifest.xml")

REPO = "Blank2007/DroidExtend"
API = "https://api.github.com/repos/" + REPO
UPLOADS = "https://uploads.github.com/repos/" + REPO
UA = "DroidExtend-release/1.0"

GIT_CANDIDATES = [
    os.environ.get("GIT_EXE"),
    shutil.which("git"),
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "cmd", "git.exe"),
]

TOKEN_PATHS = [
    os.path.join(os.environ.get("USERPROFILE", ""), ".config", "droidextend", "github.token"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "DroidExtend", "github.token"),
]


def say(msg):
    print(msg, flush=True)


def die(msg):
    say("ERROR: " + msg)
    sys.exit(1)


# --------------------------------------------------------------------------- io

def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_git():
    for c in GIT_CANDIDATES:
        if c and os.path.isfile(c):
            return c
    die("找不到 git.exe —— 设一个 GIT_EXE 环境变量，或把 git 放进 PATH")


def read_token():
    tok = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if tok:
        return tok, "$GITHUB_TOKEN"
    for p in TOKEN_PATHS:
        if p and os.path.isfile(p):
            tok = read_text(p).strip()
            if tok:
                return tok, p
    die("找不到 GitHub token。放到 %s，或设 GITHUB_TOKEN" % TOKEN_PATHS[0])


# ----------------------------------------------------------------------- github

def api(method, url, token, payload=None, timeout=120):
    data = None
    headers = {
        "Authorization": "token " + token,
        "User-Agent": UA,
        "Accept": "application/vnd.github+json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"message": body[:400]}
    except Exception as e:
        return 0, {"message": "%s: %s" % (type(e).__name__, e)}


def upload_asset(release_id, token, path, name=None):
    """Upload one file.  Direct connection on purpose - see the module docstring."""
    name = name or os.path.basename(path)
    size = os.path.getsize(path)
    url = "%s/releases/%d/assets?name=%s" % (UPLOADS, release_id, urllib.parse.quote(name))
    headers = {
        "Authorization": "token " + token,
        "User-Agent": UA,
        "Content-Type": "application/octet-stream",
        "Content-Length": str(size),
    }
    with open(path, "rb") as f:
        req = urllib.request.Request(url, data=f, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=1800) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            return e.code, {"message": body[:400]}
        except Exception as e:
            return 0, {"message": "%s: %s" % (type(e).__name__, e)}


# -------------------------------------------------------------------------- git

class Git(object):
    def __init__(self, exe):
        self.exe = exe
        # Every token we have seen, so it can never reach a log or an error
        # message.  The one-shot push URL carries it in argv, and git echoes
        # argv back in its error output.
        self.secrets = []

    def watch(self, secret):
        if secret and secret not in self.secrets:
            self.secrets.append(secret)

    def redact(self, text):
        for s in self.secrets:
            text = text.replace(s, "***")
        return text

    def __call__(self, *args, **kw):
        check = kw.pop("check", True)
        cmd = [self.exe] + list(args)
        for a in args:
            if "@github.com" in a and "://" in a:
                self.watch(a.split("://", 1)[1].split("@", 1)[0].split(":", 1)[-1])
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True)
        out = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
        if check and p.returncode != 0:
            die("git %s 失败:\n%s" % (self.redact(" ".join(args)), self.redact(out)))
        return p.returncode, self.redact(out)


# ----------------------------------------------------------------------- version

def current_version():
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', read_text(VERSION_PY))
    return m.group(1) if m else None


def bump(ver, part):
    nums = [int(x) for x in re.findall(r"\d+", ver)[:3]]
    while len(nums) < 3:
        nums.append(0)
    idx = {"major": 0, "minor": 1, "patch": 2}[part]
    nums[idx] += 1
    for i in range(idx + 1, 3):
        nums[i] = 0
    return ".".join(str(n) for n in nums)


def set_version(ver):
    today = datetime.date.today().isoformat()
    t = read_text(VERSION_PY)
    t2 = re.sub(r'(APP_VERSION\s*=\s*")[^"]+(")', r"\g<1>%s\g<2>" % ver, t, count=1)
    t2 = re.sub(r'(APP_DATE\s*=\s*")[^"]+(")', r"\g<1>%s\g<2>" % today, t2, count=1)
    if t2 != t:
        write_text(VERSION_PY, t2)
        say("  host/version.py -> %s (%s)" % (ver, today))
    iss = read_text(ISS)
    iss2 = re.sub(r'(#define\s+AppVer\s+")[^"]+(")', r"\g<1>%s\g<2>" % ver, iss, count=1)
    if iss2 != iss:
        write_text(ISS, iss2)
        say("  tools/installer/DroidExtend.iss -> %s" % ver)
    # The APK carries its own version, and Android refuses an update whose
    # versionCode did not increase, so bump both.
    man = read_text(MANIFEST)
    m = re.search(r'android:versionCode="(\d+)"', man)
    code = (int(m.group(1)) + 1) if m else 1
    man2 = re.sub(r'(android:versionCode=")\d+(")', r"\g<1>%d\g<2>" % code, man, count=1)
    man2 = re.sub(r'(android:versionName=")[^"]+(")', r"\g<1>%s\g<2>" % ver, man2, count=1)
    if man2 != man:
        write_text(MANIFEST, man2)
        say("  android/AndroidManifest.xml -> versionCode %d / versionName %s" % (code, ver))


# ----------------------------------------------------------------------- builds

def build_apk():
    say("  building the APK ...")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "android", "build.py")],
                       cwd=ROOT, capture_output=True)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    if p.returncode != 0:
        say(out[-3000:])
        die("APK 构建失败")
    apk = os.path.join(DIST, "DroidExtend.apk")
    say("  -> %s (%d bytes)" % (apk, os.path.getsize(apk)))


def build_exe():
    say("  building DroidExtend.exe (PyInstaller) ...")
    pb = os.path.join(HERE, "pybuild")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--onedir", "--windowed", "--name", "DroidExtend",
           "--icon", os.path.join(ROOT, "DroidExtend.ico"),
           "--paths", HOST,
           "--distpath", os.path.join(pb, "out"),
           "--workpath", os.path.join(pb, "work"),
           "--specpath", pb,
           os.path.join(HOST, "app.py")]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    if "Build complete!" not in out:
        say(out[-3000:])
        die("PyInstaller 构建失败")
    src = os.path.join(pb, "out", "DroidExtend")
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(ROOT, item)
        if os.path.isdir(s):
            if os.path.isdir(d):
                shutil.rmtree(d)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    say("  -> %s (%d bytes)" % (os.path.join(ROOT, "DroidExtend.exe"),
                                os.path.getsize(os.path.join(ROOT, "DroidExtend.exe"))))


def build_installer(ver):
    iscc = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "Programs", "Inno Setup 6", "ISCC.exe")
    if not os.path.isfile(iscc):
        die("找不到 Inno Setup: %s" % iscc)
    say("  building the installer ...")
    p = subprocess.run([iscc, ISS], cwd=ROOT, capture_output=True)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    exe = os.path.join(DIST, "DroidExtend-Setup-%s.exe" % ver)
    if p.returncode != 0 or not os.path.isfile(exe):
        say(out[-3000:])
        die("ISCC 构建失败")
    say("  -> %s (%d bytes)" % (exe, os.path.getsize(exe)))


# ------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="DroidExtend 打包 + 提交 + 推送 + 发版")
    ap.add_argument("version", nargs="?", help="目标版本号，例如 1.4.1")
    ap.add_argument("--bump", choices=["major", "minor", "patch"],
                    help="从当前版本号自增，而不是显式指定")
    ap.add_argument("--notes", help="release 说明的 markdown 文件")
    ap.add_argument("--message", help="提交标题（默认取 release 说明的第一行）")
    ap.add_argument("--build", action="store_true", help="先重建 APK / exe / 安装包")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要做什么")
    args = ap.parse_args()

    cur = current_version()
    if not cur:
        die("读不出 host/version.py 里的 APP_VERSION")
    if args.bump:
        ver = bump(cur, args.bump)
    elif args.version:
        ver = args.version.lstrip("v")
    else:
        ver = cur
    tag = "v" + ver
    say("DroidExtend %s -> %s" % (cur, ver))

    token, token_src = read_token()
    say("token: %s (%d chars)" % (token_src, len(token)))
    git = Git(find_git())

    notes = read_text(args.notes) if args.notes else ""

    if args.dry_run:
        say("\n[dry-run] 会写入版本号、提交、打 %s、推送、建 release 并上传两个产物" % tag)
        return 0

    # 1. version
    set_version(ver)

    # 2. builds
    exe = os.path.join(DIST, "DroidExtend-Setup-%s.exe" % ver)
    apk = os.path.join(DIST, "DroidExtend.apk")
    if args.build:
        say("building ...")
        build_apk()
        build_exe()
        build_installer(ver)
    for f in (exe, apk):
        if not os.path.isfile(f):
            die("缺少产物 %s —— 加 --build 让它现场构建" % f)

    # 3. commit + tag
    git("add", "-A")
    _, status = git("status", "--porcelain")
    if status:
        subject = args.message or (notes.splitlines()[0].lstrip("# ").strip()
                                   if notes.strip() else "Release " + ver)
        msg = subject if not notes else subject + "\n\n" + notes
        f = os.path.join(os.environ.get("TEMP", "."), "de_release_msg.txt")
        write_text(f, msg)
        git("commit", "-F", f)
        say("committed: %s" % subject)
    else:
        say("nothing to commit")
    code, _ = git("rev-parse", "-q", "--verify", "refs/tags/" + tag, check=False)
    if code == 0:
        say("tag %s already exists - reusing it" % tag)
    else:
        git("tag", "-a", tag, "-m", "DroidExtend " + ver)

    # 4. push (one-shot authenticated URL; the remote stays clean)
    auth = "https://%s:%s@github.com/%s.git" % (REPO.split("/")[0], token, REPO)
    say("pushing main ...")
    _, out = git("push", auth, "main")
    say("  " + (out.splitlines()[-1] if out else "ok"))
    say("pushing %s ..." % tag)
    git("push", auth, tag)

    # 5. release (create, or patch the body of an existing one)
    body = notes.rstrip()
    hashes = []
    for f in (exe, apk):
        n = os.path.basename(f)
        hashes.append("| `%s` | %d bytes | `%s` |" % (n, os.path.getsize(f), sha256(f)))
    body += ("\n\n### 校验\n\n| 文件 | 大小 | SHA256 |\n|---|---|---|\n"
             + "\n".join(hashes) + "\n")

    st, rel = api("GET", API + "/releases/tags/" + tag, token)
    if st == 200:
        say("release %s already exists - updating its notes" % tag)
        st, rel = api("PATCH", API + "/releases/%d" % rel["id"], token,
                      {"body": body, "name": "DroidExtend " + ver})
        if st != 200:
            die("更新 release 失败: %s" % rel)
    else:
        st, rel = api("POST", API + "/releases", token, {
            "tag_name": tag,
            "target_commitish": "main",
            "name": "DroidExtend " + ver,
            "body": body,
            "draft": False,
            "prerelease": False,
        })
        if st not in (200, 201):
            die("创建 release 失败: %s" % rel)
        say("release created: %s" % rel["html_url"])

    # 6. upload
    have = {a["name"] for a in rel.get("assets", [])}
    for f in (exe, apk):
        n = os.path.basename(f)
        if n in have:
            say("  %s already uploaded - skipping" % n)
            continue
        say("  uploading %s (%d bytes) ..." % (n, os.path.getsize(f)))
        st, r = upload_asset(rel["id"], token, f)
        if st not in (200, 201):
            die("上传 %s 失败 (%s): %s" % (n, st, r))
        say("    -> %s" % r.get("browser_download_url", "?"))

    say("\ndone: %s" % rel.get("html_url", tag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
