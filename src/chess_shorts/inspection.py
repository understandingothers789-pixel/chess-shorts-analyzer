from __future__ import annotations

import hashlib
from PIL import Image, ImageChops, ImageEnhance

from .session import contained


def difference(left, right):
    """Absolute RGB pixel difference. Different-size frames are not resampled."""
    with Image.open(left) as a, Image.open(right) as b:
        if a.size != b.size:
            raise ValueError("Pixel difference requires equal image dimensions.")
        return ImageEnhance.Contrast(ImageChops.difference(a.convert('RGB'), b.convert('RGB'))).enhance(2)


def find_duplicates(session, control=None):
    """Exact decoded-pixel duplicates only; originals are never modified."""
    seen, pairs = {}, []
    for f in session.data['frames']:
        if control:
            control.checkpoint()
        with Image.open(contained(session.root, f['file'])) as image:
            hasher = hashlib.sha256(str(image.size).encode())
            # Bound temporary RGB buffers rather than copying/concatenating a
            # full-resolution pixel array. Conversion remains pixel-exact.
            for top in range(0, image.height, 256):
                if control:
                    control.checkpoint()
                with image.crop((0, top, image.width, min(top + 256, image.height))) as strip:
                    with strip.convert('RGB') as rgb:
                        hasher.update(rgb.tobytes())
            digest = hasher.digest()
        if digest in seen:
            pairs.append((f['number'], seen[digest]))
        else:
            seen[digest] = f['number']
    return pairs
