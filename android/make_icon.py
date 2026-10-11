"""Draw the launcher icon.

There is no designer and no image asset in this project, so the icon is painted
here, pixel by pixel, with Pillow: a rounded tile with two screens on it -- a
wide one with a stand (the PC) and a narrow one (the phone) -- which is
literally what the app does.

Everything is drawn at 4x on its own layer, downsampled, and given a soft drop
shadow, which is the cheapest way to get smooth edges and depth without
shipping a font or borrowing someone else's artwork.

Run:  <python with Pillow> android/make_icon.py
Writes android/res/mipmap-*/ic_launcher{,_round,_foreground}.png and the
Windows DroidExtend.ico next to the project root.
"""
import os

from PIL import Image, ImageDraw, ImageFilter

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

BG_TOP = (84, 132, 255)
BG_BOTTOM = (24, 46, 170)
SCREEN_HI = (126, 188, 255)
SCREEN_LO = (40, 88, 212)
FRAME = (16, 38, 96, 255)
WHITE = (255, 255, 255, 255)


def _grow(b, by):
    return (b[0] - by, b[1] - by, b[2] + by, b[3] + by)


def _screen(d, layer, px, box, span, l, t, r, b):
    """A display area: the dark frame plus a light gradient masked to it."""
    x0, y0 = px(l, t)
    x1, y1 = px(r, b)
    w = max(2, int(round(x1 - x0)))
    h = max(2, int(round(y1 - y0)))
    radius = max(1, int(span * 0.045))
    d.rounded_rectangle(box(l, t, r, b), radius=radius, fill=FRAME)
    grad = _grad(max(w, h), SCREEN_HI, SCREEN_LO).resize((w, h), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    grad.putalpha(mask)
    layer.alpha_composite(grad, (int(x0), int(y0)))


def _grad(size, top, bottom, diagonal=True):
    """A gradient image, top-left -> bottom-right when diagonal."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            if diagonal:
                f = (x + y) / float(2 * (size - 1)) if size > 1 else 0.0
            else:
                f = y / float(size - 1) if size > 1 else 0.0
            px[x, y] = tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3))
    return img


def tile(size, circular=False):
    """Background tile: diagonal gradient, corner highlight, rounded corners."""
    big = size * SS
    bg = _grad(big, BG_TOP, BG_BOTTOM).convert("RGBA")

    # A soft highlight in the top-left keeps the tile from looking flat.
    glow = Image.new("L", (big, big), 0)
    gd = ImageDraw.Draw(glow)
    rad = int(big * 0.62)
    gd.ellipse((-rad // 2, -rad // 2, rad, rad), fill=64)
    glow = glow.filter(ImageFilter.GaussianBlur(big * 0.10))
    bg.alpha_composite(Image.merge("RGBA", (
        Image.new("L", (big, big), 255),
        Image.new("L", (big, big), 255),
        Image.new("L", (big, big), 255), glow)))

    mask = Image.new("L", (big, big), 0)
    md = ImageDraw.Draw(mask)
    if circular:
        md.ellipse((0, 0, big - 1, big - 1), fill=255)
    else:
        md.rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * 0.235), fill=255)
    bg.putalpha(mask)
    return bg.resize((size, size), Image.LANCZOS)


def draw_glyph(img, size, inset, shadow=True):
    """Paint the two screens onto img (RGBA, `size` px) inside an `inset` box.

    inset is the fraction of the canvas kept as padding on each side, so the
    same routine serves the legacy icon (small padding) and the adaptive
    foreground (big padding, because launchers crop the outer third).

    The glyph is built on its own layer so it can be given a drop shadow by
    blurring that layer's alpha channel.
    """
    x0 = size * inset
    span = size * (1.0 - 2 * inset)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    def px(fx, fy):
        return (x0 + span * fx, x0 + span * fy)

    def box(a, b, c, e):
        return px(a, b) + px(c, e)

    # -- the PC: a wide rounded screen on a stand -----------------------------
    d.rounded_rectangle(box(0.02, 0.15, 0.60, 0.63), radius=span * 0.070, fill=WHITE)
    _screen(d, layer, px, box, span, 0.055, 0.185, 0.565, 0.585)
    # stand: neck then foot, chunky enough to survive 48 px
    d.rectangle(box(0.245, 0.63, 0.355, 0.715), fill=WHITE)
    d.rounded_rectangle(box(0.135, 0.715, 0.465, 0.805), radius=span * 0.030, fill=WHITE)

    # -- the phone: a narrow slab on the right --------------------------------
    d.rounded_rectangle(box(0.70, 0.09, 1.00, 0.85), radius=span * 0.095, fill=WHITE)
    _screen(d, layer, px, box, span, 0.742, 0.170, 0.958, 0.775)
    d.rounded_rectangle(box(0.795, 0.118, 0.905, 0.140), radius=span * 0.011, fill=FRAME)
    d.rounded_rectangle(box(0.805, 0.792, 0.895, 0.810), radius=span * 0.009, fill=FRAME)

    if shadow:
        alpha = layer.split()[3]
        blur = max(1.0, size * 0.020)
        sh_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        sh_img.putalpha(alpha.filter(ImageFilter.GaussianBlur(blur)).point(
            lambda v: int(v * 0.40)))
        sh_img = sh_img.transform(
            (size, size), Image.AFFINE, (1, 0, 0, 0, 1, -size * 0.016),
            resample=Image.BILINEAR)
        img.alpha_composite(sh_img)

    img.alpha_composite(layer)


def main():
    written = 0
    for dens, size in ICON_SIZES.items():
        folder = os.path.join(RES, "mipmap-" + dens)
        os.makedirs(folder, exist_ok=True)
        for name, circular in (("ic_launcher.png", False),
                               ("ic_launcher_round.png", True)):
            big = size * SS
            img = tile(size, circular).resize((big, big), Image.LANCZOS)
            draw_glyph(img, big, 0.13)
            img.resize((size, size), Image.LANCZOS).save(os.path.join(folder, name))
            written += 1

    # Adaptive foreground: transparent, glyph shrunk into the safe zone.
    for dens, size in ADAPTIVE_SIZES.items():
        folder = os.path.join(RES, "mipmap-" + dens)
        os.makedirs(folder, exist_ok=True)
        big = size * SS
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw_glyph(img, big, 0.26, shadow=False)
        img.resize((size, size), Image.LANCZOS).save(
            os.path.join(folder, "ic_launcher_foreground.png"))
        written += 1

    # keep the app icon in the repo root in step with the Android one
    root_ico = os.path.join(os.path.dirname(HERE), "DroidExtend.ico")
    try:
        big = tile(256).resize((256 * 2, 256 * 2), Image.LANCZOS)
        draw_glyph(big, 512, 0.13)
        big.resize((256, 256), Image.LANCZOS).save(
            root_ico, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
        written += 1
    except Exception as e:
        print("windows .ico skipped:", e)

    print("wrote %d files under %s" % (written, RES))


if __name__ == "__main__":
    main()
