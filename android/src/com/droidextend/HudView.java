package com.droidextend;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.MotionEvent;
import android.view.View;

import java.util.Locale;

/**
 * The overlay: a live read-out of the link, drawn by hand.
 *
 * There is no layout XML behind this.  Everything is painted in onDraw with
 * pre-built Paints, because the numbers change twice a second and inflating or
 * re-laying-out views at that rate on top of a video decoder is how you turn
 * "60 fps" into "48 fps".
 *
 * The panel is deliberately readonly: every touch that is not the gear falls
 * through to the video surface, because a floating window that eats taps would
 * make the bottom half of the desktop unusable.  Needs the show-hud toggle and
 * the gear to get back in, so the gear is always there.
 */
public class HudView extends View {

    public interface Listener {
        void onGear();
    }

    private static final String[] ROW_LABELS = {"分辨率", "帧率", "码率", "延迟", "画质"};

    private final Paint panelPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint strokePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint labelPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint valuePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint statusPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint dotPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint gearPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint gearBgPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint centrePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint centreSubPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF rect = new RectF();
    private final String[] values = new String[ROW_LABELS.length];

    private final float d;
    private final int cPanel;
    private final int cStroke;
    private final int cText;
    private final int cDim;
    private final int cGood;
    private final int cWarn;
    private final int cBad;
    private final int cAccent;

    private Listener listener;
    private Stats stats = new Stats();
    private String note = "";
    private String status = "正在查找电脑端…";
    private boolean streaming;
    private boolean panelWanted = true;
    private boolean everConnected;

    private float gearCx;
    private float gearCy;
    private float gearR;

    public HudView(Context ctx) {
        super(ctx);
        d = getResources().getDisplayMetrics().density;
        cPanel = getResources().getColor(R.color.hud_panel);
        cStroke = getResources().getColor(R.color.hud_stroke);
        cText = getResources().getColor(R.color.hud_text);
        cDim = getResources().getColor(R.color.hud_dim);
        cGood = getResources().getColor(R.color.hud_good);
        cWarn = getResources().getColor(R.color.hud_warn);
        cBad = getResources().getColor(R.color.hud_bad);
        cAccent = getResources().getColor(R.color.accent);

        panelPaint.setStyle(Paint.Style.FILL);
        panelPaint.setColor(cPanel);
        strokePaint.setStyle(Paint.Style.STROKE);
        strokePaint.setStrokeWidth(dp(1));
        strokePaint.setColor(cStroke);

        labelPaint.setColor(cDim);
        labelPaint.setTextSize(sp(12));
        valuePaint.setColor(cText);
        valuePaint.setTextSize(sp(13));
        valuePaint.setTypeface(android.graphics.Typeface.create("sans-serif-medium",
                android.graphics.Typeface.NORMAL));
        statusPaint.setColor(cText);
        statusPaint.setTextSize(sp(13));
        statusPaint.setTypeface(android.graphics.Typeface.create("sans-serif-medium",
                android.graphics.Typeface.NORMAL));
        dotPaint.setStyle(Paint.Style.FILL);

        gearPaint.setColor(0xFFFFFFFF);
        gearPaint.setStyle(Paint.Style.STROKE);
        gearPaint.setStrokeWidth(dp(2.4f));
        gearPaint.setStrokeCap(Paint.Cap.ROUND);
        gearBgPaint.setColor(0x66060C18);
        gearBgPaint.setStyle(Paint.Style.FILL);

        centrePaint.setColor(cText);
        centrePaint.setTextSize(sp(19));
        centrePaint.setTypeface(android.graphics.Typeface.create("sans-serif-medium",
                android.graphics.Typeface.NORMAL));
        centrePaint.setTextAlign(Paint.Align.CENTER);
        centreSubPaint.setColor(cDim);
        centreSubPaint.setTextSize(sp(14));
        centreSubPaint.setTextAlign(Paint.Align.CENTER);

        for (int i = 0; i < values.length; i++) {
            values[i] = "—";
        }
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
        // Named columns rather than one long line, so a number that jumps from
        // 9 to 10 does not shuffle everything to its right.
        values[0] = stats.width > 0
                ? String.format(Locale.US, "%d × %d", stats.width, stats.height) : "—";
        values[1] = stats.streamFps > 0
                ? String.format(Locale.US, "%.1f fps", stats.streamFps) : "—";
        values[2] = stats.mbps > 0
                ? String.format(Locale.US, "%.2f Mbps", stats.mbps) : "—";
        if (stats.latencyMs >= 0) {
            values[3] = String.format(Locale.US, "%.0f ms（峰值 %.0f）",
                    stats.latencyMs, stats.latencyMaxMs);
        } else {
            values[3] = "—";
        }
        values[4] = note.isEmpty() ? "—" : note;
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

    @Override
    protected void onSizeChanged(int w, int h, int ow, int oh) {
        super.onSizeChanged(w, h, ow, oh);
        gearR = dp(22);
        gearCx = w - dp(14) - gearR;
        gearCy = dp(14) + gearR;
    }

    @Override
    protected void onDraw(Canvas c) {
        super.onDraw(c);

        if (streaming || everConnected) {
            if (panelWanted) {
                drawPanel(c);
            }
        } else {
            drawWaiting(c);
        }
        drawGear(c);
    }

    private void drawPanel(Canvas c) {
        float pad = dp(13);
        float left = dp(14);
        float top = dp(14);
        // Wide enough for "NVENC VBR Q26 · 40Mbps · ddagrab", but never wider
        // than the screen it is drawn on.
        float width = Math.min(dp(370), getWidth() - left * 2);
        float statusH = dp(22);
        float rowH = dp(23);
        float height = pad + statusH + dp(6) + rowH * values.length + pad;

        rect.set(left, top, left + width, top + height);
        float radius = dp(14);
        c.drawRoundRect(rect, radius, radius, panelPaint);
        c.drawRoundRect(rect, radius, radius, strokePaint);

        // status dot + text
        float dotR = dp(4.5f);
        float dotX = left + pad + dotR;
        float dotY = top + pad + statusH / 2 - dp(1);
        int col = streaming ? latencyColor() : cWarn;
        dotPaint.setColor(col);
        c.drawCircle(dotX, dotY, dotR, dotPaint);
        if (streaming && stats.catchingUp) {
            dotPaint.setColor(cWarn);
            c.drawCircle(dotX + dp(11), dotY, dotR, dotPaint);
        }

        float textX = dotX + dotR + dp(8);
        String head = streaming ? "已连接" : status;
        if (streaming && stats.catchingUp) {
            head = head + " · 追帧中";
        }
        c.drawText(head, textX, dotY + statusPaint.getTextSize() * 0.36f, statusPaint);

        // separator
        float sepY = top + pad + statusH + dp(3);
        c.drawLine(left + pad, sepY, left + width - pad, sepY, strokePaint);

        // label / value rows
        float y = sepY + dp(6);
        float labelX = left + pad;
        float valueX = left + dp(84);
        float valueRoom = left + width - pad - valueX;
        for (int i = 0; i < values.length; i++) {
            float base = y + rowH * i + rowH * 0.72f;
            c.drawText(ROW_LABELS[i], labelX, base, labelPaint);
            // The last row carries the handshake's quality note, which can be
            // arbitrarily long; shrink it rather than let it run off the panel.
            drawFitted(c, values[i], valueX, base, valueRoom,
                    i == 3 ? latencyValuePaint() : valuePaint);
        }
    }

    /** Draw text at its own size, or scaled down to fit in maxW. */
    private void drawFitted(Canvas c, String s, float x, float base, float maxW, Paint p) {
        if (s == null || s.isEmpty()) {
            return;
        }
        float size = p.getTextSize();
        float w = p.measureText(s);
        if (w > maxW && w > 0f) {
            p.setTextSize(Math.max(sp(8), size * maxW / w));
        }
        c.drawText(s, x, base, p);
        p.setTextSize(size);
    }

    private Paint latencyValuePaint() {
        // reuse valuePaint but tinted by how bad the number is
        Paint p = valuePaint;
        p.setColor(stats.latencyMs < 0 ? cText : latencyColor());
        return p;
    }

    private void drawWaiting(Canvas c) {
        float cx = getWidth() / 2f;
        float cy = getHeight() / 2f;
        c.drawText(status.isEmpty() ? "正在查找电脑端…" : status, cx, cy, centrePaint);
        c.drawText("用数据线连上电脑，然后在电脑端点「开始投屏」",
                cx, cy + sp(26), centreSubPaint);
        c.drawText("右上角的齿轮可以打开显示设置", cx, cy + sp(46), centreSubPaint);
    }

    /**
     * A "tune" glyph: three sliders, each with a knob at a different position.
     * A cog drawn from eight radial stubs reads as a ship's wheel at this size;
     * sliders stay legible and cost three lines and three dots.
     */
    private void drawGear(Canvas c) {
        c.drawCircle(gearCx, gearCy, gearR, gearBgPaint);
        c.drawCircle(gearCx, gearCy, gearR, strokePaint);
        float span = gearR * 0.54f;
        gearPaint.setStyle(Paint.Style.STROKE);
        gearPaint.setStrokeWidth(dp(2.2f));
        gearPaint.setStrokeCap(Paint.Cap.ROUND);
        gearPaint.setColor(0xFFFFFFFF);
        float[] knobs = {-0.34f, 0.30f, -0.04f};
        dotPaint.setColor(0xFFFFFFFF);
        for (int i = 0; i < 3; i++) {
            float row = gearCy + gearR * (i - 1) * 0.36f;
            c.drawLine(gearCx - span, row, gearCx + span, row, gearPaint);
            c.drawCircle(gearCx + span * knobs[i], row, dp(3.0f), dotPaint);
        }
    }

    private boolean onGear(MotionEvent e) {
        float dx = e.getX() - gearCx;
        float dy = e.getY() - gearCy;
        float r = gearR * 1.3f;
        return dx * dx + dy * dy <= r * r;
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                // Only ever claim a gesture that starts on the gear.  Returning
                // false here is what lets the picture underneath stay fully
                // clickable; FrameLayout then hands this touch to the surface.
                if (!onGear(e)) {
                    return false;
                }
                setPressed(true);
                invalidate();
                return true;

            case MotionEvent.ACTION_UP:
                if (!isPressed()) {
                    return false;
                }
                setPressed(false);
                invalidate();
                if (onGear(e) && listener != null) {
                    listener.onGear();
                }
                return true;

            case MotionEvent.ACTION_CANCEL:
                setPressed(false);
                invalidate();
                return true;

            default:
                return isPressed();
        }
    }
}
