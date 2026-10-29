from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .capture.local import extract, probe, executable
from .capture.window import WindowsCapture, capture_window
from .capture.youtube import capture_youtube, normalize_url
from .control import Cancelled, CaptureControl
from .export import export_chatgpt
from .inspection import difference, find_duplicates
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
            self.failed.emit(str(exc))


class ThumbnailWorker(QThread):
    thumbnail = Signal(int, int, object)

    def __init__(self, root, frames, token):
        super().__init__()
        self.root, self.frames, self.token = root, list(frames), token

    def run(self):
        for index, frame in enumerate(self.frames):
            if self.isInterruptionRequested():
                break
            try:
                with Image.open(contained(self.root, frame['file'])) as image:
                    image.thumbnail((112, 112), Image.Resampling.LANCZOS)
                    self.thumbnail.emit(self.token, index, qimage(image))
            except (OSError, ValueError):
                continue


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
        self.setWindowTitle('Chess Shorts · Visual Analysis Studio')
        self.resize(1500, 960)
        self.setMinimumSize(1080, 720)
        self.setStyleSheet(STYLE)
        self.settings = QSettings('ChessShorts', 'VisualAnalysisStudio')
        self.session = None
        self.job = None
        self.jobs = []
        self.pending_image_task = None
        self.thumbnail_jobs = []
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
        self.build()
        self.restore_settings()
        self.refresh_library()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(200)

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
        self.presets.addItems(['0.05 seconds', '0.1 seconds', '0.25 seconds', '0.5 seconds', '1 second', '2 seconds', 'Custom'])
        self.presets.setCurrentIndex(3)
        self.interval = number(.5, .01, 3600, 3)
        self.interval.setSuffix(' s')
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
        self.sheet_preset.addItems(['Standard · 10 frames', 'High detail · 4 frames', 'Maximum detail · 1 frame', 'Custom'])
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
        self.include_frames = QCheckBox('Include original frames in export')
        form.addRow(self.include_frames)
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
        self.gallery.setIconSize(QSize(90, 90))
        self.gallery.currentRowChanged.connect(self.select_frame)
        right_frames.addWidget(self.gallery, 1)
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
        self.export_button = button('Export for ChatGPT', self.export_session, 'export')
        self.folder_button = button('Open Output Folder', self.open_output)
        self.sheets_folder_button = button('Open Contact Sheets Folder', self.open_sheets)
        for widget in (self.start_button, self.pause_button, self.stop_button, self.generate_button,
                       self.export_button):
            toolbar.addWidget(widget)
        folders = QHBoxLayout()
        self.reveal_export_button = button("Show exported ZIP", self.reveal_export)
        self.reveal_export_button.setEnabled(False)
        folders.addWidget(button("Check setup", self.check_setup))
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
        self.windows.currentIndexChanged.connect(self.invalidate_crop)
        self.fallback.toggled.connect(self.invalidate_crop)
        self.crop_preview.changed.connect(self.mark_crop)
        self.source.textChanged.connect(self.update_estimate)
        for widget in (self.start, self.end, self.per_sheet):
            widget.valueChanged.connect(self.update_estimate)
        self.method_changed()

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
        if generation == self.crop_generation:
            self.crop_preview.set_image(image)
            self.crop_ready = True

    def check_setup(self):
        self.run_job(lambda _c, _p: diagnostics(),
                     lambda text: QMessageBox.information(self, 'Setup diagnostics', text))

    def inspect_video(self):
        path = self.source.text().strip()
        def ready(info):
            if info is not None:
                self.inspected_video = (path, info)
                self.update_estimate()
                self.status_label.setText(f"Video inspected · {info['width']} × {info['height']} · {info['duration']:.3f} seconds")
        self.run_job(lambda _c, _p: probe(Path(path)), ready)

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
            self.frame_estimate.setText(label + estimate(duration, self.interval.value(), self.per_sheet.value(), width, height))
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
        self.status_label.setText('Needs attention · ' + message.splitlines()[0])
        QMessageBox.warning(self, 'Capture needs attention', message)

    def interval_preset(self, index):
        if index < 6:
            self.interval.setValue([.05, .1, .25, .5, 1, 2][index])

    def interval_changed(self):
        self.update_estimate()
        values = [.05, .1, .25, .5, 1, 2]
        value = self.interval.value()
        index = values.index(value) if value in values else 6
        self.presets.blockSignals(True)
        self.presets.setCurrentIndex(index)
        self.presets.blockSignals(False)

    def layout_preset(self, index):
        if index < 3:
            count, columns, rows = [(10, 2, 5), (4, 2, 2), (1, 1, 1)][index]
            self.per_sheet.setValue(count)
            self.columns.setValue(columns)
            self.rows.setValue(rows)
            if index == 2:
                self.frame_width.setValue(0)

    def mode_changed(self, index):
        if index == 1:
            self.interval.setValue(.1)
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
            'Refresh preview, drag a crop, then start and play the video. Keep the browser restored. Elapsed time is not video time. End is an optional active-duration limit.',
            'Opens a separate Chromium window. Consent, ads, login, or restricted playback may prevent automation. Use Browser window capture if it fails.',
        ][index])
        if is_window and self.sender() is self.method:
            self.tabs.setCurrentIndex(0)

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

    def run_job(self, function, result, pausable=False):
        if self.busy:
            return
        self.busy = True
        self.paused = False
        self.pause_button.setText('Ⅱ  Pause')
        for widget in (self.start_button, self.generate_button, self.export_button, self.duplicates_button,
                       self.source_group, self.capture_group, self.sheet_group, self.storage_group, self.library):
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
        self.status_label.setText(f'{"Paused" if self.paused else "Working"} · {done} frames' + (f' / {total}' if total else ''))

    def job_error(self, message):
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
        self.job = None
        self.busy = False
        self.pending_session = None
        self.paused = False
        self.pause_button.setText('Ⅱ  Pause')
        for widget in (self.start_button, self.generate_button, self.export_button, self.duplicates_button,
                       self.source_group, self.capture_group, self.sheet_group, self.storage_group, self.library):
            widget.setEnabled(True)
        self.method_changed()
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        if worker:
            worker.deleteLater()
        self.refresh_library()
        if self.close_pending:
            self.close()

    def start_capture(self):
        if self.busy or self.preview_job:
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
            if index == 1 and not (self.crop_ready and self.crop_selected):
                raise ValueError('Refresh capture preview and drag a video region before starting capture.')
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
            session = Session.create(library, ['local', 'window', 'youtube'][index], self.interval.value(),
                                     window.title if index == 1 else str(Path(source).resolve()) if index == 0 else source,
                                     parent=parent)
            self.pending_session = session
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
                    if session.data['frames'] and now - last_preview >= .5:
                        with Image.open(session.root / session.data['frames'][-1]['file']) as image:
                            image.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
                            emit_frame(qimage(image))
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
                if session.data['frames']:
                    # Stop ends acquisition; completed frames still get sheets automatically.
                    generate(session, config, progress=progress)
                return session
            self.run_job(task, self.capture_done, pausable=index in (1, 2))
        except Exception as exc:
            self.error(str(exc))

    def capture_done(self, session):
        if session:
            self.show_session(session)
            self.status_label.setText(f'{session.data["status"].title()} · {len(session.data["frames"])} frames · {len(session.data["contact_sheets"])} contact sheets')

    def stop_capture(self):
        if self.job:
            self.job.control.stop()
            self.status_label.setText('Stopping · preserving completed frames…')
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
        self.gallery.clear()
        for frame in session.data['frames']:
            text = frame_label(frame)
            if frame.get('excluded_from_sheets'):
                text += ' · excluded'
            self.gallery.addItem(QListWidgetItem(text))
        self.info.setText(f'{session.data["name"]}\n{len(session.data["frames"])} frames · {session.data["requested_interval_seconds"]:g} s requested\n{session.data["method"]} · {session.data["status"]}\n{session.root}')
        worker = ThumbnailWorker(session.root, session.data['frames'], self.token)
        self.thumbnail_jobs.append(worker)
        worker.thumbnail.connect(self.set_thumbnail)
        worker.finished.connect(lambda w=worker: self.thumbnail_finished(w))
        worker.start()
        if session.data['frames']:
            self.gallery.setCurrentRow(0)
            self.tabs.setCurrentIndex(1)
        else:
            self.viewer.scene().clear()
            self.viewer._item = None
            self.frame_label.setText('No captured frames in this session.')

    def thumbnail_finished(self, worker):
        if worker in self.thumbnail_jobs:
            self.thumbnail_jobs.remove(worker)
        worker.deleteLater()

    def set_thumbnail(self, token, index, image):
        if token == self.token and index < self.gallery.count():
            self.gallery.item(index).setIcon(QIcon(QPixmap.fromImage(image)))

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
        frame = self.session.data['frames'][index]
        self.frame_label.setText(f'{frame_label(frame)}   ·   {frame["width"]} × {frame["height"]} original pixels')
        path = contained(self.session.root, frame['file'])
        def task(_control, _progress):
            with Image.open(path) as image:
                return token, qimage(image)
        def ready(result):
            if result[0] == self.preview_token:
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
        a, b = contained(session.root, left['file']), contained(session.root, right['file'])
        self.comparison_token += 1
        token = self.comparison_token
        use_diff = self.diff.isChecked()
        self.compare_hint.setText(f'A: {frame_label(left)}   /   B: {frame_label(right)}' + (' · absolute RGB difference, contrast ×2' if use_diff else ''))
        def task(_control, _progress):
            with Image.open(a) as image:
                first = qimage(image)
            with difference(a, b) if use_diff else Image.open(b) as image:
                second = qimage(image)
            return token, first, second
        def ready(result):
            if result[0] == self.comparison_token:
                self.compare_left.set_image(result[1])
                self.compare_right.set_image(result[2])
        self.load_images(task, ready)

    def generate_sheets(self):
        if not self.session:
            self.error('Open or capture a session first.')
            return
        try:
            session, config = self.session, self.sheet_config()
            self.run_job(lambda control, progress: generate(session, config, progress, control),
                         lambda result: self.status_label.setText(f'Generated {len(result)} contact sheets') if result else None)
        except Exception as exc:
            self.error(str(exc))

    def export_session(self):
        if not self.session:
            self.error('Open or capture a session first.')
            return
        session, include = self.session, self.include_frames.isChecked()
        self.run_job(lambda _control, _progress: export_chatgpt(session, include_frames=include),
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

    def delete_session(self):
        if self.busy:
            return
        item = self.library.currentItem()
        if not item:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole)).resolve()
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
                self.session = None
                self.token += 1
                self.preview_token += 1
                self.gallery.clear()
                self.viewer.scene().clear()
                self.viewer._item = None
                self.info.setText('No active session')
                self.pinned = None
            shutil.rmtree(path)
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
                'image_format': self.image_format, 'include_frames': self.include_frames}

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
                elif isinstance(widget, QComboBox): widget.setCurrentIndex(int(value))
                elif isinstance(widget, QCheckBox): widget.setChecked(str(value).lower() == 'true')
                elif isinstance(widget, QSpinBox): widget.setValue(int(value))
                else: widget.setValue(float(value))
            except (TypeError, ValueError):
                pass
            finally:
                widget.blockSignals(False)
        self.method_changed()
        self.interval_changed()

    def closeEvent(self, event):
        if self.busy:
            if QMessageBox.question(self, 'Capture is running', 'Stop the current task, preserve completed frames, and close?',
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
