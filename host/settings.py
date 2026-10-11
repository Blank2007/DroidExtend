"""Remember the settings between runs.

The window used to come up with hard-coded defaults every time, so a resolution
or bitrate you had tuned was gone after a restart.  Everything the form can set
is now written to %USERPROFILE%\\.droidextend\\settings.json and read back at
startup.

Failure is never fatal: a missing, unreadable or corrupt file just means "use
the defaults", because losing a settings file must not stop you from streaming.
"""
import json
import os
import tempfile

from paths import CONFIG_DIR
from session import DEFAULTS

FILE = os.path.join(CONFIG_DIR, "settings.json")

# Keys we write back.  Deliberately a whitelist: cfg also carries transient
# things (monitor, auto_launch, touch) that are not user settings.
KEYS = (
    "width", "height", "hz", "fps", "bitrate_mbps", "rate_control", "quality",
    "encoder", "preset", "backend", "position", "mode", "draw_mouse",
    "install_apk", "match_phone",
)
# Extra, non-cfg keys.
EXTRA = ("window", "check_updates", "custom_res")

_TYPES = {k: type(DEFAULTS[k]) for k in KEYS if k in DEFAULTS}


def load():
    """Return the full settings dict: defaults, overlaid with whatever is on disk."""
    out = {k: DEFAULTS[k] for k in KEYS if k in DEFAULTS}
    out.update({"window": "", "check_updates": True, "custom_res": ""})
    try:
        with open(FILE, "r", encoding="utf-8") as f:
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
        os.makedirs(CONFIG_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".settings-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, FILE)       # atomic: never leave a half file behind
    except Exception:
        try:
            if "tmp" in dir() and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return None
    return FILE


def where():
    return FILE
