#!/usr/bin/env python3
"""Render isopod-shell icons and placeholder artwork. Icons are white on transparent;
the shell tints them at draw time. Everything is drawn at 4x and downsampled."""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SS = 4
BOLD_FONT = os.environ.get("ISOPOD_BOLD_FONT", "/usr/share/fonts/pretendard/Pretendard-Bold.otf")
W = (255, 255, 255, 255)


def canvas(w, h):
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def done(img, w, h, path):
    img.resize((w, h), Image.LANCZOS).save(path)


def s(v):
    return v * SS


def line(d, pts, width):
    d.line([(s(x), s(y)) for x, y in pts], fill=W, width=int(s(width)), joint="curve")
    for x, y in (pts[0], pts[-1]):
        r = s(width) / 2
        d.ellipse([s(x) - r, s(y) - r, s(x) + r, s(y) + r], fill=W)


def arc(d, cx, cy, r, start, end, width):
    d.arc([s(cx - r), s(cy - r), s(cx + r), s(cy + r)], start, end, fill=W, width=int(s(width)))


def poly(d, pts):
    d.polygon([(s(x), s(y)) for x, y in pts], fill=W)


def rrect(d, x0, y0, x1, y1, r, fill=True, width=2):
    if fill:
        d.rounded_rectangle([s(x0), s(y0), s(x1), s(y1)], radius=s(r), fill=W)
    else:
        d.rounded_rectangle([s(x0), s(y0), s(x1), s(y1)], radius=s(r), outline=W, width=int(s(width)))


def ellipse(d, x0, y0, x1, y1, fill=True, width=2):
    if fill:
        d.ellipse([s(x0), s(y0), s(x1), s(y1)], fill=W)
    else:
        d.ellipse([s(x0), s(y0), s(x1), s(y1)], outline=W, width=int(s(width)))


def note(d, ox, oy, k):
    """Beamed eighth notes (♫) in a k-scaled 40x40 box."""
    ellipse(d, ox + 2 * k, oy + 28 * k, ox + 14 * k, oy + 38 * k)
    ellipse(d, ox + 24 * k, oy + 24 * k, ox + 36 * k, oy + 34 * k)
    poly(d, [(ox + 11.5 * k, oy + 32 * k), (ox + 14 * k, oy + 32 * k), (ox + 14 * k, oy + 8 * k), (ox + 11.5 * k, oy + 9 * k)])
    poly(d, [(ox + 33.5 * k, oy + 28 * k), (ox + 36 * k, oy + 28 * k), (ox + 36 * k, oy + 2 * k), (ox + 33.5 * k, oy + 3 * k)])
    poly(d, [(ox + 11.5 * k, oy + 8 * k), (ox + 36 * k, oy + 1 * k), (ox + 36 * k, oy + 7 * k), (ox + 11.5 * k, oy + 14 * k)])


def star(d, cx, cy, r):
    """A five-pointed star, r from its middle to a point, the points a little rounded."""
    soft = r * 0.07
    pts = [(cx + k * math.sin(i * math.pi / 5), cy - k * math.cos(i * math.pi / 5))
           for i, k in enumerate([r - soft, (r - soft) * 0.47] * 5)]
    poly(d, pts)
    line(d, pts + pts[:1], soft * 2)


def on_air(d, cx, cy, k, width):
    """A station: a dot, and the waves it sends out to either side."""
    ellipse(d, cx - 8 * k, cy - 8 * k, cx + 8 * k, cy + 8 * k)
    for r in (22, 37, 52):
        arc(d, cx, cy, r * k, 135, 225, width)
        arc(d, cx, cy, r * k, -45, 45, width)


def microphone(d, cx, cy, k, width):
    """A podcast: a microphone's head, the waves around it open below, and its stem."""
    ellipse(d, cx - 10 * k, cy - 10 * k, cx + 10 * k, cy + 10 * k)
    rrect(d, cx - 6.5 * k, cy + 17 * k, cx + 6.5 * k, cy + 46 * k, 6.5 * k)
    for r in (25, 41):
        arc(d, cx, cy, r * k, 140, 400, width)


def headphones(d, ox, oy, k, width):
    arc(d, ox + 20 * k, oy + 21 * k, 16 * k, 180, 360, width * k)
    rrect(d, ox + 2 * k, oy + 20 * k, ox + 10 * k, oy + 36 * k, 3 * k)
    rrect(d, ox + 30 * k, oy + 20 * k, ox + 38 * k, oy + 36 * k, 3 * k)


def listening_book(d, ox, oy, k, width):
    """An audiobook: an open book between the cups of a pair of headphones."""
    headphones(d, ox, oy, k, width)
    poly(d, [(ox + 12.5 * k, oy + 22 * k), (ox + 19 * k, oy + 23.5 * k), (ox + 19 * k, oy + 35 * k),
             (ox + 12.5 * k, oy + 33.5 * k)])
    poly(d, [(ox + 21 * k, oy + 23.5 * k), (ox + 27.5 * k, oy + 22 * k), (ox + 27.5 * k, oy + 33.5 * k),
             (ox + 21 * k, oy + 35 * k)])


def speaker_body(d, ox, oy, k):
    poly(d, [(ox + 2 * k, oy + 14 * k), (ox + 10 * k, oy + 14 * k), (ox + 20 * k, oy + 5 * k),
             (ox + 20 * k, oy + 35 * k), (ox + 10 * k, oy + 26 * k), (ox + 2 * k, oy + 26 * k)])


CLEAR = (0, 0, 0, 0)




def empty_icons(P):
    """72 pt glyphs in a thin ring for empty lists, one per kind of content."""
    def glyph(name, draw):
        img, d = canvas(72, 72)
        ellipse(d, 3, 3, 69, 69, fill=False, width=3)
        draw(d)
        done(img, 72, 72, P("empty-" + name))

    glyph("music", lambda d: note(d, 18, 16, 0.9))
    glyph("star", lambda d: star(d, 36, 37.5, 20))

    def wifi(d):
        ellipse(d, 32.5, 45, 39.5, 52)
        for r in (11, 19, 27):
            arc(d, 36, 50, r, 225, 315, 3.6)
    glyph("wifi", wifi)

    def search(d):
        ellipse(d, 20, 20, 44, 44, fill=False, width=3.6)
        line(d, [(41.5, 41.5), (51, 51)], 5)
    glyph("search", search)

    def video(d):
        rrect(d, 16, 23, 56, 49, 5, fill=False, width=3.2)
        poly(d, [(31, 29.5), (31, 42.5), (42.5, 36)])
    glyph("video", video)

    def book(d):
        poly(d, [(15, 24), (34, 27.5), (34, 50), (15, 46.5)])
        poly(d, [(38, 27.5), (57, 24), (57, 46.5), (38, 50)])
    glyph("book", book)

    glyph("podcast", lambda d: microphone(d, 36, 31, 0.5, 3))
    glyph("audiobook", lambda d: listening_book(d, 18, 17, 0.9, 3.4))



# The artwork panel beside split menus: a white disc on the section's colour, with the
# section's glyph in it. Glyphs are drawn on an 88 grid around 44,44 and saved at 100, so
# that each fills about half the 152-wide disc; their strokes share one weight and each is
# sized by eye against the others, the open ones a little larger than the solid ones.

DISC = 152
GLYPH = 100
STROKE = 7.5


def _px(v):
    return v * SS


def _g_line(d, pts, width):
    d.line([(_px(x), _px(y)) for x, y in pts], fill=255, width=int(_px(width)), joint="curve")
    for x, y in (pts[0], pts[-1]):
        r = _px(width) / 2
        d.ellipse([_px(x) - r, _px(y) - r, _px(x) + r, _px(y) + r], fill=255)


def _g_arc(d, cx, cy, r, start, end, width, caps=True):
    d.arc([_px(cx - r), _px(cy - r), _px(cx + r), _px(cy + r)], start, end, fill=255, width=int(_px(width)))
    if caps:
        for a in (start, end):
            x = cx + (r - width / 2) * math.cos(math.radians(a))
            y = cy + (r - width / 2) * math.sin(math.radians(a))
            d.ellipse([_px(x - width / 2), _px(y - width / 2), _px(x + width / 2), _px(y + width / 2)], fill=255)


def _g_poly(d, pts, fill=255):
    d.polygon([(_px(x), _px(y)) for x, y in pts], fill=fill)


def _g_circle(d, cx, cy, r, fill=255):
    d.ellipse([_px(cx - r), _px(cy - r), _px(cx + r), _px(cy + r)], fill=fill)


def _g_rrect(d, x0, y0, x1, y1, r):
    d.rounded_rectangle([_px(x0), _px(y0), _px(x1), _px(y1)], radius=_px(r), fill=255)


def _g_head(d, cx, cy, rx=11, ry=8.4, angle=-24):
    """A note's head, an ellipse leaning back. Returns the x of its right edge, where its
    stem stands, so that the two meet without a step."""
    a = math.radians(angle)
    pts = [(rx * math.cos(t), ry * math.sin(t)) for t in (2 * math.pi * i / 96 for i in range(96))]
    _g_poly(d, [(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a)) for x, y in pts])
    return cx + math.hypot(rx * math.cos(a), ry * math.sin(a))


def _g_curve(p0, p1, p2, n=24):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in (i / n for i in range(n + 1))]


def _g_pages(d, spine, edge, top, bottom, lift):
    """An open book's two pages, their outer corners lifted."""
    left = (_g_curve((spine, top + lift), ((spine + edge) / 2, top - lift), (edge, top))
            + _g_curve((edge, bottom), ((spine + edge) / 2, bottom - lift), (spine, bottom + lift)))
    _g_poly(d, left)
    _g_poly(d, [(88 - x, y) for x, y in left])


NOTE_STEM, NOTE_BEAM = 6.5, 10


def _music(d):
    """Two eighths under one beam, their stems on their heads' right edges and stopping at
    the beam's slope."""
    right1, right2 = _g_head(d, 27, 64), _g_head(d, 62, 57)
    left, right, top1, top2 = right1 - NOTE_STEM, right2, 22, 14

    def beam(x):
        return top1 + (top2 - top1) * (x - left) / (right - left)
    for edge, foot in ((right1, 64), (right2, 57)):
        _g_poly(d, [(edge - NOTE_STEM, foot), (edge, foot), (edge, beam(edge)),
                    (edge - NOTE_STEM, beam(edge - NOTE_STEM))])
    _g_poly(d, [(left, top1), (right, top2), (right, top2 + NOTE_BEAM), (left, top1 + NOTE_BEAM)])


def _radio(d):
    _g_circle(d, 44, 44, 8.5)
    for r in (21, 35):
        _g_arc(d, 44, 44, r, 135, 225, STROKE + 0.5)
        _g_arc(d, 44, 44, r, -45, 45, STROKE + 0.5)


def _podcast(d):
    _g_circle(d, 44, 38, 8.5)
    _g_rrect(d, 38, 51, 50, 76, 6)
    for r in (20, 32):
        _g_arc(d, 44, 38, r, 145, 395, STROKE)


def _video(d):
    _g_rrect(d, 11, 22, 77, 66, 10)
    _g_poly(d, [(38, 32), (38, 56), (57, 44)], fill=0)


def _book(d):
    _g_pages(d, 41.5, 11, 23, 64, 3.5)


def _audiobook(d):
    _g_arc(d, 44, 46, 30, 180, 360, STROKE, caps=False)
    _g_rrect(d, 10, 42, 24, 70, 6)
    _g_rrect(d, 64, 42, 78, 70, 6)
    _g_pages(d, 42.5, 29, 48, 68, 2)


def _settings(d):
    teeth, outer, body = 8, 34, 25
    for i in range(teeth):
        a = math.radians(i * 360 / teeth)
        c, n = math.cos(a), math.sin(a)
        _g_poly(d, [(44 + r * c - w * n, 44 + r * n + w * c)
                    for r, w in ((body - 2, 7), (outer, 5.5), (outer, -5.5), (body - 2, -7))])
    _g_circle(d, 44, 44, body)
    _g_circle(d, 44, 44, 11.5, fill=0)


def _playlist(d):
    """Three lines of a list and an eighth whose flag curls from the stem's top to a point."""
    for y, end in ((25, 46), (40, 46), (55, 34)):
        _g_line(d, [(12, y), (end, y)], 7)
    right = _g_head(d, 58, 66, 10.5, 8)
    _g_poly(d, [(right - NOTE_STEM, 66), (right, 66), (right, 17), (right - NOTE_STEM, 17)])
    outer = _g_curve((right - NOTE_STEM, 17), (right + 15.5, 23), (right + 11.5, 45))
    inner = _g_curve((right + 11.5, 45), (right + 9, 31), (right - NOTE_STEM, 31))
    _g_poly(d, outer + inner)


def _genre(d):
    for x, y in ((15, 15), (47, 15), (15, 47), (47, 47)):
        _g_rrect(d, x, y, x + 26, y + 26, 8)


def _search(d):
    _g_arc(d, 38, 38, 25, 0, 360, STROKE + 1, caps=False)
    _g_line(d, [(57, 57), (74, 74)], STROKE + 3.5)


def glyphs(P):
    """The sections' glyphs, white, which the shell tints with each section's ink."""
    for name, draw in (("music", _music), ("radio", _radio), ("podcast", _podcast), ("video", _video),
                       ("book", _book), ("audiobook", _audiobook), ("settings", _settings),
                       ("playlist", _playlist), ("genre", _genre), ("search", _search)):
        mask = Image.new("L", (88 * SS, 88 * SS), 0)
        draw(ImageDraw.Draw(mask))
        img = Image.new("RGBA", mask.size, W)
        img.putalpha(mask)
        img.resize((GLYPH, GLYPH), Image.LANCZOS).save(P("glyph-" + name))


def gizmo(P):
    """The disc the glyphs sit in: white, lit a little from above, with a hairline edge; and
    its soft shadow below it, a white mask the shell tints a deep shade of the section's
    colour. Both are the same size, to be drawn on the same spot."""
    n = DISC + 56
    c = n / 2
    shadow = Image.new("L", (n, n), 0)
    ImageDraw.Draw(shadow).ellipse([c - DISC / 2 + 4, c - DISC / 2 + 10, c + DISC / 2 - 4, c + DISC / 2 + 10],
                                   fill=255)
    shade = Image.new("RGBA", (n, n), W)
    shade.putalpha(shadow.filter(ImageFilter.GaussianBlur(11)))
    shade.save(P("gizmo-shadow"))
    face = Image.new("RGBA", (n * SS, n * SS), (0, 0, 0, 0))
    light = Image.new("RGBA", face.size)
    ld = ImageDraw.Draw(light)
    for y in range(n * SS):
        v = int(255 - 14 * y / (n * SS))
        ld.line([(0, y), (n * SS, y)], fill=(v, v, min(255, v + 3), 255))
    disc = [_px(c - DISC / 2), _px(c - DISC / 2), _px(c + DISC / 2), _px(c + DISC / 2)]
    mask = Image.new("L", face.size, 0)
    ImageDraw.Draw(mask).ellipse(disc, fill=255)
    face.paste(light, (0, 0), mask)
    ImageDraw.Draw(face).ellipse(disc, outline=(0, 0, 0, 16), width=SS)
    face.resize((n, n), Image.LANCZOS).save(P("gizmo"))


def build(out):
    icons = os.path.join(out, "icons")
    os.makedirs(icons, exist_ok=True)
    P = lambda name: os.path.join(icons, name + ".png")  # noqa: E731

    img, d = canvas(11, 20)
    line(d, [(2, 2), (9, 10), (2, 18)], 2.6)
    done(img, 11, 20, P("chevron"))

    img, d = canvas(20, 18)
    line(d, [(2, 9.5), (7.5, 15), (18, 3)], 3)
    done(img, 20, 18, P("check"))

    img, d = canvas(18, 18)
    poly(d, [(3, 1.5), (16, 9), (3, 16.5)])
    done(img, 18, 18, P("play"))

    img, d = canvas(18, 18)
    rrect(d, 2.5, 2, 7, 16, 1)
    rrect(d, 11, 2, 15.5, 16, 1)
    done(img, 18, 18, P("pause"))

    img, d = canvas(38, 18)
    rrect(d, 1, 1, 33, 17, 4, fill=False, width=2)
    rrect(d, 34.5, 6, 37, 12, 1.2)
    done(img, 38, 18, P("battery"))

    img, d = canvas(18, 16)
    poly(d, [(10, 0.5), (3, 9), (8, 9), (6.5, 15.5), (15, 6.5), (10, 6.5), (11.5, 0.5)])
    done(img, 18, 16, P("bolt"))

    img, d = canvas(26, 22)
    headphones(d, 1, -1, 0.6, 3.6)
    done(img, 26, 22, P("bt-headphones"))

    img, d = canvas(14, 22)
    line(d, [(2.5, 6.5), (11.5, 15.5), (7, 20), (7, 2), (11.5, 6.5), (2.5, 15.5)], 2)
    done(img, 14, 22, P("bar-bluetooth"))

    img, d = canvas(22, 20)
    speaker_body(d, 0, 0, 0.5)
    arc(d, 11, 10, 5, -50, 50, 1.8)
    arc(d, 11, 10, 9, -50, 50, 1.8)
    done(img, 22, 20, P("speaker"))

    img, d = canvas(18, 18)
    star(d, 9, 9.9, 9.45)
    done(img, 18, 18, P("star"))

    img, d = canvas(14, 20)
    arc(d, 7, 8, 4.5, 180, 360, 2.2)
    line(d, [(2.5, 8), (2.5, 10)], 2.2)
    line(d, [(11.5, 8), (11.5, 10)], 2.2)
    rrect(d, 0.5, 9, 13.5, 19.5, 2.5)
    done(img, 14, 20, P("lock"))

    for level in range(4):
        img, d = canvas(26, 20)
        ellipse(d, 10.5, 15, 15.5, 20)
        for i, r in enumerate((7, 12, 17)):
            if i < level:
                arc(d, 13, 18, r, 225, 315, 2.6)
            else:
                d.arc([s(13 - r), s(18 - r), s(13 + r), s(18 + r)], 225, 315,
                      fill=(255, 255, 255, 70), width=int(s(2.6)))
        done(img, 26, 20, P(f"wifi-{level}"))

    img, d = canvas(28, 28)
    for i in range(12):
        a = math.radians(i * 30 - 90)
        alpha = int(255 * (0.25 + 0.75 * (i / 11)))
        x0, y0 = 14 + 6.5 * math.cos(a), 14 + 6.5 * math.sin(a)
        x1, y1 = 14 + 12 * math.cos(a), 14 + 12 * math.sin(a)
        d.line([(s(x0), s(y0)), (s(x1), s(y1))], fill=(255, 255, 255, alpha), width=int(s(2.6)))
    done(img, 28, 28, P("spinner"))

    img, d = canvas(28, 28)
    headphones(d, 0, 0, 0.7, 3.4)
    done(img, 28, 28, P("dev-headphones"))

    img, d = canvas(28, 28)
    rrect(d, 1, 8, 27, 22, 7)
    d_cut = ImageDraw.Draw(img)
    for pts in ([(5, 15), (11, 15)], [(8, 12), (8, 18)]):
        d_cut.line([(s(x), s(y)) for x, y in pts], fill=(0, 0, 0, 0), width=int(s(2.2)))
    for cx, cy in ((19, 13), (22.5, 16.5)):
        d_cut.ellipse([s(cx - 1.6), s(cy - 1.6), s(cx + 1.6), s(cy + 1.6)], fill=(0, 0, 0, 0))
    done(img, 28, 28, P("dev-gamepad"))

    img, d = canvas(28, 28)
    line(d, [(7, 8.5), (19.5, 19.5), (14, 25), (14, 3), (19.5, 8.5), (7, 19.5)], 2.4)
    done(img, 28, 28, P("dev-bluetooth"))

    img, d = canvas(22, 22)
    speaker_body(d, 0, 1, 0.5)
    arc(d, 11, 11, 5, -50, 50, 1.8)
    done(img, 22, 22, P("speaker-low"))

    img, d = canvas(28, 22)
    speaker_body(d, 0, 1, 0.5)
    arc(d, 11, 11, 5, -50, 50, 1.8)
    arc(d, 11, 11, 9.5, -50, 50, 1.8)
    arc(d, 11, 11, 14, -50, 50, 1.8)
    done(img, 28, 22, P("speaker-high"))

    img, d = canvas(24, 20)
    line(d, [(2, 5), (7, 5), (15, 15), (19, 15)], 2.2)
    line(d, [(2, 15), (7, 15), (9.5, 12)], 2.2)
    line(d, [(12.5, 8), (15, 5), (19, 5)], 2.2)
    poly(d, [(18, 1.5), (23, 5), (18, 8.5)])
    poly(d, [(18, 11.5), (23, 15), (18, 18.5)])
    done(img, 24, 20, P("shuffle"))

    for name, one in (("repeat", False), ("repeat-one", True)):
        img, d = canvas(24, 20)
        line(d, [(4, 12), (4, 7), (6, 5), (18, 5)], 2.2)
        line(d, [(20, 8), (20, 13), (18, 15), (6, 15)], 2.2)
        poly(d, [(17, 1.5), (22, 5), (17, 8.5)])
        poly(d, [(7, 11.5), (2, 15), (7, 18.5)])
        if one:
            f = ImageFont.truetype(BOLD_FONT, int(s(9.5)))
            d.text((s(12), s(10.2)), "1", font=f, fill=W, anchor="mm")
        done(img, 24, 20, P(name))

    for name, size, rays, core, outline in (("sun-small", 24, 5, 4, False), ("sun-large", 56, 11, 10, False),
                                            ("sun-large-outline", 32, 6.5, 5.5, False)):
        img, d = canvas(size, size)
        c = size / 2
        ellipse(d, c - core, c - core, c + core, c + core)
        for i in range(8):
            a = math.radians(i * 45)
            line(d, [(c + (core + rays * 0.35) * math.cos(a), c + (core + rays * 0.35) * math.sin(a)),
                     (c + (core + rays) * math.cos(a), c + (core + rays) * math.sin(a))],
                 max(1.6, size / 20))
        done(img, size, size, P(name))

    empty_icons(P)
    glyphs(P)
    gizmo(P)

    # On the card: an arrow come down to its line.
    img, d = canvas(18, 18)
    line(d, [(9, 2), (9, 10)], 2.4)
    poly(d, [(4, 8), (14, 8), (9, 13.5)])
    line(d, [(3, 16.2), (15, 16.2)], 2.2)
    done(img, 18, 18, P("saved"))

    img, d = canvas(80, 80)
    speaker_body(d, 10, 18, 1.1)
    arc(d, 34, 40, 11, -50, 50, 4)
    arc(d, 34, 40, 21, -50, 50, 4)
    arc(d, 34, 40, 31, -50, 50, 4)
    done(img, 80, 80, P("hud-speaker"))

    img, d = canvas(80, 80)
    speaker_body(d, 14, 18, 1.1)
    line(d, [(50, 30), (68, 50)], 4)
    line(d, [(68, 30), (50, 50)], 4)
    done(img, 80, 80, P("hud-mute"))

    img, d = canvas(80, 80)
    headphones(d, 4, 4, 1.8, 3.4)
    done(img, 80, 80, P("hud-headphones"))

    for name in ("bluetooth", "wifi", "music", "moon", "battery"):
        img, d = canvas(22, 22)
        if name == "bluetooth":
            line(d, [(5.5, 7), (15.5, 15.5), (11, 20), (11, 2), (15.5, 6.5), (5.5, 15)], 2)
        elif name == "wifi":
            ellipse(d, 9, 16, 13, 20)
            for r in (6, 11, 16):
                arc(d, 11, 18, r, 225, 315, 2.2)
        elif name == "moon":
            ellipse(d, 2, 2, 20, 20)
            d.ellipse([s(7.5), s(-1.5), s(24), s(15)], fill=CLEAR)
        elif name == "battery":
            rrect(d, 1, 6, 18, 16, 2.5, fill=False, width=1.8)
            rrect(d, 19, 9, 21, 13, 0.8)
            rrect(d, 3.6, 8.6, 7.5, 13.4, 1)
        else:
            note(d, 1, 1, 0.5)
        done(img, 22, 22, os.path.join(icons, f"toast-{name}.png"))

    art(out)
    splash(out)


def gradient(size, top, bottom):
    img = Image.new("RGBA", (size, size))
    px = img.load()
    for y in range(size):
        t = y / (size - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        for x in range(size):
            px[x, y] = c
    return img


def art(out):
    size = 512
    base = gradient(size, (244, 244, 246), (206, 206, 212))
    glyph, d = canvas(128, 128)
    note(d, 24, 26, 2.0)
    glyph = glyph.resize((size, size), Image.LANCZOS)
    tint = Image.new("RGBA", (size, size), (158, 158, 166, 255))
    base.paste(tint, (0, 0), glyph)
    base.convert("RGB").save(os.path.join(out, "art-default.png"))

    base = gradient(size, (244, 244, 246), (206, 206, 212))
    glyph, d = canvas(128, 128)
    on_air(d, 64, 64, 0.82, 6)
    base.paste(tint, (0, 0), glyph.resize((size, size), Image.LANCZOS))
    base.convert("RGB").save(os.path.join(out, "art-radio.png"))

    base = gradient(size, (244, 244, 246), (206, 206, 212))
    glyph, d = canvas(128, 128)
    microphone(d, 64, 50, 0.82, 6)
    base.paste(tint, (0, 0), glyph.resize((size, size), Image.LANCZOS))
    base.convert("RGB").save(os.path.join(out, "art-podcast.png"))


PILL_BUG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "art", "pill-bug.png")


def pill_bug(path=PILL_BUG, plates=7, gap=40):
    """A pill bug for the boot screen, to stand in until someone draws the real one: it is
    only made when art/pill-bug.png is not there. It curls into its ball about a round
    middle: a ring of plates from the head, right of the top, round to a thinner tail left
    of it. Any square picture on a clear background can take its place."""
    if os.path.exists(path):
        return
    n = 1200
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c, outer, inner, light = n / 2, n * 0.43, n * 0.197, 240
    head, step = gap / 2 - 90, (360 - gap) / plates

    def at(radius, degrees):
        a = math.radians(degrees)
        return (c + radius * math.cos(a), c + radius * math.sin(a))

    def rim(plate):
        return outer * (1 - 0.1 * max(0, plate - plates + 3) / 3)

    def dot(centre, radius, fill):
        d.ellipse([centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius], fill=fill)

    def grey(shade):
        return (shade, shade, shade, 255)

    for i in range(plates):
        a0 = head + step * i
        arc = [at(rim(i) + (rim(i + 1) - rim(i)) * t / 16, a0 + step * t / 16) for t in range(17)]
        d.polygon(arc + [at(inner, a0 + step - step * t / 16) for t in range(17)], fill=grey(light - 11 * i))
    for i in range(1, plates):
        d.line([at(inner * 0.9, head + step * i - 5), at(outer * 1.03, head + step * i + 5)],
               fill=(0, 0, 0, 0), width=round(n * 0.02))
    thick = (outer - inner) / 2
    dot(at(inner + thick, head + 3), thick, grey(light))
    dot(at(inner + thick * 1.2, head + 9), n * 0.02, grey(0))
    for lean in (-18, 8):
        d.line([at(outer - thick * 0.25, head + 2), at(outer + thick * 0.55, head + lean)],
               fill=grey(light), width=round(n * 0.014))
    tail = (rim(plates) - inner) / 2
    dot(at(inner + tail, head + 360 - gap - 2), tail, grey(light - 11 * (plates - 1)))
    dot((c, c), inner * 0.52, grey(light))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)


def splash(out, credit="based on ROCKNIX"):
    """The boot screen, light on black and all on one centre line: the pill bug, isoPod's
    name under it and, small, whose work it stands on. A PPM that isopod-splash copies to
    the middle of the framebuffer as it is."""
    pill_bug()
    w, mark, name_size, credit_size = 300, 144, 44, 17
    name_font = ImageFont.truetype(BOLD_FONT, name_size * SS)
    credit_font = ImageFont.truetype(BOLD_FONT.replace("-Bold", "-Medium"), credit_size * SS)
    top = mark - 6
    h = top + 22 + name_size + 14 + 18 + credit_size + 6
    img = Image.new("RGB", (w * SS, h * SS), (0, 0, 0))
    d = ImageDraw.Draw(img)
    parts = (("iso", (168, 168, 168)), ("Pod", (244, 244, 244)))
    x = (w * SS - sum(name_font.getlength(text) for text, _ in parts)) / 2
    for text, color in parts:
        d.text((x, (top + 22) * SS), text, font=name_font, fill=color)
        x += name_font.getlength(text)
    d.text((w * SS / 2, (h - 6) * SS), credit, font=credit_font, fill=(118, 118, 118), anchor="ms")
    img = img.resize((w, h), Image.LANCZOS)
    bug = Image.open(PILL_BUG).convert("RGBA").resize((mark, mark), Image.LANCZOS)
    img.paste(bug, ((w - mark) // 2, 0), bug)
    img.save(os.path.join(out, "splash.ppm"))


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "shell", "assets"))
