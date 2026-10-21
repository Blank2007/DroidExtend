package com.droidextend;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.util.TypedValue;
import android.view.Display;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.CompoundButton;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
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

    /**
     * Hold this long without moving and it is a right click, the way every
     * other remote-desktop client on a touch screen does it.  Matches
     * ViewConfiguration.getLongPressTimeout().
     */
    private static final long LONG_PRESS_MS = 500;

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
    private BroadcastReceiver screenReceiver;
    /** The frame rate the host announced, 0 until the first handshake. */
    private int streamFps;
    /** #1: true while the terms dialog is up, so a second surface cannot stack two. */
    private boolean privacyAsking;

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
    /** Where the current one-finger gesture landed, in view coordinates. */
    private float downX, downY;
    /** Set once the finger has moved past touchSlop; a drag, not a tap. */
    private boolean dragging;
    /** Set when the long press already sent its right click. */
    private boolean longPressed;
    /**
     * How far a finger may wander before a tap becomes a drag -- the platform's
     * own touch slop, in the platform's own units.
     *
     * Windows has a drag threshold of a few pixels, and every real finger
     * wobbles more than that the moment it lands.  Without a dead zone the
     * tiniest tremor turns a click into a drag: files get moved instead of
     * opened, windows get dragged instead of focused.
     */
    private int touchSlop;

    private float dp(float v) {
        return v * getResources().getDisplayMetrics().density;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);
        // Apply the language before any text is built: every label goes through
        // Lang.t(), so setting it later would leave the window half translated.
        Lang.set(Lang.resolve(prefs.lang(), getResources().getConfiguration().locale));
        AppLog.init(this);
        AppLog.setEnabled(prefs.logging());
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        hideSystemUi();

        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        touchSlop = ViewConfiguration.get(this).getScaledTouchSlop();

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
        registerScreenReceiver();

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
            // Coming back from the background, the system may have picked a
            // different panel mode while we were gone; say it again.
            if (streamFps > 0) {
                requestFrameRate(streamFps);
            }
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
        title.setText(Lang.t("显示设置", "Display settings"));
        title.setTextColor(getResources().getColor(R.color.hud_text));
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        card.addView(title);

        // The language picker goes first: it is the one setting that changes
        // the meaning of every other line in this panel.
        Button langBtn = button(Lang.t("语言：", "Language: ") + Lang.label(prefs.lang()));
        langBtn.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                final String[] codes = Lang.choices();
                String[] names = new String[codes.length];
                for (int i = 0; i < codes.length; i++) {
                    names[i] = Lang.label(codes[i]);
                }
                new AlertDialog.Builder(MainActivity.this)
                        .setTitle(Lang.t("语言", "Language"))
                        .setItems(names, new DialogInterface.OnClickListener() {
                            @Override public void onClick(DialogInterface d, int which) {
                                String picked = codes[which];
                                if (picked.equals(prefs.lang())) {
                                    return;
                                }
                                prefs.setLang(picked);
                                AppLog.i("lang  语言切换到 " + picked);
                                // Everything already on screen was built in the
                                // old language, and retranslating a live view
                                // tree is more machinery than this app needs:
                                // rebuild instead.
                                recreate();
                            }
                        })
                        .show();
            }
        });
        card.addView(langBtn);

        card.addView(check(Lang.t("状态浮窗（码率 / 帧率 / 延迟）",
                        "Status overlay (bitrate / fps / latency)"), prefs.showHud(),
                new CompoundButton.OnCheckedChangeListener() {
                    @Override public void onCheckedChanged(CompoundButton b, boolean on) {
                        prefs.set(Prefs.SHOW_HUD, on);
                        hud.setPanelWanted(on);
                    }
                }));

        card.addView(check(Lang.t("让画面铺满屏幕（裁掉多余边缘）",
                        "Fill the screen (crop the extra edges)"), prefs.fill(),
                new CompoundButton.OnCheckedChangeListener() {
                    @Override public void onCheckedChanged(CompoundButton b, boolean on) {
                        prefs.set(Prefs.FILL, on);
                        surfaceView.setFill(on);
                    }
                }));

        // --- 触摸操作 ---------------------------------------------------
        // The gestures are the whole point of the app and not one of them is
        // guessable, so they are written down here, where somebody holding the
        // phone will actually find them -- not only in the README.
        TextView touchTitle = new TextView(this);
        touchTitle.setText(Lang.t("触摸操作", "Touch controls"));
        touchTitle.setTextColor(getResources().getColor(R.color.hud_text));
        touchTitle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        touchTitle.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        touchTitle.setPadding(0, Math.round(dp(14)), 0, Math.round(dp(4)));
        card.addView(touchTitle);

        TextView touchHelp = new TextView(this);
        touchHelp.setText(touchHelp());
        touchHelp.setTextColor(getResources().getColor(R.color.hud_dim));
        touchHelp.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f);
        touchHelp.setLineSpacing(0f, 1.35f);
        card.addView(touchHelp);

        final LinearLayout logBox = new LinearLayout(this);
        logBox.setOrientation(LinearLayout.VERTICAL);
        logBox.setVisibility(prefs.logging() ? View.VISIBLE : View.GONE);

        TextView logPath = new TextView(this);
        logPath.setText(Lang.t("日志文件：", "Log file: ") + "\n" + AppLog.path());
        logPath.setTextColor(getResources().getColor(R.color.hud_dim));
        logPath.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f);
        logPath.setTextIsSelectable(true);
        logBox.addView(logPath);

        Button clear = new Button(this);
        clear.setText(Lang.t("清空日志", "Clear log"));
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
                Toast.makeText(MainActivity.this, Lang.t("已清空日志", "Log cleared"),
                        Toast.LENGTH_SHORT).show();
            }
        });
        logBox.addView(clear);
        card.addView(logBox);

        card.addView(check(Lang.t("把日志写到文件（默认关闭）",
                        "Write the log to a file (off by default)"), prefs.logging(),
                new CompoundButton.OnCheckedChangeListener() {
                    @Override public void onCheckedChanged(CompoundButton b, boolean on) {
                        prefs.set(Prefs.LOGGING, on);
                        AppLog.setEnabled(on);
                        logBox.setVisibility(on ? View.VISIBLE : View.GONE);
                    }
                }));

        // Motion is on by default, but a phone that stutters is a phone whose
        // owner will blame the stream -- so it stays switchable.
        card.addView(check(Lang.t("界面动画", "Interface animation"), prefs.anim(),
                new CompoundButton.OnCheckedChangeListener() {
                    @Override public void onCheckedChanged(CompoundButton b, boolean on) {
                        prefs.set(Prefs.ANIM, on);
                    }
                }));

        // --- 后台保活 ---------------------------------------------------
        // The single most common "it just stops working" report on Android is
        // the system deciding we are not worth keeping.  Both fixes live in
        // manufacturer settings pages, so all we can do is hand out the links.
        TextView kaTitle = new TextView(this);
        kaTitle.setText(Lang.t("后台保活", "Staying alive in the background"));
        kaTitle.setTextColor(getResources().getColor(R.color.hud_text));
        kaTitle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        kaTitle.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        kaTitle.setPadding(0, Math.round(dp(14)), 0, Math.round(dp(4)));
        card.addView(kaTitle);

        final TextView kaState = new TextView(this);
        kaState.setText(KeepAlive.stateText(this));
        kaState.setTextColor(KeepAlive.batteryRestricted(this)
                ? getResources().getColor(R.color.accent)
                : getResources().getColor(R.color.hud_dim));
        kaState.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f);
        card.addView(kaState);

        Button kaBat = button(Lang.t("关掉省电限制", "Turn off battery limits"));
        kaBat.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                openSettings(KeepAlive.batteryIntent(MainActivity.this));
                // The system dialog is a separate activity, so re-read the
                // answer when we get back instead of guessing it now.
                kaState.postDelayed(new Runnable() {
                    @Override public void run() {
                        kaState.setText(KeepAlive.stateText(MainActivity.this));
                    }
                }, 1500);
            }
        });
        card.addView(kaBat);

        final Intent vendor = KeepAlive.autostartIntent(this);
        Button kaAuto = button(vendor != null
                ? Lang.t("打开自启动管理", "Open auto-start manager")
                : Lang.t("打开应用信息", "Open app info"));
        kaAuto.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                openSettings(vendor != null ? vendor : KeepAlive.detailsIntent(MainActivity.this));
            }
        });
        card.addView(kaAuto);

        TextView kaHint = new TextView(this);
        kaHint.setText(keepAliveHint());
        kaHint.setTextColor(getResources().getColor(R.color.hud_dim));
        kaHint.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f);
        kaHint.setPadding(0, Math.round(dp(6)), 0, 0);
        card.addView(kaHint);

        TextView hint = new TextView(this);
        hint.setText(Lang.t(
                "浮窗只占用左上角，右侧齿轮随时可以打开这里；其它位置的点按都会照常传到电脑。",
                "The overlay only covers the top-left corner, and the gear on the right opens this "
                        + "panel at any time; taps anywhere else reach the PC as usual."));
        hint.setTextColor(getResources().getColor(R.color.hud_dim));
        hint.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f);
        hint.setPadding(0, Math.round(dp(10)), 0, 0);
        card.addView(hint);

        Button close = new Button(this);
        close.setText(Lang.t("关闭", "Close"));
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

        // The panel now carries the touch-controls list and no longer fits on
        // every screen, so it scrolls instead of pushing the Close button off
        // the bottom edge where nothing can reach it.
        ScrollView scroll = new ScrollView(this);
        scroll.setVerticalScrollBarEnabled(false);
        scroll.addView(card);
        FrameLayout.LayoutParams cl = new FrameLayout.LayoutParams(
                Math.min(Math.round(dp(560)), getResources().getDisplayMetrics().widthPixels
                        - Math.round(dp(56))),
                ViewGroup.LayoutParams.WRAP_CONTENT);
        cl.gravity = Gravity.CENTER;
        int margin = Math.round(dp(24));
        cl.topMargin = margin;
        cl.bottomMargin = margin;
        scrim.addView(scroll, cl);

        sheet = scrim;
        root.addView(sheet, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        Anim.in(sheet, this, prefs);
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
        feel(cb);
        return cb;
    }

    /**
     * A secondary button, styled like the "清空日志" one.
     */
    private Button button(String text) {
        Button b = new Button(this);
        b.setText(text);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        b.setTextColor(getResources().getColor(R.color.hud_text));
        b.setBackgroundTintList(ColorStateList.valueOf(0x33FFFFFF));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = Math.round(dp(6));
        b.setLayoutParams(lp);
        feel(b);
        return b;
    }

    /**
     * Give a control its press acknowledgement.
     *
     * <p>The listener returns false, which is the whole trick: the touch carries
     * on to the real click handler, so this only ever adds a flinch.  A listener
     * that returned true here would silently swallow every button in the panel.
     */
    private void feel(final View v) {
        v.setOnTouchListener(new View.OnTouchListener() {
            @Override public boolean onTouch(View view, MotionEvent e) {
                if (e.getActionMasked() == MotionEvent.ACTION_DOWN) {
                    Anim.tap(view, MainActivity.this, prefs);
                }
                return false;
            }
        });
    }

    /**
     * Fire a settings intent, surviving the phones that do not have the page.
     * An OEM settings screen is not part of the public API and can vanish in an
     * update, so a missing one is a normal outcome, not a crash.
     */
    private void openSettings(Intent i) {
        try {
            startActivity(i);
        } catch (Throwable t) {
            AppLog.w("ui    打不开设置页", t);
            Toast.makeText(this, Lang.t("这台手机没有这个设置页", "This phone has no such settings page"),
                    Toast.LENGTH_SHORT).show();
        }
    }

    /** The words under the keep-alive buttons. */
    private String keepAliveHint() {
        String lock = Lang.t(
                "在「最近任务」里把本应用下拉锁定，按「清理全部」时才不会被一起清掉。",
                "Lock this app in the recents list (pull its card down) so \"clear all\" spares it.");
        if (!KeepAlive.isStrictVendor()) {
            return lock;
        }
        return Lang.t(
                "这台手机的系统对后台管得比较严，建议同时把本应用加进自启动白名单。",
                "This phone's system manages background apps strictly -- also add us to its auto-start whitelist.")
                + "\n" + lock;
    }

    /** Every gesture this app understands, spelled out in the settings panel. */
    private String touchHelp() {
        return Lang.t(
                "单指点击　　　　单击左键\n"
                + "单指连点两下　　双击左键\n"
                + "单指拖动　　　　按住左键拖动（选中、移动窗口、拉选框）\n"
                + "单指长按半秒　　单击右键\n"
                + "双指上下滑动　　滚轮\n"
                + "双指快速轻点　　单击右键\n"
                + "\n"
                + "手指落下时那一点点抖动不会被当成拖动，点一下就是点一下。"
                + "拖动的位置按画面帧率发送（约每秒 60 次），并且只发最新的那一个；"
                + "但按下、抬起、右键、滚轮一个都不会少。"
                + "连点两下要落在同一处附近才算双击，程序会把第二下对齐到第一下的位置。",
                "one finger, tap          left click\n"
                + "one finger, tap twice    left double-click\n"
                + "one finger, drag         drag with the left button (select, move a\n"
                + "                         window, drag a box)\n"
                + "one finger, hold 1/2 s   right click\n"
                + "two fingers, slide       wheel\n"
                + "two fingers, quick tap   right click\n"
                + "\n"
                + "The wobble of a finger landing is not treated as a drag: a tap is a "
                + "tap. Drag positions go out at the frame rate (about 60 a second) and "
                + "only the newest one is sent, but presses, releases, right clicks and "
                + "wheel turns are never dropped. Two taps count as a double-click only "
                + "when they land near each other, and the second one is snapped onto "
                + "the first.");
    }

    private void hideSettings() {
        if (sheet == null) {
            return;
        }
        // Take it out of the field first: a second tap while the fade is still
        // running must not be able to find it again, and the callback below
        // removes the view it was given, not whatever "sheet" points at then.
        final View gone = sheet;
        sheet = null;
        Anim.out(gone, this, prefs, new Runnable() {
            @Override
            public void run() {
                root.removeView(gone);
            }
        });
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

    /**
     * Where a tap is released.
     *
     * A tap releases where it went down, not wherever the finger had drifted
     * to by the time it came up.  The dead zone already stops that drift from
     * moving the cursor, but the release still went to the drifted spot: a few
     * pixels of nudge on whatever small thing was under the finger, and enough
     * to push two taps outside the little rectangle Windows insists both of
     * them land inside before it will call them a double click.  A real drag
     * still ends wherever the finger actually is.
     *
     * (getX() is pointer 0, which is the finger that went down first in every
     * case these two are called from.)
     */
    private float tapUpX(MotionEvent e) {
        return nx(dragging ? e.getX() : downX);
    }

    private float tapUpY(MotionEvent e) {
        return ny(dragging ? e.getY() : downY);
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

    /**
     * Hold still and it is a right click -- the gesture every other
     * remote-desktop client on a touch screen uses.
     *
     * The left button goes up first.  The PC has been holding it down since the
     * initial ACTION_DOWN, and a right click on top of a held left button is a
     * different thing entirely (Windows reads it as a drag).
     */
    private final Runnable longPress = new Runnable() {
        @Override public void run() {
            if (gesture != 1 || dragging || longPressed) {
                return;
            }
            longPressed = true;
            input.touch(InputClient.UP, nx(downX), ny(downY));
            input.touch(InputClient.RCLICK, nx(downX), ny(downY));
            AppLog.i("touch long press -> right click at " + downX + "," + downY);
        }
    };

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
                dragging = false;
                longPressed = false;
                downX = e.getX();
                downY = e.getY();
                input.touch(InputClient.DOWN, nx(downX), ny(downY));
                surfaceView.postDelayed(longPress, LONG_PRESS_MS);
                return true;

            case MotionEvent.ACTION_POINTER_DOWN:
                // A second finger arrived: the drag we started is over, and
                // from here on this is a scroll (or a right-click tap).
                surfaceView.removeCallbacks(longPress);
                if (gesture == 1 && !longPressed) {
                    input.touch(InputClient.UP, tapUpX(e), tapUpY(e));
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
                    if (!dragging) {
                        if (Math.hypot(e.getX() - downX, e.getY() - downY) <= touchSlop) {
                            // Still a tap.  A finger is never perfectly still when
                            // it lands, and forwarding that tremor is what makes a
                            // click come out as a drag on the PC.
                            return true;
                        }
                        dragging = true;
                        surfaceView.removeCallbacks(longPress);
                    }
                    if (!longPressed) {
                        input.touch(InputClient.MOVE, nx(e.getX()), ny(e.getY()));
                    }
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
                surfaceView.removeCallbacks(longPress);
                if (gesture == 1 && !longPressed) {
                    input.touch(InputClient.UP, tapUpX(e), tapUpY(e));
                }
                gesture = 0;
                dragging = false;
                longPressed = false;
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
        if (streamFps > 0) {
            requestFrameRate(streamFps);
        }
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

    /**
     * #1: the terms, once, before anything is captured or sent.
     *
     * Not cancellable, and declining closes the activity -- there is no "later"
     * on offer, because later would mean running without them.
     */
    private void askPrivacy() {
        new AlertDialog.Builder(this)
                .setTitle(Terms.title())
                .setMessage(Terms.body())
                .setCancelable(false)
                .setPositiveButton(Terms.agree(), new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        prefs.setPrivacy(Terms.VERSION);
                        privacyAsking = false;
                        AppLog.i("已接受使用条款与隐私说明 v" + Terms.VERSION);
                        startClientOnce();
                    }
                })
                .setNegativeButton(Terms.decline(), new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        AppLog.i("没有同意使用条款，退出。");
                        finish();
                    }
                })
                .show();
    }

    private void startClientOnce() {
        if (started) {
            return;
        }
        if (Terms.needsAccept(prefs.privacy())) {
            if (!privacyAsking) {
                privacyAsking = true;
                askPrivacy();
            }
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

            @Override public void onVideoSize(final int w, final int h, final int fps) {
                runOnUiThread(new Runnable() {
                    @Override public void run() {
                        if (surfaceView != null && w > 0 && h > 0) {
                            surfaceView.setAspect(w / (float) h);
                        }
                        requestFrameRate(fps);
                    }
                });
            }

            @Override public void onStats(final Stats s) {
                // The panel rate is a property of the phone, not of the link;
                // it is filled in here because only the Activity has a Context.
                if (s.screenHz <= 0) {
                    s.screenHz = panelHz();
                }
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
        }, currentPanelHz());
        client.start();
        AppLog.i("client started");
    }

    /**
     * Ask the panel to run at the rate the stream is produced at.
     *
     * Nothing else does, and the default is not "the fastest": measured on a
     * 144 Hz phone fed a 144 fps stream, this app declared no frame rate, so
     * SurfaceFlinger settled on 50 Hz and two frames out of three were decoded
     * and never composited -- the overlay reported 143 rendered frames while
     * the panel updated 50 times a second.  Declaring the rate, together with
     * the matching display mode, is what makes the compositor pick a faster
     * panel mode.
     */
    private void requestFrameRate(int fps) {
        if (fps <= 0) {
            return;
        }
        streamFps = fps;
        try {
            Display d = getWindowManager().getDefaultDisplay();
            WindowManager.LayoutParams lp = getWindow().getAttributes();
            Display.Mode want = pickMode(d, fps);
            boolean changed = false;
            if (want != null) {
                if (Build.VERSION.SDK_INT >= 23 && lp.preferredDisplayModeId != want.getModeId()) {
                    lp.preferredDisplayModeId = want.getModeId();
                    changed = true;
                }
                if (Math.abs(lp.preferredRefreshRate - want.getRefreshRate()) > 0.01f) {
                    lp.preferredRefreshRate = want.getRefreshRate();
                    changed = true;
                }
            }
            if (changed) {
                getWindow().setAttributes(lp);
            }
            // Android 11+ can also declare the content rate on the surface
            // itself.  The window hint above is what picks the mode; this one
            // keeps the layer's frame-rate override right while the stream
            // runs, and it still works on ROMs that ignore the window hint.
            if (Build.VERSION.SDK_INT >= 30 && surfaceView != null) {
                Surface s = surfaceView.getHolder().getSurface();
                if (s != null && s.isValid()) {
                    s.setFrameRate(fps, Surface.FRAME_RATE_COMPATIBILITY_FIXED_SOURCE);
                }
            }
            AppLog.i("display " + fps + "fps -> mode "
                    + (want == null ? "?"
                    : want.getModeId() + "@" + Math.round(want.getRefreshRate())
                    + (changed ? " requested" : " already set"))
                    + ", panel now " + Math.round(d.getRefreshRate()) + "Hz");
        } catch (Throwable t) {
            AppLog.w("request frame rate failed: " + t);
        }
    }

    /**
     * The display mode closest to `fps`, preferring the faster one on a tie.
     *
     * A mode faster than the stream repeats a frame now and then; a slower one
     * drops frames, which is the one that looks bad.  Either is better than the
     * system's own choice for an app that declares nothing.
     */
    private Display.Mode pickMode(Display d, int fps) {
        if (Build.VERSION.SDK_INT < 23) {
            return null;
        }
        Display.Mode[] modes = d.getSupportedModes();
        if (modes == null || modes.length == 0) {
            return null;
        }
        Display.Mode best = modes[0];
        for (int i = 1; i < modes.length; i++) {
            float a = Math.abs(modes[i].getRefreshRate() - fps);
            float b = Math.abs(best.getRefreshRate() - fps);
            if (a < b - 0.01f
                    || (Math.abs(a - b) <= 0.01f && modes[i].getRefreshRate() > best.getRefreshRate())) {
                best = modes[i];
            }
        }
        return best;
    }

    /**
     * The refresh rate this panel is really running at, or 0 when it cannot be
     * read.
     *
     * This is what the host is told in the hello, so it can match the capture
     * rate to the screen instead of to the fps the user happened to pick.
     *
     * The mode table is the wrong source: it advertises a 144 Hz mode on this
     * panel, but MIUI never selects it for an ordinary app -- measured, dumpsys
     * display stays pinned at peakRefreshRate=120 while getSupportedModes()
     * still lists 144.  Reporting the table maximum makes the PC encode 144
     * fps into a 120 Hz panel, which is the stutter this handshake exists to
     * remove: the decoder runs out of throughput, the socket backlogs, and the
     * app jumps from keyframe to keyframe every few seconds.
     */
    private int currentPanelHz() {
        try {
            int hz = (int) Math.round(panelHz());
            return hz >= 24 && hz <= 300 ? hz : 0;
        } catch (Throwable t) {
            return 0;
        }
    }

    /**
     * The panel's current refresh rate, or 0 when it cannot be read.
     *
     * This is not the stream rate: the PC captures at whatever fps is
     * configured, and the phone decodes at that rate regardless of how fast the
     * screen refreshes.  The overlay shows both so the two are not confused.
     */
    private double panelHz() {
        try {
            Display d = getWindowManager().getDefaultDisplay();
            return d.getRefreshRate();
        } catch (Throwable t) {
            return 0;
        }
    }

    /**
     * Tell the PC when the phone's screen goes off or comes back.
     *
     * The stream itself does not stop when the screen goes off -- Android just
     * stops compositing -- so without this the PC shows a frozen frame and has
     * no idea why.  Both are protected system broadcasts, which is why no
     * export flag is needed on API 33+.
     */
    private void registerScreenReceiver() {
        screenReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                if (input == null) {
                    return;
                }
                String action = intent.getAction();
                if (Intent.ACTION_SCREEN_OFF.equals(action)) {
                    input.notice(InputClient.SCREEN_OFF);
                } else if (Intent.ACTION_SCREEN_ON.equals(action)) {
                    input.notice(InputClient.SCREEN_ON);
                }
            }
        };
        IntentFilter filter = new IntentFilter();
        filter.addAction(Intent.ACTION_SCREEN_OFF);
        filter.addAction(Intent.ACTION_SCREEN_ON);
        registerReceiver(screenReceiver, filter);
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
        if (screenReceiver != null) {
            try {
                unregisterReceiver(screenReceiver);
            } catch (Throwable ignored) {
                // Already gone -- nothing to undo.
            }
            screenReceiver = null;
        }
        stopStream();
        super.onDestroy();
    }

    private void stopStream() {
        // Only the first caller gets to say goodbye: onPause and onDestroy both
        // land here when the user backs out, and the PC should hear it once.
        boolean wasStarted = started;
        started = false;
        if (client != null) {
            client.shutdown();
            client = null;
        }
        if (input != null) {
            if (wasStarted) {
                // Without the flush the record we just queued dies with the
                // socket, which is exactly the "the app vanished" message the
                // PC needs to distinguish this from a yanked cable.
                input.notice(InputClient.APP_GONE);
                input.flush(400);
            }
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
