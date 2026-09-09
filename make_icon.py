"""Generate the app icons.  Run once:  python make_icons.py

Writes into templates/assets/icons/, which build_site.py copies to the
site. Flat shapes in the site palette — legible at 60px on a home screen.
"""

from pathlib import Path

from PIL import Image, ImageDraw

PAPER = (237, 238, 233)
INK = (28, 35, 33)
WINE = (126, 43, 60)
OUT = Path("templates/assets/icons")


def icon(size: int, maskable: bool = False) -> Image.Image:
    img = Image.new("RGBA", (size, size), PAPER)
    d = ImageDraw.Draw(img)
    s = size / 100.0
    pad = 18 if maskable else 0  # Android crops maskable icons to a circle

    def x(v):
        return (v * (100 - 2 * pad) / 100 + pad) * s

    def y(v):
        return (v * (100 - 2 * pad) / 100 + pad) * s

    d.pieslice([x(24), y(14), x(76), y(66)], start=0, end=180, fill=WINE)
    d.rectangle([x(24), y(14), x(76), y(40)], fill=PAPER)
    d.pieslice([x(28), y(30), x(72), y(62)], start=0, end=180, fill=WINE)
    d.rectangle([x(47), y(60), x(53), y(82)], fill=INK)
    d.rounded_rectangle([x(33), y(82), x(67), y(88)], radius=int(3 * s), fill=INK)
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (180, 192, 512):  # 180 = apple-touch-icon
        icon(size).save(OUT / f"icon-{size}.png")
    icon(512, maskable=True).save(OUT / "icon-maskable-512.png")
    icon(32).save(OUT / "favicon.ico", sizes=[(32, 32), (16, 16)])
    print("wrote icons to", OUT)
