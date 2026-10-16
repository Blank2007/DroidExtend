package com.droidextend;

import android.app.ActivityManager;
import android.content.Context;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.View;
import android.view.animation.DecelerateInterpolator;
import android.view.animation.Interpolator;

/**
 * The small amount of motion this app is allowed to have.
 *
 * Motion here is a courtesy, not a feature.  It is skipped entirely when the
 * user has switched system animations off, on a low-RAM device, or when the
 * checkbox in the panel is cleared -- and in that case every method below puts
 * the view straight into its final state instead of doing nothing.  Callers
 * never branch on whether the animation is going to happen.
 *
 * Nothing animates longer than {@link #MAX_MS}: past that it stops reading as
 * feedback and starts reading as lag, which is the opposite of the point.
 */
public final class Anim {

    /** Prefs key for the checkbox. */
    public static final String KEY = "anim";

    /** Hard ceiling on any single animation. */
    private static final int MAX_MS = 200;

    private static final Interpolator EASE = new DecelerateInterpolator(1.6f);

    private static Boolean lowRam;
    private static Float scale;

    private Anim() {
    }

    /**
     * Whether motion is allowed.  Reads the pref every time (it changes while
     * the app runs) but caches the two device facts, which do not.
     */
    public static boolean allowed(Context ctx, Prefs p) {
        if (p == null || !p.anim()) {
            return false;
        }
        return !lowRam(ctx) && systemScale(ctx) > 0f;
    }

    /** A duration in the user's own terms: the system scale, capped. */
    public static int ms(Context ctx, int base) {
        float f = systemScale(ctx);
        if (f <= 0f) {
            return 0;
        }
        return Math.min(Math.round(base * f), MAX_MS);
    }

    /** The device says it is short on memory -- motion is the first thing to go. */
    private static boolean lowRam(Context ctx) {
        if (lowRam == null) {
            boolean v = false;
            try {
                ActivityManager am =
                        (ActivityManager) ctx.getSystemService(Context.ACTIVITY_SERVICE);
                v = am != null && am.isLowRamDevice();
            } catch (Throwable t) {
                v = false;
            }
            lowRam = Boolean.valueOf(v);
        }
        return lowRam.booleanValue();
    }

    /**
     * The system animation scale.  Zero means the user asked for no animation
     * anywhere, which we honour without asking again.  Some OEM builds refuse
     * to read it; that is not a reason to punish the user, so treat a failure
     * as "normal speed".
     */
    private static float systemScale(Context ctx) {
        if (scale == null) {
            float f = 1f;
            try {
                f = Settings.Global.getFloat(ctx.getContentResolver(),
                        Settings.Global.ANIMATOR_DURATION_SCALE, 1f);
            } catch (Throwable t) {
                f = 1f;
            }
            if (f < 0f) {
                f = 1f;
            }
            scale = Float.valueOf(f);
        }
        return scale.floatValue();
    }

    /** Forget the cached device facts.  Only the tests need this. */
    public static void resetCache() {
        lowRam = null;
        scale = null;
    }

    /** Fade and lift into place.  Safe on a null view. */
    public static void in(View v, Context ctx, Prefs p) {
        if (v == null) {
            return;
        }
        if (!allowed(ctx, p)) {
            v.setAlpha(1f);
            v.setTranslationY(0f);
            return;
        }
        float lift = dp(ctx, 14);
        v.setAlpha(0f);
        v.setTranslationY(lift);
        v.animate()
                .alpha(1f)
                .translationY(0f)
                .setDuration(ms(ctx, 150))
                .setInterpolator(EASE)
                .start();
    }

    /**
     * Fade and drop away, then run {@code after}.  When motion is off the
     * callback still runs, immediately -- otherwise the panel would never come
     * off the screen.
     */
    public static void out(final View v, Context ctx, Prefs p, final Runnable after) {
        if (v == null) {
            if (after != null) {
                after.run();
            }
            return;
        }
        if (!allowed(ctx, p)) {
            if (after != null) {
                after.run();
            }
            return;
        }
        v.animate()
                .alpha(0f)
                .translationY(dp(ctx, 10))
                .setDuration(ms(ctx, 110))
                .setInterpolator(EASE)
                .withEndAction(after)
                .start();
    }

    /**
     * A press acknowledgement: the control shrinks a hair and comes back.
     *
     * <p>Used from an OnTouchListener that returns false, so the click still
     * reaches the button -- this is decoration on top of the real handler, not
     * a replacement for it.
     */
    public static void tap(final View v, Context ctx, Prefs p) {
        if (v == null || !allowed(ctx, p)) {
            return;
        }
        v.animate()
                .scaleX(0.96f).scaleY(0.96f)
                .setDuration(ms(ctx, 55))
                .withEndAction(new Runnable() {
                    @Override
                    public void run() {
                        v.animate().scaleX(1f).scaleY(1f).setDuration(ms(ctx, 85)).start();
                    }
                })
                .start();
    }

    private static float dp(Context ctx, float v) {
        return TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                ctx.getResources().getDisplayMetrics());
    }
}
