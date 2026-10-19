"""Remember the settings between runs.

The window used to come up with hard-coded defaults every time, so a resolution
or bitrate you had tuned was gone after a restart.  Everything the form can set
is written to settings.json in the app's data folder -- the same folder as
DroidExtend.log, so there is one place to look (see paths.data_dir).

Failure is never fatal: a missing, unreadable or corrupt file just means "use
the defaults", because losing a settings file must not stop you from streaming.
"""
import json
import os
import shutil
import tempfile

import paths
from session import DEFAULTS


# 1.2.0 and earlier wrote here.  From 1.3.1 the settings and the log share the
# app's own data folder, so an existing file is carried over once (below).
LEGACY_DIR = os.path.join(os.path.expanduser("~"), ".droidextend")
LEGACY_FILE = os.path.join(LEGACY_DIR, "settings.json")


def path():
    """Where settings.json lives, resolved fresh so a moved folder takes effect."""
    return os.path.join(paths.data_dir(), "settings.json")


def migrate_legacy(log=None):
    """Copy a settings file from the old user-profile location.  Once only."""
    target = path()
    if os.path.exists(target) or not os.path.exists(LEGACY_FILE):
        return None
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(LEGACY_FILE, target)
        if log:
            log("已把旧设置从 %s 搬到 %s" % (LEGACY_FILE, target))
        return target
    except Exception:
        return None


# Keys we write back.  Deliberately a whitelist: cfg also carries transient
# things (monitor, auto_launch, touch) that are not user settings.
KEYS = (
    "width", "height", "hz", "fps", "bitrate_mbps", "rate_control", "quality",
    "encoder", "preset", "backend", "position", "mode", "draw_mouse",
    "install_apk", "match_phone",
    # 1.6.0 -- position="custom" needs somewhere to keep the coordinates.
    "pos_x", "pos_y",
)
# Extra, non-cfg keys.
EXTRA = (
    "window", "check_updates", "custom_res",
    # 1.6.0 -- choices that belong to the shell rather than to one stream.
    "language",          # "auto" / "zh" / "en", see i18n.resolve
    "privacy_accepted",  # version string of the notice the user agreed to
    "tray_on_close",     # closing the window hides to the tray instead of exiting
    "autostart",         # start with Windows
    "update_dir",        # where to keep the downloaded installer ("" = %TEMP%)
    "delete_installer",  # remove the installer once it has been applied
    "animations",        # fade windows and flicker controls; see anim.py
    # 1.6.4 -- what "off" means for the virtual display, see vdd.set_count /
    # app._apply_vdd_off.  "deep" = no monitor at boot at all (needs UAC);
    # "desktop" = the monitor exists but never sits on the desktop (no UAC).
    "vdd_off_mode",
    # 1.6.8 -- what the window's close button does: "ask" (default) pops the
    # question, "tray" hides to the tray, "quit" exits.  Replaces the
    # tray_on_close check box that used to sit on the main window.
    "close_action",
)

_EXTRA_DEFAULTS = {
    "window": "",
    "check_updates": True,
    "custom_res": "",
    "language": "auto",
    "privacy_accepted": "",
    "tray_on_close": True,
    "autostart": False,
    "update_dir": "",
    "delete_installer": True,
    "animations": True,
    "vdd_off_mode": "deep",
    "close_action": "ask",
}

_TYPES = {k: type(DEFAULTS[k]) for k in KEYS if k in DEFAULTS}
_TYPES.update({k: type(v) for k, v in _EXTRA_DEFAULTS.items()})


def load():
    """Return the full settings dict: defaults, overlaid with whatever is on disk."""
    out = {k: DEFAULTS[k] for k in KEYS if k in DEFAULTS}
    out.update(_EXTRA_DEFAULTS)
    try:
        # utf-8-sig, not utf-8: settings.json is a file people are invited to
        # look at, and Notepad (and plenty of other editors) save UTF-8 *with* a
        # BOM.  A BOM makes json.load raise, which used to mean "corrupt" and
        # silently threw every setting away.  utf-8-sig reads the BOM-less case
        # exactly the same way.
        with open(path(), "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        return out
    except Exception:
        return out          # corrupt / half-written -> defaults
    if not isinstance(data, dict):
        return out
    for k, v in data.items():
        if k not in KEYS and k not in EXTRA:
            continue
        want = _TYPES.get(k)
        if want is bool:
            out[k] = bool(v)
        elif want is int:
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                continue
        elif want is str:
            out[k] = str(v)
        else:
            out[k] = v
    return out


def save(data):
    """Write the whitelisted subset atomically.  Returns the path, or None."""
    payload = {}
    for k in KEYS + EXTRA:
        if k in data:
            v = data[k]
            if isinstance(v, (bool, int, float, str)) or v is None:
                payload[k] = v
    try:
        target = path()
        d = os.path.dirname(target)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".settings-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)     # atomic: never leave a half file behind
    except Exception:
        try:
            if "tmp" in dir() and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return None
    return target


def where():
    return path()
