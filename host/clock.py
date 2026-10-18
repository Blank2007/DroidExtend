"""One clock for the frame timestamps.

Every frame carries "when did these bytes leave ffmpeg"; the phone subtracts the
clock offset it learned at the handshake and reports the difference as latency.

That only works if the host clock never *jumps*.  It used to read
`time.time()`, which is the wall clock: Windows' time service slews it and can
step it outright, and every step shows up on the phone as a sudden change in
"latency" even though nothing about the picture changed.  `time.monotonic()`
never goes backwards and never gets stepped; it does drift against the phone's
own monotonic clock, but that drift is slow and steady, and the phone corrects
it (see `latFloorUs` in StreamClient.java) instead of displaying it as latency.
"""
import time


def now_us():
    """Microseconds on a monotonic clock.  Only differences are meaningful."""
    return int(time.monotonic() * 1000000)
