"""Colour scheme and ttk styling.

Two palettes, one dict each, and every widget reads its colours out of the
active one -- so switching schemes means applying a different dict, not
hunting for literals scattered through app.py.  The scheme follows Windows
by default (Settings > 外观 can pin it to light or dark), which is what the
"follows the system" promise of the redesign actually costs.

The colours are OpenAI's published brand values: Charcoal #0E0F12, Mist
#F5F7FA, Steel #9EA1AA, and the green #10A37F.  Accent-by-blue is gone on
purpose: the only saturated colour left in the window is the status dot.
"""
import tkinter as tk
from tkinter import ttk

UI = "Microsoft YaHei UI"
MONO = "Consolas"

LIGHT = {
    "bg": "#F5F7FA",
    "fg": "#0E0F12",
    "dim": "#5D5D5D",
    "faint": "#8F8F8F",
    "line": "#DDE1E6",
    "hover": "#ECEEF1",
    "field": "#FFFFFF",
    "border": "#C9CED4",
    "btn": "#0E0F12",
    "btnfg": "#FFFFFF",
    "btn_hover": "#2A2C31",
    "btn2": "#FFFFFF",
    "btn2fg": "#0E0F12",
    "btn2_hover": "#EDEFF2",
    "mono_bg": "#0E0F12",
    "mono_fg": "#C9D1D9",
    "mono_dim": "#7A8290",
    "green": "#10A37F",
    "red": "#C4322B",
    "yellow": "#9A6B00",
    "sel": "#0E0F12",
    "sel_fg": "#FFFFFF",
    "chk": "#191A1E",
    "chk_fg": "#F5F7FA",
}

DARK = {
    "bg": "#0E0F12",
    "fg": "#ECECEC",
    "dim": "#9EA1AA",
    "faint": "#6E7178",
    "line": "#26282C",
    "hover": "#1B1D21",
    "field": "#141519",
    "border": "#313438",
    "btn": "#F5F7FA",
    "btnfg": "#0E0F12",
    "btn_hover": "#DCDEE2",
    "btn2": "#17181B",
    "btn2fg": "#ECECEC",
    "btn2_hover": "#222327",
    "mono_bg": "#0A0B0D",
    "mono_fg": "#C9D1D9",
    "mono_dim": "#6E7178",
    "green": "#10A37F",
    "red": "#FF7B72",
    "yellow": "#D6A233",
    "sel": "#F5F7FA",
    "sel_fg": "#0E0F12",
    "chk": "#3B3E44",
    "chk_fg": "#ECECEC",
}

PALETTES = {"light": LIGHT, "dark": DARK}
# 外观设置的三档：auto 问 Windows，另两个是用户把答案定死
MODES = ("auto", "light", "dark")

_PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def windows_is_light():
    """Does Windows itself paint app surfaces light right now?

    AppsUseLightTheme is what the Settings > Personalisation > Colours page
    switches, and it is the flag every native app follows.  Anything unreadable
    counts as dark, which is what this program used to look like anyway.
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE) as k:
            value, _kind = winreg.QueryValueEx(k, "AppsUseLightTheme")
        return int(value) != 0
    except Exception:
        return False


def resolve(mode):
    """Turn the stored preference ("auto"/"light"/"dark") into a palette name."""
    if mode == "light":
        return "light"
    if mode == "dark":
        return "dark"
    return "light" if windows_is_light() else "dark"


def option_add(root, pal):
    """The drop-down list of a ttk.Combobox is a classic Tk widget and a
    separate toplevel-ish window, so it is coloured through option_add and
    not through the style -- otherwise the list stays white in dark mode.
    """
    root.option_add("*TCombobox*Listbox.background", pal["field"])
    root.option_add("*TCombobox*Listbox.foreground", pal["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", pal["sel"])
    root.option_add("*TCombobox*Listbox.selectForeground", pal["sel_fg"])
    root.option_add("*TCombobox*Listbox.borderWidth", 0)


def apply_styles(style, pal):
    """Paint every ttk style from one palette.

    clam is the only built-in theme that lets a style be coloured this
    completely, and its defaults paint read-only comboboxes white -- which is
    what used to swallow the encoder/preset captions -- so the TCombobox maps
    below are load-bearing, not decoration.
    """
    bg, fg = pal["bg"], pal["fg"]
    style.configure(
        ".", background=bg, foreground=fg, fieldbackground=pal["field"],
        bordercolor=pal["border"], lightcolor=bg, darkcolor=bg,
        selectbackground=pal["sel"], selectforeground=pal["sel_fg"],
        focuscolor=pal["fg"], troughcolor=pal["field"])
    style.configure("TFrame", background=bg)
    style.configure("Mono.TFrame", background=pal["mono_bg"])

    style.configure("TLabel", background=bg, foreground=fg, font=(UI, 10))
    style.configure("Dim.TLabel", background=bg, foreground=pal["dim"], font=(UI, 10))
    style.configure("Faint.TLabel", background=bg, foreground=pal["faint"], font=(UI, 9))
    style.configure("Mono.TLabel", background=bg, foreground=pal["dim"], font=(MONO, 9))
    style.configure("H1.TLabel", background=bg, foreground=fg, font=(UI, 14, "bold"))
    style.configure("H2.TLabel", background=bg, foreground=fg, font=(UI, 11, "bold"))
    style.configure("Big.TLabel", background=bg, foreground=fg, font=(UI, 13, "bold"))
    style.configure("Metric.TLabel", background=bg, foreground=fg, font=(UI, 12, "bold"))
    style.configure("Key.TLabel", background=bg, foreground=pal["faint"], font=(UI, 10))
    style.configure("Value.TLabel", background=bg, foreground=fg, font=(UI, 10))
    style.configure("Green.TLabel", background=bg, foreground=pal["green"], font=(UI, 10))
    style.configure("Log.TLabel", background=pal["mono_bg"], foreground=pal["mono_dim"],
                    font=(MONO, 9))

    # -- buttons ---------------------------------------------------------
    style.configure("TButton", background=pal["btn2"], foreground=pal["btn2fg"],
                    bordercolor=pal["border"], lightcolor=pal["btn2"],
                    darkcolor=pal["btn2"], relief="flat", padding=(12, 6),
                    font=(UI, 10))
    style.map("TButton",
              background=[("disabled", pal["btn2"]), ("active", pal["btn2_hover"])],
              foreground=[("disabled", pal["faint"])],
              lightcolor=[("active", pal["btn2_hover"])],
              darkcolor=[("active", pal["btn2_hover"])],
              bordercolor=[("disabled", pal["line"])])
    style.configure("Primary.TButton", background=pal["btn"], foreground=pal["btnfg"],
                    bordercolor=pal["btn"], lightcolor=pal["btn"], darkcolor=pal["btn"],
                    relief="flat", padding=(20, 8), font=(UI, 10, "bold"))
    style.map("Primary.TButton",
              background=[("disabled", pal["btn2"]), ("active", pal["btn_hover"])],
              foreground=[("disabled", pal["faint"]), ("active", pal["btnfg"])],
              lightcolor=[("active", pal["btn_hover"])],
              darkcolor=[("active", pal["btn_hover"])],
              bordercolor=[("disabled", pal["line"])])
    # A button that looks like a link: 更多设置 / 复制 / 另存为 / 清空.
    style.configure("Link.TButton", background=bg, foreground=pal["dim"],
                    bordercolor=bg, lightcolor=bg, darkcolor=bg,
                    relief="flat", padding=(4, 2), font=(UI, 9))
    style.map("Link.TButton",
              background=[("active", bg)], foreground=[("active", fg)],
              lightcolor=[("active", bg)], darkcolor=[("active", bg)])
    # Same thing on the black log panel.
    style.configure("LogLink.TButton", background=pal["mono_bg"], foreground=pal["mono_dim"],
                    bordercolor=pal["mono_bg"], lightcolor=pal["mono_bg"],
                    darkcolor=pal["mono_bg"], relief="flat", padding=(6, 2),
                    font=(UI, 9))
    style.map("LogLink.TButton",
              background=[("active", pal["mono_bg"])],
              foreground=[("active", pal["mono_fg"])],
              lightcolor=[("active", pal["mono_bg"])],
              darkcolor=[("active", pal["mono_bg"])])
    # The menu row along the top.  A real Windows menu bar cannot be
    # recoloured -- it stays light on a dark page -- so the menus are
    # menubuttons drawn in the page colours.
    style.configure("Menu.TMenubutton", background=bg, foreground=pal["dim"],
                    borderwidth=0, relief="flat", padding=(10, 4), font=(UI, 10))
    style.map("Menu.TMenubutton",
              background=[("active", pal["hover"])],
              foreground=[("active", fg)],
              relief=[("active", "flat")])

    # -- inputs ----------------------------------------------------------
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=bg, foreground=fg, font=(UI, 10),
                        indicatorcolor=pal["field"], focuscolor=bg,
                        indicatorbackground=pal["field"],
                        indicatorforeground=pal["fg"],
                        upperbordercolor=pal["border"],
                        lowerbordercolor=pal["border"],
                        bordercolor=pal["border"],
                        lightcolor=pal["field"], darkcolor=pal["field"],
                        indicatormargin=4)
        style.map(name,
                  background=[("active", bg)],
                  foreground=[("disabled", pal["faint"])],
                  indicatorcolor=[("selected", pal["btn"]), ("!selected", pal["field"])],
                  indicatorbackground=[("selected", pal["btn"]), ("!selected", pal["field"])],
                  indicatorforeground=[("selected", pal["green"]), ("!selected", pal["dim"])],
                  focuscolor=[("active", bg)])
    style.configure("TCombobox", fieldbackground=pal["field"], background=pal["field"],
                    foreground=fg, arrowcolor=pal["dim"], bordercolor=pal["border"],
                    selectbackground=pal["field"], selectforeground=fg,
                    lightcolor=pal["field"], darkcolor=pal["field"],
                    padding=(8, 5), font=(UI, 10))
    style.map("TCombobox",
              fieldbackground=[("readonly", pal["field"]), ("disabled", pal["bg"])],
              foreground=[("readonly", fg), ("disabled", pal["faint"])],
              background=[("readonly", pal["field"]), ("active", pal["field"])],
              selectbackground=[("readonly", pal["field"])],
              selectforeground=[("readonly", fg)],
              arrowcolor=[("disabled", pal["faint"]), ("!disabled", pal["dim"])],
              bordercolor=[("focus", pal["dim"])])
    style.configure("TEntry", fieldbackground=pal["field"], foreground=fg,
                    bordercolor=pal["border"], lightcolor=pal["field"],
                    darkcolor=pal["field"], insertcolor=fg, padding=(6, 4))
    style.map("TEntry", bordercolor=[("focus", pal["dim"])])

    style.configure("TSeparator", background=pal["line"])
    style.configure("TScrollbar", background=pal["bg"], troughcolor=pal["bg"],
                    bordercolor=pal["bg"], arrowcolor=pal["dim"],
                    lightcolor=pal["bg"], darkcolor=pal["bg"], gripcount=0)
    style.map("TScrollbar", background=[("active", pal["faint"])])
    style.configure("TProgressbar", background=pal["fg"], troughcolor=pal["field"],
                    bordercolor=pal["border"], lightcolor=pal["fg"],
                    darkcolor=pal["fg"], thickness=6)
