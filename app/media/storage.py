"""Billedfiler på disk, uden for webserverens offentlige område (specifikation 4.3 og 14.3).

Struktur: <MEDIA_ROOT>/<collection>/<public_id>/{full,thumb}.webp
"""
import os
import shutil
import uuid

from flask import current_app

VARIANTS = {"stor": "full.webp", "lille": "thumb.webp"}


def _image_dir(collection, public_id):
    return os.path.join(current_app.config["MEDIA_ROOT"], collection, public_id)


def file_path(image, variant):
    return os.path.join(_image_dir(image.collection, image.public_id), VARIANTS[variant])


def internal_path(image, variant):
    """Stien til Nginx' interne lokation ved X-Accel-Redirect."""
    return f"{current_app.config['MEDIA_X_ACCEL_PREFIX'].rstrip('/')}/{image.collection}/{image.public_id}/{VARIANTS[variant]}"


def save_files(image, processed):
    """Skriver filerne i en midlertidig mappe og flytter den på plads, så der aldrig ligger halve billeder."""
    target = _image_dir(image.collection, image.public_id)
    staging = f"{target}.{uuid.uuid4().hex}.tmp"
    os.makedirs(staging)
    try:
        with open(os.path.join(staging, VARIANTS["stor"]), "wb") as fh:
            fh.write(processed.full)
        with open(os.path.join(staging, VARIANTS["lille"]), "wb") as fh:
            fh.write(processed.thumb)
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def delete_files(image):
    """Sletter billedet og alle afledte varianter (specifikation 4.5)."""
    shutil.rmtree(_image_dir(image.collection, image.public_id), ignore_errors=True)
