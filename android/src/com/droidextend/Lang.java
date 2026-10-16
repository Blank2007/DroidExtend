package com.droidextend;

import java.util.Locale;

/**
 * Two-language table for the phone app.
 *
 * Same idea as the host's host/i18n.py: {@code Lang.t("中文", "English")} right
 * at the call site, no resource XML to keep in sync, no build step -- which
 * matters here because this app is built by a hand-rolled aapt2 + javac
 * pipeline with no Gradle.
 *
 * The choice is stored in Prefs under {@link Prefs#LANG} and may be "auto",
 * in which case the system locale decides.  Changing it restarts the activity
 * (see MainActivity) rather than retranslating live.
 */
public final class Lang {

    public static final String ZH = "zh";
    public static final String EN = "en";
    public static final String AUTO = "auto";

    private static String current = ZH;

    private Lang() {
    }

    /** Pick a language from a system locale: English only for English locales. */
    public static String fromSystem(Locale loc) {
        if (loc != null && "en".equalsIgnoreCase(loc.getLanguage())) {
            return EN;
        }
        return ZH;
    }

    /** Turn the stored setting into a concrete code. */
    public static String resolve(String setting, Locale system) {
        if (ZH.equals(setting) || EN.equals(setting)) {
            return setting;
        }
        return fromSystem(system);
    }

    public static void set(String code) {
        current = EN.equals(code) ? EN : ZH;
    }

    public static String get() {
        return current;
    }

    public static boolean en() {
        return EN.equals(current);
    }

    /** The one function every user-visible string goes through. */
    public static String t(String zh, String english) {
        return en() ? english : zh;
    }

    /** Display name of a language code, for the picker. */
    public static String label(String code) {
        if (ZH.equals(code)) {
            return "中文";
        }
        if (EN.equals(code)) {
            return "English";
        }
        return t("跟随系统", "Follow system");
    }

    /** The choices, in the order the picker lists them. */
    public static String[] choices() {
        return new String[] {AUTO, ZH, EN};
    }
}
