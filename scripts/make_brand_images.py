"""Laver de afledte logofiler og det generiske delingsbillede (Open Graph) i app/static/images.

Kilden er logoerne i app/static/images/full/. Favicons og app-ikoner kommer fra RealFaviconGenerator
og ligger direkte i app/static/images. Kør scriptet igen, når logoet ændres:

    .venv\\Scripts\\python.exe scripts\\make_brand_images.py

Billederne er generiske og indeholder aldrig brugerbilleder (specifikation 12.5).
"""
import os

from PIL import Image, ImageDraw, ImageFont

IMAGES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "images")
SOURCE_LOGO = os.path.join(IMAGES, "full", "logo.png")  # hjertet alene
SOURCE_FULL = os.path.join(IMAGES, "full", "full-logo.png")  # hjerte og navn

BACKGROUND = (250, 247, 245)  # --bg i app.css
TEXT = (43, 34, 48)  # --text
MUTED = (111, 100, 117)  # --muted
DARK_TEXT = (241, 236, 243)  # --text i mørk tilstand

FONT_CANDIDATES = [r"C:\Windows\Fonts\segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def resized(image, height):
    width = round(image.width * height / image.height)
    return image.resize((width, height), Image.LANCZOS)


def for_dark_mode(image):
    """Gør den mørkebrune skrift lys, så logoet kan ses på mørk baggrund. Hjertets farver bevares."""
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            if a and max(r, g, b) < 90 and max(r, g, b) - min(r, g, b) < 45:
                pixels[x, y] = (*DARK_TEXT, a)
    return image


def og_image(full_logo):
    width, height = 1200, 630
    image = Image.new("RGB", (width, height), BACKGROUND)
    logo = resized(full_logo, 400)
    image.paste(logo, ((width - logo.width) // 2, 50), logo)
    draw = ImageDraw.Draw(image)
    draw.text((width / 2, 520), "Moderne og diskret dating for voksne", font=font(44), fill=TEXT, anchor="mm")
    draw.text((width / 2, 575), "Opret en gratis profil på få minutter", font=font(30), fill=MUTED, anchor="mm")
    return image


def main():
    heart = Image.open(SOURCE_LOGO).convert("RGBA")
    full = Image.open(SOURCE_FULL).convert("RGBA")
    # Topbjælken viser hjertet i 32 px højde, forsiden hele logoet i 140 px højde. Dobbelt opløsning til skarpe skærme.
    resized(heart, 64).save(os.path.join(IMAGES, "logo-64.webp"), quality=90, method=6)
    hero = resized(full, 280)
    hero.save(os.path.join(IMAGES, "full-logo-280.webp"), quality=88, method=6)
    for_dark_mode(hero.copy()).save(os.path.join(IMAGES, "full-logo-280-dark.webp"), quality=88, method=6)
    og_image(full).save(os.path.join(IMAGES, "og.jpg"), quality=86, optimize=True, progressive=True)
    print("Skrev logo-64.webp, full-logo-280.webp, full-logo-280-dark.webp og og.jpg i", IMAGES)


if __name__ == "__main__":
    main()
