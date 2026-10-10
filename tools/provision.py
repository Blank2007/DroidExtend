#!/usr/bin/env python3
"""Provision the Windows-side + Android-build toolchain into tools/.

Downloads (all via https, Python's own TLS stack):
  platform-tools  -> adb + fastboot
  ffmpeg          -> capture + hardware H.264 encode
  jdk 17          -> javac/d8 host for building the APK
  commandlinetools-> sdkmanager for android platform + build-tools

Idempotent: skips anything already present.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch  # noqa: E402

TOOLS = HERE

PLATFORM_TOOLS = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
FFMPEG = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
JDK = "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse"
CMDLINE = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"


def have(path):
    return os.path.exists(path)


def step(name, fn):
    print(f"\n=== {name} ===", flush=True)
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {name}: {e}", flush=True)
        return False
    return True


def dl_zip(url, dest, marker):
    if have(marker):
        print(f"[skip] {dest} already present", flush=True)
        return
    tmp = os.path.join(os.environ.get("TEMP", "."), os.path.basename(dest) + ".zip")
    fetch.download(url, tmp)
    fetch.unzip(tmp, dest, strip_top=True)
    try:
        os.remove(tmp)
    except OSError:
        pass


def find_sdkmanager():
    for root, dirs, files in os.walk(os.path.join(TOOLS, "sdk", "cmdline-tools")):
        if "sdkmanager.bat" in files:
            return os.path.join(root, "sdkmanager.bat")
    return None


def install_sdk_packages():
    sdk_root = os.path.join(TOOLS, "sdk")
    sm = find_sdkmanager()
    if not sm:
        raise RuntimeError("sdkmanager.bat not found")
    java_home = os.path.join(TOOLS, "jdk")
    env = dict(os.environ)
    env["JAVA_HOME"] = java_home
    env["PATH"] = os.path.join(java_home, "bin") + os.pathsep + env.get("PATH", "")
    pkg_dir = os.path.join(sdk_root, "platforms")
    if os.path.isdir(pkg_dir) and os.listdir(pkg_dir):
        print("[skip] android platform already installed", flush=True)
        return
    yes = subprocess.run([sm, "--sdk_root=" + sdk_root, "--licenses"],
                         input=b"y\n" * 40, env=env, capture_output=True)
    print(yes.stdout.decode("utf-8", "replace")[-2000:], flush=True)
    r = subprocess.run([sm, "--sdk_root=" + sdk_root, "platform-tools",
                        "platforms;android-34", "build-tools;34.0.0"],
                       input=b"y\n" * 40, env=env, capture_output=True)
    out = r.stdout.decode("utf-8", "replace")
    print(out[-4000:], flush=True)
    if r.returncode != 0:
        print(r.stderr.decode("utf-8", "replace")[-2000:], flush=True)
        raise RuntimeError("sdkmanager failed rc=%d" % r.returncode)


def main():
    ok = True
    ok &= step("platform-tools (adb)", lambda: dl_zip(
        PLATFORM_TOOLS, os.path.join(TOOLS, "platform-tools"),
        os.path.join(TOOLS, "platform-tools", "adb.exe")))
    ok &= step("ffmpeg", lambda: dl_zip(
        FFMPEG, os.path.join(TOOLS, "ffmpeg"),
        os.path.join(TOOLS, "ffmpeg", "bin", "ffmpeg.exe")))
    ok &= step("jdk 17", lambda: dl_zip(
        JDK, os.path.join(TOOLS, "jdk"), os.path.join(TOOLS, "jdk", "bin", "javac.exe")))
    ok &= step("commandlinetools", lambda: dl_zip(
        CMDLINE, os.path.join(TOOLS, "sdk", "cmdline-tools", "latest"),
        os.path.join(TOOLS, "sdk", "cmdline-tools", "latest", "bin", "sdkmanager.bat")))
    ok &= step("android sdk packages", install_sdk_packages)
    print("\nPROVISION " + ("OK" if ok else "INCOMPLETE"), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
