from __future__ import annotations

import math
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .session import Session, contained


def timestamp(seconds):
    if seconds is None:
        return "unknown"
    milliseconds = round(seconds * 1000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, millis = divmod(milliseconds, 1000)
    return f"{minutes:02d}:{secs:02d}.{millis:03d}"


def frame_label(frame):
    media = frame.get("video_timestamp_seconds")
    value = media if media is not None else frame.get("elapsed_capture_seconds")
    return f"#{frame['number']:06d}  {timestamp(value)}" + (" elapsed" if media is None else " media")


@dataclass
class SheetConfig:
    frames_per_sheet: int = 10
    columns: int = 2
    rows: int = 5
    spacing: int = 20
    background: str = "#11151e"
    timestamp_size: int = 24
    format: str = "PNG"
    # 0 keeps original dimensions. Limits split sheets rather than shrinking frames.
    frame_width: int = 0
    max_dimension: int = 16000
    max_pixels: int = 60_000_000

    def validate(self):
        if not (1 <= self.frames_per_sheet <= 100 and 1 <= self.columns <= 20 and 1 <= self.rows <= 100):
            raise ValueError("Use 1–100 frames and rows, and 1–20 columns.")
        if not (0 <= self.spacing <= 200 and 8 <= self.timestamp_size <= 150 and 0 <= self.frame_width <= 16000):
            raise ValueError("Invalid spacing, timestamp size, or frame width.")
        if self.format not in ("PNG", "JPEG"):
            raise ValueError("Choose PNG or JPEG.")
        ImageColor.getrgb(self.background)
        if self.max_dimension < 100 or self.max_pixels < 10000:
            raise ValueError("Sheet limits are too small.")


def font(size):
    for candidate in ("C:/Windows/Fonts/segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def generate(session: Session, config: SheetConfig | None = None, progress=None, control=None):
    config = config or SheetConfig()
    config.validate()
    frames = [f for f in session.data["frames"] if not f.get('excluded_from_sheets')]
    if not frames:
        raise ValueError("Capture frames before generating contact sheets.")
    # Geometry only; never retain the full image collection in memory.
    widths, heights = [], []
    for f in frames:
        w, h = f["width"], f["height"]
        if w <= 0 or h <= 0:
            raise ValueError("Invalid frame dimensions.")
        scale = min(1.0, config.frame_width / w) if config.frame_width else 1.0
        widths.append(max(1, round(w * scale)))
        heights.append(max(1, round(h * scale)))
    text_font = font(config.timestamp_size)
    label_width = max(math.ceil(text_font.getlength(frame_label(f))) + 8 for f in frames)
    cell_w, image_h = max(max(widths), label_width), max(heights)
    label_h = config.timestamp_size + 18
    cell_h = image_h + label_h
    gap = config.spacing
    columns = min(config.columns, config.frames_per_sheet, len(frames))
    while columns > 1 and columns * (cell_w + gap) + gap > config.max_dimension:
        columns -= 1
    width = columns * (cell_w + gap) + gap
    max_rows = min(config.rows, (config.max_dimension - gap) // (cell_h + gap),
                   (config.max_pixels // width - gap) // (cell_h + gap))
    if width > config.max_dimension or max_rows < 1:
        raise ValueError("A single full-resolution frame exceeds sheet limits. Set a smaller output frame width or use individual frames.")
    per_sheet = min(config.frames_per_sheet, columns * max_rows)
    sheets = []
    with tempfile.TemporaryDirectory(prefix=".sheets-", dir=session.root) as temp:
        staging = Path(temp)
        for offset in range(0, len(frames), per_sheet):
            if control:
                control.checkpoint()
            batch = frames[offset:offset + per_sheet]
            rows = math.ceil(len(batch) / columns)
            canvas = Image.new("RGB", (width, rows * (cell_h + gap) + gap), config.background)
            try:
                draw = ImageDraw.Draw(canvas)
                for at, f in enumerate(batch):
                    x, y = gap + (at % columns) * (cell_w + gap), gap + (at // columns) * (cell_h + gap)
                    with Image.open(contained(session.root, f["file"])) as im:
                        im = im.convert("RGB")
                        im.thumbnail((widths[offset + at], heights[offset + at]), Image.Resampling.LANCZOS)
                        canvas.paste(im, (x + (cell_w - im.width) // 2, y + (image_h - im.height) // 2))
                    draw.text((x + 4, y + image_h + 6), frame_label(f), font=text_font, fill="#dce5f4")
                suffix = "png" if config.format == "PNG" else "jpg"
                name = f"sheet_{len(sheets) + 1:03d}.{suffix}"
                canvas.save(staging / name, quality=95)
                sheets.append("contact_sheets/" + name)
            finally:
                canvas.close()
            if progress:
                progress(min(offset + per_sheet, len(frames)), len(frames))
        # Preserve the previous set if generation was interrupted or failed.
        destination = session.root / "contact_sheets"
        backup = session.root / ".previous_sheets"
        if backup.exists():
            raise RuntimeError("A previous sheet replacement was interrupted; recover .previous_sheets before retrying.")
        destination.rename(backup)
        old_sheets = session.data['contact_sheets']
        old_config = session.data['sheet_configuration']
        try:
            destination.mkdir()
            for sheet in sheets:
                shutil.move(staging / Path(sheet).name, session.root / sheet)
            session.data["contact_sheets"] = sheets
            session.data["sheet_configuration"] = asdict(config)
            session.save()
        except Exception:
            shutil.rmtree(destination)
            backup.rename(destination)
            session.data['contact_sheets'] = old_sheets
            session.data['sheet_configuration'] = old_config
            raise
        shutil.rmtree(backup)
    return sheets
