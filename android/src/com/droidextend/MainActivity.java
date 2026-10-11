package com.droidextend;

import android.app.Activity;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.PowerManager;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.CompoundButton;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/**
 * DroidExtend receiver: renders the PC's virtual second screen and sends
 * touches back to it.
 *
 * The PC side is reachable on 127.0.0.1:<port> because the Windows app installs
 * an `adb reverse` tunnel over the USB cable - no Wi-Fi, no pairing.  Video
 * arrives on 27183, this screen's touches leave on 27184.
 *
 * There is no layout XML anywhere in this app.  The whole screen is three
 * hand-built layers:
 *
 *     VideoSurface   the decoded picture, letterboxed or cropped
 *     HudView        the live link read-out (drawn, not laid out)
 *     sheet          the display settings, built on demand
 *
 * The overlay only claims the gear button; every other touch falls through to
 * the picture, because a floating window that ate taps would make the desktop
 * unusable.
 */
public class MainActivity extends Activity implements SurfaceHolder.Callback {

    private static final String TAG = "DroidExtend";

    /** Must match HOST_PORT in the Windows app. */
    private static final int PORT = 27183;

    /** Two-finger tap: how long and how still it has to be to count. */
    private static final long TAP_MS = 300;
    private static final float TAP_SLOP = 24f;

    private VideoSurface surfaceView;
    private HudView hud;
    private VideoDecoder decoder;
    private StreamClient client;
    private InputClient input;
    private PowerManager.WakeLock wakeLock;
    private Prefs prefs;
    private FrameLayout root;
    private View sheet;
    private boolean started;

    /**
     * A SurfaceView that keeps the stream's aspect ratio.
     *
     * Two modes, both of which make touch mapping trivial because the view *is*
     * the picture:
     *
     *   contain  the picture fits inside the screen; the leftover edge is black
     *   cover    the picture fills the screen; the overflow is cropped by the
     *            parent, and because the view is centred the visible region is
     *            exactly the middle of the picture, so normalising a touch by
     *            getWidth()/getHeight() still lands in the right place
     */
    private static class VideoSurface extends SurfaceView {
        private float aspect = 16f / 9f;
        private boolean fill = true;

        VideoSurface(Context c) {
            super(c);
        }

        void setAspect(float a) {
            if (a > 0.05f && Math.abs(a - aspect) > 0.001f) {
                aspect = a;
                requestLayout();
            }
        }

        float getAspect() {
            return aspect;
        }

        void setFill(boolean on) {
            if (fill != on) {
                fill = on;
                requestLayout();
            }
        }

        @Override
        protected void onMeasure(int widthSpec, int heightSpec) {
            int w = MeasureSpec.getSize(widthSpec);
            int h = MeasureSpec.getSize(heightSpec);
            if (w > 0 && h > 0 && aspect > 0f) {
                if (fill) {
                    if (w / (float) h > aspect) {
                        h = Math.round(w / aspect);
                    } else {
                        w = Math.round(h * aspect);
                    }
                } else {
                    if (w / (float) h > aspect) {
                        w = Math.round(h * aspect);
                    } else {
                        h = Math.round(w / aspect);
                    }
                }
            }
            setMeasuredDimension(Math.max(1, w), Math.max(1, h));
        }
    }

    // -- touch gesture state -------------------------------------------------
    private int gesture;                 // 0 idle, 1 one finger, 2 two fingers
    private float lastCx, lastCy;        // two-finger centroid, in video pixels
    private float startCx, startCy;
    private long gestureStart;

    private float dp(float v) {
        return v * getResources().getDisplayMetrics().density;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);
        AppLog.init(this);
        AppLog.setEnabled(prefs.logging());
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        hideSystemUi();

        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);

        surfaceView = new VideoSurface(this);
        surfaceView.setFill(prefs.fill());
        surfaceView.getHolder().addCallback(this);
        surfaceView.setOnTouchListener(new View.OnTouchListener() {
            @Override public boolean onTouch(View v, MotionEvent e) {
                return handleTouch(e);
            }
        });
        FrameLayout.LayoutParams vp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
        vp.gravity = Gravity.CENTER;
        root.addView(surfaceView, vp);

        hud = new HudView(this);
        hud.setPanelWanted(prefs.showHud());
        hud.setGearFraction(prefs.gearX(), prefs.gearY());
        hud.setListener(new HudView.Listener() {
            @Override public void onGear() {
                showSettings();
            }

            @Override public void onGearMoved(float fx, float fy) {
                prefs.setGear(fx, fy);
            }
        });
        root.addView(hud, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        setContentView(root);
        decoder = new VideoDecoder();
        input = new InputClient("127.0.0.1", InputClient.PORT);

        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (pm != null) {
            wakeLock = pm.newWakeLock(PowerManager.SCREEN_DIM_WAKE_LOCK, "droidextend:stream");
            wakeLock.setReferenceCounted(false);
        }
        AppLog.i("main  启动 v" + versionName() + "  fill=" + prefs.fill()
                + " hud=" + prefs.showHud() + " log=" + prefs.logging()
                + " gear=" + prefs.gearX() + "," + prefs.gearY());
    }

    private String versionName() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "?";
        }
    }

    private void hideSystemUi() {
        View decor = getWindow().getDecorView();
        decor.setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            hideSystemUi();
        }
    }

    // ------------------------------------------------------------- settings
    private void showSettings() {
        if (sheet != null) {
            return;
        }
        final FrameLayout scrim = new FrameLayout(this);
        scrim.setBackgroundColor(0x99000000);
        scrim.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                hideSettings();
            }
        });

        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(getResources().getColor(R.color.sheet_bg));
        bg.setCornerRadius(dp(20));
        bg.setStroke(Math.round(dp(1)), getResources().getColor(R.color.hud_stroke));
        card.setBackground(bg);
        int pad = Math.round(dp(20));
        card.setPadding(pad, pad, pad, pad);
        card.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                // swallow: a tap inside the card is not a tap on the scrim
            }
        });

        TextView title = new TextView(this);
        title.setText("显示设置");
        title.setTextColor(getResources().getColor(R.color.hud_text));
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        card.addView(title);

        card.addView(check("状态浮窗（码率 / 帧率 / 延迟）", prefs.showHud(),
                new CompoundButton.OnCheckedChangeListener() {
                    @Override public void onCheckedChanged(CompoundButton b, boolean on) {
                        prefs.set(Prefs.SHOW_HUD, on);
                        hud.setPanelWanted(on);
                    }
                }));

        card.addView(check("让画面铺满屏幕（裁掉多余边缘）", prefs.fill(),
                new CompoundButton.OnCheckedChangeListener() {
                    @Override public void onCheckedChanged(CompoundButton b, boolean on) {
                        prefs.set(Prefs.FILL, on);
                        surfaceView.setFill(on);
                    }
                }));

        final LinearLayout logBox = new LinearLayout(this);
        logBox.setOrientation(LinearLayout.VERTICAL);
        logBox.setVisibility(prefs.logging() ? View.VISIBLE : View.GONE);

        TextView logPath = new TextView(this);
        logPath.setText("日志文件：\n" + AppLog.path());
        logPath.setTextColor(getResources().getColor(R.color.hud_dim));
        logPath.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f);
        logPath.setTextIsSelectable(true);
        logBox.addView(logPath);

        Button clear = new Button(this);
        clear.setText("清空日志");
        clear.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        clear.setTextColor(getResources().getColor(R.color.hud_text));
        clear.setBackgroundTintList(ColorStateList.valueOf(0x33FFFFFF));
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        clp.topMargin = Math.round(dp(6));
        clear.setLayoutParams(clp);
        clear.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                AppLog.clear();
                AppLog.i("log   用户清空了日志文件");
                // Clearing a file is exactly the kind of thing that gives no
                // visible feedback, so say it out loud.
                Toast.makeText(MainActivity.this, "已清空日志", Toast.LENGTH_SHORT).show();
            }
        });
        logBox.addView(clear);
        card.addView(logBox);

        card.addView(check("把日志写到文件（默认关闭）", prefs.logging(),
                new CompoundButton.OnCheckedChangeListener() {
                    @Override public void onCheckedChanged(CompoundButton b, boolean on) {
                        prefs.set(Prefs.LOGGING, on);
                        AppLog.setEnabled(on);
                        logBox.setVisibility(on ? View.VISIBLE : View.GONE);
                    }
                }));

        TextView hint = new TextView(this);
        hint.setText("浮窗只占用左上角，右侧齿轮随时可以打开这里；"
                + "其它位置的点按都会照常传到电脑。");
        hint.setTextColor(getResources().getColor(R.color.hud_dim));
        hint.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f);
        hint.setPadding(0, Math.round(dp(10)), 0, 0);
        card.addView(hint);

        Button close = new Button(this);
        close.setText("关闭");
        close.setTextColor(getResources().getColor(R.color.hud_text));
        close.setBackgroundTintList(ColorStateList.valueOf(
                getResources().getColor(R.color.accent)));
        LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        blp.topMargin = Math.round(dp(12));
        close.setLayoutParams(blp);
        close.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                hideSettings();
            }
        });
        card.addView(close);

        FrameLayout.LayoutParams cl = new FrameLayout.LayoutParams(
                Math.min(Math.round(dp(560)), getResources().getDisplayMetrics().widthPixels
                        - Math.round(dp(56))),
                ViewGroup.LayoutParams.WRAP_CONTENT);
        cl.gravity = Gravity.CENTER;
        scrim.addView(card, cl);

        sheet = scrim;
        root.addView(sheet, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        AppLog.i("ui    打开显示设置");
    }

    private CheckBox check(String text, boolean on,
                           CompoundButton.OnCheckedChangeListener l) {
        CheckBox cb = new CheckBox(this);
        cb.setText(text);
        cb.setTextColor(getResources().getColor(R.color.hud_text));
        cb.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        cb.setChecked(on);
        cb.setButtonTintList(ColorStateList.valueOf(getResources().getColor(R.color.accent)));
        cb.setOnCheckedChangeListener(l);
        cb.setPadding(0, Math.round(dp(10)), 0, Math.round(dp(10)));
        return cb;
    }

    private void hideSettings() {
        if (sheet == null) {
            return;
        }
        root.removeView(sheet);
        sheet = null;
    }

    // ------------------------------------------------------------- touch -> PC
    private float nx(float viewX) {
        float w = Math.max(surfaceView.getWidth(), 1);
        return viewX / w;
    }

    private float ny(float viewY) {
        float h = Math.max(surfaceView.getHeight(), 1);
        return viewY / h;
    }

    /** Convert a view-space delta into stream pixels, so scroll feels the same
     *  on any phone. */
    private float toVideo(float d, boolean horizontal) {
        if (horizontal) {
            return d * surfaceView.getAspect();
        }
        return d;
    }

    private float twoX(MotionEvent e) {
        return (e.getX(0) + e.getX(1)) * 0.5f * surfaceView.getAspect();
    }

    private float twoY(MotionEvent e) {
        return (e.getY(0) + e.getY(1)) * 0.5f;
    }

    private boolean handleTouch(MotionEvent e) {
        if (input == null || !started) {
            return true;
        }
        // One line per gesture boundary: cheap, and the only way to tell a dead
        // touch path from a dead tunnel when something goes wrong in the field.
        if (e.getActionMasked() == MotionEvent.ACTION_DOWN
                || e.getActionMasked() == MotionEvent.ACTION_POINTER_DOWN
                || e.getActionMasked() == MotionEvent.ACTION_UP
                || e.getActionMasked() == MotionEvent.ACTION_POINTER_UP) {
            AppLog.i("touch action=" + e.getActionMasked() + " ptrs=" + e.getPointerCount()
                    + " at " + e.getX() + "," + e.getY()
                    + " view=" + surfaceView.getWidth() + "x" + surfaceView.getHeight());
        }
        int action = e.getActionMasked();
        switch (action) {
            case MotionEvent.ACTION_DOWN:
                gesture = 1;
                input.touch(InputClient.DOWN, nx(e.getX()), ny(e.getY()));
                return true;

            case MotionEvent.ACTION_POINTER_DOWN:
                // A second finger arrived: the drag we started is over, and
                // from here on this is a scroll (or a right-click tap).
                if (gesture == 1) {
                    input.touch(InputClient.UP, nx(e.getX(0)), ny(e.getY(0)));
                }
                gesture = 2;
                lastCx = twoX(e);
                lastCy = twoY(e);
                startCx = lastCx;
                startCy = lastCy;
                gestureStart = System.currentTimeMillis();
                return true;

            case MotionEvent.ACTION_MOVE:
                if (e.getPointerCount() >= 2) {
                    if (gesture != 2) {
                        gesture = 2;
                        lastCx = twoX(e);
                        lastCy = twoY(e);
                        startCx = lastCx;
                        startCy = lastCy;
                        gestureStart = System.currentTimeMillis();
                        return true;
                    }
                    float mx = twoX(e);
                    float my = twoY(e);
                    float dx = mx - lastCx;
                    float dy = my - lastCy;
                    lastCx = mx;
                    lastCy = my;
                    if (Math.abs(dx) > 0.5f || Math.abs(dy) > 0.5f) {
                        input.scroll(dx, dy);
                    }
                } else if (gesture == 1) {
                    input.touch(InputClient.MOVE, nx(e.getX()), ny(e.getY()));
                }
                return true;

            case MotionEvent.ACTION_POINTER_UP:
                if (gesture == 2) {
                    long ms = System.currentTimeMillis() - gestureStart;
                    float moved = Math.abs(lastCx - startCx) + Math.abs(lastCy - startCy);
                    if (ms < TAP_MS && moved < TAP_SLOP) {
                        input.touch(InputClient.RCLICK, nx(e.getX(0)), ny(e.getY(0)));
                    }
                    gesture = 0;
                }
                return true;

            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                if (gesture == 1) {
                    input.touch(InputClient.UP, nx(e.getX()), ny(e.getY()));
                }
                gesture = 0;
                return true;

            default:
                return true;
        }
    }

    // ------------------------------------------------------------- surface
    @Override
    public void surfaceCreated(SurfaceHolder holder) {
        decoder.setSurface(holder.getSurface());
        startClientOnce();
    }

    @Override
    public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {
        decoder.setSurface(holder.getSurface());
        startClientOnce();
    }

    @Override
    public void surfaceDestroyed(SurfaceHolder holder) {
        decoder.setSurface((Surface) null);
    }

    private void startClientOnce() {
        if (started) {
            return;
        }
        started = true;
        try {
            if (wakeLock != null) {
                wakeLock.acquire();
            }
        } catch (Throwable ignored) { }
        if (input != null) {
            input.start();
        }
        client = new StreamClient("127.0.0.1", PORT, decoder, new StreamClient.Listener() {
            @Override public void onStatus(final String text, final boolean streaming) {
                runOnUiThread(new Runnable() {
                    @Override public void run() {
                        if (hud != null) {
                            hud.setStatus(text, streaming);
                        }
                    }
                });
            }

            @Override public void onVideoSize(final int w, final int h) {
                runOnUiThread(new Runnable() {
                    @Override public void run() {
                        if (surfaceView != null && w > 0 && h > 0) {
                            surfaceView.setAspect(w / (float) h);
                        }
                    }
                });
            }

            @Override public void onStats(final Stats s) {
                runOnUiThread(new Runnable() {
                    @Override public void run() {
                        if (hud != null) {
                            hud.setStats(s);
                        }
                    }
                });
            }

            @Override public void onNote(final String note) {
                runOnUiThread(new Runnable() {
                    @Override public void run() {
                        if (hud != null) {
                            hud.setNote(note);
                        }
                    }
                });
            }
        });
        client.start();
        AppLog.i("client started");
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (isFinishing()) {
            stopStream();
        }
    }

    @Override
    protected void onDestroy() {
        stopStream();
        super.onDestroy();
    }

    private void stopStream() {
        started = false;
        if (client != null) {
            client.shutdown();
            client = null;
        }
        if (input != null) {
            input.stop();
        }
        if (decoder != null) {
            decoder.reset();
        }
        if (wakeLock != null && wakeLock.isHeld()) {
            try { wakeLock.release(); } catch (Throwable ignored) { }
        }
    }

    @Override
    public void onBackPressed() {
        if (sheet != null) {
            hideSettings();
            return;
        }
        stopStream();
        super.onBackPressed();
    }
}
