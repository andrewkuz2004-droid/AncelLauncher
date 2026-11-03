# -*- coding: utf-8 -*-
"""Единая тёмная тема и размеры контролов."""

BG = "#0a1520"
PANEL = "#0a1520"
FIELD = "#15222b"
FIELD_HOVER = "#1c2c36"
BORDER = "#1c2833"
BORDER_LIGHT = "#2a3a48"
FG = "#e8f0f2"
MUTED = "#8a9aa3"
GREEN = "#3ecf8a"
GREEN_MID = "#2a9e62"
GREEN_DEEP = "#143d28"
# Полупрозрачная подсветка активного пункта бокового меню (тот же зелёный, без «кнопочного» вида)
NAV_ACTIVE_BG = "rgba(62, 207, 138, 0.14)"
DANGER = "#e81123"

# Цвет точки рядом с названием загрузчика на карточке экземпляра
LOADER_COLORS = {
    "vanilla": "#6fcf4a",
    "forge": "#6fcf4a",
    "neoforge": "#b07cff",
    "fabric": "#4aa3ff",
    "quilt": "#c07cff",
}

from pathlib import Path as _Path

_ICONS = (_Path(__file__).resolve().parent.parent / "assets" / "icons").as_posix()
SPIN_UP = f"{_ICONS}/spin_up.svg"
SPIN_DOWN = f"{_ICONS}/spin_down.svg"
CHECK_MARK = f"{_ICONS}/check.svg"
ARROW_DOWN = f"{_ICONS}/arrow_down.svg"
ARROW_DOWN_OFF = f"{_ICONS}/arrow_down_disabled.svg"

# Единые размеры контролов
BTN_H = 36
FIELD_H = 36
RADIUS = 8

# ─── Шкала отступов (единая для ВСЕХ окон) ─────────────────────────────────
# Любые margins/spacing в коде берём только отсюда — «магических» чисел быть не должно.
PAD = 16          # внешние поля окна / страницы / панели; боковой отступ кнопок и вкладок
GAP = 10          # расстояние между элементами по умолчанию (в т.ч. иконка → текст)
GAP_SM = 6        # тесный промежуток: подпись → поле, точка → текст
GAP_XS = 2        # между пунктами списка
INNER = 8         # внутренний отступ: текстовые области, пункты списков, строки
INNER_SM = 4      # малый внутренний отступ: рамка списка, вертикаль строки
FIELD_PAD_X = 10  # горизонтальный отступ текста внутри полей/комбобоксов/спинбоксов

ZERO_MARGINS = (0, 0, 0, 0)

# ─── Единые размеры контролов (ширина/высота) ──────────────────────────────
FORM_LABEL_W = 140   # колонка подписей в формах: поля во всех окнах начинаются с одной линии
CTRL_W_SM = 130      # кнопки, короткие списки и спинбоксы фиксированной ширины
CTRL_W_LG = 280      # широкие одиночные поля (например, язык)
PROGRESS_H = 18      # высота полосы прогресса
# Страница внутри вкладки: верхний отступ уже даёт QTabWidget::pane (padding-top = GAP)
TAB_PAGE_MARGINS = ZERO_MARGINS
# Строка списка контента (мод/ресурспак/шейдер)
ROW_MARGINS = (INNER, INNER_SM, INNER, INNER_SM)

STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "Ubuntu", "Noto Sans", sans-serif;
    font-size: 13px;
    color: {FG};
}}
QMainWindow, QDialog {{
    background: {BG};
}}
QWidget {{
    background: {BG};
}}
QScrollArea, QScrollArea > QWidget > QWidget {{
    background: {BG};
    border: none;
}}
QAbstractScrollArea {{
    background: {BG};
    border: none;
}}
QLabel {{
    background: transparent;
    color: {FG};
}}
QLabel#muted {{
    color: {MUTED};
    font-size: 12px;
}}
QLabel#title {{
    font-size: 13px;
    font-weight: 600;
}}
QFrame#sidebar {{
    background: {BG};
    border-right: 1px solid {BORDER};
}}
QFrame#windowTopLine {{
    background: {BORDER};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}
QFrame#bottom {{
    background: {BG};
    border-top: 1px solid {BORDER};
}}
QPushButton {{
    background: {FIELD};
    color: {FG};
    border: 1px solid {BORDER_LIGHT};
    border-radius: {RADIUS}px;
    padding: 0 {PAD}px;
    min-height: {BTN_H}px;
    max-height: {BTN_H}px;
    min-width: 96px;
}}
QPushButton:hover {{
    background: {FIELD_HOVER};
}}
QPushButton:disabled {{
    color: {MUTED};
    background: {FIELD};
}}
QPushButton#accent {{
    background: {GREEN_MID};
    color: #ffffff;
    border: none;
    font-weight: 600;
}}
QPushButton#accent:hover {{
    background: {GREEN};
}}
QPushButton#accent:disabled {{
    background: #1a3d2e;
    color: #7a9a88;
}}
QPushButton#nav {{
    text-align: left;
    border: none;
    border-radius: {RADIUS}px;
    padding: 0;
    min-height: {BTN_H}px;
    max-height: {BTN_H}px;
    min-width: 0;
    background: transparent;
}}
QPushButton#nav:hover {{
    background: {FIELD};
}}
QPushButton#navActive {{
    text-align: left;
    border: none;
    border-radius: {RADIUS}px;
    padding: 0;
    min-height: {BTN_H}px;
    max-height: {BTN_H}px;
    min-width: 0;
    background: {NAV_ACTIVE_BG};
    color: {GREEN};
}}
QLineEdit, QComboBox, QSpinBox {{
    background: {FIELD};
    border: 1px solid {BORDER_LIGHT};
    border-radius: {RADIUS}px;
    padding: 0 {FIELD_PAD_X}px;
    min-height: {FIELD_H}px;
    max-height: {FIELD_H}px;
    selection-background-color: {GREEN_MID};
}}
QSpinBox {{
    padding: 0;
}}
QSpinBox QLineEdit {{
    background: transparent;
    border: none;
    padding: 0;
    margin: 0;
    min-height: 0;
    max-height: 16777215px;
    selection-background-color: {GREEN_MID};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border;
    width: 26px;
    border: none;
    background: transparent;
}}
QSpinBox::up-button {{
    subcontrol-position: top right;
    border-top-right-radius: {RADIUS - 1}px;
    margin: 2px 2px 0 0;
}}
QSpinBox::down-button {{
    subcontrol-position: bottom right;
    border-bottom-right-radius: {RADIUS - 1}px;
    margin: 0 2px 2px 0;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {FIELD_HOVER};
}}
QSpinBox::up-arrow {{
    image: url({SPIN_UP});
    width: 12px;
    height: 12px;
}}
QSpinBox::down-arrow {{
    image: url({SPIN_DOWN});
    width: 12px;
    height: 12px;
}}
QComboBox {{
    /* Справа место под стрелку уже резервирует ::drop-down — свой отступ справа не нужен,
       иначе область текста сужается дважды и надпись обрезается. */
    padding: 0 0 0 {FIELD_PAD_X}px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    background: transparent;
    width: 26px;
}}
QComboBox::down-arrow {{
    image: url({ARROW_DOWN});
    width: 10px;
    height: 6px;
}}
QComboBox::down-arrow:disabled {{
    image: url({ARROW_DOWN_OFF});
}}
QComboBox QAbstractItemView {{
    background: {BG};
    border: 1px solid {BORDER_LIGHT};
    selection-background-color: {GREEN_DEEP};
    outline: 0;
}}
QTextEdit, QTextBrowser, QListWidget {{
    background: {FIELD};
    border: 1px solid {BORDER_LIGHT};
    border-radius: {RADIUS}px;
    padding: {INNER}px;
    outline: 0;
}}
QTextBrowser {{
    color: {FG};
}}
QListWidget::item {{
    padding: {INNER}px;
    border-radius: 6px;
    color: {FG};
}}
QListWidget::item:hover {{
    background: {FIELD_HOVER};
}}
QListWidget::item:selected {{
    background: {NAV_ACTIVE_BG};
    color: {GREEN};
}}
QListWidget::item:selected:hover {{
    background: {NAV_ACTIVE_BG};
    color: {GREEN};
}}
QCheckBox {{
    spacing: {GAP}px;
    min-height: 22px;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid {BORDER_LIGHT};
    background: {FIELD};
}}
QCheckBox::indicator:hover {{
    border-color: {MUTED};
}}
QCheckBox::indicator:checked {{
    background: {GREEN_MID};
    border-color: {GREEN_MID};
    image: url({CHECK_MARK});
}}
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {BORDER};
    top: -1px;
    background: {BG};
    padding-top: {GAP}px;
}}
QTabBar {{
    background: transparent;
    border: none;
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: {INNER}px {PAD}px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 600;
    min-height: 28px;
}}
QTabBar::tab:selected {{
    color: {GREEN};
    border-bottom: 2px solid {GREEN};
}}
QTabBar::tab:hover {{
    color: {FG};
}}
QFrame#tile {{
    background: {FIELD};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 10px;
}}
QFrame#tile:hover {{
    background: {FIELD_HOVER};
}}
QFrame#tileSelected {{
    background: {FIELD_HOVER};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 10px;
}}
QToolButton#menuBtn {{
    background: transparent;
    border: none;
    color: {MUTED};
    padding: 0;
    font-size: 26px;
    font-weight: 700;
    min-width: 36px;
    max-width: 36px;
    min-height: 36px;
    max-height: 36px;
}}
QToolButton#menuBtn:hover {{
    color: {FG};
}}
QToolButton#menuBtn::menu-indicator {{
    image: none;
    width: 0px;
    height: 0px;
}}
QMenu {{
    background: #101c28;
    color: {FG};
    border: 1px solid {BORDER};
    border-radius: 0px;
    padding: 0px;
}}
QMenu::item {{
    background: transparent;
    color: {FG};
    padding: {GAP_SM}px {PAD}px;
    min-height: 18px;
    border-radius: 0px;
}}
QMenu::item:selected {{
    background: {NAV_ACTIVE_BG};
}}
QMenu::item:disabled {{
    color: {MUTED};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 0px;
}}
QDialogButtonBox QPushButton {{
    min-width: 96px;
    min-height: {BTN_H}px;
    max-height: {BTN_H}px;
}}
QFormLayout {{
    background: {BG};
}}
"""


# Список контента в диалогах «Настройка экземпляра» / «Скачать контент» (один стиль на оба окна)
CONTENT_LIST_QSS = f"""
QListWidget#installedContentList, QListWidget#downloadContentList {{
    background: #111f29; border: 1px solid #263b49; border-radius: 10px;
    padding: {INNER_SM}px; outline: 0;
}}
QListWidget#installedContentList::item,
QListWidget#downloadContentList::item {{
    padding: 0px;
    margin: 0px;
    border-radius: 6px;
}}
QWidget#installedContentRow, QWidget#downloadContentRow {{
    background: transparent;
    border: none;
}}
QLabel#contentRowName {{ color: #e5edf3; font-size: 13px; }}
QLabel#contentRowMeta {{ color: #8ea6b8; font-size: 12px; }}
QLabel#contentRowIcon {{ background: transparent; }}
QCheckBox#contentEnabledCheck {{ spacing: 0; padding: 0; }}
QCheckBox#contentEnabledCheck::indicator {{
    width: 18px; height: 18px; border-radius: 4px;
}}
"""


def apply_dark_titlebar(widget) -> None:
    """Тёмный заголовок + рамка цвета разделителя сайдбара (Windows 10/11)."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        # Dark mode
        dark = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark))

        # COLORREF: 0x00BBGGRR
        # Заголовок = BG (#0a1520)
        caption = ctypes.c_int(0x0020150A)
        try:
            dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))  # DWMWA_CAPTION_COLOR
        except Exception:
            pass
        # Рамка окна = BORDER (#1c2833) — тот же цвет, что у полоски сайдбара
        border = ctypes.c_int(0x0033281C)
        try:
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))  # DWMWA_BORDER_COLOR
        except Exception:
            pass
        # DWMWA_TEXT_COLOR = 36 — светлый текст
        text_c = ctypes.c_int(0x00F2F0E8)
        try:
            dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text_c), ctypes.sizeof(text_c))
        except Exception:
            pass
    except Exception:
        pass


def ask_yes_no(parent, title: str, text: str) -> bool:
    """Реэкспорт из ui_qt.core (единый диалог)."""
    from ui_qt.core import ask_yes_no as _fn
    return _fn(parent, title, text)


def show_warning(parent, title: str, text: str) -> None:
    """Реэкспорт из ui_qt.core (единый диалог)."""
    from ui_qt.core import show_warning as _fn
    _fn(parent, title, text)


def apply_app_palette(app) -> None:
    from PySide6.QtGui import QPalette, QColor
    from PySide6.QtCore import Qt

    pal = QPalette()
    bg = QColor(BG)
    fg = QColor(FG)
    muted = QColor(MUTED)
    field = QColor(FIELD)
    for role in (
        QPalette.Window,
        QPalette.Base,
        QPalette.AlternateBase,
        QPalette.Button,
        QPalette.ToolTipBase,
    ):
        pal.setColor(role, bg)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.ToolTipText):
        pal.setColor(role, fg)
    pal.setColor(QPalette.PlaceholderText, muted)
    pal.setColor(QPalette.Highlight, QColor(GREEN_MID))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.Light, field)
    pal.setColor(QPalette.Mid, QColor(BORDER))
    pal.setColor(QPalette.Dark, bg)
    app.setPalette(pal)
