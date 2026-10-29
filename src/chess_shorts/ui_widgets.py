from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QLabel


def qimage(image):
    rgb = image.convert('RGB')
    return QImage(rgb.tobytes(), rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()


class ImageView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor('#10131b'))
        self._fit = True
        self._item = None
        self.setMinimumSize(200, 180)

    def set_image(self, image):
        self.scene().clear()
        self._item = self.scene().addPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self._item.boundingRect())
        self.fit()

    def fit(self):
        self._fit = True
        if self._item:
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def actual(self):
        self._fit = False
        self.resetTransform()

    def zoom(self, factor):
        self._fit = False
        scale = self.transform().m11() * factor
        if .01 <= scale <= 20:
            self.scale(factor, factor)

    def wheelEvent(self, event):
        self.zoom(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self.fit()


class CropPreview(QLabel):
    changed = Signal(tuple)

    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.crop = (0., 0., 1., 1.)
        self.origin = None
        self.setMinimumSize(260, 240)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText('Select a browser window to see its preview.\nOptional: drag to select just the video.')
        self.setStyleSheet('background:#10131b; color:#9aa9bd; border:1px solid #30394b; border-radius:8px;')

    def set_image(self, image):
        self.image = image
        self.update()

    def image_rect(self):
        if self.image.isNull():
            return QRectF()
        size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2, size.width(), size.height())

    def normalized(self, position):
        rect = self.image_rect()
        return (max(0, min(1, (position.x() - rect.x()) / rect.width())),
                max(0, min(1, (position.y() - rect.y()) / rect.height())))

    def paintEvent(self, event):
        if self.image.isNull():
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#10131b'))
        rect = self.image_rect()
        painter.drawImage(rect, self.image)
        left, top, right, bottom = self.crop
        selection = QRectF(rect.x() + rect.width() * left, rect.y() + rect.height() * top,
                           rect.width() * (right - left), rect.height() * (bottom - top))
        painter.setPen(QPen(QColor('#82e4b2'), 2))
        painter.fillRect(selection, QColor(80, 230, 170, 25))
        painter.drawRect(selection)

    def mousePressEvent(self, event):
        if not self.image.isNull() and event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position()):
            self.origin = self.normalized(event.position())

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            end = self.normalized(event.position())
            self.crop = (min(self.origin[0], end[0]), min(self.origin[1], end[1]),
                         max(self.origin[0], end[0]), max(self.origin[1], end[1]))
            self.update()

    def mouseReleaseEvent(self, event):
        if self.origin is not None:
            self.mouseMoveEvent(event)
            self.origin = None
            if self.crop[2] - self.crop[0] < .005 or self.crop[3] - self.crop[1] < .005:
                self.crop = (0., 0., 1., 1.)
            self.changed.emit(self.crop)
            self.update()
