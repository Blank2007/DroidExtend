"""The third-party list and the licence file that has to travel with it.

Two shells draw this list now -- the Tk window and the pywebview page -- and
the MIT/Apache/BSD notices are a legal obligation rather than a decoration, so
the rows live in one place instead of being copied per window.

A function rather than a constant: Inno Setup's row carries a note, and a table
built at import time would freeze the language before the settings file has
been read.
"""
import i18n

# Shipped next to DroidExtend.exe by the installer; MIT/Apache/BSD all want the
# notice to travel with the binary copy.
LICENSES = "第三方组件许可.txt"


def credits():
    """(component, licence, where it comes from) rows for the About window."""
    return [
        ("Virtual Display Driver (VDD)", "MIT", "github.com/VirtualDrivers/Virtual-Display-Driver"),
        ("nefcon", "MIT", "github.com/nefarius/nefcon"),
        ("FFmpeg 6.0 (gyan.dev essentials)", "GPL-3.0", "ffmpeg.org"),
        ("Android SDK Platform-Tools / adb", "Apache-2.0", "developer.android.com/tools/releases/platform-tools"),
        ("R8 / D8 (Android build-tools)", "BSD-3-Clause", "r8.googlesource.com/r8"),
        ("Python", "PSF-2.0", "python.org"),
        ("pywebview", "BSD-3-Clause", "pywebview.flowrl.com"),
        ("WebView2 (Microsoft Edge Runtime)", "Microsoft Software Licence", "developer.microsoft.com/microsoft-edge/webview2"),
        ("Tcl/Tk", "BSD-style", "tcl.tk"),
        ("PyInstaller", "GPL-2.0 + bootloader exception", "pyinstaller.org"),
        (i18n.t("Inno Setup（只用于生成安装包）",
                "Inno Setup (used only to build the installer)"),
         "Inno Setup License", "jrsoftware.org/isinfo.php"),
    ]
