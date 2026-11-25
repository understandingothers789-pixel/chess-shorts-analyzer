from __future__ import annotations

import json
import hashlib
import time
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, data):
    fd, temp = tempfile.mkstemp(prefix=".metadata-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def validate_interval(value: float):
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0.01:
        raise ValueError("Interval must be at least 0.01 seconds.")


def contained(root: Path, relative: str) -> Path:
    if not isinstance(relative, str):
        raise ValueError('Invalid session file path.')
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Session contains an unsafe file path.")
    return target


class Session:
    def __init__(self, root: Path, data: dict):
        self.root = root.resolve()
        self.data = data
        self._last_saved = 0.0
        self._last_pixel_hash = None

    @classmethod
    def create(cls, library: Path, method: str, interval: float, source: str, title=None,
               parent: str | None = None, *, storage="png"):
        validate_interval(interval)
        if storage not in ("png", "jpeg", "source") or (storage == "source" and method != "local"):
            raise ValueError("Invalid storage option for this capture source.")
        library.mkdir(parents=True, exist_ok=True)
        root = library / (datetime.now().strftime("ChessShort_%Y-%m-%d_%H%M%S_") + uuid4().hex[:6])
        root.mkdir()
        for folder in ("frames", "contact_sheets", "details"):
            (root / folder).mkdir()
        obj = cls(root, {
            "schema_version": 1, "name": root.name, "created_at": utc_now(),
            "method": method, "source": source, "source_url": source if method == "youtube" else None,
            "video_title": title, "parent_session": parent,
            "requested_interval_seconds": interval, "status": "capturing",
            "timing_basis": "elapsed_capture" if method == "window" else "media",
            "frames": [], "screenshot_count": 0, "contact_sheets": [],
            "storage": storage, "picked_frames": [],
            "sheet_configuration": None, "errors": [], "interruptions": [],
        })
        obj.save()
        return obj

    @classmethod
    def load(cls, root: Path):
        root = root.resolve()
        with (root / "metadata.json").open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("frames"), list):
            raise ValueError("Unsupported session metadata.")
        if not isinstance(data.get('name'), str) or data.get('method') not in ('local', 'window', 'youtube'):
            raise ValueError('Invalid session name or capture method.')
        if data.get('status') not in ('capturing', 'stopped', 'complete', 'interrupted', 'error'):
            raise ValueError('Invalid session status.')
        validate_interval(data.get('requested_interval_seconds', 0))
        for index, frame in enumerate(data["frames"]):
            if not isinstance(frame, dict) or 'file' not in frame:
                raise ValueError('Invalid frame metadata.')
            if frame["file"] is None:
                if data.get("storage") != "source" or type(frame.get("source_frame")) is not int or frame["source_frame"] < 0:
                    raise ValueError("Invalid indexed video frame.")
                if not isinstance(frame.get("source_pts"), (float, int)) or not math.isfinite(frame["source_pts"]):
                    raise ValueError("Invalid source presentation timestamp.")
            else:
                contained(root, frame["file"])
            if frame.get('number') != index + 1 or not all(isinstance(frame.get(k), int) and frame[k] > 0 for k in ('width', 'height')):
                raise ValueError('Invalid frame number or dimensions.')
            for key in ("video_timestamp_seconds", "elapsed_capture_seconds"):
                v = frame.get(key)
                if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0):
                    raise ValueError("Invalid frame timestamp.")
        picked = data.get("picked_frames", [])
        if not isinstance(picked, list) or any(type(i) is not int or not 1 <= i <= len(data["frames"]) for i in picked):
            raise ValueError("Invalid picked frame numbers.")
        if data.get("storage") == "source" and data["frames"]:
            signature = data.get("source_signature")
            if (data["method"] != "local" or not isinstance(data.get("source"), str)
                    or not isinstance(signature, list) or len(signature) != 2
                    or any(type(v) is not int for v in signature)):
                raise ValueError("Invalid indexed video source.")
        if not isinstance(data.get('contact_sheets'), list):
            raise ValueError('Invalid contact sheet metadata.')
        if not isinstance(data.get('errors'), list) or not isinstance(data.get('interruptions'), list):
            raise ValueError('Invalid session history.')
        for sheet in data['contact_sheets']:
            contained(root, sheet)
        return cls(root, data)

    def save(self):
        self.data["screenshot_count"] = len(self.data["frames"])
        atomic_json(self.root / "metadata.json", self.data)
        self._last_saved = time.monotonic()

    def record(self, image: Image.Image, *, elapsed=None, media=None, requested=None,
               captured_at=None):
        if shutil.disk_usage(self.root).free < image.width * image.height * 4 + 8_000_000:
            raise OSError("Insufficient disk space. Free space before continuing.")
        number = len(self.data["frames"]) + 1
        compact = self.data.get("storage") == "jpeg"
        suffix = "jpg" if compact else "png"
        relative = f"frames/frame_{number:06d}.{suffix}"
        pixels = image.convert("RGB") if compact else image
        try:
            if compact:
                digest = hashlib.sha256(pixels.tobytes()).digest()
                if self._last_pixel_hash and self._last_pixel_hash[:2] == (digest, image.size):
                    self.register(self._last_pixel_hash[2], image.size, elapsed=elapsed,
                                  media=media, requested=requested, captured_at=captured_at,
                                  checkpoint=True)
                    return
            path = self.root / relative
            temp = path.with_suffix(".tmp")
            try:
                if compact:
                    pixels.save(temp, format="JPEG", quality=89, subsampling=0)
                else:
                    image.save(temp, format="PNG")
                os.replace(temp, path)
                if compact:
                    self._last_pixel_hash = (digest, image.size, relative)
            finally:
                temp.unlink(missing_ok=True)
            self.register(relative, image.size, elapsed=elapsed, media=media,
                          requested=requested, captured_at=captured_at, checkpoint=compact)
        finally:
            if pixels is not image:
                pixels.close()

    def register(self, relative, size, *, elapsed=None, media=None, requested=None, captured_at=None,
                 checkpoint=False, **source):
        self.data["frames"].append({
            "number": len(self.data["frames"]) + 1, "file": relative,
            "width": size[0], "height": size[1],
            "video_timestamp_seconds": media, "requested_timestamp_seconds": requested,
            "elapsed_capture_seconds": elapsed, "captured_at": captured_at or utc_now(),
            **source,
        })
        self.data["screenshot_count"] = len(self.data["frames"])
        if not checkpoint or time.monotonic() - self._last_saved >= 0.5:
            self.save()

    def open_frame(self, frame, control=None, max_side=None):
        """Return a Pillow image owned by the caller, from disk or the local video."""
        if frame["file"] is None:
            from .capture.local import read_indexed_frame
            return read_indexed_frame(self, frame, control, max_side)
        return Image.open(contained(self.root, frame["file"]))

    def finish(self, status="complete", error=None):
        self.data["status"] = status
        self.data["finished_at"] = utc_now()
        if error:
            self.data["errors"].append({"at": utc_now(), "message": str(error)})
        self.save()

    def rename(self, name):
        name = name.strip()
        if not name or len(name) > 120:
            raise ValueError("Session name must contain 1–120 characters.")
        self.data["name"] = name
        self.save()


def list_sessions(library: Path):
    # Include detailed captures as independently reopenable sessions.
    return sorted(library.glob("**/metadata.json"), key=lambda p: p.stat().st_mtime, reverse=True)
