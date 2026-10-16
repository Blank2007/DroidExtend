package com.droidextend;

import android.util.Log;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;

/**
 * Sends touch events back to the PC over a second adb-reverse tunnel.
 *
 * The video comes host -> phone on 27183; this channel goes phone -> host on
 * 27184, so input never queues behind a keyframe.  Each event is a fixed
 * 9-byte little-endian record:
 *
 *     action uint8   1 down | 2 move | 3 up | 4 scroll | 5 right click | 6 notice
 *     x      uint16  0..65535 across the picture being displayed
 *     y      uint16  0..65535
 *     z      uint32  scroll only: signed dx low half, signed dy high half
 *                    (notice: x carries the event code, see SCREEN_OFF below)
 *
 * Everything here is best-effort: if the PC is not listening yet (or the app
 * started before 投屏 did) the sender just reconnects in the background and
 * touch silently does nothing until it lands.  Input must never be able to
 * stall the video thread.
 */
public class InputClient {

    private static final String TAG = "DroidExtend";

    public static final int PORT = 27184;
    public static final int DOWN = 1;
    public static final int MOVE = 2;
    public static final int UP = 3;
    public static final int SCROLL = 4;
    public static final int RCLICK = 5;
    /** Not input at all: the phone telling the PC something happened to it. */
    public static final int NOTICE = 6;

    public static final int SCREEN_OFF = 1;
    public static final int SCREEN_ON = 2;
    public static final int APP_GONE = 3;

    private static final int MAX_COORD = 65535;

    /** Bounded so a stalled tunnel can never grow without limit. */
    private static final int MAX_QUEUED = 256;

    /**
     * How often a position update may go out.
     *
     * Every write into the adb tunnel is a round of forwarding on the phone
     * side, and an unpaced finger produces one MOVE per touch sample -- a
     * 120 Hz panel while dragging emits those faster than the picture that
     * shows the result can be captured, encoded and sent back.  A position
     * faster than the next frame is work the person holding the phone never
     * sees, and it is not free: the old build sent about 91 writes a second
     * where this one sends about 60.
     *
     * 16 ms is one frame at 60 fps, which is what the capture side runs at.
     * Positions are idempotent -- only the newest one matters -- so they are
     * paced and the intermediate ones are dropped; events (down, up, right
     * click, scroll) are not, so they go out at once.
     */
    private static final long MOVE_INTERVAL_MS = 16;

    private final String host;
    private final int port;
    private final Object lock = new Object();
    private final java.util.ArrayDeque<byte[]> queue = new java.util.ArrayDeque<byte[]>();

    private Socket socket;
    private OutputStream out;
    private volatile boolean running;
    private Thread worker;

    /** Newest position that has not gone out yet.  Guarded by {@link #lock}. */
    private byte[] pendingMove;
    /** When a position update last left.  Guarded by {@link #lock}. */
    private long lastMoveAt;

    public InputClient(String host, int port) {
        this.host = host;
        this.port = port;
    }

    public void start() {
        if (running) {
            return;
        }
        running = true;
        worker = new Thread(new Runnable() {
            @Override public void run() {
                loop();
            }
        }, "droidextend-input");
        worker.setDaemon(true);
        worker.start();
    }

    public void stop() {
        running = false;
        synchronized (lock) {
            closeQuietly();
            lock.notifyAll();
        }
    }

    private void closeQuietly() {
        try {
            if (out != null) {
                out.close();
            }
        } catch (Throwable ignored) { }
        try {
            if (socket != null) {
                socket.close();
            }
        } catch (Throwable ignored) { }
        out = null;
        socket = null;
    }

    private void loop() {
        while (running) {
            byte[] batch = null;
            OutputStream sink = null;
            int records = 0;
            synchronized (lock) {
                if (socket == null) {
                    connect();
                }
                if (socket == null) {
                    try {
                        lock.wait(700);
                    } catch (InterruptedException e) {
                        return;
                    }
                    continue;
                }
                long now = System.currentTimeMillis();
                boolean moveDue = pendingMove != null
                        && now - lastMoveAt >= MOVE_INTERVAL_MS;
                if (queue.isEmpty() && !moveDue) {
                    try {
                        lock.wait(pendingMove == null
                                ? 400
                                : Math.max(1, MOVE_INTERVAL_MS - (now - lastMoveAt)));
                    } catch (InterruptedException e) {
                        return;
                    }
                    continue;
                }
                batch = drainLocked(moveDue);
                if (batch == null) {
                    try {
                        lock.wait(MOVE_INTERVAL_MS);
                    } catch (InterruptedException e) {
                        return;
                    }
                    continue;
                }
                records = batch.length / 9;
                sink = out;
            }
            // write outside the lock: the UI thread must never wait on the wire
            long t0 = System.nanoTime();
            try {
                sink.write(batch);
                sink.flush();
            } catch (IOException e) {
                Log.w(TAG, "input write failed: " + e);
                synchronized (lock) {
                    closeQuietly();
                }
                continue;
            }
            long ms = (System.nanoTime() - t0) / 1000000L;
            if (ms >= 15) {
                // A slow write is the whole reason this class batches: it means
                // the tunnel is the bottleneck, not us, and the batch size is
                // what keeps the cursor from drifting behind the finger.
                Log.w(TAG, "input write slow: " + ms + " ms for " + records
                        + " record(s)");
            }
        }
    }

    /**
     * Take what is waiting and hand it back as one buffer.
     *
     * Runs of MOVE collapse to the newest one, because a move carries an
     * absolute position: every earlier position in the same batch is already
     * stale, and the PC would only re-inject it while the next batch waits.
     * Everything else -- down, up, scroll, right click, notice -- is an event,
     * where dropping one changes what the PC does, so all of those are kept,
     * in order.
     *
     * A position is only emitted when it is due, or as a free passenger on a
     * batch that is going out anyway.
     *
     * Caller must hold {@link #lock}.
     */
    private byte[] drainLocked(boolean moveDue) {
        ByteArrayOutputStream out = null;
        while (!queue.isEmpty()) {
            byte[] rec = queue.poll();
            if (rec[0] == MOVE) {
                pendingMove = rec;              // newest position wins
                continue;
            }
            if (out == null) {
                out = new ByteArrayOutputStream(64);
            }
            if (pendingMove != null) {
                out.write(pendingMove, 0, 9);
                pendingMove = null;
                lastMoveAt = System.currentTimeMillis();
            }
            out.write(rec, 0, 9);
        }
        if (pendingMove != null && (moveDue || out != null)) {
            if (out == null) {
                out = new ByteArrayOutputStream(9);
            }
            out.write(pendingMove, 0, 9);
            pendingMove = null;
            lastMoveAt = System.currentTimeMillis();
        }
        return out == null ? null : out.toByteArray();
    }

    private void connect() {
        try {
            Socket s = new Socket();
            s.setTcpNoDelay(true);
            s.connect(new InetSocketAddress(host, port), 1200);
            socket = s;
            out = s.getOutputStream();
            Log.i(TAG, "input channel connected to " + host + ":" + port);
        } catch (IOException e) {
            closeQuietly();
        }
    }

    /** Called from the UI thread: enqueue only, never touch the socket here. */
    private void send(int action, int x, int y, int z) {
        byte[] rec = new byte[9];
        rec[0] = (byte) (action & 0xFF);
        rec[1] = (byte) (x & 0xFF);
        rec[2] = (byte) ((x >> 8) & 0xFF);
        rec[3] = (byte) (y & 0xFF);
        rec[4] = (byte) ((y >> 8) & 0xFF);
        rec[5] = (byte) (z & 0xFF);
        rec[6] = (byte) ((z >> 8) & 0xFF);
        rec[7] = (byte) ((z >> 16) & 0xFF);
        rec[8] = (byte) ((z >> 24) & 0xFF);
        synchronized (lock) {
            if (queue.size() >= MAX_QUEUED) {
                queue.poll();
            }
            queue.add(rec);
            lock.notifyAll();
        }
    }

    /** nx, ny are 0..1 inside the video rectangle the phone is showing. */
    public void touch(int action, float nx, float ny) {
        send(action, clamp(nx), clamp(ny), 0);
    }

    public void scroll(float dx, float dy) {
        int ix = (int) Math.max(-32768, Math.min(32767, dx)) & 0xFFFF;
        int iy = (int) Math.max(-32768, Math.min(32767, dy)) & 0xFFFF;
        send(SCROLL, 0, 0, ix | (iy << 16));
    }

    /**
     * Tell the PC something happened to us.
     *
     * Best-effort like everything else here: if the screen is going off the
     * write may not make it out, which is exactly why the PC also has a
     * watchdog rather than trusting this to always arrive.
     */
    public void notice(int code) {
        send(NOTICE, code, 0, 0);
    }

    /**
     * Wait for the queue to drain, so a notice queued just before shutdown has a
     * chance to reach the wire.  stop() closes the socket outright, which would
     * otherwise throw the last record away -- and the last record is the one
     * saying we are going away.
     */
    public void flush(long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        synchronized (lock) {
            while (!queue.isEmpty() && System.currentTimeMillis() < deadline) {
                try {
                    lock.wait(30);
                } catch (InterruptedException e) {
                    return;
                }
            }
        }
    }

    private static int clamp(float v) {
        int i = Math.round(v * MAX_COORD);
        if (i < 0) {
            return 0;
        }
        return Math.min(i, MAX_COORD);
    }
}
