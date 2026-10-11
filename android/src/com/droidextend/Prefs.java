package com.droidextend;

import android.content.Context;
import android.content.SharedPreferences;

/** The handful of choices the phone gets to keep between runs. */
public final class Prefs {

    private static final String FILE = "droidextend";

    public static final String SHOW_HUD = "show_hud";
    public static final String LOGGING = "logging";
    public static final String FILL = "fill";
    public static final String GEAR_X = "gear_x";
    public static final String GEAR_Y = "gear_y";

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

    /**
     * Where the settings button was left, as a fraction of the screen (0..1).
     * Defaults to the top-right corner.
     */
    public float gearX() {
        return sp.getFloat(GEAR_X, 1f);
    }

    public float gearY() {
        return sp.getFloat(GEAR_Y, 0f);
    }

    public void setGear(float fx, float fy) {
        sp.edit().putFloat(GEAR_X, fx).putFloat(GEAR_Y, fy).apply();
    }
}
