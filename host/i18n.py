"""Two-language table for the host GUI.

Deliberately tiny: `t("中文", "English")` at every call site, no resource
files to keep in sync and no build step.  The string is written where it is
used, so a missing translation is impossible -- the Chinese is the fallback and
it is always right there.

Language choice lives in settings.json as `language`:
    "zh"    Chinese
    "en"    English
    "auto"  follow Windows' UI language (the default)

`resolve()` turns that into a concrete code; `set_lang()` applies it.  Switching
at runtime rebuilds the window rather than retranslating live -- see app.py.
"""
import ctypes

ZH = "zh"
EN = "en"
AUTO = "auto"

_LANG = ZH

# The Windows LANGID's low 10 bits are the primary language id; 0x09 is English.
_PRIMARY_ENGLISH = 0x09


def from_system():
    """'en' only when Windows' UI language is English; everything else Chinese."""
    try:
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return EN if (int(lid) & 0x3FF) == _PRIMARY_ENGLISH else ZH
    except Exception:
        return ZH


def resolve(setting):
    """Turn the stored setting into a concrete code ('auto' follows Windows)."""
    code = (setting or "").strip().lower()
    if code in (ZH, EN):
        return code
    return from_system()


def set_lang(code):
    global _LANG
    _LANG = code if code in (ZH, EN) else resolve(code)
    return _LANG


def lang():
    return _LANG


def en():
    return _LANG == EN


def t(zh, english):
    """The one function every UI string goes through."""
    return english if _LANG == EN else zh


def label(code):
    """Name of a language code, written in that language.

    Not routed through t(): a language is always offered under its own name, so
    both entries stay distinguishable whichever language is active.
    """
    return "中文" if code == ZH else "English"


def choices():
    """(value, caption) pairs for the language picker, in menu order."""
    return [
        (AUTO, t("跟随系统", "Follow system")),
        (ZH, "中文"),
        (EN, "English"),
    ]
