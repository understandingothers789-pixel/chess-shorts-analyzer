from __future__ import annotations

import bisect
import json
import logging
import math
import shutil
import time
from pathlib import Path

from PIL import Image
from importlib.metadata import PackageNotFoundError, version
from PySide6.QtCore import QPoint, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSlider, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from . import __version__
from .capture.local import extract, probe, executable, video_signature
from .capture.window import WindowsCapture, capture_window
from .capture.youtube import capture_youtube, normalize_url
from .compact_export import (
    DEFAULT_BATCH_BYTES, MAX_FRAMES_PER_SHEET, MIB, MIN_FRAME_DIMENSION,
    ExportConfig, export_compact, time_field,
)
from .control import Cancelled, CaptureControl
from .export import export_chatgpt
from .inspection import difference_images, find_duplicates
from .readiness import diagnostics, estimate
from .session import Session, contained, list_sessions, utc_now
from .sheets import SheetConfig, frame_label, generate
from .ui_widgets import CropPreview, ImageView, qimage

STYLE = '''
QMainWindow, QDialog { background:#151923; color:#e6edf6; }
QWidget { color:#e6edf6; background:#151923; font-family:Segoe UI; font-size:12px; }
QLabel, QCheckBox { background:transparent; }
QGroupBox { border:1px solid #30394b; border-radius:8px; margin-top:14px; padding:12px 8px 8px; font-weight:600; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 5px; color:#aebed4; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background:#202737; border:1px solid #39455b; border-radius:5px; padding:6px; selection-background-color:#36775f; }
QPushButton { background:#283347; border:1px solid #3e4b63; border-radius:6px; padding:9px 12px; font-weight:600; }
QPushButton:hover { background:#35445c; } QPushButton:disabled { color:#68758b; background:#1b2230; border-color:#293243; }
QPushButton#primary { background:#81e4b1; color:#122b22; border:0; }
QPushButton#export { background:#6b9de0; color:#101e32; border:0; }
QLabel#muted { color:#9eacc2; } QLabel#heading { font-size:23px; font-weight:700; }
QListWidget { background:#10151e; border:1px solid #30394b; border-radius:6px; padding:4px; }
QListWidget::item { padding:6px; border-radius:4px; } QListWidget::item:selected { background:#30455c; }
QTabWidget::pane { border:1px solid #30394b; border-radius:6px; }
QTabBar::tab { background:#202737; padding:10px 16px; color:#aebbd0; }
QTabBar::tab:selected { background:#304156; color:#e5fff0; }
QScrollArea { border:0; background:transparent; }
QProgressBar { border:0; background:#252e40; border-radius:4px; text-align:center; height:12px; }
QProgressBar::chunk { background:#81e4b1; border-radius:4px; }
QToolTip { color:#fff; background:#283347; border:1px solid #52627a; }
'''

try:
    APP_VERSION = version('chess-shorts-analyzer')
except PackageNotFoundError:
    APP_VERSION = __version__


class Worker(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)
    frame = Signal(object)

    def __init__(self, function, control=None):
        super().__init__()
        self.function = function
        self.control = control or CaptureControl()

    def run(self):
        try:
            self.result.emit(self.function(self.control, self.progress.emit))
        except Cancelled:
            self.result.emit(None)
        except Exception as exc:
            logging.exception('Background task failed')
            self.failed.emit(str(exc))


class ThumbnailWorker(QThread):
    thumbnail = Signal(int, int, object)

    def __init__(self, session, indices, token):
        super().__init__()
        self.session, self.indices, self.token = session, indices, token
        self.attempted = set()

    def checkpoint(self):
        if self.isInterruptionRequested():
            raise Cancelled("Thumbnail request changed")

    def run(self):
        for index in self.indices:
            try:
                self.checkpoint()
                with self.session.open_frame(self.session.data['frames'][index], self, 128) as image:
                    image.thumbnail((104, 48), Image.Resampling.LANCZOS)
                    self.thumbnail.emit(self.token, index, qimage(image))
            except Cancelled:
                break
            except (OSError, ValueError, RuntimeError, IndexError):
                pass
            self.attempted.add(index)


def button(text, action, name=None):
    item = QPushButton(text)
    item.clicked.connect(action)
    if name:
        item.setObjectName(name)
    return item


def number(value, minimum, maximum, decimals=2):
    item = QDoubleSpinBox()
    item.setDecimals(decimals)
    item.setRange(minimum, maximum)
    item.setValue(value)
    item.setSingleStep(.05)
    return item


def integer(value, minimum, maximum):
    item = QSpinBox()
    item.setRange(minimum, maximum)
    item.setValue(value)
    return item


def group(title):
    box = QGroupBox(title)
    layout = QFormLayout(box)
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return box, layout


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'Chess Shorts · Visual Analysis Studio · v{APP_VERSION}')
        self.resize(1500, 960)
        self.setMinimumSize(1080, 720)
        self.setStyleSheet(STYLE)
        self.settings = QSettings('ChessShorts', 'VisualAnalysisStudio')
        self.session = None
        self.job = None
        self.jobs = []
        self.pending_image_task = None
        self.thumbnail_jobs = []
        self.thumb_visible = set()
        self.thumb_loaded = set()
        self.thumb_timer = QTimer(self)
        self.thumb_timer.setSingleShot(True)
        self.thumb_timer.timeout.connect(self.update_thumbnail_window)
        self.pick_anchor = None
        self.time_index = []
        self.preview_job = None
        self.token = 0
        self.preview_token = 0
        self.comparison_token = 0
        self.pending_session = None
        self.busy = False
        self.paused = False
        self.pause_started = None
        self.close_pending = False
        self.closing = False
        self.inspected_video = None
        self.crop_ready = False
        self.crop_selected = False
        self.crop_generation = 0
        self.last_export = None
        self.last_compact_export = None
        self.compact_export_running = False
        self.build()
        self.restore_settings()
        self.refresh_library()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(200)
        if self.method.currentIndex() == 1:
            QTimer.singleShot(0, self.refresh_windows)

    def build(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 18, 20, 16)
        heading = QLabel('♞  CHESS SHORTS')
        heading.setObjectName('heading')
        sub = QLabel('VISUAL ANALYSIS STUDIO   /   Capture · Inspect · Understand')
        sub.setObjectName('muted')
        layout.addWidget(heading)
        layout.addWidget(sub)
        layout.addSpacing(10)
        split = QSplitter()
        layout.addWidget(split, 1)

        left_content = QWidget()
        left = QVBoxLayout(left_content)
        left.setContentsMargins(0, 0, 12, 0)
        source, form = group('01   Capture source')
        self.method = QComboBox()
        self.method.addItems(['Local video', 'Browser window', 'YouTube URL'])
        form.addRow(self.method)
        self.source = QLineEdit()
        self.source.setPlaceholderText('Choose a video file…')
        form.addRow(self.source)
        self.browse_button = button('Choose video…', self.browse_video)
        form.addRow(self.browse_button)
        self.inspect_button = button("Inspect video / estimate", self.inspect_video)
        form.addRow(self.inspect_button)
        self.windows = QComboBox()
        self.windows.setMinimumContentsLength(16)
        self.windows.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.refresh_windows_button = button('Refresh browser windows', self.refresh_windows)
        self.preview_button = button('Refresh capture preview', self.window_preview)
        self.fallback = QCheckBox('Foreground fallback (keep browser in front)')
        self.fallback.setToolTip('Captures only the selected client DC. May fail for GPU or protected content. No desktop capture.')
        for widget in (self.windows, self.refresh_windows_button, self.preview_button, self.fallback):
            form.addRow(widget)
        self.source_hint = QLabel()
        self.source_hint.setWordWrap(True)
        self.source_hint.setObjectName('muted')
        form.addRow(self.source_hint)
        self.browser_fallback_button = button('Use Browser Window Capture', self.offer_window_capture)
        self.browser_fallback_button.setVisible(False)
        form.addRow(self.browser_fallback_button)
        left.addWidget(source)
        self.source_group = source

        capture, form = group('02   Sampling')
        self.mode = QComboBox()
        self.mode.addItems(['Overview capture', 'Detailed animation capture'])
        form.addRow('Mode', self.mode)
        self.presets = QComboBox()
        self.interval_options = [.01, .05, .1, .25, .5, 1, 2]
        self.presets.addItems([f'{value:g} seconds' for value in self.interval_options] + ['Custom'])
        self.presets.setCurrentIndex(4)
        self.interval = number(.5, .01, 3600, 3)
        self.interval.setSuffix(' s')
        self.interval.setSingleStep(.01)
        form.addRow('Interval preset', self.presets)
        form.addRow('Interval', self.interval)
        self.start = number(0, 0, 864000, 3)
        self.end = number(0, 0, 864000, 3)
        self.end.setSpecialValueText('To end / manual stop')
        form.addRow('Start (seconds)', self.start)
        form.addRow('End (seconds)', self.end)
        self.frame_estimate = QLabel('30 seconds ≈ 60 frames')
        self.frame_estimate.setObjectName('muted')
        self.frame_estimate.setWordWrap(True)
        form.addRow(self.frame_estimate)
        left.addWidget(capture)
        self.capture_group = capture

        sheets, form = group('03   Contact sheets')
        self.sheet_preset = QComboBox()
        self.sheet_preset.addItems(['Standard · 10 frames', 'High detail · 4 frames', 'Maximum detail · 1 frame', 'Custom', 'Two frames · side by side'])
        self.per_sheet = integer(10, 1, 100)
        self.columns = integer(2, 1, 20)
        self.rows = integer(5, 1, 100)
        self.spacing = integer(20, 0, 200)
        self.font_size = integer(24, 8, 150)
        self.frame_width = integer(0, 0, 16000)
        self.frame_width.setSpecialValueText('Original pixels')
        self.background = QLineEdit('#11151e')
        self.image_format = QComboBox()
        self.image_format.addItems(['PNG', 'JPEG'])
        for label, widget in [('Layout', self.sheet_preset), ('Frames / sheet', self.per_sheet),
                              ('Columns', self.columns), ('Rows', self.rows), ('Spacing (px)', self.spacing),
                              ('Timestamp size', self.font_size), ('Frame width (px)', self.frame_width),
                              ('Background', self.background), ('Format', self.image_format)]:
            form.addRow(label, widget)
        left.addWidget(sheets)
        self.sheet_group = sheets
        storage, form = group('04   Local storage')
        self.output = QLineEdit(str(Path.home() / 'Documents' / 'ChessShortsStudio'))
        form.addRow(self.output)
        form.addRow(button('Choose session library…', self.browse_output))
        self.capture_storage = QComboBox()
        self.capture_storage.addItems(['Low storage · automatic', 'Original PNG files'])
        self.capture_storage.currentIndexChanged.connect(self.update_estimate)
        form.addRow('Frame storage', self.capture_storage)
        self.auto_sheets = QCheckBox('Generate sheets after capture')
        self.auto_sheets.setToolTip('Leave this off to save space. Generate sheets or export only the frames you pick.')
        form.addRow(self.auto_sheets)
        left.addWidget(storage)
        self.storage_group = storage
        left.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left_content)
        scroll.setMinimumWidth(290)
        split.addWidget(scroll)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.crop_preview = CropPreview()
        self.tabs.addTab(self.crop_preview, 'Capture region')
        self.live_view = ImageView()

        frame_page = QWidget()
        frame_layout = QVBoxLayout(frame_page)
        self.viewer = ImageView()
        frame_layout.addWidget(self.viewer, 1)
        self.frame_label = QLabel('Import or capture a video to start inspecting frames.')
        frame_layout.addWidget(self.frame_label)
        self.frame_scrub = QSlider(Qt.Orientation.Horizontal)
        self.frame_scrub.setAccessibleName('Scrub captured frames')
        self.frame_scrub.setRange(0, 0)
        self.frame_scrub.valueChanged.connect(lambda row: self.gallery.setCurrentRow(row))
        frame_layout.addWidget(self.frame_scrub)
        seek_row = QHBoxLayout()
        self.seek_seconds = number(0, 0, 864000, 3)
        self.seek_seconds.setSingleStep(.01)
        self.seek_seconds.setSuffix(' s')
        self.seek_seconds.setAccessibleName('Jump to capture time')
        seek_row.addWidget(self.seek_seconds)
        seek_row.addWidget(button('Go to time', self.jump_to_time))
        seek_row.addWidget(button('Export current frame', self.open_current_export))
        seek_row.addStretch()
        frame_layout.addLayout(seek_row)
        nav = QHBoxLayout()
        for text, action in [('← Previous', lambda: self.navigate(-1)), ('Next →', lambda: self.navigate(1)),
                             ('−', lambda: self.viewer.zoom(1 / 1.25)), ('+', lambda: self.viewer.zoom(1.25)),
                             ('Fit', self.viewer.fit), ('1:1', self.viewer.actual)]:
            nav.addWidget(button(text, action))
        frame_layout.addLayout(nav)
        self.tabs.addTab(frame_page, 'Frame inspector')
        compare_page = QWidget()
        compare_layout = QVBoxLayout(compare_page)
        self.compare_hint = QLabel('Select a frame, pin it as A, then select another frame as B.')
        self.compare_hint.setWordWrap(True)
        compare_layout.addWidget(self.compare_hint)
        compare_controls = QHBoxLayout()
        self.pin_button = button('Pin current frame as A', self.pin_frame)
        self.diff = QCheckBox('Pixel difference ×2')
        self.diff.toggled.connect(self.show_comparison)
        compare_controls.addWidget(self.pin_button)
        compare_controls.addWidget(self.diff)
        compare_layout.addLayout(compare_controls)
        comparison = QSplitter()
        self.compare_left, self.compare_right = ImageView(), ImageView()
        comparison.addWidget(self.compare_left)
        comparison.addWidget(self.compare_right)
        compare_layout.addWidget(comparison, 1)
        self.tabs.addTab(compare_page, 'Compare A / B')
        self.live_tab_index = self.tabs.addTab(self.live_view, 'Last captured frame')
        self.build_chatgpt_export()
        self.pinned = None
        center_layout.addWidget(self.tabs, 1)
        self.status_label = QLabel('Ready · All sessions stay on this computer')
        self.status_label.setWordWrap(True)
        center_layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        center_layout.addWidget(self.progress)
        split.addWidget(center)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        self.right_tabs = QTabWidget()
        frame_library = QWidget()
        right_frames = QVBoxLayout(frame_library)
        self.info = QLabel('No active session')
        self.info.setWordWrap(True)
        self.info.setObjectName('muted')
        right_frames.addWidget(self.info)
        self.gallery = QListWidget()
        self.gallery.setIconSize(QSize(72, 46))
        self.gallery.setUniformItemSizes(True)
        self.gallery.setToolTip('Check a frame to pick it, or press Ctrl+Space to toggle the current frame.')
        self.gallery.currentRowChanged.connect(self.select_frame)
        self.gallery.verticalScrollBar().valueChanged.connect(self.schedule_thumbnails)
        self.gallery.itemChanged.connect(self.pick_changed)
        right_frames.addWidget(self.gallery, 1)
        self.pick_count = QLabel('No frames picked')
        right_frames.addWidget(self.pick_count)
        pick_row = QHBoxLayout()
        pick_range = button('Pick through current', self.pick_through_current)
        pick_range.setToolTip('Pick every frame from the last frame you checked through the current frame.')
        pick_row.addWidget(pick_range)
        pick_row.addWidget(button('Clear picks', self.clear_picks))
        right_frames.addLayout(pick_row)
        self.export_picks_button = button('Export picked frames', self.open_picked_export, 'export')
        self.export_picks_button.setEnabled(False)
        right_frames.addWidget(self.export_picks_button)
        self.relink_video_button = button('Relink original video…', self.relink_video)
        self.relink_video_button.hide()
        right_frames.addWidget(self.relink_video_button)
        right_frames.addWidget(button('Refresh frames', self.refresh_frames))
        right_frames.addWidget(button('Delete current capture…', self.delete_current_capture))
        self.duplicates_button = button('Review exact duplicates…', self.review_duplicates)
        right_frames.addWidget(self.duplicates_button)
        self.right_tabs.addTab(frame_library, 'Frames')
        session_library = QWidget()
        library_layout = QVBoxLayout(session_library)
        self.library = QListWidget()
        self.library.itemDoubleClicked.connect(self.open_library_item)
        library_layout.addWidget(self.library, 1)
        for text, action in [('Refresh library', self.refresh_library), ('Open selected', self.open_library_item),
                             ('Open session folder…', self.open_session), ('Rename selected…', self.rename_session),
                             ('Delete selected…', self.delete_session)]:
            library_layout.addWidget(button(text, action))
        self.right_tabs.addTab(session_library, 'Sessions')
        right_layout.addWidget(self.right_tabs)
        split.addWidget(right)
        split.setSizes([320, 820, 300])
        split.setStretchFactor(1, 1)
        right.setMinimumWidth(240)

        toolbar = QHBoxLayout()
        self.start_button = button('▶  Start Capture', self.start_capture, 'primary')
        self.stop_button = button('■  Stop', self.stop_capture)
        self.pause_button = button('Ⅱ  Pause', self.toggle_pause)
        self.generate_button = button('Generate Contact Sheets', self.generate_sheets)
        self.export_button = button('Export for ChatGPT', self.open_chatgpt_export, 'export')
        self.folder_button = button('Open Output Folder', self.open_output)
        self.sheets_folder_button = button('Open Contact Sheets Folder', self.open_sheets)
        for widget in (self.start_button, self.pause_button, self.stop_button, self.generate_button,
                       self.export_button):
            toolbar.addWidget(widget)
        folders = QHBoxLayout()
        self.reveal_export_button = button("Show exported ZIP", self.reveal_export)
        self.reveal_export_button.setEnabled(False)
        folders.addWidget(button("Check setup", self.check_setup))
        folders.addWidget(button('Open logs', lambda: self.open_folder(Path.home() / 'ChessShorts' / 'logs')))
        folders.addWidget(self.reveal_export_button)
        folders.addStretch()
        folders.addWidget(self.folder_button)
        folders.addWidget(self.sheets_folder_button)
        center_layout.addLayout(folders)
        layout.addSpacing(8)
        layout.addLayout(toolbar)
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.method.currentIndexChanged.connect(self.method_changed)
        self.presets.currentIndexChanged.connect(self.interval_preset)
        self.interval.valueChanged.connect(self.interval_changed)
        self.sheet_preset.currentIndexChanged.connect(self.layout_preset)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        for key, delta in [('Left', -1), ('Right', 1)]:
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(lambda checked=False, d=delta: self.navigate(d))
            self.addAction(action)
        toggle_pick = QAction('Toggle current frame pick', self)
        toggle_pick.setShortcut(QKeySequence('Ctrl+Space'))
        toggle_pick.triggered.connect(self.toggle_current_pick)
        self.addAction(toggle_pick)
        self.windows.currentIndexChanged.connect(self.window_selected)
        self.fallback.toggled.connect(self.invalidate_crop)
        self.crop_preview.changed.connect(self.mark_crop)
        self.source.textChanged.connect(self.update_estimate)
        for widget in (self.start, self.end, self.per_sheet, self.columns, self.rows):
            widget.valueChanged.connect(self.update_estimate)
        self.method_changed()

    def build_chatgpt_export(self):
        self.export_page = QScrollArea()
        self.export_page.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        self.export_session_label = QLabel()
        self.export_session_label.setTextFormat(Qt.TextFormat.PlainText)
        self.export_session_label.setWordWrap(True)
        layout.addWidget(self.export_session_label)
        self.export_options, form = group('Compact image export')
        self.export_range = QComboBox()
        for label, value in [('All captured frames', 'all'), ('Time range', 'time'),
                             ('Frame-number range', 'frame'), ('Picked frames', 'picked')]:
            self.export_range.addItem(label, value)
        form.addRow('Selection', self.export_range)
        self.export_time_start = number(0, 0, 864000, 6)
        self.export_time_end = number(0, 0, 864000, 6)
        self.export_first_frame = integer(1, 1, 1)
        self.export_last_frame = integer(1, 1, 1)
        for label, widget in [('Start (seconds)', self.export_time_start), ('End (seconds)', self.export_time_end),
                              ('First frame', self.export_first_frame), ('Last frame', self.export_last_frame)]:
            form.addRow(label, widget)
        self.export_range_hint = QLabel()
        self.export_range_hint.setWordWrap(True)
        form.addRow(self.export_range_hint)
        self.export_format = QComboBox()
        self.export_format.addItem('Individual JPEG frames', 'frames')
        self.export_format.addItem('Contact sheets (JPEG)', 'sheets')
        form.addRow('Image format', self.export_format)
        self.export_budget = number(DEFAULT_BATCH_BYTES / MIB, .01, 100)
        self.export_budget.setSuffix(' MiB')
        form.addRow('ChatGPT export size per batch', self.export_budget)
        self.export_quality = integer(86, 65, 95)
        self.export_dimension = integer(1280, MIN_FRAME_DIMENSION, 2048)
        self.export_dimension.setSuffix(' px')
        self.export_per_sheet = integer(4, 1, MAX_FRAMES_PER_SHEET)
        form.addRow('JPEG quality', self.export_quality)
        form.addRow('Maximum side per frame', self.export_dimension)
        form.addRow('Frames per contact sheet', self.export_per_sheet)
        self.export_zip_batches = QCheckBox('Also save a ZIP for each batch')
        form.addRow(self.export_zip_batches)
        hint = QLabel(
            'Smaller JPEG copies reduce upload size. Original captures stay in the session. '
            'A tight budget can add batches, split sheets, or reduce an individual frame’s JPEG quality or size. '
            'The budget applies to JPEG files on disk. ChatGPT sets its own message and attachment limits.'
        )
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        form.addRow(hint)
        self.build_compact_button = button('Create ChatGPT export', self.start_compact_export, 'export')
        form.addRow(self.build_compact_button)
        layout.addWidget(self.export_options)
        self.export_summary = QLabel()
        self.export_summary.setWordWrap(True)
        layout.addWidget(self.export_summary)
        self.open_compact_button = button('Open export folder', self.open_compact_export)
        layout.addWidget(self.open_compact_button)
        self.export_batches = QListWidget()
        self.export_batches.setMinimumHeight(106)
        self.export_batches.setMaximumHeight(184)
        layout.addWidget(self.export_batches)

        full_quality, full_form = group('Full-quality export')
        self.legacy_export_group = full_quality
        self.include_frames = QCheckBox('Include original frames in ZIP')
        full_form.addRow(self.include_frames)
        self.full_quality_button = button('Create full-quality ZIP', self.export_session)
        full_form.addRow(self.full_quality_button)
        layout.addWidget(full_quality)
        layout.addStretch()
        self.export_page.setWidget(content)
        self.tabs.addTab(self.export_page, 'ChatGPT export')
        self.export_range.currentIndexChanged.connect(self.update_export_options)
        self.export_format.currentIndexChanged.connect(self.update_export_options)
        self.reset_compact_export()

    def update_export_options(self, *_args):
        form = self.export_options.layout()
        selection = self.export_range.currentData()
        for widget in (self.export_time_start, self.export_time_end):
            form.setRowVisible(widget, selection == 'time')
        for widget in (self.export_first_frame, self.export_last_frame):
            form.setRowVisible(widget, selection == 'frame')
        form.setRowVisible(self.export_range_hint, selection != 'all')
        form.setRowVisible(self.export_per_sheet, self.export_format.currentData() == 'sheets')
        if selection == 'time' and self.session:
            elapsed = time_field(self.session) == 'elapsed_capture_seconds'
            basis = 'Elapsed screen-capture time (excluding pauses)' if elapsed else 'Video time'
            self.export_range_hint.setText(f'{basis} in seconds. Both endpoints are included.')
        elif selection == 'frame':
            self.export_range_hint.setText('All captured frame numbers from first through last are included.')
        elif selection == 'picked':
            count = len(self.session.data.get("picked_frames", [])) if self.session else 0
            self.export_range_hint.setText(f'{count} picked frames. Check frames in the gallery to add them.')

    def reset_compact_export(self):
        session = self.session
        frames = session.data['frames'] if session else []
        self.export_session_label.setText(
            f'{session.data["name"]} · {len(frames)} captured frames' if session
            else 'Open a capture from Sessions or capture a video to start.'
        )
        self.export_options.setEnabled(bool(frames))
        self.legacy_export_group.setEnabled(bool(session))
        self.export_range.setCurrentIndex(0)
        self.export_first_frame.setMaximum(max(1, len(frames)))
        self.export_last_frame.setMaximum(max(1, len(frames)))
        self.export_first_frame.setValue(1)
        self.export_last_frame.setValue(max(1, len(frames)))
        if session and session.data.get("picked_frames"):
            self.export_range.setCurrentIndex(self.export_range.findData("picked"))
        elif len(frames) > 120:
            self.export_range.setCurrentIndex(self.export_range.findData("frame"))
            self.export_last_frame.setValue(1)
        self.export_time_start.setValue(0)
        field = time_field(session) if session else None
        end = max((frame.get(field) or 0 for frame in frames), default=0)
        self.export_time_end.setValue(math.ceil(end * 1_000_000) / 1_000_000)
        self.update_export_options()
        self.last_compact_export = None
        self.open_compact_button.setEnabled(False)
        self.export_summary.clear()
        self.export_batches.clear()
        self.export_batches.hide()

    def open_chatgpt_export(self):
        self.tabs.setCurrentWidget(self.export_page)

    def open_current_export(self):
        if self.session and self.gallery.currentRow() >= 0:
            number = self.gallery.currentRow() + 1
            self.export_first_frame.setValue(number)
            self.export_last_frame.setValue(number)
            self.export_range.setCurrentIndex(self.export_range.findData("frame"))
            self.open_chatgpt_export()

    def open_picked_export(self):
        if self.session and self.session.data.get("picked_frames"):
            self.export_range.setCurrentIndex(self.export_range.findData("picked"))
            self.open_chatgpt_export()

    def start_compact_export(self):
        if self.busy or not self.session:
            return
        session = self.session
        kind = self.export_range.currentData()
        start, end = None, None
        if kind == 'time':
            start, end = self.export_time_start.value(), self.export_time_end.value()
        elif kind == 'frame':
            start, end = self.export_first_frame.value(), self.export_last_frame.value()
        config = ExportConfig(
            layout=self.export_format.currentData(), batch_bytes=int(self.export_budget.value() * MIB),
            jpeg_quality=self.export_quality.value(), max_frame_dimension=self.export_dimension.value(),
            frames_per_sheet=self.export_per_sheet.value(), zip_batches=self.export_zip_batches.isChecked(),
            range_kind=kind, start=start, end=end,
        )
        try:
            config.validate()
        except ValueError as exc:
            self.export_summary.setText(str(exc))
            return
        self.compact_export_running = True
        self.export_summary.setText('Preparing JPEGs…')
        self.export_batches.hide()
        self.open_compact_button.setEnabled(False)
        self.status_label.setText('Preparing ChatGPT export…')
        self.stop_button.setText('Cancel export')
        self.run_job(lambda control, progress: export_compact(session, config, control, progress),
                     self.compact_export_done)

    def compact_export_done(self, result):
        if result is None:
            self.status_label.setText('ChatGPT export cancelled')
            self.export_summary.setText('Export cancelled.')
            return
        self.last_compact_export = result.folder
        self.export_batches.clear()
        batch_count = len(result.batches)
        summary = f'{result.exported_frames} frames exported · {batch_count} {"batch" if batch_count == 1 else "batches"}'
        if result.skipped_frames:
            summary += f' · {len(result.skipped_frames)} skipped. See the list and manifest for details.'
        self.status_label.setText(summary)
        self.export_summary.setText(summary)
        for batch in result.batches:
            size = batch['image_bytes']
            count = batch['file_count']
            self.export_batches.addItem(f'{batch["folder"]} · {count} JPEG{"s" if count != 1 else ""} '
                                        f'· {size / MIB:.3f} MiB ({size:,} bytes)')
        for frame in result.skipped_frames:
            self.export_batches.addItem(f'Frame #{frame["number"]:06d} · {frame["skip_reason"]}')
        self.export_batches.show()
        QTimer.singleShot(0, lambda: self.export_page.ensureWidgetVisible(self.export_batches))

    def open_compact_export(self):
        if self.last_compact_export:
            self.open_folder(self.last_compact_export)

    def window_selected(self, *_args):
        self.invalidate_crop()
        if self.method.currentIndex() == 1 and self.windows.currentData():
            QTimer.singleShot(0, self.window_preview)

    def invalidate_crop(self, *_args):
        self.crop_generation += 1
        self.crop_ready = False
        self.crop_selected = False
        self.crop_preview.crop = (0., 0., 1., 1.)
        from PySide6.QtGui import QImage
        self.crop_preview.set_image(QImage())

    def mark_crop(self, _crop):
        self.crop_selected = self.crop_ready

    def preview_ready(self, result):
        generation, image = result
        if generation == self.crop_generation and not self.busy and not self.closing:
            self.crop_preview.set_image(image)
            self.crop_ready = True
            self.status_label.setText('Preview ready · drag to crop, or capture the whole window')

    def check_setup(self):
        self.run_job(lambda _c, _p: diagnostics(),
                     lambda text: self.status_label.setText('Setup · ' + text.replace('\n', '  |  ')))

    def inspect_video(self):
        path = self.source.text().strip()
        def ready(info):
            if info is not None:
                self.inspected_video = (path, info)
                self.update_estimate()
                self.status_label.setText(f"Video inspected · {info['width']} × {info['height']} · {info['duration']:.3f} seconds")
        self.run_job(lambda control, _p: probe(Path(path), control), ready)

    def update_estimate(self, *_args):
        duration, width, height = 30., None, None
        label = '30 s example: '
        if self.method.currentIndex() == 0 and self.inspected_video and self.inspected_video[0] == self.source.text().strip():
            info = self.inspected_video[1]
            end = min(self.end.value() or info['duration'], info['duration'])
            duration = end - self.start.value()
            width, height = info['width'], info['height']
            label = ''
        elif self.end.value():
            duration = self.end.value() - (0 if self.method.currentIndex() == 1 else self.start.value())
            label = ''
        try:
            low = self.capture_storage.currentIndex() == 0
            if low and self.method.currentIndex() == 0:
                count = math.ceil(max(0, duration) / self.interval.value())
                fps = self.inspected_video[1].get("fps") if width and self.inspected_video else None
                limit = f' · source averages {fps:g} fps' if fps else ''
                self.frame_estimate.setText(f'{label}up to {count:,} requested time slots{limit}. Only real frames are indexed. No image files during capture.')
            elif low:
                count = math.ceil(max(0, duration) / self.interval.value())
                self.frame_estimate.setText(f'{label}up to {count:,} capture attempts. Saves compact JPEGs and shares unchanged consecutive frames. Browser timing varies.')
            else:
                self.frame_estimate.setText(label + estimate(duration, self.interval.value(), min(self.per_sheet.value(), self.columns.value() * self.rows.value()), width, height))
        except ValueError:
            self.frame_estimate.setText('Set an end later than start to estimate capture size.')

    def reveal_export(self):
        if not self.last_export or not self.last_export.is_file():
            self.error('Export a session first; the previous ZIP may have been moved or deleted.')
            return
        import sys
        if sys.platform == 'win32':
            import subprocess
            subprocess.Popen(['explorer.exe', '/select,', str(self.last_export)])
        else:
            self.open_folder(self.last_export.parent)

    def export_done(self, path):
        self.last_export = path
        self.reveal_export_button.setEnabled(True)
        self.status_label.setText(f'Export ready · {path}')

    def error(self, message):
        logging.error('%s', message)
        self.status_label.setText('Needs attention · ' + message.splitlines()[0])
        QMessageBox.warning(self, 'Capture needs attention', message)

    def interval_preset(self, index):
        if 0 <= index < len(self.interval_options):
            self.interval.setValue(self.interval_options[index])

    def interval_changed(self):
        self.update_estimate()
        values = self.interval_options
        value = self.interval.value()
        index = values.index(value) if value in values else len(values)
        self.presets.blockSignals(True)
        self.presets.setCurrentIndex(index)
        self.presets.blockSignals(False)

    def layout_preset(self, index):
        if index in (0, 1, 2, 4):
            count, columns, rows = {0: (10, 2, 5), 1: (4, 2, 2), 2: (1, 1, 1), 4: (2, 2, 1)}[index]
            self.per_sheet.setValue(count)
            self.columns.setValue(columns)
            self.rows.setValue(rows)
            if index == 2:
                self.frame_width.setValue(0)

    def mode_changed(self, index):
        if index == 1:
            self.interval.setValue(.01 if self.method.currentIndex() == 0 else .1)
            self.sheet_preset.setCurrentIndex(1)

    def method_changed(self, _index=0):
        index = self.method.currentIndex()
        is_window = index == 1
        self.source.setVisible(not is_window)
        self.browse_button.setVisible(index == 0)
        self.inspect_button.setVisible(index == 0)
        self.update_estimate()
        for widget in (self.windows, self.refresh_windows_button, self.preview_button, self.fallback):
            widget.setVisible(is_window)
        self.start.setEnabled(not is_window)
        self.source.setPlaceholderText('Paste a YouTube video or Shorts URL…' if index == 2 else 'Choose a video file…')
        self.source_hint.setText([
            'Original pixels and real media timestamps. FFmpeg extracts without playback.',
            'Select a browser window, then start capture and play the video. Drag on the preview to capture only part of the window. Keep the browser restored.',
            'Opens a separate Chromium window. Consent, ads, login, or restricted playback may prevent automation. Use Browser window capture if it fails.',
        ][index])
        if is_window and self.sender() is self.method:
            self.tabs.setCurrentIndex(0)
            self.refresh_windows()

    def browse_video(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open video', '', 'Videos (*.mp4 *.webm *.mov *.mkv);;All files (*)')
        if path:
            self.source.setText(path)
            self.method.setCurrentIndex(0)

    def browse_output(self):
        path = QFileDialog.getExistingDirectory(self, 'Choose session library', self.output.text())
        if path:
            self.output.setText(path)
            self.refresh_library()

    def sheet_config(self):
        config = SheetConfig(self.per_sheet.value(), self.columns.value(), self.rows.value(),
                             self.spacing.value(), self.background.text(), self.font_size.value(),
                             self.image_format.currentText(), self.frame_width.value())
        config.validate()
        return config

    def refresh_windows(self):
        try:
            windows = WindowsCapture().windows()
            self.windows.clear()
            for item in windows:
                self.windows.addItem(f'{item.browser.removesuffix(".exe")} · {item.title}', item)
            if not windows:
                self.status_label.setText('No Brave, Chrome, or Edge windows found. Open a browser and refresh.')
        except Exception as exc:
            self.error(str(exc))

    def window_preview(self):
        if self.busy or self.preview_job:
            return
        window = self.windows.currentData()
        if not window:
            self.error('Refresh browser windows and select one first.')
            return
        fallback = self.fallback.isChecked()
        self.invalidate_crop()
        generation = self.crop_generation
        self.preview_job_generation = generation
        self.status_label.setText('Loading window preview…')
        def task(_control, _progress):
            if fallback:
                time.sleep(3)  # Allow the selected browser to become foreground.
            with WindowsCapture().grab(window, fallback=fallback) as image:
                return generation, qimage(image)
        if fallback:
            self.status_label.setText('Preview in 3 seconds · switch to the selected browser now')
        self.preview_button.setEnabled(False)
        worker = Worker(task)
        self.preview_job = worker
        worker.result.connect(self.preview_ready)
        worker.failed.connect(self.error)
        worker.finished.connect(self.preview_finished)
        worker.start()

    def preview_finished(self):
        worker = self.preview_job
        self.preview_job = None
        if worker:
            worker.deleteLater()
        self.preview_button.setEnabled(not self.busy)
        if (self.preview_job_generation != self.crop_generation and not self.busy
                and self.method.currentIndex() == 1 and self.windows.currentData()):
            QTimer.singleShot(0, self.window_preview)

    def run_job(self, function, result, pausable=False):
        if self.busy:
            return
        self.busy = True
        self.paused = False
        self.pause_button.setText('Ⅱ  Pause')
        for widget in (self.start_button, self.generate_button, self.export_button, self.duplicates_button,
                       self.source_group, self.capture_group, self.sheet_group, self.storage_group, self.library,
                       self.export_options, self.legacy_export_group):
            widget.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.pause_button.setEnabled(pausable)
        self.progress.setRange(0, 0)
        self.job = Worker(function)
        self.job.result.connect(result)
        self.job.failed.connect(self.job_error)
        self.job.progress.connect(self.update_progress)
        self.job.frame.connect(self.live_view.set_image)
        self.job.finished.connect(self.job_finished)
        self.job.start()

    def update_progress(self, done, total):
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        if self.compact_export_running:
            if self.job and not self.job.control.stopped:
                self.status_label.setText(f'Exporting · {done} / {total} frames processed')
        else:
            self.status_label.setText(f'{"Paused" if self.paused else "Working"} · {done} frames' + (f' / {total}' if total else ''))

    def job_error(self, message):
        if self.compact_export_running:
            self.export_summary.setText(message)
            self.status_label.setText('ChatGPT export needs attention')
            return
        if self.pending_session:
            try:
                session = self.pending_session
                session.finish('error', message) if session.data['status'] == 'capturing' else None
                self.show_session(session)
            except (OSError, ValueError):
                pass
        self.error(message)
        if self.method.currentIndex() == 2:
            self.browser_fallback_button.setVisible(True)
            self.status_label.setText('Automatic capture failed. Select Browser window, choose Brave, then refresh preview.')

    def offer_window_capture(self):
        self.method.setCurrentIndex(1)
        self.browser_fallback_button.setVisible(False)
        self.refresh_windows()

    def job_finished(self):
        worker = self.job
        if self.status_label.text().startswith('Stopping'):
            self.status_label.setText('Task finished · completed files are preserved')
        self.job = None
        self.busy = False
        self.compact_export_running = False
        self.pending_session = None
        self.paused = False
        self.pause_button.setText('Ⅱ  Pause')
        for widget in (self.start_button, self.generate_button, self.export_button, self.duplicates_button,
                       self.source_group, self.capture_group, self.sheet_group, self.storage_group, self.library):
            widget.setEnabled(True)
        self.method_changed()
        self.stop_button.setEnabled(False)
        self.stop_button.setText('■  Stop')
        self.pause_button.setEnabled(False)
        self.export_options.setEnabled(bool(self.session and self.session.data['frames']))
        self.legacy_export_group.setEnabled(self.session is not None)
        self.open_compact_button.setEnabled(self.last_compact_export is not None)
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        if worker:
            worker.deleteLater()
        self.refresh_library()
        if self.close_pending:
            self.close()

    def start_capture(self):
        if self.busy:
            return
        try:
            index = self.method.currentIndex()
            source = self.source.text().strip()
            window = self.windows.currentData()
            if index == 0 and not Path(source).is_file():
                raise ValueError('Choose an existing MP4, WebM, MOV, or MKV file.')
            if index == 2:
                source = normalize_url(source)
                self.source.setText(source)
            if index == 1 and not window:
                raise ValueError('Refresh browser windows and select the browser to capture.')
            if index == 0:
                executable('ffmpeg')
                executable('ffprobe')
            start, end = self.start.value(), self.end.value() or None
            if index != 1 and end is not None and end <= start:
                raise ValueError('End must be later than start.')
            if self.mode.currentIndex() == 1 and index != 1 and end is None:
                raise ValueError('Set an end timestamp for detailed capture.')
            config = self.sheet_config()
            library = Path(self.output.text()).expanduser()
            parent = None
            if self.mode.currentIndex() == 1 and self.session:
                parent = str(self.session.root)
                library = self.session.root / 'details'
            low = self.capture_storage.currentIndex() == 0
            storage = ("source" if index == 0 else "jpeg") if low else "png"
            make_sheets = self.auto_sheets.isChecked()
            session = Session.create(library, ['local', 'window', 'youtube'][index], self.interval.value(),
                                     window.title if index == 1 else str(Path(source).resolve()) if index == 0 else source,
                                     parent=parent, storage=storage)
            self.pending_session = session
            logging.info('Capture started: %s (%s)', session.root, session.data['method'])
            crop = self.crop_preview.crop
            fallback = self.fallback.isChecked()
            self.save_settings()
            self.status_label.setText('Preparing capture…' if index != 1 else 'Capture starting · switch to the browser and play the video')

            if index == 1:
                self.tabs.setCurrentIndex(self.live_tab_index)
            def task(control, progress):
                emit_frame = self.job.frame.emit
                last_preview = -float('inf')
                def capture_progress(done, total):
                    nonlocal last_preview
                    progress(done, total)
                    now = time.monotonic()
                    if storage != "source" and session.data['frames'] and now - last_preview >= .5:
                        try:
                            with Image.open(session.root / session.data['frames'][-1]['file']) as image:
                                image.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
                                emit_frame(qimage(image))
                        except (OSError, ValueError):
                            pass  # A preview failure must not abort acquisition.
                        last_preview = now
                try:
                    if index == 0:
                        extract(Path(source), session, start, end, control, capture_progress)
                    elif index == 1:
                        # Gives time to bring Brave forward, especially for foreground fallback.
                        for _ in range(30):
                            if control.stopped:
                                session.finish('stopped')
                                return session
                            time.sleep(.1)
                        capture_window(WindowsCapture(), window, crop, session, control, capture_progress, end, fallback)
                    else:
                        capture_youtube(source, session, control, start, end, capture_progress)
                except Cancelled:
                    session.finish('stopped')
                if make_sheets and session.data['frames']:
                    generate(session, config, progress=progress)
                return session
            self.run_job(task, self.capture_done, pausable=index in (1, 2))
        except Exception as exc:
            self.error(str(exc))

    def capture_done(self, session):
        if session:
            logging.info('Capture finished: %s; status=%s; frames=%s', session.root, session.data['status'], len(session.data['frames']))
            self.show_session(session)
            self.status_label.setText(f'{session.data["status"].title()} · {len(session.data["frames"])} frames · {len(session.data["contact_sheets"])} contact sheets')

    def stop_capture(self):
        if self.job:
            logging.info('Stop requested')
            self.job.control.stop()
            self.status_label.setText('Cancelling ChatGPT export…' if self.compact_export_running
                                      else 'Stopping · preserving completed frames…')
            self.stop_button.setEnabled(False)

    def toggle_pause(self):
        if not self.job:
            return
        self.paused = not self.paused
        control = self.job.control
        if self.paused:
            control.pause()
            self.pause_started = utc_now()
        else:
            control.resume()
        self.pause_button.setText('▶  Resume' if self.paused else 'Ⅱ  Pause')
        self.status_label.setText('Paused · playback in Brave is not paused automatically' if self.paused else 'Capturing…')

    def tick(self):
        if self.job and self.pending_session:
            elapsed = self.job.control.elapsed()
            count = len(self.pending_session.data['frames'])
            self.info.setText(f'{"Paused" if self.paused else "Capturing"} · {elapsed:.1f} s active\n{count} frames · {self.pending_session.data["requested_interval_seconds"]:g} s requested\n{self.pending_session.root}')

    def show_session(self, session):
        self.last_export = session.root / "contact_sheets.zip"
        self.reveal_export_button.setEnabled(self.last_export.is_file())
        self.session = session
        self.reset_compact_export()
        self.pinned = None
        self.token += 1
        self.preview_token += 1
        self.comparison_token += 1
        self.compare_left.scene().clear()
        self.compare_left._item = None
        self.compare_right.scene().clear()
        self.compare_right._item = None
        self.compare_hint.setText('Select a frame, pin it as A, then select another frame as B.')
        for worker in self.thumbnail_jobs:
            worker.requestInterruption()
        self.thumb_visible.clear()
        self.thumb_loaded.clear()
        self.pick_anchor = None
        picked = set(session.data.get("picked_frames", []))
        self.gallery.blockSignals(True)
        self.gallery.clear()
        for frame in session.data['frames']:
            text = frame_label(frame)
            if frame.get('excluded_from_sheets'):
                text += ' · excluded'
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if frame["number"] in picked else Qt.CheckState.Unchecked)
            item.setSizeHint(QSize(160, 52))
            self.gallery.addItem(item)
        self.gallery.blockSignals(False)
        field = time_field(session)
        self.time_index = sorted((frame[field], i) for i, frame in enumerate(session.data["frames"])
                                 if frame.get(field) is not None)
        self.frame_scrub.blockSignals(True)
        self.frame_scrub.setRange(0, max(0, self.gallery.count() - 1))
        self.frame_scrub.setValue(0)
        self.frame_scrub.blockSignals(False)
        self.frame_scrub.setEnabled(bool(self.gallery.count()))
        self.seek_seconds.setEnabled(bool(self.time_index))
        if self.time_index:
            self.seek_seconds.setRange(self.time_index[0][0], self.time_index[-1][0])
        self.update_pick_controls()
        storage = session.data.get("storage", "png")
        count = len({frame["file"] for frame in session.data["frames"] if frame["file"]})
        medium = {
            "source": "Video reference · no saved screenshot files",
            "jpeg": f"{count:,} compact JPEG files",
            "png": f"{count:,} original PNG files",
        }.get(storage, storage)
        self.relink_video_button.setVisible(storage == "source")
        self.info.setText(f'{session.data["name"]}\n{len(session.data["frames"])} real frames · {session.data["requested_interval_seconds"]:g} s requested\n{medium}\n{session.root}')
        if session.data['frames']:
            self.gallery.setCurrentRow(0)
            self.tabs.setCurrentIndex(1)
        else:
            self.viewer.scene().clear()
            self.viewer._item = None
            self.frame_label.setText('No captured frames in this session.')

    def schedule_thumbnails(self, *_args):
        self.thumb_timer.start(100)

    def update_thumbnail_window(self):
        if not self.session or self.closing or not self.gallery.count():
            return
        top = self.gallery.indexAt(QPoint(12, 12)).row()
        if top < 0:
            top = max(0, self.gallery.currentRow() - 2)
        limit = 9 if self.session.data.get("storage") == "source" else 28
        window = set(range(max(0, top - 1), min(self.gallery.count(), top + limit)))
        if window != self.thumb_visible:
            for index in self.thumb_visible - window:
                item = self.gallery.item(index)
                if item:
                    item.setIcon(QIcon())
            self.thumb_visible = window
            self.thumb_loaded.intersection_update(window)
            for worker in self.thumbnail_jobs:
                worker.requestInterruption()
        if self.thumbnail_jobs:
            return
        missing = sorted(window - self.thumb_loaded)
        if not missing:
            return
        worker = ThumbnailWorker(self.session, missing, self.token)
        self.thumbnail_jobs.append(worker)
        worker.thumbnail.connect(self.set_thumbnail)
        worker.finished.connect(lambda w=worker: self.thumbnail_finished(w))
        worker.start()

    def thumbnail_finished(self, worker):
        if worker in self.thumbnail_jobs:
            self.thumbnail_jobs.remove(worker)
        if worker.token == self.token:
            self.thumb_loaded.update(worker.attempted & self.thumb_visible)
        worker.deleteLater()
        self.schedule_thumbnails()

    def set_thumbnail(self, token, index, image):
        if token == self.token and index in self.thumb_visible:
            self.gallery.item(index).setIcon(QIcon(QPixmap.fromImage(image)))

    def jump_to_time(self):
        if not self.time_index:
            return
        time = self.seek_seconds.value()
        at = bisect.bisect_left(self.time_index, (time, -1))
        _, row = min(self.time_index[max(0, at - 1):at + 1], key=lambda pair: abs(pair[0] - time))
        self.gallery.setCurrentRow(row)

    def update_pick_controls(self):
        count = len(self.session.data.get("picked_frames", [])) if self.session else 0
        self.pick_count.setText(f'{count} picked' if count else 'No frames picked')
        self.export_picks_button.setEnabled(bool(count))
        self.update_export_options()

    def save_picks(self, picks):
        previous = self.session.data.get("picked_frames", [])
        if picks == set(previous):
            return
        self.session.data["picked_frames"] = sorted(picks)
        try:
            self.session.save()
        except OSError as error:
            self.session.data["picked_frames"] = previous
            self.error(str(error))
            self.sync_pick_checks()
        self.update_pick_controls()

    def pick_changed(self, item):
        if not self.session:
            return
        number = self.gallery.row(item) + 1
        if number < 1:
            return
        picks = set(self.session.data.get("picked_frames", []))
        checked = item.checkState() == Qt.CheckState.Checked
        if checked != (number in picks):
            if checked:
                picks.add(number)
                self.pick_anchor = number
            else:
                picks.discard(number)
            self.save_picks(picks)

    def toggle_current_pick(self):
        item = self.gallery.currentItem()
        if item:
            checked = item.checkState() == Qt.CheckState.Checked
            item.setCheckState(Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked)

    def sync_pick_checks(self):
        picks = set(self.session.data.get("picked_frames", []))
        self.gallery.blockSignals(True)
        for row in range(self.gallery.count()):
            self.gallery.item(row).setCheckState(Qt.CheckState.Checked if row + 1 in picks else Qt.CheckState.Unchecked)
        self.gallery.blockSignals(False)

    def pick_through_current(self):
        if not self.session or self.gallery.currentRow() < 0:
            return
        current = self.gallery.currentRow() + 1
        first, last = sorted((self.pick_anchor or current, current))
        self.save_picks(set(self.session.data.get("picked_frames", [])) | set(range(first, last + 1)))
        self.sync_pick_checks()
        self.pick_anchor = current

    def clear_picks(self):
        if self.session:
            self.save_picks(set())
            self.sync_pick_checks()
            self.pick_anchor = None

    def relink_video(self):
        if not self.session or self.session.data.get("storage") != "source" or self.busy:
            return
        chosen, _ = QFileDialog.getOpenFileName(self, 'Relink the original video')
        if not chosen:
            return
        try:
            path = Path(chosen).resolve()
            if video_signature(path) != self.session.data.get("source_signature"):
                raise ValueError("This file does not match the indexed original video. Reindex it if the video changed.")
            previous = self.session.data["source"]
            self.session.data["source"] = str(path)
            try:
                self.session.save()
            except OSError:
                self.session.data["source"] = previous
                raise
            row = self.gallery.currentRow()
            self.show_session(self.session)
            self.gallery.setCurrentRow(row)
        except (OSError, ValueError) as error:
            self.error(str(error))

    def load_images(self, task, callback):
        if self.closing:
            return
        # At most two full-resolution jobs plus one replaceable pending request.
        if len(self.jobs) >= 2:
            self.pending_image_task = (task, callback)
            return
        worker = Worker(task)
        self.jobs.append(worker)
        worker.result.connect(callback)
        worker.failed.connect(self.error)
        worker.finished.connect(lambda w=worker: self.image_job_finished(w))
        worker.start()

    def image_job_finished(self, worker):
        if worker in self.jobs:
            self.jobs.remove(worker)
        worker.deleteLater()
        if self.pending_image_task and not self.closing:
            task, callback = self.pending_image_task
            self.pending_image_task = None
            self.load_images(task, callback)

    def select_frame(self, index):
        if not self.session or not 0 <= index < len(self.session.data['frames']):
            return
        self.preview_token += 1
        token = self.preview_token
        session = self.session
        frame = session.data['frames'][index]
        self.frame_scrub.blockSignals(True)
        self.frame_scrub.setValue(index)
        self.frame_scrub.blockSignals(False)
        stamp = frame.get(time_field(session))
        if stamp is not None:
            self.seek_seconds.setValue(stamp)
        self.schedule_thumbnails()
        base = f'{frame_label(frame)}   ·   {frame["width"]} × {frame["height"]}'
        self.frame_label.setText(base)
        if frame['file'] is not None and not contained(session.root, frame['file']).is_file():
            self.viewer.scene().clear()
            self.viewer._item = None
            self.frame_label.setText(base + ' · Source frame file is missing')
            return
        def task(control, _progress):
            try:
                with session.open_frame(frame, control) as image:
                    return token, qimage(image), None
            except (OSError, ValueError, RuntimeError) as error:
                return token, None, str(error)
        def ready(result):
            if result[0] != self.preview_token:
                return
            if result[2]:
                self.viewer.scene().clear()
                self.viewer._item = None
                self.frame_label.setText(base + ' · ' + result[2])
            else:
                self.viewer.set_image(result[1])
        self.load_images(task, ready)
        if self.pinned is not None:
            self.show_comparison()

    def navigate(self, delta):
        if self.gallery.count():
            self.gallery.setCurrentRow(max(0, min(self.gallery.count() - 1, self.gallery.currentRow() + delta)))

    def pin_frame(self):
        if self.session and self.gallery.currentRow() >= 0:
            self.pinned = self.gallery.currentRow()
            self.tabs.setCurrentIndex(2)
            self.show_comparison()

    def show_comparison(self, _checked=False):
        if self.pinned is None or not self.session or self.gallery.currentRow() < 0:
            return
        session = self.session
        left, right = session.data['frames'][self.pinned], session.data['frames'][self.gallery.currentRow()]
        self.comparison_token += 1
        token = self.comparison_token
        use_diff = self.diff.isChecked()
        self.compare_hint.setText(f'A: {frame_label(left)}   /   B: {frame_label(right)}' + (' · absolute RGB difference, contrast ×2' if use_diff else ''))
        hint = self.compare_hint.text()
        def task(control, _progress):
            try:
                with session.open_frame(left, control) as a, session.open_frame(right, control) as b:
                    first = qimage(a)
                    if use_diff:
                        with difference_images(a, b) as delta:
                            second = qimage(delta)
                    else:
                        second = qimage(b)
                return token, first, second, None
            except (OSError, ValueError, RuntimeError) as error:
                return token, None, None, str(error)
        def ready(result):
            if result[0] != self.comparison_token:
                return
            if result[3]:
                self.compare_hint.setText(hint + ' · ' + result[3])
                for view in (self.compare_left, self.compare_right):
                    view.scene().clear()
                    view._item = None
            else:
                self.compare_left.set_image(result[1])
                self.compare_right.set_image(result[2])
        self.load_images(task, ready)

    def generate_sheets(self):
        if not self.session:
            self.error('Open or capture a session first.')
            return
        try:
            session, config = self.session, self.sheet_config()
            self.run_job(lambda control, progress: generate(session, config, progress, control,
                                                                      picked_only=bool(session.data.get("picked_frames"))),
                         lambda result: self.status_label.setText(f'Generated {len(result)} contact sheets') if result else None)
        except Exception as exc:
            self.error(str(exc))

    def export_session(self):
        if not self.session:
            self.error('Open or capture a session first.')
            return
        session, include = self.session, self.include_frames.isChecked()
        self.run_job(lambda control, _progress: export_chatgpt(session, include_frames=include, control=control),
                     self.export_done)

    def review_duplicates(self):
        if not self.session:
            return
        session = self.session
        self.run_job(lambda control, _progress: find_duplicates(session, control), self.duplicate_results)

    def duplicate_results(self, pairs):
        if pairs is None:
            return
        if not pairs:
            QMessageBox.information(self, 'Duplicate review', 'No exact pixel duplicates found. Original frames are unchanged.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Review duplicates · originals are always preserved')
        dialog.resize(560, 440)
        layout = QVBoxLayout(dialog)
        label = QLabel('Checked frames will be excluded from future contact sheets.\nUncheck to restore them. Original screenshots and metadata are retained.')
        label.setWordWrap(True)
        layout.addWidget(label)
        listing = QListWidget()
        for number, original in pairs:
            item = QListWidgetItem(f'Frame #{number:06d} is identical to #{original:06d}')
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if self.session.data['frames'][number - 1].get('excluded_from_sheets') else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, number)
            listing.addItem(item)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            try:
                for index in range(listing.count()):
                    item = listing.item(index)
                    number = item.data(Qt.ItemDataRole.UserRole)
                    frame = self.session.data['frames'][number - 1]
                    frame['excluded_from_sheets'] = item.checkState() == Qt.CheckState.Checked
                    self.gallery.item(number - 1).setText(frame_label(frame) + (' · excluded' if frame['excluded_from_sheets'] else ''))
                self.session.save()
                self.status_label.setText('Duplicate review saved · regenerate contact sheets to apply exclusions')
            except OSError as exc:
                self.error(str(exc))

    def refresh_library(self):
        if self.busy:
            return
        self.library.clear()
        try:
            for metadata in list_sessions(Path(self.output.text()).expanduser()):
                try:
                    data = json.loads(metadata.read_text(encoding='utf-8'))
                    if not isinstance(data, dict):
                        continue
                    title = data.get('name', metadata.parent.name)
                    item = QListWidgetItem(f'{title}\n{data.get("screenshot_count", 0)} frames · {data.get("method", "unknown")} · {data.get("status", "unknown")}')
                    item.setData(Qt.ItemDataRole.UserRole, str(metadata.parent))
                    self.library.addItem(item)
                except (OSError, ValueError):
                    continue
        except OSError as exc:
            self.status_label.setText('Could not read session library: ' + str(exc))

    def open_library_item(self, _item=None):
        if self.busy:
            return
        item = self.library.currentItem()
        if item:
            self.open_session_path(Path(item.data(Qt.ItemDataRole.UserRole)))

    def open_session(self):
        if self.busy:
            return
        path = QFileDialog.getExistingDirectory(self, 'Open session folder containing metadata.json', self.output.text())
        if path:
            self.open_session_path(Path(path))

    def open_session_path(self, path):
        try:
            session = Session.load(path)
            if session.data['status'] == 'capturing':
                session.finish('interrupted', 'Previous capture did not finish; completed frames recovered.')
            self.show_session(session)
        except (OSError, ValueError, KeyError) as exc:
            self.error(f'Could not open this session: {exc}')

    def rename_session(self):
        if self.busy:
            return
        item = self.library.currentItem()
        if not item:
            return
        try:
            session = Session.load(Path(item.data(Qt.ItemDataRole.UserRole)))
            name, ok = QInputDialog.getText(self, 'Rename session', 'Display name', text=session.data['name'])
            if ok:
                session.rename(name)
                if self.session and self.session.root == session.root:
                    self.session = session
                self.refresh_library()
        except (OSError, ValueError) as exc:
            self.error(str(exc))

    def refresh_frames(self):
        if self.busy or not self.session:
            return
        session = self.session
        if not session.root.is_dir():
            self.clear_current_session()
            self.refresh_library()
            self.status_label.setText('The capture folder was removed. The frame list has been cleared.')
            return
        remaining = [frame for frame in session.data['frames']
                     if frame['file'] is None or contained(session.root, frame['file']).is_file()]
        removed = len(session.data['frames']) - len(remaining)
        if removed:
            session.data['frames'] = remaining
            picked = set(session.data.get("picked_frames", []))
            session.data["picked_frames"] = [number for number, frame in enumerate(remaining, 1)
                                              if frame["number"] in picked]
            for number, frame in enumerate(remaining, 1):
                frame['number'] = number
            try:
                session.save()
            except OSError as exc:
                self.error(f'Could not update session metadata: {exc}')
            logging.info('Removed %s missing frames from %s', removed, session.root)
        self.show_session(session)
        self.status_label.setText(f'Frames refreshed · {removed} missing frames removed from the list')

    def clear_current_session(self):
        for worker in self.thumbnail_jobs:
            worker.requestInterruption()
        self.session = None
        self.update_pick_controls()
        self.relink_video_button.hide()
        self.frame_scrub.setEnabled(False)
        self.seek_seconds.setEnabled(False)
        self.time_index = []
        self.reset_compact_export()
        self.token += 1
        self.preview_token += 1
        self.comparison_token += 1
        self.gallery.clear()
        for view in (self.viewer, self.compare_left, self.compare_right, self.live_view):
            view.scene().clear()
            view._item = None
        self.frame_label.setText('No captured frames in this session.')
        self.info.setText('No active session')
        self.pinned = None
        self.last_export = None
        self.reveal_export_button.setEnabled(False)

    def delete_current_capture(self):
        if self.session:
            self.delete_session(self.session.root)

    def delete_session(self, selected_path=None):
        if self.busy:
            return
        if selected_path is None or isinstance(selected_path, bool):
            item = self.library.currentItem()
            if not item:
                return
            selected_path = item.data(Qt.ItemDataRole.UserRole)
        path = Path(selected_path).resolve()
        library = Path(self.output.text()).expanduser().resolve()
        if path == library or not path.is_relative_to(library):
            self.error('Only sessions inside the current library can be deleted here.')
            return
        if QMessageBox.question(self, 'Delete session?', f'Permanently delete this session, its detailed captures, and all exports?\n\n{path}',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            if self.session and self.session.root.is_relative_to(path):
                for worker in self.thumbnail_jobs:
                    worker.requestInterruption()
                    worker.wait()
                for worker in self.jobs:
                    worker.wait()
                self.clear_current_session()
            shutil.rmtree(path)
            logging.info('Deleted capture: %s', path)
            self.refresh_library()
        except OSError as exc:
            self.error(str(exc))

    def open_folder(self, path):
        if not path.is_dir():
            self.error('This folder does not exist yet. Capture a session first.')
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            self.error('Windows could not open the folder. Its path is shown in session information.')

    def open_output(self):
        self.open_folder(self.session.root if self.session else Path(self.output.text()).expanduser())

    def open_sheets(self):
        if self.session:
            self.open_folder(self.session.root / 'contact_sheets')

    def settings_widgets(self):
        return {'source': self.source, 'output': self.output, 'interval': self.interval, 'start': self.start,
                'end': self.end, 'per_sheet': self.per_sheet, 'columns': self.columns, 'rows': self.rows,
                'spacing': self.spacing, 'font_size': self.font_size, 'frame_width': self.frame_width,
                'background': self.background, 'method': self.method, 'sheet_preset': self.sheet_preset,
                'image_format': self.image_format, 'include_frames': self.include_frames,
                'capture_storage': self.capture_storage, 'auto_sheets': self.auto_sheets,
                'export_format': self.export_format, 'export_budget': self.export_budget,
                'export_quality': self.export_quality, 'export_dimension': self.export_dimension,
                'export_per_sheet': self.export_per_sheet, 'export_zip_batches': self.export_zip_batches}

    def save_settings(self):
        for key, widget in self.settings_widgets().items():
            if isinstance(widget, QLineEdit): value = widget.text()
            elif isinstance(widget, QComboBox): value = widget.currentIndex()
            elif isinstance(widget, QCheckBox): value = widget.isChecked()
            else: value = widget.value()
            self.settings.setValue(key, value)
        self.settings.sync()

    def restore_settings(self):
        for key, widget in self.settings_widgets().items():
            if not self.settings.contains(key):
                continue
            value = self.settings.value(key)
            try:
                widget.blockSignals(True)
                if isinstance(widget, QLineEdit): widget.setText(str(value))
                elif isinstance(widget, QComboBox):
                    index = int(value)
                    if 0 <= index < widget.count():
                        widget.setCurrentIndex(index)
                elif isinstance(widget, QCheckBox): widget.setChecked(str(value).lower() == 'true')
                elif isinstance(widget, QSpinBox): widget.setValue(int(value))
                else: widget.setValue(float(value))
            except (TypeError, ValueError):
                pass
            finally:
                widget.blockSignals(False)
        self.method_changed()
        self.interval_changed()
        self.update_export_options()

    def closeEvent(self, event):
        if self.busy:
            title = 'Export is running' if self.compact_export_running else 'Capture is running'
            question = ('Cancel the export and close?' if self.compact_export_running
                        else 'Stop the current task, preserve completed frames, and close?')
            if QMessageBox.question(self, title, question,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                self.close_pending = True
                self.stop_capture()
            event.ignore()
            return
        self.save_settings()
        self.closing = True
        self.pending_image_task = None
        for worker in self.thumbnail_jobs:
            worker.requestInterruption()
        for worker in [*self.thumbnail_jobs, *self.jobs, *([self.preview_job] if self.preview_job else [])]:
            worker.wait()
        event.accept()
