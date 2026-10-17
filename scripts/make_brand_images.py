"""Laver favicon, app-ikoner og det generiske delingsbillede (Open Graph) til app/static.

Køres kun, når udseendet ændres:  .venv\\Scripts\\python.exe scripts\\make_brand_images.py
Billederne er generiske og indeholder aldrig brugerbilleder (specifikation 12.5).
"""
import os

from PIL import Image, ImageDraw, ImageFont

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static")
ACCENT = (168, 50, 90)
DARK = (43, 34, 48)
LIGHT = (250, 247, 245)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def icon(size):
    """Et "S" i en rund, farvet flade."""
    scale = 4  # tegnes større og skaleres ned for bløde kanter
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 4, fill=ACCENT)
    draw.text((big / 2, big / 2 + big * 0.02), "S", font=font(int(big * 0.68)), fill=LIGHT, anchor="mm")
    return image.resize((size, size), Image.LANCZOS)


def og_image():
    width, height = 1200, 630
    image = Image.new("RGB", (width, height), DARK)
    draw = ImageDraw.Draw(image)
    for x in range(width):  # vandret overgang fra accent til mørk
        t = x / (width - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(ACCENT, DARK))
        draw.line([(x, 0), (x, height)], fill=color)
    draw.text((100, 250), "Sukker", font=font(132), fill=LIGHT, anchor="ls")
    sukker_width = draw.textlength("Sukker", font=font(132))
    draw.text((100 + sukker_width, 250), ".nu", font=font(132), fill=(240, 160, 190), anchor="ls")
    draw.text((104, 340), "Moderne og diskret dating for voksne", font=font(48), fill=LIGHT, anchor="ls")
    draw.text((104, 420), "Opret en gratis profil på få minutter", font=font(36), fill=(225, 210, 220), anchor="ls")
    return image


def main():
    os.makedirs(os.path.join(STATIC, "img"), exist_ok=True)
    icon(180).save(os.path.join(STATIC, "img", "icon-180.png"))
    icon(512).save(os.path.join(STATIC, "img", "icon-512.png"))
    icon(64).save(os.path.join(STATIC, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)])
    og_image().save(os.path.join(STATIC, "img", "og.jpg"), quality=86, optimize=True, progressive=True)
    print("Skrev favicon.ico, img/icon-180.png, img/icon-512.png og img/og.jpg i", STATIC)


if __name__ == "__main__":
    main()
