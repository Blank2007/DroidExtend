"""Host-side motion, kept to the amounts Tk and Windows actually do well.

Windows has real window animation -- AnimateWindow, DWM transitions -- but none
of it is reachable from Tk in a way that survives contact with the event loop:
AnimateWindow blocks the calling thread for its whole duration, and a Tk window
only has a stable top-level HWND once it is mapped, which is after the moment
you would want to animate it.  So the host fades through the layered-window
alpha channel instead, which is the same mechanism underneath and cannot wedge
the UI.

Three things decide whether anything moves at all:

  * the checkbox in the window (``settings["animations"]``),
  * the Windows "show animations" accessibility setting,
  * and whether the window is still alive, because a fade that outlives its
    widget is a TclError in a callback nobody is watching.

When motion is off, every function here still does its job -- it just does it
in one step.  Callers never have to ask.
"""

import tkinter as tk

from i18n import t

#: Settings key.  Stored in the EXTRA block, so it survives as a plain bool.
KEY = "animations"

#: Frames in a fade.  Eight is enough to read as a ramp and short enough that
#: the whole thing finishes inside the time it takes to focus the window.
STEPS = 8

FADE_IN_MS = 170
FADE_OUT_MS = 130

#: How long a control stays lit when it is pressed.
PULSE_MS = 110

_SPI_GETCLIENTAREAANIMATION = 0x1042


def system_allows():
    """Whether Windows itself is willing to animate things.

    This is the "Show animations in Windows" switch in the accessibility
    settings, which is exactly the answer we want: a user who turned that off
    has already told every program on the machine what they think.
    """
    try:
        import ctypes

        value = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            _SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0)
        if not ok:
            return True
        return bool(value.value)
    except Exception:
        # Not being able to ask is not a reason to refuse.
        return True


def allowed(settings=None):
    """Whether this program should animate, ignoring the window's health."""
    if settings is not None and not settings.get(KEY, True):
        return False
    return system_allows()


def _alive(win):
    try:
        return bool(win) and bool(win.winfo_exists())
    except Exception:
        return False


def scaled(ms, factor=1.0):
    """A duration, never longer than the point of the animation."""
    try:
        return max(1, min(int(ms * factor), 400))
    except Exception:
        return 1


def _alpha(win, value):
    try:
        win.attributes("-alpha", value)
        return True
    except tk.TclError:
        return False
    except Exception:
        return False


def _clear(win):
    """Hand the window back fully opaque and free of the attribute."""
    try:
        win.attributes("-alpha", 1.0)
    except Exception:
        pass


def fade_in(win, ms=None, settings=None, on_done=None):
    """Fade a window up from transparent.  Runs ``on_done`` either way."""
    ms = FADE_IN_MS if ms is None else ms
    if not _alive(win) or not allowed(settings):
        _clear(win)
        _call(on_done)
        return
    if not _alpha(win, 0.0):
        _call(on_done)
        return
    delay = max(1, int(scaled(ms) / STEPS))

    def step(i):
        if not _alive(win):
            return
        if i >= STEPS:
            _clear(win)
            _call(on_done)
            return
        if not _alpha(win, i / float(STEPS)):
            _call(on_done)
            return
        try:
            win.after(delay, lambda: step(i + 1))
        except Exception:
            _call(on_done)

    step(1)


def fade_out(win, ms=None, settings=None, on_done=None):
    """Fade a window away, then call ``on_done``.

    ``on_done`` is where a withdraw belongs: hiding the window first would make
    the fade a no-op, because there would be nothing left to see through.
    """
    ms = FADE_OUT_MS if ms is None else ms
    if not _alive(win) or not allowed(settings):
        _call(on_done)
        return
    delay = max(1, int(scaled(ms) / STEPS))

    def step(i):
        if not _alive(win):
            return
        if i <= 0:
            _clear(win)
            _call(on_done)
            return
        if not _alpha(win, i / float(STEPS)):
            _call(on_done)
            return
        try:
            win.after(delay, lambda: step(i - 1))
        except Exception:
            _call(on_done)

    step(STEPS - 1)


def pulse(widget, colour, settings=None, base=None):
    """Light a control up for a moment, the way a button acknowledges a press.

    ``base`` is restored afterwards; pass the widget's ordinary background so a
    pulse cannot leave a control permanently lit.
    """
    if not _alive(widget) or not allowed(settings):
        return
    try:
        original = base if base is not None else widget.cget("background")
    except Exception:
        return
    try:
        widget.configure(background=colour)
    except Exception:
        return

    def back():
        try:
            if widget.winfo_exists():
                widget.configure(background=original)
        except Exception:
            pass

    try:
        widget.after(PULSE_MS, back)
    except Exception:
        back()


def _call(fn):
    if fn is None:
        return
    try:
        fn()
    except Exception:
        pass


def label():
    """The checkbox text."""
    return t("界面动画", "Interface animation")
