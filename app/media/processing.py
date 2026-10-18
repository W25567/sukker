"""Validering og omkodning af uploadede billeder (specifikation 4.5).

- Filtypen afgøres ud fra filens faktiske indhold, ikke filnavn eller angivet type.
- Billedet dekodes med en grænse for antal pixels, så "dekompressionsbomber" afvises.
- Alt omkodes til WebP med højst 2048 pixels på den længste led. Metadata som EXIF og GPS fjernes,
  fordi kun pixeldata kopieres over i det nye billede.
"""
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

MAX_DIMENSION = 2048
THUMB_DIMENSION = 480
MAX_PIXELS = 50_000_000
MAX_FRAMES = 300
ACCEPT = "image/jpeg,image/png,image/webp,image/gif,image/avif"
ANIMATABLE = {"GIF", "WEBP", "PNG", "AVIF"}  # formater, der kan indeholde en animation

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ImageRejected(ValueError):
    """Billedet kan ikke bruges. Beskeden kan vises direkte for brugeren."""


@dataclass
class ProcessedImage:
    full: bytes
    thumb: bytes
    width: int
    height: int
    animated: bool


def sniff_format(data):
    """Pillow-formatnavnet ud fra filens første bytes, eller None."""
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "AVIF"
    return None


def _open(data, fmt):
    try:
        probe = Image.open(io.BytesIO(data), formats=[fmt])
        probe.verify()
        image = Image.open(io.BytesIO(data), formats=[fmt])
        if image.width * image.height > MAX_PIXELS:
            raise ImageRejected("Billedet har for mange pixels.")
        image.load()
        return image
    except ImageRejected:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError, EOFError):
        raise ImageRejected("Filen kunne ikke læses som et billede.")


def _target_size(width, height, limit):
    scale = min(1.0, limit / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _clean_still(frame):
    """Kopierer kun pixeldata over i et nyt billede, så ingen metadata følger med."""
    has_alpha = frame.mode in ("RGBA", "LA", "PA") or "transparency" in frame.info
    converted = frame.convert("RGBA" if has_alpha else "RGB")
    clean = Image.new(converted.mode, converted.size)
    clean.paste(converted)
    return clean


def _webp(image, quality, **extra):
    out = io.BytesIO()
    image.save(out, format="WEBP", quality=quality, method=4, **extra)
    return out.getvalue()


def rotate(full_data, clockwise):
    """Drejer et gemt stillbillede 90 grader og laver billedet og miniaturen igen. Returnerer ProcessedImage."""
    image = Image.open(io.BytesIO(full_data), formats=["WEBP"])
    if getattr(image, "n_frames", 1) > 1:
        raise ImageRejected("Animerede billeder kan ikke drejes.")
    image.load()
    turned = image.transpose(Image.Transpose.ROTATE_270 if clockwise else Image.Transpose.ROTATE_90)
    thumb = turned.copy()
    thumb.thumbnail((THUMB_DIMENSION, THUMB_DIMENSION), Image.Resampling.LANCZOS)
    return ProcessedImage(full=_webp(turned, 90), thumb=_webp(thumb, 80), width=turned.width, height=turned.height,
                          animated=False)


def process_upload(data, *, max_still_bytes, max_animated_bytes, animated_refusal=None):
    """Validerer og omkoder et billede. Er `animated_refusal` sat, afvises animationer med den besked."""
    fmt = sniff_format(data)
    if fmt is None:
        raise ImageRejected("Filtypen understøttes ikke. Brug JPEG, PNG, WebP, GIF eller AVIF.")
    if len(data) > max(max_still_bytes, max_animated_bytes):
        raise ImageRejected("Filen er for stor.")

    image = _open(data, fmt)
    # JPEG kan ikke være animeret. Billeder fra iPhone er ofte MPO: en JPEG med et ekstra, indlejret billede
    # (dybde eller HDR), som Pillow tæller som en ekstra frame. Kun det første billede bruges.
    animated = fmt in ANIMATABLE and getattr(image, "n_frames", 1) > 1

    if animated:
        if animated_refusal:
            raise ImageRejected(animated_refusal)
        if len(data) > max_animated_bytes:
            raise ImageRejected(f"Animerede billeder må højst fylde {max_animated_bytes // (1024 * 1024)} MB.")
        if image.n_frames > MAX_FRAMES:
            raise ImageRejected("Animationen har for mange billeder.")
        size = _target_size(image.width, image.height, MAX_DIMENSION)
        frames, durations = [], []
        for frame in ImageSequence.Iterator(image):
            frames.append(_clean_still(frame.convert("RGBA")).resize(size, Image.Resampling.LANCZOS))
            durations.append(frame.info.get("duration", 100) or 100)
        full = _webp(frames[0], 80, save_all=True, append_images=frames[1:], duration=durations, loop=0)
        first = frames[0]
    else:
        if len(data) > max_still_bytes:
            raise ImageRejected(f"Billedet må højst fylde {max_still_bytes // (1024 * 1024)} MB.")
        first = _clean_still(ImageOps.exif_transpose(image))
        first.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
        full = _webp(first, 85)

    thumb_image = first.copy()
    thumb_image.thumbnail((THUMB_DIMENSION, THUMB_DIMENSION), Image.Resampling.LANCZOS)
    return ProcessedImage(
        full=full, thumb=_webp(thumb_image, 80), width=first.width, height=first.height, animated=animated
    )
