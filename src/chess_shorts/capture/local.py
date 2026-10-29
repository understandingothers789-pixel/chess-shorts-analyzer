from __future__ import annotations

import bisect
import json
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

from ..control import CaptureControl, Cancelled
from ..session import Session, validate_interval


def executable(name):
    # Packaged FFmpeg can be placed beside the executable; PATH remains supported.
    import sys
    folder = Path(sys.executable).parent
    candidate = folder / (name + (".exe" if sys.platform == "win32" else ""))
    found = str(candidate) if candidate.is_file() else shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} was not found. Install FFmpeg (including ffprobe) and add its bin folder to PATH.")
    return found


def run_json(args, control=None):
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding='utf-8', errors='replace',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        try:
            while True:
                try:
                    output, errors = process.communicate(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    if control:
                        control.checkpoint()
        except BaseException:
            process.kill()
            process.communicate()
            raise
        if process.returncode:
            raise RuntimeError('Cannot read video. ' + errors.strip()[-1200:])
    return json.loads(output)


def probe(path: Path, control=None):
    if not path.is_file():
        raise ValueError("Choose an existing local video file.")
    info = run_json([executable("ffprobe"), "-v", "error", "-select_streams", "v:0",
                     "-show_streams", "-show_format", "-of", "json", str(path.resolve())], control)
    if not info.get("streams"):
        raise ValueError("The file has no readable video stream.")
    stream = info["streams"][0]
    duration = float(info.get("format", {}).get("duration", stream.get("duration", 0)))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Video duration is unavailable; this file cannot be sampled safely.")
    return {"duration": duration, "width": stream["width"], "height": stream["height"],
            "title": info.get("format", {}).get("tags", {}).get("title")}


def frame_times(path: Path, control=None):
    info = run_json([executable("ffprobe"), "-v", "error", "-select_streams", "v:0",
                     "-show_frames", "-show_entries", "frame=best_effort_timestamp_time",
                     "-of", "json", str(path.resolve())], control)
    times = []
    for index, frame in enumerate(info.get("frames", [])):
        if "best_effort_timestamp_time" not in frame:
            raise ValueError("Video contains frames without usable presentation timestamps.")
        stamp = float(frame["best_effort_timestamp_time"])
        if not math.isfinite(stamp):
            raise ValueError("Invalid presentation timestamp.")
        times.append((index, stamp))
    if not times:
        raise ValueError("No decodable frames were found.")
    origin = times[0][1]
    result = [(i, max(0.0, t - origin)) for i, t in times]
    if any(b[1] < a[1] for a, b in zip(result, result[1:])):
        raise ValueError("Video timestamps are not chronological.")
    return result


def sampling_plan(times, interval, start, end):
    validate_interval(interval)
    if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
        raise ValueError("End must be later than start; timestamps must be finite and nonnegative.")
    stamps = [t for _, t in times]
    selected = []
    previous = -1
    for n in range(math.ceil((end - start) / interval)):
        target = start + n * interval
        if target >= end - 1e-9:
            break
        # First real source frame at/after requested time; never synthesize duplicates.
        at = bisect.bisect_left(stamps, target - 1e-7)
        if at >= len(times) or stamps[at] >= end - 1e-9:
            break
        index, stamp = times[at]
        if index != previous:
            selected.append((index, stamp, target))
            previous = index
    return selected


def extract(path: Path, session: Session, start=0.0, end=None, control=None, progress=None):
    try:
        return _extract(path, session, start, end, control, progress)
    except Cancelled:
        session.finish('stopped')
        return session
    except Exception as exc:
        session.finish('error', exc)
        raise


def _extract(path: Path, session: Session, start=0.0, end=None, control=None, progress=None):
    control = control or CaptureControl()
    progress = progress or (lambda *_: None)
    info = probe(path, control)
    end = info["duration"] if end is None else min(end, info["duration"])
    times = frame_times(path, control)
    plan = sampling_plan(times, session.data["requested_interval_seconds"], start, end)
    if not plan:
        raise ValueError("No frames fall inside the requested range.")
    control.checkpoint()
    session.data.update({"video_title": info["title"], "source_duration_seconds": info["duration"],
                         "range_start_seconds": start, "range_end_seconds": end,
                         "sampling_policy": "first source frame at or after each target; duplicate selections omitted"})
    session.save()
    with tempfile.TemporaryDirectory(prefix=".extract-", dir=session.root) as temp:
        folder = Path(temp)
        expression = "+".join(f"eq(n\\,{i})" for i, _, _ in plan)
        script = folder / "filter.txt"
        script.write_text("select=" + expression, encoding="ascii")
        log = folder / "ffmpeg.log"
        with log.open("w+", encoding="utf-8") as errors:
            process = subprocess.Popen([
                executable("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
                "-threads", "2", "-i", str(path.resolve()), "-map", "0:v:0",
                "-filter_script:v", str(script), "-fps_mode", "vfr", "-threads", "2",
                str(folder / "frame_%06d.png")], stdout=subprocess.DEVNULL, stderr=errors,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                while process.poll() is None:
                    if control.stopped:
                        process.terminate()
                        break
                    time.sleep(0.04)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            errors.seek(0)
            failure = errors.read()[-2000:]
        # Only register fully written images; interrupted captures remain usable.
        paths = sorted(folder.glob("frame_*.png"))
        for index, image_path in enumerate(paths):
            if index >= len(plan):
                break
            try:
                with Image.open(image_path) as im:
                    im.load()
                    size = im.size
            except OSError:
                if control.stopped:
                    continue
                raise
            number = len(session.data["frames"]) + 1
            relative = f"frames/frame_{number:06d}.png"
            shutil.move(str(image_path), session.root / relative)
            session.register(relative, size, media=plan[index][1], requested=plan[index][2])
            progress(number, len(plan))
        if process.returncode and not control.stopped:
            raise RuntimeError("FFmpeg extraction failed: " + failure)
        if not control.stopped and len(paths) != len(plan):
            raise RuntimeError("FFmpeg returned fewer frames than expected.")
    session.finish("stopped" if control.stopped else "complete")
    return session
