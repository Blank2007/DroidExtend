# Notes for whoever works on this repository next

This file is for humans and AI coding agents alike. It records the rules and the
hard-won facts that are not obvious from the source.

## Documentation language

**Every new document, change log, release note, README or comment-style
explanation must exist in English as well.** This is a standing instruction from
the project owner and it applies to anything written from now on.

* Code comments are the exception. They stay as they are — do not spend effort
  translating comments inside `.py` / `.java` sources.
* The app's own UI is Chinese-only. Where an English document quotes a UI label
  or message, keep the Chinese and add the English meaning in parentheses:
  `` `停止` (Stop) ``, `` `开始投屏` (Start casting) ``.
* Chinese-only companion files (`开发日志.md`, `第三方组件许可.txt`,
  `安装虚拟显示器驱动.bat`, …) keep their Chinese filenames; add `(Chinese)`
  after the name when an English document links to them.

Where the English version lives:

| Chinese | English |
| --- | --- |
| `README.md` | `README.en.md` |
| `开发日志.md` | `CHANGELOG.en.md` |

The remaining docs under `.github/`, plus `NOTICE`, `.gitignore`,
`第三方组件许可.txt` and the comments in `tools/installer/DroidExtend.iss`, carry
both languages inline: the Chinese is kept and the English follows it directly.

## Layout

```
host/       the Windows side — Python + Tkinter, no third-party packages at runtime
android/    the phone side — plain Java, no Gradle; android/build.py drives aapt2/javac/d8
tools/      dev scripts, plus the bundled ffmpeg / adb / Android SDK / display driver
assets/     the intro artwork (SVG source + PNG exports, one per language)
dist/       build output (not tracked)
```

## Building and releasing

```powershell
# phone side only (needs the bundled SDK)
python android\build.py

# everything, then commit + tag + push + create the GitHub release + upload assets
$env:GIT_EXE = '<path to git.exe>'      # git is not on PATH on the author's machine
python tools\release.py 1.5.2 --notes dist\notes-1.5.2.md --build
```

`tools/release.py` reads the GitHub token from `$GITHUB_TOKEN`, then
`%USERPROFILE%\.config\droidextend\github.token`. It never writes the token to
disk or into `.git/config`.

Two network traps on the author's machine, both already handled in the script but
worth knowing:

* `git push` needs `http.proxy` **and** `http.sslbackend=openssl` (schannel fails
  the handshake here).
* `urllib.request.getproxies()` reads the **registry** proxy, so uploading a
  35 MB installer through the default opener dies with `[Errno 10054]`. The
  script uses an explicit no-proxy opener. Downloads work fine either way.

## Two things that will waste your afternoon

**1. Do not run an executable or an installer from inside the workspace.** The
AI session's sandbox refuses display-configuration changes for processes whose
image path is under the workspace: `ChangeDisplaySettingsEx` returns `-1` and
`SetDisplayConfig` returns `5`, while the identical binary copied to `D:\` or
`C:\Windows\Temp` returns `0`. `Start-Process -Wait` on an installer launched from
the workspace hangs forever and never returns. Copy the file somewhere outside
first. This is a sandbox artefact, not a bug in the code.

**2. The virtual display driver has a hard ceiling on how many target modes you
can declare.** `C:\VirtualDisplayDriver\vdd_settings.xml` is
`<resolution>` rows × `<g_refresh_rate>` values. Past roughly 100 combinations
the driver's fixed-size input buffer overflows: adapter start-up "succeeds", the
monitor object is created, and then `IddCxMonitorArrival` returns
`-1073741637` (`0xC00000BB`, `STATUS_NOT_SUPPORTED`) with

```
[ERROR] Failed to report monitor arrival. Status: -1073741637
```

in `C:\VirtualDisplayDriver\Logs\`. The display then never appears on the desktop
and every `attach` fails. Upstream: VirtualDrivers/Virtual-Display-Driver issue
#254 (108 combinations fail, 96 work). This project's ceiling is
`MAX_TARGET_MODES = 90` in `host/vdd.py`, and `tools/vdd_install.ps1` resets an
over-large file back to the shipped one. Ship the same table you grow.

## Facts worth not re-deriving

* The USB link, not the software, is the usual bandwidth ceiling. A charge-only
  cable measures ~13 Mbps; a normal data cable ~150 Mbps; USB 3 ~600 Mbps.
* Reading the capture process with `p.stdout.read(n)` adds ~100 ms of latency,
  because Python's buffered reader waits for a full 64 KB. Use
  `os.read(p.stdout.fileno(), n)`.
* `ddagrab` identifies displays by DXGI **output index**, and resolution is not an
  identity — a laptop panel and the virtual display are frequently both
  `1920x1080`. Screen detection therefore matches by size, then compares a
  thumbnail pixel by pixel, and falls back to `gdigrab` rather than guessing an
  index.
* Stopping must remove the virtual display from the desktop via
  `SetDisplayConfig(SDC_USE_SUPPLIED_DISPLAY_CONFIG)`, not merely reset its
  resolution.
