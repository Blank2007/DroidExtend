"""Generate DroidExtend.ico with nothing but the standard library.

Writing the ICO by hand avoids adding Pillow just to ship a 64x64 icon.  The
format is a small header plus one or more DIBs: a BITMAPINFOHEADER whose height
is doubled (the XOR image followed by the 1bpp AND mask), 32bpp BGRA rows stored
bottom-up, and each row padded to a 4-byte boundary.
"""
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "DroidExtend.ico")

BG = (0x17, 0x1a, 0x1f, 255)          # panel
MON = (0x2b, 0x31, 0x3a, 255)         # monitor bezel
SCR = (0x0d, 0x11, 0x17, 255)         # screen glass
ACC = (0x3f, 0xb9, 0x50, 255)         # green accent (extends right)
PHO = (0x58, 0xa6, 0xff, 255)         # phone bezel
BLANK = (0, 0, 0, 0)


def blank(n):
    return [[BLANK] * n for _ in range(n)]


def rect(px, x0, y0, x1, y1, col):
    n = len(px)
    for y in range(max(0, y0), min(n, y1)):
        for x in range(max(0, x0), min(n, x1)):
            px[y][x] = col


def draw(n):
    px = blank(n)
    s = n / 64.0

    def R(x0, y0, x1, y1, col):
        rect(px, *(int(round(v * s)) for v in (x0, y0, x1, y1)), col)

    R(3, 6, 61, 58, BG)                       # rounded body
    for cx, cy in ((3, 6), (61, 6), (3, 58), (61, 58)):
        rect(px, int(cx * s), int(cy * s), int((cx + 2) * s), int((cy + 2) * s), BLANK)

    R(7, 11, 40, 45, MON)                     # monitor bezel
    R(9, 13, 38, 36, SCR)                     # screen
    R(11, 20, 22, 23, ACC)                    # a couple of "text" bars
    R(11, 25, 30, 28, ACC)
    R(24, 40, 28, 44, MON)                    # stand
    R(17, 44, 35, 47, MON)                    # base

    R(46, 16, 59, 50, PHO)                    # phone
    R(48, 18, 57, 44, SCR)
    R(48, 45, 57, 48, PHO)
    return px


def bmp(px):
    n = len(px)
    rows = []
    # XOR image: BGRA, bottom-up
    for y in range(n - 1, -1, -1):
        row = bytearray()
        for x in range(n):
            b, g, r, a = px[y][x][2], px[y][x][1], px[y][x][0], px[y][x][3]
            row += bytes((b, g, r, a))
        while len(row) % 4:
            row += b"\x00"
        rows.append(bytes(row))
    xor = b"".join(rows)
    # AND mask: 1bpp, bottom-up, rows padded to 4 bytes
    stride = ((n + 31) // 32) * 4
    mask = b"\x00" * (stride * n)
    hdr = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, len(xor) + len(mask),
                      0, 0, 0, 0)
    return hdr + xor + mask


def main():
    sizes = (16, 32, 48, 64)
    imgs = [bmp(draw(n)) for n in sizes]
    hdr = struct.pack("<HHH", 0, 1, len(sizes))
    off = len(hdr) + 16 * len(sizes)
    entries, blobs = b"", b""
    for n, im in zip(sizes, imgs):
        entries += struct.pack("<BBBBHHII", n if n < 256 else 0, n if n < 256 else 0,
                               0, 0, 1, 32, len(im), off)
        blobs += im
        off += len(im)
    with open(OUT, "wb") as f:
        f.write(hdr + entries + blobs)
    print("icon ->", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
