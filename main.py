#!/usr/bin/env python3
"""THALORNE — a travel poster for a place that isn't there.

A pure-stdlib (no network, no fonts, no image libraries) poster generator.
It draws a vintage travel poster, 1024x1024, of THALORNE: an island that
floats in a sunset sky, its waterfall pouring down into a turquoise sea,
with a white lighthouse on its green plateau. The invented place's name is
set in large hand-drawn vector capitals in a band at the foot of the poster.

Deterministic: the same bytes come out of every run (no clock, no random
seed from the environment). Writes /output/entry.png (or $POSTER_OUT).

Run:  python3 main.py
"""

import math
import os
import struct
import zlib

W = 1024
H = 1024
OUT = os.environ.get("POSTER_OUT", "/output/entry.png")

# ---------------------------------------------------------------- palette
CREAM = (243, 229, 195)
GOLD = (214, 164, 92)
GOLD_LIGHT = (247, 200, 115)
INK = (12, 46, 64)          # deep teal, title band
SKY_TOP = (22, 50, 79)
SKY_MID = (201, 106, 94)
SKY_HORIZON = (247, 200, 115)
SUN = (255, 232, 163)
SEA_TOP = (31, 111, 122)
SEA_BOTTOM = (13, 58, 77)
ISLAND_GREEN = (63, 122, 78)
ISLAND_GREEN_DARK = (44, 90, 56)
ROCK = (138, 90, 59)
ROCK_DARK = (94, 58, 38)
FALL = (223, 243, 242)
WHITE = (250, 246, 236)
LIGHTHOUSE_RED = (178, 62, 52)


# ---------------------------------------------------------------- canvas
class Canvas:
    """RGB canvas backed by one bytearray per row; row fills are C-speed."""

    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.rows = [bytearray(w * 3) for _ in range(h)]

    def fill_rect(self, x0, y0, x1, y1, color):
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(self.w, x1)
        y1 = min(self.h, y1)
        if x0 >= x1 or y0 >= y1:
            return
        span = bytes(color) * (x1 - x0)
        for y in range(y0, y1):
            self.rows[y][x0 * 3:x1 * 3] = span

    def hline(self, y, x0, x1, color):
        if 0 <= y < self.h:
            x0 = max(0, x0)
            x1 = min(self.w, x1)
            if x0 < x1:
                self.rows[y][x0 * 3:x1 * 3] = bytes(color) * (x1 - x0)

    def blend_hline(self, y, x0, x1, color, alpha):
        """Alpha-blend a horizontal run (alpha 0..1)."""
        if not (0 <= y < self.h) or alpha <= 0:
            return
        x0 = max(0, x0)
        x1 = min(self.w, x1)
        if x0 >= x1:
            return
        row = self.rows[y]
        r, g, b = color
        ia = 1.0 - alpha
        for x in range(x0, x1):
            i = x * 3
            row[i] = int(row[i] * ia + r * alpha)
            row[i + 1] = int(row[i + 1] * ia + g * alpha)
            row[i + 2] = int(row[i + 2] * ia + b * alpha)

    def vgrad(self, y0, y1, top, bottom):
        """Vertical gradient, one flat colour per row."""
        for y in range(max(0, y0), min(self.h, y1)):
            t = (y - y0) / max(1, y1 - y0 - 1)
            c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
            self.rows[y][:] = bytes(c) * self.w

    def fill_polygon(self, points, color):
        """Scanline polygon fill (points: list of (x, y))."""
        if len(points) < 3:
            return
        ys = [p[1] for p in points]
        y0, y1 = max(0, int(min(ys))), min(self.h, int(max(ys)) + 1)
        n = len(points)
        for y in range(y0, y1):
            xs = []
            for i in range(n):
                x1, yy1 = points[i]
                x2, yy2 = points[(i + 1) % n]
                if (yy1 <= y < yy2) or (yy2 <= y < yy1):
                    t = (y - yy1) / (yy2 - yy1)
                    xs.append(x1 + t * (x2 - x1))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                self.hline(y, int(xs[k]), int(xs[k + 1]) + 1, color)

    def fill_ellipse(self, cx, cy, rx, ry, color):
        for y in range(max(0, int(cy - ry)), min(self.h, int(cy + ry) + 1)):
            t = (y - cy) / ry
            if abs(t) >= 1:
                continue
            dx = int(rx * math.sqrt(1 - t * t))
            self.hline(y, cx - dx, cx + dx + 1, color)

    def blend_ellipse(self, cx, cy, rx, ry, color, alpha):
        for y in range(max(0, int(cy - ry)), min(self.h, int(cy + ry) + 1)):
            t = (y - cy) / ry
            if abs(t) >= 1:
                continue
            dx = int(rx * math.sqrt(1 - t * t))
            self.blend_hline(y, cx - dx, cx + dx + 1, color, alpha)

    def disc(self, cx, cy, r, color):
        """Filled disc, used to stamp thick strokes."""
        self.fill_ellipse(cx, cy, r, r, color)

    def thick_line(self, x0, y0, x1, y1, width, color):
        """A line drawn as a chain of discs (round caps, round joins)."""
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        steps = max(1, int(length / max(1.0, width * 0.45)))
        r = width / 2.0
        for i in range(steps + 1):
            t = i / steps
            self.disc(int(round(x0 + dx * t)), int(round(y0 + dy * t)),
                      int(math.ceil(r)), color)


# ------------------------------------------------------------------- font
# Hand-drawn vector capitals on a 10 x 14 grid (y down). Only the letters
# of THALORNE are needed; each is a list of strokes, each stroke a list of
# (x, y) grid points. O is an ellipse, handled separately.
GLYPHS = {
    "T": [[(0, 0), (10, 0)], [(5, 0), (5, 14)]],
    "H": [[(0, 0), (0, 14)], [(10, 0), (10, 14)], [(0, 7), (10, 7)]],
    "A": [[(0, 14), (5, 0)], [(5, 0), (10, 14)], [(2.6, 9), (7.4, 9)]],
    "L": [[(0, 0), (0, 14)], [(0, 14), (10, 14)]],
    "R": [[(0, 0), (0, 14)], [(0, 0), (6.5, 0)], [(6.5, 0), (10, 2.5)],
          [(10, 2.5), (10, 5)], [(10, 5), (6.5, 7)], [(6.5, 7), (0, 7)],
          [(5, 7), (10, 14)]],
    "N": [[(0, 14), (0, 0)], [(0, 0), (10, 14)], [(10, 14), (10, 0)]],
    "E": [[(0, 0), (0, 14)], [(0, 0), (9, 0)], [(0, 7), (6.5, 7)],
          [(0, 14), (10, 14)]],
}


def draw_text(canvas, text, cx, top, height, color, spacing, weight):
    """Draw vector capitals centred on cx, cap-height `height`."""
    scale = height / 14.0
    adv = 10 * scale + spacing
    total = len(text) * adv - spacing
    x = cx - total / 2.0
    for ch in text:
        ox = x
        if ch == "O":
            ocx = ox + 5 * scale
            ocy = top + 7 * scale
            # ellipse ring stamped as a chain of discs
            for k in range(72):
                a = 2 * math.pi * k / 72
                px = ocx + 5 * scale * math.cos(a)
                py = ocy + 7 * scale * math.sin(a)
                canvas.disc(int(round(px)), int(round(py)),
                            int(math.ceil(weight / 2)), color)
        else:
            for stroke in GLYPHS[ch]:
                pts = [(ox + gx * scale, top + gy * scale) for gx, gy in stroke]
                for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                    canvas.thick_line(ax, ay, bx, by, weight, color)
        x += adv


# ------------------------------------------------------------------ scene
def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def det(i, salt):
    """Deterministic pseudo-random in [0, 1) from an integer index."""
    v = math.sin(i * 127.1 + salt * 311.7) * 43758.5453
    return v - math.floor(v)


def build():
    c = Canvas(W, H)

    # --- frame and sky -------------------------------------------------
    c.fill_rect(0, 0, W, H, CREAM)
    c.fill_rect(26, 26, W - 26, H - 26, SKY_TOP)
    # sky: deep indigo -> dusty rose -> gold at the horizon (y 690)
    for y in range(26, 691):
        t = (y - 26) / 664.0
        if t < 0.55:
            col = lerp(SKY_TOP, SKY_MID, t / 0.55)
        else:
            col = lerp(SKY_MID, SKY_HORIZON, (t - 0.55) / 0.45)
        c.rows[y][26 * 3:(W - 26) * 3] = bytes(col) * (W - 52)

    # --- stars, high in the sky ---------------------------------------
    for i in range(46):
        sx = 40 + int(det(i, 1) * 944)
        sy = 34 + int(det(i, 2) * 250)
        r = 1 if det(i, 3) < 0.7 else 2
        c.disc(sx, sy, r, CREAM)

    # --- sun and its glow (offset right, so it reads as a sun, not a halo)
    for r, a in ((260, 0.10), (200, 0.14), (150, 0.20)):
        c.blend_ellipse(730, 590, r, r, SUN, a)
    c.fill_ellipse(730, 590, 96, 96, SUN)
    c.fill_ellipse(730, 590, 82, 82, (255, 240, 190))

    # --- clouds ---------------------------------------------------------
    for (cx, cy, rx, ry) in ((250, 210, 190, 26), (300, 235, 150, 20),
                             (790, 175, 170, 24), (830, 200, 130, 18),
                             (180, 330, 130, 16), (850, 320, 120, 15)):
        c.blend_ellipse(cx, cy, rx, ry, (255, 214, 170), 0.55)
        c.blend_ellipse(cx, cy - 6, rx * 0.7, ry * 0.7, (255, 232, 205), 0.5)

    # --- birds ----------------------------------------------------------
    for (bx, by, s) in ((300, 300, 14), (340, 285, 10), (700, 260, 12),
                        (740, 285, 9), (660, 310, 8)):
        c.thick_line(bx - s, by, bx, by - 5, 3, INK)
        c.thick_line(bx, by - 5, bx + s, by, 3, INK)

    # --- the floating island -------------------------------------------
    # rocky underside: a mountain hanging upside-down, point at (512, 655)
    c.fill_polygon([(318, 452), (706, 452), (600, 585), (512, 655),
                    (424, 585)], ROCK)
    c.fill_polygon([(360, 452), (664, 452), (590, 570), (512, 640),
                    (434, 570)], ROCK_DARK)
    # strata lines across the rock
    for (y, x0, x1) in ((490, 372, 652), (525, 402, 622), (560, 440, 584),
                        (600, 478, 546)):
        c.thick_line(x0, y, x1, y, 3, (70, 42, 28))
    # green plateau top
    c.fill_polygon([(306, 424), (372, 396), (652, 396), (718, 424),
                    (706, 452), (318, 452)], ISLAND_GREEN)
    c.fill_polygon([(330, 424), (390, 400), (640, 400), (700, 424),
                    (690, 440), (340, 440)], ISLAND_GREEN_DARK)

    # cypress trees on the plateau
    for (tx, th) in ((360, 42), (392, 30), (470, 36), (700, 34)):
        c.fill_polygon([(tx - 9, 400), (tx + 9, 400), (tx, 400 - th)],
                       (28, 66, 40))
        c.fill_rect(tx - 2, 398, tx + 2, 404, (28, 66, 40))

    # a small white house with a terracotta roof
    c.fill_rect(430, 372, 468, 400, WHITE)
    c.fill_polygon([(424, 372), (474, 372), (449, 352)], LIGHTHOUSE_RED)
    c.fill_rect(445, 382, 453, 400, (60, 60, 70))

    # the lighthouse: white tower, red lantern room, gold light
    c.fill_rect(596, 322, 610, 398, WHITE)
    c.fill_rect(594, 322, 612, 330, LIGHTHOUSE_RED)
    c.fill_rect(592, 330, 614, 336, (60, 60, 70))
    c.disc(603, 318, 4, GOLD_LIGHT)
    c.blend_ellipse(603, 318, 26, 10, GOLD_LIGHT, 0.45)

    # --- the waterfall ---------------------------------------------------
    # a ribbon of water pouring off the island's left edge into the sea
    c.fill_polygon([(352, 448), (392, 448), (404, 560), (398, 690),
                    (352, 690), (344, 560)], FALL)
    for i, fx in enumerate((360, 372, 384)):
        wob = 4 * math.sin(i * 2.1)
        c.thick_line(fx + wob, 460, fx - wob, 688, 3, (168, 214, 220))
    # foam where the fall meets the sea
    for (fx, fy, fr) in ((360, 692, 26), (385, 694, 20), (340, 694, 16),
                         (372, 686, 12)):
        c.fill_ellipse(fx, fy, fr, fr // 2, WHITE)

    # --- the sea ---------------------------------------------------------
    for y in range(691, 872):
        t = (y - 691) / 180.0
        col = lerp(SEA_TOP, SEA_BOTTOM, t)
        c.rows[y][26 * 3:(W - 26) * 3] = bytes(col) * (W - 52)
    # sun's reflection: gold dashes widening towards the viewer
    for k in range(12):
        y = 700 + k * 14
        half = 26 + k * 7
        dash = 34 - k
        x = 730 - half
        while x < 730 + half:
            c.blend_hline(y, x, min(x + dash, 730 + half), GOLD_LIGHT, 0.5)
            x += dash + 12
    # gentle wave lines
    for k in range(9):
        y = 712 + k * 18
        for x in range(40, W - 40, 6):
            yy = y + int(2 * math.sin(x / 47.0 + k))
            c.blend_hline(yy, x, x + 4, (140, 200, 205), 0.35)

    # --- title band ------------------------------------------------------
    c.fill_rect(26, 872, W - 26, H - 26, INK)
    c.fill_rect(26, 872, W - 26, 876, GOLD)
    c.fill_rect(26, 994, W - 26, 998, GOLD)
    # name in cream capitals, with a darker shadow for depth
    draw_text(c, "THALORNE", 512, 908, 62, (7, 30, 42), 18, 9)
    draw_text(c, "THALORNE", 512, 905, 62, CREAM, 18, 9)

    # --- inner gold rule --------------------------------------------------
    c.fill_rect(26, 26, W - 26, 28, GOLD)
    c.fill_rect(26, H - 28, W - 26, H - 26, GOLD)
    c.fill_rect(26, 26, 28, H - 26, GOLD)
    c.fill_rect(W - 28, 26, W - 26, H - 26, GOLD)

    return c


# -------------------------------------------------------------------- png
def write_png(canvas, path):
    raw = b"".join(b"\x00" + bytes(row) for row in canvas.rows)

    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        return out + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


def main():
    canvas = build()
    size = write_png(canvas, OUT)
    print(f"wrote {OUT}: {W}x{H}, {size} bytes")


if __name__ == "__main__":
    main()
