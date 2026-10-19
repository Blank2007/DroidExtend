package com.droidextend;

/**
 * One snapshot of "how is the link doing", handed to the overlay.
 *
 * The overlay used to be a TextView fed a pre-formatted string, which meant the
 * number of things it could show was decided by whoever wrote that string.
 * Passing the numbers instead lets the HUD draw them properly.
 */
public final class Stats {

    /** Geometry from the handshake: what the PC is actually sending. */
    public int width;
    public int height;
    public int fps;

    /** Decoded access units per second, measured over the last window. */
    public double streamFps;

    /**
     * The panel's own refresh rate, 0 when it could not be read.
     *
     * `streamFps` is how many frames the PC actually sent and we actually
     * decoded -- it says nothing about the screen.  Showing both is the only way
     * to answer "is this really my phone's refresh rate?" at a glance, and the
     * two are routinely different (60 fps of video on a 120 Hz panel).
     */
    public double screenHz;

    /**
     * Frames the decoder actually handed to the screen per second.
     *
     * `streamFps` counts what arrived; this counts what was shown.  They differ
     * whenever the decoder is saturated and drops an access unit, which is
     * exactly when the picture looks less smooth than the bitrate suggests.
     */
    public double shownFps;

    /** Payload bitrate actually received, in megabits per second. */
    public double mbps;

    /** Encode-to-display latency, averaged over the window; -1 when unknown. */
    public double latencyMs = -1;
    public double latencyMaxMs;

    /** Totals since the connection was established. */
    public long frames;
    public long bytes;

    /** True while the client is discarding packets to catch the next keyframe. */
    public boolean catchingUp;

    /** True once a connection is up and video is flowing. */
    public boolean streaming;

    public Stats copy() {
        Stats s = new Stats();
        s.width = width;
        s.height = height;
        s.fps = fps;
        s.streamFps = streamFps;
        s.screenHz = screenHz;
        s.shownFps = shownFps;
        s.mbps = mbps;
        s.latencyMs = latencyMs;
        s.latencyMaxMs = latencyMaxMs;
        s.frames = frames;
        s.bytes = bytes;
        s.catchingUp = catchingUp;
        s.streaming = streaming;
        return s;
    }
}
