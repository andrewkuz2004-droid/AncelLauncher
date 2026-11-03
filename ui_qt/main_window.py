# -*- coding: utf-8 -*-
"""Главное окно AncelLauncher (Qt)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSize, QPoint
from PySide6.QtGui import QIcon, QPixmap, QMouseEvent, QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMenu,
    QDialog,
)

from core import LauncherAPI
from ui_qt.core import (
    STYLESHEET,
    BG,
    GREEN,
    MUTED,
    FG,
    PAD,
    GAP,
    GAP_SM,
    CTRL_W_LG,
    CTRL_W_SM,
    LOADER_COLORS,
    top_line,
    apply_dark_titlebar,
    ask_yes_no,
    show_warning,
    Card as Tile,
    ElidedLabel,
    IconButton,
    IconLabel,
    NavButton,
    ProgressIcon,
    RoundedPixmapLabel,
    Sidebar,
    Button,
    AccentButton,
)
from ui_qt.dialogs import (
    CreateInstanceDialog,
    ProfileDialog,
    ProfileEditDialog,
    InstanceSettingsDialog,
)

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "assets" / "icons"


def _icon(name: str) -> QPixmap:
    p = ICONS / name
    if not p.is_file():
        p = ICONS / "vanilla_48.png"
    return QPixmap(str(p))


def app_icon() -> QIcon:
    """Иконка лаунчера: несколько размеров, чтобы заголовок и панель задач были чёткими."""
    ic = QIcon()
    for n in (16, 24, 32, 48, 64, 128, 256):
        p = ICONS / f"app_{n}.png"
        if p.is_file():
            ic.addFile(str(p), QSize(n, n))
    ico = ICONS / "app.ico"
    if ic.isNull() and ico.is_file():
        ic = QIcon(str(ico))
    return ic


def _file_url_to_path(url: str) -> str:
    if not url or not url.startswith("file:"):
        return url or ""
    from urllib.parse import unquote, urlparse
    path = unquote(urlparse(url).path)
    if sys.platform == "win32" and path.startswith("/"):
        path = path[1:]
    return path


def _green_cube_pixmap(size: int = 48) -> QPixmap:
    """Зелёный изометрический куб на прозрачном фоне (подложка — отдельно в UI)."""
    from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
    from PySide6.QtCore import QPointF
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor("#3ecf8a"), max(2.0, size * 0.045))
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    cx, cy = size / 2, size / 2 + size * 0.02
    s = size * 0.29
    top = [
        QPointF(cx, cy - s * 1.15),
        QPointF(cx + s, cy - s * 0.5),
        QPointF(cx, cy + s * 0.15),
        QPointF(cx - s, cy - s * 0.5),
    ]
    left = [
        QPointF(cx - s, cy - s * 0.5),
        QPointF(cx, cy + s * 0.15),
        QPointF(cx, cy + s * 1.3),
        QPointF(cx - s, cy + s * 0.65),
    ]
    right = [
        QPointF(cx + s, cy - s * 0.5),
        QPointF(cx, cy + s * 0.15),
        QPointF(cx, cy + s * 1.3),
        QPointF(cx + s, cy + s * 0.65),
    ]
    for poly in (top, left, right):
        path = QPainterPath()
        path.moveTo(poly[0])
        for pt in poly[1:]:
            path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)
    p.end()
    return pm


def _instance_pixmap(inst: dict | None) -> QPixmap:
    """Иконка экземпляра: custom icon_path/icon_url или зелёный куб."""
    if inst:
        path = _file_url_to_path(inst.get("icon_url") or "")
        if path and Path(path).is_file():
            pm = QPixmap(path)
            if not pm.isNull():
                return pm
        raw = (inst.get("icon_path") or "").strip()
        if raw and Path(raw).is_file():
            pm = QPixmap(raw)
            if not pm.isNull():
                return pm
    return _green_cube_pixmap(48)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.api = LauncherAPI({"window": None})
        self.nav = "instances"
        self.state: dict = {}
        self._drag_pos = None
        self.setWindowTitle("AncelLauncher")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(900, 560)
        self.resize(1180, 720)
        self.setStyleSheet(STYLESHEET)

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Линия под заголовком окна (на всю ширину, включая сайдбар)
        root.addWidget(top_line())

        # ---- body ----
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # sidebar
        sidebar = Sidebar(width=220)  # отступы PAD / GAP заданы в Sidebar
        sv = sidebar.layout()
        self.nav_inst = self._nav_btn("Экземпляры", "instances", "instances.png")
        self.nav_prof = self._nav_btn("Профили", "profiles", "profiles.png")
        self.nav_set = self._nav_btn("Настройки", "settings", "settings_64.png")
        sv.addWidget(self.nav_inst)
        sv.addWidget(self.nav_prof)
        sv.addWidget(self.nav_set)
        sv.addStretch()
        prof_row = QHBoxLayout()
        prof_row.setContentsMargins(0, 0, 0, 0)
        prof_row.setSpacing(GAP)
        self.profile_avatar = IconLabel(_icon("steve_head_64.png"), size=26, radius=6)
        self.profile_name = QLabel("—")
        self.profile_name.setStyleSheet("font-weight:600;")
        prof_row.addWidget(self.profile_avatar)
        prof_row.addWidget(self.profile_name, 1)
        sv.addLayout(prof_row)
        body.addWidget(sidebar)

        # center
        center = QVBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)

        self.toolbar = QHBoxLayout()
        self.toolbar.setContentsMargins(PAD, PAD, PAD, 0)
        self.toolbar.setSpacing(GAP)
        self.toolbar.addStretch()
        self.btn_new = IconButton("Новый экземпляр", _icon("add_circle.png"), bold=True, text_color="#ffffff")
        self.btn_new.setObjectName("accent")
        self.btn_new.clicked.connect(self._create_instance)
        self.btn_import = IconButton("Импортировать сборку", _icon("import_file.png"))
        self.btn_import.clicked.connect(self._import)
        self.btn_add_profile = IconButton("Добавить профиль", _icon("add_circle.png"), bold=True, text_color="#ffffff")
        self.btn_add_profile.setObjectName("accent")
        self.btn_add_profile.clicked.connect(self._add_profile)
        self.btn_add_profile.setVisible(False)
        self.toolbar.addWidget(self.btn_new)
        self.toolbar.addWidget(self.btn_import)
        self.toolbar.addWidget(self.btn_add_profile)
        center.addLayout(self.toolbar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content_host = QWidget()
        self.content_layout = QVBoxLayout(self.content_host)
        self.content_layout.setContentsMargins(PAD, PAD, PAD, PAD)
        self.content_layout.setSpacing(GAP)
        self.scroll.setWidget(self.content_host)
        # ПКМ по полю экземпляров/профилей (не по «⋮» на карточке)
        for w in (self.scroll, self.scroll.viewport(), self.content_host):
            w.setContextMenuPolicy(Qt.CustomContextMenu)
            w.customContextMenuRequested.connect(self._on_content_context_menu)
        center.addWidget(self.scroll, 1)

        # bottom
        bottom = QFrame()
        bottom.setObjectName("bottom")
        # высота = рамка 1 + отступ GAP + иконка + отступ GAP: одинаково сверху и снизу
        bottom.setFixedHeight(1 + 2 * GAP + ProgressIcon.SIZE)
        br = QHBoxLayout(bottom)
        br.setContentsMargins(PAD, GAP, PAD, GAP)
        br.setSpacing(GAP)
        self.sel_icon = ProgressIcon()
        self.sel_icon.set_pixmap(_icon("instance_light.png"))
        self.sel_text = QLabel("Нет экземпляров")
        self.sel_text.setObjectName("muted")
        self.phase_dot = QLabel("●")
        self.phase_dot.setStyleSheet(f"color:{GREEN}; font-size:10px;")
        self.phase_text = QLabel("Готов")
        self.phase_text.setObjectName("muted")
        self.phase_wrap = QWidget()
        pw = QHBoxLayout(self.phase_wrap)
        pw.setContentsMargins(0, 0, 0, 0)
        pw.setSpacing(GAP_SM)
        pw.addWidget(self.phase_dot)
        pw.addWidget(self.phase_text)
        self.phase_wrap.setVisible(False)
        self.btn_play = QPushButton("Играть")
        self.btn_play.setObjectName("accent")
        self.btn_play.setEnabled(False)
        self.btn_play.clicked.connect(self._play)
        br.addWidget(self.sel_icon, 0, Qt.AlignVCenter)
        br.addWidget(self.sel_text, 1, Qt.AlignVCenter)
        br.addWidget(self.phase_wrap, 0, Qt.AlignVCenter)
        br.addWidget(self.btn_play, 0, Qt.AlignVCenter)

        center.addWidget(bottom)
        body.addLayout(center, 1)
        root.addLayout(body, 1)

        self._last_sig = None
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._tick_job)
        self._cfg_timer = QTimer(self)
        self._cfg_timer.timeout.connect(self.refresh)
        self._cfg_timer.start(2000)

        self._set_nav("instances")
        self.refresh()

    # ----- window chrome -----

    def showEvent(self, event):
        super().showEvent(event)
        apply_dark_titlebar(self)

    def closeEvent(self, event):
        try:
            self.api._closing = True
        except Exception:
            pass
        self._poll.stop()
        self._cfg_timer.stop()
        event.accept()

    def _nav_btn(self, text, key, icon_name=""):
        btn = NavButton(text, _icon(icon_name) if icon_name else None, icon_size=24, gap=14)
        btn.clicked.connect(lambda: self._set_nav(key))
        btn.setProperty("navKey", key)
        return btn

    def _set_nav(self, key: str):
        self.nav = key
        for b in (self.nav_inst, self.nav_prof, self.nav_set):
            active = b.property("navKey") == key
            b.setObjectName("navActive" if active else "nav")
            b.set_active(active)
            b.style().unpolish(b)
            b.style().polish(b)
        self.btn_new.setVisible(key == "instances")
        self.btn_import.setVisible(key == "instances")
        self.btn_add_profile.setVisible(key == "profiles")
        self._render()

    # ----- data -----
    def refresh(self):
        try:
            self.state = self.api.get_state() or {}
        except Exception as e:
            print("get_state:", e)
            self.state = {}
        self._apply_bottom()
        sig = json.dumps(
            {k: self.state.get(k) for k in ("instances", "active_instance", "profiles", "active_profile", "settings")},
            sort_keys=True, default=str,
        )
        if sig != self._last_sig:
            self._last_sig = sig
            self._render()

    def _apply_bottom(self):
        st = self.state
        instances = st.get("instances") or []
        active = st.get("active_instance") or ""
        inst = next((i for i in instances if i.get("id") == active), None)
        if not inst:
            self.sel_text.setText("Нет экземпляров")
            self.sel_text.setObjectName("muted")
            self.sel_icon.set_pixmap(_instance_pixmap(None))
            self.phase_wrap.setVisible(False)
            self.btn_play.setEnabled(False)
        else:
            kind = inst.get("kind") or "vanilla"
            loader = {"forge": "Forge", "fabric": "Fabric", "quilt": "Quilt", "neoforge": "NeoForge"}.get(kind, "Vanilla")
            self.sel_text.setText(f"{inst.get('name')}  ·  Minecraft {inst.get('mc')}  ·  {loader}")
            self.sel_text.setObjectName("")
            self.sel_text.setStyleSheet(f"color:{FG}; font-weight:600;")
            self.sel_icon.set_pixmap(_instance_pixmap(inst))
            self.phase_wrap.setVisible(True)
            self.btn_play.setEnabled(not st.get("busy") and bool(st.get("active_profile")))
        self.profile_name.setText(st.get("profile_label") or "—")
        av = _file_url_to_path(st.get("profile_avatar") or "")
        if av and Path(av).is_file():
            self.profile_avatar.set_pixmap(QPixmap(av))
        # Единый источник статуса — get_job_status(): раньше сюда шло состояние без счётчика
        # файлов, и надпись каждые пару секунд перебивалась («Загрузка игры» ↔ «Загрузка файлов игры»)
        try:
            job = self.api.get_job_status() or st
        except Exception:
            job = st
        self._apply_job(job)

    def _apply_job(self, job: dict):
        """Статус справа внизу + круговой прогресс вместо иконки экземпляра."""
        phase = job.get("phase") or "ready"
        busy = bool(job.get("busy"))
        stage = job.get("stage") or "game"
        done = int(job.get("progress_done") or 0)
        total = int(job.get("progress_total") or 0)
        status = (job.get("status") or "").strip()
        self._set_phase(phase, stage, done=done, total=total, status=status, busy=busy)
        downloading = busy and phase == "downloading"
        # также кольцо при скачивании во время запуска
        if busy and phase == "launching" and total > 0 and done < total:
            downloading = True
        self.sel_icon.set_progress(downloading, job.get("progress"))

    def _set_phase(
        self,
        phase: str,
        stage: str = "game",
        done: int = 0,
        total: int = 0,
        status: str = "",
        busy: bool = False,
    ):
        labels = {
            "ready": ("Готов", GREEN),
            "downloading": ("Загрузка Java" if stage == "java" else "Загрузка игры", "#4aa3ff"),
            "launching": ("Запуск игры", "#f0c14b"),
            "running": ("Запущен", GREEN),
            "error": ("Ошибка", "#e81123"),
        }
        text, color = labels.get(phase, labels["ready"])
        # Подробный прогресс файлов: «Загрузка игры: 12 из 48»
        if busy and phase in ("downloading", "launching") and total > 0:
            label = "Java" if stage == "java" else "игры"
            text = f"Загрузка {label}: {done} из {total}"
        elif busy and phase == "downloading" and status:
            # запасной текст из backend (если счётчика ещё нет)
            if "из" in status or "/" in status:
                text = status
        self.phase_text.setText(text)
        self.phase_dot.setStyleSheet(f"color:{color}; font-size:10px;")

    def _clear_content(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            lay = item.layout()
            if lay:
                while lay.count():
                    it = lay.takeAt(0)
                    if it.widget():
                        it.widget().deleteLater()

    def _render(self):
        self._clear_content()
        if self.nav == "instances":
            self._render_instances()
        elif self.nav == "profiles":
            self._render_profiles()
        else:
            self._render_settings()

    def _render_instances(self):
        instances = (self.state or {}).get("instances") or []
        active = (self.state or {}).get("active_instance") or ""
        if not instances:
            return
        grid = QGridLayout()
        grid.setSpacing(GAP)
        grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        cols = 3
        for i, inst in enumerate(instances):
            kind = (inst.get("kind") or "vanilla").lower()
            loader = {"forge": "Forge", "fabric": "Fabric", "quilt": "Quilt", "neoforge": "NeoForge"}.get(kind, "Vanilla")
            pm = _instance_pixmap(inst)
            iid = inst.get("id")

            def make_select(x=iid):
                return lambda: (self.api.select_instance(x), self.refresh())

            def make_settings(x=iid):
                return lambda: self._open_settings(x)

            def make_folder(x=iid):
                return lambda: self._open_instance_folder(x)

            def make_delete(ins=inst):
                return lambda: self._delete_instance(ins)

            tile = Tile(
                inst.get("name") or "Экземпляр",
                f"Minecraft {inst.get('mc') or '?'}",
                loader,
                pm,
                iid == active,
                make_select(),
                [
                    ("Папка игры", make_folder(), False),
                    ("Настроить", make_settings(), False),
                    ("Удалить", make_delete(), True),
                ],
                dot_color=LOADER_COLORS.get(kind, LOADER_COLORS["vanilla"]),
            )
            tile.instance = inst  # для контекстного меню (ПКМ по карточке)
            grid.addWidget(tile, i // cols, i % cols)
        wrap = QWidget()
        wrap.setContextMenuPolicy(Qt.NoContextMenu)
        wrap.setLayout(grid)
        self.content_layout.addWidget(wrap)
        self.content_layout.addStretch()

    def _render_profiles(self):
        profiles = (self.state or {}).get("profiles") or []
        active = (self.state or {}).get("active_profile") or ""
        grid = QGridLayout()
        grid.setSpacing(GAP)
        grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        cols = 3
        for i, p in enumerate(profiles):
            path = _file_url_to_path(p.get("avatar") or "")
            pm = QPixmap(path) if path and Path(path).is_file() else _icon("steve_head_64.png")
            pid = p.get("id")
            ptype = "Ely.by" if p.get("type") == "ely" else "Локальный"

            def make_select(x=pid):
                return lambda: (self.api.select_profile(x), self.refresh())

            def make_settings(profile=p):
                return lambda: self._edit_profile(profile)

            def make_delete(x=pid, name=p.get("name")):
                return lambda: self._delete_profile(x, name)

            tile = Tile(
                p.get("name") or pid,
                ptype,
                "Активный" if pid == active else "Профиль",
                pm,
                pid == active,
                make_select(),
                [("Настроить", make_settings(), False), ("Удалить", make_delete(), True)],
                fill_icon=True,
            )
            grid.addWidget(tile, i // cols, i % cols)
        wrap = QWidget()
        wrap.setContextMenuPolicy(Qt.NoContextMenu)
        wrap.setLayout(grid)
        self.content_layout.addWidget(wrap)
        self.content_layout.addStretch()

    def _render_settings(self):
        st = (self.state or {}).get("settings") or {}
        form = QVBoxLayout()
        form.setSpacing(GAP)
        form.setContentsMargins(0, 0, 0, 0)

        lbl1 = QLabel("Папка игры")
        form.addWidget(lbl1)

        row = QHBoxLayout()
        row.setSpacing(GAP)
        row.setContentsMargins(0, 0, 0, 0)
        self.game_dir = QLineEdit(st.get("game_dir") or "")
        self.game_dir.setReadOnly(True)
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._browse_dir)
        row.addWidget(self.game_dir, 1)
        row.addWidget(browse)
        form.addLayout(row)

        hint = QLabel("Сюда скачиваются версии, библиотеки и экземпляры.")
        hint.setObjectName("muted")
        form.addWidget(hint)

        lbl2 = QLabel("Язык интерфейса")
        form.addWidget(lbl2)
        self.lang = QComboBox()
        self.lang.addItem("Как в системе", "auto")
        self.lang.addItem("Русский", "ru")
        self.lang.addItem("English", "en")
        idx = self.lang.findData(st.get("language") or "auto")
        if idx >= 0:
            self.lang.setCurrentIndex(idx)
        self.lang.setFixedWidth(CTRL_W_LG)
        form.addWidget(self.lang)

        save = QPushButton("Сохранить")
        save.setObjectName("accent")
        save.setFixedWidth(CTRL_W_SM)
        save.clicked.connect(self._save_settings)
        form.addWidget(save)
        form.addStretch()

        wrap = QWidget()
        wrap.setLayout(form)
        self.content_layout.addWidget(wrap)

    # ----- content context menu (ПКМ по полю) -----
    def _on_content_context_menu(self, pos):
        # Не показываем, если клик пришёл с кнопки «⋮» на карточке
        sender = self.sender()
        if sender is None:
            return
        # map to global
        global_pos = sender.mapToGlobal(pos) if hasattr(sender, "mapToGlobal") else pos
        # если под курсором — кнопка меню карточки, не перехватываем
        w = self.childAt(self.mapFromGlobal(global_pos))
        while w is not None:
            if isinstance(w, QToolButton) and w.objectName() == "menuBtn":
                return
            w = w.parentWidget() if hasattr(w, "parentWidget") else None

        menu = QMenu(self)
        if self.nav == "instances":
            # ПКМ по карточке экземпляра — действия этого экземпляра
            tile = self._tile_at(global_pos)
            if tile is not None:
                inst = tile.instance
                for caption, cb in (
                    ("Папка игры", lambda: self._open_instance_folder(inst.get("id"))),
                    ("Настроить", lambda: self._open_settings(inst.get("id"))),
                    ("Удалить", lambda: self._delete_instance(inst)),
                ):
                    act = QAction(caption, menu)
                    act.triggered.connect(cb)
                    menu.addAction(act)
                menu.addSeparator()
            act_new = QAction("Новый экземпляр", menu)
            act_new.triggered.connect(self._create_instance)
            menu.addAction(act_new)
            act_imp = QAction("Импортировать сборку", menu)
            act_imp.triggered.connect(self._import)
            menu.addAction(act_imp)
        elif self.nav == "profiles":
            act_add = QAction("Добавить профиль", menu)
            act_add.triggered.connect(self._add_profile)
            menu.addAction(act_add)
        else:
            return
        menu.exec(global_pos)

    def _tile_at(self, global_pos):
        """Карточка экземпляра под точкой (глобальные координаты) или None."""
        w = self.childAt(self.mapFromGlobal(global_pos))
        while w is not None:
            if isinstance(w, Tile) and getattr(w, "instance", None) is not None:
                return w
            w = w.parentWidget()
        return None

    # ----- actions -----
    def _open_instance_folder(self, iid: str):
        res = self.api.open_instance_folder(iid) or {}
        if res.get("error"):
            show_warning(self, "Ошибка", res["error"])

    def _create_instance(self):
        dlg = CreateInstanceDialog(self.api, self)
        if dlg.exec() == QDialog.Accepted:
            cid = dlg.created_id
            self.refresh()
            if cid:
                self.api.prepare_instance(cid)
                self._set_phase("downloading")
                self.sel_icon.set_progress(True, None)
                self._poll.start(900)

    def _import(self):
        self.api.import_instance()
        self.refresh()

    def _add_profile(self):
        dlg = ProfileDialog(self.api, self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    def _open_settings(self, iid: str):
        dlg = InstanceSettingsDialog(self.api, iid, self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    def _delete_instance(self, inst):
        if not ask_yes_no(self, "Удалить экземпляр", f"Удалить «{inst.get('name')}»?"):
            return
        self.api.delete_instance(inst.get("id"), False)
        self.refresh()

    def _edit_profile(self, profile):
        dlg = ProfileEditDialog(self.api, profile, self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    def _delete_profile(self, pid, name):
        if not ask_yes_no(self, "Удалить профиль", f"Удалить «{name}»?"):
            return
        self.api.delete_profile(pid)
        self.refresh()

    def _browse_dir(self):
        self.api.browse_game_dir()
        self.refresh()

    def _save_settings(self):
        self.api.save_settings({"language": self.lang.currentData()})
        self.refresh()

    def _play(self):
        res = self.api.play() or {}
        if res.get("error"):
            show_warning(self, "Ошибка", res["error"])
            self._set_phase("error")
            return
        # «Загрузка» появится сама, только если backend реально начнёт скачивать файлы
        self._set_phase("launching")
        self.sel_icon.set_progress(False)
        self._poll.start(900)

    def _tick_job(self):
        job = self.api.get_job_status() or {}
        self._apply_job(job)
        if not job.get("busy"):
            self._poll.stop()
            self.refresh()


