package com.droidextend;

import android.content.Context;
import android.content.SharedPreferences;

/** The three switches the phone gets to keep between runs. */
public final class Prefs {

    private static final String FILE = "droidextend";

    public static final String SHOW_HUD = "show_hud";
    public static final String LOGGING = "logging";
    public static final String FILL = "fill";

    private final SharedPreferences sp;

    public Prefs(Context ctx) {
        sp = ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    public boolean showHud() {
        return sp.getBoolean(SHOW_HUD, true);
    }

    /**
     * Filling the panel is the default: a 3200x2136 phone showing a 2560x1600
     * desktop letterboxed would waste a tenth of the screen on black bars, and
     * cropping a few pixels off the desktop is the lesser evil.  Turn it off to
     * see every pixel at the cost of the bars.
     */
    public boolean fill() {
        return sp.getBoolean(FILL, true);
    }

    public boolean logging() {
        return sp.getBoolean(LOGGING, false);
    }

    public void set(String key, boolean value) {
        sp.edit().putBoolean(key, value).apply();
    }
}
