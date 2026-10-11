package com.droidextend;

import android.content.Context;
import android.util.Log;

import java.io.File;
import java.io.FileWriter;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Logging that the user can switch off.
 *
 * Logcat is always written -- it costs nothing, it is thrown away by the
 * system, and it is the only thing available when the app dies before the UI
 * exists.  The *file* is the part that grows, survives, and needs a cable to
 * retrieve, so that is the part behind the toggle.  Writes happen on a single
 * background thread: the streaming thread must never wait on storage.
 *
 * The file lands in the app's own external directory, so it needs no storage
 * permission on any API level:
 *
 *     /sdcard/Android/data/com.droidextend/files/DroidExtend.log
 */
public final class AppLog {

    private static final String TAG = "DroidExtend";
    private static final String NAME = "DroidExtend.log";
    private static final SimpleDateFormat STAMP =
            new SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US);
    private static final ExecutorService IO = Executors.newSingleThreadExecutor();

    private static volatile boolean enabled = false;
    private static volatile File file;

    private AppLog() {
    }

    /** Point the logger at this app's external files directory. */
    public static void init(Context ctx) {
        try {
            File dir = ctx.getExternalFilesDir(null);
            if (dir != null) {
                file = new File(dir, NAME);
            }
        } catch (Exception ignored) {
        }
    }

    public static synchronized void setEnabled(boolean on) {
        if (on == enabled) {
            return;
        }
        enabled = on;
        if (on) {
            write("=== logging enabled ===");
        } else {
            write("=== logging disabled ===");
        }
    }

    public static boolean enabled() {
        return enabled;
    }

    public static String path() {
        File f = file;
        return f == null ? "" : f.getAbsolutePath();
    }

    public static void clear() {
        final File f = file;
        if (f == null) {
            return;
        }
        IO.execute(new Runnable() {
            public void run() {
                try {
                    new FileWriter(f, false).close();
                } catch (Exception ignored) {
                }
            }
        });
    }

    public static void i(String msg) {
        Log.i(TAG, msg);
        write(msg);
    }

    public static void w(String msg) {
        Log.w(TAG, msg);
        write(msg);
    }

    public static void w(String msg, Throwable t) {
        Log.w(TAG, msg, t);
        write(msg + " : " + t);
    }

    private static void write(final String msg) {
        if (!enabled) {
            return;
        }
        final File f = file;
        if (f == null) {
            return;
        }
        final String line = STAMP.format(new Date()) + "  " + msg + "\n";
        IO.execute(new Runnable() {
            public void run() {
                FileWriter w = null;
                try {
                    w = new FileWriter(f, true);
                    w.write(line);
                } catch (Exception ignored) {
                    // a full or unmounted card must not break the stream
                } finally {
                    if (w != null) {
                        try {
                            w.close();
                        } catch (Exception ignored) {
                        }
                    }
                }
            }
        });
    }
}
