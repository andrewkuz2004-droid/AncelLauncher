# -*- coding: utf-8 -*-
"""Ядро лаунчера: конфиг, экземпляры, Modrinth, запуск игры.
UI (Qt) обращается только к LauncherAPI.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import re
import threading
import time
import types
import traceback
from pathlib import Path

# корень проекта (рядом с ancel_engine.py), не папка core/
ROOT = Path(__file__).resolve().parent.parent


def _load_launcher_module():
    """Загрузить модуль движка лаунчера (без устаревшего UI).

    Файл грузим через SourceFileLoader; в собранном exe (где исходника рядом
    нет) модуль подтягивается обычным импортом.
    ROOT = каталог проекта (родитель core/), не core/.
    """
    name = "ancel_launcher_core"
    if name in sys.modules:
        return sys.modules[name]
    path = ROOT / "ancel_engine.py"
    if not path.is_file():
        if getattr(sys, "frozen", False):
            import importlib
            mod = importlib.import_module("ancel_engine")
            sys.modules[name] = mod
            return mod
        raise RuntimeError(f"Не найден ancel_engine.py в {ROOT}")
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader(name, str(path))
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


class LauncherAPI:
    """Methods exposed to JavaScript as window.pywebview.api.*"""

    def __init__(self, window_holder: dict):
        self._window_holder = window_holder  # {"window": webview.Window | None}
        self._mod = None
        self._cfg = None
        self._busy = False
        self._status = ""
        self._job_log = []  # recent status lines
        self._job_title = ""
        self._phase = "ready"  # ready|downloading|launching|running|error
        self._stage = "game"  # что сейчас качается: "java" | "game"
        self._progress = None  # доля 0..1; None — неизвестно (бегущее кольцо)
        # True во время «Играть»: «загрузка» показывается только при реальном
        # скачивании файлов, а проверка уже установленного — это «запуск».
        self._live_dl = False
        self._closing = False
        self._lock = threading.Lock()

    def _L(self):
        if self._mod is None:
            self._mod = _load_launcher_module()
        return self._mod

    def _load_cfg(self):
        L = self._L()
        self._cfg = L.load_config_and_paths()
        L.migrate_profiles(self._cfg)
        L.migrate_instances(self._cfg)
        return self._cfg

    def _save(self):
        L = self._L()
        if self._cfg is not None:
            L.save_config(self._cfg)

    def _infer_phase(self, msg: str) -> str | None:
        low = (msg or "").lower()
        if not low:
            return None
        # Java
        if any(k in low for k in ("java", "jre", "runtime", "jvm")):
            return "downloading"
        # Download / assets / libraries
        if any(k in low for k in (
            "скач", "download", "загруз", "asset", "librar", "client.jar",
            "файл", "minecraft", "version", "манифест", "мета",
        )):
            return "downloading"
        if any(k in low for k in ("запуск", "launch", "start", "подготов")):
            return "launching"
        if any(k in low for k in ("игра запущена", "game started", "running", "процесс")):
            return "running"
        if any(k in low for k in ("ошибка", "error", "fail")):
            return "error"
        if any(k in low for k in ("готово", "заверш", "done", "ready")):
            return "ready"
        return None

    def _set_progress(self, done, total):
        """done/total из download_all; total <= 0 — размер заранее неизвестен."""
        try:
            done, total = int(done), int(total)
        except (TypeError, ValueError):
            return
        self._progress_done = max(0, done)
        self._progress_total = max(0, total)
        self._progress = None if total <= 0 else max(0.0, min(1.0, done / total))

    def _push_status(self, msg: str, *, busy: bool | None = None, title: str | None = None,
                     phase: str | None = None, track_stage: bool = False):
        if self._closing:
            return
        msg = (msg or "").strip()
        if title is not None:
            self._job_title = title
        if msg:
            low = msg.lower()
            if "java" in low and any(k in low for k in ("скач", "загруз", "download")):
                self._stage = "java"
            elif track_stage:
                self._stage = "game"
            self._status = msg
            self._job_log.append(msg)
            if len(self._job_log) > 100:
                self._job_log = self._job_log[-100:]
            inferred = phase or self._infer_phase(msg)
            if inferred:
                self._phase = inferred
        if busy is not None:
            self._busy = bool(busy)
            if not busy and self._phase not in ("error", "running"):
                if self._phase != "ready":
                    # keep last meaningful phase until explicitly set
                    pass

    def _net_busy(self) -> bool:
        """Идёт ли прямо сейчас (или только что шло) реальное скачивание файлов."""
        try:
            na = self._L().NET_ACTIVITY
            if na["active"] > 0:
                return True
            return na["last"] > 0 and (time.monotonic() - na["last"]) < 1.5
        except Exception:
            return False

    def _effective_phase(self) -> str:
        phase = self._phase or "ready"
        if self._live_dl and self._busy and phase in ("launching", "downloading"):
            return "downloading" if self._net_busy() else "launching"
        return phase

    def get_job_status(self):
        return {
            "busy": self._busy,
            "status": self._status or "",
            "title": self._job_title or "",
            "log": list(self._job_log),
            "phase": self._effective_phase(),
            "stage": self._stage,
            "progress": self._progress,
            "progress_done": getattr(self, "_progress_done", 0),
            "progress_total": getattr(self, "_progress_total", 0),
        }

    # ----- window chrome -----
    def window_close(self):
        """Закрытие окна без необрабатываемых исключений."""
        if self._closing:
            return True
        self._closing = True
        self._busy = False
        w = self._window_holder.get("window")
        self._window_holder["window"] = None

        def _destroy():
            try:
                if w is not None:
                    w.destroy()
            except Exception:
                pass
            try:
                import webview
                for win in list(getattr(webview, "windows", []) or []):
                    try:
                        win.destroy()
                    except Exception:
                        pass
            except Exception:
                pass

        # destroy на следующем тике — не из середины JS-bridge вызова
        try:
            threading.Timer(0.1, _destroy).start()
        except Exception:
            _destroy()
        return True

    def window_minimize(self):
        w = self._window_holder.get("window")
        if w is not None:
            w.minimize()
        return True

    def window_maximize(self):
        w = self._window_holder.get("window")
        maximized = False
        if w is not None:
            try:
                is_max = bool(getattr(w, "maximized", False))
                if is_max:
                    try:
                        w.restore()
                    except Exception:
                        w.maximize()
                    maximized = False
                else:
                    w.maximize()
                    maximized = True
                if hasattr(w, "maximized"):
                    maximized = bool(w.maximized)
            except Exception:
                traceback.print_exc()
        return {"maximized": maximized}

    # ----- state -----
    def get_state(self):
        try:
            cfg = self._load_cfg()
            L = self._L()
            def _icon_uri(kind: str, mc: str = "") -> str:
                """Иконка по умолчанию: загрузчик; для Vanilla — трава (релиз) или грязь (снапшот/старая версия)."""
                kind = (kind or "vanilla").lower()
                if kind in ("forge", "fabric", "quilt", "neoforge"):
                    name = f"loader_{kind}.png"
                elif re.fullmatch(r"\d+\.\d+(\.\d+)?", (mc or "").strip()) or not (mc or "").strip():
                    name = "loader_vanilla.png"
                else:
                    name = "loader_snapshot.png"
                path = ROOT / "assets" / "icons" / name
                if not path.is_file():
                    path = ROOT / "assets" / "icons" / "vanilla_48.png"
                return path.resolve().as_uri() if path.is_file() else ""

            instances = []
            for inst in cfg.get("instances") or []:
                if not isinstance(inst, dict) or not inst.get("id"):
                    continue
                kind = inst.get("kind") or "vanilla"
                custom = (inst.get("icon_path") or "").strip()
                if custom and Path(custom).is_file():
                    icon_url = Path(custom).resolve().as_uri()
                else:
                    icon_url = _icon_uri(kind, inst.get("mc") or "")
                instances.append({
                    "id": inst["id"],
                    "name": inst.get("name") or "Экземпляр",
                    "mc": inst.get("mc") or "",
                    "kind": kind,
                    "loader_version": inst.get("loader_version") or "",
                    "icon_url": icon_url,
                    "icon_path": custom if custom and Path(custom).is_file() else "",
                })

            def _head_uri(skin_path, name, ptype):
                """Голова из скина в 128 px (чёткий пиксель-арт; на экране уменьшается плавно)."""
                cache = ROOT / "assets" / "cache"
                cache.mkdir(parents=True, exist_ok=True)
                safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name))
                out = cache / f"{ptype}_head_{safe}.png"
                try:  # не перерисовываем, если голова свежее скина
                    if out.is_file() and out.stat().st_mtime >= Path(skin_path).stat().st_mtime:
                        return out.resolve().as_uri()
                except OSError:
                    pass
                head = L.extract_skin_head(skin_path, size=128, rounded=False)
                if head is None or not hasattr(head, "save"):
                    return ""
                head.save(out)
                return out.resolve().as_uri()

            def _profile_avatar(name, ptype):
                try:
                    if ptype == "ely":
                        skin = L.download_ely_skin(name, force=False)
                        if skin and Path(skin).is_file():
                            uri = _head_uri(skin, name, "ely")
                            if uri:
                                return uri
                            if Path(str(skin)).is_file():
                                return Path(str(skin)).resolve().as_uri()
                    skins = cfg.get("profile_skins") or {}
                    sp = skins.get(name)
                    if sp and Path(sp).is_file():
                        uri = _head_uri(sp, name, "local")
                        return uri or Path(sp).resolve().as_uri()
                except Exception:
                    traceback.print_exc()
                return (ROOT / "assets" / "icons" / "steve_head_64.png").resolve().as_uri()

            profiles = []
            for name in cfg.get("player_profiles") or []:
                profiles.append({
                    "id": f"local:{name}",
                    "name": name,
                    "type": "local",
                    "avatar": _profile_avatar(name, "local"),
                })
            for aid, acc in (cfg.get("ely_accounts") or {}).items():
                if isinstance(acc, dict):
                    uname = acc.get("username") or acc.get("name") or aid
                    profiles.append({
                        "id": f"ely:{aid}",
                        "name": uname,
                        "type": "ely",
                        "avatar": _profile_avatar(uname, "ely"),
                    })

            active_profile = cfg.get("active_profile") or ""
            profile_label = "—"
            profile_avatar = (ROOT / "assets" / "icons" / "steve_head_64.png").resolve().as_uri()
            for p in profiles:
                if p["id"] == active_profile:
                    profile_label = p["name"]
                    profile_avatar = p.get("avatar") or profile_avatar
                    break
            if profile_label == "—" and profiles:
                profile_label = profiles[0]["name"]
                profile_avatar = profiles[0].get("avatar") or profile_avatar

            game_dir = str(getattr(L, "GAME_DIR", "") or "")

            return {
                "instances": instances,
                "active_instance": cfg.get("active_instance") or "",
                "profiles": profiles,
                "active_profile": active_profile,
                "profile_label": profile_label,
                "profile_avatar": profile_avatar,
                "settings": {
                    "game_dir": game_dir,
                    "language": cfg.get("language") or "auto",
                },
                "busy": self._busy,
                "status": self._status,
                "phase": self._effective_phase(),
                "stage": self._stage,
                "progress": self._progress,
                "progress_done": getattr(self, "_progress_done", 0),
                "progress_total": getattr(self, "_progress_total", 0),
            }
        except Exception as e:
            traceback.print_exc()
            return {
                "instances": [],
                "active_instance": "",
                "profiles": [],
                "active_profile": "",
                "profile_label": "—",
                "settings": {},
                "busy": False,
                "status": f"Ошибка: {e}",
                "error": str(e),
            }

    def select_instance(self, iid: str):
        cfg = self._load_cfg()
        if any(i.get("id") == iid for i in (cfg.get("instances") or []) if isinstance(i, dict)):
            cfg["active_instance"] = iid
            self._save()
        return True

    def select_profile(self, pid: str):
        cfg = self._load_cfg()
        cfg["active_profile"] = pid
        # also keep nickname for local
        if pid.startswith("local:"):
            cfg["nickname"] = pid.split(":", 1)[1]
            cfg["account_type"] = "local"
        elif pid.startswith("ely:"):
            cfg["account_type"] = "ely"
        self._save()
        return True

    def save_settings(self, payload: dict | None = None):
        cfg = self._load_cfg()
        payload = payload or {}
        if "language" in payload:
            cfg["language"] = payload["language"]
        self._save()
        return True

    def browse_game_dir(self):
        """Открыть системный Qt-диалог выбора папки игры."""
        try:
            from PySide6.QtWidgets import QFileDialog
            path = QFileDialog.getExistingDirectory(None, "Папка игры")
            if path:
                cfg = self._load_cfg()
                cfg["game_dir"] = path
                self._save()
                L = self._L()
                L.apply_game_dir(cfg)
            return True
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e)}

    def open_instance_folder(self, instance_id: str):
        """Открыть папку игры экземпляра (сохранения, моды, настройки) в проводнике."""
        try:
            cfg = self._load_cfg()
            inst = next((i for i in (cfg.get("instances") or []) if i.get("id") == instance_id), None)
            if not inst:
                return {"error": "Экземпляр не найден"}
            path = self._L().instance_path(inst)
            path.mkdir(parents=True, exist_ok=True)
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                return {"error": f"Не удалось открыть папку:\n{path}"}
            return {"ok": True, "path": str(path)}
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e)}

    def list_mc_versions(self, show_all: bool = False):
        """Список версий Minecraft из манифеста Mojang."""
        try:
            # Кэш на 10 минут — манифест меняется редко, а запрос блокирует UI
            cache = getattr(self, "_mc_versions_cache", None)
            now = time.time()
            if cache and (now - cache.get("ts", 0)) < 600:
                versions = cache["versions"]
                latest = cache["latest"]
            else:
                L = self._L()
                manifest = L.http_get_json(L.MANIFEST_URL)
                versions = []
                latest = manifest.get("latest", {}).get("release", "")
                for v in manifest.get("versions") or []:
                    versions.append({
                        "id": v["id"],
                        "type": v.get("type") or "release",
                    })
                self._mc_versions_cache = {
                    "ts": now,
                    "versions": versions,
                    "latest": latest,
                }
            if not show_all:
                versions = [v for v in versions if v.get("type") == "release"]
            # releases first already in manifest order (newest first)
            return {"versions": versions, "latest": latest}
        except Exception as e:
            traceback.print_exc()
            return {"versions": [], "latest": "1.20.1", "error": str(e)}

    def list_available_loaders(self, mc: str):
        """Типы загрузчиков, для которых существуют сборки выбранной версии Minecraft."""
        try:
            L = self._L()
            available, errors = L.check_loader_types(mc)
            return {"loaders": available, "errors": errors}
        except Exception as e:
            traceback.print_exc()
            return {"loaders": [], "errors": [str(e)]}

    def list_loader_versions(self, kind: str, mc: str):
        """Версии Fabric/Quilt/Forge/NeoForge для выбранной MC."""
        kind = (kind or "").lower().strip()
        if not kind or kind in ("vanilla", "none"):
            return {"versions": []}
        try:
            L = self._L()
            items = L.fetch_loader_versions(kind, mc) or []
            out = []
            for it in items:
                if isinstance(it, dict):
                    out.append({
                        "version": it.get("version") or it.get("label") or "",
                        "label": it.get("label") or it.get("version") or "",
                        "stable": bool(it.get("stable", True)),
                    })
                else:
                    out.append({"version": str(it), "label": str(it), "stable": True})
            return {"versions": out}
        except Exception as e:
            traceback.print_exc()
            return {"versions": [], "error": str(e)}

    def search_modpacks(self, query: str = "", offset: int = 0):
        """Поиск сборок (modpack) на Modrinth."""
        try:
            L = self._L()
            import urllib.parse
            facets = '[["project_type:modpack"]]'
            params = {
                "query": query or "",
                "limit": 20,
                "offset": int(offset or 0),
                "index": "relevance",
                "facets": facets,
            }
            url = L.MODRINTH_API + "/search?" + urllib.parse.urlencode(params)
            data = L.http_get_json(url)
            hits = []
            for h in data.get("hits") or []:
                hits.append({
                    "project_id": h.get("project_id") or h.get("slug") or "",
                    "slug": h.get("slug") or "",
                    "title": h.get("title") or h.get("slug") or "Сборка",
                    "description": (h.get("description") or "")[:160],
                    "icon_url": h.get("icon_url") or "",
                    "downloads": h.get("downloads") or 0,
                    "categories": h.get("categories") or [],
                })
            return {"hits": hits, "total": data.get("total_hits") or len(hits)}
        except Exception as e:
            traceback.print_exc()
            return {"hits": [], "total": 0, "error": str(e)}

    def install_modpack(self, project_id: str, icon_url: str = ""):
        """Скачать modpack с Modrinth в фоне (не блокирует UI)."""
        project_id = (project_id or "").strip()
        if not project_id:
            return {"error": "Не указан проект"}
        if self._busy:
            return {"error": "Уже выполняется операция"}

        self._modpack_result = None
        self._job_log = []
        self._phase = "downloading"
        self._push_status("Загрузка версий сборки…", busy=True, title="Установка сборки", phase="downloading")

        def worker(pid=project_id, icon=icon_url or ""):
            import tempfile
            import zipfile
            import uuid
            import urllib.parse
            import urllib.request
            import shutil

            try:
                L = self._L()
                cfg = self._load_cfg()
                versions = L.http_get_json(
                    f"{L.MODRINTH_API}/project/{urllib.parse.quote(pid)}/version"
                )
                if not isinstance(versions, list) or not versions:
                    self._modpack_result = {"error": "У проекта нет версий"}
                    self._push_status("Ошибка", busy=False, phase="error")
                    return

                chosen = None
                fileinfo = None
                for ver in versions:
                    files = ver.get("files") or []
                    primary = next((f for f in files if f.get("primary")), None) or (files[0] if files else None)
                    if not primary:
                        continue
                    fn = (primary.get("filename") or "").lower()
                    url = primary.get("url") or ""
                    if fn.endswith(".mrpack") or "mrpack" in fn or url.endswith(".mrpack"):
                        chosen = ver
                        fileinfo = primary
                        break
                if not chosen or not fileinfo:
                    self._modpack_result = {"error": "Не найден .mrpack файл у проекта"}
                    self._push_status("Ошибка", busy=False, phase="error")
                    return

                self._push_status("Скачивание архива сборки (1 из 1)…", busy=True, phase="downloading")
                self._set_progress(0, 1)
                tmpdir = Path(tempfile.mkdtemp(prefix="ancel_mrpack_"))
                try:
                    mrpack_path = tmpdir / (fileinfo.get("filename") or "pack.mrpack")
                    L.download(fileinfo["url"], mrpack_path, sha1=(fileinfo.get("hashes") or {}).get("sha1"))
                    self._set_progress(1, 1)
                    self._push_status("Распаковка архива…", busy=True, phase="downloading")

                    extract_dir = tmpdir / "extracted"
                    extract_dir.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(mrpack_path, "r") as zf:
                        zf.extractall(extract_dir)

                    meta = L.detect_instance_metadata(extract_dir) if hasattr(L, "detect_instance_metadata") else None
                    if meta is None:
                        meta = {"name": "", "mc": "", "kind": None, "loader_version": ""}
                        for idx_name in ("modrinth.index.json", "modrinth.index"):
                            idx = extract_dir / idx_name
                            if not idx.is_file():
                                continue
                            data = json.loads(idx.read_text(encoding="utf-8"))
                            deps = data.get("dependencies") or {}
                            meta["name"] = str(data.get("name") or "")
                            meta["mc"] = str(deps.get("minecraft") or "")
                            if deps.get("fabric-loader"):
                                meta["kind"] = "fabric"
                                meta["loader_version"] = str(deps["fabric-loader"])
                            elif deps.get("quilt-loader"):
                                meta["kind"] = "quilt"
                                meta["loader_version"] = str(deps["quilt-loader"])
                            elif deps.get("forge"):
                                meta["kind"] = "forge"
                                meta["loader_version"] = str(deps["forge"])
                            elif deps.get("neoforge"):
                                meta["kind"] = "neoforge"
                                meta["loader_version"] = str(deps["neoforge"])
                            break

                    proj_icon = (icon or "").strip()
                    try:
                        proj = L.http_get_json(f"{L.MODRINTH_API}/project/{urllib.parse.quote(pid)}")
                        title = proj.get("title") or proj.get("slug") or "Сборка Modrinth"
                        if not proj_icon:
                            proj_icon = (proj.get("icon_url") or "").strip()
                    except Exception:
                        title = "Сборка Modrinth"

                    name = (meta.get("name") or title or "Сборка").strip()[:40]
                    existing = {i.get("name") for i in (cfg.get("instances") or []) if isinstance(i, dict)}
                    base, n = name, 2
                    while name in existing:
                        name = f"{base} {n}"[:40]
                        n += 1

                    mc = meta.get("mc") or ""
                    if not mc:
                        self._modpack_result = {"error": "Не удалось определить версию Minecraft у сборки"}
                        self._push_status("Ошибка", busy=False, phase="error")
                        return

                    kind = meta.get("kind")
                    loader_version = meta.get("loader_version") or ""
                    iid = uuid.uuid4().hex[:12]
                    rel_dir = f"instances/{iid}"
                    dest = L.GAME_DIR / rel_dir
                    dest.mkdir(parents=True, exist_ok=True)

                    self._push_status("Подготовка файлов сборки…", busy=True, phase="downloading")

                    def _files_progress(done, total, stage="download"):
                        try:
                            d, t = int(done), max(1, int(total))
                        except (TypeError, ValueError):
                            return
                        self._set_progress(d, t)
                        if stage == "install":
                            self._push_status(
                                f"Установка файлов: {d} из {t}",
                                busy=True,
                                phase="downloading",
                            )
                        else:
                            self._push_status(
                                f"Скачивание файлов: {d} из {t}",
                                busy=True,
                                phase="downloading",
                            )

                    L.copy_instance_files(extract_dir, dest, progress_cb=_files_progress)

                    icon_path = ""
                    if proj_icon:
                        try:
                            ext = ".png"
                            low = proj_icon.lower().split("?", 1)[0]
                            for e in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                                if low.endswith(e):
                                    ext = e
                                    break
                            icon_file = dest / f"icon{ext}"
                            # Прямое скачивание с User-Agent (CDN Modrinth)
                            req = urllib.request.Request(
                                proj_icon,
                                headers={"User-Agent": "AncelLauncher/1.0"},
                            )
                            with urllib.request.urlopen(req, timeout=30) as resp:
                                data = resp.read()
                            if data:
                                icon_file.write_bytes(data)
                            if icon_file.is_file() and icon_file.stat().st_size > 0:
                                icon_path = str(icon_file.resolve())
                        except Exception:
                            traceback.print_exc()
                            icon_path = ""

                    # перечитать cfg — мог измениться
                    cfg = self._load_cfg()
                    inst = {
                        "id": iid,
                        "name": name,
                        "mc": mc,
                        "kind": kind,
                        "loader_version": loader_version if kind else "",
                        "dir": rel_dir,
                        "ram_gb": int(cfg.get("ram_gb", 6) or 6),
                    }
                    if icon_path:
                        inst["icon_path"] = icon_path
                    cfg.setdefault("instances", []).append(inst)
                    cfg["active_instance"] = iid
                    self._cfg = cfg
                    self._save()
                    self._modpack_result = {"id": iid, "instance": inst}
                    self._phase = "ready"
                    self._push_status("Готово", busy=False, phase="ready")
                finally:
                    try:
                        shutil.rmtree(tmpdir, ignore_errors=True)
                    except Exception:
                        pass
            except Exception as e:
                traceback.print_exc()
                self._modpack_result = {"error": str(e)}
                self._phase = "error"
                self._push_status(f"Ошибка: {e}", busy=False, phase="error")

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    def get_modpack_install_result(self):
        """Результат фоновой установки сборки (или None, пока идёт)."""
        return getattr(self, "_modpack_result", None)


    def search_content(self, content_type: str, query: str, mc: str, loader: str = "", offset: int = 0, limit: int = 20):
        """Поиск модов / шейдеров / ресурспаков на Modrinth (с пагинацией)."""
        try:
            L = self._L()
            import urllib.parse
            ctype = (content_type or "mod").lower()
            if ctype not in ("mod", "shader", "resourcepack"):
                ctype = "mod"
            try:
                limit = max(1, min(50, int(limit or 20)))
            except (TypeError, ValueError):
                limit = 20
            try:
                offset = max(0, int(offset or 0))
            except (TypeError, ValueError):
                offset = 0
            facets = [[f"project_type:{ctype}"]]
            if mc:
                facets.append([f"versions:{mc}"])
            if loader and ctype == "mod":
                facets.append([f"categories:{loader}"])
            params = {
                "query": query or "",
                "limit": limit,
                "offset": offset,
                "index": "relevance" if (query or "").strip() else "downloads",
                "facets": json.dumps(facets),
            }
            url = L.MODRINTH_API + "/search?" + urllib.parse.urlencode(params)
            data = L.http_get_json(url)
            hits = []
            for h in data.get("hits") or []:
                hits.append({
                    "project_id": h.get("project_id") or h.get("slug") or "",
                    "slug": h.get("slug") or "",
                    "title": h.get("title") or h.get("slug") or "",
                    "description": (h.get("description") or "")[:140],
                    "icon_url": h.get("icon_url") or "",
                    "downloads": h.get("downloads") or 0,
                })
            total = int(data.get("total_hits") or 0)
            return {
                "hits": hits,
                "total": total,
                "offset": offset,
                "limit": limit,
                "page": (offset // limit) + 1,
                "pages": max(1, (total + limit - 1) // limit) if total else 1,
            }
        except Exception as e:
            traceback.print_exc()
            return {"hits": [], "total": 0, "offset": 0, "limit": 20, "page": 1, "pages": 1, "error": str(e)}

    def get_content_details(self, project_id: str, slug: str = ""):
        """Полная карточка проекта Modrinth; повторяет запрос по slug при сбое ID."""
        import urllib.parse
        L = self._L()
        errors = []
        candidates = []
        for candidate in (project_id, slug):
            candidate = str(candidate or "").strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        for candidate in candidates:
            try:
                project = L.http_get_json(
                    f"{L.MODRINTH_API}/project/{urllib.parse.quote(candidate)}"
                )
                if not isinstance(project, dict) or not (project.get("id") or project.get("slug")):
                    raise ValueError("Modrinth вернул неполную карточку проекта")
                return {
                    "ok": True,
                    "project_id": project.get("id") or project_id,
                    "slug": project.get("slug") or slug or str(project_id),
                    "title": project.get("title") or project.get("slug") or str(project_id),
                    "description": project.get("body") or project.get("description") or "",
                    "icon_url": project.get("icon_url") or "",
                    "source_url": f"https://modrinth.com/{project.get('project_type') or 'mod'}/{project.get('slug') or slug or project_id}",
                    "downloads": project.get("downloads") or 0,
                    "followers": project.get("followers") or 0,
                    "license": (project.get("license") or {}).get("name", "") if isinstance(project.get("license"), dict) else "",
                }
            except Exception as e:
                errors.append(f"{candidate}: {e}")
        return {"error": "; ".join(errors) or "Не удалось получить карточку проекта", "description": "", "icon_url": ""}

    def get_content_versions(self, project_id: str, mc: str = "", loader: str = "", content_type: str = "mod"):
        """Версии проекта Modrinth, совместимые с экземпляром, для выбора в UI."""
        try:
            import urllib.parse
            L = self._L()
            query = {"game_versions": json.dumps([mc])} if mc else {}
            raw = L.http_get_json(
                f"{L.MODRINTH_API}/project/{urllib.parse.quote(str(project_id))}/version"
                + (("?" + urllib.parse.urlencode(query)) if query else "")
            )
            result = []
            for ver in raw if isinstance(raw, list) else []:
                loaders = [str(x).lower() for x in (ver.get("loaders") or [])]
                game_versions = ver.get("game_versions") or []
                if mc and game_versions and mc not in game_versions:
                    continue
                if content_type == "mod" and loader and loaders and loader.lower() not in loaders:
                    continue
                result.append({
                    "id": ver.get("id") or "",
                    "version_number": ver.get("version_number") or ver.get("name") or "—",
                    "name": ver.get("name") or "",
                    "game_versions": game_versions,
                    "loaders": loaders,
                    "date_published": ver.get("date_published") or "",
                })
            return {"versions": result}
        except Exception as e:
            return {"versions": [], "error": str(e)}

    def install_content(self, instance_id: str, project_id: str, content_type: str = "mod", version_id: str = ""):
        """Установить проект Modrinth и его обязательные зависимости рекурсивно."""
        import urllib.parse
        try:
            L = self._L()
            cfg = self._load_cfg()
            inst = next((i for i in (cfg.get("instances") or []) if i.get("id") == instance_id), None)
            if not inst:
                return {"error": "Экземпляр не найден"}
            mc = inst.get("mc") or ""
            kind = (inst.get("kind") or "").lower()
            dest_root = L.instance_path(inst)
            folder = {"mod": "mods", "shader": "shaderpacks", "resourcepack": "resourcepacks"}.get((content_type or "mod").lower(), "mods")
            dest_dir = dest_root / folder
            dest_dir.mkdir(parents=True, exist_ok=True)
            meta_path = dest_root / ".ancel_content.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
            except Exception:
                meta = {}

            installed, skipped, errors, visited = [], [], [], set()
            self._push_status(
                "Подготовка установки…",
                busy=True,
                title="Установка контента",
                phase="downloading",
            )
            self._set_progress(0, 0)

            def install_one(pid: str, depth: int = 0):
                pid = str(pid or "").strip()
                if not pid or pid in visited or depth > 12:
                    return
                visited.add(pid)
                # Уже установленный проект не скачиваем повторно.
                for filename, entry in meta.items():
                    if isinstance(entry, dict) and entry.get("project_id") == pid and (dest_dir / filename).exists():
                        skipped.append(pid)
                        return
                q = "?" + urllib.parse.urlencode({"game_versions": json.dumps([mc])}) if mc else ""
                versions = L.http_get_json(f"{L.MODRINTH_API}/project/{urllib.parse.quote(pid)}/version{q}")
                if not isinstance(versions, list) or not versions:
                    versions = L.http_get_json(f"{L.MODRINTH_API}/project/{urllib.parse.quote(pid)}/version")
                if not versions:
                    raise RuntimeError(f"Нет совместимых версий: {pid}")
                chosen = None
                if version_id:
                    chosen = next((ver for ver in versions if ver.get("id") == version_id), None)
                for ver in versions:
                    if chosen is not None:
                        break
                    loaders = [str(x).lower() for x in (ver.get("loaders") or [])]
                    if kind and content_type == "mod" and loaders and kind not in loaders:
                        continue
                    if mc and ver.get("game_versions") and mc not in ver.get("game_versions"):
                        continue
                    chosen = ver
                    break
                if chosen is None:
                    raise RuntimeError(f"Нет версии {pid} для Minecraft {mc} / {kind}")
                files = chosen.get("files") or []
                primary = next((f for f in files if f.get("primary")), None) or (files[0] if files else None)
                if not primary or not primary.get("url"):
                    raise RuntimeError(f"Нет файла для скачивания: {pid}")
                filename = Path(primary.get("filename") or f"{pid}.jar").name
                target = dest_dir / filename
                if target.exists():
                    stem, suffix, n = target.stem, target.suffix, 2
                    while target.exists():
                        target = dest_dir / f"{stem}-{n}{suffix}"
                        n += 1
                    filename = target.name
                self._push_status(
                    f"Скачивание {filename}…",
                    busy=True,
                    title="Установка контента",
                    phase="downloading",
                )
                # Для одиночных файлов — неопределённый прогресс (0/0),
                # UI показывает анимацию и текущее имя файла.
                self._set_progress(0, 0)
                L.download(primary["url"], target, sha1=(primary.get("hashes") or {}).get("sha1"))
                self._set_progress(1, 1)
                project = L.http_get_json(f"{L.MODRINTH_API}/project/{urllib.parse.quote(pid)}")
                meta[filename] = {
                    "project_id": pid,
                    "slug": project.get("slug") or pid,
                    "name": project.get("title") or project.get("slug") or pid,
                    "description": (project.get("description") or "")[:500],
                    "icon_url": project.get("icon_url") or "",
                    "version": chosen.get("version_number") or chosen.get("name") or "—",
                    "source": "Modrinth",
                }
                installed.append({"project_id": pid, "filename": filename, "name": meta[filename]["name"]})
                # Modrinth version.dependencies: install required project dependencies only.
                # Optional dependencies remain optional; incompatible dependencies are never installed.
                for dep in (chosen.get("dependencies") or []):
                    dep_type = (dep.get("dependency_type") or "required").lower()
                    dep_pid = dep.get("project_id")
                    if dep_type == "required" and dep_pid:
                        install_one(dep_pid, depth + 1)

            install_one(project_id)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            self._push_status(f"Установлено: {len(installed)}; уже было: {len(skipped)}", busy=False)
            return {"ok": True, "installed": installed, "skipped": skipped, "errors": errors,
                    "filename": installed[0]["filename"] if installed else "", "count": len(installed)}
        except Exception as e:
            traceback.print_exc()
            self._push_status(f"Ошибка: {e}", busy=False)
            return {"error": str(e)}

    def delete_instance(self, instance_id: str, delete_files: bool = False):
        cfg = self._load_cfg()
        L = self._L()
        insts = cfg.get("instances") or []
        inst = next((i for i in insts if i.get("id") == instance_id), None)
        if not inst:
            return {"error": "Не найден"}
        cfg["instances"] = [i for i in insts if i.get("id") != instance_id]
        if cfg.get("active_instance") == instance_id:
            cfg["active_instance"] = (cfg["instances"][0]["id"] if cfg["instances"] else "")
        if delete_files:
            try:
                import shutil
                path = L.instance_path(inst)
                if path.exists() and path != L.GAME_DIR:
                    shutil.rmtree(path, ignore_errors=True)
            except Exception:
                traceback.print_exc()
        self._save()
        return {"ok": True}

    def update_instance(self, instance_id: str, payload: dict | None = None):
        payload = payload or {}
        cfg = self._load_cfg()
        inst = next((i for i in (cfg.get("instances") or []) if i.get("id") == instance_id), None)
        if not inst:
            return {"error": "Не найден"}
        name = str(payload.get("name") or inst.get("name") or "").strip()[:40]
        if not name:
            return {"error": "Введите название"}
        # uniqueness
        for other in cfg.get("instances") or []:
            if other.get("id") != instance_id and other.get("name") == name:
                return {"error": "Имя уже занято"}
        inst["name"] = name
        if payload.get("mc"):
            inst["mc"] = str(payload["mc"]).strip()
        kind_raw = payload.get("kind")
        if kind_raw is not None:
            if not kind_raw or str(kind_raw).lower() in ("", "vanilla", "none"):
                inst["kind"] = None
                inst["loader_version"] = ""
            else:
                inst["kind"] = str(kind_raw).lower()
                inst["loader_version"] = str(payload.get("loader_version") or inst.get("loader_version") or "")
        if "ram_gb" in payload:
            try:
                inst["ram_gb"] = max(1, min(32, int(payload["ram_gb"])))
            except (TypeError, ValueError):
                pass
        if "icon_path" in payload:
            inst["icon_path"] = str(payload.get("icon_path") or "").strip()
        self._save()
        return {"ok": True, "instance": inst}

    def get_instance(self, instance_id: str):
        cfg = self._load_cfg()
        inst = next((i for i in (cfg.get("instances") or []) if i.get("id") == instance_id), None)
        if not inst:
            return {"error": "Не найден"}
        return {"instance": dict(inst)}

    def create_instance(self, payload: dict | None = None):
        """Создать экземпляр из формы (не авто-ваниль 1.20.1)."""
        payload = payload or {}
        cfg = self._load_cfg()
        import uuid

        name = str(payload.get("name") or "").strip() or "Новый экземпляр"
        if len(name) > 40:
            name = name[:40]
        existing = {i.get("name") for i in (cfg.get("instances") or []) if isinstance(i, dict)}
        if name in existing:
            return {"error": f"Экземпляр «{name}» уже есть"}

        mc = str(payload.get("mc") or "").strip()
        if not mc:
            # fallback latest release
            try:
                data = self.list_mc_versions(False)
                mc = data.get("latest") or "1.20.1"
            except Exception:
                mc = "1.20.1"

        kind_raw = payload.get("kind")
        kind = None
        if kind_raw and str(kind_raw).lower() not in ("", "vanilla", "none"):
            kind = str(kind_raw).lower()

        try:
            ram_gb = int(payload.get("ram_gb") or 4)
        except (TypeError, ValueError):
            ram_gb = 4
        ram_gb = max(1, min(32, ram_gb))

        loader_version = str(payload.get("loader_version") or "").strip()
        # If loader chosen but version empty — pick first available
        if kind and not loader_version:
            try:
                lv = self.list_loader_versions(kind, mc)
                vers = lv.get("versions") or []
                if vers:
                    loader_version = vers[0].get("version") or ""
            except Exception:
                pass

        icon_path = str(payload.get("icon_path") or "").strip()

        iid = uuid.uuid4().hex[:12]
        inst = {
            "id": iid,
            "name": name,
            "mc": mc,
            "kind": kind,
            "loader_version": loader_version if kind else "",
            "dir": f"instances/{iid}",
            "ram_gb": ram_gb,
        }
        if icon_path:
            inst["icon_path"] = icon_path

        cfg.setdefault("instances", []).append(inst)
        cfg["active_instance"] = iid
        cfg["ram_gb"] = ram_gb
        self._save()
        return {"id": iid, "instance": inst}


    def pick_instance_icon(self):
        """Выбор PNG-иконки через системный диалог."""
        try:
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(
                None, "Иконка экземпляра", "", "PNG (*.png);;Все файлы (*)"
            )
            if not path:
                return {"cancelled": True}
            return {"path": path, "url": Path(path).resolve().as_uri()}
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e)}

    def import_instance(self):
        try:
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(
                None, "Импорт сборки", "", "Сборки (*.zip *.mrpack);;Все файлы (*)"
            )
            if not path:
                return {"cancelled": True}
            # Full import lives in Tk App — stub message for now
            return {
                "error": "Импорт через Web UI будет подключён к полному импорту "
                "из AncelLauncher (пока выберите создание экземпляра)."
            }
        except Exception as e:
            return {"error": str(e)}

    def edit_instance(self, iid: str):
        return {"info": "Редактор экземпляра в Web UI — следующий шаг.", "id": iid}

    def add_local_profile(self, name: str = ""):
        cfg = self._load_cfg()
        L = self._L()
        name = (name or "").strip() or "Player"
        if not L.NICK_RE.fullmatch(name):
            return {"error": "Никнейм: 3–16 символов (латиница, цифры, _)"}
        profiles = list(cfg.get("player_profiles") or [])
        if name in profiles:
            return {"error": "Такой локальный профиль уже есть"}
        if name in (cfg.get("ely_accounts") or {}):
            return {"error": "Имя занято аккаунтом Ely.by"}
        profiles.append(name)
        cfg["player_profiles"] = profiles
        cfg["active_profile"] = f"local:{name}"
        cfg["nickname"] = name
        self._save()
        return {"ok": True, "id": f"local:{name}"}

    def add_ely_account(self, login: str = "", password: str = ""):
        """Вход Ely.by через authserver + сохранение токенов и скина."""
        login = (login or "").strip()
        password = password or ""
        if not login or not password:
            return {"error": "Укажите логин и пароль Ely.by"}
        cfg = self._load_cfg()
        L = self._L()
        try:
            auth_fn = (
                getattr(L, "ely_authenticate", None)
                or getattr(L, "ely_auth", None)
                or getattr(L, "login_ely", None)
            )
            if not callable(auth_fn):
                return {"error": "Модуль авторизации Ely.by недоступен"}
            try:
                account = auth_fn(login, password)
            except Exception as auth_err:
                return {"error": str(auth_err) or "Не удалось войти в Ely.by"}

            if not isinstance(account, dict):
                return {"error": "Пустой ответ сервера Ely.by"}

            username = (account.get("username") or account.get("name") or "").strip()
            if not username:
                return {"error": "Сервер Ely.by не вернул имя профиля"}
            normalized = {
                "username": username,
                "uuid": (account.get("uuid") or account.get("id") or "").replace("-", ""),
                "access_token": account.get("access_token") or account.get("accessToken") or "",
                "client_token": account.get("client_token") or account.get("clientToken") or "",
            }
            if not normalized["access_token"]:
                return {"error": "Сервер Ely.by не вернул access token"}

            ely = dict(cfg.get("ely_accounts") or {})
            for k in list(ely.keys()):
                acc = ely.get(k) or {}
                if k == username or k == login:
                    ely.pop(k, None)
                    continue
                if (acc.get("uuid") or "").replace("-", "") == normalized["uuid"]:
                    ely.pop(k, None)
            ely[username] = normalized
            cfg["ely_accounts"] = ely
            locals_ = [n for n in (cfg.get("player_profiles") or []) if n != username]
            cfg["player_profiles"] = locals_
            cfg["active_profile"] = f"ely:{username}"
            cfg["nickname"] = username
            cfg["account_type"] = "ely.by"
            self._save()
            try:
                L.download_ely_skin(username, force=True)
            except Exception:
                traceback.print_exc()
            return {"ok": True, "id": f"ely:{username}", "username": username}
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e)}

    def delete_profile(self, pid: str):
        cfg = self._load_cfg()
        L = self._L()
        pid = pid or ""
        if pid.startswith("local:"):
            name = pid.split(":", 1)[1]
            cfg["player_profiles"] = [n for n in (cfg.get("player_profiles") or []) if n != name]
        elif pid.startswith("ely:"):
            name = pid.split(":", 1)[1]
            ely = dict(cfg.get("ely_accounts") or {})
            ely.pop(name, None)
            cfg["ely_accounts"] = ely
        else:
            return {"error": "Неизвестный профиль"}
        if cfg.get("active_profile") == pid:
            plist = []
            for n in cfg.get("player_profiles") or []:
                plist.append(f"local:{n}")
            for n in (cfg.get("ely_accounts") or {}):
                plist.append(f"ely:{n}")
            cfg["active_profile"] = plist[0] if plist else ""
        self._save()
        return {"ok": True}

    def update_profile(self, pid: str, name: str = ""):
        cfg = self._load_cfg()
        L = self._L()
        name = (name or "").strip()
        if not name or not L.NICK_RE.fullmatch(name):
            return {"error": "Никнейм: 3–16 символов (латиница, цифры, _)"}
        if pid.startswith("local:"):
            old = pid.split(":", 1)[1]
            profiles = list(cfg.get("player_profiles") or [])
            if old not in profiles:
                return {"error": "Не найден"}
            if name != old and name in profiles:
                return {"error": "Имя занято"}
            profiles = [name if n == old else n for n in profiles]
            cfg["player_profiles"] = profiles
            if cfg.get("active_profile") == pid:
                cfg["active_profile"] = f"local:{name}"
                cfg["nickname"] = name
            skins = dict(cfg.get("profile_skins") or {})
            if old in skins:
                skins[name] = skins.pop(old)
                cfg["profile_skins"] = skins
        elif pid.startswith("ely:"):
            return {"error": "Ник Ely.by меняется на сайте ely.by"}
        else:
            return {"error": "Неизвестный профиль"}
        self._save()
        return {"ok": True}

    def sync_ely_profile(self, pid: str):
        """Обновить скин и данные Ely.by."""
        if not pid.startswith("ely:"):
            return {"error": "Только для Ely.by"}
        name = pid.split(":", 1)[1]
        L = self._L()
        cfg = self._load_cfg()
        acc = (cfg.get("ely_accounts") or {}).get(name)
        if not acc:
            return {"error": "Аккаунт не найден"}
        try:
            skin = L.download_ely_skin(name, force=True)
            # refresh token if helper exists
            refresh = getattr(L, "ely_refresh", None)
            if callable(refresh):
                try:
                    updated = refresh(acc)
                    if isinstance(updated, dict):
                        cfg["ely_accounts"][name] = updated
                        self._save()
                except Exception:
                    traceback.print_exc()
            return {
                "ok": True,
                "skin": str(skin) if skin else "",
                "avatar": (Path(skin).resolve().as_uri() if skin and Path(skin).is_file() else ""),
            }
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e)}

    def list_installed_content(self, instance_id: str, content_type: str = "mod"):
        cfg = self._load_cfg()
        L = self._L()
        inst = next((i for i in (cfg.get("instances") or []) if i.get("id") == instance_id), None)
        if not inst:
            return {"items": [], "error": "Экземпляр не найден"}
        folder = {"mod": "mods", "shader": "shaderpacks", "resourcepack": "resourcepacks"}.get(
            (content_type or "mod").lower(), "mods"
        )
        try:
            root = L.instance_path(inst) / folder
            root.mkdir(parents=True, exist_ok=True)
            items = []
            try:
                meta_path = L.instance_path(inst) / ".ancel_content.json"
                content_meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
            except Exception:
                content_meta = {}
            for f in sorted(root.iterdir()):
                if not f.is_file():
                    continue
                if f.name.startswith("."):
                    continue
                disabled = f.name.endswith(".disabled")
                display = f.name[:-9] if disabled else f.name
                entry_meta = content_meta.get(display, {}) or content_meta.get(f.name, {}) or {}
                items.append({
                    "name": entry_meta.get("name") or display,
                    "filename": f.name,
                    "disabled": disabled,
                    "path": str(f),
                    "project_id": entry_meta.get("project_id", ""),
                    "slug": entry_meta.get("slug", ""),
                    "description": entry_meta.get("description", ""),
                    "icon_url": entry_meta.get("icon_url", ""),
                    "version": entry_meta.get("version", "—"),
                    "source": entry_meta.get("source", "Локальный файл"),
                })
            return {"items": items}
        except Exception as e:
            traceback.print_exc()
            return {"items": [], "error": str(e)}

    def toggle_content(self, instance_id: str, content_type: str, filename: str):
        cfg = self._load_cfg()
        L = self._L()
        inst = next((i for i in (cfg.get("instances") or []) if i.get("id") == instance_id), None)
        if not inst:
            return {"error": "Не найден"}
        folder = {"mod": "mods", "shader": "shaderpacks", "resourcepack": "resourcepacks"}.get(
            (content_type or "mod").lower(), "mods"
        )
        path = L.instance_path(inst) / folder / filename
        if not path.is_file():
            return {"error": "Файл не найден"}
        try:
            if path.name.endswith(".disabled"):
                new = path.with_name(path.name[:-9])
            else:
                new = path.with_name(path.name + ".disabled")
            path.rename(new)
            return {"ok": True, "filename": new.name, "disabled": new.name.endswith(".disabled")}
        except Exception as e:
            return {"error": str(e)}

    def delete_content(self, instance_id: str, content_type: str, filename: str):
        cfg = self._load_cfg()
        L = self._L()
        inst = next((i for i in (cfg.get("instances") or []) if i.get("id") == instance_id), None)
        if not inst:
            return {"error": "Не найден"}
        folder = {"mod": "mods", "shader": "shaderpacks", "resourcepack": "resourcepacks"}.get(
            (content_type or "mod").lower(), "mods"
        )
        path = L.instance_path(inst) / folder / filename
        if not path.is_file():
            return {"error": "Файл не найден"}
        try:
            path.unlink()
            return {"ok": True}
        except Exception as e:
            return {"error": str(e)}


    def prepare_instance(self, instance_id: str):
        """Скачать Minecraft, Java и загрузчик без запуска игры."""
        if self._busy:
            return {"error": "Уже выполняется операция"}
        try:
            cfg = self._load_cfg()
            L = self._L()
            inst = next((i for i in (cfg.get("instances") or []) if i.get("id") == instance_id), None)
            if not inst:
                return {"error": "Экземпляр не найден"}
            try:
                loader = L.loader_for_instance(inst)
                game_dir = L.instance_path(inst)
            except Exception as e:
                return {"error": str(e)}
            mc = inst.get("mc") or ""
            entry = {"id": mc, "type": "release"}
            try:
                manifest = L.http_get_json(L.MANIFEST_URL)
                for v in manifest.get("versions") or []:
                    if v.get("id") == mc:
                        entry = v
                        break
            except Exception:
                pass
            self._job_log = []
            self._stage = "game"
            self._progress = None
            self._progress_done = 0
            self._progress_total = 0
            self._live_dl = False
            self._phase = "downloading"
            self._push_status(
                f"Установка «{inst.get('name')}»…",
                busy=True,
                title="Загрузка игры и Java",
                phase="downloading",
            )

            def worker():
                try:
                    def log(msg):
                        if self._closing:
                            return
                        self._push_status(str(msg), busy=True)

                    def progress(done, total):
                        if self._closing:
                            return
                        try:
                            self._set_progress(done, total)
                            d, tot = int(done), max(1, int(total))
                            label = "Java" if self._stage == "java" else "игры"
                            self._push_status(
                                f"Загрузка {label}: {d} из {tot}",
                                busy=True,
                                phase="downloading",
                            )
                        except Exception:
                            pass

                    def status(msg):
                        if self._closing:
                            return
                        self._push_status(str(msg), busy=True, phase="downloading", track_stage=True)

                    L.install_and_launch(
                        entry,
                        loader,
                        "Player",
                        max(1, min(32, int(inst.get("ram_gb", 4) or 4))) * 1024,
                        log=log,
                        progress=progress,
                        status=status,
                        launch_game=False,
                        game_dir=game_dir,
                    )
                    if not self._closing:
                        self._phase = "ready"
                        self._progress = None
                        self._push_status("Установка завершена", busy=False, phase="ready")
                except Exception as e:
                    if self._closing:
                        return
                    traceback.print_exc()
                    self._phase = "error"
                    self._push_status(f"Ошибка: {e}", busy=False, phase="error")

            threading.Thread(target=worker, daemon=True).start()
            return {"started": True}
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e)}

    def play(self):
        """Скачать/подготовить версию и запустить игру (фон)."""
        if self._busy:
            return {"error": "Уже выполняется операция"}
        try:
            cfg = self._load_cfg()
            L = self._L()
            inst = L.current_instance(cfg)
            if not inst:
                return {"error": "Сначала выберите экземпляр"}

            profile = L.active_profile(cfg)
            if profile is None:
                return {"error": "Сначала создайте профиль"}
            nick = profile["name"]
            auth_account = None
            if profile.get("type") == "local":
                if not L.NICK_RE.fullmatch(nick):
                    return {"error": "Некорректный никнейм (3–16: латиница, цифры, _)"}
            else:
                # Ключ в ely_accounts — username (ник), не email
                ely_map = cfg.get("ely_accounts") or {}
                raw = dict(ely_map.get(nick) or {})
                if not raw:
                    # fallback: поиск по username внутри записей
                    for k, v in ely_map.items():
                        if isinstance(v, dict) and (v.get("username") or k) == nick:
                            raw = dict(v)
                            break
                if not raw:
                    return {"error": "Аккаунт Ely.by не найден — войдите снова"}
                # Нормализация ключей (старые записи могли хранить accessToken)
                auth_account = {
                    "username": raw.get("username") or nick,
                    "uuid": (raw.get("uuid") or "").replace("-", ""),
                    "access_token": raw.get("access_token") or raw.get("accessToken") or "",
                    "client_token": raw.get("client_token") or raw.get("clientToken") or "",
                }
                if not auth_account["access_token"] or not auth_account["client_token"]:
                    return {"error": "Сессия Ely.by устарела — войдите в аккаунт заново"}
                nick = auth_account["username"] or nick

            try:
                loader = L.loader_for_instance(inst)
                game_dir = L.instance_path(inst)
            except Exception as e:
                return {"error": str(e)}

            mc = inst.get("mc") or ""
            entry = {"id": mc, "type": "release"}
            try:
                manifest = L.http_get_json(L.MANIFEST_URL)
                for v in manifest.get("versions") or []:
                    if v.get("id") == mc:
                        entry = v
                        break
            except Exception:
                pass

            ram_mb = max(1, min(32, int(inst.get("ram_gb", 4) or 4))) * 1024
            instance_id = inst.get("id")
            cfg["version"] = mc
            cfg["active_instance"] = instance_id
            self._save()

            self._job_log = []
            self._stage = "game"
            self._progress = None
            self._progress_done = 0
            self._progress_total = 0
            self._live_dl = True
            try:
                L.NET_ACTIVITY["last"] = 0.0  # забыть прошлые загрузки
            except Exception:
                pass
            self._phase = "launching"
            self._push_status(
                f"Подготовка «{inst.get('name')}»…",
                busy=True,
                title="Запуск игры",
                phase="launching",
            )

            def worker():
                try:
                    def base_phase():
                        # «загрузка» подмешивает _effective_phase по реальной сети;
                        # здесь фаза либо «запущен», либо «запуск» — тексты сообщений
                        # (в т.ч. вывод самой игры) её больше не меняют
                        return "running" if self._phase == "running" else "launching"

                    def log(msg):
                        if self._closing:
                            return
                        self._push_status(str(msg), busy=True, phase=base_phase())

                    def progress(done, total):
                        if self._closing:
                            return
                        try:
                            self._set_progress(done, total)
                            d, tot = int(done), max(1, int(total))
                            label = "Java" if self._stage == "java" else "игры"
                            self._push_status(
                                f"Загрузка {label}: {d} из {tot}",
                                busy=True,
                                phase=base_phase(),
                            )
                        except Exception:
                            pass

                    def status(msg):
                        if self._closing:
                            return
                        s = str(msg)
                        low = s.lower()
                        if any(k in low for k in ("игра запущена", "game started", "game_started")):
                            phase = "running"
                        else:
                            phase = base_phase()
                        self._push_status(s, busy=True, phase=phase, track_stage=True)

                    # Обновляем сессию Ely.by и скин перед запуском
                    launch_nick = nick
                    launch_auth = auth_account
                    if launch_auth:
                        try:
                            status("Проверка сессии Ely.by…")
                            ensure = getattr(L, "ely_ensure_session", None)
                            if callable(ensure):
                                fresh, changed = ensure(launch_auth)
                                launch_auth = dict(fresh)
                                launch_nick = launch_auth.get("username") or launch_nick
                                if changed:
                                    try:
                                        cfg2 = self._load_cfg()
                                        ely = dict(cfg2.get("ely_accounts") or {})
                                        # перенос ключа при смене ника
                                        old_keys = [k for k, v in ely.items()
                                                    if k == nick or (isinstance(v, dict) and (v.get("username") == nick))]
                                        for k in old_keys:
                                            ely.pop(k, None)
                                        ely[launch_nick] = {
                                            "username": launch_nick,
                                            "uuid": (launch_auth.get("uuid") or "").replace("-", ""),
                                            "access_token": launch_auth.get("access_token") or "",
                                            "client_token": launch_auth.get("client_token") or "",
                                        }
                                        cfg2["ely_accounts"] = ely
                                        if (cfg2.get("active_profile") or "").startswith("ely:"):
                                            cfg2["active_profile"] = f"ely:{launch_nick}"
                                            cfg2["nickname"] = launch_nick
                                        self._cfg = cfg2
                                        self._save()
                                    except Exception:
                                        traceback.print_exc()
                            try:
                                L.download_ely_skin(launch_nick, force=False)
                            except Exception:
                                pass
                        except Exception as sess_err:
                            self._phase = "error"
                            self._push_status(
                                f"Сессия Ely.by: {sess_err}",
                                busy=False,
                                phase="error",
                            )
                            return

                    duration = L.install_and_launch(
                        entry,
                        loader,
                        launch_nick,
                        ram_mb,
                        log=log,
                        progress=progress,
                        status=status,
                        launch_game=True,
                        auth_account=launch_auth,
                        game_dir=game_dir,
                    ) or 0.0
                    if self._closing:
                        return
                    if instance_id and duration > 0:
                        try:
                            cfg2 = self._load_cfg()
                            for i in cfg2.get("instances") or []:
                                if i.get("id") == instance_id:
                                    i["playtime"] = int(i.get("playtime") or 0) + int(duration)
                                    break
                            self._cfg = cfg2
                            self._save()
                        except Exception:
                            traceback.print_exc()
                    self._phase = "ready"
                    self._push_status("Игра завершена", busy=False, phase="ready")
                except Exception as e:
                    if self._closing:
                        return
                    traceback.print_exc()
                    self._phase = "error"
                    self._push_status(f"Ошибка: {e}", busy=False, phase="error")

            threading.Thread(target=worker, daemon=True).start()
            return {
                "status": "Запуск начат — смотрите статус установки",
                "started": True,
            }
        except Exception as e:
            traceback.print_exc()
            self._push_status(f"Ошибка: {e}", busy=False)
            return {"error": str(e)}

