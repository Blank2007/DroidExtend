"""Hand-drawn corners, because Tk cannot round one.

Every rounded shape in the window is a four-corner PhotoImage whose edge
pixels are blended by hand against the colour behind it: a 12x12 image with
exact anti-aliasing looks sharper than any amount of Canvas polygon smoothing,
and it needs no third-party imaging library.

Sizing follows the design sheet in logical pixels.  A frameless DPI-aware
window would otherwise shrink by the display scale, so every number that comes
out of here goes through px() first.
"""
import math
import tkinter as tk
import tkinter.font as tkfont

SCALE = 1.0


def set_scale(value):
    global SCALE
    SCALE = max(1.0, float(value))


def px(v):
    """A design-sheet number, in real pixels."""
    return int(round(v * SCALE))


def _rgb(color):
    color = color.lstrip("#")
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def blend(bg, fg, a):
    """Mix fg over bg; a = 0 keeps the background, a = 1 the foreground."""
    if a <= 0:
        return bg
    if a >= 1:
        return fg
    b, f = _rgb(bg), _rgb(fg)
    return _hex(tuple(b[i] + (f[i] - b[i]) * a for i in range(3)))


def corner_image(radius, fill, bg, border=None, corner="tl"):
    """One quarter disc of radius x radius, blended onto bg."""
    size = max(1, int(radius))
    img = tk.PhotoImage(width=size, height=size)
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            # the arc centre sits on the inner corner of this patch
            cx = 0.0 if corner in ("tr", "br") else float(size)
            cy = 0.0 if corner in ("bl", "br") else float(size)
            d = ((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) ** 0.5
            cover = size - d
            if cover <= 0:
                row.append(bg)
            elif border and cover < 1.0:
                row.append(blend(bg, border, min(1.0, cover)))
            elif border:
                row.append(border if cover < 1.6 else fill)
            else:
                row.append(blend(bg, fill, min(1.0, cover)))
        rows.append("{" + " ".join(row) + "}")
    img.put(" ".join(rows), to=(0, 0, size, size))
    return img


def draw_gear(cv, w, h, color, stroke=None, teeth=8):
    """A gear glyph: eight teeth on a ring around a hollow hub.

    Drawn from primitives so it keeps the same weight as the rest of the
    hand-drawn controls -- the stock U+2699 character renders at a different
    weight in every fallback font and never lined up with the window buttons.
    """
    t = px(stroke if stroke else 1.5)
    cx, cy = w / 2.0, h / 2.0
    R = min(w, h) / 2.0 - px(3.0)
    r_in = R * 0.70
    hub = R * 0.32
    for i in range(teeth):
        a = math.radians(i * (360.0 / teeth) - 90.0)
        ca, sa = math.cos(a), math.sin(a)
        cv.create_line(cx + ca * (r_in - t * 0.6), cy + sa * (r_in - t * 0.6),
                       cx + ca * R, cy + sa * R, fill=color, width=t,
                       capstyle="round")
    cv.create_oval(cx - r_in, cy - r_in, cx + r_in, cy + r_in,
                   outline=color, width=t)
    cv.create_oval(cx - hub, cy - hub, cx + hub, cy + hub,
                   outline=color, width=t)


def _drop_trace(var, tid):
    """Stop listening to a variable that may already be gone.

    The drawn controls trace their own variable so a change from elsewhere
    (a settings load, the language rebuild) repaints them.  The trace outlives
    the widget unless it is removed, and then Tk tries to paint a canvas that
    no longer exists:  invalid command name "...!roundradio.!canvas".
    """
    if tid is None or var is None:
        return
    try:
        var.trace_remove("write", tid)
    except Exception:
        pass


class _Compat(object):
    """Answers the configure() calls the ttk widgets used to answer.

    app.py was written against ttk: it says cb.configure(state="readonly"),
    btn.configure(state="disabled"), chk.configure(command=...) and
    cb["values"] = [...].  These drawn widgets have no such options, so every
    one of them routes the handful of keys that mean something here through
    _compat_key() and hands the rest to Tk.
    """

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            merged = dict(cnf)
            merged.update(kw)
            kw = merged
            cnf = None
        if cnf is not None:
            return super().configure(cnf)
        rest = {}
        for key, value in kw.items():
            if not self._compat_key(key, value):
                rest[key] = value
        if rest:
            return super().configure(**rest)
        return None

    def cget(self, key):
        try:
            return self._compat_get(key)
        except KeyError:
            return super().cget(key)

    def _compat_key(self, key, value):
        return False

    def _compat_get(self, key):
        raise KeyError(key)


class Card(tk.Frame):
    """A rounded panel with an inner frame for the real widgets."""

    def __init__(self, master, pal, fill=None, border=True, radius=12,
                 pad=(0, 0), back=None):
        self._back = back or pal["bg"]
        tk.Frame.__init__(self, master, bg=self._back, bd=0,
                          highlightthickness=0)
        self.pal = pal
        self.fill = fill or pal["card"]
        self.border = pal["line"] if border is True else border
        self.radius = px(radius)
        self.pad = (px(pad[0]), px(pad[1]))
        self.cv = tk.Canvas(self, bg=self._back, highlightthickness=0, bd=0,
                            takefocus=0)
        self.cv.place(x=0, y=0, width=1, height=1)
        self.inner = tk.Frame(self, bg=self.fill, bd=0, highlightthickness=0)
        self._cache = {}
        self.bind("<Configure>", self._relayout)

    def repaint(self):
        self._relayout()

    def _relayout(self, event=None):
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        r = max(1, min(self.radius, w // 2, h // 2))
        self.cv.place_configure(width=w, height=h)
        self.cv.delete("all")
        fill, border = self.fill, self.border
        self.cv.create_rectangle(r, 0, w - r, h, fill=fill, outline="")
        self.cv.create_rectangle(0, r, w, h - r, fill=fill, outline="")
        if border:
            self.cv.create_line(r, 0, w - r, 0, fill=border)
            self.cv.create_line(r, h - 1, w - r, h - 1, fill=border)
            self.cv.create_line(0, r, 0, h - r, fill=border)
            self.cv.create_line(w - 1, r, w - 1, h - r, fill=border)
        for name, x, y in (("tl", 0, 0), ("tr", w - r, 0),
                           ("bl", 0, h - r), ("br", w - r, h - r)):
            key = (name, r, fill, border, self._back)
            img = self._cache.get(key)
            if img is None:
                img = corner_image(r, fill, self._back, border, name)
                self._cache[key] = img
            self.cv.create_image(x, y, image=img, anchor="nw")
        # The inner frame is a plain rectangle, so it has to stop short of the
        # corners or its square edges paint over the rounded ones -- 0.34r is
        # just past where the arc meets the straight side, which keeps the
        # corners visible without stealing noticeable space from the content.
        inset = max(1, int(r * 0.34))
        pw = self.pad[0] or inset
        ph = self.pad[1] or inset
        self.inner.place_configure(x=pw, y=ph,
                                   width=max(1, w - 2 * pw),
                                   height=max(1, h - 2 * ph))


class RoundButton(_Compat, tk.Canvas):
    """Flat button with rounded corners and a hover tint.

    kind is one of primary (solid), soft (field + outline), ghost (text only,
    optional outline) or icon (square, no label).
    """

    def __init__(self, master, pal, text="", command=None, kind="ghost",
                 height=34, width=None, font=None, pad_x=14, radius=8,
                 back=None, fg=None, fill=None, hover=None, border=True,
                 icon="", draw=None):
        self.pal = pal
        self.kind = kind
        self.radius = px(radius)
        self.command = command
        self.enabled = True
        self.text = text
        self.icon = icon
        self._draw = draw
        self.font = font or tkfont.Font(family="Microsoft YaHei UI", size=-px(12.5))
        self.pad_x = px(pad_x)
        self.h = px(height)
        self._back = back or pal["bg"]
        self._fg = fg
        self._fill = fill
        self._hover = hover
        self._border_on = border
        self._cache = {}
        self._sel = False
        tk.Canvas.__init__(self, master, bd=0, highlightthickness=0,
                           bg=self._back, takefocus=0,
                           cursor="hand2" if command else "arrow")
        self._bw = px(width) if width else self._auto_width()
        self.configure(width=self._bw, height=self.h)
        self._hot = False
        self.bind("<Button-1>", lambda e: self._set_hot(True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", lambda e: self._set_hot(True))
        self.bind("<Leave>", lambda e: self._set_hot(False))
        self.bind("<Configure>", lambda e: self._paint())
        self._paint()

    def _label(self):
        if self._draw is not None:
            return ""
        if self.icon and self.text:
            return self.icon + "  " + self.text
        return self.icon or self.text

    def _auto_width(self):
        return max(px(26), self.font.measure(self._label()) + 2 * self.pad_x)

    def _compat_key(self, key, value):
        if key == "text":
            self.set_text(value)
            return True
        return False

    def _compat_get(self, key):
        if key == "text":
            return self.text
        raise KeyError(key)

    def set_text(self, text):
        self.text = text
        self._bw = max(self._bw, self._auto_width())
        self.configure(width=self._bw)
        self._paint()

    def set_font(self, font):
        if font is None or font is self.font:
            return
        self.font = font
        self._bw = max(self._bw, self._auto_width())
        self.configure(width=self._bw)
        self._paint()

    def set_selected(self, sel):
        # Mark this one as the current choice (nav rows use it).
        sel = bool(sel)
        if self._sel == sel:
            return
        self._sel = sel
        self._paint()

    def set_enabled(self, on):
        if bool(on) == self.enabled:
            return
        self.enabled = bool(on)
        self.configure(cursor="hand2" if (on and self.command) else "arrow")
        self._paint()

    def _compat_key(self, key, value):
        if key == "state":
            self.set_enabled(str(value) not in ("disabled", "inactive"))
            return True
        if key == "text":
            self.set_text(value)
            return True
        if key == "command":
            self.command = value
            return True
        return False

    def _compat_get(self, key):
        if key == "state":
            return "normal" if self.enabled else "disabled"
        if key == "text":
            return self.text
        raise KeyError(key)

    def _colors(self):
        pal = self.pal
        if self.kind == "primary":
            fill = self._fill or pal["btn"]
            fg = self._fg or pal["btnfg"]
            hover = self._hover or pal["btn_hover"]
            border = None
        elif self.kind == "soft":
            fill = self._fill or pal["field"]
            fg = self._fg or pal["fg"]
            hover = self._hover or pal["hover"]
            border = pal["border"] if self._border_on else None
        else:
            fill = self._fill or self._back
            fg = self._fg or pal["dim"]
            hover = self._hover or pal["hover"]
            border = pal["border"] if self._border_on else None
        if self._sel and self.kind == "ghost":
            fill = pal["hover"]
            fg = pal["fg"]
        if not self.enabled:
            fg = pal["faint"]
        if self._hot and self.enabled:
            fill = hover
        return fill, fg, border

    def _set_hot(self, on):
        self._hot = on
        self._paint()

    def _paint(self):
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        r = max(1, min(self.radius, h // 2, w // 2))
        fill, fg, border = self._colors()
        self.delete("all")
        self.create_rectangle(r, 0, w - r, h, fill=fill, outline="")
        self.create_rectangle(0, r, w, h - r, fill=fill, outline="")
        if border:
            self.create_line(r, 0, w - r, 0, fill=border)
            self.create_line(r, h - 1, w - r, h - 1, fill=border)
            self.create_line(0, r, 0, h - r, fill=border)
            self.create_line(w - 1, r, w - 1, h - r, fill=border)
        for name, x, y in (("tl", 0, 0), ("tr", w - r, 0),
                           ("bl", 0, h - r), ("br", w - r, h - r)):
            key = (name, r, fill, border, self._back)
            img = self._cache.get(key)
            if img is None:
                img = corner_image(r, fill, self._back, border, name)
                self._cache[key] = img
            self.create_image(x, y, image=img, anchor="nw")
        if self._draw is not None:
            self._draw(self, w, h, fg)
            return
        label = self._label()
        if label:
            self.create_text(w // 2, h // 2, text=label, fill=fg,
                             font=self.font)

    def _release(self, event=None):
        self._set_hot(False)
        if not (self.enabled and self.command):
            return
        if event is not None:
            if not (0 <= event.x <= self.winfo_width()
                    and 0 <= event.y <= self.winfo_height()):
                return
        self.command()


class RoundCheck(_Compat, tk.Frame):
    """A 14px box plus label, drawn rather than themed."""

    def __init__(self, master, pal, text="", variable=None, command=None,
                 font=None, back=None):
        self.pal = pal
        self.var = variable if variable is not None else tk.BooleanVar(value=False)
        self.command = command
        self.font = font or tkfont.Font(family="Microsoft YaHei UI", size=-px(12))
        self._back = back or pal["bg"]
        self._cache = {}
        tk.Frame.__init__(self, master, bg=self._back, bd=0,
                          highlightthickness=0, cursor="hand2")
        self.box = tk.Canvas(self, width=px(14), height=px(14), bd=0,
                             highlightthickness=0, bg=self._back, takefocus=0,
                             cursor="hand2")
        self.box.pack(side="left")
        self.lbl = tk.Label(self, text=text, bg=self._back, fg=pal["dim"],
                            font=self.font, cursor="hand2", bd=0,
                            highlightthickness=0)
        self.lbl.pack(side="left", padx=(px(8), 0))
        for widget in (self, self.box, self.lbl):
            widget.bind("<Button-1>", self.toggle)
        self._trace = self.var.trace_add("write", lambda *a: self._paint())
        self._paint()

    def set_text(self, text):
        self.lbl.configure(text=text)

    def set_state(self, value):
        self._enabled = str(value) not in ("disabled", "inactive")
        cursor = "hand2" if self._enabled else "arrow"
        for widget in (self, self.box, self.lbl):
            widget.configure(cursor=cursor)
        self._paint()

    def _compat_key(self, key, value):
        if key == "command":
            self.command = value
            return True
        if key == "state":
            self.set_state(value)
            return True
        if key == "text":
            self.set_text(value)
            return True
        if key == "variable":
            _drop_trace(self.var, getattr(self, "_trace", None))
            self.var = value
            self._trace = self.var.trace_add("write", lambda *a: self._paint())
            self._paint()
            return True
        return False

    def _compat_get(self, key):
        if key == "state":
            return "normal" if getattr(self, "_enabled", True) else "disabled"
        if key == "text":
            return self.lbl.cget("text")
        if key == "variable":
            return self.var
        raise KeyError(key)

    def destroy(self):
        _drop_trace(getattr(self, "var", None), getattr(self, "_trace", None))
        self._trace = None
        super().destroy()

    def toggle(self, event=None):
        if not getattr(self, "_enabled", True):
            return
        self.var.set(not self.var.get())
        if self.command:
            self.command()
        self._paint()

    def _paint(self):
        pal = self.pal
        s = px(14)
        r = px(3)
        on = bool(self.var.get())
        # The tick inverts with the palette: black on light, white on dark,
        # with the tick itself in the window colour.  pal["chk"]/["chk_fg"]
        # was a fixed pair, so the dark theme drew a dark-grey tick on a
        # dark-grey box -- two nearly identical greys.
        ink = pal["fg"]
        fill = ink if on else pal["field"]
        border = ink if on else pal["border"]
        self.box.delete("all")
        self.box.create_rectangle(r, 0, s - r, s, fill=fill, outline="")
        self.box.create_rectangle(0, r, s, s - r, fill=fill, outline="")
        self.box.create_line(r, 0, s - r, 0, fill=border)
        self.box.create_line(r, s - 1, s - r, s - 1, fill=border)
        self.box.create_line(0, r, 0, s - r, fill=border)
        self.box.create_line(s - 1, r, s - 1, s - r, fill=border)
        for name, x, y in (("tl", 0, 0), ("tr", s - r, 0),
                           ("bl", 0, s - r), ("br", s - r, s - r)):
            key = (name, r, fill, border)
            img = self._cache.get(key)
            if img is None:
                img = corner_image(r, fill, self._back, border, name)
                self._cache[key] = img
            self.box.create_image(x, y, image=img, anchor="nw")
        if on:
            self.box.create_line(px(3.4), px(7.2), px(6), px(10),
                                 px(10.8), px(4.4), fill=pal["bg"],
                                 width=max(1, px(1.7)), capstyle="round",
                                 joinstyle="round")
        if not getattr(self, "_enabled", True):
            self.lbl.configure(fg=pal["faint"])
        else:
            self.lbl.configure(fg=pal["fg"] if on else pal["dim"])



class RoundRadio(_Compat, tk.Frame):
    """A 14px circle plus label, drawn rather than themed."""

    def __init__(self, master, pal, text="", value=None, variable=None,
                 command=None, font=None, back=None):
        self.pal = pal
        self.value = value
        self.var = variable if variable is not None else tk.StringVar(value="")
        self.command = command
        self.font = font or tkfont.Font(family="Microsoft YaHei UI", size=-px(12))
        self._back = back or pal["bg"]
        self._enabled = True
        tk.Frame.__init__(self, master, bg=self._back, bd=0,
                          highlightthickness=0, cursor="hand2")
        self.box = tk.Canvas(self, width=px(14), height=px(14), bd=0,
                             highlightthickness=0, bg=self._back, takefocus=0,
                             cursor="hand2")
        self.box.pack(side="left")
        self.lbl = tk.Label(self, text=text, bg=self._back, fg=pal["dim"],
                            font=self.font, cursor="hand2", bd=0,
                            highlightthickness=0)
        self.lbl.pack(side="left", padx=(px(8), 0))
        for widget in (self, self.box, self.lbl):
            widget.bind("<Button-1>", self.toggle)
        self._trace = self.var.trace_add("write", lambda *a: self._paint())
        self._paint()

    def set_text(self, text):
        self.lbl.configure(text=text)

    def set_state(self, value):
        self._enabled = str(value) not in ("disabled", "inactive")
        cursor = "hand2" if self._enabled else "arrow"
        for widget in (self, self.box, self.lbl):
            widget.configure(cursor=cursor)
        self._paint()

    def _compat_key(self, key, value):
        if key == "command":
            self.command = value
            return True
        if key == "state":
            self.set_state(value)
            return True
        if key == "text":
            self.set_text(value)
            return True
        if key == "variable":
            _drop_trace(self.var, getattr(self, "_trace", None))
            self.var = value
            self._trace = self.var.trace_add("write", lambda *a: self._paint())
            self._paint()
            return True
        return False

    def _compat_get(self, key):
        if key == "state":
            return "normal" if self._enabled else "disabled"
        if key == "text":
            return self.lbl.cget("text")
        if key == "variable":
            return self.var
        raise KeyError(key)

    def destroy(self):
        _drop_trace(getattr(self, "var", None), getattr(self, "_trace", None))
        self._trace = None
        super().destroy()

    def toggle(self, event=None):
        if not self._enabled:
            return
        self.var.set(self.value)
        if self.command:
            self.command()
        self._paint()

    def _paint(self):
        pal = self.pal
        s = px(14)
        on = self.var.get() == self.value
        # Same inversion as the check box, and for the same reason: the
        # selected radio is a solid dot in the text colour, so on the light
        # palette it is a black dot and on the dark one a white dot.  The old
        # pair drew a dark ring with a light dot on both, which looked like an
        # empty circle on light and was all but invisible on dark.
        ink = pal["fg"]
        ring = ink if on else pal["border"]
        self.box.delete("all")
        self.box.create_oval(0, 0, s - 1, s - 1, outline=ring,
                             width=max(1, px(1)))
        if on:
            self.box.create_oval(px(3.5), px(3.5),
                                 px(10.5) - 1, px(10.5) - 1,
                                 outline="", fill=ink)


class DropList(tk.Toplevel):
    """The list a RoundSelect opens.

    tk.Menu brings its own native frame and sizes itself to the widest entry,
    which on a 90 px field is a postage stamp with a grey border around it.
    This is a borderless card instead: exactly as wide as the field it drops
    from, rounded, with one hovered row in the palette's hover colour.
    """

    def __init__(self, master, pal, values, current, width, on_pick):
        tk.Toplevel.__init__(self, master)
        self.pal = pal
        self.values = list(values)
        self.on_pick = on_pick
        self._back = pal["bg"]
        self._fill = pal["field"]
        self._border = pal["border"]
        self._r = px(8)
        self.row_h = px(30)
        self.w = max(px(110), int(width))
        self.h = max(1, len(self.values)) * self.row_h + px(8)
        self._hot = self._index_of(current)
        self._cache = {}
        try:
            self.overrideredirect(True)
        except Exception:
            pass
        self.configure(bg=self._back, bd=0, highlightthickness=0)
        self.font = tkfont.Font(family="Microsoft YaHei UI", size=-px(12.5))
        self.cv = tk.Canvas(self, bd=0, highlightthickness=0, bg=self._back,
                            width=self.w, height=self.h, takefocus=0,
                            cursor="hand2")
        self.cv.pack()
        self._paint()
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<Leave>", lambda _e: self._hover(None))
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<Button-1>", self._outside)

    def _index_of(self, value):
        try:
            return self.values.index(value)
        except ValueError:
            return None

    def _row_at(self, y):
        i = int((y - px(4)) // self.row_h)
        return i if 0 <= i < len(self.values) else None

    def _motion(self, event):
        self._hover(self._row_at(event.y))

    def _hover(self, i):
        if i == self._hot:
            return
        self._hot = i
        self._paint()

    def _click(self, event):
        i = self._row_at(event.y)
        if i is None:
            self.close()
            return
        value = self.values[i]
        self.close()
        self.on_pick(value)

    def _outside(self, event):
        if not (0 <= event.x <= self.w and 0 <= event.y <= self.h):
            self.close()

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def _paint(self):
        pal = self.pal
        w, h, r = self.w, self.h, self._r
        cv = self.cv
        cv.delete("all")
        cv.create_rectangle(r, 0, w - r, h, fill=self._fill, outline="")
        cv.create_rectangle(0, r, w, h - r, fill=self._fill, outline="")
        cv.create_line(r, 0, w - r, 0, fill=self._border)
        cv.create_line(r, h - 1, w - r, h - 1, fill=self._border)
        cv.create_line(0, r, 0, h - r, fill=self._border)
        cv.create_line(w - 1, r, w - 1, h - r, fill=self._border)
        for name, x, y in (("tl", 0, 0), ("tr", w - r, 0),
                           ("bl", 0, h - r), ("br", w - r, h - r)):
            key = (name, r, self._fill, self._border)
            img = self._cache.get(key)
            if img is None:
                img = corner_image(r, self._fill, self._back,
                                   self._border, name)
                self._cache[key] = img
            cv.create_image(x, y, image=img, anchor="nw")
        for i, value in enumerate(self.values):
            y0 = px(4) + i * self.row_h
            if i == self._hot:
                cv.create_rectangle(px(8), y0 + 1, w - px(8),
                                    y0 + self.row_h - 1,
                                    fill=pal["hover"], outline="")
            text = value
            while text and self.font.measure(text) > w - px(26):
                text = text[:-1]
            cv.create_text(px(13), y0 + self.row_h // 2, text=text,
                           anchor="w", font=self.font,
                           fill=pal["fg"] if i == self._hot else pal["dim"])


class RoundSelect(_Compat, tk.Canvas):
    """A 34px combo box with a triangle caret; the list is a tk.Menu."""

    def __init__(self, master, pal, values, variable, command=None,
                 height=34, width=120, font=None, back=None, radius=8,
                 auto_width=False, min_width=90, editable=False):
        self.pal = pal
        self.editable = editable
        self._state = "readonly"
        self.values = list(values)
        self.auto_width = auto_width
        self.min_width = min_width
        self.var = variable
        self.command = command
        self.font = font or tkfont.Font(family="Microsoft YaHei UI", size=-px(12.5))
        self.radius = px(radius)
        self.h = px(height)
        self._back = back or pal["bg"]
        self._cache = {}
        tk.Canvas.__init__(self, master, bd=0, highlightthickness=0,
                           bg=self._back, takefocus=0, cursor="hand2",
                           width=px(width), height=self.h)
        self._hot = False
        self.bind("<Button-1>", self._open)
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<Configure>", lambda e: self._paint())
        self.var.trace_add("write", lambda *a: self._paint())
        self.entry = None
        if editable:
            # A free-text combo: the list is still a menu, but the value may be
            # typed (the bitrate cap accepts anything, the list is a shortcut).
            self.entry = tk.Entry(self, textvariable=self.var, bd=0,
                                  relief="flat", highlightthickness=0,
                                  bg=pal["field"], fg=pal["fg"],
                                  insertbackground=pal["fg"], font=self.font,
                                  selectbackground=pal["sel"],
                                  selectforeground=pal["sel_fg"])
        self._fit()
        self._paint()

    def _fit(self):
        # Widen to the longest entry so nothing is ever cut off mid-word.
        if not getattr(self, "auto_width", False):
            return
        widest = max([self.font.measure(str(v)) for v in self.values] + [0])
        self.configure(width=max(px(self.min_width), widest + px(40)))

    def destroy(self):
        _drop_trace(getattr(self, "var", None), getattr(self, "_trace", None))
        self._trace = None
        super().destroy()

    def set(self, value):
        self.var.set(value)

    def get(self):
        return self.var.get()

    def set_values(self, values):
        self.values = list(values)
        self._fit()
        self._paint()

    def set_state(self, value):
        self._state = "disabled" if str(value) in ("disabled", "inactive") else "readonly"
        self.configure(cursor="arrow" if self._state == "disabled" else "hand2")
        self._paint()

    def _compat_key(self, key, value):
        if key == "values":
            self.set_values(value)
            return True
        if key == "state":
            self.set_state(value)
            return True
        if key == "textvariable":
            _drop_trace(self.var, getattr(self, "_trace", None))
            self.var = value
            self._trace = self.var.trace_add("write", lambda *a: self._paint())
            self._paint()
            return True
        if key == "command":
            self.command = value
            return True
        return False

    def _compat_get(self, key):
        if key == "values":
            return list(self.values)
        if key == "state":
            return self._state
        if key == "textvariable":
            return self.var
        raise KeyError(key)

    def __setitem__(self, key, value):
        if self._compat_key(key, value):
            return
        super().__setitem__(key, value)

    def _hover(self, on):
        self._hot = on
        self._paint()

    def _paint(self):
        pal = self.pal
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        r = max(1, min(self.radius, h // 2, w // 2))
        fill = pal["hover"] if self._hot else pal["field"]
        border = pal["border"]
        self.delete("all")
        self.create_rectangle(r, 0, w - r, h, fill=fill, outline="")
        self.create_rectangle(0, r, w, h - r, fill=fill, outline="")
        self.create_line(r, 0, w - r, 0, fill=border)
        self.create_line(r, h - 1, w - r, h - 1, fill=border)
        self.create_line(0, r, 0, h - r, fill=border)
        self.create_line(w - 1, r, w - 1, h - r, fill=border)
        for name, x, y in (("tl", 0, 0), ("tr", w - r, 0),
                           ("bl", 0, h - r), ("br", w - r, h - r)):
            key = (name, r, fill, border)
            img = self._cache.get(key)
            if img is None:
                img = corner_image(r, fill, self._back, border, name)
                self._cache[key] = img
            self.create_image(x, y, image=img, anchor="nw")
        if self.entry is not None:
            eh = self.font.metrics("linespace") + px(4)
            self.entry.place(x=px(10), y=max(1, (h - eh) // 2),
                             width=max(px(18), w - px(36)), height=eh)
            self.entry.configure(
                state="normal" if self._state != "disabled" else "disabled")
        else:
            text = self.var.get()
            avail = w - px(34)
            while text and self.font.measure(text) > avail:
                text = text[:-1]
            self.create_text(px(10), h // 2, text=text, anchor="w",
                             fill=pal["faint"] if self._state == "disabled" else pal["fg"],
                             font=self.font)
        cx, cy = w - px(14), h // 2
        self.create_polygon(cx - px(4), cy - px(2.5), cx + px(4), cy - px(2.5),
                            cx, cy + px(3), fill=pal["faint"], outline="")

    def _open(self, event=None):
        if self._state == "disabled":
            return
        drop = DropList(self.winfo_toplevel(), self.pal, self.values,
                        self.var.get(),
                        max(self.winfo_width(), self.winfo_reqwidth()),
                        self._pick)
        x, y = self.winfo_rootx(), self.winfo_rooty() + self.h
        if y + drop.h > self.winfo_screenheight() - px(24):
            y = max(px(24), self.winfo_rooty() - drop.h)
        drop.geometry("+%d+%d" % (x, y))
        drop.lift()
        try:
            drop.grab_set()
            drop.focus_force()
        except Exception:
            pass

    def _pick(self, value):
        self.var.set(value)
        if self.command:
            self.command()
        try:
            self.event_generate("<<ComboboxSelected>>")
        except Exception:
            pass


class Pill(_Compat, tk.Canvas):
    """Small rounded label, e.g. the version badge."""

    def __init__(self, master, pal, text, back=None, fg=None, border=None,
                 font=None, pad_x=7, height=17):
        self.pal = pal
        self.text = text
        self.font = font or tkfont.Font(family="Microsoft YaHei UI", size=-px(10.5))
        self._back = back or pal["bg"]
        self._fg = fg or pal["faint"]
        self._border = border or pal["border"]
        self.pad_x = px(pad_x)
        self.h = px(height)
        w = self.font.measure(text) + 2 * self.pad_x
        tk.Canvas.__init__(self, master, bd=0, highlightthickness=0,
                           bg=self._back, width=w, height=self.h, takefocus=0)
        self.bind("<Configure>", lambda e: self._paint())

    def set_text(self, text):
        self.text = text
        self.configure(width=self.font.measure(text) + 2 * self.pad_x)
        self._paint()

    def _paint(self):
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        r = max(1, h // 2)
        self.delete("all")
        self.create_rectangle(r, 0, w - r, h, fill=self._back, outline="")
        self.create_rectangle(0, r, w, h - r, fill=self._back, outline="")
        for name, x, y in (("tl", 0, 0), ("tr", w - r, 0),
                           ("bl", 0, h - r), ("br", w - r, h - r)):
            img = corner_image(r, self._back, self._back, self._border, name)
            self.create_image(x, y, image=img, anchor="nw")
            self._keep = getattr(self, "_keep", {})
            self._keep[(name, x, y)] = img
        self.create_line(r, 0, w - r, 0, fill=self._border)
        self.create_line(r, h - 1, w - r, h - 1, fill=self._border)
        self.create_line(0, r, 0, h - r, fill=self._border)
        self.create_line(w - 1, r, w - 1, h - r, fill=self._border)
        self.create_text(w // 2, h // 2, text=self.text, fill=self._fg,
                         font=self.font)


class RoundEntry(tk.Canvas):
    """A rounded text field: the canvas draws the box, a bare Entry sits inside.

    ttk.Entry cannot be rounded on any Windows theme, and the settings page is
    otherwise all rounded controls -- one square field in the middle of it reads
    as a bug.  The Entry is a plain tk one with every border turned off, placed
    inside the drawn box, so typing, selection, undo and the textvariable all
    behave exactly as before.
    """

    def __init__(self, master, pal, textvariable=None, chars=8, height=32,
                 back=None, radius=8, font=None, justify="left", fill=None,
                 fg=None, show=""):
        self.pal = pal
        self._back = back or pal["bg"]
        self.radius = px(radius)
        self.h = px(height)
        self.font = font or tkfont.Font(family="Microsoft YaHei UI",
                                        size=-px(12.5))
        self._fill = fill or pal["field"]
        self._fg = fg or pal["fg"]
        self._focus = False
        tk.Canvas.__init__(self, master, bd=0, highlightthickness=0,
                           bg=self._back, takefocus=0,
                           width=self._width_for(chars), height=self.h)
        self.entry = tk.Entry(self, textvariable=textvariable, bd=0,
                              relief="flat", highlightthickness=0,
                              bg=self._fill, fg=self._fg,
                              insertbackground=self._fg, font=self.font,
                              justify=justify, show=show,
                              selectbackground=pal["sel"],
                              selectforeground=pal["sel_fg"])
        self.bind("<Configure>", lambda e: self._paint())
        self.entry.bind("<FocusIn>", lambda e: self._paint(True))
        self.entry.bind("<FocusOut>", lambda e: self._paint(False))
        self._paint()

    def _width_for(self, chars):
        return max(px(46), self.font.measure("0") * int(chars) + px(22))

    # -- pass-throughs so callers can keep treating it like a widget ---------
    def get(self):
        return self.entry.get()

    def set(self, text):
        self.entry.delete(0, "end")
        self.entry.insert(0, str(text))

    def bind_entry(self, seq, func):
        self.entry.bind(seq, func)

    def focus_set(self):
        self.entry.focus_set()

    def _paint(self, focus=None):
        if focus is not None:
            self._focus = focus
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        r = max(1, min(self.radius, h // 2, w // 2))
        border = self._fg if self._focus else self.pal["border"]
        fill = self._fill
        self.delete("all")
        self.create_rectangle(r, 0, w - r, h, fill=fill, outline="")
        self.create_rectangle(0, r, w, h - r, fill=fill, outline="")
        self.create_line(r, 0, w - r, 0, fill=border)
        self.create_line(r, h - 1, w - r, h - 1, fill=border)
        self.create_line(0, r, 0, h - r, fill=border)
        self.create_line(w - 1, r, w - 1, h - r, fill=border)
        for name, x, y in (("tl", 0, 0), ("tr", w - r, 0),
                           ("bl", 0, h - r), ("br", w - r, h - r)):
            key = (name, r, fill, border, self._back)
            cache = getattr(self, "_cache", None)
            if cache is None:
                cache = self._cache = {}
            img = cache.get(key)
            if img is None:
                img = corner_image(r, fill, self._back, border, name)
                cache[key] = img
            self.create_image(x, y, image=img, anchor="nw")
        pad = px(9)
        eh = self.font.metrics("linespace") + px(4)
        self.entry.place(x=pad, y=max(1, (h - eh) // 2),
                         width=max(px(18), w - 2 * pad), height=eh)

class MenuCard(tk.Toplevel):
    """The ≡ popup, and every submenu inside it, drawn instead of native.

    tk.Menu on Windows always brings its own frame, its own font and its own
    white text; next to a dark rounded window it looks like a window from some
    other program (a white hairline around a dark menu is the giveaway).  This
    is a borderless card with the same radius, palette and type as the rest of
    the UI, and it nests itself: a row with an arrow opens a second card to the
    side, so nothing has to live in a system menu any more.

    items are tuples:
        ("title", text)
        ("item", text, command)
        ("sub", text, [items])
        ("check", text, boolean_var, command)
        ("radio", text, variable, value, command)
    """

    def __init__(self, master, pal, items, x, y, width=None, on_close=None):
        tk.Toplevel.__init__(self, master)
        self.pal = pal
        self.items = list(items)
        self.on_close = on_close
        self._child = None
        self._back = pal["bg"]
        self._fill = pal["field"]
        self._border = pal["border"]
        self._r = px(12)
        self.row_h = px(30)
        self.title_h = px(26)
        self.pad_y = px(7)
        self.font = tkfont.Font(family="Microsoft YaHei UI", size=-px(12.5))
        self.tfont = tkfont.Font(family="Microsoft YaHei UI", size=-px(11),
                                 weight="bold")
        self.afont = tkfont.Font(family="Microsoft YaHei UI", size=-px(10))
        self._hot = None
        self._rows = []
        self._cache = {}
        try:
            self.overrideredirect(True)
        except Exception:
            pass
        self.configure(bg=self._back, bd=0, highlightthickness=0)
        self._measure(width)
        self._layout()
        self.cv = tk.Canvas(self, bd=0, highlightthickness=0, bg=self._back,
                            width=self.w, height=self.h, takefocus=0,
                            cursor="hand2")
        self.cv.pack()
        self._place(x, y)
        self._paint()
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<Leave>", lambda _e: self._hover(None))
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<Button-1>", self._outside)
        try:
            # Above every window, not just above its parent: a popup that the
            # window it belongs to can cover is not a popup.
            self.attributes("-topmost", True)
            self.lift()
            self.grab_set()
            # A borderless Toplevel does not get the keyboard focus by itself,
            # so Escape would go to the window behind it and do nothing.  The
            # focus comes back on its own when the card is destroyed.
            self.focus_force()
        except Exception:
            pass

    # -- geometry ---------------------------------------------------------
    def _measure(self, width):
        need = 0
        for it in self.items:
            kind = it[0]
            if kind == "title":
                need = max(need, self.tfont.measure(it[1]) + px(30))
            else:
                need = max(need, self.font.measure(it[1]) + px(56))
        self.w = int(width or max(px(210), min(px(310), need + px(18))))

    def _layout(self):
        """Rows as (top, height, item); a title gets its own smaller height."""
        self._rows = []
        y = self.pad_y
        for it in self.items:
            h = self.title_h if it[0] == "title" else self.row_h
            self._rows.append((y, h, it))
            y += h
        self.h = y + self.pad_y

    def _place(self, x, y):
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, min(int(x), sw - self.w - px(4)))
        if y + self.h > sh - px(8):
            y = max(px(4), sh - self.h - px(8))
        self._x = int(x)
        self._y = int(y)
        try:
            # Size and position in one call: a borderless Toplevel that is
            # only moved keeps whatever size Tk felt like (200x200), which
            # clips the card instead of growing it to fit the rows.
            self.geometry("%dx%d+%d+%d" % (self.w, self.h, self._x, self._y))
        except Exception:
            pass

    # -- painting ---------------------------------------------------------
    def _row_at(self, y):
        for top, h, it in self._rows:
            if top <= y < top + h:
                return top, it
        return None, None

    def _motion(self, event):
        top, it = self._row_at(event.y)
        if it is not None and it[0] == "title":
            top = None
        self._hover(top)

    def _hover(self, top):
        if top == self._hot:
            return
        self._hot = top
        self._paint()

    def _paint(self):
        pal = self.pal
        w, h, r = self.w, self.h, self._r
        cv = self.cv
        cv.delete("all")
        cv.create_rectangle(r, 0, w - r, h, fill=self._fill, outline="")
        cv.create_rectangle(0, r, w, h - r, fill=self._fill, outline="")
        cv.create_line(r, 0, w - r, 0, fill=self._border)
        cv.create_line(r, h - 1, w - r, h - 1, fill=self._border)
        cv.create_line(0, r, 0, h - r, fill=self._border)
        cv.create_line(w - 1, r, w - 1, h - r, fill=self._border)
        for name, x, y in (("tl", 0, 0), ("tr", w - r, 0),
                           ("bl", 0, h - r), ("br", w - r, h - r)):
            key = (name, r, self._fill, self._border)
            img = self._cache.get(key)
            if img is None:
                img = corner_image(r, self._fill, self._back,
                                   self._border, name)
                self._cache[key] = img
            cv.create_image(x, y, image=img, anchor="nw")
        for top, rh, it in self._rows:
            kind = it[0]
            if kind == "title":
                cv.create_text(px(15), top + rh // 2 + px(2), anchor="w",
                               text=it[1], font=self.tfont,
                               fill=pal["faint"])
                continue
            if top == self._hot:
                cv.create_rectangle(px(6), top + 1, w - px(6), top + rh - 1,
                                    fill=pal["hover"], outline="")
            cy = top + rh // 2
            x = px(16)
            if kind == "check":
                self._draw_check(cv, x, cy, bool(it[2].get()), pal)
                x += px(24)
            elif kind == "radio":
                on = (str(it[2].get()) == str(it[3]))
                self._draw_radio(cv, x, cy, on, pal)
                x += px(24)
            cv.create_text(x, cy, anchor="w", text=it[1], font=self.font,
                           fill=pal["fg"])
            if kind == "sub":
                cv.create_text(w - px(14), cy, anchor="e", text="\u203a",
                               font=self.font, fill=pal["faint"])

    @staticmethod
    def _draw_check(cv, x, cy, on, pal):
        s = px(14)
        y0 = cy - s // 2
        if on:
            cv.create_rectangle(x, y0, x + s, y0 + s, fill=pal["fg"],
                                outline=pal["fg"])
            cv.create_line(x + px(3.4), y0 + px(7.2), x + px(6), y0 + px(10),
                           x + px(10.6), y0 + px(4.4), fill=pal["field"],
                           width=max(1, int(px(1.7))), capstyle="round",
                           joinstyle="round")
        else:
            cv.create_rectangle(x, y0, x + s, y0 + s, fill="",
                                outline=pal["border"])

    @staticmethod
    def _draw_radio(cv, x, cy, on, pal):
        s = px(14)
        y0 = cy - s // 2
        cv.create_oval(x, y0, x + s, y0 + s, outline=pal["border"], fill="")
        if on:
            k = px(3.6)
            cv.create_oval(x + k, y0 + k, x + s - k, y0 + s - k,
                           outline="", fill=pal["fg"])

    # -- interaction ------------------------------------------------------
    def _click(self, event):
        self._handle(event.y)

    def _handle(self, y):
        """Run whatever the row at y does (also used by a submenu forwarding)."""
        top, it = self._row_at(y)
        if it is None:
            self.close()
            return
        kind = it[0]
        if kind == "title":
            return
        if kind == "sub":
            self._open_child(top, it[2])
            return
        if kind == "item":
            self.close_all()
            try:
                it[2]()
            except Exception:
                pass
            return
        if kind == "check":
            var = it[2]
            try:
                var.set(not bool(var.get()))
            except Exception:
                pass
        elif kind == "radio":
            try:
                it[2].set(it[3])
            except Exception:
                pass
        cmd = it[-1]
        if callable(cmd):
            try:
                cmd()
            except Exception:
                pass
        self._paint()
        if kind == "radio":
            self.close_all()

    def _outside(self, event):
        if 0 <= event.x <= self.w and 0 <= event.y <= self.h:
            return                      # a click on the card is not an outside
        parent = getattr(self, "_parent", None)
        self.close()
        # A submenu holds the grab, so while it is open every click in the world
        # is delivered to it -- including clicks on the card it belongs to.
        # Forward those, or a second click would be needed to pick a row.
        if parent is not None:
            try:
                parent._hit(event.x_root - parent._x, event.y_root - parent._y)
            except Exception:
                pass

    def _hit(self, x, y):
        """A click that landed on this card while one of its submenus grabbed."""
        if not (0 <= x <= self.w and 0 <= y <= self.h):
            self.close()
            return
        self._handle(y)

    def _open_child(self, top, items):
        if self._child is not None:
            try:
                self._child.close_all()
            except Exception:
                pass
            self._child = None
        y = self._y + top - px(7)
        child = MenuCard(self.master, self.pal, items,
                         self._x + self.w - px(4), y)
        if child._x + child.w > self.winfo_screenwidth() - px(8):
            child._place(self._x - child.w + px(4), y)
        child._parent = self
        self._child = child

    def close(self):
        self.close_all()
        if callable(self.on_close):
            try:
                self.on_close()
            except Exception:
                pass

    def close_all(self):
        if self._child is not None:
            try:
                self._child.close_all()
            except Exception:
                pass
            self._child = None
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass



class MenuRow(tk.Canvas):
    """One line of a menu page -- drawn, because a native row cannot be themed.

    kind is "item" (runs command), "sub" (opens the next page), "check"
    (toggles a variable in place) or "radio" (picks one value of a variable).
    The row is a canvas so the hover wash can have the same 8px radius as the
    cards it sits on; a ttk label would give a square grey band.
    """

    def __init__(self, master, pal, text, kind="item", command=None,
                 variable=None, value=None, on_open=None, height=38,
                 back=None, font=None, indent=0):
        tk.Canvas.__init__(self, master, bd=0, highlightthickness=0,
                           bg=back or pal["bg"], height=px(height),
                           takefocus=0, cursor="hand2")
        self.pal = pal
        self.kind = kind
        self.text = text
        self.command = command
        self.var = variable
        self.value = value
        self.on_open = on_open
        self._bgc = back or pal["bg"]
        self._rh = px(height)
        self._r = px(8)
        self._ix = px(indent)
        self.font = font or tkfont.Font(family="Microsoft YaHei UI",
                                        size=-px(12.5))
        self._hot = False
        self._cache = {}
        self.bind("<Configure>", lambda _e: self._paint())
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self.bind("<Button-1>", self._click)
        if self.var is not None:
            try:
                self.var.trace_add("write", lambda *_a: self._paint())
            except Exception:
                pass

    def _paint(self):
        pal = self.pal
        cv = self
        cv.delete("all")
        w = self.winfo_width()
        if w < px(20):
            w = self.winfo_reqwidth()
        h, r = self._rh, self._r
        if self._hot:
            cv.create_rectangle(r, 0, w - r, h, fill=pal["hover"], outline="")
            cv.create_rectangle(0, r, w, h - r, fill=pal["hover"], outline="")
            for name, x, y in (("tl", 0, 0), ("tr", w - r, 0),
                               ("bl", 0, h - r), ("br", w - r, h - r)):
                key = (name, r, pal["hover"], self._bgc)
                img = self._cache.get(key)
                if img is None:
                    img = corner_image(r, pal["hover"], self._bgc, None, name)
                    self._cache[key] = img
                cv.create_image(x, y, image=img, anchor="nw")
        cy = h // 2
        x = self._ix + px(14)
        if self.kind in ("check", "radio"):
            on = bool(self.var.get()) if self.var is not None else False
            if self.kind == "check":
                MenuCard._draw_check(cv, x, cy, on, pal)
            else:
                MenuCard._draw_radio(cv, x, cy, on, pal)
            x += px(24)
        cv.create_text(x, cy, anchor="w", text=self.text, font=self.font,
                       fill=pal["fg"])
        if self.kind == "sub":
            cv.create_text(w - px(14), cy, anchor="e", text="\u203a",
                           font=self.font, fill=pal["faint"])

    def _hover(self, on):
        if bool(on) == self._hot:
            return
        self._hot = bool(on)
        self._paint()

    def _click(self, _event=None):
        kind = self.kind
        if kind == "check" and self.var is not None:
            try:
                self.var.set(not bool(self.var.get()))
            except Exception:
                pass
            if callable(self.command):
                self.command()
            self._paint()
            return
        if kind == "radio" and self.var is not None:
            try:
                self.var.set(self.value)
            except Exception:
                pass
            if callable(self.command):
                self.command()
            self._paint()
            return
        if kind == "sub":
            if callable(self.on_open):
                self.on_open()
            return
        if callable(self.command):
            self.command()
