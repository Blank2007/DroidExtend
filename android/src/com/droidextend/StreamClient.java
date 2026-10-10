package com.droidextend;

import android.os.SystemClock;
import android.util.Log;

import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;

/** Connects to the host through `adb reverse`, reads the handshake, pumps access units. */
public class StreamClient extends Thread {

    private static final String TAG = "DroidExtend";
    private static final int MAX_UNIT = 8 * 1024 * 1024;
    // If more than this is sitting in the socket we are behind, and every byte
    // in there is added latency.  768 KB was ~2 s of delay at 3 Mbps; 384 KB
    // is still generous for a momentary burst but caps the wait at a level
    // where jumping to the next keyframe is better than catching up.
    private static final int BACKLOG_DROP_BYTES = 384 * 1024;

    public interface Listener {
        void onStatus(String text, boolean streaming);

        /** Fired once per connection with the geometry from the host handshake. */
        void onVideoSize(int width, int height);
    }

    private final String host;
    private final int port;
    private final VideoDecoder decoder;
    private final Listener listener;
    private volatile boolean running = true;
    private Socket socket;
    private OutputStream control;
    private long clockSkewUs;

    public StreamClient(String host, int port, VideoDecoder decoder, Listener listener) {
        this.host = host;
        this.port = port;
        this.decoder = decoder;
        this.listener = listener;
        setName("droid-extend-stream");
    }

    public void shutdown() {
        running = false;
        try { if (socket != null) socket.close(); } catch (IOException ignored) { }
        interrupt();
    }

    private void status(String s, boolean streaming) {
        if (listener != null) listener.onStatus(s, streaming);
    }

    private void videoSize(int w, int h) {
        if (listener != null) listener.onVideoSize(w, h);
    }

    @Override
    public void run() {
        byte[] unit = new byte[512 * 1024];
        while (running) {
            try {
                connectAndStream(unit);
            } catch (Throwable t) {
                if (running) {
                    status("等待电脑端…", false);
                    Log.w(TAG, "stream ended: " + t);
                }
            } finally {
                decoder.reset();
                closeQuietly();
            }
            if (!running) {
                break;
            }
            try { Thread.sleep(700); } catch (InterruptedException ignored) { }
        }
        status("已停止", false);
    }

    private void closeQuietly() {
        try { if (socket != null) socket.close(); } catch (IOException ignored) { }
        socket = null;
        control = null;
    }

    private void connectAndStream(byte[] unit) throws IOException {
        status("连接 " + host + ":" + port + " …", false);
        Socket s = new Socket();
        s.setTcpNoDelay(true);
        s.setReceiveBufferSize(1024 * 1024);
        s.connect(new InetSocketAddress(host, port), 3000);
        socket = s;
        control = s.getOutputStream();
        InputStream raw = s.getInputStream();
        DataInputStream in = new DataInputStream(raw);

        byte[] magic = new byte[4];
        in.readFully(magic);
        if (magic[0] != 'D' || magic[1] != 'X' || magic[2] != 'S' || magic[3] != '2') {
            throw new IOException("bad handshake " + new String(magic));
        }
        int w = readIntLE(in);
        int h = readIntLE(in);
        int fps = readIntLE(in);
        long hostNowUs = readLongLE(in);
        // The two clocks have a fixed, unknown offset; assume the handshake
        // crossed the cable instantly (<1 ms over adb reverse) and use it to
        // line them up.  Everything after this measures capture -> display.
        clockSkewUs = SystemClock.elapsedRealtimeNanos() / 1000 - hostNowUs;
        decoder.setFormat(w, h, fps);
        videoSize(w, h);
        status(w + "x" + h + " @" + fps + "fps  已连接", true);
        Log.i(TAG, "handshake " + w + "x" + h + "@" + fps + " skew=" + clockSkewUs + "us");

        boolean skipUntilKey = false;
        long frames = 0;
        long lastReport = System.currentTimeMillis();
        long latSum = 0;
        long latMax = 0;
        int latCount = 0;
        while (running) {
            int len = readIntLE(in);
            if (len <= 0 || len > MAX_UNIT) {
                throw new IOException("bad frame length " + len);
            }
            long stampUs = readLongLE(in);
            if (len > unit.length) {
                unit = new byte[len];
            }
            in.readFully(unit, 0, len);

            if (raw.available() > BACKLOG_DROP_BYTES) {
                skipUntilKey = true;
            }
            boolean isKey = isKeyframe(unit, len);
            if (isKey) {
                skipUntilKey = false;
            }
            if (!skipUntilKey) {
                decoder.feed(unit, 0, len);
                frames++;
                long latUs = SystemClock.elapsedRealtimeNanos() / 1000 - clockSkewUs - stampUs;
                if (latUs > 0 && latUs < 5000000L) {
                    latSum += latUs;
                    latCount++;
                    if (latUs > latMax) {
                        latMax = latUs;
                    }
                }
            }

            long now = System.currentTimeMillis();
            if (now - lastReport >= 2000) {
                long secs = Math.max(now - lastReport, 1);
                long avgMs = latCount > 0 ? latSum / latCount / 1000 : -1;
                // This is the number that matters: ms from the frame leaving
                // the encoder on the PC to the decoder handing it to the screen.
                Log.i(TAG, "perf fps=" + (frames * 1000 / secs)
                        + " latency_avg_ms=" + avgMs
                        + " latency_max_ms=" + (latMax / 1000)
                        + " samples=" + latCount
                        + (skipUntilKey ? " (dropping to keyframe)" : ""));
                status(w + "x" + h + "  " + (frames * 1000 / secs) + " fps"
                        + (avgMs >= 0 ? "  延迟 " + avgMs + " ms" : ""), true);
                frames = 0;
                latSum = 0;
                latMax = 0;
                latCount = 0;
                lastReport = now;
            }
        }
    }

    private static boolean isKeyframe(byte[] d, int len) {
        int end = len - 4;
        for (int i = 0; i < end; i++) {
            if (d[i] == 0 && d[i + 1] == 0 && (d[i + 2] == 1 || (d[i + 2] == 0 && d[i + 3] == 1))) {
                int hdr = (d[i + 2] == 1) ? i + 3 : i + 4;
                if (hdr < len) {
                    int type = d[hdr] & 0x1F;
                    if (type == 5 || type == 7) {
                        return true;
                    }
                    if (type == 1) {
                        return false;
                    }
                }
            }
        }
        return false;
    }

    private static int readIntLE(DataInputStream in) throws IOException {
        int b0 = in.read();
        int b1 = in.read();
        int b2 = in.read();
        int b3 = in.read();
        if ((b0 | b1 | b2 | b3) < 0) {
            throw new IOException("eof");
        }
        return b0 | (b1 << 8) | (b2 << 16) | (b3 << 24);
    }

    private static long readLongLE(DataInputStream in) throws IOException {
        long lo = readIntLE(in) & 0xFFFFFFFFL;
        long hi = readIntLE(in) & 0xFFFFFFFFL;
        return lo | (hi << 32);
    }
}
