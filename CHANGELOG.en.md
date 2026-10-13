# DroidExtend development log

> **Let me be clear first: the code in this project was written by AI.**
>
> All of the source code (4480 lines of Python on the Windows side, 1701 lines of Java on the
> Android side, 3261 lines of Python in tools and test scripts) was written from scratch by an
> **AI coding agent** inside **DeepSeek Harness**, using the **deepseek-flash** model, and
> delivered as an installable 1.5.1 within a few sessions on **2026-10-11**.
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
| [19](#19-151-the-virtual-screen-vanishes--the-driver-mode-table-blew-the-buffer) | 1.5.1: virtual screen vanishing / mode table cap | 110 combinations → 55, the driver finally does its job after being installed |

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

*This log was written by an AI coding agent. All of the project's code was written by it as well.*

