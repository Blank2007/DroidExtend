package com.droidextend;

import android.media.MediaCodec;
import android.media.MediaFormat;
import android.os.SystemClock;
import android.util.Log;
import android.view.Surface;

import java.nio.ByteBuffer;
import java.util.ArrayDeque;

/**
 * Minimal H.264 Annex-B decoder that renders straight onto a Surface.
 *
 * The first access unit we receive carries SPS/PPS (the host repeats them before
 * every IDR), so we can build csd-0 from the stream itself instead of relying on
 * out-of-band configuration - that keeps us compatible with every vendor decoder.
 */
public class VideoDecoder {

    private static final String TAG = "DroidExtend";
    /**
     * How long one "give me a free input buffer" call may block.
     *
     * This used to be 10 ms, which quietly overrode QUEUE_WAIT_MS: the very
     * first call could sleep ten milliseconds before answering "no room", so a
     * full input queue stalled the socket reader for a whole frame plus change
     * on every frame.  One millisecond answers the same question and leaves the
     * wait budget to QUEUE_WAIT_MS.
     */
    private static final long DEQ_TIMEOUT_US = 1000L;

    /** How long the drain thread sleeps inside the codec when no picture is ready. */
    private static final long DEQ_OUTPUT_US = 5000L;

    /**
     * How long to keep asking for an input buffer before giving up on this
     * frame -- 2 ms, not 40.
     *
     * This number used to be 40 ms, and it was the engine of the drift that
     * made the overlay climb from 15 ms to 43 ms over one session: whenever the
     * decoder's input queue was full the socket reader slept for 40 ms, the
     * host kept writing five more frames in that time, the TCP buffer filled up
     * with frames nobody had read yet, and the next frame was older still.  The
     * wait did not make the decoder faster, it only moved the backlog from the
     * decoder into the socket, where nothing ever drained it.  Two milliseconds
     * is under a quarter of a 120 Hz frame: long enough to ride out the
     * codec's own thread hand-off, short enough that a full queue costs less
     * than a frame and the reader keeps up with the wire.
     */
    private static final long QUEUE_WAIT_MS = 2L;

    /**
     * Where decoder events go so the host can write them into its own log.
     *
     * The phone has no way to hand a log file to the user, and the log is the
     * only thing that says why a Huawei tablet showed nothing but a black
     * rectangle while the same app on a Xiaomi phone was fine.  Implemented by
     * StreamClient, which puts the text on the uplink.
     */
    public interface Sink {
        void onEvent(String msg);
    }

    private MediaCodec codec;
    private Surface surface;
    private boolean configured;
    private int width, height, fps;
    private byte[] csd;
    private long frameIndex;
    /** Frames actually handed to the surface.  See renderedFrames(). */
    private long rendered;
    private boolean inputDone;
    private volatile Sink sink;
    // Only said once per decoder, so a stream that never resynchronises does
    // not turn the uplink into a flood.
    private boolean warnedNoCsd, warnedNoSurface, warnedFirstFrame;
    private boolean warnedQueueFull;

    /** Host clock to this process's elapsedRealtime, measured once at the handshake. */
    private long clockSkewUs;
    /**
     * Host timestamps of the frames handed to the decoder, oldest first.
     *
     * H.264 without B frames comes out in the order it went in, so the Nth
     * output buffer belongs to the Nth queued stamp and a plain queue is
     * enough.  Keeping them lets the overlay report the delay that actually
     * matters -- host put the frame on the wire -> it is on the glass.
     */
    private final ArrayDeque<Long> stamps = new ArrayDeque<Long>();
    /** When each of those frames finished arriving here, same order. */
    private final ArrayDeque<Long> recvs = new ArrayDeque<Long>();
    /** Last measured wire-to-glass delay in microseconds; -1 until one frame lands. */
    private long shownLatencyUs = -1;
    /** The two halves of that delay: host -> here, and here -> glass. */
    private long netUs = -1;
    private long decodeUs = -1;
    private long shownLatencyMaxUs;

    /**
     * Rolling window of the last frames' wire-to-glass delay.
     *
     * The old log line printed the *last* sample and called it an average,
     * which made a 3 ms improvement and a 30 ms regression look identical.  A
     * window keeps the number comparable between builds, and the percentiles
     * say what the tail is doing, which is what a remote desktop feels.
     */
    private static final int LAT_WINDOW = 256;
    private final long[] latWin = new long[LAT_WINDOW];
    private int latWinN;
    /** Output buffers thrown away because several were ready for one vsync. */
    private long droppedOutput;

    /**
     * True while every unit except the next IDR is thrown away.
     *
     * Set when this process falls behind (see skipToNextKey).  Nothing but an
     * IDR may be fed while it is set: the P frames around it reference pictures
     * the codec never decoded, and feeding them would paint garbage.
     */
    private boolean awaitKey;
    /** HEVC instead of H.264: different NAL header, different csd layout. */
    private boolean hevc;
    /** Units thrown away while waiting for that IDR, for the log line. */
    private long skippedUnits;
    /** Consecutive "input queue full" answers; three in a row means we are behind. */
    private int queueFullRun;

    /**
     * The thread that takes decoded pictures off the codec and onto the surface.
     *
     * Until 2.0.0 this was done from inside the socket reader, right after
     * feeding the *next* frame -- which meant a picture that became ready 0.1 ms
     * after the last feed sat in the codec until the following frame arrived, a
     * whole frame interval later.  Measured on the 120 Hz tablet that was
     * dec=12-13 ms for a decoder that needs 4 ms: 8.33 ms was pure waiting.
     * A thread of its own collects each picture as soon as it exists.
     */
    private Thread drainThread;
    private volatile boolean draining;

    /**
     * How many frames have really been put on screen.
     *
     * This is not the same as frames fed in: the decoder drops an access unit
     * when its input queue is full, and a saturated decoder produces fewer
     * pictures than it was given.  The overlay reports *this* number, because
     * "what the phone shows per second" is the question people actually ask --
     * `streamFps` (frames received) can be higher than what is displayed.
     */
    public synchronized long renderedFrames() {
        return rendered;
    }

    /**
     * Throw the queued pictures away and wait for the next IDR.
     *
     * The host writes straight to the socket, so when this process stops reading
     * for a moment -- the window was hidden, the system stalled, the codec got
     * stuck -- every frame not read stays in the TCP buffer, and all of it is
     * old.  Reading it back one frame at a time can never catch up: the encoder
     * keeps producing at the same rate we consume, so the delay stays as large
     * as the stall was.  That is the "latency jumps to a second and stays
     * there" failure; before, the only way out was to reconnect.
     *
     * Skipping to the next IDR costs one GOP of frozen picture and then the
     * delay is back to a frame interval.  One second without new pictures beats
     * a second of pictures that are already a second old.
     */
    public synchronized void skipToNextKey(String why) {
        if (awaitKey) {
            return;
        }
        awaitKey = true;
        try {
            if (codec != null) {
                codec.flush();
            }
        } catch (Throwable t) {
            Log.w(TAG, "flush after falling behind failed, resetting: " + oneLine(t));
            reset();
        }
        stamps.clear();
        recvs.clear();
        // Samples taken before the jump describe a pipeline that no longer
        // exists; without this the overlay keeps showing the old second long
        // after the picture is fast again.
        latWinN = 0;
        shownLatencyUs = -1;
        if (codec != null) {
            startDrain();       // the old drain thread may have exited on the flush
        }
        event("behind (" + why + "), skipping to the next keyframe");
    }

    /**
     * Forget the latency samples collected so far.  Used when the clock offset
     * is corrected mid-stream: the older samples were taken against the old
     * offset and would keep the median wrong for a couple of seconds.
     */
    public synchronized void clearLatency() {
        latWinN = 0;
        shownLatencyUs = -1;
        shownLatencyMaxUs = 0;
    }

    /** Units thrown away while waiting for a keyframe. */
    public synchronized long skipped() {
        return skippedUnits;
    }

    /** Is this access unit an IDR?  The only unit allowed after a flush. */
    private boolean isKeyUnit(byte[] d, int off, int len) {
        int end = off + len;
        int i = off;
        int hlen = hevc ? 2 : 1;
        while (i < end - 3) {
            if (d[i] == 0 && d[i + 1] == 0 && d[i + 2] == 1) {
                int hdr = i + 3;
                int type = nalType(d, hdr, end);
                // H.264: IDR slice (5).  HEVC: any IRAP picture, BLA/IDR/CRA
                // (16..21) -- all of them start a fresh reference chain.
                if (hevc ? (type >= 16 && type <= 21) : type == 5) {
                    return true;
                }
                i = hdr + hlen;
            } else {
                i++;
            }
        }
        return false;
    }

    /** Line the host clock up with this process's clock (called after the handshake). */
    public synchronized void setClockSkew(long skewUs) {
        clockSkewUs = skewUs;
    }

    /**
     * Delay from the frame leaving the host to the frame reaching the surface,
     * in milliseconds; -1 while no frame has landed yet.
     */
    public synchronized double shownLatencyMs() {
        return shownLatencyUs < 0 ? -1 : shownLatencyUs / 1000.0;
    }

    /** Median wire-to-glass delay over the last few hundred frames (ms). */
    public synchronized double latencyP50Ms() {
        return percentileMs(50);
    }

    /** 90th percentile of the same window: what the stutter feels like (ms). */
    public synchronized double latencyP90Ms() {
        return percentileMs(90);
    }

    private double percentileMs(int p) {
        int n = Math.min(latWinN, LAT_WINDOW);
        if (n <= 0) {
            return shownLatencyUs < 0 ? -1 : shownLatencyUs / 1000.0;
        }
        long[] a = new long[n];
        System.arraycopy(latWin, 0, a, 0, n);
        java.util.Arrays.sort(a);
        int i = (int) Math.round((n - 1) * p / 100.0);
        if (i < 0) {
            i = 0;
        }
        if (i >= n) {
            i = n - 1;
        }
        return a[i] / 1000.0;
    }

    /** Take the worst wire-to-glass delay since the last call (ms), then reset it. */
    public synchronized double takeShownLatencyMaxMs() {
        double v = shownLatencyMaxUs / 1000.0;
        shownLatencyMaxUs = 0;
        return v;
    }

    /** Time from the host putting the frame on the wire to it arriving here (ms). */
    public synchronized double transferMs() {
        return netUs < 0 ? -1 : netUs / 1000.0;
    }

    /** Time from the frame arriving here to it reaching the surface (ms). */
    public synchronized double decodeMs() {
        return decodeUs < 0 ? -1 : decodeUs / 1000.0;
    }

    /** Output frames dropped because several were ready at once. */
    public synchronized long droppedOutputFrames() {
        return droppedOutput;
    }

    /** Route decoder events to the host's log (null to stop). */
    public void setSink(Sink s) {
        sink = s;
    }

    public void logEvent(String msg) {
        event(msg);
    }

    private void event(String msg) {
        Log.i(TAG, msg);
        Sink s = sink;
        if (s != null) {
            try {
                s.onEvent(msg);
            } catch (Throwable ignored) {
                // The uplink is a diagnostic, never a reason to fail.
            }
        }
    }

    public synchronized void setSurface(Surface s) {
        if (s == null) {
            // The window is going away, and the codec's output surface dies with
            // it.  Until 2.0.0 the codec was left running here, so the socket
            // reader kept feeding a decoder whose surface no longer existed and
            // the app died with "Invalid to call at Released state" inside
            // queue().  Tear it down; a new surface reconfigures it.
            reset();
            surface = null;
            return;
        }
        surface = s;
        if (configured && codec != null && s != null) {
            try {
                codec.setOutputSurface(s);
                event("decoder surface replaced, still " + width + "x" + height);
            } catch (Throwable t) {
                event("setOutputSurface failed, reconfiguring: " + oneLine(t));
                reset();
            }
        }
    }

    public synchronized boolean isConfigured() {
        return configured;
    }

    public synchronized void reset() {
        // Clear the flag first: the drain thread exits on its own within one
        // dequeue timeout (5 ms) and must not touch a codec we are about to
        // release.  No join() -- this method holds the monitor the drain thread
        // needs to record its latency samples.
        draining = false;
        drainThread = null;
        if (codec != null) {
            try { codec.stop(); } catch (Throwable ignored) { }
            try { codec.release(); } catch (Throwable ignored) { }
        }
        codec = null;
        configured = false;
        csd = null;
        frameIndex = 0;
        rendered = 0;
        inputDone = false;
        stamps.clear();
        recvs.clear();
        shownLatencyUs = -1;
        netUs = -1;
        decodeUs = -1;
        shownLatencyMaxUs = 0;
    }

    private void configure(byte[] csdData) {
        // MediaFormat.KEY_LOW_LATENCY (API 30) tells the vendor decoder not to
        // sit on frames, which is worth real milliseconds on a remote desktop.
        // Some codecs reject the key outright, so if configure() fails with it
        // set we retry once without rather than looping forever.
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                MediaFormat fmt = MediaFormat.createVideoFormat(mime(), width, height);
                if (csdData != null) {
                    fmt.setByteBuffer("csd-0", ByteBuffer.wrap(csdData));
                }
                fmt.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 4 * 1024 * 1024);
                if (fps > 0) {
                    try { fmt.setFloat(MediaFormat.KEY_FRAME_RATE, fps); } catch (Throwable ignored) { }
                    // Tell the decoder how fast frames really arrive.  A vendor
                    // codec that believes it is playing a 30 fps file may clock
                    // itself down; naming the real rate keeps it at full speed.
                    try { fmt.setInteger("operating-rate", fps); } catch (Throwable ignored) { }
                }
                try { fmt.setInteger(MediaFormat.KEY_PRIORITY, 0); } catch (Throwable ignored) { }
                if (attempt == 0) {
                    try { fmt.setInteger(MediaFormat.KEY_LOW_LATENCY, 1); } catch (Throwable ignored) { }
                }
                MediaCodec c = MediaCodec.createDecoderByType(mime());
                c.configure(fmt, surface, null, 0);
                c.start();
                codec = c;
                configured = true;
                startDrain();
                event("decoder configured " + mime() + " " + width + "x" + height
                        + " lowLatency=" + (attempt == 0)
                        + " csd=" + (csdData == null ? 0 : csdData.length));
                return;
            } catch (Throwable t) {
                if (attempt == 0) {
                    Log.w(TAG, "configure with low latency failed, retrying plain: " + t);
                } else {
                    Log.e(TAG, "configure failed", t);
                    event("decoder configure FAILED for " + width + "x" + height
                            + ": " + oneLine(t));
                    reset();
                    throw new RuntimeException(t);
                }
            }
        }
    }

    /**
     * Which codec the host is sending.  Called from the handshake before the
     * first access unit arrives, so the decoder is opened with the matching
     * MIME type instead of always guessing H.264.
     */
    public synchronized void setCodec(boolean isHevc) {
        if (hevc == isHevc) {
            return;
        }
        hevc = isHevc;
        // reset() drops the configured codec; the next access unit rebuilds it
        // with the new MIME type.
        reset();
    }

    private String mime() {
        return hevc ? "video/hevc" : "video/avc";
    }

    /** NAL unit type, H.264 or HEVC depending on the stream. */
    private int nalType(byte[] d, int hdr, int end) {
        if (hevc) {
            if (hdr + 1 >= end) {
                return -1;
            }
            return (d[hdr] >> 1) & 0x3F;
        }
        if (hdr >= end) {
            return -1;
        }
        return d[hdr] & 0x1F;
    }

    /** Announce the stream geometry (from the host handshake). */
    public synchronized void setFormat(int w, int h, int f) {
        if (w != width || h != height) {
            width = w;
            height = h;
            reset();
        }
        fps = f;
    }

    /**
     * Feed one Annex-B access unit. Returns false when the unit could not be
     * taken yet: still waiting for SPS/PPS, no surface, or no free input
     * buffer at this instant (the caller waits briefly and tries the next
     * frame instead, which pushes the delay back up the socket).
     */
    public boolean feed(byte[] data, int offset, int length, long hostStampUs, long recvUs) {
        synchronized (this) {
            if (!configured) {
                byte[] found = scanCsd(data, offset, length);
                if (found == null) {
                    if (!warnedNoCsd) {
                        warnedNoCsd = true;
                        event("first access unit has no SPS/PPS yet, waiting for a keyframe");
                    }
                    return false;
                }
                csd = found;
                if (surface == null) {
                    // Silent before: a decoder that never got a surface simply
                    // threw every frame away and said nothing, which looks
                    // exactly like a broken link from the PC.
                    if (!warnedNoSurface) {
                        warnedNoSurface = true;
                        event("no surface yet, dropping frames");
                    }
                    return false;
                }
                configure(csd);
            }
            return queue(data, offset, length, hostStampUs, recvUs);
        }
    }

    private boolean queue(byte[] data, int offset, int length, long hostStampUs, long recvUs) {
        if (inputDone) {
            return true;
        }
        if (awaitKey) {
            if (!isKeyUnit(data, offset, length)) {
                skippedUnits++;
                return true;        // thrown away on purpose, nothing is wrong
            }
            awaitKey = false;
            queueFullRun = 0;
        }
        try {
            int idx = -1;
            long deadline = SystemClock.elapsedRealtime() + QUEUE_WAIT_MS;
            while (idx < 0) {
                idx = codec.dequeueInputBuffer(DEQ_TIMEOUT_US);
                if (idx >= 0) {
                    break;
                }
                if (drainThread == null) {
                    drain();        // no drain thread yet: keep it alive by hand
                }
                if (SystemClock.elapsedRealtime() >= deadline) {
                    break;
                }
            }
            if (idx < 0) {
                // The decoder cannot take another frame right now.  It used to
                // be dropped here, and that is the one thing you must not do to
                // an H.264 stream: every P frame references the one before it,
                // so a hole in the chain smears the picture until the next IDR.
                // Reporting "not taken" makes the caller wait a moment instead,
                // and that wait travels back up the socket to the host's
                // encoder -- where dropping a frame before encoding is free.
                if (!warnedQueueFull) {
                    warnedQueueFull = true;
                    event("decoder input queue full, waiting for it to drain");
                }
                // Three refusals in a row is not a burst, it is a decoder that
                // cannot keep up.  Waiting longer only grows the backlog, so
                // jump to the next IDR and start again.
                if (++queueFullRun >= 3) {
                    queueFullRun = 0;
                    skipToNextKey("input queue full");
                }
                return false;
            }
            ByteBuffer buf = codec.getInputBuffer(idx);
            if (buf == null) {
                return false;
            }
            buf.clear();
            int n = Math.min(length, buf.capacity());
            buf.put(data, offset, n);
            long pts = frameIndex * 1000000L / Math.max(fps, 1);
            codec.queueInputBuffer(idx, 0, n, pts, 0);
            queueFullRun = 0;
            frameIndex++;
            stamps.addLast(hostStampUs);
            recvs.addLast(recvUs);
            if (stamps.size() > 480) {
                // Way more stamps than frames on screen: the pairing is lost,
                // so start over rather than report a made-up delay.
                stamps.clear();
                recvs.clear();
            }
            // Nothing to collect here any more: the drain thread takes each
            // picture the moment the codec produces it (see drainLoop).
            return true;
        } catch (Throwable t) {
            Log.e(TAG, "queue failed", t);
            reset();
            return false;
        }
    }

    /** Start (or restart) the thread that moves decoded pictures to the surface. */
    private void startDrain() {
        draining = false;
        Thread old = drainThread;
        if (old != null && old.isAlive()) {
            try { old.join(100); } catch (InterruptedException ignored) { }
        }
        draining = true;
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                drainLoop();
            }
        }, "droidextend-drain");
        t.setDaemon(true);
        drainThread = t;
        t.start();
    }

    /**
     * Hand each decoded picture to the surface the instant it exists.
     *
     * The 5 ms timeout is the idle cost: the thread sleeps inside the codec, so
     * this is a blocking wait, not a poll loop.  When several pictures are ready
     * at once they are all already late -- one vsync can show one picture -- so
     * the older ones are released without rendering and counted in
     * droppedOutput, the same way a real-time player skips to the newest frame.
     */
    private void drainLoop() {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        MediaCodec.BufferInfo spare = new MediaCodec.BufferInfo();
        while (draining) {
            MediaCodec c = codec;
            if (c == null) {
                break;
            }
            int idx;
            try {
                idx = c.dequeueOutputBuffer(info, DEQ_OUTPUT_US);
            } catch (Throwable t) {
                if (draining) {
                    Log.w(TAG, "dequeueOutputBuffer stopped: " + oneLine(t));
                }
                break;
            }
            if (idx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                try {
                    event("decoder output format: " + c.getOutputFormat());
                } catch (Throwable ignored) { }
                continue;
            }
            if (idx < 0) {
                continue;
            }
            int newest = idx;
            while (true) {
                int extra;
                try {
                    extra = c.dequeueOutputBuffer(spare, 0);
                } catch (Throwable t) {
                    break;
                }
                if (extra == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    continue;
                }
                if (extra < 0) {
                    break;
                }
                showOne(c, newest, false);
                newest = extra;
            }
            showOne(c, newest, true);
            boolean first;
            synchronized (this) {
                first = (rendered == 1 && !warnedFirstFrame);
                if (first) {
                    warnedFirstFrame = true;
                }
            }
            if (first) {
                event("first frame rendered to the surface");
            }
        }
    }

    /** Release one decoded buffer, rendering it or throwing it away. */
    private void showOne(MediaCodec c, int idx, boolean render) {
        try {
            c.releaseOutputBuffer(idx, render);
        } catch (Throwable ignored) {
            return;
        }
        long stamp, recv;
        synchronized (this) {
            stamp = pollStamp();
            recv = pollRecv();
            if (render) {
                rendered++;
            } else {
                droppedOutput++;
            }
        }
        if (render) {
            noteShown(stamp, recv);
        }
    }
    private void drain() {
        if (codec == null || !configured) {
            return;
        }
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        int last = -1;
        while (true) {
            int idx;
            try {
                idx = codec.dequeueOutputBuffer(info, 0);
            } catch (Throwable t) {
                break;
            }
            if (idx >= 0) {
                if (last >= 0) {
                    // Several pictures became ready since the last drain.  Show
                    // them all and let the compositor pace them against vsync:
                    // at 120 Hz a 120 fps stream fills exactly one frame per
                    // refresh, so throwing the older ones away would halve what
                    // the panel actually gets to show.
                    try {
                        codec.releaseOutputBuffer(last, true);
                        rendered++;
                        noteShown(pollStamp(), pollRecv());
                    } catch (Throwable ignored) { }
                }
                last = idx;
            } else if (idx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                event("decoder output format: " + codec.getOutputFormat());
            } else {
                break;
            }
        }
        if (last < 0) {
            return;
        }
        try {
            codec.releaseOutputBuffer(last, true);
            rendered++;
            noteShown(pollStamp(), pollRecv());
            if (rendered == 1 && !warnedFirstFrame) {
                warnedFirstFrame = true;
                event("first frame rendered to the surface");
            }
        } catch (Throwable ignored) { }
    }

    /** Host timestamp of the frame that was just shown; 0 when unknown. */
    private long pollStamp() {
        Long s = stamps.pollFirst();
        return s == null ? 0L : s.longValue();
    }

    private long pollRecv() {
        Long r = recvs.pollFirst();
        return r == null ? 0L : r.longValue();
    }

    private synchronized void noteShown(long hostStampUs, long recvUs) {
        if (hostStampUs <= 0) {
            return;
        }
        long nowUs = SystemClock.elapsedRealtimeNanos() / 1000L - clockSkewUs;
        long lat = nowUs - hostStampUs;
        if (lat > 10L * 1000 * 1000) {
            return;     // the two clocks clearly disagree; do not report it
        }
        if (lat < -2000L) {
            return;     // more than 2 ms of clock offset is not a measurement
        }
        if (lat < 0) {
            lat = 0;    // sub-millisecond offset: StreamClient re-zeros it anyway
        }
        shownLatencyUs = lat;
        if (lat > 0) {
            latWin[latWinN % LAT_WINDOW] = lat;
            latWinN++;
        }
        if (lat > shownLatencyMaxUs) {
            shownLatencyMaxUs = lat;
        }
        if (recvUs > 0) {
            // The clock-skew correction eats whatever is left of a sub-millisecond
            // network hop, which showed up as "net_ms=-1" in the log.  A negative
            // transfer time is not a measurement, it is a rounding artefact.
            netUs = Math.max(recvUs - hostStampUs, 0);
            decodeUs = Math.max(nowUs - recvUs, 0);
        }
    }

    /** One line of an exception, for a log the user can actually send us. */
    private static String oneLine(Throwable t) {
        String m = t.getClass().getSimpleName() + ": " + t.getMessage();
        m = m.replace('\n', ' ').replace('\r', ' ');
        return m.length() > 120 ? m.substring(0, 120) : m;
    }

    /**
     * Walk the Annex-B unit looking for the parameter sets MediaCodec needs in
     * csd-0: SPS(7)+PPS(8) for H.264, VPS(32)+SPS(33)+PPS(34) for HEVC.
     */
    private byte[] scanCsd(byte[] d, int off, int len) {
        int end = off + len;
        int hlen = hevc ? 2 : 1;
        // Wanted NAL types, in the order they have to appear in csd-0.
        int[] want = hevc ? new int[]{32, 33, 34} : new int[]{7, 8};
        int[] starts = new int[want.length];
        int[] ends = new int[want.length];
        for (int k = 0; k < want.length; k++) {
            starts[k] = -1;
            ends[k] = -1;
        }
        int i = off;
        int nalStart = -1;
        while (i < end - 3) {
            if (d[i] == 0 && d[i + 1] == 0 && d[i + 2] == 1) {
                int hdr = i + 3;
                if (nalStart >= 0 && hdr > nalStart) {
                    int type = nalType(d, nalStart, end);
                    for (int k = 0; k < want.length; k++) {
                        if (type == want[k] && starts[k] < 0) {
                            starts[k] = nalStart;
                            ends[k] = i;
                        }
                    }
                }
                nalStart = hdr;
                i = hdr + hlen;
                continue;
            }
            i++;
        }
        if (nalStart >= 0 && nalStart < end) {
            int type = nalType(d, nalStart, end);
            for (int k = 0; k < want.length; k++) {
                if (type == want[k] && starts[k] < 0) {
                    starts[k] = nalStart;
                    ends[k] = end;
                }
            }
        }
        // SPS and PPS are mandatory; a missing VPS is tolerated (some encoders
        // only repeat it in-band), so csd-0 simply starts at the SPS.
        int first = hevc ? 1 : 0;
        for (int k = first; k < want.length; k++) {
            if (starts[k] < 0) {
                return null;
            }
        }
        int n = 0;
        for (int k = 0; k < want.length; k++) {
            if (starts[k] >= 0) {
                n += 4 + (ends[k] - starts[k]);
            }
        }
        byte[] out = new byte[n];
        int p = 0;
        for (int k = 0; k < want.length; k++) {
            if (starts[k] < 0) {
                continue;
            }
            out[p++] = 0; out[p++] = 0; out[p++] = 0; out[p++] = 1;
            System.arraycopy(d, starts[k], out, p, ends[k] - starts[k]);
            p += ends[k] - starts[k];
        }
        return out;
    }
}
