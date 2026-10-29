<p align="center">
  <a href="HUAWEI.md">简体中文</a> · <b>English</b>
</p>

# Huawei / HarmonyOS devices: troubleshooting

**Let me be upfront about where this document comes from.** On 2026-10-29 a user reported three
symptoms on a HarmonyOS 4.x Huawei device (a MatePad Pro): (1) the device is not recognised at all;
(2) mirror mode does nothing and no matching output is found; (3) in extend mode the picture works
but the mouse pointer is invisible. **We do not own a Huawei device, so most of what follows comes
from Huawei's official documentation and public troubleshooting threads, not from measurements on
that machine** — anything unverified is called out as such. Mirror mode was measured working on our
own reference phone (Xiaomi 16 / Android 16), so the mirroring code itself is fine; the problem lives
on the Huawei / HarmonyOS side, in the environment and in the OS differences. Version 1.8.3 only adds
diagnostics plus this document, and changes no risky behaviour.

## 1. Try these three things, in this order

### 1.1 Turn on "Allow ADB debugging when charging only", set USB to "File transfer"

* In Developer options, enable **USB debugging** and Huawei's extra switch
  **「仅充电时允许 ADB 调试」** (Allow ADB debugging when charging only).
* After plugging in the cable, **pull down the notification shade** and change the USB preference
  from "Charge only" to "File transfer / MTP".
* Why: while it sits on "Charge only", adb cannot see the device at all — the debugging switch
  being on does not help.

Source: [Huawei official support document](https://consumer.huawei.com/uz/support/content/ru-ru00407281/)

### 1.2 Kill hdc: adb and Huawei's hdc fight over the same port

If you have **DevEco Studio**, **Huawei PC Manager**, **HiSuite**, or any tool carrying an hdc service installed,
hdc grabs the device first and `adb devices` becomes an **empty list** (note: not `unauthorized`,
just no device at all).

* Quit DevEco Studio;
* Quit Huawei PC Manager (tray icon, right-click → Quit);
* End `hdc.exe` in Task Manager;
* Then unplug and replug the cable, and look at `adb devices` again.

adb and hdc both use port **8710** — that is the port they collide on.

Sources: [Huawei developer FAQ](https://developer.huawei.com/consumer/cn/doc/harmonyos-faqs-V14/faqs-performance-analysis-kit-32-0000001934976910-V14),
[Huawei developer forum](https://developer.huawei.com/consumer/cn/forum/topic/0204155318543671213)

### 1.3 Turn off Pure mode

HarmonyOS **Pure mode** blocks APKs that do not come from the app store. When the phone-side app
will not install (`adb install` fails), this is almost always why: go to
`设置 → 系统和更新 → 纯净模式` (Settings → System & updates → Pure mode) and turn it off, or
allow this one installation.

Source: [Huawei developer forum](https://developer.huawei.com/consumer/cn/forum/topic/0214202917493369228)

## 2. Match your symptom

| Symptom | Most likely cause | How to confirm | What to do |
|---|---|---|---|
| `adb devices` lists nothing at all, the program says no phone was detected | USB preference is still "Charge only", or 「仅充电时允许 ADB 调试」 (Allow ADB debugging when charging only) is off | Check the USB preference in the phone's notification shade; check that switch in Developer options | Do step 1.1: turn the switch on, set "File transfer", replug |
| Device Manager showed the phone briefly while plugging in, but `adb devices` is empty | hdc took the device (DevEco Studio / Huawei PC Manager / HiSuite / any hdc service) | With the cable plugged in, look for `hdc.exe` in Task Manager; or check who holds port 8710 in the program's diagnostic log | End `hdc.exe`, quit those two programs, replug (step 1.2) |
| `adb devices` shows `unauthorized` | Nobody tapped the "Allow USB debugging" prompt on the phone | The state column literally reads `unauthorized` | Tap Allow and tick "Always allow"; if the prompt never appears, **Revoke USB debugging authorizations** in Developer options first, then replug |
| The device is recognised but the phone-side app will not install, `adb install` fails | HarmonyOS Pure mode blocking an APK from outside the app store | Did the phone show a Pure mode prompt during the install? | Turn Pure mode off, or allow this installation (step 1.3) |
| Mirror mode: "Start casting" does nothing and no matching output is found | HarmonyOS implements MediaCodec / SurfaceView differently from AOSP (see Known limitations) | Switch encoder, stop and restart, see whether the symptom changes | Use `h264`; click `停止` (Stop) then `开始投屏` (Start casting); if it still fails, send the diagnostic log |
| Extend mode works but the mouse pointer is invisible | The cursor is composited into the picture by ffmpeg; either the option is off or Huawei differs | Is 「显示鼠标指针」 (Show mouse pointer) ticked in the settings? | Tick it and retry; if it still does not show → send the diagnostic log, we cannot locate this one yet |
| Changing the resolution mid-session in mirror / extend mode leaves the picture unchanged | HarmonyOS's SurfaceView is stricter about resolution changes while it is running | After changing the resolution, is the picture still the old resolution? | Click `停止` (Stop) then `开始投屏` (Start casting) so the driver restarts |
| The phone shows black or corrupted frames (there is a picture, but it is broken) | MediaCodec decoder differences; Kirin decoders need vendor adaptation | Does it recover with `h264`? | Use `h264` instead of `hevc` and retry |

## 3. What the program can tell you

The menu **`工具 → 诊断连接问题…`** (Tools → Diagnose connection problems…) prints all of the
following into the log in one go:

* the adb version;
* every device it recognised;
* when not a single device was recognised, the whole raw `adb devices -l` output (**when it is
  empty, this block is the whole story** — an empty list and `unauthorized` are two different
  problems);
* hdc processes / a port 8710 conflict;
* whether the device was identified as a Huawei one;
* who holds ports 27183 / 27184 — those carry the video and touch tunnels, and another program
  squatting on them is exactly the "connects but no picture" case.

On 1.8.3, when a Huawei / HarmonyOS device is detected the diagnostic also prints the hints from
steps 1.1, 1.2 and 1.3 directly.

If it still does not work, **paste that whole log into an
[issue](https://github.com/Blank2007/DroidExtend/issues)**.

## 4. Known limitations (honestly: these are not solved)

### 4.1 Decoding: HarmonyOS's Android MediaCodec is not a perfect match

Decoders on the Kirin platform need vendor adaptation, and we cannot promise that `h264` / `hevc`
decode on every model. **The symptom is a black or corrupted picture on the phone** (not "no
picture at all"). Switch to `h264` (not `hevc`) and try again — that tells you whether the decoder
is the problem.

### 4.2 Resolution changes: HarmonyOS's SurfaceView is stricter

If the picture does not change after you change the resolution in mirror / extend mode, **click
`停止` (Stop) and then `开始投屏` (Start casting)** so the driver restarts in between.

### 4.3 Mouse pointer: invisible in extend mode

The program composites the cursor into the picture during the ffmpeg capture; the switch is
「显示鼠标指针」 (Show mouse pointer) in the settings — check that it is ticked first. If it is
ticked and the pointer still does not show, **we cannot reproduce or locate this one on a Huawei
device yet**; please click `工具 → 诊断连接问题…` and send the log.

### 4.4 HarmonyOS 4.x works, pure HarmonyOS does not

HarmonyOS 4.x is still an AOSP-compatible layer, so both APKs and adb work — 4.x is theoretically
within our supported range and belongs to the "can be diagnosed" category. Pure HarmonyOS NEXT /
6.0 switched to hdc and is not APK-compatible, so it is **out of scope**.

Source: [itying forum](https://bbs.itying.com/topic/6a115059c4b04d0047fcada4)

**That means "worth trying on 4.x", not "HarmonyOS is fully supported".** We do not have the device,
so we have no business claiming the latter.

## 5. What to include when it still does not work

Put these five things in the issue, otherwise everyone is guessing:

1. **Model and HarmonyOS version** — `设置 → 关于手机 / 平板` (Settings → About phone / tablet); copy
   the whole version line, e.g. `HarmonyOS 4.2.0`.
2. **The raw `adb devices -l` output** — copied line by line; if it is empty, say "it's empty".
3. **The whole log from `工具 → 诊断连接问题…`** — paste it in full, do not excerpt.
4. **Which mode** — mirror or extend; and in extend mode, is it "no picture", "picture but no
   pointer", or "pointer but the picture is wrong"?
5. **The settings in use** — encoder (`h264` / `hevc`), resolution, refresh rate, bitrate; cable
   straight into the PC or through a dock / adapter.

## 6. Sources

* Huawei official support (ADB debugging switch):
  <https://consumer.huawei.com/uz/support/content/ru-ru00407281/>
* Huawei developer FAQ (hdc / adb ports):
  <https://developer.huawei.com/consumer/cn/doc/harmonyos-faqs-V14/faqs-performance-analysis-kit-32-0000001934976910-V14>
* Huawei developer forum (hdc taking the device):
  <https://developer.huawei.com/consumer/cn/forum/topic/0204155318543671213>
* Huawei developer forum (Pure mode blocking APKs):
  <https://developer.huawei.com/consumer/cn/forum/topic/0214202917493369228>
* itying forum (HarmonyOS 4.x vs the AOSP layer / NEXT):
  <https://bbs.itying.com/topic/6a115059c4b04d0047fcada4>
