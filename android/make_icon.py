"""Draw the Android launcher icon.

There is no designer and no image asset in this project, so the icon is painted
here, pixel by pixel, with Pillow: a rounded blue tile with two screens on it --
a wide one (the PC) and a narrow one (the phone) -- which is literally what the
app does.

Everything is drawn at 4x and downsampled, which is the cheapest way to get
smooth edges without shipping a font or borrowing someone else's artwork.

Run:  <python with Pillow> android/make_icon.py
Writes android/res/mipmap-*/ic_launcher{,_round,_foreground}.png
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "res")

# Legacy launcher icon densities (48dp baseline).
ICON_SIZES = {
    "mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192,
}
# Adaptive icons are 108dp, of which only the middle 72dp is guaranteed visible.
ADAPTIVE_SIZES = {
    "mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432,
}
SS = 4                                    # supersampling factor

BG_TOP = (46, 104, 236)
BG_BOTTOM = (18, 52, 150)
WHITE = (255, 255, 255, 255)


def _rounded(d, box, radius, fill):
    d.rounded_rectangle(box, radius=radius, fill=fill)


def draw_glyph(img, size, inset):
    """Paint the two screens onto img (RGBA, `size` px) inside an `inset` box.

    inset is the fraction of the canvas kept as padding on each side, so the
    same routine serves the legacy icon (small padding) and the adaptive
    foreground (big padding, because launchers crop the outer third).
    """
    d = ImageDraw.Draw(img)
    x0 = size * inset
    span = size * (1.0 - 2 * inset)

    def px(fx, fy):
        return (x0 + span * fx, x0 + span * fy)

    # -- the PC screen: a wide rounded rectangle with a title bar and a stand --
    l, t = px(0.00, 0.16)
    r, b = px(0.62, 0.68)
    _rounded(d, (l, t, r, b), span * 0.055, WHITE)
    # an inner dark panel so it reads as a screen and not as a white slab
    il, it = px(0.06, 0.25)
    ir, ib = px(0.56, 0.60)
    _rounded(d, (il, it, ir, ib), span * 0.03, (16, 40, 110, 235))
    # title bar
    bl, bt = px(0.06, 0.25)
    br, bb = px(0.56, 0.33)
    _rounded(d, (bl, bt, br, bb), span * 0.03, (90, 150, 255, 255))
    # stand
    sl, st = px(0.24, 0.68)
    sr, sb = px(0.38, 0.78)
    d.rectangle((sl, st, sr, sb), fill=WHITE)
    fl, ft = px(0.10, 0.78)
    fr, fb = px(0.52, 0.88)
    _rounded(d, (fl, ft, fr, fb), span * 0.02, WHITE)

    # -- the phone: narrow rounded rectangle, right hand side -----------------
    pl, pt = px(0.70, 0.06)
    pr, pb = px(1.00, 0.86)
    _rounded(d, (pl, pt, pr, pb), span * 0.075, WHITE)
    gl, gt = px(0.75, 0.15)
    gr, gb = px(0.95, 0.78)
    _rounded(d, (gl, gt, gr, gb), span * 0.04, (24, 62, 150, 255))
    # earpiece slit
    el, et = px(0.80, 0.10)
    er, eb = px(0.90, 0.125)
    _rounded(d, (el, et, er, eb), span * 0.012, (24, 62, 150, 255))


def tile(size, circular=False):
    """A background tile: vertical gradient, rounded (or circular) corners."""
    big = size * SS
    grad = Image.new("RGB", (1, big))
    for y in range(big):
        f = y / float(big - 1)
        grad.putpixel((0, y), tuple(
            int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * f) for i in range(3)))
    bg = grad.resize((big, big), Image.BILINEAR).convert("RGBA")

    mask = Image.new("L", (big, big), 0)
    md = ImageDraw.Draw(mask)
    if circular:
        md.ellipse((0, 0, big - 1, big - 1), fill=255)
    else:
        md.rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * 0.22), fill=255)
    bg.putalpha(mask)
    return bg.resize((size, size), Image.LANCZOS)


def main():
    written = 0
    for dens, size in ICON_SIZES.items():
        folder = os.path.join(RES, "mipmap-" + dens)
        os.makedirs(folder, exist_ok=True)
        for name, circular in (("ic_launcher.png", False),
                               ("ic_launcher_round.png", True)):
            big = size * SS
            img = tile(size, circular).resize((big, big), Image.LANCZOS)
            draw_glyph(img, big, 0.14)
            img.resize((size, size), Image.LANCZOS).save(os.path.join(folder, name))
            written += 1

    # Adaptive foreground: transparent, glyph shrunk into the safe zone.
    for dens, size in ADAPTIVE_SIZES.items():
        folder = os.path.join(RES, "mipmap-" + dens)
        os.makedirs(folder, exist_ok=True)
        big = size * SS
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw_glyph(img, big, 0.26)
        img.resize((size, size), Image.LANCZOS).save(
            os.path.join(folder, "ic_launcher_foreground.png"))
        written += 1

    # keep the app icon in the repo root in step with the Android one
    root_ico = os.path.join(os.path.dirname(HERE), "DroidExtend.ico")
    try:
        big = tile(256).resize((256 * 2, 256 * 2), Image.LANCZOS)
        draw_glyph(big, 512, 0.14)
        big.resize((256, 256), Image.LANCZOS).save(
            root_ico, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
        written += 1
    except Exception as e:
        print("windows .ico skipped:", e)

    print("wrote %d files under %s" % (written, RES))


if __name__ == "__main__":
    main()
