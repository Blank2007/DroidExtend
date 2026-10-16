package com.droidextend;

/**
 * The terms and privacy notice, phone side.
 *
 * Mirrors host/privacy.py: same idea, same version gate, and the body is a list
 * of lines rather than prose baked into the dialog builder, so the two
 * languages provably stay in step.
 *
 * The version is stored as a string in Prefs, not a boolean, so changing the
 * text later re-asks everyone instead of silently applying to people who agreed
 * to something else.
 */
public final class Terms {

    /** Bump whenever the wording changes in a way people should see again. */
    public static final String VERSION = "1";

    private Terms() {
    }

    public static boolean needsAccept(String accepted) {
        return accepted == null || !VERSION.equals(accepted.trim());
    }

    public static String title() {
        return Lang.t("使用条款与隐私说明", "Terms of use and privacy");
    }

    public static String agree() {
        return Lang.t("同意并继续", "Agree and continue");
    }

    public static String decline() {
        return Lang.t("不同意，退出", "Decline and quit");
    }

    public static String body() {
        StringBuilder b = new StringBuilder();
        for (String line : lines()) {
            b.append(line).append('\n');
        }
        return b.toString();
    }

    /**
     * One entry per paragraph.  Kept as an array so a test can assert that both
     * languages have the same count -- a translation that quietly drops a clause
     * is a legal problem, not a cosmetic one.
     */
    static String[] lines() {
        return new String[] {
            Lang.t("一、这个程序做什么", "1. What this app does"),
            Lang.t(
                "DroidExtend 用一根 USB 数据线，把安卓手机或平板变成 Windows 的第二块显示器，"
                + "并让手机的触摸屏反过来控制 Windows。它全程只在你自己的电脑与手机之间工作，"
                + "不连接任何服务器。",
                "DroidExtend turns this phone or tablet into a second Windows monitor over a "
                + "single USB cable, and lets the touchscreen drive Windows in return. It "
                + "works only between your own PC and your own phone, and it never connects "
                + "to any server."),

            Lang.t("二、它不收集任何东西", "2. It collects nothing"),
            Lang.t("· 不收集、不上传、不分享任何个人信息、使用数据或屏幕内容。",
                   "· No personal information, usage data or screen content is collected, "
                   + "uploaded or shared."),
            Lang.t("· 屏幕画面只经 USB 线发给这台电脑，不出这台机器。",
                   "· The screen picture is sent only to this PC over the USB cable. It does "
                   + "not leave the machine."),
            Lang.t("· 手机端只在手机本地保存自己的偏好设置。",
                   "· This app stores its preferences locally on the phone only."),

            Lang.t("三、手机端需要的能力", "3. What this app needs"),
            Lang.t("· 录屏（MediaProjection）：把屏幕画面编码后发给电脑。系统会弹窗向你确认。",
                   "· Screen capture (MediaProjection): encodes the screen and sends it to the "
                   + "PC. Android asks you to confirm this."),
            Lang.t("· 输入注入：把你在手机上的点击、拖动、滚动送到电脑。这是本程序的核心功能。",
                   "· Input injection: sends your taps, drags and scrolls to the PC. This is "
                   + "the core feature, not a side effect."),
            Lang.t("· 保持屏幕唤醒：在你使用期间不让系统息屏，连接才不会断。",
                   "· Keeping the screen awake: stops the system from sleeping while you use "
                   + "it, so the connection stays up."),

            Lang.t("四、风险与免责", "4. Risk and disclaimer"),
            Lang.t("· 本程序按「现状」提供，不附带任何明示或暗示的担保。",
                   "· This app is provided \"as is\", without warranty of any kind, express or "
                   + "implied."),
            Lang.t("· 它会在电脑上创建一块虚拟屏，改动显示配置，并安装或卸载显示驱动。"
                   + "请自行确认这些操作在你的设备上是被允许的。",
                   "· It creates a virtual display on the PC, changes the display "
                   + "configuration, and installs or removes a display driver. Make sure that "
                   + "is allowed on your device."),
            Lang.t("· 因使用或无法使用本程序造成的任何损失，作者不承担责任。",
                   "· The author is not liable for any loss caused by using, or being unable "
                   + "to use, this app."),
            Lang.t("· 请只在你本人拥有、或已获明确授权的设备上使用。未经许可远程控制他人设备"
                   + "是违法的。",
                   "· Use it only on devices you own or are explicitly authorised to use. "
                   + "Controlling someone else's device without permission is illegal."),

            Lang.t("五、许可", "5. Licence"),
            Lang.t("本程序以 MIT 许可开源。第三方组件许可见电脑端程序目录下的"
                   + "「第三方组件许可.txt」。",
                   "This app is open source under the MIT licence. Third-party notices are in "
                   + "第三方组件许可.txt in the PC program's folder."),

            Lang.t("点「同意并继续」表示你已阅读、理解并接受以上全部内容。",
                   "Choosing \"Agree and continue\" means you have read, understood and accept "
                   + "all of the above."),
        };
    }
}
