from __future__ import annotations

import csv
import io
import os
import tempfile
import zipfile
from pathlib import Path

from .session import Session, contained

ANALYSIS_INSTRUCTIONS = """Analyze these frames from a chess YouTube Short.

Focus on piece movement, capture animations, motion blur, camera movement, lighting, shadows, visual effects, and transitions.

Identify which effects are directly observable and which interpretations are uncertain.

Explain how the observed effects could be recreated in a real-time chess animation application.

Recommend practical technologies and implementation approaches.

The ultimate goal is to build a Chess Animation Studio that automatically plays cinematic chess animations without expensive video-editing software.

Use metadata.json and timestamp_index.csv to distinguish actual media presentation timestamps from elapsed screen-capture time. Elapsed capture timestamps are not video timestamps. Missing metadata is unknown; do not infer frame-accurate screen timing. Frames may omit changes between samples.
"""


def export_chatgpt(session: Session, destination: Path | None = None, include_frames=False):
    if not session.data["contact_sheets"]:
        raise ValueError("Generate contact sheets before exporting.")
    destination = destination or session.root / "contact_sheets.zip"
    destination = destination.resolve()
    if destination.suffix.lower() != '.zip':
        raise ValueError('Choose a .zip export destination.')
    protected = [session.root / "metadata.json"] + [contained(session.root, p) for p in session.data["contact_sheets"]]
    protected += [contained(session.root, f["file"]) for f in session.data["frames"]]
    if destination in protected:
        raise ValueError("Export destination would overwrite a session source file.")
    index = io.StringIO(newline="")
    writer = csv.writer(index)
    writer.writerow(["frame", "file", "video_timestamp_seconds", "elapsed_capture_seconds", "requested_timestamp_seconds", "captured_at"])
    for f in session.data["frames"]:
        writer.writerow([f["number"], f["file"], f.get("video_timestamp_seconds"),
                         f.get("elapsed_capture_seconds"), f.get("requested_timestamp_seconds"), f.get("captured_at")])
    session.save()
    fd, temp = tempfile.mkstemp(prefix=".export-", suffix=".zip", dir=destination.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(session.root / "metadata.json", "metadata.json")
            archive.writestr("timestamp_index.csv", index.getvalue())
            archive.writestr("analysis_instructions.txt", ANALYSIS_INSTRUCTIONS)
            # PNG/JPEG payloads are already compressed; retain their exact bytes
            # without spending CPU on a second compression pass. Text stays deflated.
            for relative in session.data["contact_sheets"]:
                archive.write(contained(session.root, relative), relative, compress_type=zipfile.ZIP_STORED)
            if include_frames:
                for frame in session.data["frames"]:
                    archive.write(contained(session.root, frame["file"]), frame["file"],
                                  compress_type=zipfile.ZIP_STORED)
        os.replace(temp, destination)
    finally:
        Path(temp).unlink(missing_ok=True)
    return destination
