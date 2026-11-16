// DroidCapture.exe -- DXGI Desktop Duplication -> D3D11 VideoProcessor -> NVENC.
//
// Why this exists.  The old path was ffmpeg (ddagrab -> nvenc), and ffmpeg is a
// general-purpose tool: measured on this machine it handed a 120 fps request
// only ~76 fps of packets, 15.2 ms apart, with internal queue spikes of up to
// 75 ms.  The capture-to-packet path here measures 3-10 ms and keeps a steady
// cadence, because it is one thread doing exactly one thing:
//
//   AcquireNextFrame -> VideoProcessorBlt (BGRA->NV12, optional scale) ->
//   nvEncEncodePicture -> LockBitstream -> write() to stdout.
//
// The stdout protocol is deliberately identical to what ffmpeg produced
// ("-f h264 -"): raw Annex-B H.264 access units, no framing of our own.  The
// Python side splits them with split_access_units() and does not care which
// process produced the bytes, so the phone app is untouched.
//
// Exit codes: 0 stopped, 2 startup failure, 3 display mode changed (the caller
// should re-read the screen rectangle and start a fresh process), 4 device
// removed.

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_6.h>
#include <cstdio>
#include <cstdarg>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <algorithm>
#include <timeapi.h>
#include <io.h>
#include <fcntl.h>
#include "nvEncodeAPI.h"

// ---------------------------------------------------------------------------
// logging: stderr only.  stdout carries the bitstream and nothing else.
// ---------------------------------------------------------------------------
static void say(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    std::vfprintf(stderr, fmt, ap);
    va_end(ap);
    std::fputc('\n', stderr);
    std::fflush(stderr);
}

static const char* nv_str(NVENCSTATUS s) {
    switch (s) {
        case NV_ENC_SUCCESS: return "SUCCESS";
        case NV_ENC_ERR_NO_ENCODE_DEVICE: return "NO_ENCODE_DEVICE";
        case NV_ENC_ERR_UNSUPPORTED_DEVICE: return "UNSUPPORTED_DEVICE";
        case NV_ENC_ERR_INVALID_ENCODERDEVICE: return "INVALID_ENCODERDEVICE";
        case NV_ENC_ERR_INVALID_DEVICE: return "INVALID_DEVICE";
        case NV_ENC_ERR_DEVICE_NOT_EXIST: return "DEVICE_NOT_EXIST";
        case NV_ENC_ERR_INVALID_PTR: return "INVALID_PTR";
        case NV_ENC_ERR_INVALID_PARAM: return "INVALID_PARAM";
        case NV_ENC_ERR_UNSUPPORTED_PARAM: return "UNSUPPORTED_PARAM";
        case NV_ENC_ERR_INVALID_VERSION: return "INVALID_VERSION";
        case NV_ENC_ERR_NOT_ENOUGH_BUFFER: return "NOT_ENOUGH_BUFFER";
        case NV_ENC_ERR_ENCODER_BUSY: return "ENCODER_BUSY";
        case NV_ENC_ERR_GENERIC: return "GENERIC";
        default: return "?";
    }
}
#define NV_OK(x) do { NVENCSTATUS _s = (x); if (_s != NV_ENC_SUCCESS) { \
    say("FATAL %s -> %s (%d)", #x, nv_str(_s), (int)_s); std::exit(2); } } while (0)

static void hr_ok(const char* what, HRESULT hr, int code = 2) {
    if (SUCCEEDED(hr)) return;
    say("FATAL %s hr=0x%08X", what, (unsigned)hr);
    std::exit(code);
}

// ---------------------------------------------------------------------------
// monotonic clock
// ---------------------------------------------------------------------------
static LARGE_INTEGER g_freq;
static void clock_init() {
    QueryPerformanceFrequency(&g_freq);
    // Deliberately NO timeBeginPeriod(1) here. It reads like the obvious fix
    // for the whole-millisecond per-tick timeout below, but measured on a
    // 300 Hz desktop it made the picture older: with a 1 ms timer the wait
    // returns on time to find no new desktop frame yet, so 92% of the encoded
    // frames became repeats of the previous picture (38% before), the bitrate
    // dropped from 14 to 9 Mbps and the phone measured 30 ms to the glass
    // instead of 13 ms. The coarse 15.6 ms timer is what lets one acquisition
    // gather a fresh frame. Waiting on GetFrameLatencyWaitableObject() is the
    // proper answer and is not implemented yet.
}
static uint64_t now_us() {
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    // Divide first: t.QuadPart * 1000000 overflows int64 after about ten days
    // of uptime at the usual 10 MHz counter, and a negative result here would
    // wreck the absolute tick grid the main loop depends on.
    const LONGLONG q = g_freq.QuadPart;
    return (uint64_t)((t.QuadPart / q) * 1000000 + (t.QuadPart % q) * 1000000 / q);
}

// ---------------------------------------------------------------------------
// stdout as a plain byte sink
// ---------------------------------------------------------------------------
static bool write_all(const void* p, size_t n) {
    const uint8_t* b = (const uint8_t*)p;
    size_t done = 0;
    while (done < n) {
        int chunk = (int)std::min<size_t>(n - done, 1u << 20);
        int w = _write(1, b + done, chunk);
        if (w <= 0) return false;
        done += (size_t)w;
    }
    return true;
}

// ---------------------------------------------------------------------------
// command line
// ---------------------------------------------------------------------------
struct Args {
    int adapter = 0;
    int output = 0;
    int fps = 60;
    int width = 0, height = 0;      // 0 = the output's native size
    int cq = 20;
    int mbps = 20;
    int gop = 0;                    // 0 = one second
    int preset = 1;                 // 1..7, P1 is the fastest
    bool cbr = false;
    bool loop_forever = true;
    bool verbose = false;
    bool list = false;              // print the adapters/outputs and exit
    bool pointer = true;            // draw the mouse pointer into the picture
    int frames = 0;                 // >0: stop after this many (self test)
};

static bool parse(int argc, char** argv, Args& a) {
    for (int i = 1; i < argc; i++) {
        std::string k = argv[i];
        auto next = [&]() -> std::string {
            return (i + 1 < argc) ? std::string(argv[++i]) : std::string();
        };
        if (k == "--adapter") a.adapter = std::atoi(next().c_str());
        else if (k == "--output") a.output = std::atoi(next().c_str());
        else if (k == "--fps") a.fps = std::atoi(next().c_str());
        else if (k == "--width") a.width = std::atoi(next().c_str());
        else if (k == "--height") a.height = std::atoi(next().c_str());
        else if (k == "--cq") a.cq = std::atoi(next().c_str());
        else if (k == "--mbps") a.mbps = std::atoi(next().c_str());
        else if (k == "--gop") a.gop = std::atoi(next().c_str());
        else if (k == "--preset") a.preset = std::atoi(next().c_str());
        else if (k == "--rc") a.cbr = next() == "cbr";
        else if (k == "--frames") { a.frames = std::atoi(next().c_str()); a.loop_forever = false; }
        else if (k == "--verbose") a.verbose = true;
        else if (k == "--list") a.list = true;
        else if (k == "--pointer") a.pointer = next() != "0";
        else if (k == "--help" || k == "-h") return false;
        else {
            say("unknown option: %s", k.c_str());
            return false;
        }
    }
    if (a.fps <= 0 || a.fps > 480) { say("bad --fps"); return false; }
    if (a.cq < 1 || a.cq > 51) { say("bad --cq"); return false; }
    if (a.mbps < 1 || a.mbps > 1000) { say("bad --mbps"); return false; }
    if (a.preset < 1) a.preset = 1;
    if (a.preset > 7) a.preset = 7;
    if (a.width < 0 || a.width > 7680) { say("bad --width"); return false; }
    if (a.height < 0 || a.height > 4320) { say("bad --height"); return false; }
    if (a.gop <= 0) a.gop = a.fps;
    return true;
}

// ---------------------------------------------------------------------------
// The duplication hands pointer shapes over in three layouts.  Normalise any
// of them to one straight-alpha BGRA image, which is the only thing the video
// processor will blend.
// ---------------------------------------------------------------------------
static bool pointer_to_bgra(const DXGI_OUTDUPL_POINTER_SHAPE_INFO& info,
                            const unsigned char* src, UINT srcBytes,
                            std::vector<unsigned char>& out, UINT* ow, UINT* oh) {
    const UINT w = info.Width, h = info.Height;
    if (!src || !w || !h) return false;
    const UINT pitch = info.Pitch ? info.Pitch
                             : (info.Type == DXGI_OUTDUPL_POINTER_SHAPE_TYPE_MONOCHROME
                                    ? (UINT)(((w + 7) / 8 + 3) & ~3u)
                                    : w * 4);

    if (info.Type == DXGI_OUTDUPL_POINTER_SHAPE_TYPE_COLOR) {
        // Straight 32bpp BGRA with an alpha channel: copy row by row, because
        // the pitch DDA reports is not always w*4.
        if (srcBytes < (size_t)pitch * h) return false;
        out.assign((size_t)w * h * 4, 0);
        for (UINT y = 0; y < h; y++)
            std::memcpy(&out[(size_t)y * w * 4], src + (size_t)y * pitch, (size_t)w * 4);
        *ow = w; *oh = h;
        return true;
    }

    if (info.Type == DXGI_OUTDUPL_POINTER_SHAPE_TYPE_MASKED_COLOR) {
        // 32bpp masked colour: alpha 0xFF means "XOR this pixel with the screen",
        // which the src-over blending of a video-processor substream cannot express --
        // such pixels are drawn opaque in their own colour instead.  Only programs
        // that really use an XOR cursor (a few CAD tools) can tell the difference.
        if (srcBytes < (size_t)pitch * h) return false;
        out.assign((size_t)w * h * 4, 0);
        for (UINT y = 0; y < h; y++) {
            const unsigned char* row = src + (size_t)y * pitch;
            unsigned char* dst = &out[(size_t)y * w * 4];
            for (UINT x = 0; x < w; x++) {
                unsigned char a = row[x * 4 + 3];
                if (!a) continue;                    // transparent pixel
                dst[x * 4 + 0] = row[x * 4 + 0];
                dst[x * 4 + 1] = row[x * 4 + 1];
                dst[x * 4 + 2] = row[x * 4 + 2];
                dst[x * 4 + 3] = a;
            }
        }
        *ow = w; *oh = h;
        return true;
    }

    // Monochrome: a 1bpp AND mask stacked on a 1bpp XOR mask, so the buffer is
    // twice as tall as the pointer.  The classic four combinations are
    // (AND,XOR) = (1,0) transparent, (0,0) black, (0,1) white, (1,1) invert --
    // src-over cannot invert a pixel, so the invert case is approximated with white
    // (it is the one that stays visible on a dark desktop).
    const UINT half = h / 2;
    if (!half || srcBytes < (size_t)pitch * h) return false;
    out.assign((size_t)w * half * 4, 0);
    for (UINT y = 0; y < half; y++) {
        const unsigned char* andRow = src + (size_t)y * pitch;
        const unsigned char* xorRow = src + (size_t)(y + half) * pitch;
        unsigned char* dst = &out[(size_t)y * w * 4];
        for (UINT x = 0; x < w; x++) {
            int aBit = (andRow[x >> 3] >> (7 - (x & 7))) & 1;
            int xBit = (xorRow[x >> 3] >> (7 - (x & 7))) & 1;
            if (aBit && !xBit) continue;             // transparent: desktop shows
            unsigned char v = (xBit || aBit) ? 255 : 0;
            dst[x * 4 + 0] = v; dst[x * 4 + 1] = v; dst[x * 4 + 2] = v;
            dst[x * 4 + 3] = 255;
        }
    }
    *ow = w; *oh = half;
    return true;
}

// ---------------------------------------------------------------------------
// NVENC preset guid for 1..7
// ---------------------------------------------------------------------------
static GUID preset_guid(int p) {
    switch (p) {
        case 1: return NV_ENC_PRESET_P1_GUID;
        case 2: return NV_ENC_PRESET_P2_GUID;
        case 3: return NV_ENC_PRESET_P3_GUID;
        case 4: return NV_ENC_PRESET_P4_GUID;
        case 5: return NV_ENC_PRESET_P5_GUID;
        case 6: return NV_ENC_PRESET_P6_GUID;
        default: return NV_ENC_PRESET_P7_GUID;
    }
}

// ---------------------------------------------------------------------------
// --list: what can be duplicated, in a form the host can parse
// ---------------------------------------------------------------------------
// One line per output.  The pixel size comes from EnumDisplaySettings, not from
// DesktopCoordinates: that rect is in DPI-scaled virtual units and reports
// 2048x1280 for this 2560x1600 panel at 125%.
static int list_outputs() {
    IDXGIFactory1* f = nullptr;
    if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&f)) || !f) {
        say("FATAL CreateDXGIFactory1 for --list");
        return 2;
    }
    int n = 0;
    for (UINT ai = 0; ; ai++) {
        IDXGIAdapter1* ad = nullptr;
        if (f->EnumAdapters1(ai, &ad) != S_OK || !ad) break;
        DXGI_ADAPTER_DESC1 d{};
        ad->GetDesc1(&d);
        char gpu[512] = {0};
        WideCharToMultiByte(CP_UTF8, 0, d.Description, -1, gpu, (int)sizeof(gpu) - 1, nullptr, nullptr);
        for (UINT oi = 0; ; oi++) {
            IDXGIOutput* o = nullptr;
            if (ad->EnumOutputs(oi, &o) != S_OK || !o) break;
            DXGI_OUTPUT_DESC od{};
            o->GetDesc(&od);
            DEVMODEW dm{};
            dm.dmSize = sizeof(dm);
            int w = 0, h = 0, l = 0, t = 0;
            char devname[64] = {0};
            WideCharToMultiByte(CP_UTF8, 0, od.DeviceName, -1, devname, (int)sizeof(devname) - 1, nullptr, nullptr);
            if (EnumDisplaySettingsW(od.DeviceName, ENUM_CURRENT_SETTINGS, &dm)) {
                w = (int)dm.dmPelsWidth;
                h = (int)dm.dmPelsHeight;
                l = (int)dm.dmPosition.x;
                t = (int)dm.dmPosition.y;
            } else {
                // A pixel=0x0 line would look like a legal target to the host.
                w = (int)(od.DesktopCoordinates.right - od.DesktopCoordinates.left);
                h = (int)(od.DesktopCoordinates.bottom - od.DesktopCoordinates.top);
                l = (int)od.DesktopCoordinates.left;
                t = (int)od.DesktopCoordinates.top;
            }
            printf("out adapter=%u output=%u gpu=\"%s\" device=\"%s\" pixel=%dx%d pos=%d,%d "
                   "rect=%ld,%ld,%ld,%ld attached=%d\n",
                   ai, oi, gpu, devname, w, h, l, t,
                   (long)od.DesktopCoordinates.left, (long)od.DesktopCoordinates.top,
                   (long)(od.DesktopCoordinates.right - od.DesktopCoordinates.left),
                   (long)(od.DesktopCoordinates.bottom - od.DesktopCoordinates.top),
                   od.AttachedToDesktop ? 1 : 0);
            fflush(stdout);
            o->Release();
            n++;
        }
        ad->Release();
    }
    f->Release();
    printf("out total=%d\n", n);
    fflush(stdout);
    return 0;
}

int main(int argc, char** argv) {
    Args a;
    // The duplication reports pixels, and so does the encoder.  Without this the
    // Win32 cursor APIs would answer in 125%-scaled coordinates on a scaled
    // display and the arrow would land in the wrong place.
    SetProcessDPIAware();
    if (!parse(argc, argv, a)) {
        say("DroidCapture --adapter N --output M --fps F [--width W --height H] "
            "[--cq Q] [--mbps M] [--gop G] [--preset 1-7] [--rc vbr|cbr]"
            " [--pointer 0|1]");
        return 2;
    }
    _setmode(1, _O_BINARY);
    if (a.list) return list_outputs();
    clock_init();

    // ---- DXGI adapter + output ----
    IDXGIFactory1* factory = nullptr;
    hr_ok("CreateDXGIFactory1", CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&factory));
    IDXGIAdapter1* adapter = nullptr;
    hr_ok("EnumAdapters1", factory->EnumAdapters1((UINT)a.adapter, &adapter), 2);
    DXGI_ADAPTER_DESC1 ad{};
    adapter->GetDesc1(&ad);
    say("adapter %d: %ls", a.adapter, ad.Description);

    IDXGIOutput* output = nullptr;
    hr_ok("EnumOutputs", adapter->EnumOutputs((UINT)a.output, &output), 2);
    DXGI_OUTPUT_DESC od{};
    output->GetDesc(&od);
    const RECT& dr = od.DesktopCoordinates;
    say("output %d: %ls desktop rect %ld,%ld %ldx%ld", a.output, od.DeviceName,
        (long)dr.left, (long)dr.top, (long)(dr.right - dr.left), (long)(dr.bottom - dr.top));

    // ---- D3D11 device on that adapter ----
    UINT flags = D3D11_CREATE_DEVICE_BGRA_SUPPORT | D3D11_CREATE_DEVICE_VIDEO_SUPPORT;
    D3D_FEATURE_LEVEL fl{};
    ID3D11Device* dev = nullptr;
    ID3D11DeviceContext* ctx = nullptr;
    hr_ok("D3D11CreateDevice",
          D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, flags, nullptr, 0,
                            D3D11_SDK_VERSION, &dev, &fl, &ctx));

    IDXGIOutput1* output1 = nullptr;
    hr_ok("QI IDXGIOutput1", output->QueryInterface(__uuidof(IDXGIOutput1), (void**)&output1));
    IDXGIOutputDuplication* dup = nullptr;
    // A locked screen or the UAC secure desktop makes this fail with
    // E_ACCESSDENIED / NOT_CURRENTLY_AVAILABLE; both clear up by themselves,
    // so retry for a few seconds before calling it a startup failure.
    HRESULT dhr = output1->DuplicateOutput(dev, &dup);
    for (int attempt = 0; attempt < 20 && FAILED(dhr); attempt++) {
        Sleep(250);
        dhr = output1->DuplicateOutput(dev, &dup);
    }
    if (FAILED(dhr)) {
        say("FATAL DuplicateOutput hr=0x%08X (0x%08X already duplicated, "
            "0x%08X access denied)", (unsigned)dhr,
            (unsigned)DXGI_ERROR_NOT_CURRENTLY_AVAILABLE, (unsigned)E_ACCESSDENIED);
        return 2;
    }
    DXGI_OUTDUPL_DESC dd{};
    dup->GetDesc(&dd);
    // The duplication reports the *pixel* size of the mode.  DesktopCoordinates
    // above is in DPI-scaled virtual units (2048x1280 for a 2560x1600 panel at
    // 125%), so sizing anything from that rect would encode and send a
    // downscaled desktop -- measured exactly that on the first run.
    int nativeW = (int)dd.ModeDesc.Width;
    int nativeH = (int)dd.ModeDesc.Height;
    int W = a.width > 0 ? a.width : nativeW;
    int H = a.height > 0 ? a.height : nativeH;
    W &= ~1; H &= ~1;
    say("duplication %dx%d -> encode %dx%d", nativeW, nativeH, W, H);
    // A normal desktop comes back as 8-bit RGBA.  An HDR (10-bit or FP16)
    // desktop comes back in a format the BGRA video processor input cannot
    // take, so say it out loud instead of failing later with a bare HRESULT.
    if (dd.ModeDesc.Format != DXGI_FORMAT_B8G8R8A8_UNORM &&
        dd.ModeDesc.Format != DXGI_FORMAT_B8G8R8A8_UNORM_SRGB &&
        dd.ModeDesc.Format != DXGI_FORMAT_R8G8B8A8_UNORM &&
        dd.ModeDesc.Format != DXGI_FORMAT_R8G8B8A8_UNORM_SRGB) {
        say("WARN desktop format %u is not 8-bit RGBA (HDR desktop?); "
            "colours may come out wrong -- turn HDR off for this display",
            (unsigned)dd.ModeDesc.Format);
    }

    // ---- VideoProcessor: BGRA -> NV12 (with scaling when we were asked to) ----
    ID3D11VideoDevice* vdev = nullptr;
    ID3D11VideoContext* vctx = nullptr;
    hr_ok("QI video device", dev->QueryInterface(__uuidof(ID3D11VideoDevice), (void**)&vdev));
    hr_ok("QI video context", ctx->QueryInterface(__uuidof(ID3D11VideoContext), (void**)&vctx));

    D3D11_VIDEO_PROCESSOR_CONTENT_DESC cd{};
    cd.InputFrameFormat = D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE;
    cd.InputWidth = (UINT)nativeW;
    cd.InputHeight = (UINT)nativeH;
    cd.OutputWidth = (UINT)W;
    cd.OutputHeight = (UINT)H;
    cd.Usage = D3D11_VIDEO_USAGE_OPTIMAL_SPEED;
    ID3D11VideoProcessorEnumerator* vpe = nullptr;
    hr_ok("CreateVideoProcessorEnumerator", vdev->CreateVideoProcessorEnumerator(&cd, &vpe));
    ID3D11VideoProcessor* vp = nullptr;
    hr_ok("CreateVideoProcessor", vdev->CreateVideoProcessor(vpe, 0, &vp));

    D3D11_TEXTURE2D_DESC td{};
    td.Width = (UINT)W; td.Height = (UINT)H; td.MipLevels = 1; td.ArraySize = 1;
    td.Format = DXGI_FORMAT_NV12; td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DEFAULT; td.BindFlags = D3D11_BIND_RENDER_TARGET;
    const int NSURF = 3;
    ID3D11Texture2D* nv12[NSURF] = {};
    ID3D11VideoProcessorOutputView* ov[NSURF] = {};
    for (int i = 0; i < NSURF; i++) {
        hr_ok("CreateTexture2D NV12", dev->CreateTexture2D(&td, nullptr, &nv12[i]));
        D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC ovd{};
        ovd.ViewDimension = D3D11_VPOV_DIMENSION_TEXTURE2D;
        ovd.Texture2D.MipSlice = 0;
        hr_ok("CreateVideoProcessorOutputView",
              vdev->CreateVideoProcessorOutputView(nv12[i], vpe, &ovd, &ov[i]));
    }

    // ---- NVENC ----
    HMODULE nvlib = LoadLibraryW(L"nvEncodeAPI64.dll");
    if (!nvlib) { say("FATAL cannot load nvEncodeAPI64.dll"); return 2; }
    typedef NVENCSTATUS (NVENCAPI *PFN_CREATE)(NV_ENCODE_API_FUNCTION_LIST*);
    PFN_CREATE create = (PFN_CREATE)GetProcAddress(nvlib, "NvEncodeAPICreateInstance");
    if (!create) { say("FATAL nvEncodeAPI dll has no NvEncodeAPICreateInstance"); return 2; }
    NV_ENCODE_API_FUNCTION_LIST nv{ NV_ENCODE_API_FUNCTION_LIST_VER };
    NV_OK(create(&nv));

    void* sess = nullptr;
    NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS oep{ NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS_VER };
    oep.device = dev;
    oep.deviceType = NV_ENC_DEVICE_TYPE_DIRECTX;
    oep.apiVersion = NVENCAPI_VERSION;
    NV_OK(nv.nvEncOpenEncodeSessionEx(&oep, &sess));

    GUID presetGuid = preset_guid(a.preset);
    NV_ENC_PRESET_CONFIG pc{ NV_ENC_PRESET_CONFIG_VER };
    pc.presetCfg = { NV_ENC_CONFIG_VER };
    NV_OK(nv.nvEncGetEncodePresetConfigEx(sess, NV_ENC_CODEC_H264_GUID, presetGuid,
                                          NV_ENC_TUNING_INFO_ULTRA_LOW_LATENCY, &pc));
    NV_ENC_CONFIG cfg = pc.presetCfg;
    NV_ENC_INITIALIZE_PARAMS ip{ NV_ENC_INITIALIZE_PARAMS_VER };
    ip.encodeGUID = NV_ENC_CODEC_H264_GUID;
    ip.presetGUID = presetGuid;
    ip.tuningInfo = NV_ENC_TUNING_INFO_ULTRA_LOW_LATENCY;
    ip.encodeWidth = (uint32_t)W;
    ip.encodeHeight = (uint32_t)H;
    ip.maxEncodeWidth = (uint32_t)W;
    ip.maxEncodeHeight = (uint32_t)H;
    ip.frameRateNum = (uint32_t)a.fps;
    ip.frameRateDen = 1;
    ip.enablePTD = 1;
    ip.encodeConfig = &cfg;

    cfg.profileGUID = NV_ENC_H264_PROFILE_HIGH_GUID;
    cfg.gopLength = (uint32_t)a.gop;
    cfg.frameIntervalP = 1;                       // no B frames: nothing to reorder
    NV_ENC_RC_PARAMS& rc = cfg.rcParams;
    // Desktop content is mostly still, so a quality target with a rate ceiling
    // spends almost nothing while the screen is idle and only approaches the
    // cap during real motion -- exactly what a USB link wants.
    const uint32_t br = (uint32_t)a.mbps * 1000u * 1000u;
    rc.rateControlMode = a.cbr ? NV_ENC_PARAMS_RC_CBR : NV_ENC_PARAMS_RC_VBR;
    rc.averageBitRate = br;
    rc.maxBitRate = br;
    rc.vbvBufferSize = br / 4;
    rc.vbvInitialDelay = rc.vbvBufferSize;
    if (!a.cbr) {
        // API 12.0 has no enableTargetQuality/targetQualityLSB (13.x added
        // them); targetQuality alone is what nvenc used for -cq at this level.
        rc.targetQuality = (uint32_t)a.cq;
    }
    rc.lookaheadDepth = 0;
    rc.multiPass = NV_ENC_MULTI_PASS_DISABLED;
    cfg.encodeCodecConfig.h264Config.outputAUD = 0;
    cfg.encodeCodecConfig.h264Config.repeatSPSPPS = 1;   // every IDR is a resync point
    cfg.encodeCodecConfig.h264Config.idrPeriod = (uint32_t)a.gop;
    cfg.encodeCodecConfig.h264Config.chromaFormatIDC = 1;

    NVENCSTATUS ist = nv.nvEncInitializeEncoder(sess, &ip);
    if (ist != NV_ENC_SUCCESS) {
        say("FATAL nvEncInitializeEncoder -> %s (%d)", nv_str(ist), (int)ist);
        return 2;
    }
    say("nvenc %dx%d @%d fps p%d %s cq=%d cap=%d Mbps gop=%d",
        W, H, a.fps, a.preset, a.cbr ? "cbr" : "vbr", a.cq, a.mbps, a.gop);

    NV_ENC_REGISTERED_PTR reg[NSURF] = {};
    for (int i = 0; i < NSURF; i++) {
        NV_ENC_REGISTER_RESOURCE rr{ NV_ENC_REGISTER_RESOURCE_VER };
        rr.resourceType = NV_ENC_INPUT_RESOURCE_TYPE_DIRECTX;
        rr.width = (uint32_t)W;
        rr.height = (uint32_t)H;
        rr.pitch = 0;
        rr.resourceToRegister = nv12[i];
        rr.bufferFormat = NV_ENC_BUFFER_FORMAT_NV12;
        rr.bufferUsage = NV_ENC_INPUT_IMAGE;
        NV_OK(nv.nvEncRegisterResource(sess, &rr));
        reg[i] = rr.registeredResource;
    }
    NV_ENC_CREATE_BITSTREAM_BUFFER cbs{ NV_ENC_CREATE_BITSTREAM_BUFFER_VER };
    NV_OK(nv.nvEncCreateBitstreamBuffer(sess, &cbs));

    // ---- colours: set once, the processor keeps them ----
    D3D11_VIDEO_PROCESSOR_COLOR_SPACE cs{};
    cs.Usage = 0;
    cs.RGB_Range = 0;      // desktop is full-range RGB
    cs.YCbCr_Matrix = 1;   // BT.709
    cs.YCbCr_xvYCC = 0;
    cs.Nominal_Range = D3D11_VIDEO_PROCESSOR_NOMINAL_RANGE_0_255;
    // The SPS the encoder writes carries no VUI colour information, so every
    // H.264 decoder -- MediaCodec included -- assumes studio range 16-235.
    // Leaving this UNDEFINED let the driver decide; say it explicitly instead.
    D3D11_VIDEO_PROCESSOR_COLOR_SPACE out_cs = cs;
    out_cs.Nominal_Range = D3D11_VIDEO_PROCESSOR_NOMINAL_RANGE_16_235;
    vctx->VideoProcessorSetStreamFrameFormat(vp, 0, D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE);
    RECT src{ 0, 0, nativeW, nativeH };
    RECT dst{ 0, 0, W, H };
    vctx->VideoProcessorSetStreamSourceRect(vp, 0, TRUE, &src);
    vctx->VideoProcessorSetStreamDestRect(vp, 0, TRUE, &dst);
    vctx->VideoProcessorSetStreamColorSpace(vp, 0, &cs);
    vctx->VideoProcessorSetOutputColorSpace(vp, &out_cs);

    // ---- cursor: a second input stream through the same Blt ----
    // The duplication can tell us where the pointer is and what it looks like;
    // it just will not put it into the picture, because a hardware cursor is
    // composited by the display hardware after the desktop has been read.
    // Handing the shape to the video processor as a second input stream (a
    // "substream") keeps the path at a single GPU pass: the desktop stays the
    // frame the encoder reads and the pointer is blended in during the same
    // Blt.  Nothing is copied on the CPU.
    D3D11_VIDEO_PROCESSOR_CAPS vcaps{};
    UINT cap_inputs = 0;
    bool cap_alpha = false;
    if (SUCCEEDED(vpe->GetVideoProcessorCaps(&vcaps))) {
        cap_inputs = vcaps.MaxInputStreams;
        cap_alpha = (vcaps.FeatureCaps & D3D11_VIDEO_PROCESSOR_FEATURE_CAPS_ALPHA_STREAM) != 0;
    }
    const bool have_pointer = a.pointer && cap_inputs > 1 && cap_alpha;
    say("video processor: inputs=%u alpha_stream=%s -> pointer %s",
        (unsigned)cap_inputs, cap_alpha ? "yes" : "no",
        have_pointer ? "drawn as a substream" : "not drawn");

    {
        CURSORINFO ci{};
        ci.cbSize = sizeof(ci);
        if (GetCursorInfo(&ci))
            say("win32 cursor: flags=0x%X pos=%ld,%ld", (unsigned)ci.flags,
                ci.ptScreenPos.x, ci.ptScreenPos.y);
        else
            say("win32 cursor: GetCursorInfo failed");
    }

    std::vector<unsigned char> pshape, pimg;
    DXGI_OUTDUPL_POINTER_SHAPE_INFO psi{};
    ID3D11Texture2D* ptex = nullptr;
    ID3D11VideoProcessorInputView* pview = nullptr;
    UINT pw = 0, ph = 0;
    long px = 0, py = 0;
    bool pvisible = false, have_shape = false, pointer_dirty = false;

    auto upload_pointer = [&]() -> bool {
        UINT w = 0, h = 0;
        if (!pointer_to_bgra(psi, pshape.data(), (UINT)pshape.size(), pimg, &w, &h)) {
            say("pointer: shape type %u %ux%u cannot be converted",
                (unsigned)psi.Type, psi.Width, psi.Height);
            return false;
        }
        if (!ptex || w != pw || h != ph) {
            if (pview) { pview->Release(); pview = nullptr; }
            if (ptex) { ptex->Release(); ptex = nullptr; }
            D3D11_TEXTURE2D_DESC d{};
            d.Width = w; d.Height = h; d.MipLevels = 1; d.ArraySize = 1;
            d.Format = DXGI_FORMAT_B8G8R8A8_UNORM; d.SampleDesc.Count = 1;
            d.Usage = D3D11_USAGE_DEFAULT;
            d.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
            if (FAILED(dev->CreateTexture2D(&d, nullptr, &ptex))) {
                say("pointer: CreateTexture2D %ux%u failed", w, h);
                ptex = nullptr; pw = ph = 0;
                return false;
            }
            D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC ivd{};
            ivd.FourCC = 0;
            ivd.ViewDimension = D3D11_VPIV_DIMENSION_TEXTURE2D;
            ivd.Texture2D.MipSlice = 0;
            ivd.Texture2D.ArraySlice = 0;
            if (FAILED(vdev->CreateVideoProcessorInputView(ptex, vpe, &ivd, &pview))) {
                say("pointer: CreateVideoProcessorInputView failed");
                // Leave pw/ph at 0 so the next pointer update rebuilds the
                // view instead of being skipped for the rest of the run.
                pview = nullptr;
                ptex->Release();
                ptex = nullptr;
                pw = ph = 0;
                return false;
            }
            pw = w; ph = h;
            vctx->VideoProcessorSetStreamFrameFormat(vp, 1, D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE);
            vctx->VideoProcessorSetStreamAlpha(vp, 1, TRUE, 1.0f);
            vctx->VideoProcessorSetStreamColorSpace(vp, 1, &cs);
        }
        ctx->UpdateSubresource(ptex, 0, nullptr, pimg.data(), w * 4, 0);
        return pview != nullptr;
    };

    // Called on every frame the duplication hands over, before the frame is
    // released: the shape buffer is only valid until then.
    auto read_pointer = [&](const DXGI_OUTDUPL_FRAME_INFO& fi) -> bool {
        if (!have_pointer) return false;
        bool changed = false;
        // PointerPosition is only filled in for frames that actually carry a
        // pointer update (LastMouseUpdateTime != 0).  On every other frame it
        // reads back as "hidden at 0,0", so taking it at face value would park
        // the arrow in the top-left corner.  Keep the last known position.
        if (fi.LastMouseUpdateTime.QuadPart != 0) {
            bool vis = fi.PointerPosition.Visible != FALSE;
            long nx = (long)fi.PointerPosition.Position.x;
            long ny = (long)fi.PointerPosition.Position.y;
            if (vis != pvisible || nx != px || ny != py) changed = true;
            pvisible = vis;
            px = nx;
            py = ny;
        }
        if (fi.PointerShapeBufferSize > 0) {
            pshape.resize((size_t)fi.PointerShapeBufferSize + 256);
            UINT needed = (UINT)fi.PointerShapeBufferSize + 256;
            HRESULT phr = E_FAIL;
            for (int attempt = 0; attempt < 3; attempt++) {
                pshape.resize(needed);
                phr = dup->GetFramePointerShape((UINT)pshape.size(), pshape.data(),
                                                 &needed, &psi);
                if (phr != DXGI_ERROR_MORE_DATA) break;
                needed += 256;
            }
            if (SUCCEEDED(phr)) have_shape = upload_pointer();
            else { say("GetFramePointerShape hr=0x%08X", (unsigned)phr); have_shape = false; }
            changed = true;
        }
        return changed;
    };

    if (have_pointer) {
        // The duplication only mentions the pointer when it moves, so a pointer
        // that has not been touched yet would never be drawn.  Ask Win32.
        POINT pt{};
        if (GetCursorPos(&pt)) { px = (long)pt.x; py = (long)pt.y; }
        CURSORINFO ci{};
        ci.cbSize = sizeof(ci);
        pvisible = GetCursorInfo(&ci) && (ci.flags & CURSOR_SHOWING);
        pointer_dirty = true;
        say("pointer starts at %ld,%ld visible=%d", px, py, pvisible ? 1 : 0);
    }

    // ---- main loop ----
    const uint64_t period = 1000000ull / (uint64_t)a.fps;
    uint64_t next_tick = now_us();
    uint64_t t_stat = now_us();
    uint64_t stat_frames = 0, stat_bytes = 0;
    std::vector<double> enc_ms;
    enc_ms.reserve(4096);

    // Which NV12 surface the last picture went into.  A repeat frame has to
    // re-encode that exact surface: the next one in the ring still holds the
    // picture from two frames ago.
    int last_slot = -1;
    bool have_content = false;    // something has been converted and can be repeated
    bool skip_first = true;       // the duplication's priming frame is not desktop content
    int encoded = 0;
    int rc_exit = 0;
    uint64_t last_acq = 0;
    int repeat_run = 0, repeat_max = 0;
    int dropped = 0, drop_max = 0;
    uint64_t drop_run = 0;
    uint64_t t_prev_acq = 0;
    std::vector<double> acq_gap;

    ID3D11Texture2D* view_tex = nullptr;
    // The duplication hands its own texture over to us only between Acquire and
    // Release, and it is free to move that texture in and out of whatever state
    // it likes afterwards.  A repeat blit happens outside that window, so the
    // desktop is copied into a texture we own first: every blit then reads a
    // resource whose state is completely ours.
    ID3D11Texture2D* bg_tex = nullptr;
    ID3D11VideoProcessorInputView* bg_view = nullptr;

    // One Blt converts the desktop and blends the pointer on top of it.  The
    // pointer rectangle is scaled with the picture, so a stream that is scaled
    // down still gets the arrow in the right spot and the right size.
    auto blit = [&](int target) -> HRESULT {
        D3D11_VIDEO_PROCESSOR_STREAM st[2]{};
        st[0].Enable = TRUE;
        st[0].OutputIndex = 0;
        st[0].InputFrameOrField = 0;
        st[0].pInputSurface = bg_view;
        UINT nstreams = 1;
        // Re-state the substream configuration on every Blt: a repeat blit comes
        // from the loop rather than from a fresh desktop frame and the runtime
        // wants the whole stream description at hand.
        if (vctx) {
            vctx->VideoProcessorSetStreamFrameFormat(vp, 1, D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE);
            vctx->VideoProcessorSetStreamAlpha(vp, 1, TRUE, 1.0f);
            vctx->VideoProcessorSetStreamColorSpace(vp, 1, &cs);
        }
        if (have_shape && pvisible && pview) {
            // A hardware cursor may hang over the edge of the desktop, and the
            // video processor rejects a destination rectangle that is not fully
            // inside the output (E_INVALIDARG), so clip it and take the matching
            // part of the shape.
            long ox = (long)((long long)px * W / nativeW);
            long oy = (long)((long long)py * H / nativeH);
            long ow = (long)((long long)pw * W / nativeW);
            long oh = (long)((long long)ph * H / nativeH);
            if (ow < 1) ow = 1;
            if (oh < 1) oh = 1;
            long cx0 = ox < 0 ? 0 : ox;
            long cy0 = oy < 0 ? 0 : oy;
            long cx1 = (ox + ow) > (long)W ? (long)W : (ox + ow);
            long cy1 = (oy + oh) > (long)H ? (long)H : (oy + oh);
            if (cx1 > cx0 && cy1 > cy0) {
                RECT psrc{ (long)((cx0 - ox) * (long)pw / ow),
                           (long)((cy0 - oy) * (long)ph / oh),
                           (long)((cx1 - ox) * (long)pw / ow),
                           (long)((cy1 - oy) * (long)ph / oh) };
                RECT pdst{ cx0, cy0, cx1, cy1 };
                vctx->VideoProcessorSetStreamSourceRect(vp, 1, TRUE, &psrc);
                vctx->VideoProcessorSetStreamDestRect(vp, 1, TRUE, &pdst);
                st[1].Enable = TRUE;
                st[1].OutputIndex = 0;
                st[1].InputFrameOrField = 0;
                st[1].pInputSurface = pview;
                nstreams = 2;
            }
        }
        return vctx->VideoProcessorBlt(vp, ov[target], 0, nstreams, st);
    };

    for (;;) {
        uint64_t t_now = now_us();
        uint64_t wait_us = (next_tick > t_now) ? (next_tick - t_now) : 0;
        UINT timeout = (UINT)((wait_us + 999) / 1000);
        uint64_t dbg_wait = wait_us;
        UINT dbg_timeout = timeout;

        DXGI_OUTDUPL_FRAME_INFO fi{};
        IDXGIResource* res = nullptr;
        HRESULT hr = dup->AcquireNextFrame(timeout, &fi, &res);
        bool fresh = false;
        uint64_t t_acq = 0;
        int blit_slot = last_slot;

        if (hr == S_OK) {
            t_acq = now_us();
            fresh = true;
        } else if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
            // Nothing moved.  Repeat the last picture so the phone's vsync
            // cadence stays steady (this is what ddagrab's framerate option
            // did for ffmpeg); the repeated P frames are a couple of kB.
            fresh = false;
        } else if (hr == DXGI_ERROR_ACCESS_LOST || hr == DXGI_ERROR_SESSION_DISCONNECTED) {
            if (res) res->Release();
            say("AcquireNextFrame 0x%08X access lost (display mode change, session "
                "switch or secure desktop)", (unsigned)hr);
            rc_exit = 3;   // caller rebuilds the process and re-reads the geometry
            break;
        } else if (hr == DXGI_ERROR_INVALID_CALL) {
            if (res) res->Release();
            // Only one thing causes this: a frame was acquired twice without a
            // ReleaseFrame in between.  That is a bug here, not a mode change.
            say("FATAL AcquireNextFrame INVALID_CALL -- a frame was not released");
            rc_exit = 2;
            break;
        } else if (hr == DXGI_ERROR_DEVICE_REMOVED || hr == DXGI_ERROR_DEVICE_RESET) {
            if (res) res->Release();
            say("FATAL AcquireNextFrame device removed hr=0x%08X", (unsigned)hr);
            rc_exit = 4;
            break;
        } else {
            if (res) res->Release();
            say("FATAL AcquireNextFrame hr=0x%08X", (unsigned)hr);
            rc_exit = 2;
            break;
        }

        if (fresh) {
            // Read the pointer first: the shape buffer it refers to is only
            // valid until ReleaseFrame, and a pointer that moves while the
            // desktop itself is still arrives as a frame of its own.
            if (read_pointer(fi)) pointer_dirty = true;

            // A desktop that refreshes faster than the rate we were asked for
            // (a 300 Hz panel feeding a 120 fps stream) delivers frames well
            // before the next tick is due.  Drop those unencoded: the picture
            // would be stale by the time the phone shows it, and skipping it
            // keeps the cadence exact.  Nothing here touches the reference
            // chain, because the frame never reaches the encoder.
            if (t_acq < next_tick) {
                res->Release();
                dup->ReleaseFrame();
                dropped++;
                drop_run++;
                if (drop_run > (uint64_t)drop_max) drop_max = (int)drop_run;
                continue;            // next_tick is untouched: still early
            }
            drop_run = 0;

            ID3D11Texture2D* desktop = nullptr;
            res->QueryInterface(__uuidof(ID3D11Texture2D), (void**)&desktop);
            res->Release();
            if (!desktop) { dup->ReleaseFrame(); next_tick += period; continue; }

            D3D11_TEXTURE2D_DESC dtd{};
            desktop->GetDesc(&dtd);
            if ((int)dtd.Width != nativeW || (int)dtd.Height != nativeH) {
                // the mode changed under us; the caller has to rebuild
                dup->ReleaseFrame();
                desktop->Release();
                say("desktop is now %ux%u, need a fresh session", dtd.Width, dtd.Height);
                rc_exit = 3;
                break;
            }

            if (desktop != view_tex) {
                view_tex = desktop;

                if (bg_view) { bg_view->Release(); bg_view = nullptr; }
                if (bg_tex) { bg_tex->Release(); bg_tex = nullptr; }
                D3D11_TEXTURE2D_DESC bd{};
                desktop->GetDesc(&bd);
                bd.Usage = D3D11_USAGE_DEFAULT;
                bd.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
                bd.CPUAccessFlags = 0;
                bd.MiscFlags = 0;
                hr_ok("CreateTexture2D (desktop copy)", dev->CreateTexture2D(&bd, nullptr, &bg_tex));
                D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC bvd{};
                bvd.FourCC = 0;
                bvd.ViewDimension = D3D11_VPIV_DIMENSION_TEXTURE2D;
                bvd.Texture2D.MipSlice = 0;
                bvd.Texture2D.ArraySlice = 0;
                hr_ok("CreateVideoProcessorInputView (desktop copy)",
                      vdev->CreateVideoProcessorInputView(bg_tex, vpe, &bvd, &bg_view));
            }

            if (bg_tex) ctx->CopyResource(bg_tex, desktop);

            // blit into the surface after the one we are about to encode, so a
            // surface is never written while the encoder still reads it
            if (last_slot < 0) last_slot = 0;
            int target = (last_slot + 1) % NSURF;
            hr_ok("VideoProcessorBlt", blit(target));
            blit_slot = target;
            pointer_dirty = false;

            dup->ReleaseFrame();     // the desktop texture is borrowed until here
            desktop->Release();

            if (t_prev_acq) acq_gap.push_back((t_acq - t_prev_acq) / 1000.0);
            t_prev_acq = t_acq;

            if (skip_first) {
                // ddagrab needed select=gt(n,0) to drop the same frame; the
                // encoder's first picture is an IDR either way.
                skip_first = false;
                repeat_run = 0;
                next_tick += period;
                continue;
            }
            have_content = true;
            repeat_run = 0;
            last_acq = t_acq;
        } else {
            if (!have_content) { next_tick += period; continue; }
            repeat_run++;
            if (repeat_run > repeat_max) repeat_max = repeat_run;
            // Only the pointer moved, and those frames were dropped above for
            // being early.  Redraw the last picture with the newest pointer
            // position instead of re-encoding a picture with a stale arrow.
            if (pointer_dirty && bg_view && last_slot >= 0) {
                int t = (last_slot + 1) % NSURF;
                hr_ok("VideoProcessorBlt (pointer)", blit(t));
                blit_slot = t;
                pointer_dirty = false;
            }
        }

        // ---- encode the picture: the fresh blit, or the same surface again ----
        int enc_slot = (fresh || blit_slot != last_slot) ? blit_slot : last_slot;
        NV_ENC_MAP_INPUT_RESOURCE mi{ NV_ENC_MAP_INPUT_RESOURCE_VER };
        mi.registeredResource = reg[enc_slot];
        NVENCSTATUS s = nv.nvEncMapInputResource(sess, &mi);
        if (s != NV_ENC_SUCCESS) {
            say("FATAL nvEncMapInputResource -> %s", nv_str(s));
            rc_exit = 2;
            break;
        }
        NV_ENC_PIC_PARAMS pp{ NV_ENC_PIC_PARAMS_VER };
        pp.inputWidth = (uint32_t)W;
        pp.inputHeight = (uint32_t)H;
        pp.inputPitch = 0;
        pp.inputBuffer = mi.mappedResource;
        pp.outputBitstream = cbs.bitstreamBuffer;
        pp.bufferFmt = NV_ENC_BUFFER_FORMAT_NV12;
        pp.pictureStruct = NV_ENC_PIC_STRUCT_FRAME;
        pp.inputTimeStamp = fresh ? t_acq : now_us();
        pp.frameIdx = (uint32_t)encoded;
        uint64_t t_enc0 = now_us();
        s = nv.nvEncEncodePicture(sess, &pp);
        if (s != NV_ENC_SUCCESS) {
            nv.nvEncUnmapInputResource(sess, mi.mappedResource);
            say("FATAL nvEncEncodePicture -> %s", nv_str(s));
            rc_exit = 2;
            break;
        }
        NV_ENC_LOCK_BITSTREAM lb{ NV_ENC_LOCK_BITSTREAM_VER };
        lb.outputBitstream = cbs.bitstreamBuffer;
        lb.doNotWait = 0;
        s = nv.nvEncLockBitstream(sess, &lb);
        if (s != NV_ENC_SUCCESS) {
            nv.nvEncUnmapInputResource(sess, mi.mappedResource);
            say("FATAL nvEncLockBitstream -> %s", nv_str(s));
            rc_exit = 2;
            break;
        }
        uint64_t t_out = now_us();
        size_t n = lb.bitstreamSizeInBytes;
        bool ok = write_all(lb.bitstreamBufferPtr, n);
        nv.nvEncUnlockBitstream(sess, cbs.bitstreamBuffer);
        nv.nvEncUnmapInputResource(sess, mi.mappedResource);
        if (!ok) {
            say("stdout closed, stopping");
            rc_exit = 0;
            break;
        }
        if (encoded) enc_ms.push_back((t_out - t_enc0) / 1000.0);
        if (a.verbose)
            say("frame %d t=%llu fresh=%d enc=%.2f ms %zu B slot=%d "
                "wait=%llu timeout=%u period=%llu ahead=%lld "
                "ptr=%ld,%ld vis=%d shape=%d %ux%u",
                encoded, (unsigned long long)t_out, fresh ? 1 : 0,
                (t_out - t_enc0) / 1000.0, n, enc_slot,
                (unsigned long long)dbg_wait, dbg_timeout, (unsigned long long)period,
                (long long)(next_tick - t_out), px, py, pvisible ? 1 : 0,
                have_shape ? 1 : 0, pw, ph);
        encoded++;
        stat_frames++;
        stat_bytes += n;
        last_slot = enc_slot;

        if (a.frames > 0 && encoded >= a.frames) break;

        // ---- cadence ----
        // Absolute grid, one period per emitted picture, so the long run rate
        // is exact.  When an encode overruns its tick the grid skips whole
        // periods instead of snapping to "now": snapping is what made pairs go
        // out 2 ms apart, and a missed tick has to cost a frame, not jitter.
        next_tick += period;
        uint64_t t_after = now_us();
        if (t_after > next_tick) {
            uint64_t missed = (t_after - next_tick) / period;
            next_tick += missed * period;
        }

        // ---- stats, every 5 s ----
        if (t_after - t_stat > 5000000ull) {
            double dt = (t_after - t_stat) / 1e6;
            double p50 = 0, p90 = 0, mx = 0;
            if (!enc_ms.empty()) {
                std::vector<double> v = enc_ms;
                std::sort(v.begin(), v.end());
                p50 = v[(size_t)(0.5 * (v.size() - 1))];
                p90 = v[(size_t)(0.9 * (v.size() - 1))];
                mx = v.back();
            }
            double gap50 = 0;
            if (!acq_gap.empty()) {
                std::vector<double> g = acq_gap;
                std::sort(g.begin(), g.end());
                gap50 = g[(size_t)(0.5 * (g.size() - 1))];
            }
            say("perf fps=%.1f mbps=%.2f enc_p50=%.2f enc_p90=%.2f enc_max=%.2f "
                "acq_gap_p50=%.2f repeats=%d(%d) dropped=%d(%d)",
                stat_frames / dt, stat_bytes * 8.0 / dt / 1e6, p50, p90, mx, gap50,
                repeat_run, repeat_max, dropped, drop_max);
            t_stat = t_after;
            stat_frames = 0;
            stat_bytes = 0;
            enc_ms.clear();
            acq_gap.clear();
            repeat_max = 0;
        }
    }

    // ---- teardown (best effort; the process is about to go) ----
    say("stopped after %d frames", encoded);
    if (pview) pview->Release();
    if (ptex) ptex->Release();
    if (bg_tex) bg_tex->Release();
    if (bg_view) bg_view->Release();
    for (int i = 0; i < NSURF; i++) {
        if (reg[i]) nv.nvEncUnregisterResource(sess, reg[i]);
        if (ov[i]) ov[i]->Release();
        if (nv12[i]) nv12[i]->Release();
    }
    if (cbs.bitstreamBuffer) nv.nvEncDestroyBitstreamBuffer(sess, cbs.bitstreamBuffer);
    if (sess) nv.nvEncDestroyEncoder(sess);
    if (vp) vp->Release();
    if (vpe) vpe->Release();
    if (vctx) vctx->Release();
    if (vdev) vdev->Release();
    if (dup) dup->Release();
    if (output1) output1->Release();
    if (output) output->Release();
    if (adapter) adapter->Release();
    if (factory) factory->Release();
    if (ctx) ctx->Release();
    if (dev) dev->Release();
    return rc_exit;
}
