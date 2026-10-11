"""Keep a log file next to settings.json.

Before this the Windows side had no log file at all: the only copy of what
happened was the text pane, and it died with the window.  That is exactly the
wrong property for a program whose interesting failures (a display the driver
refused to activate, an adb install that failed, a capture that had to be
restarted) happen when nobody is watching.

The file lives in the app's data folder -- the same one as settings.json, see
paths.data_dir -- is capped so it cannot grow forever, and is opened as early as
possible so a crash during startup still leaves a trace.  Everything written
here is best effort: a logging failure must never take the app down.
"""
import os
import threading
import time

import paths

NAME = "DroidExtend.log"
MAX_BYTES = 2 * 1024 * 1024       # rotate at 2 MB, keep one old file
_lock = threading.Lock()
_fh = None
_path = None


def path():
    """Full path of the log file, whether or not it is open yet."""
    if _path:
        return _path
    return os.path.join(paths.data_dir(), NAME)


def _rotate(target):
    try:
        if os.path.exists(target) and os.path.getsize(target) > MAX_BYTES:
            old = target + ".1"
            try:
                os.remove(old)
            except OSError:
                pass
            os.replace(target, old)
    except Exception:
        pass


def start(prefix=""):
    """Open the log for this run.  Safe to call more than once."""
    global _fh, _path
    with _lock:
        if _fh is not None:
            return _path
        try:
            d = paths.data_dir()
            os.makedirs(d, exist_ok=True)
            target = os.path.join(d, NAME)
            _rotate(target)
            _fh = open(target, "a", encoding="utf-8", errors="replace")
            _path = target
            _fh.write("\n")
            _fh.write("=== %s %s ===\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), prefix or "DroidExtend"))
            _fh.flush()
        except Exception:
            _fh = None
            _path = None
        return _path


def write(text):
    """Append one already-formatted block.  Never raises, never blocks long."""
    fh = _fh
    if fh is None:
        return
    try:
        with _lock:
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")
            fh.flush()
    except Exception:
        pass


def line(text):
    """Append one line, stamped the way the window stamps it."""
    write("%s %s" % (time.strftime("%H:%M:%S"), text))


def close():
    global _fh
    with _lock:
        if _fh is not None:
            try:
                _fh.close()
            except Exception:
                pass
            _fh = None
