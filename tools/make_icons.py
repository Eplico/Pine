#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Generate Evergreen's placeholder icon set from one geometry.

Writes PNG/ICO/SVG assets into branding/evergreen/. The outputs are
committed, so only run this when the icon changes (requires Pillow:
`pip install pillow`).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "branding" / "evergreen"

# Colours.
BG_TOP = (46, 160, 96)      # #2ea060
BG_BOTTOM = (20, 83, 45)    # #14532d
TREE = (240, 251, 244)      # #f0fbf4
TILE_BG = "#14532d"

# Geometry in a unit square (0..1). Three stacked tiers and a trunk.
TIERS = [  # (apex y, base y, base half-width)
    (0.15, 0.42, 0.19),
    (0.29, 0.60, 0.26),
    (0.44, 0.78, 0.33),
]
TRUNK = (0.455, 0.76, 0.545, 0.87)  # x0, y0, x1, y1
CORNER = 0.22  # rounded-square corner radius


def _gradient(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(size - 1, 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
        draw.line([(0, y), (size, y)], fill=color + (255,))
    return img


def _tree(draw: ImageDraw.ImageDraw, size: int, inset: float = 0.0) -> None:
    def px(v: float) -> float:
        return (inset + v * (1 - 2 * inset)) * size

    for apex, base, half in TIERS:
        draw.polygon(
            [(px(0.5), px(apex)), (px(0.5 - half), px(base)), (px(0.5 + half), px(base))],
            fill=TREE + (255,),
        )
    x0, y0, x1, y1 = TRUNK
    draw.rectangle([px(x0), px(y0), px(x1), px(y1)], fill=TREE + (230,))


def render(size: int, background: bool = True) -> Image.Image:
    """Render at 4x and downsample for anti-aliasing."""
    big = size * 4
    if background:
        img = _gradient(big)
        mask = Image.new("L", (big, big), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, big - 1, big - 1], radius=round(CORNER * big), fill=255
        )
        img.putalpha(mask)
        inset = 0.0
    else:
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        inset = 0.12
    _tree(ImageDraw.Draw(img), big, inset)
    return img.resize((size, size), Image.LANCZOS)


def svg_icon(size: int = 512) -> str:
    def p(v: float) -> str:
        return f"{v * size:.1f}"

    tiers = "\n".join(
        f'  <polygon points="{p(0.5)},{p(a)} {p(0.5 - h)},{p(b)} {p(0.5 + h)},{p(b)}" fill="#f0fbf4"/>'
        for a, b, h in TIERS
    )
    x0, y0, x1, y1 = TRUNK
    return f"""<!-- This Source Code Form is subject to the terms of the Mozilla Public
   - License, v. 2.0. If a copy of the MPL was not distributed with this
   - file, You can obtain one at http://mozilla.org/MPL/2.0/. -->
<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2ea060"/>
      <stop offset="1" stop-color="#14532d"/>
    </linearGradient>
  </defs>
  <rect width="{size}" height="{size}" rx="{p(CORNER)}" fill="url(#bg)"/>
{tiers}
  <rect x="{p(x0)}" y="{p(y0)}" width="{p(x1 - x0)}" height="{p(y1 - y0)}" fill="#f0fbf4" fill-opacity="0.9"/>
</svg>
"""


def svg_wordmark(fill: str) -> str:
    return f"""<!-- This Source Code Form is subject to the terms of the Mozilla Public
   - License, v. 2.0. If a copy of the MPL was not distributed with this
   - file, You can obtain one at http://mozilla.org/MPL/2.0/. -->
<svg xmlns="http://www.w3.org/2000/svg" fill="{fill}" viewBox="0 0 372 99">
  <text x="0" y="78" font-family="Segoe UI, system-ui, sans-serif" font-size="84" font-weight="600" letter-spacing="-1">Evergreen</text>
</svg>
"""


VISUAL_ELEMENTS = f"""<!-- This Source Code Form is subject to the terms of the Mozilla Public
   - License, v. 2.0. If a copy of the MPL was not distributed with this file,
   - You can obtain one at http://mozilla.org/MPL/2.0/. -->

<Application xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance'>
  <VisualElements
      ShowNameOnSquare150x150Logo='on'
      Square150x150Logo='browser\\VisualElements\\VisualElements_150.png'
      Square70x70Logo='browser\\VisualElements\\VisualElements_70.png'
      ForegroundText='light'
      BackgroundColor='{TILE_BG}'/>
</Application>
"""


def main() -> None:
    (OUT / "content").mkdir(parents=True, exist_ok=True)
    (OUT / "source").mkdir(parents=True, exist_ok=True)
    for s in (16, 22, 24, 32, 48, 64, 128, 256):
        render(s).save(OUT / f"default{s}.png")
    render(256).save(OUT / "firefox.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    render(64).save(OUT / "firefox64.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    render(142, background=False).save(OUT / "VisualElements_70.png")
    render(300, background=False).save(OUT / "VisualElements_150.png")
    (OUT / "firefox.VisualElementsManifest.xml").write_text(VISUAL_ELEMENTS, encoding="utf-8")
    render(192).save(OUT / "content" / "about-logo.png")
    render(384).save(OUT / "content" / "about-logo@2x.png")
    (OUT / "content" / "about-logo.svg").write_text(svg_icon(), encoding="utf-8")
    (OUT / "content" / "about-wordmark.svg").write_text(svg_wordmark("context-fill"), encoding="utf-8")
    (OUT / "content" / "firefox-wordmark.svg").write_text(
        svg_wordmark("context-fill #14532d"), encoding="utf-8"
    )
    (OUT / "source" / "evergreen.svg").write_text(svg_icon(), encoding="utf-8")
    print(f"Wrote icons to {OUT}")


if __name__ == "__main__":
    main()
