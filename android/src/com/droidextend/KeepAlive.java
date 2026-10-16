package com.droidextend;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;

import java.util.Locale;

/**
 * Points the user at the Android settings that decide whether a screen sharing
 * app is allowed to keep running: the battery whitelist and the vendor's own
 * "auto start" manager.
 *
 * Only those two have intents we can fire.  The third trick -- locking the app
 * in the recents list so "clear all" spares it -- is a gesture with no public
 * intent, so it is explained in words instead.
 */
public final class KeepAlive {

    private KeepAlive() {
    }

    /** True when Android is still free to kill us to save power. */
    public static boolean batteryRestricted(Context c) {
        try {
            PowerManager pm = (PowerManager) c.getSystemService(Context.POWER_SERVICE);
            return pm != null && !pm.isIgnoringBatteryOptimizations(c.getPackageName());
        } catch (Throwable t) {
            // No answer is not a reason to nag: say "fine" and move on.
            return false;
        }
    }

    /** The system dialog that puts us on the battery whitelist. */
    public static Intent batteryIntent(Context c) {
        Intent i = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
        i.setData(Uri.parse("package:" + c.getPackageName()));
        return i;
    }

    /** App info page -- the fallback when this phone has no vendor page. */
    public static Intent detailsIntent(Context c) {
        Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
        i.setData(Uri.parse("package:" + c.getPackageName()));
        return i;
    }

    /**
     * Vendor auto start managers, most specific first.  These are not public
     * API: an OEM is free to rename or drop one in any update, so every entry
     * is probed with resolveActivity() before it is offered.
     *
     * Note the pairs are (package of the app, fully qualified class name), and
     * the class does NOT have to live under that package -- Xiaomi's auto start
     * screen is com.miui.permcenter.* inside the com.miui.securitycenter app,
     * and Samsung's is com.samsung.android.sm.* inside com.samsung.android.lool.
     */
    private static final String[][] AUTOSTART = {
            {"com.miui.securitycenter", "com.miui.permcenter.autostart.AutoStartManagementActivity"},
            {"com.huawei.systemmanager", "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity"},
            {"com.huawei.systemmanager", "com.huawei.systemmanager.optimize.process.ProtectActivity"},
            {"com.honor.systemmanager", "com.honor.systemmanager.startupmgr.ui.StartupNormalAppListActivity"},
            {"com.coloros.safecenter", "com.coloros.safecenter.permission.startup.StartupAppListActivity"},
            {"com.coloros.safecenter", "com.coloros.safecenter.startupapp.StartupAppListActivity"},
            {"com.oppo.safe", "com.oppo.safe.permission.startup.StartupAppListActivity"},
            {"com.vivo.permissionmanager", "com.vivo.permissionmanager.activity.BgStartUpManagerActivity"},
            {"com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.AddWhiteListActivity"},
            {"com.samsung.android.lool", "com.samsung.android.sm.ui.battery.BatteryActivity"},
            {"com.meizu.safe", "com.meizu.safe.security.SHOW_APPSEC"},
            {"com.letv.android.letvsafe", "com.letv.android.letvsafe.AutobootManageActivity"},
            {"com.lenovo.security", "com.lenovo.security.optimize.AutoStartupActivity"},
    };

    /** This phone's auto start manager, or null when it has none we know of. */
    public static Intent autostartIntent(Context c) {
        for (String[] pair : AUTOSTART) {
            try {
                Intent i = new Intent();
                i.setComponent(new ComponentName(pair[0], pair[1]));
                if (c.getPackageManager().resolveActivity(i, 0) != null) {
                    return i;
                }
            } catch (Throwable ignored) {
                // A malformed component name must not stop us trying the rest.
            }
        }
        return null;
    }

    /** The manufacturer as the user would write it, e.g. "Xiaomi". */
    public static String vendor() {
        String m = Build.MANUFACTURER == null ? "" : Build.MANUFACTURER.trim();
        if (m.isEmpty()) {
            return "";
        }
        return Character.toUpperCase(m.charAt(0)) + m.substring(1);
    }

    /** True for the phones whose battery saver is known to be aggressive. */
    public static boolean isStrictVendor() {
        String m = (Build.MANUFACTURER == null ? "" : Build.MANUFACTURER).toLowerCase(Locale.ROOT);
        return m.contains("xiaomi") || m.contains("redmi") || m.contains("huawei")
                || m.contains("honor") || m.contains("oppo") || m.contains("realme")
                || m.contains("vivo") || m.contains("iqoo") || m.contains("oneplus")
                || m.contains("meizu") || m.contains("samsung") || m.contains("transsion")
                || m.contains("tecno") || m.contains("infinix");
    }

    /** The one-line result shown next to the buttons. */
    public static String stateText(Context c) {
        if (!batteryRestricted(c)) {
            return Lang.t("省电限制：已关闭（后台不会被系统杀掉）",
                    "Battery limits: off (Android will not kill us in the background)");
        }
        return Lang.t("省电限制：还开着，后台随时可能被杀",
                "Battery limits: still on, we can be killed in the background at any time");
    }
}
