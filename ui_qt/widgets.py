# -*- coding: utf-8 -*-
"""Небольшие самописные виджеты."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ui_qt.theme import BORDER_LIGHT, FG, GAP, GREEN, PAD


def hq_scaled(src: QPixmap, w: int, h: int, dpr: float = 1.0) -> QPixmap:
    """Вписать исходник в w×h логических пикселей с учётом масштаба экрана.

    Сжимаем до *физических* пикселей (w*dpr × h*dpr) и помечаем devicePixelRatio,
    поэтому Qt рисует 1:1 — без второго растягивания (оно и даёт «мыло»).
    Большое уменьшение делаем ступенями по 2×, иначе края получаются рваными.
    """
    if src.isNull():
        return QPixmap()
    tw, th = max(1, round(w * dpr)), max(1, round(h * dpr))
    img: QImage = src.toImage()
    # вписываем с сохранением пропорций
    k = min(tw / img.width(), th / img.height())
    fw, fh = max(1, round(img.width() * k)), max(1, round(img.height() * k))
    while img.width() > fw * 2 or img.height() > fh * 2:
        img = img.scaled(max(fw, img.width() // 2), max(fh, img.height() // 2),
                         Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    if (img.width(), img.height()) != (fw, fh):
        img = img.scaled(fw, fh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    out = QPixmap.fromImage(img)
    out.setDevicePixelRatio(dpr)
    return out


class _HiDpiPixmapCache:
    """Кэш отмасштабированного под текущий экран pixmap (пересчёт при смене dpr/размера)."""

    def __init__(self):
        self._src: QPixmap | None = None
        self._key = None
        self._pm: QPixmap | None = None

    def set_source(self, pm: QPixmap | None):
        self._src = pm
        self._key = None

    def get(self, w: int, h: int, dpr: float) -> QPixmap | None:
        if self._src is None or self._src.isNull():
            return None
        key = (self._src.cacheKey(), w, h, round(dpr, 3))
        if key != self._key:
            self._pm = hq_scaled(self._src, w, h, dpr)
            self._key = key
        return self._pm


def _draw_centered(p: QPainter, pm: QPixmap, w: int, h: int, dpr: float):
    """Нарисовать pixmap по центру, выровняв по физическим пикселям (чёткие края)."""
    dw, dh = pm.width() / dpr, pm.height() / dpr
    x = round((w - dw) * dpr / 2) / dpr
    y = round((h - dh) * dpr / 2) / dpr
    p.drawPixmap(QPointF(x, y), pm)


class IconLabel(QWidget):
    """Иконка фиксированного размера, которая всегда рисуется в разрешении экрана.

    Опционально обрезается скруглённым контуром (radius > 0).
    """

    def __init__(self, pixmap: QPixmap | None = None, size: int = 24, radius: int = 0, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._cache = _HiDpiPixmapCache()
        self._cache.set_source(pixmap)
        self.setFixedSize(size, size)

    def set_pixmap(self, pixmap: QPixmap | None):
        self._cache.set_source(pixmap)
        self.update()

    # совместимость с QLabel.setPixmap
    setPixmap = set_pixmap

    def paintEvent(self, _event):
        dpr = self.devicePixelRatioF()
        pm = self._cache.get(self.width(), self.height(), dpr)
        if pm is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self._radius > 0:
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
            p.setClipPath(path)
        _draw_centered(p, pm, self.width(), self.height(), dpr)


class RoundedPixmapLabel(IconLabel):
    """Иконка, обрезанная по скруглённому контуру (плитки экземпляров и профилей)."""

    def __init__(self, pixmap: QPixmap, size: int = 47, radius: int = 10, parent=None):
        super().__init__(pixmap, size=size, radius=radius, parent=parent)


class ProgressIcon(QWidget):
    """Иконка экземпляра, которая на время загрузки превращается в круговой прогресс.

    set_progress(False)        — показывает иконку;
    set_progress(True, 0.4)    — кольцо, заполненное на 40 %;
    set_progress(True, None)   — бегущее кольцо (размер загрузки неизвестен).
    """

    SIZE = 38
    ICON = 31
    RING = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._cache = _HiDpiPixmapCache()
        self._active = False
        self._value: float | None = None
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    def set_pixmap(self, pm: QPixmap):
        self._cache.set_source(pm)
        self.update()

    def set_progress(self, active: bool, value: float | None = None):
        self._active = bool(active)
        self._value = value
        spin = self._active and value is None
        if spin and not self._timer.isActive():
            self._timer.start()
        elif not spin and self._timer.isActive():
            self._timer.stop()
        self.update()

    def _tick(self):
        self._angle = (self._angle + 9) % 360
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self._active:
            dpr = self.devicePixelRatioF()
            pm = self._cache.get(self.ICON, self.ICON, dpr)
            if pm is not None:
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                _draw_centered(p, pm, self.width(), self.height(), dpr)
            return
        m = self.RING / 2 + 1
        rect = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        pen = QPen(QColor(BORDER_LIGHT), self.RING)
        p.setPen(pen)
        p.drawEllipse(rect)
        pen = QPen(QColor(GREEN), self.RING)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        if self._value is None:
            p.drawArc(rect, -self._angle * 16, -110 * 16)
        elif self._value > 0:
            # от верхней точки по часовой стрелке
            p.drawArc(rect, 90 * 16, -int(360 * 16 * min(self._value, 1.0)))


class ElidedLabel(QLabel):
    """Однострочная подпись, которая обрезается многоточием, а не растягивает карточку."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text
        self.setMinimumWidth(0)
        self._apply()

    def setText(self, text: str):
        self._full = text
        self._apply()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def _apply(self):
        w = max(0, self.width())
        shown = QFontMetrics(self.font()).elidedText(self._full, Qt.ElideRight, w) if w > 0 else self._full
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class NavButton(QPushButton):
    """Пункт бокового меню: иконка + подпись с настраиваемым расстоянием.

    Цвет текста всегда белый, активный пункт отличается только фоном.
    """

    def __init__(self, text: str, pixmap: QPixmap | None = None, icon_size: int = 24,
                 gap: int = GAP, pad: int = PAD, parent=None):
        super().__init__(parent)
        self.setObjectName("nav")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(pad, 0, pad, 0)
        lay.setSpacing(gap)
        self._icon = IconLabel(pixmap, size=icon_size)
        self._text = QLabel(text)
        self._text.setStyleSheet(f"color:{FG}; background: transparent;")
        for lb in (self._icon, self._text):
            lb.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self._icon, 0, Qt.AlignVCenter)
        lay.addWidget(self._text, 1, Qt.AlignVCenter)

    def set_active(self, active: bool):
        # Активный пункт больше не выделяется жирным — только фоном из темы
        pass


class IconButton(QPushButton):
    """Обычная кнопка с иконкой слева от текста и заданным расстоянием между ними.

    Стиль (фон, рамка, hover) берётся из темы по objectName, как у QPushButton.
    """

    def __init__(self, text: str, pixmap: QPixmap | None = None, icon_size: int = 20,
                 gap: int = GAP, pad: int = PAD, bold: bool = False, text_color: str = FG, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QPushButton { padding: 0; min-width: 0; }")  # поля задаёт layout ниже, без двойного отступа
        lay = QHBoxLayout(self)
        lay.setContentsMargins(pad, 0, pad, 0)
        lay.setSpacing(gap)
        lay.setAlignment(Qt.AlignCenter)
        self._icon = IconLabel(pixmap, size=icon_size)
        self._text = QLabel(text)
        self._text.setStyleSheet(f"color:{text_color}; background: transparent;" + (" font-weight:600;" if bold else ""))
        for lb in (self._icon, self._text):
            lb.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self._icon, 0, Qt.AlignVCenter)
        lay.addWidget(self._text, 0, Qt.AlignVCenter)

    def sizeHint(self):
        # QPushButton без собственного текста не знает ширину вложенного layout,
        # а min-width из темы перебивает setMinimumWidth — поэтому размер считаем сами.
        self._text.ensurePolished()
        hint = self.layout().sizeHint()
        return QSize(hint.width() + 4, max(super().sizeHint().height(), hint.height(), 36))

    def minimumSizeHint(self):
        return self.sizeHint()
