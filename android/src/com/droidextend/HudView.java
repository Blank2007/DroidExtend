package com.droidextend;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.MotionEvent;
import android.view.View;

import java.util.Locale;

/**
 * The overlay: a couple of lines of small light text, plus a draggable button.
 *
 * This started life as a 370dp panel with a background, a separator and five
 * label/value rows.  It was accurate and completely in the way: a solid block
 * covering the top-left corner of the desktop, which is exactly where Windows
 * puts window titles and menus.  It is now the shape every remote-desktop app
 * settles on -- small, light, shadowed text that reads over any wallpaper and
 * hides nothing:
 *
 *      ● 2560 × 1600 · 60 fps
 *        6.25 Mbps · 6 ms
 *        NVENC VBR Q18
 *
 * There is no layout XML behind this.  Everything is painted in onDraw with
 * pre-built Paints, because the numbers change twice a second and inflating or
 * re-laying-out views at that rate on top of a video decoder is how you turn
 * "60 fps" into "48 fps".
 *
 * The button can be dragged anywhere and remembers where it was put, because
 * "top-right" is not the same corner for everyone -- and it is the only way back
 * into the settings once the read-out is hiding something.  Touches that are
 * not the button fall through to the video surface, so the desktop stays
 * clickable.
 */
public class HudView extends View {

    public interface Listener {
        void onGear();
        /** The button was dragged; fractions are 0..1 of the view. */
        void onGearMoved(float fx, float fy);
    }

    private final Paint textPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint subPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint shadowPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint dotPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint gearPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint gearBgPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint centrePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint centreSubPaint = new Paint(Paint.ANTI_ALIAS_FLAG);

    private final float d;
    private final int cText;
    private final int cDim;
    private final int cGood;
    private final int cWarn;
    private final int cBad;
    private final int cShadow;

    private Listener listener;
    private Stats stats = new Stats();
    private String note = "";
    private String status = "正在查找电脑端…";
    private boolean streaming;
    private boolean panelWanted = true;
    private boolean everConnected;

    // Button position, as a fraction of the view, remembered across runs.
    private float gearFx = 1f;
    private float gearFy = 0f;
    private float gearCx;
    private float gearCy;
    private float gearR;
    private float dragDx;
    private float dragDy;
    private boolean dragging;
    private boolean moved;

    public HudView(Context ctx) {
        super(ctx);
        d = getResources().getDisplayMetrics().density;
        cText = getResources().getColor(R.color.hud_light);
        cDim = getResources().getColor(R.color.hud_light_dim);
        cGood = getResources().getColor(R.color.hud_good);
        cWarn = getResources().getColor(R.color.hud_warn);
        cBad = getResources().getColor(R.color.hud_bad);
        cShadow = getResources().getColor(R.color.hud_shadow);

        textPaint.setColor(cText);
        textPaint.setTextSize(sp(12));
        textPaint.setTypeface(android.graphics.Typeface.create("sans-serif-medium",
                android.graphics.Typeface.NORMAL));
        subPaint.setColor(cDim);
        subPaint.setTextSize(sp(10.5f));
        shadowPaint.setColor(cShadow);
        shadowPaint.setTextSize(sp(12));
        shadowPaint.setTypeface(android.graphics.Typeface.create("sans-serif-medium",
                android.graphics.Typeface.NORMAL));
        dotPaint.setStyle(Paint.Style.FILL);

        gearPaint.setColor(cText);
        gearPaint.setStyle(Paint.Style.STROKE);
        gearPaint.setStrokeCap(Paint.Cap.ROUND);
        gearBgPaint.setColor(0x40060C18);
        gearBgPaint.setStyle(Paint.Style.FILL);

        centrePaint.setColor(cText);
        centrePaint.setTextSize(sp(19));
        centrePaint.setTypeface(android.graphics.Typeface.create("sans-serif-medium",
                android.graphics.Typeface.NORMAL));
        centrePaint.setTextAlign(Paint.Align.CENTER);
        centreSubPaint.setColor(cDim);
        centreSubPaint.setTextSize(sp(14));
        centreSubPaint.setTextAlign(Paint.Align.CENTER);
    }

    private float dp(float v) {
        return v * d;
    }

    private float sp(float v) {
        return v * getResources().getDisplayMetrics().scaledDensity;
    }

    public void setListener(Listener l) {
        listener = l;
    }

    public void setPanelWanted(boolean on) {
        panelWanted = on;
        invalidate();
    }

    /** Place the button: fractions of the view, 0..1. */
    public void setGearFraction(float fx, float fy) {
        gearFx = clamp01(fx);
        gearFy = clamp01(fy);
        layoutGear();
        invalidate();
    }

    public float gearFractionX() {
        return gearFx;
    }

    public float gearFractionY() {
        return gearFy;
    }

    private static float clamp01(float v) {
        return v < 0f ? 0f : (v > 1f ? 1f : v);
    }

    public void setStatus(String s, boolean isStreaming) {
        status = s == null ? "" : s;
        streaming = isStreaming;
        if (isStreaming) {
            everConnected = true;
        }
        invalidate();
    }

    public void setNote(String n) {
        note = n == null ? "" : n;
        invalidate();
    }

    public void setStats(Stats s) {
        stats = s;
        invalidate();
    }

    private int latencyColor() {
        if (stats.latencyMs < 0) {
            return cDim;
        }
        if (stats.latencyMs <= 18) {
            return cGood;
        }
        if (stats.latencyMs <= 40) {
            return cWarn;
        }
        return cBad;
    }

    private String line1() {
        String size = stats.width > 0
                ? String.format(Locale.US, "%d × %d", stats.width, stats.height) : "—";
        if (stats.streamFps > 0) {
            size += String.format(Locale.US, " · %.0f fps", stats.streamFps);
        }
        return size;
    }

    private String line2() {
        String s = stats.mbps > 0
                ? String.format(Locale.US, "%.2f Mbps", stats.mbps) : "— Mbps";
        if (stats.latencyMs >= 0) {
            s += String.format(Locale.US, " · %.0f ms", stats.latencyMs);
        }
        return s;
    }

    @Override
    protected void onSizeChanged(int w, int h, int ow, int oh) {
        super.onSizeChanged(w, h, ow, oh);
        gearR = dp(21);
        layoutGear();
    }

    private void layoutGear() {
        float m = dp(9) + gearR;
        float x = m + clamp01(gearFx) * Math.max(0f, getWidth() - 2 * m);
        float y = m + clamp01(gearFy) * Math.max(0f, getHeight() - 2 * m);
        gearCx = x;
        gearCy = y;
    }

    @Override
    protected void onDraw(Canvas c) {
        super.onDraw(c);

        if (streaming || everConnected) {
            if (panelWanted) {
                drawReadout(c);
            }
        } else {
            drawWaiting(c);
        }
        drawGear(c);
    }

    /**
     * The read-out: small light text with a hard shadow, no panel.
     *
     * The shadow is drawn as four offset copies of the glyphs rather than
     * setShadowLayer, which silently does nothing on a hardware-accelerated
     * canvas on some devices -- and a read-out you cannot read over a bright
     * wallpaper is worse than no read-out.
     */
    private void drawReadout(Canvas c) {
        float x = dp(12);
        float y = dp(16) + textPaint.getTextSize();
        float gap = dp(17);

        // status dot
        float dotR = dp(3.4f);
        int col = streaming ? latencyColor() : cWarn;
        dotPaint.setColor(col);
        c.drawCircle(x + dotR, y - textPaint.getTextSize() * 0.3f, dotR, dotPaint);
        if (streaming && stats.catchingUp) {
            dotPaint.setColor(cWarn);
            c.drawCircle(x + dp(9) + dotR, y - textPaint.getTextSize() * 0.3f, dotR, dotPaint);
        }

        String head = streaming ? line1()
                : (status.isEmpty() ? "正在重连…" : status);
        drawShadowed(c, head, x + dotR * 2 + dp(6), y, textPaint);
        drawShadowed(c, line2(), x + dotR * 2 + dp(6), y + gap, subPaint);
        if (!note.isEmpty()) {
            drawShadowed(c, note, x + dotR * 2 + dp(6), y + gap * 2, subPaint);
        }
    }

    private void drawShadowed(Canvas c, String s, float x, float base, Paint p) {
        if (s == null || s.isEmpty()) {
            return;
        }
        shadowPaint.setTextSize(p.getTextSize());
        shadowPaint.setTypeface(p.getTypeface());
        float o = dp(1);
        c.drawText(s, x - o, base, shadowPaint);
        c.drawText(s, x + o, base, shadowPaint);
        c.drawText(s, x, base - o, shadowPaint);
        c.drawText(s, x, base + o, shadowPaint);
        if (p == textPaint) {
            // The latency line is tinted, so take its colour from the live value.
            p.setColor(cText);
        }
        c.drawText(s, x, base, p);
    }

    private void drawWaiting(Canvas c) {
        float cx = getWidth() / 2f;
        float cy = getHeight() / 2f;
        String head = status.isEmpty() ? "正在查找电脑端…" : status;
        c.drawText(head, cx, cy, centrePaint);
        c.drawText("用数据线连上电脑，然后在电脑端点「开始投屏」", cx, cy + sp(26), centreSubPaint);
        c.drawText("拖动右上角的按钮可以移动它，点一下打开设置", cx, cy + sp(46), centreSubPaint);
    }

    /**
     * A "tune" glyph: three sliders, each with a knob at a different position.
     * A cog drawn from eight radial stubs reads as a ship's wheel at this size;
     * sliders stay legible and cost three lines and three dots.
     */
    private void drawGear(Canvas c) {
        c.drawCircle(gearCx, gearCy, gearR, gearBgPaint);
        gearPaint.setStyle(Paint.Style.STROKE);
        gearPaint.setStrokeWidth(dp(2.1f));
        gearPaint.setColor(dragging ? cGood : cText);
        float span = gearR * 0.52f;
        float[] knobs = {-0.34f, 0.30f, -0.04f};
        dotPaint.setColor(dragging ? cGood : cText);
        for (int i = 0; i < 3; i++) {
            float row = gearCy + gearR * (i - 1) * 0.36f;
            c.drawLine(gearCx - span, row, gearCx + span, row, gearPaint);
            c.drawCircle(gearCx + span * knobs[i], row, dp(2.8f), dotPaint);
        }
    }

    private boolean onGear(MotionEvent e) {
        float dx = e.getX() - gearCx;
        float dy = e.getY() - gearCy;
        float r = gearR * 1.35f;
        return dx * dx + dy * dy <= r * r;
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                // Only the button is ours.  Returning false for everything else
                // is what keeps the desktop underneath fully clickable;
                // FrameLayout then hands this touch to the surface.
                if (!onGear(e)) {
                    return false;
                }
                dragging = true;
                moved = false;
                dragDx = e.getX() - gearCx;
                dragDy = e.getY() - gearCy;
                invalidate();
                return true;

            case MotionEvent.ACTION_MOVE:
                if (!dragging) {
                    return false;
                }
                // Anyone who has fought an on-screen button knows the pain of
                // a 2-pixel slip turning a press into a no-op, so a drag only
                // starts once the finger has really travelled.
                if (Math.abs(e.getX() - gearCx) + Math.abs(e.getY() - gearCy)
                        > dp(9)) {
                    moved = true;
                }
                if (moved) {
                    float m = dp(9) + gearR;
                    float nx = Math.max(m, Math.min(getWidth() - m, e.getX() - dragDx));
                    float ny = Math.max(m, Math.min(getHeight() - m, e.getY() - dragDy));
                    gearCx = nx;
                    gearCy = ny;
                    float spanX = Math.max(1f, getWidth() - 2 * m);
                    float spanY = Math.max(1f, getHeight() - 2 * m);
                    gearFx = clamp01((nx - m) / spanX);
                    gearFy = clamp01((ny - m) / spanY);
                    invalidate();
                }
                return true;

            case MotionEvent.ACTION_UP:
                if (!dragging) {
                    return false;
                }
                dragging = false;
                invalidate();
                if (moved) {
                    if (listener != null) {
                        listener.onGearMoved(gearFx, gearFy);
                    }
                } else if (onGear(e) && listener != null) {
                    listener.onGear();
                }
                return true;

            case MotionEvent.ACTION_CANCEL:
                if (!dragging) {
                    return false;
                }
                dragging = false;
                invalidate();
                return true;

            default:
                return dragging;
        }
    }
}
