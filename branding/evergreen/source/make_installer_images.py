# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Draw the Windows installer's bitmaps from Evergreen's tree.

    python branding/evergreen/source/make_installer_images.py

Writes wizWatermark.bmp (welcome and finish pages, 164x314) and
wizHeader.bmp / wizHeaderRTL.bmp (the other pages, 150x57) into
branding/evergreen/, and source/installer-stub.ico: the icon `eg.py prepare`
puts into the installer's self-extracting stub (tools/eg/sfxstub.py), in the
stub's own formats (32-bit bitmaps at 16, 32 and 48 px, PNG at 256 px). The
tree is the one in evergreen.svg. Needs Pillow; the generated files are
committed, so builds do not.
"""

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1]
SCALE = 4  # draw at 4x and downsample, for smooth edges

TOP = (0x2E, 0xA0, 0x60)  # evergreen.svg gradient
BOTTOM = (0x14, 0x53, 0x2D)
SNOW = (0xF0, 0xFB, 0xF4)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# The tree from evergreen.svg (512x512 viewBox): three tiers and a trunk.
TIERS = [
    [(256.0, 76.8), (158.7, 215.0), (353.3, 215.0)],
    [(256.0, 148.5), (122.9, 307.2), (389.1, 307.2)],
    [(256.0, 225.3), (87.0, 399.4), (425.0, 399.4)],
]
TRUNK = (233.0, 389.1, 279.1, 445.4)


def gradient(size, top, bottom):
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        row = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
        for x in range(w):
            px[x, y] = row
    return img


def tree(draw, cx, top, height, fill):
    """The evergreen.svg tree, `height` tall (tip to trunk base), centred on cx."""
    s = height / (445.4 - 76.8)

    def pt(x, y):
        return (cx + (x - 256.0) * s, top + (y - 76.8) * s)

    for tier in TIERS:
        draw.polygon([pt(x, y) for x, y in tier], fill=fill)
    x0, y0, x1, y1 = TRUNK
    draw.rectangle([pt(x0, y0), pt(x1, y1)], fill=fill)


def rounded_icon(size):
    """The app icon (rounded green square with the tree), size x size."""
    big = size * SCALE
    img = gradient((big, big), TOP, BOTTOM).convert("RGBA")
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, big - 1, big - 1], radius=big * 0.22, fill=255)
    img.putalpha(mask)
    tree(ImageDraw.Draw(img), big / 2, big * 0.15, big * 0.72, SNOW)
    return img.resize((size, size), Image.LANCZOS)


def watermark():
    w, h = 164, 314
    img = gradient((w * SCALE, h * SCALE), TOP, BOTTOM)
    d = ImageDraw.Draw(img)
    # A row of darker trees along the bottom.
    shade = (0x10, 0x45, 0x25)
    for i, (cx, height) in enumerate([(10, 70), (38, 96), (70, 64), (100, 88), (132, 74), (160, 100)]):
        base = h - 6 + (i % 2) * 4
        tree(d, cx * SCALE, (base - height) * SCALE, height * SCALE, shade)
    d.rectangle([0, (h - 8) * SCALE, w * SCALE, h * SCALE], fill=shade)
    # The tree and the name.
    tree(d, w / 2 * SCALE, 54 * SCALE, 118 * SCALE, SNOW)
    font = ImageFont.truetype(FONT, 21 * SCALE)
    d.text((w / 2 * SCALE, 196 * SCALE), "Evergreen", font=font, fill=SNOW, anchor="mm")
    return img.resize((w, h), Image.LANCZOS)


def header(rtl):
    w, h = 150, 57
    img = Image.new("RGB", (w, h), (255, 255, 255))
    icon = rounded_icon(41)
    x = 8 if rtl else w - 41 - 8
    img.paste(icon, (x, (h - 41) // 2), icon)
    return img


def dib(img):
    """An icon image as a 32-bit DIB (BITMAPINFOHEADER, BGRA rows bottom-up, AND mask)."""
    w, h = img.size
    px = img.convert("RGBA").load()
    xor = b"".join(
        bytes((px[x, y][2], px[x, y][1], px[x, y][0], px[x, y][3])) for y in reversed(range(h)) for x in range(w)
    )
    mask = bytes(((w + 31) // 32) * 4 * h)  # all zero: the alpha channel decides
    header = struct.pack("<IiiHHIIiiII", 40, w, 2 * h, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
    return header + xor + mask


def stub_icon():
    """An .ico with the entries the installer stub has: DIBs at 16/32/48, PNG at 256."""
    images = []
    for size in (16, 32, 48):
        images.append((size, dib(rounded_icon(size))))
    png = io.BytesIO()
    rounded_icon(256).save(png, "PNG", optimize=True)
    images.append((256, png.getvalue()))
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for size, data in images:
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(data for _, data in images)


def main():
    watermark().save(OUT / "wizWatermark.bmp")
    header(rtl=False).save(OUT / "wizHeader.bmp")
    header(rtl=True).save(OUT / "wizHeaderRTL.bmp")
    (OUT / "source" / "installer-stub.ico").write_bytes(stub_icon())
    for name in ("wizWatermark.bmp", "wizHeader.bmp", "wizHeaderRTL.bmp", "source/installer-stub.ico"):
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
