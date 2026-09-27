"""Monochrome NSFW glyphs for the Cronologia bar, as CSS mask data URIs.

Prints the `.g-*` rules of app/static/style.css (block "NSFW on the session
bar"); tests/test_nsfw_ui_contract.py checks the stylesheet matches.
Usage: python scripts/nsfw-glyphs.py

Same drawings as app/static/icons.svg, flattened to one colour: the internal
<mask> keeps the icon's shading as alpha (white = full colour, grey = darker
shade, black = cut), so the glyph takes the colour of the element it masks.
Every viewBox has the same 1.1 aspect ratio (drawing centred, about one unit
of gap each side), so a chain of glyphs (mask-repeat: space) has one pitch for
every body part and the shortest stretch (one glyph) is the same width.
"""
from urllib.parse import quote

W, G, K = "#fff", "shade", "#000"  # full, shade (45%), cut
SHADE = 0.45

GLYPHS = {
    "vulva": ("1 2 22 20", [
        ("path", W, "M12 2.4c3.6 0 6.2 2.2 6.2 5.4 0 4.6-2.8 9.8-6.2 13.8C8.6 17.6 5.8 12.4 5.8 7.8c0-3.2 2.6-5.4 6.2-5.4z"),
        ("path", G, "M12 7c1.9 1.6 2.7 4.2 2.4 7-.3 2.3-1.2 4.1-2.4 5.4-1.2-1.3-2.1-3.1-2.4-5.4-.3-2.8.5-5.4 2.4-7z"),
        ("path", K, "M12 9.2c.9 1.3 1.2 3.1 1 4.9-.2 1.4-.5 2.5-1 3.4-.5-.9-.8-2-1-3.4-.2-1.8.1-3.6 1-4.9z"),
    ]),
    "phallus": ("1 2.2 22 20", [
        ("circle", W, (8.8, 18.6, 3.2)),
        ("circle", W, (15.2, 18.6, 3.2)),
        ("path", W, "M9.4 18.4V9.6h5.2v8.8c-.8.5-1.7.8-2.6.8s-1.8-.3-2.6-.8z"),
        ("path", W, "M8.8 10c0-3.6 1.4-7.4 3.2-7.4s3.2 3.8 3.2 7.4c-.9.6-2 .9-3.2.9s-2.3-.3-3.2-.9z"),
        ("stroke", K, ("M8.9 10.1c.9.6 2 .9 3.1.9s2.2-.3 3.1-.9", 1.1)),
        ("stroke", K, ("M12 3.6v1.4", 0.9)),
    ]),
    "anus": ("0.5 1 23 20.9", [
        ("path", W, "M12 5.4C10.5 3.6 8 2.8 5.7 3.5 2.9 4.4 1.3 7.4 1.5 11c.3 4.6 3.4 8.6 7.2 9 1.4.1 2.5-.3 3.3-1 .8.7 1.9 1.1 3.3 1 3.8-.4 6.9-4.4 7.2-9 .2-3.6-1.4-6.6-4.2-7.5-2.3-.7-4.8.1-6.3 1.9z"),
        ("ellipse", G, (12, 12.6, 3, 3.4)),
        ("ellipse", K, (12, 12.6, 1.3, 1.5)),
    ]),
    "breast": ("0.5 1.55 23 20.9", [
        ("circle", W, (6.6, 12.2, 5.3)),
        ("circle", W, (17.4, 12.2, 5.3)),
        ("circle", G, (7.3, 13.9, 2.1)),
        ("circle", G, (16.7, 13.9, 2.1)),
        ("circle", K, (7.3, 14.0, 0.95)),
        ("circle", K, (16.7, 14.0, 0.95)),
    ]),
    "butt": ("0.8 2.1 22.4 20.4", [
        ("path", W, "M12 7C10.6 5.2 8.3 4.4 6.1 5 3.3 5.8 1.7 8.6 1.9 12.1c.2 4.1 2.8 7.4 6.1 7.7 1.8.2 3.1-.5 4-1.7.9 1.2 2.2 1.9 4 1.7 3.3-.3 5.9-3.6 6.1-7.7.2-3.5-1.4-6.3-4.2-7.1-2.2-.6-4.5.2-6 2z"),
        ("stroke", K, ("M12 7.4c-.2 3.3-.1 6.9 0 10.4", 1.4)),
        ("stroke", G, ("M3.6 15.8c1.4 2.1 3.6 3 6 2.4M20.4 15.8c-1.4 2.1-3.6 3-6 2.4", 1.2)),
    ]),
    "other": ("1 2 22 20", [
        ("path", W, "M12 3.5l6.5 8.5-6.5 8.5-6.5-8.5z"),
        ("stroke", K, ("M12 8v5M12 15.6v.4", 1.6)),
    ]),
}


def element(kind, color, data):
    # Mask luminance depends on colour space; opacity does not. A shade is a
    # cut (black) with white at SHADE on top, so it is exactly SHADE alpha.
    if color == G:
        opacity = "stroke-opacity" if kind == "stroke" else "fill-opacity"
        return element(kind, K, data) + element(kind, W, data).replace("/>", f" {opacity}='{SHADE}'/>")
    if kind == "path":
        return f"<path fill='{color}' d='{data}'/>"
    if kind == "circle":
        cx, cy, r = data
        return f"<circle fill='{color}' cx='{cx}' cy='{cy}' r='{r}'/>"
    if kind == "ellipse":
        cx, cy, rx, ry = data
        return f"<ellipse fill='{color}' cx='{cx}' cy='{cy}' rx='{rx}' ry='{ry}'/>"
    d, width = data
    return f"<path fill='none' stroke='{color}' stroke-width='{width}' stroke-linecap='round' d='{d}'/>"


def svg(name):
    view, parts = GLYPHS[name]
    body = "".join(element(*part) for part in parts)
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='{view}'>"
            f"<mask id='m'>{body}</mask><rect x='-4' y='-4' width='32' height='32' mask='url(#m)'/></svg>")


def data_uri(name):
    return "data:image/svg+xml," + quote(svg(name), safe=" /=:'-.,()")


def css():
    return "\n".join(f'.g-{name} {{ --glyph: url("{data_uri(name)}"); }}' for name in GLYPHS)


if __name__ == "__main__":
    print(css())
