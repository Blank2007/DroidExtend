# DroidExtend development log

> **Let me be clear first: the code in this project was written by AI.**
>
> All of the source code (8444 lines of Python in 21 files on the Windows side, 2985 lines of Java
> in 12 files on the Android side, 10371 lines of Python in 96 tool and probe scripts) was written
> from scratch by an **AI coding agent** inside **DeepSeek Harness**, using the **deepseek-flash**
> model, and delivered as an installable 1.6.2 within a few sessions between **2026-10-10** and
> **2026-10-16**.
>
> What the human ([@Blank2007](https://github.com/Blank2007)) did: state the requirements, plug in
> a real device, click UAC wherever administrator rights were needed, and accept each finished step
> and report problems back. The code, the debugging, the pits I fell into, the documentation, the
> installer packaging — I did all of that.
>
> To be blunt: I have no eyes and no hands. Throughout this development, the way I "saw" the screen
> was grabbing it with `PrintWindow` and reading the PNG, and the way I "clicked" buttons was
> injecting mouse and keyboard events with `SendInput`. Every screenshot and every number below came
> from exactly that.

---

## Table of contents

| Stage | Content | Key numbers |
|---|---|---|
| [1](#1-requirements-and-technology-choice) | Requirements and technology choice | why IDD out of three routes |
| [2](#2-making-windows-actually-grow-a-screen) | Making Windows actually grow a screen | 12 rounds of attach/detach |
| [3](#3-capture-and-encoding-gdigrab--ddagrab) | Capture and encoding | gdigrab stuck at 49 fps |
| [4](#4-latency-from-3142-ms-down-to-511-ms) | Latency optimization | **31–42 ms → 5–11 ms** |
| [5](#5-touch) | Touch | a swipe of 46 events, coordinates match point by point |
| [6](#6-disconnect-means-detach) | Disconnect means detach | gone within 1 second of unplugging the cable |
| [7](#7-identifying-the-screen-by-content-the-key-110-fix) | Identifying the screen by content (1.1.0) | on a collision, 0.0 vs 33.8 |
| [8](#8-just-how-fast-is-this-link) | USB link speed test | **597 Mbps** |
| [9](#9-packaging-into-an-exe-and-a-one-click-installer) | Packaging and installer | a 34 MB single-file installer |
| [10](#10-the-weirdest-bug-the-ai-session-sandbox) | The weirdest bug | `CDS_TEST rc=-1` |
| [11](#11-code-layout) | Code layout | |
| [12](#12-known-limits--things-i-did-not-do) | Known limits | |
| [13](#13-a-few-pieces-of-advice-for-whoever-comes-next) | Advice | |
| [14](#14-120-making-it-look-like-a-real-program) | 1.2.0: settings memory / update check / in-app docs / UI | 11 settings checked item by item across a restart |
| [15](#15-130-growing-an-app-on-the-phone-from-nothing) | 1.3.0: phone icon / live overlay / log switch / fill the screen | screen detection 9 s → 1 s |
| [16](#16-140-stall-self-recovery--data-directory--phone-ui-redraw) | 1.4.0: stall self-recovery / data directory / phone redraw | recovery 8.1 s → 4.1 s |
| [17](#17-141-the-issue-1-path-that-broke-four-releases) | 1.4.1: issue #1 / path cleanup / CI | driver install broken in all 4 releases |
| [18](#18-150-update-in-app-instead-of-jumping-to-a-browser) | 1.5.0: in-app update | four checks, any one failing means no install |
| [19](#19-151-the-virtual-screen-vanishes-out-of-thin-air--the-driver-mode-table-blew-the-buffer) | 1.5.1: virtual screen vanishing / mode table cap | 110 combinations → 55, the driver finally does its job after being installed |
| [20](#20-160-fourteen-things-that-were-promised) | 1.6.0: the fourteen promises / eight real bugs | 11 encoders advertised, **7 actually work** |
| [21](#21-161-touch--measure-the-numbers-first) | 1.6.1: touch dead zone / long-press right click / frame-rate pacing | the phone went from 91 writes a second to 45 |
| [22](#22-162-a-double-click-that-windows-refused-to-call-one) | 1.6.2: install folder / agree button / double click | Windows' double-click box is 4 × 4 pixels |
| [23](#23-163-two-menus-that-both-asked-the-wrong-question) | 1.6.3: resolution menu / encoder menu | eleven of twenty-two sizes had no mode behind them |
| [24](#24-164-making-the-virtual-display-actually-disappear) | 1.6.4: putting the virtual display away / two modes | a count of 0 does nothing; disabling the device works |
| [25](#25-164-part-two-the-in-app-update-never-once-installed) | 1.6.4, part two: the in-app update never installed | under `DETACHED_PROCESS` the child exits 0 and runs nothing |
| [26](#26-165-the-update-folder-moved-on-its-own-and-uninstalling-left-the-app-running) | 1.6.5: the update folder / uninstall leftovers | the data-folder pointer had been written to point into `%TEMP%` |
| [27](#27-166-all-data-in-the-install-folder-one-instance-only) | 1.6.6: all data in one folder / a clean uninstall / one instance | the folder disappears after an uninstall; a second double-click starts nothing |
| [28](#28-167-why-the-latency-creeps-up) | 1.6.7: the latency figure keeps growing | clock drift of 3 ms per minute; the picture never got slower |

---

## 1. Requirements and technology choice

The requirements started from these three points (the original words are in the session record; paraphrased here):

1. **The picture has to be live** — you cannot "read the whole picture first and then send it out", that adds a lot of latency;
2. **It has to be controllable by touch**;
3. **After the connection drops, the system must not still have a screen left in it**.

Plus one very important hint: "or think about it differently — make an apk that lets Windows believe the Android tablet I plugged in is a touch-capable wired extended screen."

That last sentence is the basis for the whole project's technology choice. **Mirroring** (the scrcpy, Deskreen family) solves "send the computer's picture out",
whereas what is wanted here is "**make Windows genuinely believe it has one more monitor**". At the operating-system level these two are completely different:

* Mirroring: grab the main screen → encode → send it to the phone. Windows' display topology does not change one character, and windows cannot be dragged over there.
* A real extended screen: first **Windows itself has to grow one more monitor**, and then the system renders part of the desktop onto that screen.

To make Windows grow a monitor out of thin air, only a kernel-level **IDD (Indirect Display Driver)** can do it.
So the choice was settled:

```
Virtual display driver (IDD)  →  DXGI Desktop Duplication  →  h264_nvenc  →  adb reverse (USB)  →  phone MediaCodec hardware decode
   Windows really has a screen       ddagrab grabs that screen      GPU encoding       one TCP tunnel         zero-copy onto the screen
```

* The driver is the open-source **Virtual Display Driver 25.7.23** (signed; no need to turn off Secure Boot, no need for test mode)
* Transport goes over **`adb reverse`**, i.e. it reuses the USB cable — **no Wi-Fi, no need to be on the same subnet**
* Nothing third-party is installed on the phone; the APK ships with this repository (902 lines of Java, no Gradle)

---

## 2. Making Windows actually grow a screen

`host/win_display.py` (22 KB) is the most "low-level" file in this project; it wraps:

* `ChangeDisplaySettingsEx` + `DEVMODEW` (a 220-byte struct, not one field may be wrong)
* the CCD API (`SetDisplayConfig` / `GetDisplayConfigBufferSizes` / `QueryDisplayConfig`)
* `EnumDisplaySettingsEx` / `EnumDisplayDevices` (enumerating modes and monitor names)

Putting a screen up takes three things in order: `attach` (activate the monitor's path) → `extend_topology` (change the topology to extended)
→ `set_mode` (set resolution and refresh rate). Get the order wrong, or leave too long a gap in the middle, and Windows turns nasty.

**The first pit** was a key name in the enumeration function: `a["monitors"]` was written as `a["modes"]`,
so although the virtual screen was installed properly, it "could not be found". In the GUI this kind of KeyError shows up as a button that does nothing, and that is very hard to guess.

**The second pit** is more interesting, and it stayed alive until it was finally fixed for good in 1.1.0:
Windows **briefly locks the display configuration** before and after every display topology change. During that window:

```
ChangeDisplaySettingsEx → DISP_CHANGE_FAILED (-1)
SetDisplayConfig         → ERROR_INVALID_PARAMETER (5)
EnumDisplaySettingsEx    → fails outright
```

Originally each action was performed only once, so "**the second click on Start casting is bound to fail**". The fix was to give `apply_display()`
a retry loop (6 attempts each for attach / extend_topology, 4 for set_mode, 0.5–0.7 s apart),
and on failure to **detach the virtual screen as a fallback**, never leaving a half-applied state. Measured over 3 back-to-back rounds of `apply_display()` → `detach()`:

```
round 1  apply_display OK in 2.6s      live: ['\\.\DISPLAY1'] → ['\\.\DISPLAY1','\\.\DISPLAY20']
round 2  apply_display OK in 2.7s      live: ['\\.\DISPLAY1'] → ['\\.\DISPLAY1','\\.\DISPLAY20']
round 3  apply_display OK in 2.6s      live: ['\\.\DISPLAY1'] → ['\\.\DISPLAY1','\\.\DISPLAY20']
```

---

## 3. Capture and encoding: gdigrab → ddagrab

I first got the whole chain working with the laziest option, `gdigrab` (GDI screen capture), and then found it has a **hard ceiling**:

```
gdigrab measured throttled at ~49 fps — and it has nothing to do with how large an area you grab
```

The 1080p60 target was immediately hopeless. So I switched to `ddagrab` (DXGI Desktop Duplication, desktop duplication),
which hands you a D3D11 texture on the GPU that can be **fed straight to `h264_nvenc`, barely touching the CPU**.

But `ddagrab` has a few tempers you have to remember (all measured):

* `output_idx` is a **DXGI output ordinal**, not a stable ID; on this machine `0` = the laptop's internal screen at 2560×1600, `1` = the virtual screen at 1920×1080, and `>=2` reports `Failed to enumerate DXGI output` outright.
* **Frame 0 of every stream is a pure black frame**, and it has to be dropped before encoding, otherwise the phone shows a black flash first.
* Omitting `video_size` uses the output's native size — which is exactly the way to **enumerate which outputs exist** (grab 1 frame and ask how big it is).

---

## 4. Latency from 31–42 ms down to 5–11 ms

This was the first item in the requirements, and it is the fix I am most pleased with myself.

Every 2 seconds the phone prints a line `perf fps=… latency_avg_ms=… latency_max_ms=…`;
the timestamp is stamped by the host **at frame-grab time**, and the phone converts it using the clock offset worked out during the handshake,
so what is measured is the whole chain **from grabbing the frame to the picture coming out**:

```
Before the fix   latency_avg_ms 31~42    latency_max 56~79
After the fix    latency_avg_ms  5~11    latency_max 26~45
```

**The root cause was neither the network nor the encoding, but one line of Python reading.** This is how ffmpeg's output used to be fetched:

```python
data = p.stdout.read(65536)      # ← 灾难 (disaster)
```

`p.stdout` is a `BufferedReader`. The semantics of `read(n)` are "**read n bytes, or read until EOF**",
not "give me whatever there is". An H.264 stream does not fill up 64 KB by itself the way a file does,
so every single time it sat there waiting until the buffer was full before returning. Measured:

```
p.stdout.read(65536)          → delivers 64 KB only once every 200.0 ms on average
os.read(p.stdout.fileno(), n) → delivers 4.6 KB once every  13.7 ms on average
```

In other words, the picture sat idle in the buffer for about **100 ms** before entering the encoding queue; on the phone this showed up as
"accumulate a batch and then render it all at once" (logcat used to contain `Render time interval reaches 154~258ms`, and after the fix it was gone).

Changed to:

```python
data = os.read(p.stdout.fileno(), 65536)   # 有数据立刻拿走，绝不等待凑满 (take whatever there is immediately, never wait for a full buffer)
```

With that one line changed, the average latency dropped straight into single digits.

> **Lesson**: `BufferedReader.read(n)` does not mean "read at most n bytes", it means "read until n bytes".
> No code that handles a **live stream** should ever use it.

---

## 5. Touch

`host/touch.py` listens on `127.0.0.1:27184` (on the host side; the phone connects in through `adb reverse`),
and converts the normalized coordinates the phone reports (0.0–1.0 floats) into Windows
**absolute-coordinate injection** (`SendInput` + `MOUSEEVENTF_ABSOLUTE`, range 0–65535),
supporting: tap, drag, two-finger scroll, two-finger tap = right click.

End-to-end verification (both from the source tree and from the frozen exe):

```
tap   : cursor (3520,539) → (3520,319)          ← the mouse really moved, and it landed on the virtual screen
swipe : produced 46 events, coordinates match point by point
e2e   : cursor (1280,800) → (2848,755) (expected (2848,756), off by 1 pixel = coordinate rounding)
```

While I am at it, let me record a **bug in a test script I wrote myself**, because it is very typical:
`Injector.down(nx, ny)` takes `nx/ny` as **0–65535** integers, and the first time I passed fractions in 0.0–1.0,
so all three clicks landed in the window's top-left corner `(32,32)`, which on the screen looks exactly like "the button does nothing".
I fiddled with that for a long time before it occurred to me to call `GetCursorPos` and print the actual coordinates.

---

## 6. Disconnect means detach

This is the third item in the requirements. A virtual screen is a **system-level resource**: if the program crashes, gets killed, or the cable is unplugged and it forgets to detach,
the user will see a **ghost monitor** in `Win+P`, and the mouse will run off past the edge of the screen into the void — which is extremely annoying.

Three layers of insurance were put in:

1. `session.stop()` explicitly calls `wd.detach()` (the normal path);
2. when starting a cast **fails**, `_bg_done()` also calls `session.stop()`, clearing the half-applied state;
3. exiting the program / closing the window cleans up the same way.

Measured, "click Stop → the virtual screen disappears" takes **under 1 second**, and `desktop bounds` goes cleanly from `4480x1600` back to `2560x1600`.

---

## 7. Identifying the screen by content (the key 1.1.0 fix)

This one came from user feedback (original words, m02626):

> After installation, when using the extended-screen mode it transmits the main screen picture. And during installation it should let me choose the path myself

The second half is an installer problem (see section 9); the first half is a **real bug**, and a deeply hidden one.

`ddagrab` only understands a DXGI output ordinal — **it neither understands device names nor has an option to pick a screen by desktop coordinate**
(the complete set of AVOptions from `ffmpeg -h filter=ddagrab` is just
`output_idx` / `draw_mouse` / `framerate` / `video_size` / `offset_x` / `offset_y` /
`output_fmt` / `allow_fallback` / `force_fmt`). So it had to be identified by hand.

1.0.0's approach was **to identify the screen by resolution**:

```python
for idx, w, h in outs:
    if w == int(width) and h == int(height):
        return idx          # ← 撞车时先命中的是主屏 (on a collision the first hit is the main screen)
...
return outs[0][0]           # ← 兜底也是主屏 (the fallback is the main screen too)
```

The trouble is that **a resolution is not an identity at all**. `1920x1080` is both the most common laptop panel in the world and the virtual screen's default value.
Once the two are equal, the code confidently picks **output 0 = the laptop's own screen**, and the phone naturally shows the desktop you are looking at.

1.1.0 changed to **identifying the screen by picture content**:

1. First filter the candidates by resolution;
2. only one → just use it;
3. more than one → use `gdigrab` to grab the **desktop rectangle** the virtual screen sits on as a reference, then grab a
   16×16 thumbnail of every candidate output and compare them pixel by pixel for the average difference;
4. **if it cannot tell (the difference is too large, or the first and second place cannot be told apart), it returns "don't know"**,
   and the layer above automatically falls back to `gdigrab` grabbing by desktop coordinate — slower, but **always right**.

I deliberately set the virtual screen to the same 2560×1600 as the laptop's internal panel to create a collision, and measured:

```
ddagrab output: #0=2560x1600, #1=2560x1600      ← exactly the same resolution, size cannot tell them apart
identify(virtual screen) → #1   (#1 diff=0.0,   #0 diff=33.8)
identify(main screen)    → #0   (#0 diff=5.0,   #1 diff=35.6)
cross-check: #1 vs virtual screen=0.00 / vs main screen=33.66
             #0 vs virtual screen=33.26 / vs main screen=4.59    ← cleanly separated
```

Then I ran it again through the real capture path (under the colliding topology): the first 51 frames `dropped 0`, and the decoded video was confirmed to be the **virtual screen**.

> **While I am here, let me explain an illusion**: an extended screen defaults to the **same wallpaper** (Windows' wallpaper "Fill" mode),
> and the taskbar draws another strip on it as well. So **an empty extended screen looks exactly like the main screen at a glance**,
> and it is very easy to misjudge it as "it is sending the main screen". Now the log writes the virtual screen's desktop coordinates straight out:
>
> ```
> 拓展屏就绪: \\.\DISPLAY20 -> 1920x1080@60 at (2560,0)
>   手机上是桌面 (2560,0) 往右 1920 像素那块屏；把窗口拖过去就看得见
> ```

---

## 8. Just how fast is this link

The program has a 「测一下数据线速度」 (Test the data cable speed) button. Building it also settled "**how to measure without fooling yourself**":

* **`adb push`'s numbers cannot be trusted.** Pushing a 64 MB **all-zero** file to the phone, it reports
  `1265.1 MB/s` — because all zeros get compressed, and because it only counts local buffering time.
* **`adb exec-out dd` is no good either**: pulling 64 MB took 4.78 s = **13.4 MB/s**,
  but that is the **shell/exec-out protocol's own ceiling**, not the USB limit.

The measurement that does hold up: open a **raw TCP** connection over `adb reverse`, push
**incompressible random data** generated by `os.urandom`, and **have the phone itself report how many bytes it received**, then compute "total bytes ÷ (send + drain) time":

```
Sent 64.0 MB in 0.73s, phone confirmed receiving 56.0 MB, send+drain 0.79s
→ 71.1 MB/s = 597 Mbps
```

71 MB/s already **exceeds USB 2.0's theoretical ceiling of 60 MB/s**, so this link is **USB 3 class**.
Swapping cables on the same phone, I measured three stages: **13.7 Mbps (a charge-only cable) → 154.2 / 147.3 Mbps (an ordinary data cable) → 597 Mbps (a good cable)**.

One more conclusion worth stating on its own: **the default 40 Mbps is only VBR's ceiling; picture quality is mostly decided by `Q`**.
Full-screen motion at 1080p60 at `Q=20` only used 2.3–2.7 Mbps (the content is too simple).

---

## 9. Packaging into an exe and a one-click installer

### 9.1 Freezing into an exe

PyInstaller (`--onedir --windowed`). A few points that have to be remembered:

* **Every path in the command must be absolute**, and you need the `&` call operator plus backticks for line continuation;
  giving `--icon` a relative path resolves it against `--specpath` and then reports `FileNotFoundError`.
* **Do not** expect to find `touch.pyc` inside `_internal/` — PyInstaller packs pure-Python modules into the PYZ archive inside the exe.
  The right way to verify a build is to look at the module-level `from touch import ...` on `host/session.py:21`:
  **if the window opens, touch made it in**.
* Do not use `cmd /c start` to launch a GUI-subsystem program, it will not really start it;
  using `& '.\DroidExtend.exe'` inside a background job also returns exit 0 immediately and the process is then reaped.
  The reliable way is `Start-Process -PassThru -RedirectStandardOutput …`, and completing "start → check → act" within a single invocation.

### 9.2 The Inno Setup installer

The installer is 34 MB and bundles ffmpeg, adb, the virtual display driver files and the phone APK, so **nothing else has to be prepared after installing**. Pits I fell into:

* **Install into `%LOCALAPPDATA%\Programs\DroidExtend` rather than `Program Files`, and never elevate** —
  because at runtime the program writes adb's temporary files into `tools\tmp`, and installed under `Program Files` a normal-privilege process cannot write there.
  The virtual display driver is a system-level component, and **the installer does not do it for you**; the button in the program's UI installs it separately, with elevation.
* Parameters in the `[Setup]` section use **equals signs**, while parameters in `[Icons]/[Files]/[Run]/[Tasks]` use **colons**.
  Get one wrong and you get `Unrecognized parameter name "Comment="…""`.
* **After editing the `.iss` you must put the UTF-8 BOM back.** Edit it once in an editor and the BOM is gone,
  and without a BOM Inno reads the file as ANSI, so **all the Chinese turns into mojibake**.
* Inno's **page headings** are the `Wizard*` family (`WizardSelectDir` / `WizardSelectTasks` / `WizardReady` / `WizardInstalling`);
  `SelectDirDesc` is only the small line under the heading. The finished page's heading is `FinishedHeadingLabel` —
  I tried both the nonexistent `WizardFinished` and `FinishedTitle`.
* The disk-space line uses the `[mb]` placeholder, **not `%1`**.
* **`DisableDirPage` must be set to `no` explicitly.** Its `auto` **silently keeps the old directory and skips the whole page** when it detects a previous installation,
  so the user never sees the page for choosing a path — which is exactly the second thing the user complained about.
* If you want screenshots of the installer wizard: Inno 6's wizard window class is **`TWizardForm`** (a VCL form), not `#32770`;
  and `Setup.exe` unpacks itself into `%TEMP%\is-XXXX.tmp\` before running,
  so **the window's owning process is not the one you launched**, and the only way to find it is by title. Force-killing the outer process also leaves a ghost wizard behind in the inner one,
  and the next screenshot then captures the previous round's old window.

---

## 10. The weirdest bug: the AI session sandbox

This was the **hardest thing to track down** in the whole project, and the only problem that was **not a defect in the program**.

**Symptom**: running the packaged `DroidExtend.exe` inside the project directory, clicking 「开始投屏」 (Start casting) always fails:

```
虚拟显示器: \\.\DISPLAY20 (Virtual Display Driver)
 激活显示器  attach rc=-1  (重试 2..5)
 attach 未生效，改用扩展拓扑 rc=5
RuntimeError: 虚拟显示器 \\.\DISPLAY20 无法激活（未上桌面）
```

Yet **on the same machine, with the same code**, running `python host/app.py` from source works fine.

**The investigation** (ruled out in order):

| Suspect | How it was ruled out |
|---|---|
| PyInstaller freezing itself | wrote a minimal probe; both the `windowed` and `console` ways of freezing reproduce it |
| DPI awareness | a frozen process defaults to `GetProcessDpiAwareness = 0` (unaware); setting PER_MONITOR_V2 explicitly **still fails** |
| runtime differences | aligned item by item: both are `python 3.14.7`, `sizeof(DEVMODEW)=220`, `sizeof(PATH_INFO)=72`, the same `cwd`, the same function address `fn=0x7ffd303bd5a0` |
| module resolution | `ctypes.windll.user32` / `WinDLL(full path)` / `LoadLibraryW` give **exactly the same function address**, and there is no user32.dll in `_internal` either |
| code integrity policy | Smart App Control is off (`VerifiedAndReputablePolicyState = 0`), there is no WDAC user-mode policy, no ASR rules |
| parent process chain | launching from `explorer` (detached from my process tree) **still fails** |

**The decisive experiment**: I wrote a minimal probe in C# (`tools/disp_probe.cs`, calling only
`EnumDisplaySettingsEx` / `ChangeDisplaySettingsEx` / `SetDisplayConfig`),
compiled it into **the same `DispProbe.exe`**, and then **changed only the directory it sits in**:

| Location of the executable | `CDS_TEST` | `SDC_VALIDATE` |
|---|---|---|
| `C:\WINDOWS\Temp\DispProbe.exe` | `rc=0` ✅ | `rc=0` ✅ |
| `D:\probe_test\DispProbe.exe` | `rc=0` ✅ | `rc=0` ✅ |
| `C:\Users\15530\DispProbe.exe` | `rc=0` ✅ | `rc=0` ✅ |
| `D:\dsh projects\DispProbe.exe` | `rc=-1` ❌ | `rc=5` ❌ |
| `D:\dsh projects\droid-extend\tools\tmp\DispProbe.exe` | `rc=-1` ❌ | `rc=5` ❌ |

**Conclusion**: it is the **AI session's sandbox** refusing to let a process whose **executable image lives under the workspace `D:\dsh projects`**
change the display configuration. It **acts on the image path**, and has nothing to do with the program itself, with PyInstaller, with signing, or with DPI.
In the same way, the sandbox also refuses to let such processes write files **outside** the workspace —
the installer, launched inside the workspace, simply hangs silently (`Start-Process -Wait` does not return for over 300 seconds,
the target directory never appears, and it is not visible in the process list either).

**This finding has exactly one practical consequence**: **while this AI session of mine is still open, do not run the installer or the exe inside the workspace**;
copy it somewhere else and run it there (`D:\DroidExtend\` or an install into `%LOCALAPPDATA%`).
Once the session is closed, this restriction does not exist. The README carries this warning too.

I am writing this down in full because **if another AI ever develops a Windows system-level program in this workspace, it will hit this again**,
and its symptoms (`rc=-1` / `rc=5`) are far too easy to misjudge as "the code is wrong".

---

## 11. Code layout

```
host/         Windows side, 2687 lines of Python
  app.py            GUI main program (Tkinter)
  session.py        one casting session: display → capture → encode → USB → phone
  capture.py        ffmpeg command line, ddagrab output probing, H.264 access-unit splitting
  server.py         TCP streaming service (handshake + frame queue + frame-dropping policy)
  touch.py          touch injection (SendInput absolute coordinates)
  win_display.py    CCD / ChangeDisplaySettingsEx wrappers
  vdd.py            virtual display driver installation and status checks
  adb.py            adb wrappers (reverse tunnel, install, launch)
  linkspeed.py      USB link speed test
  paths.py          locating the bundled adb / ffmpeg / JDK / SDK

android/      phone side, 902 lines of Java (no Gradle)
  src/com/droidextend/MainActivity.java    UI + touch collection
  src/com/droidextend/StreamClient.java    TCP stream intake + clock sync
  src/com/droidextend/VideoDecoder.java    MediaCodec hardware decode + on-screen display
  src/com/droidextend/InputClient.java     touch/gesture reporting
  build.py                                 aapt2 → javac → d8 → package → apksigner one-shot build

tools/        toolchain and test scripts, 2713 lines of Python
```

During debugging I wrote a large number of one-off probes (`tools/probe_*.py`, `bench_*.py`, `dbg_*.py`),
and I left them all in the repository — they record how each conclusion was measured.

---

## 12. Known limits / things I did not do

* **No sound.** Video only; the audio path was never built at all.
* **Only measured on Windows 11 + NVIDIA (NVENC)**. The code has `h264_amf` (AMD) and `libx264` (pure CPU) as fallbacks,
  and the driver install script is generic too, but **I never verified on an AMD / Intel machine**.
* **A kernel-level virtual display driver must be installed** (it raises UAC once). This is the precondition a "real extended screen" cannot avoid.
* The phone APK is signed with an **automatically generated debug key** (`android/build.py` generates the keystore on the first build),
  so it is not suitable for an app store.
* By design it is **one computer to one phone**; there is no scheduling for casting to several devices at once.
* It depends on `adb`, so the phone must have USB debugging on; that is the price paid for "not going over Wi-Fi".

---

## 13. A few pieces of advice for whoever comes next

1. `BufferedReader.read(n)` **waits for n bytes** before returning. For live streams always use `os.read(fd, n)`.
2. `ddagrab`'s `output_idx` is an **unstable ordinal**, and resolution is not an identity either. To identify a screen you have to **compare the picture**, or grab by desktop coordinate.
3. Windows **briefly locks the display configuration** before and after topology changes, so every `ChangeDisplaySettingsEx` / `SetDisplayConfig`
   needs **retries**, and on failure a **fallback cleanup**, so you never leave a ghost monitor.
4. System-level resources (virtual monitors, drivers, display configuration) **must always be rollback-able**. I spent about a third of my time on that, and it was worth it.
5. When measuring speed, first ask "**could this number be lying to me**". `adb push` can (compression + local time only),
   `adb exec-out dd` can (protocol ceiling). Only **the receiver reporting its own byte count** counts.
6. If a program "is clearly correct in the code but only breaks once packaged", **suspect the environment before the code** —
   for the sandbox problem in section 10 I went a long way around before it occurred to me to run the same binary from a different directory.
7. Do not forget **`第三方组件许可.txt` (Chinese)**. If you use MIT / Apache / BSD things and distribute binaries,
   attaching the copyright notice is not a matter of courtesy, it is a licence condition.

---

## 14. 1.2.0: making it look like a real program

1.1.0 already ran, but using it still felt like "a script that works". The user's feedback was very specific: **you tune the parameters once, close it and open it again and they are all back to how they were**,
   there was nowhere in the UI to see the version, no documentation, and no way to know whether there was a newer version. So this round went entirely into "what a proper piece of software ought to look like".

### 14.1 Persisting settings

`%USERPROFILE%\.droidextend\settings.json`, with `host/settings.py` doing the reading and writing; the `CONFIG_DIR` in `host/paths.py` that was **defined but never used by anyone** finally earned its keep.

A few deliberate decisions:

* **A whitelist.** Only the keys listed in `KEYS` / `EXTRA` get written to disk, so junk data cannot get in;
  keys that are not listed (say a `junk` someone inserts by hand) are ignored when read, and I confirmed by measurement that they are not written to the file.
* **Never throw.** Missing file, contents that are not valid JSON, a root node that is not a dict — everything falls back to the defaults;
  a corrupted config file should not stop the program from opening.
* **Atomic writes.** `mkstemp` + `fsync` + `os.replace`, so a power cut cannot leave half a broken file behind.
* For values like resolution, where there is "a dropdown + possibly something not in the presets", a `custom_res` flag records whether it was picked from the list or typed in by hand.

The verification was **really opening two instances of the program**: the first changed all 11 settings to non-default values and saved and quit;
the second, after starting, compared item by item whether what it read back was the same set. Not by comparing file contents, but by reading the **values of the UI controls**.

### 14.2 Checking for updates

`host/update.py` requests the `releases/latest` endpoint, and `re.findall(r"\d+")` extracts a version tuple for the comparison
(`1.10.0 > 1.9.9`; a string comparison would get it wrong). Every network exception is **returned as data** rather than thrown —
a failed update check is not an error, it is just "nothing found", and it should not throw up a pile of error boxes. It checks quietly once at startup, and the `工具` (Tools) menu can turn it off.

### 14.3 In-app instructions

`host/manual.py` holds one whole plain-text document (9 sections, about 120 lines), and `帮助 → 使用说明` (Help → Instructions) opens a read-only window.
`帮助 → 常见问题` / `参数怎么选` (Help → FAQ / How to choose parameters) opens the same window and does `search()` + `see()` to **jump to the matching section**,
rather than giving you three windows. The About box lists, besides the version and the author, 9 third-party components and their licences.

For the same reason — **MIT requires the copyright notice to be included in distributed copies** — `第三方组件许可.txt` (Chinese) is placed both in the installer and in the program directory, and it can be opened straight from the `帮助` (Help) menu.

### 14.4 UI cleanup

Menu bar (文件 / 工具 / 帮助 — File / Tools / Help), a log-area toolbar (复制全部 / 另存为… / 清空 — Copy all / Save as… / Clear),
and a bottom status bar (version + repository URL + settings file path). The window size and position are stored in the settings too,
so it opens the same way next time (`_safe_geometry()` validates it, so changing screens will not leave the window off-screen).

### 14.5 A problem that only showed up during testing

In automated testing, opening two instances in a row, after the second one exited the console spewed out:

```
RuntimeError: main thread is not in main loop
```

The cause was background threads (`refresh_env` / `run_bg` and the like) still calling
`self.after(0, …)` after the window had already been `destroy()`ed — Tk's interpreter is gone, so registering a callback raises an error. **This is visible in my own tests,
but once it is packaged into a windowed exe stderr is thrown away, and the user just feels "now and then something does not respond".**
The fix is a 7-line `_later()` that replaces every `self.after(0, …)` in the worker threads, so a closed window does not count as a failure.

### 14.6 The stuck `busy`: a deadlock you only see once

After 1.2.0 was packaged, end-to-end testing produced the hardest kind of symptom to chase: **clicking 「开始投屏」 (Start casting) did absolutely nothing** —
the status sat at 「就绪」 (Ready), not one new line appeared in the log, and there was no error either. Yet clicking by hand worked fine.

In the end it was two causes stacked on top of each other:

1. **`busy` got stuck.** The first thing `run_bg()` does is `if self.busy: pop up 「请稍候」 (Please wait) and return`.
   `refresh_env()`, which runs automatically 300ms after startup, held that flag for about two seconds (it has to wait for adb to start its server and check the driver state).
   The test script happened to click the button inside those two seconds, so it popped up a **modal dialog** and then did nothing.
   Worse, the screenshot only captured the main window, and `PrintWindow` cannot capture that dialog — so it looked exactly like a broken button.
   The fix: give the environment refresh its own flag (`refr_busy`), which no longer occupies `busy` and no longer disables the start button;
   and use `try/finally` to guarantee that `busy` is always cleared, no matter what error happens in between.

2. **I broke `_later()` myself.** I used a bulk replace to turn `self.after(0, ` in the worker threads into
   `self._later(`, and as a result also replaced the line **inside `_later`'s own body** — turning it straight into infinite recursion.
   And `RecursionError` happens to be a subclass of `RuntimeError`, so it was silently swallowed by `_later`'s
   `except (RuntimeError, TclError)`: **every callback from a background thread to the UI became a no-op, and `busy` could never go back to False.**
   Lesson: after a bulk replace, go back and check whether the pattern you replaced also appears somewhere else, especially inside that function itself.

One more testing-method lesson while I am at it: when a probe uses an `a.update()` loop instead of `a.mainloop()`,
Tk's `after()` in a worker thread **inevitably** reports `main thread is not in main loop` —
that is not a problem with the code under test, it is the probe breaking the code under test. A probe has to use a real `mainloop()`.

---

## 15. 1.3.0: growing an app on the phone from nothing

### 15.1 The icon is drawn, not borrowed

`android/make_icon.py` draws it with Pillow: a blue gradient rounded square with a white monitor on it (with a title bar and a stand)
and a white phone beside it (with an earpiece slit). 4× supersampling and then scaled back down for anti-aliasing; every shape is computed in code,
**with no ready-made assets and no font used**. The legacy icon comes in 5 densities
(mdpi 48 / hdpi 72 / xhdpi 96 / xxhdpi 144 / xxxhdpi 192), one image each, and the adaptive icon's foreground layer likewise in 5
(108 → 432), with margins leaving the one-third safe area that the system crops away.
While I was at it I redrew `DroidExtend.ico` too.

There is an environment pit here: `C:\Python314` has **no PIL**. You have to use the interpreter DSH ships, the one with Pillow:
`C:\Users\15530\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe`.

Another pit is more worth recording: the manifest originally had **no `android:icon` at all**, so after installing, the launcher kept showing the system default icon —
this kind of "one line missing, looks like it was never done" problem is only visible in a screenshot from a real device.

### 15.2 Phone UI: status overlay + settings panel

The overlay is `HudView`, drawn entirely by hand in `onDraw`: the panel is 300dp wide with 14dp rounded corners,
a status line with a coloured dot + 「已连接」 (Connected), and 5 lines below it — resolution / frame rate / bitrate / latency / quality.
Latency is coloured in two steps, 18 ms and 40 ms (green / amber / red); when catching up on frames it draws an extra amber dot and marks 「追帧中」 (Catching up).
**There is no layout XML**: this panel is redrawn every 2 seconds, and running it through inflate + relayout is not worth it.

The overlay sits on top of the video picture, so it must **take touches only on its own gear** and return `false` for everything else so the event reaches the
`VideoSurface` underneath (otherwise you cannot click on the desktop at all). A very typical bug got buried here:
`ACTION_DOWN` returned `true` on hitting the gear but never called `setPressed(true)`, so in the `ACTION_UP` branch `isPressed()` was false and the callback never fired —
**the gear could not be clicked at all**. The evidence was that two screenshots taken before and after the click had byte-for-byte identical sizes. The fix was to write `onTouchEvent`
as a complete switch: DOWN sets pressed, UP clears pressed and fires the callback.

The settings panel (three switches) is built in code: a `GradientDrawable` rounded card + a full-screen translucent scrim,
closed by tapping empty scrim. The three switches are 「状态浮窗」 (Status overlay), 「让画面铺满屏幕（裁掉多余边缘）」 (Fill the screen, cropping the extra edges) and 「把日志写到文件」 (Write the log to a file).

### 15.3 Logging: logcat by default, a file only if you ask

`AppLog` always writes to logcat; the file is written only after the switch is turned on, and it lands in
`Android/data/com.droidextend/files/DroidExtend.log` (the app's external private directory, **no storage permission needed**).
A stream thread must not block on disk, so file writes go through a single-threaded `ExecutorService` writing serially to disk. The switch state lives in `Prefs` (SharedPreferences) and defaults to off. Once the log is turned on the panel shows the full path,
and gives you a 「清空日志」 (Clear log) button.

### 15.4 Making the virtual screen fill the phone's resolution

The requirement was "read the phone's maximum resolution and make the extended screen fill the whole picture". The approach has three steps:

1. On the host, `adb shell wm size` gets the physical size (on this device `Physical: 2136x3200`). The app is locked to landscape,
   so take `max/min` and treat the long edge as the width → `3200x2136`.
2. Pick a mode from the driver's mode table with **exactly the same aspect ratio** (`win_display.best_mode()`, exact hits first,
   otherwise sorted by `abs(aspect ratio difference)` and then by `abs(area difference)`).
3. If the table really has nothing, write the phone's resolution into `C:\VirtualDisplayDriver\vdd_settings.xml`.

Step 3 took the most time in this requirement. Looking into it turned up three things:

* The driver reads `vdd_settings.xml` **only when the device node starts**. After editing the file, re-enumerating the mode list
  gives exactly the same count — you have to reinstall the driver or reboot the computer for it to take effect.
* But that file **can be written without administrator rights** (it is under `C:\VirtualDisplayDriver\`, writable by a normal user).
  So you can "book" the phone's resolution into it, and prompt the user to click 「安装虚拟显示器驱动」 (Install virtual display driver) once next time.
* Elevation inside the sandbox always fails (`Start-Process -Verb RunAs` returns `exit=-196608`, and the elevated child process
  produces no files at all), so **the driver reinstall can only be triggered by the user clicking a button**; I cannot do it for them.

The mode table grew from the 5 it shipped with to **22** (1280x720 … 3840x2160, including 3200x2136, 2712x1220, 1600x720
and other landscape sizes of common models), with refresh rates 30/60/90/120/144. So that there would not be two sources of truth,
the copy of the XML embedded in `tools/vdd_install.ps1` was deleted, leaving only
`tools/vdd/vdd25/VirtualDisplayDriver/vdd_settings.xml`, which the install script copies directly.

This phone (3200x2136, aspect ratio 1.498) has no exact hit in the mode table, so `best_mode` gave the closest,
**2560x1600@90** (aspect ratio 1.6). Filling it completely is done by cropping, as described in 15.2's 「铺满屏幕」 (Fill the screen) switch —
scale first and then crop the edges, which beats leaving black bars. The host UI also gained a 「跟随手机分辨率」 (Follow the phone's resolution) checkbox;
when it is ticked the resolution dropdown is disabled, so there are not two places that can set the resolution.

### 15.5 Screen detection from 9 seconds down to 1

In 1.2.0, to work out "which ddagrab output is the virtual screen", I changed to grabbing 16x16 thumbnails and comparing them pixel by pixel.
The logic is right, but the implementation was slow: those two `ffmpeg` calls used `-framerate 1` with `select=gt(n,0)`
(skipping frame 0, which is always black on every stream), and at 1 fps you have to wait for two frames — **about 3 seconds per call, 9 seconds for three**.
The log shows it very clearly: the two lines `ddagrab 输出` and `按画面比对` are 9 seconds apart.

After changing to `-framerate 30` the same stretch went from 9 seconds to **1 second**, with the same identification result (`#1=0.0, #0=25.0`).
Lesson: **for a capture that only exists to get one frame, the frame rate is a latency parameter, not a quality parameter.**

### 15.6 Test scripts lie too

On 1.3.0's first end-to-end run, "clicking Stop did nothing" appeared. I nearly treated it as a program bug, but it was **a problem with the test script**:

The button coordinates used to be stored as **a fraction of the whole window**, `(0.218, 0.583)`. Those numbers were measured on a 998-pixel-wide window,
while this time the window had been dragged out to 1196 wide — the layout is left-aligned, so the button's **absolute** position had not changed,
but the point computed from the fraction had slid onto the button's right edge, and a few pixels off sometimes means missing it entirely.
After changing to **absolute pixels relative to the window's top-left**, `(214, 504)`, the log shows
`want (538,890) cursor (538,890)` matching exactly.

The same run exposed a second thing: the script hard-coded "the virtual screen is on the right, so 15%,70% should land on (2848, 756)".
But the screen's position is a remembered setting, and on that machine it was "below", so the actual landing point was (383, 2720) —
**the mapping itself was completely correct (off by 1 pixel); what was wrong was the assertion**. The assertion now reads the virtual screen's real rectangle from
`live_monitors()` first and then computes the expected value, independent of the settings.

Put those two together and you get an old saying: **when a test fails, suspect the test first.**

---

## 16. 1.4.0: stall self-recovery / data directory / phone UI redraw

The user raised nine things at once (m04279):

> On the Android app, change the status overlay to something like the light small text in NetEase UU Remote (网易UU远程); do not leave one big blob in the top-left corner blocking the view. Also the settings button has to be movable.
> There is another problem: when I set the virtual screen parameters in Windows settings, the output picture just freezes; and when some Windows programs start and
> ask for administrator rights, the output picture freezes as well. In README.md on GitHub you do not need to write what the current version changed, it is enough that the log shows it.
> On the Android side, clearing the log should show a "cleared" message. On the Windows side, try to keep the config file and the log file in the same directory,
> preferably the directory chosen at install time, or let the user customize it. Draw the icon again, I am not happy with this version.

### 16.1 The picture just freezes — the only real bug in 1.4.0

**Symptom**: with casting running normally, all it takes is a touch of the virtual screen's parameters in Windows' display settings (change resolution, change position,
change scaling), or a program popping up a UAC elevation window at startup, and **the picture on the phone stays on the last frame forever**.
The host UI still says 「已连接」 (Connected), fps drops to 0, and it reports no error at all.

**Root cause**: both of these operations invalidate the desktop's DXGI output, and ffmpeg's ddagrab fails immediately afterwards:

```
AcquireNextFrame failed: 887a0026
Error demuxing input file 0: Generic error in an external library
```

`887a0026` is `DXGI_ERROR_ACCESS_LOST`. What makes it nasty is that **ffmpeg's return code here is 0** — it counts
"external library error" as a normal end too. The old code treated the process exiting as the only sign that "the stream is over", so it neither reconnected nor reported an error,
it just hung there.

The reproduction script `tools/tmp/stall.py`: mid-stream, call `win_display.set_mode(NAME, 1280, 720, 60, x, y)`
to change the mode once, and it **always freezes**.

**The fix** (three places):

* `capture.py`: `Capture` gained three states — `last_data` (refreshed on every frame received), `err_tail`
  (a rolling tail of the last few lines of stderr), `ever_started`; plus the matching `dead()` / `stalled(limit)` /
  `access_lost()` (looking for `887a0026` in `err_tail`). `start()` spawns a
  **stderr drain thread** — without draining, once ffmpeg fills the stderr pipe it blocks itself, which is yet another kind of false hang.
* `session.py`: `_watch_capture()` checks once per round, `STALL_LIMIT = 3.0` (no picture for 3 seconds means stalled),
  `RECOVER_WAIT = 4.0` (do not recover again within 4 seconds). What `_recover_capture(why)` does is:
  stop the old ffmpeg → **`_refresh_rect()` re-reads the virtual screen's real rectangle** → `Capture.start()` again.
* After recovery, **the phone connection must be cut** (`server.drop_client()`): the phone's `MediaCodec` is configured
  for the old size, and once the host recovers at a new size the decoder simply throws the picture away; without reconnecting, "frozen" just turns into "garbled".

There is also a branch that cannot be ignored: **the virtual screen really is gone** (the user uninstalled the driver, or deleted the screen in display settings) is not
a recoverable error. When `_refresh_rect()` returns `None` it sets `fatal = "虚拟显示器已从桌面消失，投屏停止"`,
and `app.py`'s `_poll_perf` reads `s.fatal` and stops the UI, rather than reconnecting without end.

Logging is throttled: the first time it always says so, after that once every 30 seconds — otherwise unplugging the screen would flood the log at one line per second.

**Measured** (`tools/tmp/recover.py`, producing two stalls): recovery time **8.1 seconds → 4.1 seconds**, both stages PASS.

The biggest chunk of those 4.1 seconds turned out to be **screen detection**. `_refresh_rect()` goes through `detect_ddagrab_outputs()` grabbing a thumbnail per output
and comparing them, and that used `-framerate 1`, while ddagrab **delivers frames on the frame-rate beat**, so at 1 fps you wait for two frames
(`select=gt(n,0)` has to skip the always-black frame 0) — measured at **2.25 seconds** per call; `-framerate 30` is
**0.31 seconds**, `-framerate 60` is 0.29 seconds (faster than that is pointless). The three call sites together went from 9 seconds to 1 second.

| Change | Time for one screen detection |
|---|---|
| `-framerate 1` (before) | 2.25 s |
| `-framerate 30` (now) | 0.31 s |
| `-framerate 60` | 0.29 s |

### 16.2 Settings and logs live in the same directory

The requirement was "keep the config file and the log file in one directory, preferably the one chosen at install time, or let the user decide".

The resolution order (`paths.data_dir()`):

1. the path recorded in `%LOCALAPPDATA%\DroidExtend\data_dir.txt` — if the user changed it themselves;
2. **the directory the program is in** (after packaging, the exe's own level; when running from source, the project root) — in other words **the directory chosen at install time**;
3. `%LOCALAPPDATA%\DroidExtend` — the fallback for when neither of the above can be written to.

The key is that 2 must not be trusted blindly: installed under `Program Files`, a normal user cannot write there. `_usable(d)` decides by
**really creating the directory, writing a probe file and deleting it again**, and if that fails it moves on. The decision is cached after the first time, so it does not probe repeatedly.

`settings.path()` and `applog.path()` both compute from `paths.data_dir()` on the spot, so the two files are **always in the same directory**;
there is no "settings here, logs there".

`settings.migrate_legacy()` is called once in `main()`: `%USERPROFILE%\.droidextend\settings.json`
(the 1.2.0–1.3.0 location), if it exists and the new location does not, is copied over — so existing users do not have to tune every parameter again.

`applog.py`: `DroidExtend.log`, `MAX_BYTES = 2 * 1024 * 1024`, rolled to `.1` when exceeded (only one generation kept).
**Every logging failure is swallowed**: dying because of the log is the stupidest way to go.

The menu gained `文件 → 打开数据目录（设置与日志）` / `更改数据目录…` / `打开程序目录` (File → Open data directory (settings and logs) / Change data directory… / Open program directory),
and the bottom status bar permanently shows which directory is in use; when you change directories it moves both files along.
The verification script `tools/tmp/datadir_test.py` passed all 12 items.

### 16.3 Phone UI: from "one big blob" to "small light text"

The old overlay was a whole translucent black slab with a rounded rectangle — a plaster on a bright picture. Now:

* **There is no background any more.** Just three small lines, 11sp, kept readable by an **outline-style shadow** (draw the black pass offset by 1px first, then the white one),
  so it reads whether what is underneath is snow or night. The first line is `● 2560 × 1600 · 59 fps`, the second is
  `3.00 Mbps · 4 ms`, and the third is the encoding parameters the host sent over at handshake time (`NVENC VBR Q20 · 40Mbps`).
* The status dot is the only coloured thing: green = normal, yellow = dropping frames, red = disconnected.
* The settings button changed from pinned in the top-right corner to **draggable**: in `onTouchEvent`, DOWN decides the hit, MOVE follows the finger, UP drops it into place,
  and the position is stored in `Prefs` (`gearX` / `gearY`, normalized 0..1, so changing resolution will not push it off-screen).
  Only a movement under 9dp counts as a "click" that opens the settings panel — otherwise every attempt to nudge it would pop the panel open as well.
* After clearing the log, a Toast says 「已清空日志」 (Log cleared).

After a restart the button is still where it was (`tools/tmp/drag_test.py`: drag to the bottom-left → restart → still at the bottom-left, PASS).

### 16.4 Redrawing the icon

All five `android/res/mipmap-*` densities plus `DroidExtend.ico` are drawn on the spot by `android/make_icon.py` with Pillow,
with no asset files: a rounded gradient base + a white monitor + a white phone + a blue gradient inside the screen + a drop shadow.
(`tools/make_icon.py` is a separate minimal version that uses only the standard library's `struct`, needs no Pillow and writes nothing but `DroidExtend.ico`; it is kept as a fallback.)

### 16.5 Lessons

**An ffmpeg exit code of 0 does not mean a normal end.** It returns 0 for `Generic error in an external library` too.
To judge that "the stream is dead" you cannot just look at whether the process exited, you have to look at **whether data is still coming** — that is exactly why `last_data` exists.
A 3-second silence timer is more reliable than any exit code.

**A recovery action must include "re-reading the external state".** A stall is usually caused by the user having just changed the display settings,
and at that moment the virtual screen's rectangle on the Windows side **has already changed**; restarting ffmpeg without looking at the new rectangle recovers into something still wrong.
We put "re-read the rectangle" into the recovery flow, and picked up a free bonus along the way: after changing the resolution you no longer have to click Start casting again.

**After recovery you have to tell the downstream.** The phone's decoder is configured for a size, and when the host switches size it must cut the connection and let it start over,
otherwise the user still sees something broken.

---

## 17. 1.4.1: the issue #1 path that broke four releases

### 17.1 The problem: one default argument wasted all four releases

[issue #1](https://github.com/Blank2007/DroidExtend/issues/1) was reported by **[@Bai-2008](https://github.com/Bai-2008)**,
and came with a complete repository investigation report. The heaviest item in it was a P0:

**From v1.1.0 to v1.4.0, the 「安装虚拟显示器驱动（需要管理员）」 (Install virtual display driver (requires administrator)) button was broken for every user.**

The four parameters of `tools/vdd_install.ps1` looked like this:

```powershell
param(
    [string]$Src = 'D:\dsh projects\droid-extend\tools\vdd\vdd25\VirtualDisplayDriver',
    [string]$Nef = 'D:\dsh projects\droid-extend\tools\vdd\nefcon\x64\nefconw.exe',
    ...
)
```

These are absolute paths from **my own machine**. The installer (`tools/installer/DroidExtend.iss:101`)
installs the script as-is to `{app}\tools\vdd_install.ps1`, and when `host/vdd.py` calls it,
**it passes not one argument** — so the script looks in that development machine's directory:

```
ERROR: source missing: D:\dsh projects\droid-extend\tools\vdd\vdd25\VirtualDisplayDriver
```

Exit code 3. The Windows side cannot get stdout (an elevated child process has its own console), and the log went somewhere else,
so what the UI showed was **"clicked it, nothing happened"**.

The fix: all four defaults became `Join-Path $PSScriptRoot ...`; `$PSScriptRoot` is
**the directory the script itself lives in**, so installed under `{app}\tools` it resolves to `{app}\tools\vdd\...`,
and under a source checkout's `tools\` it resolves into the repository. Both are right.

While I was there I also fixed the three other real defects the report pointed out:

1. **`Copy-Item`'s non-terminating errors were being swallowed.** The old code printed `Log("copied ...")` perfectly clearly,
   and the directory was empty. After changing to `Get-ChildItem ... | Copy-Item` it **counts** the entries in `$Dest`,
   and exits 3 if that comes to 0.
2. **Nothing handled a failing `Add-Content` inside `Log()`.** `Add-Content` only emits non-terminating errors by default,
   and the old code's empty `catch {}` caught nothing at all. Changed to `-ErrorAction Stop` plus a real
   `Write-Warning`, and the log directory is created up front.
3. **`-Path` changed to `-LiteralPath`.** The report's author gets this one slightly wrong (historic versions always used
   `-Path`, not the `-LiteralPath` he claims), but the direction is right: when a path contains spaces or square brackets,
   `-Path` treats them as wildcards.

(On the report's point that it "prints copied but the directory is empty", there is one more detail: `Copy-Item` errors are
**non-terminating** — it reports an error file by file, carries on, and finally returns success. That is why the log looked
perfectly normal.)

### 17.2 The host side can no longer rely on `$PSScriptRoot` either

`host/vdd.py` used to copy the script to `%TEMP%` first and then run it elevated — which made `$PSScriptRoot`
point at `%TEMP%`, so of course the driver source directory could not be found. Now **all paths are passed explicitly**:
`_write_elevator()` generates a launcher script in `%TEMP%` that calls the real install script with `-File`, carrying
`-Src` / `-Nef` / `-Log`, which sidesteps the nested-quote hell of `powershell -Command "... Start-Process ..."`
(`Start-Process`'s `-ArgumentList` merely joins the array with spaces and
**does not add quotes itself**, so paths that contain spaces have to be quoted by hand).

The log moved to `data_dir()\vdd_install.log` — an elevated child process's stdout is lost, so the log is the only channel.

The verification was not "reading the code and thinking it looks right", it was building a **simulated install layout**
(`%TEMP%\de_vdd_sim\tools`, holding only the script + `vdd25` + `nefcon`) and running 4 rounds for 11 assertions in total, including "the output must never contain
`dsh projects`". All PASS.

### 17.3 Root cause: a project that only ever ran on one machine

The real cause is not that `Join-Path` was used wrongly, it is that **this project only ever ran on the development machine, from beginning to end**.
The same batch of bad habits was scattered across 24 probe scripts under `tools/`, plus `disp_probe.cs` and
`sdk_manifest.json`.

A one-off script `tools/tmp/fix_dev_paths.py` replaced all these literals with

```python
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
```

(`tools/x.py`'s grandparent is the repository root). 24 files changed in one go, `compileall` clean.

One pit I fell into: `tools/shotsnap.py` originally had a UTF-8 BOM, and after inserting two lines at the top of the file
the BOM ended up on line 3, and Python reported `SyntaxError: invalid non-printable character U+FEFF`.
A BOM is only legal at the **very beginning** of a file.

The guard written into CI: if `D:\dsh projects` appears in `host/`, `android/`, `tools/` (except `tmp/`), it goes red.

### 17.4 Repository front matter

The parts of the report about "this is not a bug but it makes people doubt the project's quality" were fixed as well:

* `LICENSE` cleaned up — line 23 onward still carried a statement that "this project's code was written by AI",
  and GitHub therefore judged it as `NOASSERTION` instead of MIT. Now the LICENSE holds only the MIT text,
  and that statement moved into a separate `NOTICE`, which also lists the licences of ffmpeg / Android SDK / MTT VDD / nefcon
  clearly.
* A new `.github/`: issue templates (bug / feature), a PR template, `CONTRIBUTING.md`,
  `SECURITY.md` (spelling out that this program installs a kernel driver, injects input and opens an adb tunnel — these
  are exactly the things that are dangerous precisely when they are not spelled out).
* `.editorconfig`.
* `homepage` cleared — it used to hold the repository's own URL, which shows up on the GitHub page as
  "a link pointing at itself". Discussions was opened as promised in the README.
* `tools/release.py` now also bumps `android/AndroidManifest.xml`'s
  `versionCode` by 1 and sets `versionName` to the new version. Before, it only changed `host/version.py`
  and the `.iss`, and the version number inside the APK was missed — and Android refuses to install an update whose `versionCode`
  has not increased.

### 17.5 Lessons

**Putting a development machine's paths into default arguments is planting a dud for every user.** Worse, it **does not report an error**:
exit 3 is swallowed by the elevated child process, and the UI just shows "clicked it, nothing happened". A feature can be broken for four releases without anyone knowing,
because on the author's own machine it is always right.

**"I only run it on my own computer" is not an acceptable verification strategy**, even when the project has exactly one user.
This time the verification became "build someone else's directory layout and run it there" — which is the only valuable part of that fix.

**A reporter is free testing.** In [@Bai-2008](https://github.com/Bai-2008)'s report
one accusation does not hold up (the `-Path` one), but four do, and one of them is a P0.
When a report arrives, the right thing to do is reproduce each item and answer each item, including saying clearly which one does not hold and why.

---

## 18. 1.5.0: update in-app instead of jumping to a browser

The user's words for this version were:

> Keep working on what is not finished. For updates it is best to update inside the program instead of jumping out and updating on its own. It has to carry a security check, and download and update after confirmation.

Before this, the end point of 「检查更新」 (Check for updates) was `webbrowser.open()` — toss the release page at the browser and that is that.
The trouble is that **the user has to work out which file to download and verify the checksum themselves**, and the overwhelming majority of people will not verify it.
A program that goes stale on its own, paired with an update flow that needs the user to inspect the goods by hand, amounts to no update flow at all.

### 18.1 The full flow

```
工具 (Tools) → 检查更新 (Check for updates)
   → GET api.github.com/repos/Blank2007/DroidExtend/releases/latest
   → is the version number newer than the current one?
        → pop up 「发现新版本」 (New version found): release notes + installer size + release date
             → the user clicks 「下载并安装」 (Download and install)
                  → download to %TEMP%\droidextend_update\DroidExtend-Setup-X.Y.Z.exe
                  → four checks (see below)
                       → ask once more 「现在关闭本程序、安装并重新打开吗？」 (Close this program now, install and reopen it?)
                            → write a batch file, quit myself, and when the batch is done have it start me up again
```

**The key point is that "confirm and then download" and "download and then confirm" are two separate times**, with the checks in between.
The first confirmation is "do you want to download it", the second is "it passed the checks, do you really want to install it" — the second lays out the
SHA256, the byte count and the signature state exactly as they are for the user to see.

### 18.2 Four checks, any one failing means no install

`host/update.py`'s `download()` returns a path only after everything has passed; on any failure it deletes the temporary file:

| # | Check | What it looks like when it fails |
|---|---|---|
| 1 | `host_ok(url)`: must be https, and the host must be on the whitelist of `github.com` / `objects.githubusercontent.com` / `release-assets.githubusercontent.com` / `github-releases.githubusercontent.com`; **the URL after a redirect has to go through it again** | `下载地址不是 GitHub 官方域名，已拒绝：…` (the download URL is not an official GitHub domain, refused: …) |
| 2 | the official source must give a checksum: prefer the API's `assets[].digest` (the 64 digits after `sha256:`); if that is unavailable, look in the release body for the 64-digit hex of the file with the same name | `官方没有给出校验和，为安全起见拒绝下载。` (the official source gave no checksum, refusing to download for safety.) |
| 3 | byte count: first against `Content-Length` / the official size, then against the on-disk size after writing | `下载不完整…` (the download is incomplete…) |
| 4 | SHA256 must equal the official value | `校验和不符，文件可能被篡改。` (checksum mismatch, the file may have been tampered with.) |
| 5 | the first two bytes of the file must be `MZ` | `文件格式校验失败：不是 Windows 可执行文件` (file format check failed: not a Windows executable) |

Check 1 guards against "the release was modified to point at an external address", and check 5 guards against
**"the size and the hash both match, but it is an APK rather than an installer"** — which sounds absurd,
but it is exactly what came out of testing: passing `DroidExtend.apk` off as the installer makes the size and SHA256
fail, of course; change the checksums to the APK's as well and the first four all pass, and only `MZ` stops it.

**There is no code signing, so it does not pretend there is.** `signature_status()` reads the signature with
`Get-AuthenticodeSignature`, but that is **display only** and is not a gate —
this project has not bought a code-signing certificate, so the current installer is always `NotSigned`. The confirmation dialog says, in so many words,
「本程序没有代码签名证书，所以完整性靠这个校验和保证，不靠签名」 (this program has no code-signing certificate, so its integrity is guaranteed by this checksum, not by a signature).
The README's FAQ spells this out too, along with what a checksum cannot stop (it cannot stop the repository itself being taken over).

### 18.3 How the update actually lands

The hard part is not downloading, it is **replacing a running exe**: the installer cannot overwrite a running
`DroidExtend.exe`, and the program cannot wait for an installer that is waiting for it to exit. The solution is a batch file of our own
(`%TEMP%\droidextend_update\apply_update.cmd`):

```bat
set PID=12345
:wait
tasklist /FI "PID eq %PID%" 2>nul | find /I "%PID%" >nul
if not errorlevel 1 ( ping -n 2 127.0.0.1 >nul & goto wait )
start "" /wait "…\DroidExtend-Setup-1.5.0.exe" /SILENT /NORESTART /DIR="<current install directory>"
if exist "…\DroidExtend.exe" start "" "…\DroidExtend.exe"
del "%~f0"
```

A few subtleties:

* polling the PID with `tasklist | find` rather than `timeout`, because what is being waited for is "I really am gone";
* `ping -n 2 127.0.0.1` used as a sleep — `timeout` fails outright in a detached process with no console;
* **`/DIR` is written out explicitly.** Inno's `UsePreviousAppDir` would find the old directory anyway,
  but if that registry entry were gone, the update would **silently install a second copy** of the program, with the desktop shortcut
  still pointing at the old one, and the user would think the update never took effect. Hard-coding the directory removes that possibility;
* `install_dir` is only passed when running packaged (`ROOT` is the directory the exe sits in); running from source it is left empty;
* exiting goes through the **normal window-close path** (`_on_close`), so an update also saves the settings,
  stops the cast and detaches the virtual screen from the desktop — an update must not be a special case that bypasses cleanup.

### 18.4 Pits I fell into

**`tot = tot or total` blew up.** Writing that in a progress callback raises
`UnboundLocalError: cannot access local variable 'tot'` — the assignment makes `tot`
a local variable of `paint()`, and the closure's read fails outright. The consequence is that the progress bar **does not move a single number**,
staying at 「正在连接…」 (Connecting…) forever. Changed to `full = tot or total`. This bug only shows itself when you really drive the UI once through: testing
`update.download()` on its own is green, because the thing that is broken is the part that draws the interface.

**Release-body Markdown shoved straight into a Text widget looks terrible.** The release notes contain
`## headings`, `**bold**`, `[text](link)`. I wrote a 30-line `_insert_notes()`:
lines starting with `#` are drawn in bold and coloured, `[x](y)` keeps only `x`, backticks are stripped, `**` becomes a tag.
The pit is that `**` **often spans lines** (a paragraph has one only at the start and one at the end), so a per-line regex necessarily misses some;
you need a `bold` state bit carried across lines and cut as you scan.

**`X-OAuth-Scopes: repo`.** I could not push files under `.github/workflows/` —
GitHub requires the token to carry the additional `workflow` scope, and the error text is:
`refusing to allow a Personal Access Token to create or update workflow '.github/workflows/ci.yml' without 'workflow' scope`.
Only a human can solve that (sign a new token), so the workflow file sat temporarily at
`tools/tmp/ci.yml.hold`.

**`urllib.request.getproxies()` reads the proxy out of the Windows registry**, even when
`HTTP_PROXY` / `HTTPS_PROXY` are both empty. When uploading the 36 MB installer it quietly went through this machine's
Clash, reporting `[Errno 10054] 远程主机强迫关闭了一个现有的连接` (the remote host forcibly closed an existing connection). The fix is to construct an opener that does not use a proxy explicitly:
`urllib.request.build_opener(urllib.request.ProxyHandler({}))`.
What is interesting is that **downloads through the proxy are fine, uploads through the proxy are broken** — the same proxy, two directions.

### 18.5 CI (added the same day, right after 1.5.0)

With the token swapped for one carrying the `workflow` scope, the workflow finally went up. The first version's
"the Android side compiles" was really just `Get-ChildItem android/src -Include *.java` counting files —
wrong source would go green too, which amounts to not checking at all. The real version split into two jobs:

| job | What it does |
|---|---|
| `静态检查` | `compileall host tools`; the version number consistent in **three** places (`host/version.py` / the installer's `#define AppVer` / `AndroidManifest.xml`'s `versionName`); no hard-coded development machine paths; PowerShell script syntax |
| `安卓端能编译` | `setup-java`(temurin 17) → download `build-tools_r34-windows.zip` and `platform-34-ext7_r03.zip` → `aapt2 compile` + `aapt2 link` to generate `R.java` → `javac -bootclasspath android.jar` compiling all 8 java files |

Two pits:

1. **`android-actions/setup-android@v3` is unusable.** Internally it runs `sdkmanager` itself,
   and on `windows-latest` it exits with code 1 straight away, hanging the whole job at the setup step
   (`Error: The process 'C:\Android\android-sdk\cmdline-tools\16.0\bin\sdkmanager.bat' failed with exit code 1`).
   Changed to downloading the two zips from `dl.google.com` myself — **the same pair of URLs that `tools/provision2.py` uses**,
   which also saves the 150 MB cmdline-tools and a whole round of licence interaction.
2. **`javac` must use `-bootclasspath android.jar`**, not `-cp`. With `-cp`, the JDK's own
   `java.*` gets in front of `android.jar`, and it compiles but with the wrong semantics; `android/build.py`'s
   `[3/7]` has always used `-bootclasspath`, and CI copied that.

Only after running the same steps locally (including really downloading and unzipping those two zips) did I push it: 8 java files, 33 `.class` files,
and run #3 on CI had both jobs green.

### 18.6 Still not done

* **No code signing.** This version moved "unsigned" from "the user does not know" to "it is written in the confirmation dialog",
  but whether to buy signing at all is the user's decision.
* The update flow **does no differential download**, it fetches 35 MB every time. At this update frequency it is not worth doing.
* CI only proves "it compiles"; it produces no APK and runs no tests — the 40-odd probe scripts in the repository
  all depend on real hardware (a cable, a real display driver) and cannot go on CI.

---

## 19. 1.5.1: the virtual screen vanishes out of thin air — the driver mode table blew the buffer

### 19.1 Bug report

> **User said**: "How did you change it? Casting now fails the moment I start."

The user's suspicion was well aimed: **it was me who changed it**, it just takes five layers to see who changed it.

```
虚拟显示器: \\.\DISPLAY21 (Virtual Display Driver)
  激活显示器 attach rc=-1
  attach 未生效，改用扩展拓扑 rc=31
  ...（重试 1..5）
RuntimeError: 虚拟显示器 \\.\DISPLAY21 无法激活（未上桌面）
```

`rc=-1` is `DISP_CHANGE_FAILED`, `rc=31` is `ERROR_GEN_FAILURE`. In Device Manager the driver is fine:
`Get-PnpDevice` reports `Status=OK`, `ProblemCode=0`, the DLL hash is identical to the one in `C:\VirtualDisplayDriver`, and there is only one package in the DriverStore. **The driver is installed perfectly; it just will not hand out a screen.**

### 19.2 Investigation: crossing off every answer I could think of

| Tried | Result |
| --- | --- |
| `git diff 95bb2d9 HEAD -- host` | `win_display.py` / `session.py` **byte-for-byte unchanged** since 1.4.0 |
| restarting the driver device (`Disable-PnpDevice` + `Enable-PnpDevice`) | modes still empty, and the `\\.\DISPLAYnn` numbers climb the whole way |
| rebooting the computer (the user had already rebooted twice that day) | the fault remained |
| suspecting NetEase UU Remote (网易UU远程) (it is also an IddCx virtual screen driver, and had left 10 ghost adapters) | **after killing every process + stopping the service + disabling the device and retrying, it failed in exactly the same way** → ruled out |
| HVCI / duplicate DriverStore packages / Windows update history / `setupapi.dev.log` | no lead |
| the control pipe `\\.\pipe\MTTVirtualDisplayPipe` | it `open`s, `write` blocks outright, and it will not answer questions |

What really opened things up was **the driver's own log**: the strings in `MttVDD.dll` contain `\Logs\log_*.txt`
and a whole set of `IddCx` diagnostic strings. Turning on `<logging>` in `vdd_settings.xml` and restarting the device produced 4980 lines of log:

```
[INFO] IDDCX Version: 0x1b01
[DEBUG] Max Monitors Supported: 1
[INFO] ASSIGNED GPU: NVIDIA GeForce RTX 5070 Laptop GPU (LUID: 4874866-0)
[INFO] Creating Monitor: 1
[DEBUG] Monitor created successfully.
[WARNING] Input buffer is too small for target modes.
[ERROR] Failed to report monitor arrival. Status: -1073741637
[DEBUG] Adapter initialization finished successfully.
```

`-1073741637` = `0xC00000BB` = `STATUS_NOT_SUPPORTED`.

**The adapter came up, the monitor object was created too, and only `IddCxMonitorArrival` was refused by the system.**
So there is not a single screen on the desktop → the mode table the driver hands over is empty → `ChangeDisplaySettingsEx` is bound to return `-1`.
And the `SetDisplayConfig` path with `rc=31` is no use either, because there is nothing to attach to.

### 19.3 Root cause: the mode table is a multiplication

The driver multiplies **every `<resolution>`** by **every `<g_refresh_rate>`**, laying the result out as one flat table for
Windows, and that table lives in a **fixed-size buffer**. `Input buffer is too small for target modes.`
is the sound of it overflowing.

Upstream [issue #254](https://github.com/VirtualDrivers/Virtual-Display-Driver/issues/254)
describes this landmine very clearly: **108 combinations fail, and deleting a few from the same file down to 96 succeeds**.

And **the `vdd_settings.xml` we ship with the installer is 22 resolutions × 5 refresh rates = 110 combinations**:

```
1280x720  1600x720  1600x900  1920x864  1920x1080 1920x1200 2000x1200 2160x1080
2280x1080 2340x1080 2400x1080 2412x1080 2560x1440 2560x1600 2700x1224 2712x1220
2800x1260 3040x1440 3120x1440 3200x1440 3200x2136 3840x2160
```

Every one of these is a phone/tablet panel size — for the "follow the phone's resolution" feature I stuffed in the long and short edges of all sorts of models,
aiming for "whatever phone the user turns up with, it will line up exactly". **Every single entry had a reason, and nobody ever did the addition.**

**Why did it blow up on 10-12 when 10-11 was perfectly fine?**
The driver reads this file only **when the device starts**. The successful cast on 10-11 at 23:48 used the old table loaded at
the 10-10 boot; the file had grown to 22 entries at 10-11 13:57 and had been lying there without taking effect.
**The reboot at 10-12 12:48 read it in, and from then on every single cast ran into the wall at `IddCxMonitorArrival`.**

### 19.4 The fix

1. **`vdd_settings.xml` cut from 22 entries to 11 × 5 = 55 combinations**
   (`tools/vdd/vdd25/VirtualDisplayDriver/vdd_settings.xml`). What was cut were duplicated near-sizes
   (things like `1600x720` / `1920x864` / `2000x1200`), keeping the batch that would really be used.
2. **`host/vdd.py` gained a hard cap.** New `MAX_TARGET_MODES = 90`, `target_mode_count()`,
   `mode_room()`. `add_resolution()` now first works out how many rows are left; when it is full it deletes **the entry we added ourselves**
   (marked with the `auto="1"` attribute), and **never touches a single entry the driver ships**.
   Better to be one size short than to hand over a table the driver cannot take again.
3. **`tools/vdd_install.ps1` now self-heals.** At install time it parses `vdd_settings.xml`, and if the computed count exceeds 90 it copies
   the bundled one back over it, and writes in the log 「原本 N 种组合，已重置」 (originally N combinations, reset).
   **On a machine that has already stepped on the mine, reinstalling the driver once fixes it.**

### 19.5 Verification

After the change I copied the new table back to `C:\VirtualDisplayDriver\` and restarted the device, then looked with the project's own `host/win_display.py`:

```
state   : ready
adapter : \\.\DISPLAY46 | Virtual Display Driver
monitors: 1
   \\.\DISPLAY1           2560x1600  primary=True
   \\.\DISPLAY46          1280x720   primary=False
best_mode -> (2560, 1600, 144, True)
```

**The mode table that had been empty for days was back**, and `best_mode` matched 2560x1600@144 as an exact hit (the fourth return value
`True` means exact). Then I synced `host/` to a mirror outside the workspace and ran the complete end-to-end:

```
virtual display: \\.\DISPLAY46
set_mode rc=0
live: ['\\.\DISPLAY1 2560x1600@300+0+0', '\\.\DISPLAY46 1920x1080@60+2560+0']
ddagrab 输出: #0=2560x1600, #1=1920x1080
手机已连接: 127.0.0.1:49687
已发送握手 1920x1080@60
10-12 21:27:14.602 I DroidExtend: perf fps=60 mbps=2.14 latency_avg_ms=10 latency_max_ms=42
FINAL capture: frames=1604 bytes=7488821 fps=56.3
```

Casting fully recovered, 60 fps, average latency 10 ms.

### 19.6 Lessons

* **"I generated the file" does not mean "the file is right".** Not one character of this configuration was written by the user,
  every one of the 22 resolutions had a reason, and nobody ever did the addition.
* **Treat a multiplication as a multiplication.** `resolutions` and `g_refresh_rate` are two parallel lists in the XML,
  which reads like "either/or" but is actually "multiply them all together". Two `for`s in a config file are two `for`s.
* **There is no hint at all on the failure path.** `attach rc=-1` says only "it did not succeed", not "why".
  The real reason was buried in the driver's own log, and that log **is off by default**.
  When chasing "the driver is installed but does not do anything" problems, go and find the log switch inside the driver package first.
* **The climbing driver number** (`DISPLAY20 → 21 → 32 → 34 → 46`) is not the cause, it is a symptom:
  every failed restart takes up a new display device slot. Do not follow that thread.
* **When the user says "how did you change it", lay out the changes and compare first.** This time I proved `win_display.py` /
  `session.py` were byte-for-byte untouched before I dared look into the driver configuration; otherwise it is very easy to get stuck in the files you changed most recently and never get out.

---

## 20. 1.6.0: fourteen things that were promised

### 20.1 The request

> **User said**: "当前其他编码选项根本不可用" ("none of the other encoding options work at all")

This release did not add features so much as **finish the things I had already said yes to**.
The user listed fourteen items in one go, and **eight of them were real bugs** (the last one was
only found after the installer was built, during a smoke test of the packaged exe).
They are worth writing down because they share one property: **the feature was "there",
but it had never once been verified.**

### 20.2 Item by item

| # | Request | Outcome |
| --- | --- | --- |
| ① | Privacy terms on both ends, agreed on first run | new `host/privacy.py` / `android/…/Terms.java`; the stored value is a version string, so editing the terms asks again |
| ② | Native animation on both ends | new `host/anim.py` / `android/…/Anim.java`; respects the system "turn animations off" setting |
| ③ | Tray + autostart + a real exit | new `host/tray.py` (pure ctypes) / `host/autostart.py` |
| ④ | Android background-survival guidance | new `android/…/KeepAlive.java`; 13 vendor autostart pages |
| ⑤ | Publish all the source | already satisfied |
| ⑥ | Finish the updater | changelog shipped with the app, download folder is yours, installer deleted after install |
| ⑦ | Touch offset, and touch dying after a window closes | **real bugs, two of them** (see 20.3) |
| ⑧ | Tell Windows when the phone sleeps or is killed | new `NOTICE` record in the input protocol |
| ⑨ | Emoji in the README and the logs | already satisfied (measured 76 / 79 / 87 / 90 characters) |
| ⑩ | Virtual screen active at boot without the app, and custom positions | **real bug** (see 20.3) |
| ⑪ | More codecs, with detection and fallback | **real bugs, three of them** (see 20.3) |
| ⑫ | Stutter diagnosis, a note about GPU usage, and resolution not switching | **real bug** (see 20.3) |
| ⑬ | English/Chinese in the installer and in the app itself | three places: installer, Windows app, Android app |
| ⑭ | adb port usage check and driver install | new "Diagnose connection problems…" in the Tools menu |

### 20.3 The real bugs

**1. Every codec other than H.264 produced garbage.**
The end of `command()` in `capture.py` **hard-coded** `-f h264`. Pick any other encoder and
the stream was still unpacked as H.264. The fix picks the muxer by codec:
`{avc:"h264", hevc:"hevc", av01:"obu"}`.
**AV1 must be `obu`, not `av1`** — `-f av1` fails outright with `pipe:: Invalid argument`,
because that muxer does not accept a pipe.

**2. HEVC NAL headers are two bytes; the splitter only knew about one.**
`split_access_units()` read the NAL type as `buf[pos+3] & 0x1F` (the H.264 layout), so it
never found a VCL unit in HEVC. `_NAL_SPECS` now states the header length per codec.

**3. AV1's `-profile:v main` made NVENC refuse to initialise.**
The error was `Error initializing output stream: ... maybe incorrect parameters such as
bit_rate, rate, width or height` — **a message that mentions neither the profile nor the codec.**
AV1 has no `main` profile, so the only fix is not to pass `-profile:v` at all.

**4. Vertical scrolling had never worked.**
`z` in the input protocol was a `uint16` while the phone packed `dx | (dy << 16)` into it,
and the host read dy with `z >> 16` — **shifting a 16-bit value right by 16 is always zero.**
The record went from `<BHHH>` (7 bytes) to `<BHHI>` (9 bytes). Both ends ship together,
so there is no compatibility problem.

**5. Touch died after closing the foreground window: the left button stayed down.**
When the window is destroyed or the phone disconnects while a finger is down, the UP event
never arrives, Windows is left inside a modal drag loop with the left button held, and every
later injection is swallowed — **the stream keeps running, the screen simply does not respond.**
Three belts: `Injector.release()`, a 30-second watchdog, and a release on disconnect and exit.

**6. Detach was not persisted, so the virtual screen came back at every boot.**
Windows keeps display configuration under
`HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers\Connectivity`,
and **`Recent` is what the topology is rebuilt from at boot**. Attach went through
`ChangeDisplaySettingsEx` + `CDS_UPDATEREGISTRY` (persistent); detach went through
`SetDisplayConfig` **without `SDC_SAVE_TO_DATABASE`** — **asymmetric, so attach survived the
reboot and detach did not.** Measured `Recent` round trip:
`BOE0D550_26_07E8_EC` → `…+MTT1337518463207_1C_07E8_C2` → back to the original value.

**7. Changing the resolution did nothing.**
`apply_display()` **was only ever called once, when you pressed "Start"**; nothing called
`wd.set_mode()` afterwards — the setting was saved, but nobody told Windows to switch.
There is now a `retarget()` with a 600 ms debounce, four attempts, and success judged by
**the mode that actually took effect**.

**8. A `settings.json` with a byte-order mark threw every setting away, silently.**
This one was not on the request list — it turned up in the **smoke test of the packaged
installer**, after everything else was green. The test wrote a settings file containing
`"language": "en"`; the app came up with a Chinese title bar. `settings.load()` opened the
file with `encoding="utf-8"`, and both PowerShell's `Set-Content -Encoding UTF8` and
**Notepad's "UTF-8" option** start the file with `EF BB BF`. `json.load` raises on a BOM,
the blanket `except Exception` reads that as "corrupt file", and so **the whole settings
file is dropped and every value falls back to its default, without one word in the log.**
A user can absolutely hit this: that file lives in the folder we invite people to look at.
The fix is two characters — `encoding="utf-8-sig"`, which eats the BOM and behaves
identically on files that do not have one. The "remembered data folder" pointer read in
`paths.py` got the same treatment, because a stray leading `\ufeff` turns the remembered
path into one that does not exist. `tools\tmp\settings_bom_test.py` (23 checks) guards it.

### 20.4 Two near-misses

* **NetEase UU Remote.** Ten of the 21 display outputs on this machine belong to
  `Root\GameViewerIddDriver`. The old `find_virtual_display()` sorted by "attached and has a
  monitor", so **as soon as UU was running its adapter outranked ours** and was treated as
  "our virtual screen". It is now decided by driver identity (`OUR_IDD` / `FOREIGN_IDD`),
  and only `MttVDD` counts.
* **What `probe_encoder()` is actually for.** Before this I "knew" that all 11 encoders
  ffmpeg listed would work. Running one real frame showed every `*_qsv` failing
  (`Error creating a MFX session: -9`; there is no Intel GPU here) and `av1_amf` unsupported
  (the Radeon 610M iGPU cannot encode AV1). **`ffmpeg -encoders` tells you what was compiled
  into the build, not what this machine can run.**

### 20.5 Verification

Seventeen regression scripts under `tools/tmp/`, roughly 620 assertions, all green.
A few worth calling out:

```
encoder_matrix.py       7 usable encoders, all PASS (encode a real frame, then split it and count units)
notice_test.py          37/37  including direct proof that dy was always 0 under the old layout
db_roundtrip_test.py     4/4   attach → detach leaves Recent back at the clean value
retarget_test.py        32/32  including the "nothing changed, so do not touch Windows" idempotence check
log_keep_test.py        23/23  log colours survive the window rebuild on a language switch
settings_bom_test.py    23/23  a BOM in settings.json no longer discards every setting
```

After packaging I read the PYZ archive inside `DroidExtend.exe` directly and confirmed that
`i18n`/`anim`/`tray`/`privacy`/`autostart`/`changelog` and the other 14 modules really are
frozen in there — **"the build succeeded" and "the thing is inside" are two different claims.**

And then I **actually launched the packaged exe**: wrote an English settings file *with* a
BOM, started it, and the title bar read `DroidExtend — Android Extended Screen   v1.6.0`
with the log landing in the data folder as expected. That is how bug 8 was found — **skip
that step and it ships**, because not one of the seventeen scripts ever writes a file with
a BOM.

### 20.6 Lessons

* **"The option exists" is not "the option works".** The encoder dropdown lists 11 names and
  7 of them run. Any capability that *looks* supported needs a path that **actually exercises it**.
* **Hard-coded assumptions live a long time.** That `-f h264` was probably correct when it was
  written — H.264 was the only choice then. It survived until the day a second encoder appeared,
  and it failed **with no hint at all** (a green screen).
* **The width of a protocol field is part of the interface.** Moving `z` from 16 to 32 bits
  required both ends at once; and `z >> 16` on a uint16 was **an assumption that was never true**.
* **Check symmetry in pairs.** Persisting on attach and not on detach — each function looks
  fine alone, and only together do they read as "one direction works, the other does not".
  When hunting this class of bug, ask: **does this operation have an inverse, and does the
  inverse do the same set of things?**
* **A wrong test is more common than wrong code.** In this release I wrote more bad assertions
  (assuming a startup log line count, comparing captions instead of values, treating
  `[x for x in after] == [x for x in after]` as a check) than I found product bugs.
  **Suspect the test before you suspect the code** — but once you have suspected it, make the
  assertion correct and stronger, do not delete it.
* **All tests green does not mean it runs.** Seventeen scripts passed without a single failure
  and bug 8 was still sitting there — because **not one of them writes a file with a BOM**,
  which is exactly what an ordinary user does. The packaged thing has to be **actually started
  once**. That step was worth more than writing three more test scripts.

## 21. 1.6.1: touch — measure the numbers first

### 21.1 The request

> **User said** (m03654): "I have the device plugged in right now, you can connect directly.
> One thing: the touch logic on the Android side is still wrong. Can you look at how other
> remote-desktop apps handle touch? And why is the touch latency *larger* than the video
> latency?"

Two asks: make the **gestures** behave like everyone else's remote-desktop client, and
**explain why touch lags more than video**. The second one is answerable on the spot
(21.2); the first is the actual work.

The device was genuinely attached this time: a Xiaomi 24091RPADC (muyu), Android 16,
`adb shell wm size` reporting `2136x3200`, density 440 / override 352, `adb devices`
name `d1de99c4`. **If you have real hardware, measure on it** — every number in this
section came off that phone rather than out of an argument.

### 21.2 Why touch latency must exceed video latency

This one needs no measurement, because it is an identity. The path from finger to eye
has two segments:

```
finger ──①touch link──> PC injection ──②capture→encode→transport→decode→display──> eye
```

The `latency_avg_ms` figure on the HUD measures **only segment ②** (it times a frame
from capture to display). Segment ① is added in front of it the moment you press, so

**touch latency = ① + ② > ② = video latency.**

That inequality **always holds** — it is not a bug. The only way to bring the two
together is to push ① down towards zero. So the real question is not "why is it bigger",
it is "how big is ①, and can it be smaller".

And ① has a nasty property: **the link is a queue, so producing faster than you can send
means a backlog**. Once a backlog starts, the latency does not merely stay high, it
**grows with time** — the longer you drag the further behind it falls, and it keeps
catching up after you let go. 21.3 shows the old version sitting exactly on that.

### 21.3 Measure first, then change

Three numbers, taken before touching anything, because "what is slow" has to be
settled first — otherwise you change a feeling, not a problem.

**① The tunnel: the bottleneck is "one write", not bandwidth.**
`tools/tmp/tunnel_bench.py` pushed 600 nine-byte records into 27184:

| How it was sent | Arrived | Gap |
|---|---|---|
| one `write` per record | **22 / 600** | 31.4 ms (≈32 a second) |
| all 600 in **one** `write` (5400 B) | **600 / 600** | 0.000 s (1 batch) |

5400 bytes in one go is nothing; one record at a time is thirty times slower.
**What is limited is the number of calls, not the throughput.**

**② PC-side injection: not remotely the bottleneck.**
`Injector.move()` two thousand times: median **0.457 ms** / p90 0.687 / p99 1.069 →
about **2187 a second**. The old phone sent 91 a second. **Twenty times of headroom.**

**③ The phone: three times over budget — and that is where the backlog came from.**
Listening on 27184 while `adb shell input swipe` played three identical drags:

```
before   122 records / 95 write batches / median batch 1 record / gaps ~11 ms  ≈ 91 a second
```

**91 a second against a capacity of ~32 a second — roughly three times over.**
①②③ all point the same way: **the thing to change is the phone's sending rhythm**,
not the bandwidth and not the injection.

One correction to a judgement of my own: `MOVE_INTERVAL_MS` used to be 28, justified by
"a tunnel write costs about 30 ms, so only 30 a second". That 30 came from a
`toybox nc` plus shell-loop benchmark — **polluted by mksh's own loop overhead**. The
real app sustains 91 writes a second; the tunnel was never capped at 30. The comment has
been rewritten from scratch.

### 21.4 Four changes

**① A dead zone on the phone (most likely the answer to "the touch logic is wrong").**
The old code sent an `ACTION_MOVE` the instant one arrived after `ACTION_DOWN`, and a real
finger **always** wobbles a few pixels as it lands — so **every "click" reached the PC as a
tiny drag**. The symptom is exactly: taps that miss, a tap that turns into a drag, a window
nudged slightly out of place. Now the touch has to clear
`ViewConfiguration.get(this).getScaledTouchSlop()` first: inside the dead zone **not one
MOVE is sent**; past it, it is promoted to a drag and the long-press timer is cancelled.
Measured with `swipe 700 1400 702 1403 80` (a 2–3 px wobble) → only `DOWN` and `UP`,
**zero MOVEs**.

**② Hold for half a second on the phone = right click.** Every other remote-desktop client
has it; this one did not. After 500 ms without moving it sends an `UP` (letting go of the
left button) and then an `RCLICK`. Measured: it fires **503 ms** after the `DOWN`. Moving
past the dead zone cancels it, so a drag never fires a right click by accident.

**③ Dragging on the phone is paced to the frame rate.** `MOVE_INTERVAL_MS = 16` — **16 ms
is exactly one frame at 60 fps**, which is the rate the capture side actually runs at. A
move faster than the next frame is one the person holding the phone cannot see, yet it
spends a whole write of the budget. Positions are idempotent, so intermediate ones are
dropped; **events (down / up / right click / scroll) are not paced at all.**

**④ Both ends keep only the newest position.** On the phone `pendingMove` holds a single
position (a newer one overwrites it); on the PC, `touch.py` grew `_apply_all()`, which
**injects only the last MOVE in a run**. MOVEs separated by a scroll are not merged and the
order is untouched.

### 21.5 The numbers afterwards

Same three `adb shell input swipe` drags:

| | records | write batches | gap | rate |
|---|---|---|---|---|
| before | 122 | **95** | ~11 ms | ≈91 a second |
| paced at 28 ms | 64 | 38 | ~33 ms | ≈30 a second |
| paced at **16 ms** (final) | — | — | **~21–24 ms** | **≈45 a second** |

**From 91 down to 45 a second (about half)**, and position updates went from one every
11 ms to one every 21–24 ms — **still denser than a frame**, so tracking did not get worse.
The budget the pacing freed up was spent on denser positions rather than saved.

Real-device gesture re-test (`tools/tmp/gesture_test.py`, run twice, both clean):

```
tap          600 1200                -> ['DOWN', 'UP']
long press   900 1500 held 900 ms    -> ['DOWN', 'UP', 'RCLICK']   (fired at 503 ms)
drag         500 1000 -> 1200 1800   -> ['DOWN'] + 27 MOVEs + ['UP']
jitter tap   700 1400 -> 702 1403    -> ['DOWN', 'UP']             (zero MOVEs)
```

**`input write slow` never appeared once** in the log — the socket write was never slow,
so what this release saves is **calls**, which lines up exactly with 21.3①.

### 21.6 Found along the way: filling the screen eats both edges

Stumbled on while checking coordinates. It may not be the same complaint, but it really
does make things unclickable.

"**Fill the screen (crop the extra edges)**" **defaults to on**. When it is on, the picture
is scaled up to fill the screen and the excess is cropped:

```
landscape window 3200 x 2136 (ratio 1.498)
desktop is 16:9 — filling computes 2136 * 16/9 = 3797
3797 - 3200 = 597, so 298.5 px is cropped from each side
```

The cropped part **never receives touch**: the reachable horizontal range is only
**0.079 to 0.921**, meaning **about 7.9% is unreachable at the far left and far right of the
desktop**. On a 1920-wide desktop that is about 151 px on each side — the far left of the
Start menu, and the close button in the top-right of a maximised window, both live there.

**This is not a bug; it is what that option means** (its own name says "crop the extra
edges"). So the default was left alone — it is written up in `dist/notes-1.6.1.md`
instead: if you would rather click the edges than keep black bars, turn it off.

### 21.7 Verification

**18 regression scripts and about 650 assertions green under `tools/tmp/`, zero non-zero
exits.** Two are new:

```
touch_batch_test.py   23/23  20 MOVEs collapse to the newest, MOVEs split by a SCROLL are
                             not merged, signed scroll deltas, six event kinds in order,
                             a 9-byte record split 4/5/2 across recv() is glued back,
                             a disconnect releases, long press is strictly down/up/right_click
gesture_test.py              plays four gestures on real hardware and decodes the actual
                             byte stream (not a guess from reading the code)
```

`touch_batch_test.py` failed 22/23 on its first run, and the one failure was because
`_read()` **always** finishes by calling `release()` — which is what the *next* test
asserts. The assertion now strips the trailing `release`. **A wrong test again, not wrong
code.**

Bumping the version then broke two assertions in `update_extras_test.py`, and **both of
them had the newest version number hard-coded**: `vs[0] == "1.6.0"` and a search for
`v1.5.1` inside `all_text(limit=2)`. That kind of assertion fails once per release, so
rather than change 1.6.0 to 1.6.1 (which only postpones the mine) they were made
version-agnostic:

```python
check("the newest entry's version is the program's own version",
      vs[0] == ver.APP_VERSION, "%s vs %s" % (vs[0], ver.APP_VERSION))
top2 = cl.all_text("zh", limit=2)
check("all_text(limit=2) takes only the newest two (%s, %s, not %s)" % (vs[0], vs[1], vs[2]),
      ("v" + vs[0]) in top2 and ("v" + vs[1]) in top2 and ("v" + vs[2]) not in top2)
```

The first one is now **stronger** than what it replaced: it asserts that the changelog's
newest entry must equal the program's version, so **bumping the version without writing a
changelog entry** is caught immediately — the old one only said "1.6.0 is at the front".

Packaging was checked too: `classes.dex` was grepped directly for `pendingMove` /
`drainLocked` / `input write slow` / `getScaledTouchSlop`. The APK went from
`versionCode 10` to **11**, `versionName 1.6.1`.

### 21.8 Lessons

* **"Why is A bigger than B" is sometimes an identity, not a bug.** Touch latency exceeding
  video latency is structural; the way to answer it is to **split the path in two**, not to
  measure. The real work is "how big is ①".
* **Find the bottleneck before changing anything.** Three numbers (tunnel 32 a second,
  injection 2187 a second, phone 91 a second) leave no doubt about where to cut. Without
  them the tempting target is whatever you know best (PC injection) — and it had twenty
  times the headroom.
* **Benchmark scripts lie.** "The tunnel can only do 30 a second" was wrong for two
  releases because it was measured with `toybox nc` and a shell loop, so it was **measuring
  the shell**. Before writing a benchmark, ask what else is moving inside the measurement.
* **A queue's limit is not latency, it is that latency grows.** 91 against 32 is not "three
  times slower", it is "59 more piled up every second", so it **gets worse as you drag**.
  When production outruns the sender, think in backlog, not in multiples.
* **Idempotent things can be dropped; events cannot.** A position is "where it is now" and
  its intermediate values are disposable; a down or an up is "something happened" and not
  one may be lost. Pacing and merging apply only to the former.
* **When a user says "the logic is wrong", look for the thing that happens a little extra
  every single time.** Here it was: every click carried a tiny drag along with it. Not
  rare, not a corner case — **every operation slightly off**, which is exactly what "the
  logic is still wrong" describes.
* **"The option exists" and "the option is good" are two things.** Filling the screen does
  what it says, at the price of 7.9% unreachable on the left and right. Leave that out of
  the notes and the user just concludes the touch is inaccurate.
* **Never hard-code "the current version" in a test.** Bumping to 1.6.1 immediately broke two
  assertions in `update_extras_test.py`, both written as `== "1.6.0"`. **That kind of
  assertion fails once per release, and changing it to the new number only postpones the
  mine.** Compare against `version.APP_VERSION` instead — and the rewritten assertion is
  **stronger** than the old one: it now catches "bumped the version without writing a
  changelog entry".

---

## 22. 1.6.2: a double click that Windows refused to call one

### 22.1 The request

> **User said** (m04395): "更新的时候要可以直接安装在老目录 Windows端的隐私协议按钮直接放在协议最下面的了不然没拉伸窗口容易找不到同意按钮在哪 安卓端操作逻辑还是有点小问题 操作逻辑在安卓端设置里写明一下"

"安卓端操作逻辑还是有点小问题" was an open multiple-choice, so I listed the candidates and
he picked exactly one:

> **User said** (m04446): "单击/双击不准，双击打不开文件"

### 22.2 The four, side by side

| # | What was asked | Root cause | The change |
| --- | --- | --- | --- |
| ① | An update should go back into the old folder | `DisableDirPage=no` in `DroidExtend.iss`: an upgrade still forced the destination page, and its default was `{localappdata}\Programs\DroidExtend`, not where the program actually lives | `DisableDirPage=auto` plus `UsePreviousAppDir=yes` |
| ② | Put the agree button at the bottom of the terms | The two buttons sat in a `pack(side="bottom")` footer, and **a footer cannot be scrolled to** — a window that was never stretched simply cut them off | Buttons are `window_create`d *inside* the Text, so they scroll; the height is computed from the screen |
| ③ | A double click does not open a file | Windows' `SM_CXDOUBLECLK` is 4 × 4 pixels by default, and we released the tap wherever the finger had drifted to | The phone releases a tap where it went down; the host snaps the second press onto the first |
| ④ | Write the touch logic into the phone's settings | The settings panel never mentioned any gesture | A new "触摸操作" section: six gestures and three notes |

### 22.3 ③, which deserves its own section

**Windows decides whether two clicks are one double click using a set of constants that date
from the mouse era:** `SM_CXDOUBLECLK` / `SM_CYDOUBLECLK`. Both clicks have to land inside the
same rectangle, and **the default is 4 × 4 pixels**.

Pressing a mouse button twice on the same icon puts the two points 0–2 pixels apart, comfortably
inside the box. A fingertip cannot do that: tapping the same icon twice through glass, tens of
desktop pixels apart is normal. So Windows saw two unrelated single clicks and the file never
opened.

Our own code was making it worse: the release was sent as `nx(e.getX())`, the position *after*
the finger had drifted. The dead zone tolerates a wobble of `touchSlop` pixels without calling it
a drag, and that wobble was exactly enough to push the second click out of the four-pixel box.

So both ends were fixed:

* **The phone** (`android\src\com\droidextend\MainActivity.java`):

  ```java
  private float tapUpX(MotionEvent e) { return nx(dragging ? e.getX() : downX); }
  private float tapUpY(MotionEvent e) { return ny(dragging ? e.getY() : downY); }
  ```

  A tap releases where it went down; only a drag follows the finger. All three release sites
  (`ACTION_POINTER_DOWN`, `ACTION_UP`, `ACTION_CANCEL`) now use these.

* **The host** (`host\touch.py`):

  ```python
  DOUBLE_CLICK_SLOP = 0.03          # normalised; about 58 px on a 1920-wide desktop
  DOUBLE_CLICK_FALLBACK = 0.5       # when GetDoubleClickTime() cannot be read
  def double_click_time(): ...      # the system's window, clamped to 0.1..1.0 s
  ```

  `_down()` notices when the previous complete click is inside the time window and within the
  slop on both axes, records `_snap`, and **both the press and the release go out at the first
  click's coordinates**. A single click and a double click therefore land on exactly the same
  pixel, and the four-pixel box stops mattering.

  The rules that keep it from snapping things it should not: a `MOVE` after the press means this
  was a drag, so `_snap` is dropped; the wheel, a right click and a disconnect all clear the
  pairing.

### 22.4 I fell into the same pit twice while writing the tests

The first `double_click_test.py` had three failures, all of the form "a drag must not count as a
double click". The cause was **my own code**: `stats["double"] += 1` was in `_down()`, and **at
the moment of the press you do not yet know whether it will become a drag**. Moving the count to
`_up()` (where a surviving `_snap` proves the press stayed clean all the way) turned all three
green.

The other one was in `privacy_layout_test.py`: I looked for the agree button with
`"同意" in b.cget("text")`, but **「不同意，退出」 also contains 「同意」**, so `[0]` handed me the
*decline* button and "pressing agree returns True" failed. Matching on the absence of
「不同意」 fixed it.

### 22.5 Verification

**Twenty-two scripts, 672 assertions, all green, zero non-zero exits** (four of them new):

```
privacy_test                 32      privacy_layout_test (new)    23
notice_test                  37      double_click_test (new)      27
retarget_test                32      install_dir_test (new)       23
diagnose_test                21      log_toc_test (new)           19
touch_test                   25      tray_test / tray_app / gui   22 / 47 / 16
touch_batch_test             23      terms_test                   34
anim_test                    51      keepalive_test               46
i18n_app_test                83      update_extras_test           61
log_keep_test                23      db_roundtrip_test             4
settings_bom_test            23      orphan_cleanup_test           no leftover virtual displays
```

How each of the four new ones works is worth recording:

* `privacy_layout_test.py` opens a **real Tk window** and measures the layout, then **squeezes
  the window to 300 px on purpose** and confirms the buttons can still be scrolled into view
  with `txt.see()` — which is precisely the situation the user was in.
* `double_click_test.py` swaps `time.monotonic` for a clock of its own, so "half a second later"
  is a number instead of half a second of sleeping.
* `install_dir_test.py` replaces `subprocess.Popen` and **reads the batch file that would have
  run**, never launching an installer.
* `log_toc_test.py` recomputes the anchor every table-of-contents entry ought to have, using
  GitHub's own rules, and then goes looking for it in the body. It immediately caught two
  links that **had never worked at all** (sections 7 and 19). A broken anchor like that simply
  does nothing when you click it; nothing but running a check will ever find it.

**On the real device, `tools\tmp\gesture_test.py` → ALL OK** (with a new double-tap case):

```
tap        ['DOWN', 'UP']                                   OK
longpress  ['DOWN', 'UP', 'RCLICK']                         OK
drag       ['DOWN', 27×MOVE, 'UP']  (~22 ms apart)          OK
jitter     ['DOWN', 'UP']                                   OK
doubletap  ['DOWN', 'UP', 'DOWN', 'UP']                     OK
  tap 1    down=(18261,39886) up=(18261,39886)              OK
  tap 2    down=(18313,39978) up=(18313,39978)              OK
  pair gap the two presses are 125.6 ms apart, (52,92) < 1966 apart   OK
```

Those two "the release is where the press was" lines are the direct evidence of the phone half of
the fix: before it, that `up` landed wherever the finger had drifted to.

**Packaged smoke test**: `DroidExtend.exe` came up with the title
`DroidExtend — 安卓拓展屏   v1.6.2`. The PYZ inside the exe holds 167 modules with all 20 of ours
present; `touch` contains `DOUBLE_CLICK_SLOP` / `double_click_time` / `GetDoubleClickTime` /
`_snap`, `privacy` contains `_height` / `window_create`, and `update` contains `/DIR=`.

**End to end, over the real phone and a real virtual display** (`tools\touch_test.py`, which
needed a fix of its own: the virtual display only comes up after `extend_topology()`, and the
script assumed it was already attached, so capture died with
`Capture failed to start: 'NoneType' object is not subscriptable`). Once attached:

```
capture backend: ddagrab 1920x1080@60
tap   phone(1600,700)        -> cursor (3519,353)   display starts at x=2560, 1920 wide => nx=0.4995
swipe (320,900)->(1500,400)  -> 20 events, cursor (3469,202)
app log  action=0 at 1898.0,700.0   / action=1 at 1898.0,700.0   <- the release sat on the press
         action=0 at  618.0,900.0   / action=1 at 1798.0,400.0   <- a drag follows the finger
```

The last two lines are the phone half of the fix, seen from the far end of the whole chain. The
script read logcat with `text=True`, so Python decoded UTF-8 Chinese log lines with the console's
GBK code page, the first byte it could not map turned `stdout` into `None`, and `.splitlines()`
threw `AttributeError` — now `capture_output=True` plus `.decode("utf8", "replace")`.

### 22.6 Lessons

* **"This bug must be in our code" is an assumption that needs checking.** The double click that
  would not open a file came down to a four-pixel constant in Windows. The first question about a
  bug should be "who defined the rule".
* **I fell into the same pit twice: a tool reporting False does not mean the artefact lacks it.**
  Last time it was a constant walker that would not recurse into tuples; this time **function and
  global names live in `co_names`, not in `co_consts`**, so `DOUBLE_CLICK_SLOP` briefly read as
  False. Inspect both.
* **Watch for containment when matching strings in a test.** 「不同意」 contains 「同意」.
* **The console code page is not the right decoder for somebody else's UTF-8.** On a Chinese
  Windows, `subprocess.run(..., text=True)` decodes the phone's log as GBK; the first byte it
  cannot map turns `stdout` into `None`. Pass `encoding=` explicitly, or `decode(..., "replace")`.
* **Put a counter where the information is complete.** `stats["double"]` in the press handler was
  wrong, because at that moment you cannot know whether the press becomes a drag.

---

## 23. 1.6.3: two menus that both asked the wrong question

Shortly after 1.6.2 went out the owner raised two things, both bluntly, and both correctly:

> 为什么分辨率更改后还是没变化 你其他分辨率白做的吗
> (why does nothing change after I change the resolution — was all that other resolution work for nothing)

> 还是有部分视频编码不可用
> (some video encoders are still unavailable)

Both had been "fixed" in 1.6.0. The real problem is that **both times the program asked the wrong
question**: the resolution menu asked "what should the driver support?" (in fact, a hard-coded list),
and the encoder menu asked "what was this ffmpeg compiled with?". What the owner cares about is
**"does it work on this machine?"**.

### 23.1 Resolution: the driver only accepts its own table

The symptom: pick a size from the menu, click it, and nothing happens — no flicker, no size change, no
message.

Measure it first (`tools\tmp\res_change_probe.py`, with the virtual display really attached):

```
set_mode \\.\DISPLAY10 1920x1080@60  -> rc =  0  SUCCESSFUL   1920x1080@60
set_mode \\.\DISPLAY10 2280x1080@60  -> rc = -2  BADMODE      1920x1080@60   (unchanged)
set_mode \\.\DISPLAY10 1920x1080@75  -> rc = -2  BADMODE      1920x1080@60   (unchanged)
set_mode \\.\DISPLAY10 3200x2136@120 -> rc =  0  SUCCESSFUL   3200x2136@120
```

`-2` is `DISP_CHANGE_BADMODE`. Then look at what the driver actually has
(`tools\tmp\vdd_modes_probe.py` plus `wd.available_modes()`):

```
C:\VirtualDisplayDriver\vdd_settings.xml   11 <resolution> rows × 5 <g_refresh_rate> values
                                     ->    55 modes (MAX_TARGET_MODES = 90; room for 7 more rows)
sizes:  1280x720 1600x900 1920x1080 1920x1200 2160x1080 2400x1080
        2560x1440 2560x1600 3200x1440 3200x2136 3840x2160
rates:  30 60 90 120 144
```

Meanwhile `RESOLUTIONS` in `host\app.py` is **twenty-two hard-coded sizes**, **eleven** of which the
driver does not have at all:

```
1600x720   1920x864   2000x1200  2280x1080  2340x1080  2412x1080
2700x1224  2712x1220  2800x1260  3040x1440  3120x1440
```

`75` / `165` / `240` in `HZ_CHOICES` do not exist either. **Half the menu was dead**, and clicking it
only ever produced `-2`.

**Worse than the mismatch is the silence**, and this layer explains every symptom the owner saw:

1. the "the driver has no such mode" branch in `retarget()` **logged nothing at all**;
2. `_set_mode_retry()` retried a mode that could never work four times, `sleep(1.0)` + `sleep(0.5)`
   each — **about nine seconds of nothing**;
3. and it then called `_recover_capture("display mode changed")` anyway — **restarting ffmpeg**. The
   picture blinked and came back the same size, so it *looked* like something had been attempted.

**Four changes**:

* `vdd.declared_modes()` (`host\vdd.py`) reads the driver's own settings file and returns the
  `(sizes, rates)` it will build, or `(None, None)` when there is nothing to read.
* `app._load_resolutions()` (`host\app.py`) builds **both dropdowns** from that table, falling back to
  the static list only when there is no driver to ask (that list is now documented as a fallback and
  nothing more). A saved size the driver cannot produce is replaced with the nearest one it can, and
  the log says so. It runs in two places: `_build()`, right after the events are bound, and
  `_bg_done()` — because starting or retargeting may just have written a row into the driver's table.
* `session._resolve_mode(name, w, h, hz)` settles a request once and for all, returning
  `(w, h, hz, note, usable)`: an exact hit passes straight through; the same size at another rate takes
  the nearest rate and says so; a size that is not in the table at all is reported — "The driver cannot
  offer 2280x1080 (not in its mode table, and it could not be added). Available now: …" — and marked
  unusable.
* Unusable now means each caller does its own right thing: `retarget()` logs "显示模式未更改" (display
  mode unchanged) and returns `False`, **without touching the capture**; `apply_display()` (the
  Start-casting path) falls back to `wd.best_mode()` and gets a picture up — a picture at a nearby size
  beats insisting on an exact one. `_set_mode_retry()` also returns immediately on `-2`, so those nine
  seconds are gone.

The boundaries of `add_resolution()` are pinned down as well: when the driver's table still has room the
missing size is written into `vdd_settings.xml` marked `auto="1"`, and you are told to **reinstall the
driver (or reboot)** before it can be used. When the table is full, a row we added ourselves is
reclaimed — **rows that came with the driver are never touched**.

Measured on real hardware (`tools\tmp\mode_live_test.py`, 19 PASS / 0 FAIL):

```
apply_display 2280x1080  -> The driver cannot offer 2280x1080 (not in its mode table, and it could not
                            be added). Available now: 1280x720, 1600x900, 1920x1080, 1920x1200, 2160x1080, …
                            using 2160x1080@60                 -> SUCCESSFUL
retarget 2280x1080       -> Display mode unchanged             -> False, capture not restarted, size same
retarget 3200x2136@120   -> ChangeDisplaySettingsEx SUCCESSFUL -> True, capture restarted
retarget same mode again -> Mode unchanged, nothing to switch
retarget 3840x2160@75    -> 3840x2160 does not offer 75 Hz; using 60 Hz -> SUCCESSFUL
```

### 23.2 Encoders: the menu was asking "was it compiled in?"

The symptom: pick `h264_qsv` or `av1_amf` from the menu and it fails to connect, or quietly falls back to
a different encoder.

The numbers (`capture.probe_encoder()`, actually encoding a frame with each):

```
compiled into this ffmpeg: 11
  av1_amf  av1_nvenc  av1_qsv  h264_amf  h264_nvenc
  h264_qsv  hevc_amf  hevc_nvenc  hevc_qsv  libx264  libx265

survived a real frame: 7
  h264_nvenc  hevc_nvenc  av1_nvenc  h264_amf  hevc_amf  libx264  libx265

the four that died:
  h264_qsv  hevc_qsv  av1_qsv  -> Error creating a MFX session: -9   (no Intel GPU here)
  av1_amf                      -> the Radeon 610M has no AV1 encoder block
  libsvtav1                    -> not in this build at all
```

Root cause: `app._load_encoders()` used `list_hardware_encoders()`, which answers **"what was this
ffmpeg build compiled with?"**; `available_encoders()`, added in 1.6.0, answers **"what actually runs on
this machine?"**. The second one arrived and the first was never swapped out, so the menu kept listing
all eleven. The docstring in `host\capture.py` had already spelled out the difference and even said
"Use available_encoders() when the answer has to be trustworthy" — written down correctly, just never
wired up.

The fix: the menu is filled in two passes. The first is instant (the compiled list, so there is something
to choose the moment the window opens); the second **test-encodes a frame with each one in the
background** and rebuilds the menu from the survivors, naming the casualties in the log: "Not offered:
these cannot encode on this machine: h264_qsv, hevc_qsv, av1_qsv, av1_amf". If the current selection is
one of them it moves to a working encoder by itself, and a probe that fails entirely does not empty the
menu.

### 23.3 Found on the way: a test that had not passed since 1.6.0

Running the full suite is what revealed that `tools/tmp` holds **31** `*_test.py` files (33 by the time
this release was wrapped up), not the 22 that had been quoted for three releases. And of those 31, `tools\tmp\update_test.py` was dying on:

```
File "tools\tmp\update_test.py", line 111, in main
  body = u.WAIT_CMD.format(pid=4321, installer=..., flags=..., restart=...)
KeyError: 'cleanup'
```

`WAIT_CMD` in `host\update.py` gained a `{cleanup}` placeholder in 1.6.0 (the "delete the installer once
it is installed" requirement) and the test was never updated. So **that test failed every single run
since 1.6.0**, and the "twenty-two scripts, all green" quoted until now never included it — **nobody
counted with a tool**.

It is fixed, and it gained three assertions it should always have had: the installer delete must come
after `start "" /wait` (deleting it early is exactly how a half-applied update happens), the script's
own `del "%~f0"` must come last, and `delete_installer=False` must keep the download.

That script's section 6 really does download the 34.8 MB v1.5.1 installer (35–65 seconds), which is why
the suite looks stuck. It is not stuck; it is testing a real download and a real checksum.

### 23.4 And another test that could never have passed, from the day it was written

`tools\tmp\drag_test.py` came up red in every full run, while `gesture_test` — the same phone, doing a
real double tap / long press / drag / jitter — was green. The test itself was wrong, in two places:

* It hard-coded a 3200x2136 screen and a 60 px margin. The screen size happens to be right (that is the
  landscape `logicalFrame`), but the app centres the button with `m = dp(9) + dp(21)`, which at density
  352 is **66 px**, not 60 — a 7 px miss. Survivable, because the button's hit radius is
  `gearR * 1.35` ≈ 62 px, so the press still landed on it.
* The fatal one: it dragged **13 px**. `HudView.onTouchEvent` contains a deliberate anti-slip rule:

  ```java
  if (Math.abs(e.getX() - gearCx) + Math.abs(e.getY() - gearCy) > dp(9)) {
      moved = true;
  }
  ```

  A finger has to travel 9dp (19.8 px at density 352) before the gesture counts as a drag; anything
  shorter is routed to `onGear()`, which **opens the settings sheet**. 13 px never reaches 19.8 px, so
  that `input swipe` became a tap: the `drag_moved` screenshot jumped from 69650 bytes to 305951 bytes
  (the sheet was open) while the button did not move at all.

  And even if it had been classified as a drag it would not have helped: 13/3068 ≈ **0.004**, against
  an assertion asking for **> 0.1**.

So that assertion was **unreachable by construction** — it could only hold if the button snapped
somewhere on release, and this button does not snap. `tools\tmp\drag_probe.py` measured it directly on
the same button:

| swipe | fraction afterwards | movement |
| --- | --- | --- |
| 13 px | (0.14596808, 0.74855125) | **0.0000** |
| 700 px | (0.37389198, 0.74855125) | **0.2279** (expected 700/3068 = 0.2282) |

There was a third trap, the sneakiest one: the position was read with `gear=([-\d.]+),([-\d.]+)`, and
**Java prints small floats in scientific notation**. With the button parked against the top edge the log
reads:

```
10-16 23:52:07.437 I DroidExtend: main  启动 v1.6.3  fill=true hud=true log=false gear=0.99984306,2.8117592E-4
```

So the button is really at **(0.99984306, 0.00028117592)** — the top-right corner, which is exactly the
default `Prefs` hands back for `gearX`/`gearY` (1 and 0). That pattern captured `2.8117592`, so the test
computed `sy = 66 + 2.8117592 × 2004 ≈ 5701` — **a press 3565 px below the bottom of the screen** — and the
swipe touched nothing, after which the test reported "the button did not move".

The pattern now accepts an exponent, the parsed value is clamped into `[0, 1]`, and the drag direction is
chosen from whichever side the button has room on (it used to always drag up and to the right, which from
the top-right corner is nowhere). Same phone, same button:

```
gear at boot: (0.99984306, 0.00028117592)
drag (3134,67) -> (2434,417)
moved x 0.2291 (asked 0.2282), y 0.1754 (asked 0.1747)
gear restored to: (1.0, 0.0)
RESULT: PASS
```

**The product was right; the test was wrong.** The fix: read the screen size out of the `screencap` PNG
header, compute the margin from `wm density`, drag 700 px, assert the x and y fractions separately, and
put the button back afterwards. `drag_test.py` now reports `RESULT: PASS`.

### 23.5 The frozen-build check tripped over a third version of the same trap

The build is `--onedir`: the program's own modules sit zlib-compressed inside the PYZ embedded in the
exe, so **a raw byte scan of `DroidExtend.exe` finds nothing at all**. The first `frozen_check_test.py`
did exactly that and reported all seven names MISSING, including the version string `1.6.3`.

Switching to `PyInstaller.archive.readers.CArchiveReader` tripped a second one: the embedded archive is
named **`PYZ.pyz`**, not the older `PYZ-00.pyz`; and the **entry module `app` is not in the PYZ at all** —
it is a separate CArchive record (`marshal.loads(car.extract("app"))`). Looking only in the PYZ reports
`No entry named 'app' found`, which reads as "the UI code was not packaged" when it is right there.

This is the third time the same shape of mistake has appeared in this project: **a tool reporting False
does not mean the artefact lacks it**. Constants live in `co_consts`, function and global names live in
`co_names`, and modules live in both the PYZ and the CArchive.

### 23.6 Verification

| Script | Covers | Result |
| --- | --- | --- |
| `tools\tmp\mode_menu_test.py` | mode-table parsing / real Tk menu / fallback / `_resolve_mode` / BADMODE short-circuit / encoder menu / real `add_resolution` into a temp XML / source guards | **76 PASS** |
| `tools\tmp\mode_live_test.py` | real virtual display, real `ChangeDisplaySettingsEx` | **19 PASS** |
| `tools\tmp\frozen_check_test.py` | opens the PYZ and CArchive inside the exe, looks for the new code | **0 MISSING** |

Full regression: **33 scripts, all green, zero non-zero exits**. The ones that run against a real phone
or a real artefact:

| Script | What it runs | Result |
| --- | --- | --- |
| `tools\tmp\gesture_test.py` | real phone: double tap / long press / drag / jitter | ALL OK |
| `tools\tmp\drag_test.py` | real phone: drag the settings button away, restart, see if it stayed | RESULT: PASS |
| `tools\tmp\touch_test.py` | real phone: touch coordinates, point by point | 25 PASS / 0 FAIL |
| `tools\tmp\double_click_test.py` | real phone: double tap | 27 PASS / 0 FAIL |
| `tools\tmp\touch_batch_test.py` | real phone: batched touches | 23 passed, 0 failed |
| `tools\tmp\update_test.py` | a real 34.8 MB v1.5.1 installer download and a real checksum | RESULT: PASS |

Assertion counts for the rest: `i18n_app_test` 83, `mode_menu_test` 76, `update_extras_test` 62,
`anim_test` 51, `tray_app_test` 47, `keepalive_test` 46, `notice_test` 37, `terms_test` 34,
`privacy_test` 32, `install_dir_test` 23, `log_keep_test` 23, `privacy_layout_test` 23,
`settings_bom_test` 23, `tray_test` 22, `log_toc_test` 19 (table-of-contents anchors),
`mode_live_test` 19, `tray_gui_test` 16, `frozen_check_test` 0 MISSING; and `datadir_test`,
`db_roundtrip_test`, `diagnose_test`, `mode_budget_test`, `orphan_cleanup_test`, `retarget_test`,
`toast_test`, `upd_ui_test`, `vdd_path_test` each PASS.

### 23.7 Lessons

* **"I already fixed that" and "it works for the user" are two different claims.** 1.6.0 added
  `available_encoders()` and never changed the call site, so nothing was fixed; and the acceptance test
  called `available_encoders()` directly — **the test exercised the new function, not the function the
  UI actually used**. For this class of bug, walk the *call sites*, do not just admire the new function.
* **Count with a tool before quoting a number.** "Twenty-two scripts, all green" was repeated for three
  releases; there are 31 (33 once this release's new ones are added), and one of them was red. The 22 was
  presumably counted by hand once.
* **Silent failure costs more than failure.** A failed mode change needed one log line. Because it was
  silent the owner saw "nothing happened" and reasonably concluded the whole feature was fake. A failure
  path owes three things: **what happened, why, and what still works**.
* **Doing *something* after a failure makes it look like a different bug.** The mode never changed, yet
  ffmpeg was restarted; the picture blinked, so the symptom went from "no reaction" to "flickers and
  stays the same", which is harder to trace. Now `retarget()` returns without touching the capture.
* **A tool reporting False does not mean the artefact lacks it** (third time). Confirming with a second
  reading is cheaper than concluding.
* **When a test has been red for a long time, suspect the test first.** `drag_test` asserted that a
  13 px drag moves the button by more than 0.1 — but the app deliberately requires 9dp (19.8 px) before
  it calls a gesture a drag, and the button does not snap, so **no input could ever satisfy that
  assertion**. A permanently red case desensitises everyone to the whole regression report, which is
  worse than not having it; either make it pass or delete it and say why.

---

## 24. 1.6.4: making the virtual display actually disappear

This release does one thing: **when it is not in use, can this screen be treated as not existing?**

### 24.1 The request

> 虚拟屏能不能关闭干净 就是不要让这块屏幕开机自己激活和远程软件使用

Two things that turn out to be one: as long as Windows can still enumerate this screen, remote-desktop
software (ToDesk, Sunlogin, UU Remote and friends) will offer it as a selectable monitor; and it is there
every boot because **the driver creates it by itself**, whether or not our program runs.

`C:\VirtualDisplayDriver\vdd_settings.xml` says:

```xml
<monitors>
    <count>1</count>
</monitors>
```

Every time the device node starts (`ROOT\DISPLAY\0001`, hardware id `Root\MttVDD`, service `WUDFRd`) it
creates that many monitors. So there are exactly two levers: **change the count**, or **do not let the
device start**.

### 24.2 The obvious attempt, which does not work: a count of 0

Measured with `tools\tmp\count_zero_probe.py` and `count_monitor_probe.py`, each of which really restarts
the device node:

```
count = 1  ->  adapter \\.\DISPLAY22   55 modes   monitor Status OK       desktop: laptop only
count = 0  ->  adapter \\.\DISPLAY23   55 modes   monitor Status OK       desktop: laptop only
count = 0  ->  adapter \\.\DISPLAY24   55 modes   monitor Status OK       desktop: laptop only
```

**Nothing changed.** A string inside `MttVDD.dll` explains why:

```
Loading singular monitor (Monitor Count is not valid)
```

The driver treats 0 as invalid and falls back to one monitor. That road is closed.

(One side effect worth recording: **every device-node restart increments the display number** -- 22, 23,
24. This release restarted it a few dozen times, so `\\.\DISPLAY` is now in the twenties. Harmless.)

### 24.3 What does work: disabling the device node

A disabled device never starts the driver, so there is no screen. Measured with
`tools\tmp\device_toggle_probe.py`:

```
before: vdd.status()["state"] = ready     adapter \\.\DISPLAY26   monitor Status OK
after : vdd.status()["state"] = node      adapter None            monitor Status Unknown
again : vdd.status()["state"] = ready     adapter \\.\DISPLAY27   monitor Status OK
```

All three things line up: **the adapter no longer enumerates** (so remote software has nothing to find),
**the monitor is no longer OK**, and **the desktop only ever holds the laptop**. Windows also persists the
disabled state, so it is **still disabled after a reboot** -- which is exactly the property "do not show up
at boot" needs.

The price: `Disable-PnpDevice` / `Enable-PnpDevice` need administrator rights, so **one UAC prompt per
switch**.

### 24.4 Two modes, defaulting to switching it off

Because that price is real, 1.6.4 makes it a choice, defaulting to the thorough one:

| Mode | What it does | Price |
|---|---|---|
| **Switch it off for real** (default) | disables the device node when the stream stops, enables it when a stream starts | one UAC prompt each way |
| Just take it off the desktop | only detaches it from the desktop; the driver is left alone | none, but the screen still exists |

Leftovers from a crash have a fallback too: if the last session died mid-stream, the device node is still
enabled, and the next launch notices it, takes it off the desktop straight away (no approval needed) and
says so in the log; the menu item "Shut the virtual display down for real (asks once)" does the thorough
job on demand. In the other direction -- device disabled, and now you want to stream -- starting a stream
enables it, and if that fails it says so plainly instead of quietly showing a black screen.

### 24.5 A check that had never once worked, fixed in passing

The function that answers "is the virtual display's device node still there" filtered like this:

```python
"Get-PnpDevice ... | Where-Object { $_.InstanceId -like 'ROOT\\MTTVDD*' }"
```

But a root-enumerated device's instance id is **`ROOT\DISPLAY\0001`**; nothing of the shape
`ROOT\MTTVDD\...` exists. So **in this project's entire history it never found anything** and always
returned an empty list.

Nothing broke, because `status()` prefers the adapter and the adapter is visible while it exists -- until
this release needed the state "device disabled, node still present". It now matches on the **hardware id**
(`DEVPKEY_Device_HardwareIds` containing `MttVDD`) and immediately returns
`ROOT\DISPLAY\0001 | OK | Virtual Display Driver`.

### 24.6 Verification

New `tools/tmp/vdd_off_test.py`, **61 assertions green**:

```
device_enabled            5   OK / Unknown / Error / no node / empty status
set_enabled               6   UAC cancelled (two different messages) / no node / odd exit code /
                              adapter never came back / still there after disabling / success
_TOGGLE_PS                6   has Enable/Disable-PnpDevice, matches by hardware id,
                              re-checks the end state, exits 4 when there is no node
settings                  5   vdd_off_mode in EXTRA, defaults to deep, is a str, comes back from load()
Session, three methods   13   default mode / lowercasing / no-op when already on / enable when disabled /
                              enable in "desktop" mode too / RuntimeError that is bilingual /
                              "desktop" never touches the driver / does not re-disable / disables and
                              logs / a failure logs without raising
source guards            13   session.py no longer calls set_count_and_restart, app.py has the menu item
                              and the radio buttons, the choice is saved and reaches the stream config,
                              "deep" detaches a leftover at startup
```

The same change also passed `py_compile` and a real-machine `import`, and `vdd.device_enabled()` returns
`True` on the actual machine.

Full regression: **35 scripts green, zero non-zero exits** (33 when 1.6.3 shipped; this release adds
`vdd_off_test.py`, plus one script that started life as a 1.6.3 probe).

### 24.7 Lessons

1. **Measure the "obvious" approach before shipping it.** Writing `count=0` took ten minutes and proving
   it useless took another ten. Skipping the proof would have shipped a setting that does nothing at
   all -- exactly the bug 1.6.3 had just fixed in two menus.
2. **A `PermissionError` in a traceback is not necessarily from the code under test.** That
   `restart_device -> False` took several rounds; the failing line was not in `vdd.py` at all but in a
   throwaway diagnostic script reading a log. Check which file the line number belongs to first.
3. **Judge success by the observable outcome -- not the exit code, and not the log.** Exit code 0 only
   means the script finished. What this release finally checks is "the adapter enumerates, or it does
   not".
4. **A branch that never once worked is harder to notice than a bug that errors.** `InstanceId -like
   'ROOT\MTTVDD*'` returned an empty list forever, and nobody raises an alarm about "found nothing".
5. **A hardcoded number in a test quietly changes what the test is testing.** `update_test.py` had the
   expected APK size as `194390`, a leftover from v1.4 / v1.5. Once the APK grew to 210774, sections 3
   and 4 -- "does the MZ check reject an apk" and "is a hash mismatch thrown away" -- both failed at the
   *size* check first, with an error message that had nothing to do with what they were written to test.
   The size now comes out of the release body, falling back to the local build.
6. **The `adb reverse` tunnel is not persistent.** It lives in the adb daemon; restart the daemon and
   the tunnel is silently gone (this time it was restarted to clear an `adb.log` with bad permissions),
   after which `gesture_test` receives `(nothing)` for every gesture and looks like broken touch handling
   on the phone. `gesture_test.py` now creates the tunnel itself instead of relying on whatever a
   previous session happened to leave behind.
7. **When a real-device test goes red, check the phone's state first.** With the screen locked no
   gesture reaches the app, and `drag_test` and `gesture_test` both go red saying "nothing happened".
   One `adb shell dumpsys window | grep isKeyguardShowing` is faster than reading two test files -- and
   this time the answer really was "the code is fine, the phone was locked".

---

## 25. 1.6.4, part two: the in-app update never once installed

"Redo check-for-updates, it still cannot install directly -- test it yourself, is it a logic
problem?" By the time the user said that they had tried it more than once. The whole chain looked
right: it finds the new version, downloads it, passes four checks, asks "close DroidExtend now,
install v1.6.4 and start it again?" -- you press yes, the window closes, and nothing happens.

### 25.1 Actually run that step

Rather than reading code and guessing, reproduce it (`tools/tmp/update_apply_probe.py`): stand up a
dummy process to impersonate the running DroidExtend, call `upd.run_installer()`, wait four seconds to
confirm it has not jumped the gun, kill the dummy, then wait for `DroidExtend.exe` to appear in the
target folder.

Result: **180 seconds and `DroidExtend.exe` never appeared**, while `apply_update.cmd` was still
sitting there -- and if it had finished, its last line (`del "%~f0"`) would have removed it.

### 25.2 The script itself is fine

Change that script's PID to one that does not exist and run it in the foreground with `cmd /c`:
**installed in four seconds**, the target folder got the complete install, and the script deleted
itself.

So the problem is not what the script does, it is **how it gets launched**.

### 25.3 Instrument the script

`tools/tmp/batch_trace_probe.py` swaps `WAIT_CMD` for an identical script that appends a line to
`batch.log` at every step, still launched exactly the way `run_installer` launches it. Result:

```
[1] script started
[2] loop checking 26696      <- this line only; no further [2], and no [3] at all
```

The dummy had been dead for 90 seconds; the batch had neither moved on nor exited, and `cmd.exe` was
still hanging there.

### 25.4 The culprit is the launch flag

`tools/tmp/ps_launch_probe.py` swaps the creationflags one at a time, and every variant has its child
write a marker file:

```
DETACHED|NEW_GROUP (what it used)  started=18672 exit=0  mark=False   <- exit 0, did nothing
CREATE_NO_WINDOW                   started=5316  exit=0  mark=True
CREATE_NEW_CONSOLE                 started=3392  exit=0  mark=True
no flags                           started=648   exit=0  mark=True
cmd with DETACHED|NEW_GROUP        started=2280  exit=0  mark=False
```

**Under `DETACHED_PROCESS` the child exits 0 without executing a single line**, for cmd and for
powershell alike. The old code picked DETACHED_PROCESS precisely so the child would outlive the parent
process -- and what it got was a child that lives but does nothing, which is harder to find than one
that never starts, because `Popen` returns success.

### 25.5 The fix: PowerShell, and different flags

Both halves changed:

1. The batch file became a PowerShell script, `apply_update.ps1` (written as **utf-8-sig**; PowerShell
   5.1 reads a BOM-less file as ANSI, which mangles non-ASCII paths). One process does everything:
   wait for the PID to vanish (with a 180 s deadline) -> `Start-Process -Wait` the installer ->
   relaunch the new build -> delete the installer if that setting is on -> delete itself. **Every step
   goes into `apply_update.log`**, so a failure is no longer silent.
2. Launch with `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, and try adding
   `CREATE_BREAKAWAY_FROM_JOB` first (when the job does not allow breakaway that raises, and we fall
   back to the version without it).

### 25.6 Verified

Same probe again: the moment the dummy is killed, `DroidExtend.exe` shows up in the target folder --
**1.5 seconds** -- and `apply_update.log` says `app is gone; running the installer`. The script deletes
itself, `delete_installer=False` keeps the download, and no processes are left behind.

### 25.7 Lessons

1. **Exit code 0 does not mean any work was done.** Here `Popen` succeeded, the child exited 0, not a
   single word was printed -- and not one thing happened.
2. **"It works when I run it by hand" is a hint to look at how it is launched**, not proof the script
   is right. The same script installs in four seconds in the foreground and does nothing under a
   different launch.
3. **A feature that never once worked is quieter than one that errors.** This update chain downloads,
   verifies and asks for confirmation, all of it "working" -- only the last step never happens, which
   is why it survived so long.

---

## 26. 1.6.5: the update folder moved on its own, and uninstalling left the app running

This round of feedback was two sentences:

> Why did the update folder change again? And the uninstaller does not close the background
> process, so it never uninstalls completely.
> (`User said (m06361)`)

Three unrelated problems behind them: one of my own tests had hijacked the data folder of an
installed copy, the updater did not honour the folder the user picked, and the installer never
checked whether a process was still running.

### 26.1 First: the data folder was hijacked by a test

Where settings live is decided by a pointer file, in this order:
`%DROIDEXTEND_HOME%` -> `%LOCALAPPDATA%\DroidExtend\data_dir.txt` -> the program folder.

On the affected machine the pointer had been written to
`C:\Users\15530\AppData\Local\Temp\de_bom_target_c9uhsnz0` -- a leftover test sandbox.

The culprit was `settings_bom_test.py`: to prove that a settings file with a BOM does not lose
every setting, it called `paths.set_data_dir()`, and that function wrote the **real** pointer. So
for a whole day the installed copy read its settings out of `%TEMP%`.

The damage shows up later: the moment Windows cleans TEMP (or the folder is deleted), the pointer
stops resolving, the app falls back to the program folder, and **every setting drops to its
default** -- which is what "the update folder changed again" looks like from the outside. The
machine still had 22 `de_bom_target_*` folders, one per full regression run.

### 26.2 Second: the update script did not follow the folder

Even with the settings intact, changing the update folder only half worked: the downloaded
installer went to the chosen folder, but `run_installer()` called `update_dir()` **with no
argument**, so `apply_update.ps1` and `apply_update.log` always landed in
`%TEMP%\droidextend_update`.

### 26.3 Third: the uninstaller did not close the program

`DroidExtend.iss` says `CloseApplications=no`, and the file has no `[Code]`, no `AppMutex` and no
`[UninstallRun]`. So uninstalling while the app sits in the tray leaves it running, the files
cannot be deleted, and the uninstall "succeeds" with leftovers. `CloseApplications` would not have
helped either -- it sends WM_CLOSE, and "close the window" in this program means "hide to tray".

The evidence is direct: after that uninstall, `D:\DroidExtend` contained only a `tools\` folder
(`adb.exe`, two DLLs, an `adb.log`) -- exactly the files the running program held open.

### 26.4 The fixes

| Problem | Fix |
|---|---|
| hijacked pointer | `data_dir()` now **ignores a remembered folder that sits under `%TEMP%`**; the pointer itself is written under `%DROIDEXTEND_HOME%` (tests all set it, so they can no longer touch the real one); added `forget_data_dir()` / `remembered_data_dir()` |
| update script location | `run_installer(..., work_dir="")`; `host/app.py` passes the `update_dir` setting in |
| uninstall leaves the app running | the `.iss` gained a `[Code]` section: `PrepareToInstall` and `CurUninstallStepChanged(usUninstall)` both run `taskkill /F /IM DroidExtend.exe /T`, retrying up to 8 times; exit code 128 (not running) counts as success |

### 26.5 A false alarm fixed along the way

The live log contained `could not shut it down completely (the device was disabled but the
virtual display is still there after 20 s)`. The device node really was disabled and the adapter
row was merely still enumerable -- it will not start at the next boot, so that is a success. Now
`set_enabled(False)` reports success in that case and says the screen will drop out of the list
once the system lets go of it.

### 26.6 Verification

New `tools/tmp/datadir_guard_test.py`, **31 assertions all green**:

```
pointer location   3  %DROIDEXTEND_HOME% wins / default LOCALAPPDATA\DroidExtend / data_dir.txt
%TEMP% guard       7  containment, a %TEMP% pointer is ignored and ROOT is used instead
switch and forget  7  set_data_dir takes effect at once, pointer in the sandbox, forget is idempotent
update script      8  script and log go to the chosen folder, UTF-8 BOM, powershell really starts,
                      empty work_dir falls back to %TEMP%
installer          6  [Code], PrepareToInstall, CurUninstallStepChanged, taskkill
```

Full regression, 36 scripts: **31 green, 5 red**. All five are environmental -- the phone was not
connected (`drag_test` / `gesture_test` cannot even get a screenshot), and at that moment the virtual
screen happened to be the primary display with the laptop panel not attached, while `db_roundtrip_test` /
`mode_live_test` / `orphan_cleanup_test` all assume "laptop only" as their baseline. None of them goes
through anything this release changed (`paths` / `update` / `.iss`): `_ensure_vdd_on` is a no-op while
the device is enabled, and the attach / detach / registry path those three tests exercise was not touched.

### 26.7 Lessons

1. **A test that writes to a real location costs more than a failing test.** Nobody looked at
   those 22 sandboxes, and the price was paid by a real machine -- with the symptom (settings
   changing by themselves) a day away from the cause (running a regression).
2. **"Settings change by themselves" -- suspect the folder and the pointer before suspecting an
   overwrite.** `_form_values()` starts with `dict(self.settings)`, so unknown keys cannot be
   dropped; find out which folder the program is actually reading first.
3. **A successful uninstall is not a clean one.** What was left was exactly the files the running
   program held open, and one `[Code]` section with a single `taskkill` would have avoided it.

---

## 27. 1.6.6: all data in the install folder, one instance only

### 27.1 The request

> I am on a remote session, never mind the display state. I just want the installer to not go wrong,
> the uninstaller to remove the program completely, and as much as possible every piece of data kept
> inside the install folder I picked -- not scattered all over the place.

And later:

> Uninstalling still throws an error. Also make it so only one copy of the program can run at a time,
> because double-clicking the exe just starts another copy, and another tray icon.

### 27.2 How many places the data was in

Apart from `DroidExtend.exe` and `_internal\` in the install folder, the program wrote to seven other
locations:

| Location | What | Since |
|---|---|---|
| `%LOCALAPPDATA%\DroidExtend\data_dir.txt` | the data-folder pointer | 1.4, "change data folder" |
| `<data folder>\settings.json` | settings | always |
| `<data folder>\DroidExtend.log` | the log | always |
| `%TEMP%\droidextend_update\` | the update script and its log (the installer went to the folder you picked) | 1.5 |
| `%TEMP%\droidextend_vdd_*.ps1` | scratch PowerShell for elevation | 1.6.4 |
| `%TEMP%\droidextend_vdd_elevate.ps1` | the same | 1.6.0 |
| `<data folder>\vdd_install.log`, `vdd_off.log` | driver install / switch results | 1.6.0 |

For "all data in the install folder" to be true, the first six had to change.

### 27.3 Pulling it all into the install folder

1. **The pointer is gone.** `data_dir()` now has two answers: `%DROIDEXTEND_HOME%` (used by tests, and
   the only way to override) or the install folder. The 1.6.5 "remembered folder" was deleted -- it
   existed precisely so data could live elsewhere, which is the opposite of what this release wants.
   `set_data_dir()` still exists but only sets an in-process variable and writes no file.
2. **The "File -> Change data folder..." menu item is removed.** Leaving a button that moves the
   settings away is leaving a door for the data to scatter again.
3. **The update folder applies to the whole update.** `update_dir()` defaults to
   `<data folder>\updates` instead of `%TEMP%\droidextend_update`, and `run_installer(work_dir=...)`
   makes the script and its log follow the setting -- previously only the installer obeyed it.
4. **The elevation scripts moved to** `<data folder>\tmp\`.
5. **The uninstaller names every file.** `[UninstallDelete]` lists `settings.json`, `DroidExtend.log`,
   `vdd_install.log`, `vdd_off.log`, `data_dir.txt`, the folders `updates`, `tmp`, `tools`, and the
   `%LOCALAPPDATA%\DroidExtend` an older version left in the profile. Inno only removes what it
   installed, so anything unnamed survives.
6. **Old data is brought back.** `paths.migrate_old_data_dir()` reads the old pointer at startup,
   copies `settings.json` and the logs into the install folder (never overwriting), then deletes the
   pointer; `paths.clean_old_temp()` removes leftover update and elevation scripts from `%TEMP%`.

### 27.4 Why an uninstall still left files behind

What survived was `tools\platform-tools` -- `adb.exe`, `AdbWinApi.dll`, `AdbWinUsbApi.dll` and
`adb.log`. Not because Inno could not delete them, but because **they belonged to adb's daemon**: once
the program has started adb, `adb.exe` spawns a **server process** that outlives the window and keeps
those files open. Inno sees open files, skips them, and reports at the end that some elements could not
be removed.

Three changes together:

* `[Code]` gained `KillAppAdb()`, which kills only an adb **inside the install folder**
  (`Where-Object { $_.Path -like '{app}\*' }`) and leaves the one Android Studio installed alone;
  it runs before both install and uninstall.
* The program cleans up after itself: `_quit_now()` calls `adb.kill_server()` after removing the port
  forwards.
* `[UninstallDelete]` now covers the whole `{app}\tools` folder.

### 27.5 Why an uninstall still showed an error box

The uninstall had actually finished. The box said:

```
Runtime error (at 7:852):
Unknown constant "userprofile".
```

The last step of the uninstaller starts a detached PowerShell sweep of leftover folders, and one of its
three targets was `ExpandConstant('{userprofile}\.droidextend')`. **Inno Setup has no `{userprofile}`
constant**, and `ExpandConstant` raises a runtime error on a constant it does not know -- so the
uninstall crashed on its very last statement.

The fix is `GetEnv('USERPROFILE')`. Every `{...}` constant in the `.iss` was then listed and checked;
only `{app}`, `{autodesktop}`, `{group}`, `{localappdata}`, `{sys}` and `{uninstallexe}` remain, all
valid.

Separately, the sweep used to sleep two seconds and then start deleting, while the uninstaller itself
(`unins000.exe`) was still living in `{app}` -- competing for the same files also produces "some
elements could not be removed". It now **waits for `unins000` to disappear** (up to 30 s) before
touching anything, and only deletes leftovers.

A sibling folder named `<install folder>-backup` is **deliberately left alone**: that name is very
likely the user's own backup, and an uninstaller has no business deleting it just because it happens to
sit next to the program.

### 27.6 Only one instance

Double-clicking the exe again used to start a second process and a second tray icon, and two instances
could fight over the same virtual screen. A named mutex fixes it:

* `CreateMutexW(None, 1, "Local\\DroidExtend.single")`; `GetLastError() == 183`
  (`ERROR_ALREADY_EXISTS`) means an instance is already there. The `Local\` prefix keeps it inside the
  current logon session.
* The handle is held in a module-level variable so it cannot be collected; a second call to
  `_already_running()` returns True immediately instead of creating another handle (otherwise closing
  one instance leaves the other's handle alive and later checks misread it).
* The check sits in `main()` before `App()` is built, so losing the race exits **without flashing a
  window**.
* Before exiting it raises the existing window (`FindWindowW` + `ShowWindow(SW_RESTORE)` +
  `SetForegroundWindow`); if that instance is hidden in the tray with no window to find, it says
  "the program is already running -- double-click its tray icon".

### 27.7 Tests

| Script | Result | Covers |
|---|---|---|
| `datadir_guard_test.py` | **45 PASS** | one folder / no pointer / migration / `%TEMP%` cleanup / update folder / applier placement / `.iss` guards |
| `single_instance_test.py` | **14 PASS** | twice in one process / cross-process / released and re-taken / raising the window / source guards |
| `uninstall_sweep_test.py` | **11 PASS** | rebuilds the `[Code]` PowerShell and really runs it against fake folders / missing targets are silent / **no unknown Inno constants** |
| `settings_bom_test.py` | **24 PASS** | a legacy pointer is ignored, `_old_pointer_path()` location |
| `update_extras_test.py` | **65 PASS** | the default update folder is inside the install folder, not in `%TEMP%` |

One environment note: on this machine the Inno installer **of every version** exits 1 within 0.1 s
("Setup could not create the directory `%TEMP%\is-XXXX.tmp`. Error 5: access denied"), while the same
account can `mkdir` that very directory from PowerShell. That is this machine, not the package
(1.6.3 / 1.6.4 / 1.6.5 all behave identically), so the install-then-uninstall round trip could not be
completed here; the uninstall logic was verified by the script-level tests above plus the user's own
machine.

### 27.8 Lessons

1. **An error box and a failed operation are two different things.** Every uninstall really did
   finish; the code crashed on its last statement, which makes it look like the uninstall failed.
   Read what the box says before deciding which layer broke.
2. **Do not write constant names from memory.** `{userprofile}` reads as self-evident and simply does
   not exist in Inno; the `ExpandConstant` message already gave the answer.
3. **"The uninstall is not clean" -- suspect a child process that is still alive.** A daemon the
   program started itself (adb server) does not exit with the window, and Inno will not delete the
   files it holds.
4. **Fewer places to put data beats more flexibility.** A pointer, a movable data folder and update
   scripts in `%TEMP%` were each "convenient" at the time, and each one ended up as "the settings
   changed on their own" or "there is still junk after an uninstall".

---

## 28. 1.6.7: why the latency creeps up

### 28.1 One sentence

> It is stable now, but why does the latency shown on the Android side keep growing after a while?
> From 3 ms to 60 ms, though I can barely feel it.

"I can barely feel it" is the clue: the picture did not get slower, only the number did. A number that
climbs while the picture stays identical is measuring something that has nothing to do with the
encoder or the link.

### 28.2 Where that number comes from

The phone reports a latency figure every two seconds, computed like this:

```java
// measured once at handshake, and never again
clockSkewUs = SystemClock.elapsedRealtimeNanos() / 1000 - hostNowUs;

// for every frame
long latUs = SystemClock.elapsedRealtimeNanos() / 1000 - clockSkewUs - stampUs;
```

`latUs` is "what time the phone thinks it is", minus "the offset between the two machines", minus
"when this frame was stamped on the PC". Averaged over a two-second window and reported as
`latency_avg_ms`.

The formula assumes **the two monotonic clocks tick at the same rate**. They do not: consumer crystals
are off by roughly ±50 ppm, which is 50 seconds per million -- 0.05 ms/s, or **3 ms per minute**. With
the offset measured once at handshake, that drift is added to every single frame. After an hour the
number is 180 ms larger and the picture has not changed at all.

### 28.3 Both ends were wrong

* **The PC side used the wall clock**: `host/capture.py` had
  `stamp = int(time.time() * 1000000)`, and so did `_now_us()` in `host/server.py`. The Windows time
  service slews the wall clock and occasionally steps it -- a step is "the latency suddenly changed".
  The whole chain wants `time.monotonic()`.
* **The phone treated a one-off measurement as permanent**: the offset was computed at handshake and
  never corrected, so all of the drift went straight into the reported figure.

Together: slow steady growth (drift) plus occasional jumps (time sync).

### 28.4 The fix

**(a) A monotonic clock on the PC.** New `host/clock.py`:

```python
def now_us():
    """Microseconds of a clock that only moves forward.

    time.time() is the wall clock: the Windows time service slews it, and
    occasionally steps it.  Frame timestamps must not jump.
    """
    return int(time.monotonic() * 1000000)
```

`host/capture.py` and `host/server.py` both use it now.

**(b) The phone corrects the drift itself.** Each window also keeps `latMin`, the fastest frame in it
-- the fastest frame is the one where the pipeline was empty, so a change in *that* is drift. The
reference `latFloorUs` is the first window's minimum, and after that:

```java
if (Math.abs(excess) > DRIFT_CAP_US)      latFloorUs = latMin;   // really faster/slower: new reference
else if (Math.abs(excess) > DRIFT_MIN_US) clockSkewUs += excess; // drift: take it out
```

Two attempts failed before this one, and both are worth remembering:

* **A ring buffer of the last 30 window minima**: the correction pinned the reading to the floor, the
  ring could never rise, and **a genuine increase in latency was erased permanently**; on top of that
  `latSum -= excess*latCount` subtracted twice and the readings went negative.
* **A reference that followed `latMin`**: the correction only removed the increment above a moving
  reference, never the accumulated residual -- after an hour it still read 92 ms, exactly half the
  true drift, which looks like "half fixed".

The key property of the final version: **the reference must not follow the signal**. It moves only
when the level genuinely changes (more than `DRIFT_CAP_US`); ordinary drift is absorbed by
`clockSkewUs`.

### 28.5 Verification

`tools/tmp/latency_clock_test.py`, **28 assertions all green**. The core ones re-implement the Java
algorithm in Python and feed it 60 minutes of synthetic data (60 fps, a report every 2 s):

```
+50 ppm   uncorrected 3.0 -> 182.9 ms   corrected 3.05 -> 3.15
-50 ppm                                 corrected 2.85 -> 2.95
+200 ppm  (worst-case crystal)          corrected 3.20 -> 3.60
-200 ppm                                corrected 2.40 -> 2.80
0 ppm                                   corrected 3.00 -> 3.00
true latency 3 -> 12 ms                 reads 12.25 within two minutes
```

The thresholds (`DRIFT_MIN_US` / `DRIFT_CAP_US`) are **read out of `StreamClient.java` with a regex**,
so the test and the code cannot drift apart -- 1.6.6 had just been burned by exactly that
(`update_test.py` had the APK size hardcoded).

### 28.6 Lessons

1. **"It gets worse the longer you run it" is almost always drift.** Two clocks, one accumulator and an
   offset measured once will produce that curve every time; suspect the clock before the encoder.
2. **Drift must be corrected against a reference that does not move.** If the reference follows the
   signal, the correction only ever removes the increment -- it looks fixed and is half fixed.
3. **Get the algorithm right in three minutes of Python before touching Java.** The ring-buffer
   version cost several compile cycles; running the same data through Python takes seconds.
4. **The test and the code must not each keep their own constant.** Read it out of the source so a
   change to the code changes the test instead of quietly invalidating it.

---

*This log was written by an AI coding agent. All of the project's code was written by it as well.*
