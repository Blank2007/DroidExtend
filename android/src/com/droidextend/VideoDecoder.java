package com.droidextend;

import android.media.MediaCodec;
import android.media.MediaFormat;
import android.util.Log;
import android.view.Surface;

import java.nio.ByteBuffer;

/**
 * Minimal H.264 Annex-B decoder that renders straight onto a Surface.
 *
 * The first access unit we receive carries SPS/PPS (the host repeats them before
 * every IDR), so we can build csd-0 from the stream itself instead of relying on
 * out-of-band configuration - that keeps us compatible with every vendor decoder.
 */
public class VideoDecoder {

    private static final String TAG = "DroidExtend";
    private static final long DEQ_TIMEOUT_US = 10000L;

    private MediaCodec codec;
    private Surface surface;
    private boolean configured;
    private int width, height, fps;
    private byte[] csd;
    private long frameIndex;
    private boolean inputDone;

    public synchronized void setSurface(Surface s) {
        surface = s;
        if (configured && codec != null && s != null) {
            try {
                codec.setOutputSurface(s);
            } catch (Throwable t) {
                Log.w(TAG, "setOutputSurface failed, reconfiguring: " + t);
                reset();
            }
        }
    }

    public synchronized boolean isConfigured() {
        return configured;
    }

    public synchronized void reset() {
        if (codec != null) {
            try { codec.stop(); } catch (Throwable ignored) { }
            try { codec.release(); } catch (Throwable ignored) { }
        }
        codec = null;
        configured = false;
        csd = null;
        frameIndex = 0;
        inputDone = false;
    }

    private void configure(byte[] csdData) {
        // MediaFormat.KEY_LOW_LATENCY (API 30) tells the vendor decoder not to
        // sit on frames, which is worth real milliseconds on a remote desktop.
        // Some codecs reject the key outright, so if configure() fails with it
        // set we retry once without rather than looping forever.
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                MediaFormat fmt = MediaFormat.createVideoFormat("video/avc", width, height);
                if (csdData != null) {
                    fmt.setByteBuffer("csd-0", ByteBuffer.wrap(csdData));
                }
                fmt.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 4 * 1024 * 1024);
                if (fps > 0) {
                    try { fmt.setFloat(MediaFormat.KEY_FRAME_RATE, fps); } catch (Throwable ignored) { }
                }
                try { fmt.setInteger(MediaFormat.KEY_PRIORITY, 0); } catch (Throwable ignored) { }
                if (attempt == 0) {
                    try { fmt.setInteger(MediaFormat.KEY_LOW_LATENCY, 1); } catch (Throwable ignored) { }
                }
                MediaCodec c = MediaCodec.createDecoderByType("video/avc");
                c.configure(fmt, surface, null, 0);
                c.start();
                codec = c;
                configured = true;
                Log.i(TAG, "decoder configured " + width + "x" + height
                        + " lowLatency=" + (attempt == 0)
                        + " csd=" + (csdData == null ? 0 : csdData.length));
                return;
            } catch (Throwable t) {
                if (attempt == 0) {
                    Log.w(TAG, "configure with low latency failed, retrying plain: " + t);
                } else {
                    Log.e(TAG, "configure failed", t);
                    reset();
                    throw new RuntimeException(t);
                }
            }
        }
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
     * Feed one Annex-B access unit. Returns false when the unit was buffered
     * waiting for SPS/PPS.
     */
    public boolean feed(byte[] data, int offset, int length) {
        synchronized (this) {
            if (!configured) {
                byte[] found = scanCsd(data, offset, length);
                if (found == null) {
                    return false;
                }
                csd = found;
                if (surface == null) {
                    return false;
                }
                configure(csd);
            }
            return queue(data, offset, length);
        }
    }

    private boolean queue(byte[] data, int offset, int length) {
        if (inputDone) {
            return true;
        }
        try {
            int idx = codec.dequeueInputBuffer(DEQ_TIMEOUT_US);
            if (idx < 0) {
                drain();
                idx = codec.dequeueInputBuffer(DEQ_TIMEOUT_US);
            }
            if (idx < 0) {
                // Decoder is saturated: drop this unit rather than stall the socket.
                return true;
            }
            ByteBuffer buf = codec.getInputBuffer(idx);
            if (buf == null) {
                return true;
            }
            buf.clear();
            int n = Math.min(length, buf.capacity());
            buf.put(data, offset, n);
            long pts = frameIndex * 1000000L / Math.max(fps, 1);
            codec.queueInputBuffer(idx, 0, n, pts, 0);
            frameIndex++;
            drain();
            return true;
        } catch (Throwable t) {
            Log.e(TAG, "queue failed", t);
            reset();
            return false;
        }
    }

    private void drain() {
        if (codec == null || !configured) {
            return;
        }
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        while (true) {
            int idx;
            try {
                idx = codec.dequeueOutputBuffer(info, 0);
            } catch (Throwable t) {
                return;
            }
            if (idx >= 0) {
                try {
                    codec.releaseOutputBuffer(idx, true);
                } catch (Throwable ignored) { }
            } else if (idx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                Log.i(TAG, "output format: " + codec.getOutputFormat());
            } else {
                return;
            }
        }
    }

    /** Walk the Annex-B unit looking for SPS(7)+PPS(8) to build csd-0. */
    private byte[] scanCsd(byte[] d, int off, int len) {
        int end = off + len;
        int spsStart = -1, spsEnd = -1, ppsStart = -1, ppsEnd = -1;
        int i = off;
        int nalStart = -1;
        while (i < end - 3) {
            if (d[i] == 0 && d[i + 1] == 0 && d[i + 2] == 1) {
                int sc = 3;
                if (i > off && d[i - 1] == 0) {
                    // 4-byte start code already consumed as part of the previous header
                }
                int hdr = i + sc;
                if (nalStart >= 0 && hdr - sc > nalStart) {
                    int type = d[nalStart] & 0x1F;
                    if (type == 7 && spsStart < 0) { spsStart = nalStart; spsEnd = hdr - sc; }
                    if (type == 8 && ppsStart < 0) { ppsStart = nalStart; ppsEnd = hdr - sc; }
                }
                nalStart = hdr;
                i = hdr + 1;
                continue;
            }
            i++;
        }
        if (nalStart >= 0 && nalStart < end) {
            int type = d[nalStart] & 0x1F;
            if (type == 7 && spsStart < 0) { spsStart = nalStart; spsEnd = end; }
            if (type == 8 && ppsStart < 0) { ppsStart = nalStart; ppsEnd = end; }
        }
        if (spsStart < 0 || ppsStart < 0) {
            return null;
        }
        int n = (spsEnd - spsStart) + (ppsEnd - ppsStart) + 8;
        byte[] out = new byte[n];
        int p = 0;
        out[p++] = 0; out[p++] = 0; out[p++] = 0; out[p++] = 1;
        System.arraycopy(d, spsStart, out, p, spsEnd - spsStart);
        p += spsEnd - spsStart;
        out[p++] = 0; out[p++] = 0; out[p++] = 0; out[p++] = 1;
        System.arraycopy(d, ppsStart, out, p, ppsEnd - ppsStart);
        return out;
    }
}
