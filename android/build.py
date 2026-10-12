"""Build + sign DroidExtend.apk without Gradle.

Everything here comes from the archives already in tools/, plus any JDK 17+ found
on the machine, so the build is offline once tools/sdk is populated.

    python android/build.py            # full build
    python android/build.py --clean    # wipe build/ first

Needs a JDK (javac + keytool).  Resolution order is JAVA_HOME, then javac on
PATH, then a few well-known install locations.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "tools")
HOME = os.path.expanduser("~")

# Last-resort guesses only.  JAVA_HOME and PATH come first, so a normal developer
# machine never reaches these; they exist so a fresh clone on Windows without a
# configured JDK still has a chance.
JDK_CANDIDATES = [
    os.environ.get("JAVA_HOME") or "",
    os.path.join(HOME, "AppData", "Roaming", ".minecraft", "runtime", "java-runtime-delta"),
    os.path.join(HOME, "AppData", "Roaming", ".minecraft", "runtime", "java-runtime-alpha"),
    r"C:\Program Files\Eclipse Adoptium\jdk-17",
    r"C:\Program Files\Eclipse Adoptium\jdk-21",
    r"C:\Program Files\Java\jdk-17",
    r"C:\Program Files\Java\jdk-21",
    r"C:\Program Files\Microsoft\jdk-17.0.13.11-hotspot",
    r"C:\Program Files\Android\Android Studio\jbr",
]

BUILD = os.path.join(HERE, "build")
DIST = os.path.join(ROOT, "dist")
PKG = "com.droidextend"
APP_NAME = "DroidExtend"
# The keystore lives OUTSIDE build/ on purpose: `--clean` must not destroy it,
# because a new key makes every future update refuse to install over the old
# one (INSTALL_FAILED_UPDATE_INCOMPATIBLE).  It is a throwaway debug key with
# the password printed right here, so keeping it in the tree is fine.
KEYSTORE = os.path.join(HERE, "droidextend.keystore")
KS_PASS = "droidextend"
KS_ALIAS = "droidextend"


def log(msg):
    print(msg, flush=True)


def find_jdk():
    for c in JDK_CANDIDATES:
        if c and os.path.exists(os.path.join(c, "bin", "javac.exe")):
            return c
    # javac on PATH: derive the JDK home from it (…\bin\javac.exe -> …).
    javac = shutil.which("javac")
    if javac:
        home = os.path.dirname(os.path.dirname(os.path.abspath(javac)))
        if os.path.exists(os.path.join(home, "bin", "javac.exe")):
            return home
    raise SystemExit("no JDK found; set JAVA_HOME to a JDK 17+ install")


def java_tool(jdk, name):
    for ext in (".exe", ".bat", ""):
        p = os.path.join(jdk, "bin", name + ext)
        if os.path.exists(p):
            return p
    raise SystemExit("%s not found in %s" % (name, jdk))


def run(cmd, **kw):
    log("  $ " + " ".join('"%s"' % c if " " in c else c for c in cmd[:6]) +
        (" ..." if len(cmd) > 6 else ""))
    env = dict(os.environ)
    env["JAVA_HOME"] = kw.pop("java_home", env.get("JAVA_HOME", ""))
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", env=env, **kw)
    if p.returncode != 0:
        log(p.stdout[-6000:])
        log(p.stderr[-6000:])
        raise SystemExit("FAILED (%d): %s" % (p.returncode, cmd[0]))
    if p.stderr.strip():
        for line in p.stderr.strip().splitlines():
            if not re.search(r"warning|Note:|^$", line, re.I):
                log("    " + line)
    return p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--debug", action="store_true", help="skip signing (unsigned apk)")
    a = ap.parse_args()

    jdk = find_jdk()
    sdk = os.path.join(TOOLS, "sdk")
    bt = os.path.join(sdk, "build-tools", "34.0.0")
    android_jar = os.path.join(sdk, "platforms", "android-34", "android.jar")
    for p in (bt, android_jar):
        if not os.path.exists(p):
            raise SystemExit("missing %s - run tools/provision2.py first" % p)

    javac = java_tool(jdk, "javac")
    java = java_tool(jdk, "java")
    keytool = java_tool(jdk, "keytool")
    aapt2 = os.path.join(bt, "aapt2.exe")
    d8 = os.path.join(bt, "d8.bat")
    zipalign = os.path.join(bt, "zipalign.exe")
    apksigner = os.path.join(bt, "apksigner.bat")

    if a.clean and os.path.isdir(BUILD):
        shutil.rmtree(BUILD, ignore_errors=True)
    os.makedirs(BUILD, exist_ok=True)
    os.makedirs(DIST, exist_ok=True)
    env = dict(os.environ, JAVA_HOME=jdk)
    t_all = time.time()

    srcs = []
    for base, _, files in os.walk(os.path.join(HERE, "src")):
        for f in files:
            if f.endswith(".java"):
                srcs.append(os.path.join(base, f))
    srcs.sort()
    log("source files: %d" % len(srcs))

    # 1. resources + manifest -> base apk (no dex yet)
    log("\n[1/7] aapt2 compile")
    res_dir = os.path.join(HERE, "res")
    res_zip = os.path.join(BUILD, "res.zip")
    res_args = []
    if os.path.isdir(res_dir):
        if os.path.exists(res_zip):
            os.remove(res_zip)
        run([aapt2, "compile", "--dir", res_dir, "-o", res_zip], java_home=jdk)
        res_args = [res_zip]
        log("    compiled %d resource files -> %.1f KB"
            % (sum(len(f) for _, _, f in os.walk(res_dir)), os.path.getsize(res_zip) / 1024))
    else:
        log("    no res/ directory, building without resources")

    log("\n[2/7] aapt2 link")
    base_apk = os.path.join(BUILD, "base.apk")
    # aapt2 writes R.java here, and our sources reference R.color.* -- so it has
    # to be compiled along with them.  No resources means no R.java and nothing
    # to add; the app still has to compile without one.
    gendir = os.path.join(BUILD, "gen")
    shutil.rmtree(gendir, ignore_errors=True)
    os.makedirs(gendir, exist_ok=True)
    # versionCode / versionName come from AndroidManifest.xml -- passing them
    # here too would be a second source of truth that silently drifts.
    run([aapt2, "link", "-o", base_apk,
         "-I", android_jar,
         "--manifest", os.path.join(HERE, "AndroidManifest.xml"),
         "--min-sdk-version", "21", "--target-sdk-version", "34",
         "--java", gendir,
         "--no-version-vectors"] + res_args, java_home=jdk)
    for base, _, files in os.walk(gendir):
        for f in files:
            if f.endswith(".java"):
                srcs.append(os.path.join(base, f))
    log("    generated %d R.java" % len([s for s in srcs if s.endswith("R.java")]))

    # 2. javac
    log("\n[3/7] javac")
    classes = os.path.join(BUILD, "classes")
    os.makedirs(classes, exist_ok=True)
    # NOTE: do NOT pass -g:none here.  R8/D8 dereferences the SourceFile
    # attribute and dies with an NPE ("Cannot invoke String.length() because
    # <parameter1> is null") on class files that lack it.
    run([javac, "-encoding", "UTF-8", "-nowarn",
         "-source", "8", "-target", "8",
         "-bootclasspath", android_jar,
         "-d", classes] + srcs, java_home=jdk)

    # 3. d8 -> classes.dex
    #
    # NOT build-tools/34.0.0/d8.bat: that ships R8 8.2.2-dev, which dies with
    # "java.lang.NullPointerException: Cannot invoke String.length() because
    # <parameter1> is null" on MainActivity$1.class no matter which javac
    # -source/-target/--release combination produced it.  Standalone R8 9.5.23
    # from maven.google.com dexes the identical class files without complaint.
    log("\n[4/7] d8 (R8 9.5.23)")
    classfiles = []
    for base, _, files in os.walk(classes):
        for f in files:
            if f.endswith(".class"):
                classfiles.append(os.path.join(base, f))
    dexdir = os.path.join(BUILD, "dex")
    shutil.rmtree(dexdir, ignore_errors=True)
    os.makedirs(dexdir, exist_ok=True)
    r8_jar = os.path.join(TOOLS, "sdk", "build-tools", "r8-9.5.23.jar")
    if os.path.exists(r8_jar):
        run([java, "-cp", r8_jar, "com.android.tools.r8.D8", "--release",
             "--min-api", "21", "--lib", android_jar, "--output", dexdir] + classfiles,
            java_home=jdk)
    else:
        run([d8, "--release", "--min-api", "21", "--lib", android_jar,
             "--output", dexdir] + classfiles, java_home=jdk)

    # 4. fold the dex into the apk
    log("\n[5/7] packaging classes.dex")
    unsigned = os.path.join(BUILD, "unsigned.apk")
    if os.path.exists(unsigned):
        os.remove(unsigned)
    dexes = sorted(f for f in os.listdir(dexdir) if f.endswith(".dex"))
    with zipfile.ZipFile(base_apk) as zin, zipfile.ZipFile(unsigned, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename.startswith("classes") and item.filename.endswith(".dex"):
                continue
            zout.writestr(item, zin.read(item.filename))
        for d in dexes:
            zout.write(os.path.join(dexdir, d), d)
    log("    dex files: %s  ->  %.1f KB" % (",".join(dexes), os.path.getsize(unsigned) / 1024))

    # 5. zipalign
    log("\n[6/7] zipalign")
    aligned = os.path.join(BUILD, "aligned.apk")
    if os.path.exists(aligned):
        os.remove(aligned)
    run([zipalign, "-f", "-p", "4", unsigned, aligned], java_home=jdk)

    if a.debug:
        out = os.path.join(DIST, APP_NAME + "-unsigned.apk")
        shutil.copyfile(aligned, out)
        log("\nunsigned -> %s" % out)
        return

    # 6. sign
    log("\n[7/7] sign")
    if not os.path.exists(KEYSTORE):
        log("    generating keystore")
        run([keytool, "-genkeypair", "-keystore", KEYSTORE,
             "-storepass", KS_PASS, "-keypass", KS_PASS, "-alias", KS_ALIAS,
             "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
             "-dname", "CN=DroidExtend,OU=dev,O=droidextend,L=NA,ST=NA,C=CN"],
            java_home=jdk)
    out = os.path.join(DIST, APP_NAME + ".apk")
    if os.path.exists(out):
        os.remove(out)
    run([apksigner, "sign", "--ks", KEYSTORE,
         "--ks-pass", "pass:" + KS_PASS, "--key-pass", "pass:" + KS_PASS,
         "--ks-key-alias", KS_ALIAS,
         "--v1-signing-enabled", "true", "--v2-signing-enabled", "true",
         "--min-sdk-version", "21", "--out", out, aligned], java_home=jdk)
    log("\n    verify:")
    log(run([apksigner, "verify", "--print-certs", "-v", out], java_home=jdk))
    log("\nAPK -> %s  (%.2f MB, %.1fs total)"
        % (out, os.path.getsize(out) / 1e6, time.time() - t_all))


if __name__ == "__main__":
    main()
