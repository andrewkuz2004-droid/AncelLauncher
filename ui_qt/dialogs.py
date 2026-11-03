# -*- coding: utf-8 -*-
"""Отдельные окна: создание, профиль, настройки экземпляра, контент, статус."""

from __future__ import annotations

import hashlib
import re
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, Qt, QSize, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap, QTextOption
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

from ui_qt.core import (
    STYLESHEET,
    PAD,
    GAP,
    GAP_SM,
    GAP_XS,
    FIELD_PAD_X,
    ROW_MARGINS,
    TAB_PAGE_MARGINS,
    ZERO_MARGINS,
    CONTENT_LIST_QSS,
    CTRL_W_SM,
    FORM_LABEL_W,
    PROGRESS_H,
    build_dialog_body,
    FIELD_HOVER,
    apply_dark_titlebar,
    ask_yes_no,
    show_warning,
    hq_scaled,
    IconButton,
    AppDialog,
    top_line,
    AccentButton,
    Button,
    Field,
    Dropdown,
    Tabs,
    ItemList,
    Check,
    ContextMenu,
    RoundedIcon,
)

LIST_ICON = 40


class RemoteIcons(QObject):
    """Фоновая загрузка иконок по URL (Modrinth) с кэшем на диске.

    Скачивание идёт в потоках, QPixmap создаётся только в главном потоке.
    Когда иконка готова — сигнал ready(url).
    """

    ready = Signal(str)
    _fetched = Signal(str, object)

    def __init__(self, parent=None, size: int = LIST_ICON):
        super().__init__(parent)
        self._size = size
        self._icons: dict[str, QIcon] = {}
        self._pending: set[str] = set()
        self._pool = ThreadPoolExecutor(max_workers=6)
        self._cache_dir = Path(tempfile.gettempdir()) / "AncelLauncher_icons"
        self._fetched.connect(self._on_fetched)
        self.placeholder = self._make_placeholder()

    @staticmethod
    def _dpr() -> float:
        """Максимальный масштаб среди экранов — иконка будет чёткой на любом из них."""
        try:
            return max([1.0] + [float(sc.devicePixelRatio()) for sc in QGuiApplication.screens()])
        except Exception:
            return 1.0

    def _rounded(self, pm: QPixmap) -> QPixmap:
        s, dpr = self._size, self._dpr()
        pm = hq_scaled(pm, s, s, dpr)  # сразу в физических пикселях экрана
        out = QPixmap(round(s * dpr), round(s * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(0, 0, s, s, 8, 8)
        p.setClipPath(path)
        p.drawPixmap(QPointF(round((s - pm.width() / dpr) * dpr / 2) / dpr,
                             round((s - pm.height() / dpr) * dpr / 2) / dpr), pm)
        p.end()
        return out

    def _make_placeholder(self) -> QIcon:
        s, dpr = self._size, self._dpr()
        pm = QPixmap(round(s * dpr), round(s * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(FIELD_HOVER))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, s, s, 8, 8)
        p.end()
        return QIcon(pm)

    def get(self, url: str) -> QIcon:
        """Готовая иконка или заглушка; если иконки ещё нет — запускает загрузку."""
        if not url:
            return self.placeholder
        icon = self._icons.get(url)
        if icon is not None:
            return icon
        if url not in self._pending:
            self._pending.add(url)
            self._pool.submit(self._fetch, url)
        return self.placeholder

    def _fetch(self, url: str):
        data = None
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            f = self._cache_dir / hashlib.sha1(url.encode("utf-8")).hexdigest()
            if f.is_file() and 0 < f.stat().st_size <= 8 * 1024 * 1024:
                cached = f.read_bytes()
                probe = QPixmap()
                if probe.loadFromData(cached):
                    data = cached
                else:
                    # Не оставляем повреждённый кэш: иначе заглушка будет вечной.
                    f.unlink(missing_ok=True)
            if data is None:
                req = urllib.request.Request(url, headers={"User-Agent": "AncelLauncher/1.0", "Accept": "image/*"})
                with urllib.request.urlopen(req, timeout=12) as r:
                    content_type = (r.headers.get("Content-Type") or "").lower()
                    data = r.read(8 * 1024 * 1024 + 1)
                    if len(data) > 8 * 1024 * 1024 or (content_type and not content_type.startswith("image/")):
                        data = None
                if data:
                    probe = QPixmap()
                    if probe.loadFromData(data):
                        f.write_bytes(data)
                    else:
                        data = None
        except Exception:
            data = None
        try:
            self._fetched.emit(url, data)
        except RuntimeError:
            pass  # окно уже закрыто

    def _on_fetched(self, url: str, data):
        self._pending.discard(url)
        pm = QPixmap()
        if data and pm.loadFromData(data):
            self._icons[url] = QIcon(self._rounded(pm))
            self.ready.emit(url)


def _apply_icon(list_widget: QListWidget, icons: RemoteIcons, url: str):
    """Подставляет готовую иконку во все строки списка с этим icon_url."""
    icon = icons._icons.get(url)
    if icon is None:
        return
    for i in range(list_widget.count()):
        item = list_widget.item(i)
        if ((item.data(Qt.UserRole) or {}).get("icon_url") or "") == url:
            item.setIcon(icon)


def _setup_dialog(dlg: QDialog, title: str, w: int, h: int):
    dlg.setWindowTitle(title)
    dlg.setMinimumSize(w - 40, h - 40)
    dlg.resize(w, h)
    dlg.setStyleSheet(STYLESHEET)
    layout = dlg.layout()
    if layout is not None:
        layout.setContentsMargins(PAD, PAD, PAD, PAD)
        layout.setSpacing(GAP)




def _form_defaults(form: QFormLayout, margins=ZERO_MARGINS) -> None:
    """Единые отступы для QFormLayout: промежутки GAP, поля — как у остальных страниц."""
    form.setContentsMargins(*margins)
    form.setHorizontalSpacing(GAP)
    form.setVerticalSpacing(GAP)


ICON_PX = 56  # логический размер иконки в превью
_ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"


def is_release_version(mc: str) -> bool:
    """Релиз вида 1.20.1 / 26.1; всё остальное (снапшоты, пре-релизы, старые версии) — «не релиз»."""
    return bool(re.fullmatch(r"\d+\.\d+(\.\d+)?", (mc or "").strip()))


def default_instance_icon(kind: str, mc: str) -> Path:
    """Загрузчик → его иконка; Vanilla → блок травы (релиз) или блок грязи (снапшот и старые версии)."""
    kind = (kind or "").lower()
    if kind in ("forge", "fabric", "quilt", "neoforge"):
        name = f"loader_{kind}.png"
    elif not (mc or "").strip() or is_release_version(mc):
        name = "loader_vanilla.png"
    else:
        name = "loader_snapshot.png"
    path = _ICON_DIR / name
    return path if path.is_file() else _ICON_DIR / "vanilla_48.png"


class _InstanceFormSignals(QObject):
    versions_ready = Signal(object)
    loaders_ready = Signal(object, object)  # data, previous_kind
    loader_versions_ready = Signal(object)


class _PackSearchSignals(QObject):
    ready = Signal(object, int)  # data, req


class InstanceForm(QWidget):
    """Форма экземпляра: название, версия, загрузчик, память.

    Одна и та же вкладка используется в окне создания («Свой экземпляр»)
    и в окне настройки («Экземпляр»), поэтому выглядят они одинаково.
    """

    def __init__(self, api, inst: dict | None = None, parent=None):
        super().__init__(parent)
        self.api = api
        self._editing = inst is not None
        inst = inst or {}
        self._icon_path = (inst.get("icon_path") or "").strip()
        self._pending_mc = (inst.get("mc") or "").strip() or None
        self._pending_kind = ((inst.get("kind") or "").lower() or "") if self._editing else None
        self._pending_loader_ver = (inst.get("loader_version") or "").strip() or None
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._signals = _InstanceFormSignals(self)
        self._signals.versions_ready.connect(self._apply_versions)
        self._signals.loaders_ready.connect(self._apply_loaders)
        self._signals.loader_versions_ready.connect(self._apply_loader_versions)
        self._versions_req = 0
        self._loaders_req = 0
        self._loader_ver_req = 0

        # ---------- Свой экземпляр ----------
        cv = QVBoxLayout(self)
        cv.setContentsMargins(*TAB_PAGE_MARGINS)
        cv.setSpacing(GAP)

        # Ширина под самую длинную подпись «Версия загрузчика»

        def _left_label(text: str) -> QLabel:
            lab = QLabel(text)
            lab.setFixedWidth(FORM_LABEL_W)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            return lab

        ICON_SIZE = 88

        # Иконка: высота ≈ две строки полей (Название + Версия), прижата вправо
        self.icon_preview = QLabel()
        self.icon_preview.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.icon_preview.setAlignment(Qt.AlignCenter)
        self.icon_preview.setStyleSheet(
            "background:#15222b; border:1px solid #2a3a48; border-radius:10px;"
        )
        self.icon_preview.setContextMenuPolicy(Qt.CustomContextMenu)
        self.icon_preview.customContextMenuRequested.connect(self._on_icon_context_menu)

        self.btn_icon = QPushButton("Выбрать иконку")
        self.btn_icon.setFixedWidth(CTRL_W_SM)
        self.btn_icon.clicked.connect(self._pick_icon)

        from PySide6.QtWidgets import QGridLayout
        top = QGridLayout()
        top.setHorizontalSpacing(GAP_SM)  # подпись → поле
        top.setVerticalSpacing(GAP)
        top.setColumnStretch(1, 1)

        # row 0: Название | поле | иконка (rowspan 2)
        top.addWidget(_left_label("Название"), 0, 0)
        self.name = QLineEdit("Новый экземпляр")
        top.addWidget(self.name, 0, 1, 1, 2)  # поле на 2 колонки (под версию+кнопку)
        top.addWidget(self.icon_preview, 0, 3, 2, 1, Qt.AlignTop | Qt.AlignRight)

        # row 1: Версия | короткое поле | кнопка иконки
        top.addWidget(_left_label("Версия"), 1, 0)
        self.mc = QComboBox()
        top.addWidget(self.mc, 1, 1)
        top.addWidget(self.btn_icon, 1, 2, Qt.AlignLeft)

        # row 2: снапшоты
        top.addWidget(_left_label(""), 2, 0)
        self.show_all = QCheckBox("Снапшоты и старые версии")
        top.addWidget(self.show_all, 2, 1, 1, 2)
        cv.addLayout(top)

        # Загрузчик — поле на всю ширину до правого края
        loader_row = QHBoxLayout()
        loader_row.setSpacing(GAP_SM)  # как horizontalSpacing у верхней сетки — левый край полей совпадает
        loader_row.addWidget(_left_label("Загрузчик"))
        self.loader = QComboBox()
        self.loader.addItem("Vanilla", "")
        self.loader.setEnabled(False)
        loader_row.addWidget(self.loader, 1)
        cv.addLayout(loader_row)

        self.loader_ver_label = _left_label("Версия загрузчика")
        self.loader_ver = QComboBox()
        self.loader_ver.setEnabled(False)
        loader_ver_row = QHBoxLayout()
        loader_ver_row.setContentsMargins(*ZERO_MARGINS)
        loader_ver_row.setSpacing(GAP_SM)
        loader_ver_row.addWidget(self.loader_ver_label)
        loader_ver_row.addWidget(self.loader_ver, 1)
        self._loader_ver_row_widget = QWidget()
        self._loader_ver_row_widget.setLayout(loader_ver_row)
        self._loader_ver_row_widget.setVisible(False)
        cv.addWidget(self._loader_ver_row_widget)

        # Память — без растягивания полей на всю ширину
        self.mem_check = QCheckBox("Память")
        self.mem_check.setChecked(False)
        cv.addWidget(self.mem_check)

        self.mem_panel = QWidget()
        mem_lay = QVBoxLayout(self.mem_panel)
        mem_lay.setContentsMargins(*ZERO_MARGINS)
        mem_lay.setSpacing(GAP)

        def _mem_spin(default: int, lo: int = 128, hi: int = 65536) -> QSpinBox:
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setSingleStep(128)
            s.setValue(default)
            s.setSuffix(" MiB")
            s.setFixedWidth(CTRL_W_SM)
            s.lineEdit().setTextMargins(FIELD_PAD_X, 0, 0, 0)
            return s

        self.ram_min = _mem_spin(512)
        self.ram_max = _mem_spin(4096)
        self.ram_perm = _mem_spin(128, 64, 2048)

        def _mem_row(label: str, spin: QSpinBox, hint: str) -> QWidget:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(*ZERO_MARGINS)
            h.setSpacing(GAP_SM)
            lab = QLabel(label)
            lab.setFixedWidth(FORM_LABEL_W)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            h.addWidget(lab)
            h.addWidget(spin, 0)
            tip = QLabel(hint)
            tip.setObjectName("muted")
            h.addWidget(tip)
            h.addStretch(1)
            return w

        mem_lay.addWidget(_mem_row("Мин. память", self.ram_min, "(-Xms)"))
        mem_lay.addWidget(_mem_row("Макс. память", self.ram_max, "(-Xmx)"))
        mem_lay.addWidget(_mem_row("PermGen", self.ram_perm, "(-XX:PermSize)"))
        # Всегда видно; при снятой галочке — неактивно и затемнено
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        self._mem_fade = QGraphicsOpacityEffect(self.mem_panel)
        self.mem_panel.setGraphicsEffect(self._mem_fade)
        self.mem_check.toggled.connect(self._on_mem_toggled)
        self._on_mem_toggled(False)
        cv.addWidget(self.mem_panel)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        cv.addWidget(self.status)
        cv.addStretch()

        # ---------- значения существующего экземпляра ----------
        if self._editing:
            self.name.setText(inst.get("name") or "")
            if self._pending_mc and not is_release_version(self._pending_mc):
                self.show_all.setChecked(True)
            try:
                ram = max(1, min(32, int(inst.get("ram_gb") or 4)))
            except (TypeError, ValueError):
                ram = 4
            if ram != 4:
                self.ram_max.setValue(ram * 1024)
                self.mem_check.setChecked(True)

        self.show_all.toggled.connect(self._load_versions)
        self.mc.currentIndexChanged.connect(self._load_loaders)
        self.loader.currentIndexChanged.connect(self._on_loader_changed)
        # Откладываем сетевые запросы, чтобы окно открылось сразу
        self.status.setText("Загрузка версий…")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._load_versions)

    # ----- иконка -----
    def _show_icon(self, pm: QPixmap):
        """Показать pixmap в превью с учётом масштаба экрана (чётко, без «мыла»)."""
        if pm.isNull():
            self.icon_preview.clear()
            return
        self.icon_preview.setPixmap(hq_scaled(pm, ICON_PX, ICON_PX, self.devicePixelRatioF()))

    def _refresh_default_icon(self):
        """Своя иконка, если выбрана; иначе иконка загрузчика / трава (релиз) / грязь (снапшот)."""
        if not hasattr(self, "loader") or not hasattr(self, "mc"):
            return
        if self._icon_path:
            pm = QPixmap(self._icon_path)
            if not pm.isNull():
                self._show_icon(pm)
                return
            self._icon_path = ""
        path = default_instance_icon(self.loader.currentData() or "", self.mc.currentData() or "")
        self._show_icon(QPixmap(str(path)))

    def _reset_icon(self):
        self._icon_path = ""
        self._refresh_default_icon()

    def _on_icon_context_menu(self, pos):
        menu = QMenu(self)
        act = QAction("Выбрать иконку", menu)
        act.triggered.connect(self._pick_icon)
        menu.addAction(act)
        if self._icon_path:
            act_reset = QAction("Сбросить иконку", menu)
            act_reset.triggered.connect(self._reset_icon)
            menu.addAction(act_reset)
        menu.exec(self.icon_preview.mapToGlobal(pos))

    def _pick_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Иконка экземпляра", "", "Изображения (*.png *.jpg *.jpeg *.webp);;Все файлы (*.*)"
        )
        if not path:
            return
        pm = QPixmap(path)
        if pm.isNull():
            self.status.setText("Не удалось открыть изображение")
            return
        self._icon_path = path
        self._show_icon(pm)

    def _on_mem_toggled(self, enabled: bool):
        self.mem_panel.setEnabled(enabled)
        self._mem_fade.setOpacity(1.0 if enabled else 0.38)

    def _on_loader_changed(self, *_):
        self._refresh_default_icon()
        self._load_loader_versions()

    # ----- версии и загрузчики (сетевые запросы в фоне) -----
    def _load_versions(self):
        self.status.setText("Загрузка версий…")
        self.mc.setEnabled(False)
        self.loader.setEnabled(False)
        self.loader_ver.setEnabled(False)
        show_all = self.show_all.isChecked()
        self._versions_req += 1
        req = self._versions_req

        def work():
            data = self.api.list_mc_versions(show_all) or {}
            if req == self._versions_req:
                self._signals.versions_ready.emit(data)

        self._pool.submit(work)

    def _apply_versions(self, data):
        self.mc.blockSignals(True)
        self.mc.clear()
        versions = data.get("versions") or []
        for v in versions:
            self.mc.addItem(v["id"], v["id"])
        latest = data.get("latest") or ""
        # при редактировании сначала выбираем версию самого экземпляра
        pending = self._pending_mc
        self._pending_mc = None
        idx = self.mc.findData(pending or latest)
        if idx < 0 and pending:
            self.mc.insertItem(0, pending, pending)
            idx = 0
        if idx < 0:
            idx = self.mc.findData(latest)
        if idx >= 0:
            self.mc.setCurrentIndex(idx)
        elif self.mc.count():
            self.mc.setCurrentIndex(0)
        self.mc.blockSignals(False)
        self.mc.setEnabled(True)
        if data.get("error"):
            self.status.setText(f"Ошибка версий: {data['error']}")
        elif not versions:
            self.status.setText("Список версий пуст — проверьте интернет")
        else:
            self.status.setText("")
        self._load_loaders()

    def _load_loaders(self):
        previous = self.loader.currentData() or ""
        if self._pending_kind is not None:
            previous = self._pending_kind
        self.loader.setEnabled(False)
        self._loader_ver_row_widget.setVisible(False)
        self.loader_ver.setEnabled(False)
        self.loader_ver.clear()
        self.status.setText("Проверка доступных загрузчиков…")
        mc = self.mc.currentData() or ""
        self._loaders_req += 1
        req = self._loaders_req

        def work():
            data = self.api.list_available_loaders(mc) or {}
            if req == self._loaders_req:
                self._signals.loaders_ready.emit(data, previous)

        self._pool.submit(work)

    def _apply_loaders(self, data, previous):
        keep_kind = None
        if self._pending_kind is not None:
            previous = self._pending_kind
            keep_kind = previous or None
            self._pending_kind = None
        names = {"fabric": "Fabric", "quilt": "Quilt", "forge": "Forge", "neoforge": "NeoForge"}
        available = data.get("loaders") or []
        self.loader.blockSignals(True)
        self.loader.clear()
        self.loader.addItem("Vanilla", "")
        for kind in available:
            self.loader.addItem(names.get(kind, kind.title()), kind)
        if keep_kind and self.loader.findData(keep_kind) < 0:
            # загрузчик экземпляра не должен пропадать, даже если проверка сети не удалась
            self.loader.addItem(names.get(keep_kind, keep_kind.title()), keep_kind)
        idx = self.loader.findData(previous)
        self.loader.setCurrentIndex(idx if idx >= 0 else 0)
        self.loader.blockSignals(False)
        self.loader.setEnabled(True)
        self.status.setText("; ".join(data.get("errors") or []))
        self._refresh_default_icon()
        if self.loader.currentData():
            self._load_loader_versions()

    def _load_loader_versions(self):
        kind = self.loader.currentData() or ""
        self._loader_ver_row_widget.setVisible(bool(kind))
        self.loader_ver.setEnabled(False)
        self.loader_ver.clear()
        if not kind:
            return
        self.status.setText(f"Загрузка версий {self.loader.currentText()}…")
        mc = self.mc.currentData() or ""
        self._loader_ver_req += 1
        req = self._loader_ver_req

        def work():
            data = self.api.list_loader_versions(kind, mc) or {}
            if req == self._loader_ver_req:
                self._signals.loader_versions_ready.emit(data)

        self._pool.submit(work)

    def _apply_loader_versions(self, data):
        for v in data.get("versions") or []:
            self.loader_ver.addItem(v.get("label") or v["version"], v["version"])
        want = self._pending_loader_ver
        self._pending_loader_ver = None
        if want:
            i = self.loader_ver.findData(want)
            if i < 0:
                self.loader_ver.insertItem(0, want, want)
                i = 0
            self.loader_ver.setCurrentIndex(i)
        self.loader_ver.setEnabled(self.loader_ver.count() > 0)
        self.status.setText(
            data.get("error")
            or ("" if self.loader_ver.count() else "Для выбранной версии нет доступных версий загрузчика")
        )

    # ----- данные формы -----
    def payload(self) -> dict:
        # Макс. ОЗУ → ГБ для бэкенда; без галочки «Память» — 4 ГБ по умолчанию
        if self.mem_check.isChecked():
            ram_gb = max(1, min(32, int(round(self.ram_max.value() / 1024))))
        else:
            ram_gb = 4
        data = {
            "name": self.name.text().strip(),
            "mc": self.mc.currentData(),
            # "" = Vanilla (None бэкенд при обновлении игнорирует, и смена загрузчика на Vanilla не сохранялась бы)
            "kind": self.loader.currentData() or "",
            "loader_version": self.loader_ver.currentData() or "",
            "ram_gb": ram_gb,
        }
        if self._icon_path or self._editing:
            data["icon_path"] = self._icon_path
        return data


class CreateInstanceDialog(QDialog):
    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self.setWindowTitle("Новый экземпляр")
        self.setMinimumSize(556, 580)
        self.resize(556, 580)
        self.setStyleSheet(STYLESHEET)

        self.tabs = QTabWidget()
        self._pack_pool = ThreadPoolExecutor(max_workers=1)
        self._pack_req = 0
        self._pack_sig = _PackSearchSignals(self)
        self._pack_sig.ready.connect(self._apply_pack_results)

        # ---------- Свой экземпляр ----------
        self.form = InstanceForm(api)
        self.tabs.addTab(self.form, "Свой экземпляр")

        # ---------- Скачать сборку ----------
        pack = QWidget()
        pv = QVBoxLayout(pack)
        pv.setContentsMargins(*TAB_PAGE_MARGINS)
        pv.setSpacing(GAP)

        self._pack_page = 0
        self._pack_page_size = 20
        self._pack_total = 0

        search_row = QHBoxLayout()
        search_row.setSpacing(GAP)
        self.pack_source = QComboBox()
        self.pack_source.addItem("Modrinth", "modrinth")
        self.pack_source.setFixedWidth(CTRL_W_SM)
        self.pack_search = QLineEdit()
        self.pack_search.setPlaceholderText("Поиск сборок…")
        # Живой поиск с небольшой задержкой
        self._pack_search_timer = QTimer(self)
        self._pack_search_timer.setSingleShot(True)
        self._pack_search_timer.setInterval(350)
        self._pack_search_timer.timeout.connect(self._on_pack_search_debounce)
        self.pack_search.textChanged.connect(self._on_pack_search_text)
        search_row.addWidget(self.pack_source)
        search_row.addWidget(self.pack_search, 1)
        pv.addLayout(search_row)

        self.pack_status = QLabel("")
        self.pack_status.setObjectName("muted")
        pv.addWidget(self.pack_status)

        self.pack_list = QListWidget()
        self.pack_list.setIconSize(QSize(LIST_ICON, LIST_ICON))
        self.pack_list.itemDoubleClicked.connect(self._install_pack)
        self.pack_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pack_list.customContextMenuRequested.connect(self._on_pack_context_menu)
        self.pack_icons = RemoteIcons(self)
        self.pack_icons.ready.connect(lambda url: _apply_icon(self.pack_list, self.pack_icons, url))
        pv.addWidget(self.pack_list, 1)

        # Навигация по страницам
        page_row = QHBoxLayout()
        page_row.setSpacing(GAP)
        self.btn_pack_prev = QPushButton("← Назад")
        self.btn_pack_prev.setFixedWidth(CTRL_W_SM)
        self.btn_pack_prev.clicked.connect(self._pack_prev_page)
        self.pack_page_label = QLabel("Стр. 1")
        self.pack_page_label.setObjectName("muted")
        self.pack_page_label.setAlignment(Qt.AlignCenter)
        self.btn_pack_next = QPushButton("Вперёд →")
        self.btn_pack_next.setFixedWidth(CTRL_W_SM)
        self.btn_pack_next.clicked.connect(self._pack_next_page)
        page_row.addWidget(self.btn_pack_prev)
        page_row.addStretch(1)
        page_row.addWidget(self.pack_page_label)
        page_row.addStretch(1)
        page_row.addWidget(self.btn_pack_next)
        pv.addLayout(page_row)

        self.tabs.addTab(pack, "Скачать сборку")

        # Кнопки
        buttons = QDialogButtonBox()
        self.btn_ok = buttons.addButton("Создать", QDialogButtonBox.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        self.btn_ok.setObjectName("accent")

        # Каркас окна: полоса под заголовком + тело с едиными отступами
        body_lay = build_dialog_body(self, tabbed=True)
        body_lay.addWidget(self.tabs, 1)
        body_lay.addWidget(buttons)

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._packs_loaded = False
        # Не грузим сборки при открытии — только при переходе на вкладку

    def _on_pack_context_menu(self, pos):
        item = self.pack_list.itemAt(pos)
        if item is None:
            return
        h = item.data(Qt.UserRole) or {}
        slug = (h.get("slug") or h.get("project_id") or "").strip()
        if not slug:
            return
        menu = QMenu(self)
        act = QAction("Ссылка на Modrinth", menu)
        act.triggered.connect(lambda: self._open_modrinth(slug))
        menu.addAction(act)
        menu.exec(self.pack_list.mapToGlobal(pos))

    def _open_modrinth(self, slug: str):
        url = QUrl(f"https://modrinth.com/modpack/{slug}")
        QDesktopServices.openUrl(url)

    def _on_tab_changed(self, index: int):
        # 0 — свой: «Создать»; 1 — сборка: «ОК» (установить выбранную)
        if index == 0:
            self.btn_ok.setText("Создать")
            self.btn_ok.setVisible(True)
        else:
            self.btn_ok.setText("ОК")
            self.btn_ok.setVisible(True)
            if not self._packs_loaded:
                self._packs_loaded = True
                self._search_packs()

    def _on_accept(self):
        if self.tabs.currentIndex() == 0:
            self._create()
        else:
            item = self.pack_list.currentItem()
            if item is None:
                self.pack_status.setText("Выберите сборку")
                return
            self._install_pack(item)

    def _on_pack_search_text(self, _text: str):
        # Сброс на первую страницу и отложенный поиск
        self._pack_page = 0
        self._pack_search_timer.start()

    def _on_pack_search_debounce(self):
        self._search_packs()

    def _pack_prev_page(self):
        if self._pack_page <= 0:
            return
        self._pack_page -= 1
        self._search_packs()

    def _pack_next_page(self):
        pages = max(1, (self._pack_total + self._pack_page_size - 1) // self._pack_page_size)
        if self._pack_page + 1 >= pages:
            return
        self._pack_page += 1
        self._search_packs()

    def _update_pack_pager(self):
        pages = max(1, (self._pack_total + self._pack_page_size - 1) // self._pack_page_size)
        page = min(self._pack_page, pages - 1)
        self._pack_page = max(0, page)
        self.pack_page_label.setText(f"Стр. {self._pack_page + 1} / {pages}")
        self.btn_pack_prev.setEnabled(self._pack_page > 0)
        self.btn_pack_next.setEnabled(self._pack_page + 1 < pages)

    def _search_packs(self):
        self.pack_status.setText("Поиск…")
        self.pack_list.clear()
        offset = self._pack_page * self._pack_page_size
        query = self.pack_search.text().strip()
        self._pack_req += 1
        req = self._pack_req

        def work():
            try:
                data = self.api.search_modpacks(query, offset=offset) or {}
            except Exception as e:
                data = {"hits": [], "total": 0, "error": str(e)}
            # Signal безопасно доставляет результат в UI-поток
            self._pack_sig.ready.emit(data, req)

        self._pack_pool.submit(work)

    def _apply_pack_results(self, data, req):
        if req != getattr(self, "_pack_req", 0):
            return
        if not isinstance(data, dict):
            data = {}
        hits = data.get("hits") or []
        self._pack_total = int(data.get("total") or len(hits) or 0)
        self.pack_status.setText(data.get("error") or f"Найдено: {self._pack_total}")
        self.pack_list.clear()
        for h in hits:
            item = QListWidgetItem(f"{h.get('title')}\n{(h.get('description') or '')[:80]}")
            item.setData(Qt.UserRole, h)
            item.setIcon(self.pack_icons.get(h.get("icon_url") or ""))
            self.pack_list.addItem(item)
        self._update_pack_pager()

    def _install_pack(self, item):
        h = item.data(Qt.UserRole) or {}
        self.pack_status.setText(f"Установка {h.get('title')}…")
        self.btn_ok.setEnabled(False)
        self.pack_list.setEnabled(False)
        self.pack_search.setEnabled(False)
        self.btn_pack_prev.setEnabled(False)
        self.btn_pack_next.setEnabled(False)
        res = self.api.install_modpack(
            h.get("project_id") or h.get("slug"),
            icon_url=h.get("icon_url") or "",
        ) or {}
        if res.get("error"):
            self.pack_status.setText(res["error"])
            self._restore_pack_ui()
            return
        if not res.get("started"):
            self.pack_status.setText("Не удалось начать установку")
            self._restore_pack_ui()
            return
        # Опрос статуса в фоне — UI не блокируется
        self._pack_install_timer = QTimer(self)
        self._pack_install_timer.setInterval(400)
        self._pack_install_timer.timeout.connect(self._poll_pack_install)
        self._pack_install_timer.start()

    def _restore_pack_ui(self):
        self.btn_ok.setEnabled(True)
        self.pack_list.setEnabled(True)
        self.pack_search.setEnabled(True)
        self._update_pack_pager()

    def _poll_pack_install(self):
        job = self.api.get_job_status() or {}
        msg = (job.get("status") or job.get("title") or "").strip()
        if msg:
            self.pack_status.setText(msg)
        # Пока busy — ждём
        if job.get("busy"):
            return
        self._pack_install_timer.stop()
        result = self.api.get_modpack_install_result() or {}
        if result.get("error"):
            self.pack_status.setText(result["error"])
            self._restore_pack_ui()
            return
        # id для prepare_instance после закрытия диалога
        self._created_id = result.get("id")
        self.accept()

    def _create(self):
        payload = self.form.payload()
        if not payload["name"]:
            self.form.status.setText("Введите название")
            return
        self.form.status.setText("Создание…")
        res = self.api.create_instance(payload) or {}
        if res.get("error"):
            self.form.status.setText(res["error"])
            return
        self._created_id = res.get("id")
        self.accept()

    @property
    def created_id(self):
        return getattr(self, "_created_id", None)


class ProfileDialog(QDialog):
    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self.setWindowTitle("Добавить профиль")
        self.setMinimumSize(400, 280)
        self.resize(420, 300)
        self.setStyleSheet(STYLESHEET)

        tabs = QTabWidget()
        local = QWidget()
        lf = QFormLayout(local)
        _form_defaults(lf, margins=TAB_PAGE_MARGINS)
        self.local_nick = QLineEdit()
        self.local_nick.setMaxLength(16)
        lf.addRow("Никнейм", self.local_nick)
        lf.addRow(QLabel("3–16: латиница, цифры, _"))
        tabs.addTab(local, "Локальный")

        ely = QWidget()
        ef = QFormLayout(ely)
        _form_defaults(ef, margins=TAB_PAGE_MARGINS)
        self.ely_login = QLineEdit()
        self.ely_pass = QLineEdit()
        self.ely_pass.setEchoMode(QLineEdit.Password)
        ef.addRow("Логин Ely.by", self.ely_login)
        ef.addRow("Пароль", self.ely_pass)
        tabs.addTab(ely, "Аккаунт Ely.by")

        self.status = QLabel("")
        self.status.setObjectName("muted")
        buttons = QDialogButtonBox()
        ok = buttons.addButton("Добавить", QDialogButtonBox.AcceptRole)
        ok.setObjectName("accent")
        buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)

        body_lay = build_dialog_body(self, tabbed=True)
        body_lay.addWidget(tabs, 1)
        body_lay.addWidget(self.status)
        body_lay.addWidget(buttons)
        self.tabs = tabs

    def _submit(self):
        if self.tabs.currentIndex() == 0:
            res = self.api.add_local_profile(self.local_nick.text().strip()) or {}
        else:
            res = self.api.add_ely_account(self.ely_login.text().strip(), self.ely_pass.text()) or {}
        if res.get("error"):
            self.status.setText(res["error"])
            return
        self.accept()


class ProfileEditDialog(QDialog):
    """Настройка существующего профиля игрока."""
    def __init__(self, api, profile: dict, parent=None):
        super().__init__(parent)
        self.api = api
        self.profile = profile or {}
        self.pid = self.profile.get("id") or ""
        self.setWindowTitle("Настроить профиль")
        self.setMinimumWidth(380)
        self.setStyleSheet(STYLESHEET)

        body_lay = build_dialog_body(self)

        form = QFormLayout()
        _form_defaults(form)
        self.name = QLineEdit(self.profile.get("name") or "")
        self.name.setMaxLength(16)
        if self.pid.startswith("ely:"):
            self.name.setReadOnly(True)
            form.addRow("Аккаунт Ely.by", self.name)
            note = QLabel("Имя аккаунта Ely.by изменяется на сайте сервиса.")
            note.setObjectName("muted")
            body_lay.addLayout(form)
            body_lay.addWidget(note)
        else:
            form.addRow("Никнейм", self.name)
            hint = QLabel("3–16 символов: латиница, цифры, _")
            hint.setObjectName("muted")
            body_lay.addLayout(form)
            body_lay.addWidget(hint)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        body_lay.addWidget(self.status)
        buttons = QDialogButtonBox()
        if self.pid.startswith("ely:"):
            sync = buttons.addButton("Обновить скин", QDialogButtonBox.ActionRole)
            sync.clicked.connect(self._sync_ely)
            buttons.addButton("Закрыть", QDialogButtonBox.RejectRole)
        else:
            save = buttons.addButton("Сохранить", QDialogButtonBox.AcceptRole)
            save.setObjectName("accent")
            buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
            save.clicked.connect(self._save)
        buttons.rejected.connect(self.reject)
        body_lay.addWidget(buttons)

    def _save(self):
        result = self.api.update_profile(self.pid, self.name.text().strip()) or {}
        if result.get("error"):
            self.status.setText(result["error"])
            return
        self.accept()

    def _sync_ely(self):
        result = self.api.sync_ely_profile(self.pid) or {}
        if result.get("error"):
            self.status.setText(result["error"])
        else:
            self.status.setText("Скин обновлён.")


class InstanceSettingsDialog(QDialog):
    def __init__(self, api, instance_id: str, parent=None):
        super().__init__(parent)
        self.api = api
        self.instance_id = instance_id
        self.setWindowTitle("Настройка экземпляра")
        # тот же размер и оформление, что у окна «Новый экземпляр»
        self.setMinimumSize(556, 580)
        self.resize(556, 580)
        self.setStyleSheet(STYLESHEET + CONTENT_LIST_QSS)

        data = api.get_instance(instance_id) or {}
        inst = data.get("instance") or {}

        self.tabs = QTabWidget()

        # ---------- Экземпляр: та же форма, что «Свой экземпляр» при создании ----------
        self.form = InstanceForm(api, inst)
        self.tabs.addTab(self.form, "Экземпляр")

        # ---------- Контент ----------
        cont = QWidget()
        cv = QVBoxLayout(cont)
        cv.setContentsMargins(*TAB_PAGE_MARGINS)
        cv.setSpacing(GAP)

        content_head = QHBoxLayout()
        self.content_type = QComboBox()
        for name, key in [("Моды", "mod"), ("Ресурспаки", "resourcepack"), ("Шейдеры", "shader")]:
            self.content_type.addItem(name, key)
        self.content_type.currentIndexChanged.connect(self._reload_installed)
        add_icon_path = Path(__file__).resolve().parent.parent / "assets" / "icons" / "add_circle.png"
        add_icon = QPixmap(str(add_icon_path))
        self.btn_content_download = IconButton("Скачать контент", add_icon, icon_size=20,
                                               bold=True, text_color="#ffffff")
        self.btn_content_download.setObjectName("accent")
        self.btn_content_download.clicked.connect(self._open_download)
        content_head.addWidget(self.content_type)
        content_head.addStretch(1)
        content_head.addWidget(self.btn_content_download)
        cv.addLayout(content_head)

        self.installed = QListWidget()
        self.installed.setObjectName("installedContentList")
        self.installed.setSpacing(GAP_XS)
        self.installed.setContextMenuPolicy(Qt.CustomContextMenu)
        self.installed.customContextMenuRequested.connect(self._content_context_menu)
        cv.addWidget(self.installed, 1)
        self.content_status = QLabel("")
        self.content_status.setObjectName("muted")
        cv.addWidget(self.content_status)
        self.content_icons = RemoteIcons(self, size=32)
        self.content_icons.ready.connect(self._apply_installed_icon)
        self.tabs.addTab(cont, "Контент")

        # ---------- Кнопки ----------
        buttons = QDialogButtonBox()
        folder = buttons.addButton("Папка игры", QDialogButtonBox.ActionRole)
        folder.clicked.connect(self._open_folder)
        save = buttons.addButton("Сохранить", QDialogButtonBox.AcceptRole)
        save.setObjectName("accent")
        buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        # Каркас окна — как у «Новый экземпляр»
        body_lay = build_dialog_body(self, tabbed=True)
        body_lay.addWidget(self.tabs, 1)
        body_lay.addWidget(buttons)

        self._reload_installed()

    def _open_folder(self):
        res = self.api.open_instance_folder(self.instance_id) or {}
        if res.get("error"):
            show_warning(self, "Ошибка", res["error"])

    def _ctype(self):
        return self.content_type.currentData() or "mod"

    def _reload_installed(self, *_):
        self.installed.clear()
        data = self.api.list_installed_content(self.instance_id, self._ctype()) or {}
        items = data.get("items") or []
        for it in items:
            row = QListWidgetItem()
            row.setData(Qt.UserRole, it)
            row.setSizeHint(QSize(0, 48))
            self.installed.addItem(row)

            widget = QWidget()
            widget.setObjectName("installedContentRow")
            lay = QHBoxLayout(widget)
            lay.setContentsMargins(*ROW_MARGINS)
            lay.setSpacing(GAP)
            lay.setAlignment(Qt.AlignVCenter)
            enabled = QCheckBox()
            enabled.setObjectName("contentEnabledCheck")
            enabled.setChecked(not it.get("disabled", False))
            enabled.setToolTip("Включить или отключить")
            enabled.toggled.connect(lambda checked, entry=it: self._set_content_enabled(entry, checked))
            icon = QLabel()
            icon.setObjectName("contentRowIcon")
            icon.setFixedSize(32, 32)
            icon.setAlignment(Qt.AlignCenter)
            icon.setPixmap(self.content_icons.get(it.get("icon_url") or "").pixmap(32, 32))
            name = QLabel(it.get("name") or it.get("filename") or "Неизвестный файл")
            name.setObjectName("contentRowName")
            name.setMinimumWidth(100)
            version = QLabel(it.get("version") or "—")
            version.setObjectName("contentRowMeta")
            version.setMinimumWidth(78)
            version.setMaximumWidth(125)
            version.setToolTip(it.get("version") or "Версия не указана")
            source = QLabel(it.get("source") or "Локальный файл")
            source.setObjectName("contentRowMeta")
            source.setMinimumWidth(82)
            source.setMaximumWidth(115)
            lay.addWidget(enabled, 0, Qt.AlignVCenter)
            lay.addWidget(icon, 0, Qt.AlignVCenter)
            lay.addWidget(name, 1, Qt.AlignVCenter)
            lay.addWidget(version, 0, Qt.AlignVCenter)
            lay.addWidget(source, 0, Qt.AlignVCenter)
            self.installed.setItemWidget(row, widget)
        self.content_status.setText(data.get("error") or f"Установлено: {len(items)}")

    def _apply_installed_icon(self, url):
        icon = self.content_icons._icons.get(url)
        if icon is None:
            return
        for i in range(self.installed.count()):
            item = self.installed.item(i)
            entry = item.data(Qt.UserRole) or {}
            if (entry.get("icon_url") or "") == url:
                widget = self.installed.itemWidget(item)
                if widget and widget.layout() and widget.layout().count() > 1:
                    label = widget.layout().itemAt(1).widget()
                    if isinstance(label, QLabel):
                        label.setPixmap(icon.pixmap(32, 32))

    def _set_content_enabled(self, it, enabled):
        if bool(it.get("disabled")) == (not bool(enabled)):
            return
        result = self.api.toggle_content(self.instance_id, self._ctype(), it.get("filename"))
        if result.get("error"):
            show_warning(self, "Ошибка", result["error"])
        self._reload_installed()

    def _content_context_menu(self, pos):
        row = self.installed.itemAt(pos)
        if row is None:
            return
        it = row.data(Qt.UserRole) or {}
        menu = QMenu(self)
        toggle = menu.addAction("Включить" if it.get("disabled") else "Отключить")
        delete = menu.addAction("Удалить")
        menu.addSeparator()
        open_modrinth = menu.addAction("Открыть на Modrinth")
        open_modrinth.setEnabled(bool(it.get("slug") or it.get("project_id")))
        chosen = menu.exec(self.installed.mapToGlobal(pos))
        if chosen == toggle:
            self._set_content_enabled(it, bool(it.get("disabled")))
        elif chosen == delete:
            if ask_yes_no(self, "Удалить", f"Удалить «{it.get('name')}»?"):
                result = self.api.delete_content(self.instance_id, self._ctype(), it.get("filename"))
                if result.get("error"):
                    show_warning(self, "Ошибка", result["error"])
                self._reload_installed()
        elif chosen == open_modrinth:
            slug = it.get("slug") or it.get("project_id")
            QDesktopServices.openUrl(QUrl(f"https://modrinth.com/{self._ctype()}/{slug}"))

    def _open_download(self):
        dlg = ContentDownloadDialog(self.api, self.instance_id, self, initial_type=self._ctype())
        dlg.exec()
        self._reload_installed()

    def _save(self):
        payload = self.form.payload()
        if not payload["name"]:
            self.form.status.setText("Введите название")
            self.tabs.setCurrentIndex(0)
            return
        res = self.api.update_instance(self.instance_id, payload) or {}
        if res.get("error"):
            show_warning(self, "Ошибка", res["error"])
            return
        self.accept()


class ContentDownloadSignals(QObject):
    details_ready = Signal(object)
    versions_ready = Signal(object)
    install_ready = Signal(object)


class ContentDownloadDialog(QDialog):
    def __init__(self, api, instance_id: str, parent=None, initial_type="mod"):
        super().__init__(parent)
        self.api = api
        self.instance_id = instance_id
        self.page = 1
        self.pages = 1
        self.total = 0
        self._hits = []
        self._selected = {}
        self._selection = {}
        self._version_choices = {}
        self._detail_request = 0
        self._versions_request = 0
        self._installing = False
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._signals = ContentDownloadSignals(self)
        self._signals.details_ready.connect(self._apply_details)
        self._signals.versions_ready.connect(self._apply_versions)
        self._signals.install_ready.connect(self._install_finished)
        self.setWindowTitle("Скачать контент — Modrinth")
        # Минимальный размер = стандартный (по умолчанию)
        self.setMinimumSize(1080, 720)
        self.resize(1080, 720)
        self.setStyleSheet(STYLESHEET + CONTENT_LIST_QSS)

        self.type_select = QComboBox()
        for name, key in [("Моды", "mod"), ("Ресурспаки", "resourcepack"), ("Шейдеры", "shader")]:
            self.type_select.addItem(name, key)
        self.type_select.setCurrentIndex(max(0, self.type_select.findData(initial_type)))
        self.type_select.currentIndexChanged.connect(self._search)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск на Modrinth…")
        self.search.setClearButtonEnabled(True)
        search_row = QHBoxLayout()
        search_row.setSpacing(GAP)
        search_row.addWidget(self.type_select)
        search_row.addWidget(self.search, 1)

        self.status = QLabel("Поиск…")
        self.status.setObjectName("muted")
        from PySide6.QtWidgets import QProgressBar
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setRange(0, 0)  # indeterminate by default when shown
        self.progress.setFixedHeight(PROGRESS_H)
        self.progress.setVisible(False)
        self._install_timer = QTimer(self)
        self._install_timer.setInterval(250)
        self._install_timer.timeout.connect(self._poll_install_progress)
        self.list = QListWidget()
        self.list.setObjectName("downloadContentList")
        self.list.setSpacing(GAP_XS)
        self.list.currentItemChanged.connect(self._show_details)
        self.icons = RemoteIcons(self, size=32)
        self.icons.ready.connect(self._apply_download_icon)

        self.detail_title = QLabel("Выберите проект")
        self.detail_title.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.detail_description = QTextBrowser()
        self.detail_description.setOpenExternalLinks(True)
        self.detail_description.setPlaceholderText("Описание проекта")
        self.detail_description.setMinimumHeight(120)
        self.detail_description.setLineWrapMode(QTextBrowser.WidgetWidth)
        self.detail_description.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.detail_description.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        # Картинки из Markdown часто ломают вёрстку — не подгружаем внешние ресурсы
        try:
            self.detail_description.document().setDocumentMargin(0)  # отступ даёт padding самого виджета
            self.detail_description.document().setDefaultStyleSheet(
                "body { color: #e5edf3; margin: 0; } "
                f"p, li {{ margin: 0 0 {GAP}px 0; }} "
                "img { display: none; width: 0; height: 0; }"
            )
        except Exception:
            pass
        self.version_select = QComboBox()
        self.version_select.addItem("Загрузка версий…", "")
        self.version_select.currentIndexChanged.connect(self._version_changed)
        self.detail_link = QPushButton("Открыть на Modrinth")
        self.detail_link.clicked.connect(self._open_selected)
        self.install_btn = QPushButton("Установить выбранное (0)")
        self.install_btn.setObjectName("accent")
        self.install_btn.clicked.connect(self._install_selected)
        detail = QVBoxLayout()
        detail.setContentsMargins(*ZERO_MARGINS)
        detail.setSpacing(GAP)
        detail.addWidget(self.detail_title)
        detail.addWidget(QLabel("Версия проекта"))
        detail.addWidget(self.version_select)
        detail.addWidget(self.detail_description, 1)
        detail.addWidget(self.detail_link)
        detail.addWidget(self.install_btn)

        from PySide6.QtWidgets import QSplitter
        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(*ZERO_MARGINS)
        left_lay.setSpacing(GAP)
        # Гибкие минимумы: влезают в окно 1080×720 с учётом отступов
        left.setMinimumWidth(280)
        left_lay.addWidget(self.list, 1)
        list_pager = QHBoxLayout()
        list_pager.setSpacing(GAP)
        self.btn_prev = QPushButton("←")
        self.btn_next = QPushButton("→")
        self.pager_lbl = QLabel("Стр. 1 / 1")
        self.pager_lbl.setObjectName("muted")
        self.btn_prev.setToolTip("Предыдущая страница")
        self.btn_next.setToolTip("Следующая страница")
        self.btn_prev.clicked.connect(self._prev)
        self.btn_next.clicked.connect(self._next)
        list_pager.addWidget(self.btn_prev)
        list_pager.addStretch()
        list_pager.addWidget(self.pager_lbl)
        list_pager.addStretch()
        list_pager.addWidget(self.btn_next)
        left_lay.addLayout(list_pager)
        right = QWidget()
        right.setMinimumWidth(360)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(GAP, 0, 0, 0)
        right_lay.setSpacing(GAP)
        right_lay.addLayout(detail)
        splitter.addWidget(left)
        splitter.addWidget(right)
        # Правая панель растягивается; стартовые доли — список / описание
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([380, 640])
        splitter.setChildrenCollapsible(False)

        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        layout = build_dialog_body(self)
        layout.addLayout(search_row)
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addWidget(splitter, 1)
        layout.addWidget(close, alignment=Qt.AlignRight)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        # Live search: wait briefly for typing to pause, then query Modrinth.
        self._search_timer.setInterval(400)
        self._search_timer.timeout.connect(self._load)
        self.search.textChanged.connect(self._on_search_text_changed)
        self._load()

    def _apply_download_icon(self, url):
        icon = self.icons._icons.get(url)
        if icon is None:
            return
        for i in range(self.list.count()):
            item = self.list.item(i)
            hit = item.data(Qt.UserRole) or {}
            if (hit.get("icon_url") or "") != url:
                continue
            widget = self.list.itemWidget(item)
            if widget and widget.layout() and widget.layout().count() > 1:
                label = widget.layout().itemAt(1).widget()
                if isinstance(label, QLabel):
                    label.setPixmap(icon.pixmap(32, 32))

    def _ctype(self):
        return self.type_select.currentData() or "mod"

    def _on_search_text_changed(self, _text):
        self.page = 1
        self._search_timer.start()

    def _search(self, *_):
        self.page = 1
        self._load()

    def _prev(self):
        if self.page > 1:
            self.page -= 1
            self._load()

    def _next(self):
        if self.page < self.pages:
            self.page += 1
            self._load()

    def _load(self):
        st = self.api.get_state() or {}
        inst = next((i for i in (st.get("instances") or []) if i.get("id") == self.instance_id), {}) or {}
        self.status.setText("Поиск…")
        self.list.clear()
        self.detail_title.setText("Выберите проект")
        self.detail_description.clear()
        offset = (self.page - 1) * 20
        data = self.api.search_content(self._ctype(), self.search.text().strip(),
                                       inst.get("mc") or "", inst.get("kind") or "", offset, 20) or {}
        if not (data.get("hits") or []) and not data.get("error"):
            data = self.api.search_content(self._ctype(), self.search.text().strip(), "",
                                           inst.get("kind") or "", offset, 20) or {}
        hits = data.get("hits") or []
        self.pages = max(1, int(data.get("pages") or 1))
        self.page = int(data.get("page") or self.page)
        self.total = int(data.get("total") or 0)
        self.status.setText(data.get("error") or f"Найдено: {self.total}")
        self.pager_lbl.setText(f"Стр. {self.page} / {self.pages}")
        self.btn_prev.setEnabled(self.page > 1)
        self.btn_next.setEnabled(self.page < self.pages)
        for h in hits:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, h)
            item.setFlags(item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            item.setSizeHint(QSize(0, 48))
            self.list.addItem(item)

            # Same custom green checkbox used in the installed-content list.
            row_widget = QWidget()
            row_widget.setObjectName("downloadContentRow")
            row_lay = QHBoxLayout(row_widget)
            row_lay.setContentsMargins(*ROW_MARGINS)
            row_lay.setSpacing(GAP)
            row_lay.setAlignment(Qt.AlignVCenter)
            check = QCheckBox()
            check.setObjectName("contentEnabledCheck")
            pid = h.get("project_id") or h.get("slug")
            check.setChecked((self._ctype(), pid) in self._selection)
            check.setToolTip("Добавить проект в список установки")
            check.toggled.connect(lambda checked, entry=item: self._set_download_selected(entry, checked))
            icon_label = QLabel()
            icon_label.setObjectName("contentRowIcon")
            icon_label.setFixedSize(32, 32)
            icon_label.setAlignment(Qt.AlignCenter)
            icon_label.setPixmap(self.icons.get(h.get("icon_url") or "").pixmap(32, 32))
            title_label = QLabel(h.get("title") or h.get("slug") or "Без названия")
            title_label.setObjectName("contentRowName")
            title_label.setMinimumWidth(0)
            row_lay.addWidget(check, 0, Qt.AlignVCenter)
            row_lay.addWidget(icon_label, 0, Qt.AlignVCenter)
            row_lay.addWidget(title_label, 1, Qt.AlignVCenter)
            self.list.setItemWidget(item, row_widget)
        if hits:
            self.list.setCurrentRow(0)
        self._update_selected_count()

    @staticmethod
    def _clean_description(text: str) -> str:
        """Убираем картинки из Markdown/HTML — они ломают вёрстку QTextBrowser."""
        if not text:
            return ""
        # ![alt](url) and bare HTML <img ...>
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
        text = re.sub(r"<img\b[^>]*/?>", "", text, flags=re.IGNORECASE)
        # Схлопываем лишние пустые строки после удаления картинок
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _set_description(self, text: str):
        cleaned = self._clean_description(text or "")
        if cleaned:
            self.detail_description.setMarkdown(cleaned)
        else:
            self.detail_description.setPlainText("")

    def _show_details(self, current, _previous=None):
        h = current.data(Qt.UserRole) if current else {}
        h = h or {}
        self._selected = h
        self.detail_title.setText(h.get("title") or "Выберите проект")
        # Краткое описание из поиска показываем сразу, не оставляя пустую панель,
        # пока загружается полное Markdown-описание проекта.
        self._set_description(h.get("description") or "_Загрузка полного описания проекта…_")
        self.detail_link.setEnabled(bool(h.get("slug") or h.get("project_id")))
        self.version_select.blockSignals(True)
        self.version_select.clear()
        self.version_select.addItem("Загрузка версий…", "")
        self.version_select.setEnabled(False)
        self.version_select.blockSignals(False)
        self._detail_request += 1
        request_id = self._detail_request
        pid = h.get("project_id") or h.get("slug")
        if pid and hasattr(self.api, "get_content_details"):
            slug = h.get("slug") or ""
            future = self._pool.submit(self.api.get_content_details, pid, slug)
            future.add_done_callback(lambda f, rid=request_id: self._signals.details_ready.emit((rid, f.result() if not f.exception() else {})))
        self._versions_request += 1
        versions_request = self._versions_request
        if pid and hasattr(self.api, "get_state") and hasattr(self.api, "get_content_versions"):
            st = self.api.get_state() or {}
            inst = next((i for i in (st.get("instances") or []) if i.get("id") == self.instance_id), {}) or {}
            key = (self._ctype(), pid)
            future = self._pool.submit(self.api.get_content_versions, pid, inst.get("mc") or "", inst.get("kind") or "", self._ctype())
            future.add_done_callback(lambda f, rid=versions_request, k=key: self._signals.versions_ready.emit((rid, k, f.result() if not f.exception() else {})))

    def _apply_details(self, payload):
        request_id, data = payload
        if request_id != self._detail_request or not isinstance(data, dict):
            return
        if data.get("description"):
            # Modrinth body is Markdown; картинки убираем — иначе ломают вёрстку.
            self._set_description(data["description"])
        elif not (getattr(self, "_selected", {}) or {}).get("description"):
            self.detail_description.setPlainText("У этого проекта нет опубликованного описания.")
        if data.get("title"):
            self.detail_title.setText(data["title"])

        # В поисковой выдаче icon_url иногда отсутствует. Подтягиваем его из
        # полной карточки и обновляем строку списка после получения метаданных.
        icon_url = data.get("icon_url") or ""
        if icon_url:
            selected = getattr(self, "_selected", {}) or {}
            selected["icon_url"] = icon_url
            if data.get("description"):
                selected["description"] = data["description"]
            for i in range(self.list.count()):
                item = self.list.item(i)
                hit = item.data(Qt.UserRole) or {}
                if (hit.get("project_id") or hit.get("slug")) == (selected.get("project_id") or selected.get("slug")):
                    hit.update({k: v for k, v in data.items() if v not in (None, "")})
                    hit["icon_url"] = icon_url
                    item.setData(Qt.UserRole, hit)
                    self._apply_download_icon(icon_url)
                    break

    def _apply_versions(self, payload):
        request_id, key, data = payload
        if request_id != self._versions_request or not isinstance(data, dict):
            return
        versions = data.get("versions") or []
        self.version_select.blockSignals(True)
        self.version_select.clear()
        if not versions:
            self.version_select.addItem(data.get("error") or "Нет совместимых версий", "")
            self.version_select.setEnabled(False)
        else:
            for ver in versions:
                label = ver.get("version_number") or ver.get("name") or "Без названия"
                game_versions = ver.get("game_versions") or []
                if game_versions:
                    label += f"  ·  MC {', '.join(game_versions[:3])}"
                self.version_select.addItem(label, ver.get("id") or "")
            saved = self._version_choices.get(key, "")
            idx = self.version_select.findData(saved) if saved else -1
            self.version_select.setCurrentIndex(idx if idx >= 0 else 0)
            self.version_select.setEnabled(True)
            self._version_choices[key] = self.version_select.currentData() or ""
        self.version_select.blockSignals(False)

    def _version_changed(self, _index):
        h = getattr(self, "_selected", {}) or {}
        pid = h.get("project_id") or h.get("slug")
        if pid and self.version_select.currentData():
            self._version_choices[(self._ctype(), pid)] = self.version_select.currentData()

    def _open_selected(self):
        h = getattr(self, "_selected", {}) or {}
        slug = h.get("slug") or h.get("project_id")
        if slug:
            QDesktopServices.openUrl(QUrl(f"https://modrinth.com/{self._ctype()}/{slug}"))

    def _checked_items(self):
        return [self.list.item(i) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def _set_download_selected(self, item, checked):
        h = item.data(Qt.UserRole) or {}
        pid = h.get("project_id") or h.get("slug")
        key = (self._ctype(), pid)
        if checked:
            self._selection[key] = (h, self._ctype())
        else:
            self._selection.pop(key, None)
        self._update_selected_count()

    def _on_item_changed(self, item):
        h = item.data(Qt.UserRole) or {}
        pid = h.get("project_id") or h.get("slug")
        key = (self._ctype(), pid)
        if item.checkState() == Qt.Checked:
            self._selection[key] = (h, self._ctype())
        else:
            self._selection.pop(key, None)
        self._update_selected_count()

    def _update_selected_count(self, *_):
        count = len(self._selection)
        self.install_btn.setText(f"Установить выбранное ({count})")
        self.install_btn.setEnabled(count > 0 and not self._installing)

    def _install_selected(self):
        if self._installing:
            return
        selected = [(key, value[0], value[1], self._version_choices.get(key, "")) for key, value in self._selection.items()]
        if not selected:
            return
        self._installing = True
        self.install_btn.setEnabled(False)
        self.type_select.setEnabled(False)
        self.list.setEnabled(False)
        self.search.setEnabled(False)
        total = len(selected)
        self.status.setText(f"Установка {total} проектов и их обязательных зависимостей…")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # indeterminate until backend reports counts
        self.progress.setFormat("Установка…")
        self._install_total = total
        self._install_timer.start()

        def work():
            results = []
            for idx, (key, h, ctype, version_id) in enumerate(selected, start=1):
                pid = h.get("project_id") or h.get("slug")
                title = h.get("title") or pid
                try:
                    self.api._push_status(
                        f"Установка {idx}/{total}: {title}…",
                        busy=True,
                        title="Установка контента",
                        phase="downloading",
                    )
                except Exception:
                    pass
                results.append((key, title, self.api.install_content(self.instance_id, pid, ctype, version_id) or {}))
            return results

        future = self._pool.submit(work)
        future.add_done_callback(lambda f: self._signals.install_ready.emit(f.result() if not f.exception() else [(None, "", {"error": str(f.exception())})]))

    def _poll_install_progress(self):
        if not self._installing:
            self._install_timer.stop()
            return
        job = self.api.get_job_status() or {}
        msg = (job.get("status") or job.get("title") or "").strip()
        if msg:
            self.status.setText(msg)
        # Числовой прогресс, если бэкенд его отдаёт
        done = job.get("progress_done")
        total = job.get("progress_total")
        frac = job.get("progress")
        if isinstance(total, int) and total > 0 and isinstance(done, (int, float)):
            self.progress.setRange(0, total)
            self.progress.setValue(int(done))
            self.progress.setFormat("%v / %m")  # текст статуса — в подписи над полосой
        elif isinstance(frac, (int, float)) and 0 <= frac <= 1:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(frac * 100))
            self.progress.setFormat("%p%")
        else:
            # Неопределённый прогресс: текст текущего шага уже в подписи над полосой
            self.progress.setRange(0, 0)
            self.progress.setFormat("Установка…")

    def _install_finished(self, results):
        self._installing = False
        self._install_timer.stop()
        self.progress.setVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.type_select.setEnabled(True)
        self.list.setEnabled(True)
        self.search.setEnabled(True)
        ok = sum(1 for _key, _name, result in results if isinstance(result, dict) and result.get("ok"))
        failed = [(name, result.get("error", "Ошибка")) for _key, name, result in results
                  if isinstance(result, dict) and not result.get("ok")]
        for key, _name, result in results:
            if isinstance(result, dict) and result.get("ok"):
                self._selection.pop(key, None)
        self.status.setText(f"Установлено проектов: {ok}; ошибок: {len(failed)}")
        if failed:
            self.status.setToolTip("\n".join(f"{name}: {error}" for name, error in failed))
        else:
            self.status.setToolTip("")
        for i in range(self.list.count()):
            item = self.list.item(i)
            h = item.data(Qt.UserRole) or {}
            key = (self._ctype(), h.get("project_id") or h.get("slug"))
            widget = self.list.itemWidget(item)
            if widget and widget.layout() and widget.layout().count():
                checkbox = widget.layout().itemAt(0).widget()
                if isinstance(checkbox, QCheckBox):
                    checkbox.blockSignals(True)
                    checkbox.setChecked(key in self._selection)
                    checkbox.blockSignals(False)
        self._update_selected_count()


class JobDialog(QDialog):
    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self.setWindowTitle("Статус загрузки")
        self.setMinimumSize(440, 360)
        self.resize(480, 400)
        self.setStyleSheet(STYLESHEET)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.reload)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.setSpacing(GAP)
        row.addStretch()
        row.addWidget(refresh)
        row.addWidget(close)
        layout = build_dialog_body(self)
        layout.addWidget(self.log, 1)
        layout.addLayout(row)
        self.reload()

    def reload(self):
        job = self.api.get_job_status() or {}
        self.log.setPlainText("\n".join(job.get("log") or []) or "Нет сообщений")


# Тёмный системный заголовок для всех диалогов
for _cls_name in ("CreateInstanceDialog", "ProfileDialog", "ProfileEditDialog", "InstanceSettingsDialog", "ContentDownloadDialog", "JobDialog"):
    _cls = globals()[_cls_name]
    _orig_show = getattr(_cls, "showEvent", None)

    def _make_show(orig):
        def showEvent(self, event):
            if orig:
                orig(self, event)
            else:
                QDialog.showEvent(self, event)
            apply_dark_titlebar(self)
        return showEvent

    _cls.showEvent = _make_show(_orig_show)
