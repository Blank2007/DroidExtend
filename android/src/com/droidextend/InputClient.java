package com.droidextend;

import android.util.Log;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;

/**
 * Sends touch events back to the PC over a second adb-reverse tunnel.
 *
 * The video comes host -> phone on 27183; this channel goes phone -> host on
 * 27184, so input never queues behind a keyframe.  Each event is a fixed
 * 7-byte little-endian record:
 *
 *     action uint8   1 down | 2 move | 3 up | 4 scroll | 5 right click
 *     x      uint16  0..65535 across the picture being displayed
 *     y      uint16  0..65535
 *     z      uint16  scroll only: dx in the low half, dy in the high half
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

    private static final int MAX_COORD = 65535;

    /** Bounded so a stalled tunnel can never grow without limit. */
    private static final int MAX_QUEUED = 256;

    private final String host;
    private final int port;
    private final Object lock = new Object();
    private final java.util.ArrayDeque<byte[]> queue = new java.util.ArrayDeque<byte[]>();

    private Socket socket;
    private OutputStream out;
    private volatile boolean running;
    private Thread worker;

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
            byte[] rec = null;
            OutputStream sink = null;
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
                rec = queue.poll();
                if (rec == null) {
                    try {
                        lock.wait(400);
                    } catch (InterruptedException e) {
                        return;
                    }
                    continue;
                }
                sink = out;
            }
            // write outside the lock: the UI thread must never wait on the wire
            try {
                sink.write(rec);
                sink.flush();
            } catch (IOException e) {
                Log.w(TAG, "input write failed: " + e);
                synchronized (lock) {
                    closeQuietly();
                }
            }
        }
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
        byte[] rec = new byte[7];
        rec[0] = (byte) (action & 0xFF);
        rec[1] = (byte) (x & 0xFF);
        rec[2] = (byte) ((x >> 8) & 0xFF);
        rec[3] = (byte) (y & 0xFF);
        rec[4] = (byte) ((y >> 8) & 0xFF);
        rec[5] = (byte) (z & 0xFF);
        rec[6] = (byte) ((z >> 8) & 0xFF);
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

    private static int clamp(float v) {
        int i = Math.round(v * MAX_COORD);
        if (i < 0) {
            return 0;
        }
        return Math.min(i, MAX_COORD);
    }
}
