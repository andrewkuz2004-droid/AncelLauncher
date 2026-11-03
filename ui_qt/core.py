# -*- coding: utf-8 -*-
"""Ядро интерфейса AncelLauncher.

Единые компоненты с общим дизайном — чтобы не дублировать разметку
и стили в каждом окне. Импортируйте отсюда вместо «ручной» сборки виджетов.

Компоненты:
  - токены дизайна (цвета, отступы) — реэкспорт из theme
  - AppDialog          — диалог с тёмным заголовком и полоской
  - build_dialog_body  — каркас для обычных QDialog (полоска + единые отступы)
  - top_line()         — полоска под заголовком окна
  - Sidebar            — боковая панель
  - Field              — поле ввода
  - Dropdown           — выпадающий список
  - ContextMenu        — контекстное меню
  - ItemList           — список пунктов
  - Check              — галочка
  - Card               — карточка экземпляра/профиля
  - RoundedIcon        — иконка в квадрате со скруглением
  - Button / AccentButton
  - Tabs               — вкладки с нижней полоской
  - ask_yes_no / show_warning
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize, QPointF, QRectF
from PySide6.QtGui import QAction, QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# ─── Дизайн-токены (единый источник) ───────────────────────────────────────
from ui_qt.theme import (  # noqa: F401
    BG,
    BORDER,
    BORDER_LIGHT,
    BTN_H,
    DANGER,
    FIELD,
    FIELD_H,
    CONTENT_LIST_QSS,
    CTRL_W_LG,
    CTRL_W_SM,
    FIELD_HOVER,
    FORM_LABEL_W,
    FIELD_PAD_X,
    FG,
    GAP,
    GAP_SM,
    GAP_XS,
    GREEN,
    GREEN_DEEP,
    GREEN_MID,
    INNER,
    INNER_SM,
    LOADER_COLORS,
    MUTED,
    NAV_ACTIVE_BG,
    PAD,
    PANEL,
    PROGRESS_H,
    RADIUS,
    ROW_MARGINS,
    STYLESHEET,
    TAB_PAGE_MARGINS,
    ZERO_MARGINS,
    apply_dark_titlebar,
)

# Низкоуровневые виджеты (иконки, навигация)
from ui_qt.widgets import (  # noqa: F401
    ElidedLabel,
    IconButton,
    IconLabel,
    NavButton,
    ProgressIcon,
    RoundedPixmapLabel,
    hq_scaled,
)


# ─── Полоска под заголовком ────────────────────────────────────────────────

def top_line(parent=None) -> QFrame:
    """Тонкая полоска от края до края — под системным заголовком окна."""
    line = QFrame(parent)
    line.setObjectName("windowTopLine")
    line.setFixedHeight(1)
    line.setFrameShape(QFrame.NoFrame)
    return line


# ─── Диалоговое окно ───────────────────────────────────────────────────────

class AppDialog(QDialog):
    """Базовый диалог: стиль темы, тёмный title bar, полоска под заголовком.

    Использование::

        dlg = AppDialog(parent, "Заголовок", min_size=(400, 280))
        body = dlg.body_layout()   # сюда кладём контент
        body.addWidget(...)
        dlg.exec()
    """

    def __init__(
        self,
        parent=None,
        title: str = "",
        min_size: tuple[int, int] | None = None,
        size: tuple[int, int] | None = None,
        body_margins: tuple[int, int, int, int] | None = None,
    ):
        super().__init__(parent)
        if title:
            self.setWindowTitle(title)
        self.setStyleSheet(STYLESHEET)
        if min_size:
            self.setMinimumSize(*min_size)
        if size:
            self.resize(*size)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(top_line())

        self._body = QWidget()
        m = body_margins if body_margins is not None else (PAD, PAD, PAD, PAD)
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(*m)
        self._body_lay.setSpacing(GAP)
        root.addWidget(self._body, 1)

    def body_layout(self) -> QVBoxLayout:
        return self._body_lay

    def body_widget(self) -> QWidget:
        return self._body

    def showEvent(self, event):
        super().showEvent(event)
        apply_dark_titlebar(self)


def apply_dialog_chrome(dlg: QDialog, with_top_line: bool = True) -> QVBoxLayout:
    """Обернуть уже созданный QDialog: стиль, полоска, body-layout.

    Возвращает layout тела (контент). Вызывать в начале __init__ до виджетов.
    """
    dlg.setStyleSheet(STYLESHEET)
    root = QVBoxLayout(dlg)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    if with_top_line:
        root.addWidget(top_line())
    body = QWidget()
    body_lay = QVBoxLayout(body)
    body_lay.setContentsMargins(PAD, PAD, PAD, PAD)
    body_lay.setSpacing(GAP)
    root.addWidget(body, 1)

    # тёмный заголовок при показе
    _orig = getattr(dlg, "showEvent", None)

    def _show(event, _o=_orig, _d=dlg):
        if _o and _o.__func__ is not QDialog.showEvent:
            try:
                _o(event)
            except TypeError:
                QDialog.showEvent(_d, event)
        else:
            QDialog.showEvent(_d, event)
        apply_dark_titlebar(_d)

    dlg.showEvent = _show  # type: ignore
    return body_lay


def build_dialog_body(dlg: QDialog, tabbed: bool = False) -> QVBoxLayout:
    """Единый каркас окна: полоска под заголовком + тело с отступами PAD.

    tabbed=True  — окно с вкладками: вкладки идут сразу под полоской (верхний отступ 0).
    tabbed=False — обычное окно: PAD со всех сторон.
    Стиль темы применяется здесь же. Возвращает layout тела.
    """
    dlg.setStyleSheet(dlg.styleSheet() or STYLESHEET)
    root = QVBoxLayout(dlg)
    root.setContentsMargins(*ZERO_MARGINS)
    root.setSpacing(0)
    root.addWidget(top_line())
    body = QWidget()
    lay = QVBoxLayout(body)
    lay.setContentsMargins(PAD, 0 if tabbed else PAD, PAD, PAD)
    lay.setSpacing(GAP)
    root.addWidget(body, 1)
    return lay


# ─── Боковое меню ──────────────────────────────────────────────────────────

class Sidebar(QFrame):
    """Боковая панель с objectName=sidebar (стиль из STYLESHEET)."""

    def __init__(self, parent=None, width: int = 200):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(width)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(PAD, PAD, PAD, PAD)
        self._lay.setSpacing(GAP)

    def layout(self) -> QVBoxLayout:  # type: ignore[override]
        return self._lay


# ─── Поле ввода ────────────────────────────────────────────────────────────

class Field(QLineEdit):
    """Однострочное поле (высота и стиль из темы)."""

    def __init__(self, text: str = "", placeholder: str = "", parent=None):
        super().__init__(text, parent)
        if placeholder:
            self.setPlaceholderText(placeholder)


# ─── Выпадающий список ─────────────────────────────────────────────────────

class Dropdown(QComboBox):
    """Выпадающий список единого стиля."""

    def __init__(self, parent=None, items: list | None = None):
        super().__init__(parent)
        if items:
            for it in items:
                if isinstance(it, tuple):
                    self.addItem(it[0], it[1] if len(it) > 1 else None)
                else:
                    self.addItem(str(it))


# ─── Контекстное / выпадающее меню ─────────────────────────────────────────

class ContextMenu(QMenu):
    """Меню с действиями [(подпись, callback), ...] или [(подпись, callback, danger)]."""

    def __init__(self, parent=None, actions: list | None = None):
        super().__init__(parent)
        if actions:
            for item in actions:
                if item is None:
                    self.addSeparator()
                    continue
                if len(item) == 2:
                    label, cb = item
                else:
                    label, cb = item[0], item[1]
                act = QAction(label, self)
                if cb:
                    act.triggered.connect(cb)
                self.addAction(act)


# ─── Список пунктов ────────────────────────────────────────────────────────

class ItemList(QListWidget):
    """Список с единым стилем (контент, сборки, …)."""

    def __init__(self, parent=None, object_name: str = ""):
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.setUniformItemSizes(True)
        self.setSpacing(GAP_XS)


# ─── Галочка ───────────────────────────────────────────────────────────────

class Check(QCheckBox):
    """Чекбокс единого стиля (индикатор 18px из STYLESHEET)."""

    def __init__(self, text: str = "", checked: bool = False, parent=None):
        super().__init__(text, parent)
        self.setChecked(checked)


# ─── Иконка со скруглением ─────────────────────────────────────────────────

class RoundedIcon(QWidget):
    """Квадратная иконка со скруглёнными углами (голова игрока, мод, экземпляр).

    pad > 0  — иконка по центру с отступом;
    fill     — на всю подложку (голова профиля).
    """

    def __init__(
        self,
        pixmap: QPixmap | None = None,
        size: int = 47,
        pad: int = 5,
        radius: int = 10,
        fill: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pad = 0 if fill else pad
        self._radius = radius
        self._fill = fill
        self._pm = pixmap if pixmap and not pixmap.isNull() else QPixmap()

    def set_pixmap(self, pixmap: QPixmap | None):
        self._pm = pixmap if pixmap and not pixmap.isNull() else QPixmap()
        self.update()

    setPixmap = set_pixmap

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        p.fillPath(path, QColor(FIELD))
        if self._pm.isNull():
            p.setPen(QPen(QColor(BORDER_LIGHT), 1))
            p.drawPath(path)
            return
        p.setClipPath(path)
        dpr = self.devicePixelRatioF()
        if self._fill:
            target = max(1, int(self.width() * dpr))
            scaled = self._pm.scaled(target, target, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            p.drawPixmap(QPointF(0, 0), scaled)
        else:
            inner = self.width() - 2 * self._pad
            if inner < 1:
                return
            scaled = self._pm.scaled(
                int(inner * dpr), int(inner * dpr),
                Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            dw = scaled.width() / dpr
            dh = scaled.height() / dpr
            x = (self.width() - dw) / 2
            y = (self.height() - dh) / 2
            p.drawPixmap(QPointF(x, y), scaled)
        p.setClipping(False)
        p.setPen(QPen(QColor(BORDER_LIGHT), 1))
        p.drawPath(path)


# ─── Кнопки ────────────────────────────────────────────────────────────────

class Button(QPushButton):
    """Обычная кнопка (стиль QPushButton из темы)."""

    def __init__(self, text: str = "", parent=None, on_click=None):
        super().__init__(text, parent)
        if on_click:
            self.clicked.connect(on_click)


class AccentButton(QPushButton):
    """Зелёная акцентная кнопка (objectName=accent)."""

    def __init__(self, text: str = "", parent=None, on_click=None):
        super().__init__(text, parent)
        self.setObjectName("accent")
        if on_click:
            self.clicked.connect(on_click)


# ─── Вкладки с полоской ────────────────────────────────────────────────────

class Tabs(QTabWidget):
    """Вкладки с нижней полоской (стиль QTabBar из STYLESHEET)."""

    def __init__(self, parent=None):
        super().__init__(parent)


# ─── Карточка (экземпляр / профиль) ────────────────────────────────────────

class Card(QFrame):
    """Карточка фиксированного размера для страниц «Экземпляры» и «Профили»."""

    WIDTH = 280
    HEIGHT = 78

    def __init__(
        self,
        title: str,
        line1: str,
        line2: str,
        pixmap: QPixmap,
        selected: bool,
        on_click,
        menu_actions: list,
        parent=None,
        dot_color: str = "",
        fill_icon: bool = False,
    ):
        super().__init__(parent)
        self.setObjectName("tileSelected" if selected else "tile")
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self._on_click = on_click

        icon_slot = RoundedIcon(pixmap, size=47, pad=5, radius=10, fill=fill_icon)

        t = ElidedLabel(title)
        t.setObjectName("title")
        l1 = ElidedLabel(line1)
        l1.setObjectName("muted")
        l2 = ElidedLabel(line2)
        l2.setObjectName("muted")

        l2_row = QHBoxLayout()
        l2_row.setContentsMargins(0, 0, 0, 0)
        l2_row.setSpacing(GAP_SM)
        if dot_color:
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background:{dot_color}; border-radius:5px;")
            l2_row.addWidget(dot, 0, Qt.AlignVCenter)
        l2_row.addWidget(l2, 1)

        col = QVBoxLayout()
        col.setSpacing(GAP_XS)
        col.setContentsMargins(0, 0, 0, 0)
        col.addStretch()
        col.addWidget(t)
        col.addWidget(l1)
        col.addLayout(l2_row)
        col.addStretch()

        menu_btn = QToolButton()
        menu_btn.setObjectName("menuBtn")
        menu_btn.setText("⋮")
        menu_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        menu_btn.setArrowType(Qt.NoArrow)
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        menu_btn.setAutoRaise(True)
        menu_btn.setFixedSize(BTN_H, BTN_H)
        menu = ContextMenu(menu_btn)
        for item in menu_actions:
            if len(item) >= 2:
                label, cb = item[0], item[1]
                act = QAction(label, menu)
                act.triggered.connect(cb)
                menu.addAction(act)
        menu_btn.setMenu(menu)
        menu_btn.mousePressEvent = lambda e, b=menu_btn: QToolButton.mousePressEvent(b, e)

        row = QHBoxLayout(self)
        row.setContentsMargins(PAD, GAP_SM, INNER, GAP_SM)
        row.setSpacing(GAP)
        row.addWidget(icon_slot, 0, Qt.AlignVCenter)
        row.addLayout(col, 1)
        row.addWidget(menu_btn, 0, Qt.AlignVCenter)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._on_click:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            child = self.childAt(pos)
            if child is not None and isinstance(child, QToolButton):
                return
            self._on_click()
        super().mousePressEvent(event)


# Алиас для совместимости со старым именем
Tile = Card


# ─── Простые диалоги (Да/Нет, предупреждение) ──────────────────────────────

def _simple_dialog(parent, title: str, text: str, buttons: list[tuple[str, int]]) -> int:
    dlg = AppDialog(parent, title, min_size=(340, 0))
    lay = dlg.body_layout()

    label = QLabel(text)
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    lay.addWidget(label)

    row = QHBoxLayout()
    row.setSpacing(GAP)
    row.addStretch(1)
    result = {"code": 0}

    def _handler(code):
        def _run():
            result["code"] = code
            dlg.accept()
        return _run

    for i, (caption, code) in enumerate(buttons):
        btn = AccentButton(caption) if i == 0 else Button(caption)
        if i == 0:
            btn.setDefault(True)
        btn.clicked.connect(_handler(code))
        row.addWidget(btn)
    lay.addLayout(row)
    dlg.exec()
    return result["code"]


def ask_yes_no(parent, title: str, text: str) -> bool:
    """Вопрос Да/Нет. True = Да."""
    return _simple_dialog(parent, title, text, [("Да", 1), ("Нет", 2)]) == 1


def show_warning(parent, title: str, text: str) -> None:
    """Предупреждение с кнопкой ОК."""
    _simple_dialog(parent, title, text, [("ОК", 1)])
