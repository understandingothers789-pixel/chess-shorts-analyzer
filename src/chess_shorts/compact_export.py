from __future__ import annotations

import logging
import math
import tempfile
import zipfile
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from .control import Cancelled, CaptureControl
from .session import Session, atomic_json, contained, utc_now
from .sheets import font, timestamp

MIB = 1024 * 1024
DEFAULT_BATCH_BYTES = 5 * MIB // 2
MIN_FRAME_DIMENSION = 480
MAX_FRAMES_PER_SHEET = 6
SHEET_LABEL_SIZE = 24
SHEET_GAP = 12
SHEET_LABEL_HEIGHT = 36
logger = logging.getLogger(__name__)

UPLOAD_INSTRUCTIONS = """Upload the JPEGs from batch_001 to your ChatGPT conversation in filename
order, then continue with batch_002, batch_003, and so on. Mention the batch
number in each message. Within each contact sheet, read left to right, then
top to bottom. If there are too many files for one message, send smaller
groups in the same order.

Share manifest.json or the relevant entries for exact timestamps and capture
notes. Media timestamps refer to the source video. Elapsed capture timestamps
measure active screen-capture time and do not identify a video position.
Missing or unreadable source frames are listed in the manifest.

The batch budget counts JPEG files on disk. ChatGPT may apply separate limits
on message size and attachment count. If a message is rejected, try fewer files.
Optional ZIPs are for storing or moving batches. Attach the JPEGs directly
for visual analysis.

Suggested prompt
These are consecutive captured samples from a chess video. Follow the frame
numbers and timestamps in the manifest. Describe visible changes in pieces,
square highlights, badges, and animation. Flag any gaps or uncertainty.
"""


@dataclass(frozen=True)
class ExportConfig:
    layout: str = 'frames'
    batch_bytes: int = DEFAULT_BATCH_BYTES
    jpeg_quality: int = 86
    max_frame_dimension: int = 1280
    frames_per_sheet: int = 4
    zip_batches: bool = False
    range_kind: str = 'all'
    start: int | float | None = None
    end: int | float | None = None

    def validate(self):
        if self.layout not in ('frames', 'sheets'):
            raise ValueError('Choose individual JPEG frames or contact sheets.')
        if not isinstance(self.batch_bytes, int) or self.batch_bytes < 1:
            raise ValueError('The batch budget must be a positive whole number of bytes.')
        if not 65 <= self.jpeg_quality <= 95 or not MIN_FRAME_DIMENSION <= self.max_frame_dimension <= 2048:
            raise ValueError('Use JPEG quality 65–95 and a maximum frame dimension of 480–2048 pixels.')
        if not 1 <= self.frames_per_sheet <= MAX_FRAMES_PER_SHEET:
            raise ValueError('Choose 1–6 frames per contact sheet.')
        if self.range_kind in ('all', 'picked'):
            return
        if self.range_kind not in ('time', 'frame'):
            raise ValueError('Choose all frames, a time range, or a frame-number range.')
        if not all(isinstance(bound, (int, float)) and math.isfinite(bound)
                   for bound in (self.start, self.end)) or not 0 <= self.start <= self.end:
            raise ValueError('Enter a start and end with the start at or before the end.')
        if self.range_kind == 'frame' and not all(
            isinstance(bound, int) and bound >= 1 for bound in (self.start, self.end)
        ):
            raise ValueError('Frame numbers must be positive whole numbers.')


@dataclass(frozen=True)
class ExportResult:
    folder: Path
    batches: list[dict]
    exported_frames: int
    skipped_frames: list[dict]


def time_field(session: Session):
    if session.data['method'] == 'window':
        return 'elapsed_capture_seconds'
    return 'video_timestamp_seconds'


def _selected_frames(session, config, field):
    frames = session.data['frames']
    if config.range_kind == 'picked':
        picked = set(session.data.get("picked_frames", []))
        frames = [frame for frame in frames if frame["number"] in picked]
    elif config.range_kind != 'all':
        range_field = field if config.range_kind == 'time' else 'number'
        frames = [frame for frame in frames if (value := frame.get(range_field)) is not None
                  and config.start <= value <= config.end]
    # Unknown timestamps follow the timed frames in capture order.
    return sorted(frames, key=lambda frame: (frame.get(field) is None, frame.get(field), frame['number']))


def _jpeg(image, quality):
    with BytesIO() as encoded:
        # Keep color detail around small pieces, badges and square highlights.
        image.save(encoded, 'JPEG', quality=quality, subsampling=0)
        return encoded.getvalue()


def _frame_size(image, limit):
    width, height = image.size
    scale = min(1, limit / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _render(items, dimension, field, sheet):
    if not sheet:
        image = items[0][1]
        return image.resize(_frame_size(image, dimension), Image.Resampling.LANCZOS)

    sizes = [_frame_size(image, dimension) for _, image in items]
    labels = []
    for frame, _ in items:
        seconds = frame[field]
        basis = 'elapsed' if field == 'elapsed_capture_seconds' else 'media'
        labels.append(f'#{frame["number"]:06d}  {timestamp(seconds)} {basis}')
    label_font = font(SHEET_LABEL_SIZE)
    cell_width = max(max(width for width, _ in sizes),
                     math.ceil(max(label_font.getlength(label) for label in labels)) + 12)
    image_height = max(height for _, height in sizes)
    cell_height = image_height + SHEET_LABEL_HEIGHT
    columns = min(2, len(items))
    rows = math.ceil(len(items) / columns)
    canvas = Image.new('RGB', (columns * (cell_width + SHEET_GAP) + SHEET_GAP,
                               rows * (cell_height + SHEET_GAP) + SHEET_GAP), '#11151e')
    draw = ImageDraw.Draw(canvas)
    for index, ((_, image), size, label) in enumerate(zip(items, sizes, labels)):
        x = SHEET_GAP + (index % columns) * (cell_width + SHEET_GAP)
        y = SHEET_GAP + (index // columns) * (cell_height + SHEET_GAP)
        with image.resize(size, Image.Resampling.LANCZOS) as resized:
            canvas.paste(resized, (x + (cell_width - resized.width) // 2, y))
        draw.text((x + 6, y + image_height + 5), label, font=label_font, fill='#f4f6fa')
    return canvas


def _fit_single(items, config, field, control):
    dimension = min(config.max_frame_dimension, max(items[0][1].size))
    minimum_dimension = min(dimension, MIN_FRAME_DIMENSION)
    minimum_quality = min(config.jpeg_quality, 70)
    qualities = sorted({config.jpeg_quality, minimum_quality,
                        *range(config.jpeg_quality - 5, minimum_quality, -5)}, reverse=True)
    while True:
        control.checkpoint()
        with _render(items, dimension, field, config.layout == 'sheets') as image:
            for quality in qualities:
                control.checkpoint()
                payload = _jpeg(image, quality)
                if len(payload) <= config.batch_bytes:
                    return payload, image.size, quality
        if dimension == minimum_dimension:
            frame_number = items[0][0]['number']
            raise ValueError(
                f'Frame #{frame_number:06d} cannot fit in {config.batch_bytes:,} bytes at '
                'the minimum export detail. Increase the batch budget to keep this frame readable.'
            )
        dimension = max(minimum_dimension, int(dimension * .8))


def _encode(items, config, field, control):
    if not items:
        return
    if len(items) == 1:
        yield items, _fit_single(items, config, field, control)
        return

    control.checkpoint()
    with _render(items, config.max_frame_dimension, field, sheet=True) as image:
        payload = _jpeg(image, config.jpeg_quality)
        size = image.size
    if len(payload) <= config.batch_bytes:
        yield items, (payload, size, config.jpeg_quality)
        return

    # Use smaller groups before reducing tile resolution or JPEG quality.
    del payload
    midpoint = (len(items) + 1) // 2
    yield from _encode(items[:midpoint], config, field, control)
    yield from _encode(items[midpoint:], config, field, control)


def _load_copy(session, frame, dimension, control):
    with session.open_frame(frame, control, dimension) as original:
        original.thumbnail((dimension, dimension), Image.Resampling.LANCZOS)
        return original.convert('RGB')


def _frame_entry(frame):
    return {
        'number': frame['number'],
        'source_file': frame['file'],
        'source_frame_index': frame.get('source_frame'),
        'video_timestamp_seconds': frame.get('video_timestamp_seconds'),
        'elapsed_capture_seconds': frame.get('elapsed_capture_seconds'),
        'requested_timestamp_seconds': frame.get('requested_timestamp_seconds'),
        'captured_at': frame.get('captured_at'),
    }


def _write_export(staging, session, frames, config, field, control, progress):
    batches, entries, skipped = [], [], []
    image_count = 0
    group_size = config.frames_per_sheet if config.layout == 'sheets' else 1
    for offset in range(0, len(frames), group_size):
        with ExitStack() as copies:
            items = []
            for frame in frames[offset:offset + group_size]:
                control.checkpoint()
                entry = _frame_entry(frame)
                entries.append(entry)
                try:
                    image = _load_copy(session, frame, config.max_frame_dimension, control)
                except FileNotFoundError:
                    entry['skip_reason'] = 'Original video is missing' if frame['file'] is None else 'Source frame file is missing'
                    skipped.append(entry)
                    continue
                except (OSError, ValueError, Image.DecompressionBombError) as error:
                    entry['skip_reason'] = str(error) if frame['file'] is None else 'Source frame file could not be read'
                    skipped.append(entry)
                    continue
                items.append((entry, copies.enter_context(image)))

            for image_items, (payload, (width, height), quality) in _encode(items, config, field, control):
                if not batches or batches[-1]['image_bytes'] + len(payload) > config.batch_bytes:
                    name = f'batch_{len(batches) + 1:03d}'
                    (staging / name).mkdir()
                    batches.append({'folder': name, 'file_count': 0, 'image_bytes': 0, 'images': []})
                batch = batches[-1]
                image_count += 1
                if config.layout == 'sheets':
                    filename = f'sheet_{image_count:06d}.jpg'
                else:
                    filename = f'{image_count:06d}_frame_{image_items[0][0]["number"]:06d}.jpg'
                relative = f'{batch["folder"]}/{filename}'
                (staging / relative).write_bytes(payload)
                batch['image_bytes'] += len(payload)
                batch['file_count'] += 1
                batch['images'].append({
                    'file': relative, 'bytes': len(payload), 'width': width, 'height': height,
                    'jpeg_quality': quality, 'frame_numbers': [entry['number'] for entry, _ in image_items],
                })
                for position, (entry, _) in enumerate(image_items, 1):
                    entry.update(export_file=relative, position_in_image=position)
        if progress:
            progress(min(offset + group_size, len(frames)), len(frames))

    if config.zip_batches and batches:
        (staging / 'archives').mkdir()
        for batch in batches:
            batch['zip_file'] = f'archives/{batch["folder"]}.zip'
            with zipfile.ZipFile(staging / batch['zip_file'], 'w') as archive:
                for image in batch['images']:
                    control.checkpoint()
                    path = staging / image['file']
                    archive.write(path, path.name)

    session_fields = (
        'name', 'method', 'source', 'source_url', 'video_title', 'created_at', 'status',
        'requested_interval_seconds', 'capture_backend', 'sampling_policy', 'timing_note',
        'timestamp_precision', 'range_start_seconds', 'range_end_seconds', 'interruptions', 'errors',
    )
    atomic_json(staging / 'manifest.json', {
        'schema_version': 1, 'exported_at': utc_now(),
        'source_session': {key: session.data[key] for key in session_fields if key in session.data}
        | {'folder': session.root.name, 'timing_basis': 'elapsed_capture' if field == 'elapsed_capture_seconds' else 'media'},
        'settings': asdict(config),
        'time_range_field': field,
        'timing_notes': 'Elapsed capture time measures active screen capture and is not video time. '
                        'Unknown timestamps are null. Time ranges include both endpoints and only select known times.',
        'frame_order': 'Chronological by the session time field. Ties and unknown times use capture order, with unknown times last.',
        'selected_frame_count': len(entries), 'exported_frame_count': len(entries) - len(skipped),
        'skipped_frame_numbers': [entry['number'] for entry in skipped],
        'frames': entries, 'batches': batches,
    })
    (staging / 'UPLOAD_INSTRUCTIONS.txt').write_text(UPLOAD_INSTRUCTIONS, encoding='utf-8')
    return batches, skipped


def export_compact(session: Session, config: ExportConfig | None = None, control=None, progress=None):
    config = config or ExportConfig()
    control = control or CaptureControl()
    logger.info('ChatGPT export started for %s (%s, %d image bytes per batch)',
                session.root, config.layout, config.batch_bytes)
    try:
        config.validate()
        control.checkpoint()
        field = time_field(session)
        frames = _selected_frames(session, config, field)
        if not frames:
            raise ValueError('No captured frames match this selection. Change the range or choose all captured frames.')
        exports = contained(session.root, 'chatgpt_exports')
        exports.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.partial-', dir=exports) as temp:
            staging = Path(temp) / 'export'
            staging.mkdir()
            batches, skipped = _write_export(staging, session, frames, config, field, control, progress)
            control.checkpoint()
            suffix = Path(temp).name.removeprefix('.partial-')
            folder = exports / f'export_{datetime.now():%Y%m%d_%H%M%S}_{suffix}'
            staging.rename(folder)
    except Cancelled:
        logger.info('ChatGPT export cancelled for %s', session.root)
        raise
    except Exception:
        logger.exception('ChatGPT export failed for %s', session.root)
        raise

    result = ExportResult(folder, batches, len(frames) - len(skipped), skipped)
    logger.info('ChatGPT export completed: %s (%d frames, %d batches, %d skipped)',
                folder, result.exported_frames, len(batches), len(skipped))
    return result
