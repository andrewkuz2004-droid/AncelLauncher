#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AncelLauncher — простой лаунчер Minecraft: Java Edition (версия 1.0_pre-alpha).

Возможности:
  * скачивание и запуск версий игры (офлайн / Ely.by);
  * автоматическая Java;
  * загрузчики: Fabric, Quilt, Forge, NeoForge;
  * установки (сборки) и менеджер контента через Modrinth;
  * локальные профили и аккаунты Ely.by.

Запуск:  pythonw AncelLauncher.pyw   или   python AncelLauncher.pyw
Данные игры: папка minecraft/ рядом с программой.
Сборка exe: см. scripts/build_windows.bat и README.md.

Сделан с помощью нейросети.
"""

import hashlib
import http.client
import json
import os
import ctypes
import platform
import queue
import webbrowser
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

# ----------------------------------------------------------------------------
# Настройки и константы
# ----------------------------------------------------------------------------

def _runtime_base():
    """Каталог с данными пользователя (рядом с .pyw или .exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resource_base():
    """Каталог ассетов: в разработке — рядом со скриптом; в exe — _MEIPASS."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _runtime_base()
RESOURCE_DIR = _resource_base()
CONFIG_FILE = APP_DIR / "launcher_config.json"
LOG_FILE = APP_DIR / "launcher.log"
SKINS_DIR = APP_DIR / "skins"
LOCALE_DIR = RESOURCE_DIR / "locale"

# Папка игры (версии, libraries, instances…). По умолчанию — minecraft/ рядом с лаунчером.
# Может быть переопределена в настройках (ключ game_dir в конфиге).
GAME_DIR = APP_DIR / "minecraft"


def default_game_dir():
    return APP_DIR / "minecraft"


def apply_game_dir(cfg=None):
    """Обновить глобальный GAME_DIR из конфига. Пустой game_dir → папка по умолчанию."""
    global GAME_DIR
    if cfg is None:
        try:
            cfg = load_config()
        except Exception:
            cfg = {}
    custom = str(cfg.get("game_dir") or "").strip()
    if custom:
        GAME_DIR = Path(custom)
    else:
        GAME_DIR = default_game_dir()
    try:
        GAME_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return GAME_DIR

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
ASSETS_URL = "https://resources.download.minecraft.net"
LAUNCHER_VERSION = "1.0_alpha"
USER_AGENT = f"AncelLauncher/{LAUNCHER_VERSION} (desktop Minecraft launcher)"
MODRINTH_API = "https://api.modrinth.com/v2"

FABRIC_META = "https://meta.fabricmc.net/v2"
QUILT_META = "https://meta.quiltmc.org/v3"
FORGE_META_URL = "https://files.minecraftforge.net/net/minecraftforge/forge/maven-metadata.json"
FORGE_PROMOS_URL = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged"

# Последние успешно проверенные результаты совместимости загрузчиков.
# Нужны, чтобы временный сбой API не выглядел как отсутствие поддержки.
LOADER_AVAILABILITY_CACHE = {}
LOADER_VERSIONS_CACHE = {}  # (kind, mc) -> list items
_FORGE_META_CACHE = {"data": None}

# Что показываем в списке «Тип» и как это называется внутри программы

# ----------------------------------------------------------------------------
# Локализация
# ----------------------------------------------------------------------------

SUPPORTED_LANGS = ("ru", "en")


def detect_system_language():
    """Язык ОС → 'ru' | 'en' (fallback)."""
    try:
        import locale as _locale
        lang, _ = _locale.getdefaultlocale() or (None, None)
        if not lang:
            lang = _locale.getlocale()[0]
        if lang:
            code = lang.replace("-", "_").split("_")[0].lower()
            if code.startswith("ru"):
                return "ru"
            if code.startswith("en"):
                return "en"
    except Exception:
        pass
    return "ru"


def resolve_language(cfg_value):
    """'auto' / 'ru' / 'en' → конкретный код языка."""
    v = (cfg_value or "auto").strip().lower()
    if v in SUPPORTED_LANGS:
        return v
    return detect_system_language()


_I18N_CACHE = {}
_CURRENT_LANG = "ru"


def load_strings(lang):
    lang = lang if lang in SUPPORTED_LANGS else "ru"
    if lang in _I18N_CACHE:
        return _I18N_CACHE[lang]
    path = LOCALE_DIR / f"{lang}.json"
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    if not data and lang != "ru":
        return load_strings("ru")
    _I18N_CACHE[lang] = data
    return data


def set_language(lang):
    global _CURRENT_LANG
    _CURRENT_LANG = resolve_language(lang)
    load_strings(_CURRENT_LANG)
    return _CURRENT_LANG


def t(key, **kwargs):
    """Перевод строки по ключу."""
    strings = load_strings(_CURRENT_LANG)
    s = strings.get(key)
    if s is None:
        s = load_strings("ru").get(key, key)
    if kwargs:
        try:
            return s.format(**kwargs)
        except (KeyError, ValueError):
            return s
    return s


LOADER_NAMES = {"Vanilla": None, "Fabric": "fabric", "Quilt": "quilt",
                "Forge": "forge", "NeoForge": "neoforge"}

DEFAULT_CONFIG = {
    "nickname": "Player", "player_profiles": ["Player"], "active_player_profile": "Player",
    "version": "", "ram_gb": 4, "show_all": False,
    "java_path": "", "loader_kind": "Vanilla", "loader_version": "",
    "modified_version": False, "show_console": False,
    "profile_skins": {}, "account_type": "local",
    "ely_accounts": {},
    "active_profile": "", "instances": None, "active_instance": "",
    "language": "auto",
    "game_dir": "",
}

# Название нашей ОС и архитектуры в терминах Mojang
if sys.platform.startswith("win"):
    OS_NAME = "windows"
elif sys.platform == "darwin":
    OS_NAME = "osx"
else:
    OS_NAME = "linux"

_machine = platform.machine().lower()
if _machine in ("arm64", "aarch64"):
    ARCH = "arm64"
elif _machine in ("x86", "i386", "i686"):
    ARCH = "x86"
else:
    ARCH = "x86_64"

# Чтобы при запуске Java не мигало чёрное окно консоли (только Windows)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class LauncherError(Exception):
    """Понятная пользователю ошибка."""


# ----------------------------------------------------------------------------
# Конфиг
# ----------------------------------------------------------------------------

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text("utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def load_config_and_paths():
    """Загрузить конфиг и применить пути (папка игры)."""
    cfg = load_config()
    apply_game_dir(cfg)
    return cfg


def save_config(cfg):
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass


def read_json(path):
    return json.loads(Path(path).read_text("utf-8"))


# ----------------------------------------------------------------------------
# Скачивание файлов
# ----------------------------------------------------------------------------

def http_get_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def http_get_json(url):
    return json.loads(http_get_text(url))


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_local = threading.local()

# Сколько одновременных соединений разрешаем к каждому серверу Mojang.
# Если открыть слишком много, сервер начинает обрывать соединения.
HOST_LIMITS = {
    "libraries.minecraft.net": 4,
    "piston-data.mojang.com": 4,
    "resources.download.minecraft.net": 24,
}
_host_slots = {}
_host_slots_lock = threading.Lock()


def host_slot(host):
    with _host_slots_lock:
        if host not in _host_slots:
            _host_slots[host] = threading.BoundedSemaphore(HOST_LIMITS.get(host, 8))
        return _host_slots[host]


def fetch_to_file(url, dest):
    """Скачивает url в файл dest. Соединение с сервером переиспользуется
    (отдельное для каждого потока) — это быстрее, чем открывать новое
    на каждый файл."""
    parts = urllib.parse.urlsplit(url)
    key = (parts.scheme, parts.netloc)
    path = parts.path + ("?" + parts.query if parts.query else "")
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}

    with host_slot(parts.netloc):
        for attempt in range(2):
            conn = conns.get(key)
            if conn is None:
                cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
                conn = conns[key] = cls(parts.netloc, timeout=30)
            try:
                conn.request("GET", path, headers={"User-Agent": USER_AGENT})
                response = conn.getresponse()
                if response.status != 200:
                    response.read()
                    raise IOError(f"HTTP {response.status}")
                with open(dest, "wb") as f:
                    shutil.copyfileobj(response, f)
                return
            except (http.client.HTTPException, OSError):
                # соединение могло устареть — закрываем и пробуем на новом
                conn.close()
                conns.pop(key, None)
                if attempt == 1:
                    raise


def fetch_with_urllib(url, dest):
    """Запасной способ: каждый раз новое соединение."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with host_slot(urllib.parse.urlsplit(url).netloc):
        with urllib.request.urlopen(req, timeout=30) as response, open(dest, "wb") as f:
            shutil.copyfileobj(response, f)


def download(url, path, sha1=None, size=None, verify_existing=True):
    """Скачивает файл, если его ещё нет (или он повреждён). 5 попыток."""
    path = Path(path)
    if path.exists():
        ok = True
        if size is not None and path.stat().st_size != size:
            ok = False
        if ok and verify_existing and sha1 and sha1_of(path) != sha1:
            ok = False
        if ok:
            return

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    last_error = None
    for attempt in range(5):
        try:
            if attempt < 3:
                fetch_to_file(url, tmp)
            else:
                fetch_with_urllib(url, tmp)  # запасной способ
            if sha1 and sha1_of(tmp) != sha1:
                raise IOError(t("checksum_fail"))
            os.replace(tmp, path)
            return
        except Exception as e:  # noqa: BLE001
            last_error = e
            if attempt < 4:
                time.sleep(min(2 ** attempt, 8) * 0.5)
    raise LauncherError(
        t("download_fail", url=url, error=f"{type(last_error).__name__}: {last_error}"))


def download_all(tasks, log, progress, status, label):
    """Скачивает список файлов (url, путь, sha1, размер, проверять_ли_существующие)
    параллельно. Неудавшиеся файлы в конце повторяет по одному."""
    total = len(tasks)
    progress(0, max(total, 1))
    pool = ThreadPoolExecutor(max_workers=24)
    failed = []
    try:
        futures = {pool.submit(download, *t): t for t in tasks}
        done = 0
        for future in as_completed(futures):
            done += 1
            try:
                future.result()
            except Exception:  # noqa: BLE001
                failed.append(futures[future])
            if done % 5 == 0 or done == total:
                progress(done, total)
                status(t("download_progress", label=label, done=done, total=total))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    for i, task in enumerate(failed, 1):
        status(t("download_retry", i=i, n=len(failed)))
        log(t("download_retry_log", url=task[0]))
        time.sleep(2)
        download(*task)


# ----------------------------------------------------------------------------
# Правила Mojang (какие библиотеки и аргументы нужны на этой системе)
# ----------------------------------------------------------------------------

def os_matches(rule):
    if "name" in rule and rule["name"] != OS_NAME:
        return False
    if "arch" in rule and rule["arch"] != ARCH:
        return False
    if "version" in rule and not re.match(rule["version"], platform.version()):
        return False
    return True


def rules_allow(rules):
    if not rules:
        return True
    allowed = False
    for rule in rules:
        applies = True
        if "os" in rule:
            applies = os_matches(rule["os"])
        if applies and "features" in rule:
            # Особые функции (демо-режим, свой размер окна и т.п.) у нас выключены
            applies = not any(rule["features"].values())
        if applies:
            allowed = rule["action"] == "allow"
    return allowed


def substitute(text, variables):
    return re.sub(r"\$\{(\w+)\}", lambda m: variables.get(m.group(1), m.group(0)), text)


def expand_args(items, variables):
    result = []
    for item in items:
        if isinstance(item, str):
            values = [item]
        else:
            if not rules_allow(item.get("rules")):
                continue
            value = item["value"]
            values = [value] if isinstance(value, str) else value
        result.extend(substitute(v, variables) for v in values)
    return result


def maven_path(name):
    """'группа:артефакт:версия[:классификатор]' -> путь файла в maven-репозитории."""
    ext = "jar"
    if "@" in name:
        name, ext = name.split("@", 1)
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = f"-{parts[3]}" if len(parts) > 3 else ""
    return f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}{classifier}.{ext}"


def lib_key(lib):
    """Ключ для отбрасывания дублей библиотек: группа:артефакт[:классификатор]."""
    parts = lib.get("name", "").split(":")
    return ":".join(parts[:2] + parts[3:4])


# ----------------------------------------------------------------------------
# Поиск Java и Java от Mojang
# ----------------------------------------------------------------------------

def java_major(java_path):
    """Возвращает основную версию Java (8, 17, 21...) или None."""
    try:
        out = subprocess.run(
            [str(java_path), "-version"],
            capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW,
        ).stderr
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out)
    if not m:
        return None
    major = int(m.group(1))
    if major == 1 and m.group(2):  # старый формат: 1.8.0 -> 8
        major = int(m.group(2))
    return major


def java_candidates():
    exe = "java.exe" if OS_NAME == "windows" else "java"
    found = []
    on_path = shutil.which("java")
    if on_path:
        found.append(Path(on_path))
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        found.append(Path(java_home) / "bin" / exe)

    roots = []
    if OS_NAME == "windows":
        program_dirs = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
        vendors = ("Eclipse Adoptium", "Java", "Microsoft", "Zulu", "BellSoft",
                   "Amazon Corretto", "Eclipse Foundation")
        for pf in filter(None, program_dirs):
            roots.extend(Path(pf) / v for v in vendors)
        roots.append(Path.home() / ".jdks")
    else:
        roots += [Path("/usr/lib/jvm"), Path.home() / ".jdks", Path.home() / ".sdkman/candidates/java"]

    for root in roots:
        if root.is_dir():
            for sub in root.iterdir():
                candidate = sub / "bin" / exe
                if candidate.exists():
                    found.append(candidate)
    return found


def find_java(required):
    """Ищет Java нужной версии (или ближайшую более новую).
    Возвращает (путь, версия, список_найденных)."""
    best = None
    found = []
    seen = set()
    for path in java_candidates():
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        major = java_major(path)
        if major is None:
            continue
        found.append((major, path))
        if major == required:
            return path, major, found
        if major > required and (best is None or major < best[0]):
            best = (major, path)
    if best:
        return best[1], best[0], found
    return None, None, found


RUNTIME_INDEX_URL = (
    "https://launchermeta.mojang.com/v1/products/java-runtime/"
    "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json"
)


def runtime_platform():
    """Название платформы в каталоге Java от Mojang."""
    if OS_NAME == "windows":
        return {"x86_64": "windows-x64", "x86": "windows-x86", "arm64": "windows-arm64"}[ARCH]
    if OS_NAME == "osx":
        return "mac-os-arm64" if ARCH == "arm64" else "mac-os"
    return "linux-i386" if ARCH == "x86" else "linux"


def ensure_mojang_java(component, log, progress, status):
    """Скачивает ту же Java, которую ставит официальный лаунчер
    (например, java-runtime-delta = Java 21). Возвращает путь к java."""
    runtime_dir = GAME_DIR / "runtime" / component
    marker = runtime_dir / ".installed"

    cached, cached_info = None, {}
    if marker.exists():
        try:
            cached_info = json.loads(marker.read_text("utf-8"))
            cached = runtime_dir / cached_info["java"]
            if not cached.exists():
                cached = None
        except (OSError, ValueError, KeyError):
            cached = None

    status(t("java_check"))
    try:
        index = http_get_json(RUNTIME_INDEX_URL)
        entries = index.get(runtime_platform(), {}).get(component) or []
        if not entries:
            raise LauncherError(t("java_missing_mojang", component=component))
        manifest_info = entries[0]["manifest"]
        files = http_get_json(manifest_info["url"])["files"]
    except Exception:  # noqa: BLE001
        if cached:  # нет сети — используем то, что уже скачано
            return cached
        raise

    if cached and cached_info.get("sha1") == manifest_info.get("sha1"):
        return cached

    java_rel = next((p for p in files if p.endswith("bin/java.exe") or p.endswith("bin/java")), None)
    if not java_rel:
        raise LauncherError(t("java_bin_missing"))

    log(t("java_download", component=component))
    tasks = []
    for rel, info in files.items():
        target = runtime_dir / rel
        kind = info.get("type")
        if kind == "directory":
            target.mkdir(parents=True, exist_ok=True)
        elif kind == "file":
            raw = info["downloads"]["raw"]
            tasks.append((raw["url"], target, raw.get("sha1"), raw.get("size"), True))
    download_all(tasks, log, progress, status, t("java_download_label"))

    if OS_NAME != "windows":  # права на запуск и символические ссылки
        for rel, info in files.items():
            target = runtime_dir / rel
            if info.get("type") == "file" and info.get("executable"):
                os.chmod(target, 0o755)
            elif info.get("type") == "link" and not target.exists():
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(info["target"], target)
                except OSError:
                    pass

    marker.write_text(json.dumps({"sha1": manifest_info.get("sha1"), "java": java_rel}), "utf-8")
    return runtime_dir / java_rel


def get_java(vjson, log, progress, status):
    """Java для версии: свой путь из конфига -> Java от Mojang -> системная."""
    java_info = vjson.get("javaVersion", {})
    required = java_info.get("majorVersion", 8)
    component = java_info.get("component", "jre-legacy")
    java = java_ver = None
    found = []

    custom = load_config().get("java_path")
    if custom:  # путь, указанный вручную в launcher_config.json
        m = java_major(custom)
        if m and m >= required:
            java, java_ver = Path(custom), m
        else:
            log(t("java_custom_bad", required=required, path=custom))

    if not java:
        try:
            java = ensure_mojang_java(component, log, progress, status)
            java_ver = t("java_from_mojang", required=required, component=component)
        except Exception as e:  # noqa: BLE001
            log(t("java_mojang_fail", error=e))

    if not java:
        java, java_ver, found = find_java(required)
    if not java:
        seen_text = ", ".join(f"Java {m}" for m, _ in found) or t("java_none")
        raise LauncherError(
            t("java_required_fail", required=required, found=seen_text)
        )
    log(t("java_using", version=java_ver, path=java))
    return java


# ----------------------------------------------------------------------------
# Описание версии: чтение и склейка с родителем (inheritsFrom)
# ----------------------------------------------------------------------------

def load_version(vid):
    """Читает описание версии. Если версия «наследуется» от другой
    (так устроены Fabric/Forge и т.д.), склеивает её с родительской."""
    data = read_json(GAME_DIR / "versions" / vid / f"{vid}.json")
    parent_id = data.get("inheritsFrom")
    if not parent_id:
        data["_jar_id"] = data.get("jar", vid)
        return data

    parent = load_version(parent_id)
    merged = dict(parent)
    for key, value in data.items():
        if key == "inheritsFrom":
            continue
        if key == "libraries":
            merged[key] = value + parent.get("libraries", [])  # свои библиотеки — первыми
        elif key == "arguments":
            parent_args = parent.get("arguments", {})
            merged[key] = {k: parent_args.get(k, []) + value.get(k, []) for k in ("jvm", "game")}
        else:
            merged[key] = value
    merged["_jar_id"] = data.get("jar", parent["_jar_id"])
    return merged


def prepare_game(vjson, log, progress, status):
    """Скачивает всё нужное для запуска (библиотеки, клиент, ресурсы)
    и возвращает данные для сборки команды запуска."""
    vid = vjson["id"]
    jar_id = vjson["_jar_id"]
    libs_dir = GAME_DIR / "libraries"
    assets_root = GAME_DIR / "assets"

    tasks = []  # (url, путь, sha1, размер, проверять_ли_существующий)
    classpath = []
    natives_jobs = []
    seen_libs = set()

    for lib in vjson["libraries"]:
        if not rules_allow(lib.get("rules")):
            continue
        key = lib_key(lib)
        if key in seen_libs:  # дубль: оставляем первую (версию загрузчика)
            continue
        seen_libs.add(key)

        dl = lib.get("downloads", {})
        artifact = dl.get("artifact")
        if artifact is None and "name" in lib and "natives" not in lib:
            # Библиотеки Fabric/Quilt: только имя и адрес maven-репозитория
            rel = maven_path(lib["name"])
            base = lib.get("url", "https://libraries.minecraft.net/")
            artifact = {"path": rel, "url": base.rstrip("/") + "/" + rel,
                        "sha1": lib.get("sha1"), "size": lib.get("size")}
        if artifact:
            p = libs_dir / artifact["path"]
            if artifact.get("url"):  # пустой адрес = файл создаёт установщик Forge
                tasks.append((artifact["url"], p, artifact.get("sha1"), artifact.get("size"), True))
            classpath.append(p)

        natives = lib.get("natives")
        if natives and OS_NAME in natives:
            classifier = natives[OS_NAME].replace("${arch}", "32" if ARCH == "x86" else "64")
            nat = dl.get("classifiers", {}).get(classifier)
            if nat:
                p = libs_dir / nat["path"]
                tasks.append((nat["url"], p, nat.get("sha1"), nat.get("size"), True))
                natives_jobs.append((p, lib.get("extract", {}).get("exclude", [])))

    client_jar = GAME_DIR / "versions" / jar_id / f"{jar_id}.jar"
    client = vjson.get("downloads", {}).get("client")
    if client:
        tasks.append((client["url"], client_jar, client.get("sha1"), client.get("size"), True))
    classpath.append(client_jar)

    extra_jvm = []
    log_cfg = vjson.get("logging", {}).get("client")
    if log_cfg:
        f = log_cfg["file"]
        p = assets_root / "log_configs" / f["id"]
        tasks.append((f["url"], p, f.get("sha1"), f.get("size"), True))
        extra_jvm.append(log_cfg["argument"].replace("${path}", str(p)))

    # Индекс ресурсов (звуки, языки, иконки...)
    status(t("assets_list"))
    asset_info = vjson["assetIndex"]
    index_path = assets_root / "indexes" / f"{asset_info['id']}.json"
    download(asset_info["url"], index_path, sha1=asset_info.get("sha1"))
    index = read_json(index_path)

    seen = set()
    for obj in index["objects"].values():
        h = obj["hash"]
        if h in seen:
            continue
        seen.add(h)
        p = assets_root / "objects" / h[:2] / h
        tasks.append((f"{ASSETS_URL}/{h[:2]}/{h}", p, h, obj["size"], False))

    log(t("files_to_check", n=len(tasks)))
    download_all(tasks, log, progress, status, t("download_game_files"))

    # Нативные библиотеки (для старых версий)
    natives_dir = GAME_DIR / "versions" / vid / "natives"
    natives_dir.mkdir(parents=True, exist_ok=True)
    base_dir = natives_dir.resolve()
    for jar, excludes in natives_jobs:
        with zipfile.ZipFile(jar) as z:
            for info in z.infolist():
                if info.is_dir() or any(info.filename.startswith(e) for e in excludes):
                    continue
                target = natives_dir / info.filename
                if not target.resolve().is_relative_to(base_dir):
                    continue
                if target.exists() and target.stat().st_size == info.file_size:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with z.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                except OSError:
                    pass

    # Совсем старые версии хотят ресурсы под обычными именами
    game_assets = assets_root
    if index.get("virtual") or index.get("map_to_resources"):
        if index.get("map_to_resources"):
            copy_to = GAME_DIR / "resources"
        else:
            copy_to = assets_root / "virtual" / "legacy"
            game_assets = copy_to
        for name, obj in index["objects"].items():
            src = assets_root / "objects" / obj["hash"][:2] / obj["hash"]
            dst = copy_to / name
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)

    return {
        "classpath": [str(p) for p in dict.fromkeys(classpath)],
        "natives_dir": natives_dir, "libs_dir": libs_dir, "assets_root": assets_root,
        "game_assets": game_assets, "asset_index": asset_info["id"], "extra_jvm": extra_jvm,
    }


def offline_uuid(nick):
    digest = bytearray(hashlib.md5(("OfflinePlayer:" + nick).encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(digest)).hex


def build_command(vjson, prep, java, game_dir, nick, ram_mb, auth_account=None, authlib_path=None):
    classpath = os.pathsep.join(prep["classpath"])
    variables = {
        "auth_player_name": nick,
        # Для версий-«наследников» это имя исходной версии (так делает и официальный лаунчер)
        "version_name": vjson["_jar_id"],
        "game_directory": str(game_dir),
        "assets_root": str(prep["assets_root"]),
        "assets_index_name": prep["asset_index"],
        "game_assets": str(prep["game_assets"]),
        "auth_uuid": (auth_account.get("uuid", "").replace("-", "") if auth_account else offline_uuid(nick)),
        "auth_access_token": (auth_account.get("access_token", "0") if auth_account else "0"),
        "auth_session": (auth_account.get("access_token", "0") if auth_account else "0"),
        "auth_xuid": "0",
        "clientid": "0",
        "user_type": ("msa" if auth_account else "legacy"),
        "user_properties": "{}",
        "version_type": vjson.get("type", "release"),
        "natives_directory": str(prep["natives_dir"]),
        "library_directory": str(prep["libs_dir"]),
        "launcher_name": "AncelLauncher",
        "launcher_version": LAUNCHER_VERSION,
        "classpath_separator": os.pathsep,
        "classpath": classpath,
    }

    if "arguments" in vjson:  # новый формат (1.13+)
        jvm_args = expand_args(vjson["arguments"]["jvm"], variables)
        game_args = expand_args(vjson["arguments"]["game"], variables)
    else:  # старый формат
        jvm_args = [f"-Djava.library.path={prep['natives_dir']}", "-cp", classpath]
        game_args = [substitute(a, variables) for a in vjson["minecraftArguments"].split()]

    agent_args = [f"-javaagent:{authlib_path}=ely.by"] if auth_account and authlib_path else []
    return (
        [str(java), f"-Xmx{ram_mb}M", f"-Xms{min(ram_mb, 512)}M"]
        + agent_args + prep["extra_jvm"] + jvm_args + [vjson["mainClass"]] + game_args
    )


def stream_process(command, cwd, log):
    """Запускает процесс и выводит его сообщения в журнал. Возвращает код выхода."""
    proc = subprocess.Popen(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
    )
    for line in proc.stdout:
        log(line.rstrip())
    return proc.wait()


# ----------------------------------------------------------------------------
# Загрузчики модов: Fabric, Quilt, Forge, NeoForge
# ----------------------------------------------------------------------------

def mc_tuple(mc):
    """'1.20.1' -> (1, 20, 1); для снапшотов и прочего возвращает None."""
    m = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", mc)
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def numeric_key(version):
    return [int(x) for x in re.findall(r"\d+", version)]


def mark_default(items):
    """Отмечает версию, выбранную по умолчанию: первая стабильная (иначе первая)."""
    if items:
        best = next((i for i in items if i.get("stable", True)), items[0])
        best["default"] = True
    return items


def fetch_loader_versions(kind, mc):
    """Версии загрузчика для версии игры mc. Новые — первыми.
    Каждая: {'version', 'label', 'stable', ...}. Пустой список — не поддерживается."""
    cache_key = (kind, mc)
    if cache_key in LOADER_VERSIONS_CACHE:
        return LOADER_VERSIONS_CACHE[cache_key]

    if kind in ("fabric", "quilt"):
        base = FABRIC_META if kind == "fabric" else QUILT_META
        data = http_get_json(f"{base}/versions/loader/{urllib.parse.quote(mc)}")
        items = []
        for d in data:
            v = d["loader"]["version"]
            stable = d["loader"].get("stable", not re.search(r"alpha|beta|pre|rc", v, re.I))
            items.append({"version": v, "stable": stable,
                          "label": v if stable else t("loader_unstable", v=v)})
        items = mark_default(items)
        LOADER_VERSIONS_CACHE[cache_key] = items
        return items

    mc_ver = mc_tuple(mc)

    if kind == "forge":
        if mc_ver is None:
            raise LauncherError(t("forge_mc_unrecognized"))

        # Основной источник Forge — JSON с сайта загрузчика. Он удобен, но
        # список старых Minecraft может быть неполным. Для старых версий
        # используем Maven metadata, в котором перечислены артефакты Forge.
        slugs = []
        metadata_error = None
        try:
            if _FORGE_META_CACHE["data"] is None:
                _FORGE_META_CACHE["data"] = http_get_json(FORGE_META_URL)
            forge_meta = _FORGE_META_CACHE["data"] or {}
            versions = forge_meta.get(mc, [])
            if isinstance(versions, list):
                slugs = [v for v in versions if isinstance(v, str)]
        except Exception as e:  # noqa: BLE001
            metadata_error = e

        # Даже если JSON ответил успешно, проверяем, что в нём действительно
        # есть артефакты именно для этой версии игры. Если нет — пробуем Maven.
        if not any(isinstance(v, str) and v.startswith(f"{mc}-") for v in slugs):
            try:
                xml_text = http_get_text(f"{FORGE_MAVEN}/maven-metadata.xml")
                root = ET.fromstring(xml_text)
                all_versions = [
                    node.text.strip()
                    for node in root.findall(".//version")
                    if node.text and node.text.strip()
                ]
                prefix = f"{mc}-"
                slugs = [v for v in all_versions if v.startswith(prefix)]
            except Exception as e:  # noqa: BLE001
                detail = f" JSON: {metadata_error}; Maven: {e}" if metadata_error else f" Maven: {e}"
                raise LauncherError(
                    t("forge_list_fail", mc=mc, detail=detail)
                ) from e

        # В некоторых версиях metadata могут встречаться дубликаты.
        slugs = list(dict.fromkeys(slugs))
        slugs.sort(key=numeric_key, reverse=True)

        try:
            promos = http_get_json(FORGE_PROMOS_URL).get("promos", {})
        except Exception:  # noqa: BLE001
            promos = {}
        recommended, latest = promos.get(f"{mc}-recommended"), promos.get(f"{mc}-latest")
        items = []
        for slug in slugs:
            # Maven хранит полное имя артефакта, например
            # 1.12.2-14.23.5.2859 или 1.7.10-10.13.4.1614-1.7.10.
            if not slug.startswith(mc + "-"):
                continue
            fv = slug[len(mc) + 1:]
            tags = []
            if fv == recommended:
                tags.append(t("tag_recommended"))
            if fv == latest:
                tags.append(t("tag_latest"))
            label = fv + (f" ({', '.join(tags)})" if tags else "")
            items.append({
                "version": fv,
                "slug": slug,
                "label": label,
                "stable": fv == recommended,
            })
        items = mark_default(items)
        LOADER_VERSIONS_CACHE[cache_key] = items
        return items

    if kind == "neoforge":
        if mc_ver is None or mc_ver < (1, 20, 1):
            raise LauncherError(t("neoforge_old"))
        if mc == "1.20.1":  # самые первые сборки NeoForge выходили под другим именем
            text = http_get_text(f"{NEOFORGE_MAVEN}/forge/maven-metadata.xml")
            prefix = "1.20.1-"
        else:
            text = http_get_text(f"{NEOFORGE_MAVEN}/neoforge/maven-metadata.xml")
            # NeoForge 21.1.x = Minecraft 1.21.1, 20.4.x = 1.20.4 и т.д.
            prefix = f"{mc_ver[1]}.{mc_ver[2]}." if mc_ver[0] == 1 else mc + "."
        versions = [v for v in re.findall(r"<version>([^<]+)</version>", text) if v.startswith(prefix)]
        versions.sort(key=numeric_key, reverse=True)
        items = []
        for v in versions:
            stable = not re.search(r"alpha|beta|rc|snapshot", v, re.I)
            items.append({"version": v, "stable": stable,
                          "label": v if stable else t("loader_beta", v=v)})
        items = mark_default(items)
        LOADER_VERSIONS_CACHE[cache_key] = items
        return items

    return []


def installed_loader_versions(kind, mc):
    """Уже установленные версии загрузчика (на случай, когда нет интернета)."""
    items = []
    vdir = GAME_DIR / "versions"
    if not vdir.is_dir():
        return items
    for d in sorted(vdir.iterdir(), reverse=True):
        f = d / f"{d.name}.json"
        if not f.exists():
            continue
        try:
            data = read_json(f)
        except (OSError, ValueError):
            continue
        if data.get("inheritsFrom") != mc:
            continue
        name, lv = d.name, None
        if kind == "fabric" and name.startswith("fabric-loader-"):
            lv = name[len("fabric-loader-"):-(len(mc) + 1)]
        elif kind == "quilt" and name.startswith("quilt-loader-"):
            lv = name[len("quilt-loader-"):-(len(mc) + 1)]
        elif kind == "forge" and "-forge-" in name:
            lv = name.split("-forge-", 1)[1]
        elif kind == "neoforge" and name.startswith("neoforge-"):
            lv = name[len("neoforge-"):]
        if lv:
            items.append({"version": lv, "stable": True, "label": t("loader_installed", v=lv)})
    return mark_default(items)


def installer_version_id(jar):
    """Читает из установщика Forge/NeoForge, под каким именем появится версия."""
    with zipfile.ZipFile(jar) as z:
        with z.open("install_profile.json") as f:
            profile = json.load(f)
    if isinstance(profile.get("version"), str):
        return profile["version"]
    return profile["versionInfo"]["id"]


def ensure_loader(kind, mc, loader, vanilla, log, progress, status):
    """Ставит загрузчик (если ещё не установлен). Возвращает id версии для запуска."""
    lv = loader["version"]

    # --- Fabric / Quilt: достаточно скачать готовое описание версии -----------
    if kind in ("fabric", "quilt"):
        prefix = "fabric-loader" if kind == "fabric" else "quilt-loader"
        vid = f"{prefix}-{lv}-{mc}"
        path = GAME_DIR / "versions" / vid / f"{vid}.json"
        if path.exists():
            return vid
        status(t("installing_loader", kind=kind.capitalize()))
        base = FABRIC_META if kind == "fabric" else QUILT_META
        profile = http_get_json(
            f"{base}/versions/loader/{urllib.parse.quote(mc)}/{urllib.parse.quote(lv)}/profile/json")
        vid = profile["id"]
        path = GAME_DIR / "versions" / vid / f"{vid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), "utf-8")
        return vid

    # --- Forge / NeoForge: скачиваем установщик и запускаем его без окна ------
    if kind == "forge":
        title = "Forge"
        slug = loader.get("slug") or f"{mc}-{lv}"
        url = f"{FORGE_MAVEN}/{slug}/forge-{slug}-installer.jar"
        installer = GAME_DIR / "installers" / f"forge-{slug}-installer.jar"
    else:
        title = "NeoForge"
        artifact = "forge" if lv.startswith(mc + "-") else "neoforge"
        url = f"{NEOFORGE_MAVEN}/{artifact}/{lv}/{artifact}-{lv}-installer.jar"
        installer = GAME_DIR / "installers" / f"{artifact}-{lv}-installer.jar"

    status(t("downloading_installer", title=title))
    download(url, installer)
    try:
        vid = installer_version_id(installer)
    except (zipfile.BadZipFile, KeyError, ValueError):
        installer.unlink(missing_ok=True)
        raise LauncherError(t("installer_corrupt", title=title))

    version_dir = GAME_DIR / "versions" / vid
    marker = version_dir / ".loader_ok"
    if marker.exists() and (version_dir / f"{vid}.json").exists():
        return vid

    # Установщику нужны файлы чистой игры и Java
    prepare_game(vanilla, log, progress, status)
    java = get_java(vanilla, log, progress, status)

    profiles = GAME_DIR / "launcher_profiles.json"  # без этого файла установщик не стартует
    if not profiles.exists():
        profiles.write_text(json.dumps({"profiles": {}, "settings": {}, "version": 3}), "utf-8")

    status(t("installing_title_long", title=title))
    log(t("running_installer", title=title))
    progress(0, 0)  # бегущая полоска: время установки заранее неизвестно
    code = stream_process([str(java), "-jar", str(installer), "--installClient", str(GAME_DIR)],
                          GAME_DIR, log)
    progress(0, 1)
    if code != 0 or not (version_dir / f"{vid}.json").exists():
        raise LauncherError(
            t("installer_exit_error", title=title, code=code))
    marker.write_text("ok", "utf-8")
    return vid


def instance_dir(kind, mc):
    """Папка игры (сохранения, моды, настройки). У каждого загрузчика своя,
    чтобы моды Fabric и Forge не смешивались. Чистая игра — общая папка."""
    if kind is None:
        return GAME_DIR
    return GAME_DIR / "instances" / f"{kind}-{mc}"


# ----------------------------------------------------------------------------
# Ely.by: вход по логину и паролю (authserver.ely.by) и authlib-injector
# ----------------------------------------------------------------------------
# Так же, как в Legacy Launcher: игрок вводит ник/e-mail и пароль от Ely.by прямо в
# лаунчере (при включённой двухфакторке — ещё и код). Пароль нигде не сохраняется:
# хранятся только accessToken и clientToken, которыми лаунчер потом сам продлевает сессию.

ELY_AUTH_SERVER = "https://authserver.ely.by"
AUTHLIB_RELEASES_URL = "https://api.github.com/repos/yushijinhun/authlib-injector/releases/latest"


class ElyAuthError(LauncherError):
    """Ошибка входа в Ely.by. needs_2fa — нужен код двухфакторной защиты;
    relogin — сохранённая сессия не подошла, нужно ввести пароль заново."""

    def __init__(self, message, needs_2fa=False, relogin=False):
        super().__init__(message)
        self.needs_2fa = needs_2fa
        self.relogin = relogin


ELY_ERROR_TEXTS = {
    "invalid credentials": t("ely_err_credentials"),
    "account protected with two factor auth": t("ely_err_2fa"),
    "this account has been suspended": t("ely_err_suspended"),
    "token expired": t("ely_err_token_expired"),
    "invalid token": t("ely_err_token_invalid"),
}


def _ely_error_text(message):
    low = (message or "").lower()
    for key, text in ELY_ERROR_TEXTS.items():
        if key in low:
            return text
    return message or t("ely_err_unknown")


def ely_post(path, payload):
    """POST JSON на authserver.ely.by. Успех — dict (или {} для пустого ответа)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ELY_AUTH_SERVER + path, data=data, headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8", "replace").strip()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            info = json.loads(body)
            message = info.get("errorMessage") or info.get("error") or body
        except ValueError:
            message = body or f"HTTP {exc.code}"
        low = message.lower()
        raise ElyAuthError(_ely_error_text(message),
                           needs_2fa="two factor" in low) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise LauncherError(t("ely_contact_fail", error=exc)) from exc


def ely_account_from_response(result, client_token):
    profile = result.get("selectedProfile")
    if not profile:
        profiles = result.get("availableProfiles") or []
        profile = profiles[0] if profiles else None
    if not profile or not profile.get("name") or not profile.get("id"):
        raise ElyAuthError(t("ely_err_no_profile"))
    if not result.get("accessToken"):
        raise ElyAuthError(t("ely_err_no_token"))
    return {
        "username": profile["name"],
        "uuid": profile["id"],
        "access_token": result["accessToken"],
        "client_token": result.get("clientToken") or client_token,
    }


def ely_authenticate(login, password, totp="", client_token=None):
    """POST /auth/authenticate. Код 2FA дописывается к паролю как «пароль:код»."""
    client_token = client_token or uuid.uuid4().hex
    result = ely_post("/auth/authenticate", {
        "username": login,
        "password": f"{password}:{totp}" if totp else password,
        "clientToken": client_token,
        "requestUser": True,
    })
    return ely_account_from_response(result, client_token)


def ely_ensure_session(account):
    """Проверяет сохранённый токен перед запуском и при необходимости продлевает его.
    Возвращает (account, изменился_ли_токен)."""
    token = account.get("access_token")
    client_token = account.get("client_token")
    if not token or not client_token:
        raise ElyAuthError(t("ely_err_legacy"), relogin=True)
    try:
        ely_post("/auth/validate", {"accessToken": token})
        return account, False
    except ElyAuthError:
        pass  # токен не принят — пробуем продлить
    try:
        result = ely_post("/auth/refresh", {"accessToken": token, "clientToken": client_token})
    except ElyAuthError as exc:
        raise ElyAuthError(t("ely_err_session_end"), relogin=True) from exc
    fresh = ely_account_from_response(result, client_token)
    updated = dict(account)
    updated.update(access_token=fresh["access_token"], client_token=fresh["client_token"],
                   uuid=fresh["uuid"], username=fresh["username"])
    return updated, True


def ely_invalidate(account):
    """Гасит токен на стороне Ely.by (при удалении аккаунта). Ошибки игнорируем."""
    try:
        ely_post("/auth/invalidate", {"accessToken": account.get("access_token", ""),
                                      "clientToken": account.get("client_token", "")})
    except Exception:  # noqa: BLE001
        pass


def ensure_authlib_injector(log, status):
    target = GAME_DIR / "libraries" / "authlib-injector.jar"
    if target.is_file() and target.stat().st_size > 100_000:
        return target
    status(t("authlib_download"))
    log(t("authlib_fetch"))
    release = http_get_json(AUTHLIB_RELEASES_URL)
    assets = release.get("assets", [])
    asset = next((a for a in assets if a.get("name", "").lower().endswith(".jar") and "sources" not in a.get("name", "").lower()), None)
    if not asset or not asset.get("browser_download_url"):
        raise LauncherError(t("authlib_jar_missing"))
    download(asset["browser_download_url"], target, size=asset.get("size"), verify_existing=False)
    if not target.exists() or target.stat().st_size < 100_000:
        raise LauncherError(t("authlib_corrupt"))
    return target


# ----------------------------------------------------------------------------
# Профили игрока и установки (instances)
# ----------------------------------------------------------------------------

# Страница профиля Ely.by, на которую ведёт кнопка у аккаунта Ely.by.
# Если хотите вести на публичную страницу вида https://ely.by/u<ID>, поменяйте адрес здесь.
ELY_PROFILE_URL = "https://account.ely.by/"

NICK_RE = re.compile(r"[A-Za-z0-9_]{3,16}")
def no_loader_label():
    """Localized 'no loader' combobox label."""
    return t("no_loader")


LOADER_LABELS = {"fabric": "Fabric", "quilt": "Quilt", "forge": "Forge", "neoforge": "NeoForge"}
LABEL_TO_KIND = {label: kind for kind, label in LOADER_LABELS.items()}
LOADER_ORDER = ["Forge", "Fabric", "NeoForge", "Quilt"]


# --- профили -----------------------------------------------------------------

def profile_key(ptype, name):
    return f"{ptype}:{name}"


def profile_list(cfg):
    """Все профили: сначала локальные, потом аккаунты Ely.by."""
    items = [{"key": profile_key("local", n), "type": "local", "name": n}
             for n in cfg.get("player_profiles", [])]
    items += [{"key": profile_key("ely", n), "type": "ely", "name": n}
              for n in cfg.get("ely_accounts", {})]
    return items


def active_profile(cfg):
    for p in profile_list(cfg):
        if p["key"] == cfg.get("active_profile"):
            return p
    return None


def profile_label(p):
    """Подпись профиля с указанием типа (в списке и на кнопке выбора)."""
    return f"{p['name']}  ·  " + (t("profile_summary_ely") if p["type"] == "ely" else t("profile_summary_local"))


def migrate_profiles(cfg):
    """Приводит конфиг к модели: локальные профили (player_profiles) и
    аккаунты Ely.by (ely_accounts) раздельно. Поддерживает старый формат,
    где ники аккаунтов Ely.by лежали в общем списке профилей."""
    ely = cfg.get("ely_accounts")
    if not isinstance(ely, dict):
        ely = {}
    cfg["ely_accounts"] = ely

    raw = cfg.get("player_profiles")
    if not isinstance(raw, list):
        raw = [cfg.get("nickname", "Player")]
    local = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if name and name not in local and name not in ely:
                local.append(name)
    if not local and not ely:
        local = ["Player"]
    cfg["player_profiles"] = local

    if not isinstance(cfg.get("profile_skins"), dict):
        cfg["profile_skins"] = {}

    keys = [p["key"] for p in profile_list(cfg)]
    active = cfg.get("active_profile")
    if active not in keys:
        old = cfg.get("active_player_profile") or cfg.get("nickname")
        if cfg.get("account_type") == "ely.by" and old in ely:
            active = profile_key("ely", old)
        elif old in local:
            active = profile_key("local", old)
        else:
            active = keys[0]
    cfg["active_profile"] = active
    # Старые ключи оставляем согласованными (имя активного профиля)
    name = active.partition(":")[2]
    cfg["nickname"] = cfg["active_player_profile"] = name
    cfg["account_type"] = "ely.by" if active.startswith("ely:") else "local"
    return cfg


def remove_profile(cfg, key):
    """Удаляет профиль или аккаунт. Возвращает путь к файлу скина, если он был."""
    ptype, _, name = key.partition(":")
    skin = None
    if ptype == "local":
        if name in cfg.get("player_profiles", []):
            cfg["player_profiles"].remove(name)
        skin = cfg.get("profile_skins", {}).pop(name, None)
    else:
        cfg.get("ely_accounts", {}).pop(name, None)
    migrate_profiles(cfg)  # выберет другой профиль; если не осталось — создаст Player
    return skin


def png_size(path):
    with open(path, "rb") as f:
        header = f.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(t("png_invalid"))
    return struct.unpack(">II", header[16:24])


def skin_path_for(name):
    return SKINS_DIR / (re.sub(r"[^A-Za-z0-9_-]", "_", name) + ".png")


def extract_skin_head(skin_file, size=32, grayscale=False):
    """Вырезать лицо из скина Minecraft (как в Prism): head + hat overlay.

    Стандартный скин 64×64 (или 64×32): лицо (8,8)–(16,16), шляпа (40,8)–(48,16).
    Возвращает PIL.Image RGBA или None.
    """
    try:
        from PIL import Image as PILImage
        from PIL import ImageOps
    except ImportError:
        return None
    try:
        path = Path(skin_file)
        if not path.is_file():
            return None
        skin = PILImage.open(path).convert("RGBA")
    except Exception:
        return None
    w, h = skin.size
    if w < 64 or h < 32:
        return None
    face = skin.crop((8, 8, 16, 16))
    if w >= 64 and h >= 32:
        hat = skin.crop((40, 8, 48, 16))
        face = face.copy()
        face.alpha_composite(hat)
    if grayscale:
        # ч/б, сохраняем альфу
        alpha = face.getchannel("A")
        gray = ImageOps.grayscale(face.convert("RGB")).convert("RGBA")
        gray.putalpha(alpha)
        face = gray
    return face.resize((size, size), PILImage.Resampling.NEAREST)


def ely_skin_cache_path(username):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", username or "player")
    return SKINS_DIR / "ely_cache" / f"{safe}.png"


def download_ely_skin(username, force=False):
    """Скачать скин с skinsystem.ely.by в локальный кэш. None если нет."""
    if not username:
        return None
    dest = ely_skin_cache_path(username)
    if dest.is_file() and not force and dest.stat().st_size > 100:
        return dest
    url = f"https://skinsystem.ely.by/skins/{urllib.parse.quote(username)}.png"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = resp.read()
        if not data or len(data) < 100:
            return None
        # простая проверка PNG
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        dest.write_bytes(data)
        return dest
    except Exception:
        return None


def resolve_profile_skin_file(cfg, profile):
    """Путь к PNG-скину для иконки головы.

    Локальный профиль → None (рисуем ч/б Steve).
    Ely.by → кэш skinsystem.ely.by или уже скачанный файл.
    """
    if not profile:
        return None
    if profile.get("type") != "ely":
        return None
    name = profile.get("name") or ""
    cached = ely_skin_cache_path(name)
    if cached.is_file() and cached.stat().st_size > 100:
        return cached
    # синхронно пробуем скачать (быстрый кэш после первого раза)
    return download_ely_skin(name)


# --- установки (instances) ---------------------------------------------------

def new_instance_id():
    return uuid.uuid4().hex[:8]


def safe_dir_name(name):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "instance"
    if s.upper() in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(COM|LPT)\d", s.upper()):
        s = "_" + s
    return s[:40]


def unique_instance_dir(cfg, name):
    """Папка для новой установки внутри minecraft/instances (относительный путь)."""
    base = safe_dir_name(name)
    used = {str(i.get("dir", "")).lower() for i in cfg.get("instances", [])}
    candidate, n = f"instances/{base}", 2
    while candidate.lower() in used or (GAME_DIR / candidate).exists():
        candidate = f"instances/{base}-{n}"
        n += 1
    return candidate


def instance_path(inst):
    """Папка игры установки (сохранения, моды, настройки). Не выходит за пределы GAME_DIR."""
    path = (GAME_DIR / (inst.get("dir") or ".")).resolve()
    root = GAME_DIR.resolve()
    if path != root and root not in path.parents:
        raise LauncherError(t("bad_instance_dir"))
    return path


def instance_summary(inst):
    text = inst["mc"]
    kind = inst.get("kind")
    if kind:
        text += " · " + LOADER_LABELS.get(kind, kind)
        if inst.get("loader_version"):
            text += " " + inst["loader_version"]
    return text


def instance_label(inst):
    return f"{inst['name']}  —  {instance_summary(inst)}"


def current_instance(cfg):
    for inst in cfg.get("instances", []):
        if inst.get("id") == cfg.get("active_instance"):
            return inst
    return None


def _parse_mc_version_guess(text):
    """Из строки вытащить правдоподобную версию Minecraft (1.x.y / 1.x)."""
    if not text or not isinstance(text, str):
        return ""
    m = re.search(r"\b(1\.\d+(?:\.\d+)?(?:-pre\d+|-rc\d+)?)\b", text)
    return m.group(1) if m else ""


def detect_instance_metadata(source_dir):
    """Попытаться определить название, MC-версию и загрузчик по папке сборки.

    Поддерживаются типичные форматы: MultiMC/Prism, CurseForge, Modrinth index,
    а также эвристики по mods/ и version.json.
    Возвращает dict: name, mc, kind, loader_version (поля могут быть пустыми).
    """
    source = Path(source_dir)
    result = {"name": "", "mc": "", "kind": None, "loader_version": ""}

    # MultiMC / Prism Launcher
    cfg_path = source / "instance.cfg"
    if cfg_path.is_file():
        try:
            text = cfg_path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if key in ("name", "Name") and val and not result["name"]:
                    result["name"] = val
                if key in ("IntendedVersion", "MinecraftVersion") and val:
                    result["mc"] = val
        except OSError:
            pass

    mmc = source / "mmc-pack.json"
    if mmc.is_file():
        try:
            data = json.loads(mmc.read_text(encoding="utf-8"))
            components = data.get("components") or []
            for comp in components:
                uid = str(comp.get("uid") or "")
                ver = str(comp.get("version") or "").strip()
                if uid == "net.minecraft" and ver:
                    result["mc"] = ver
                elif uid in ("net.fabricmc.fabric-loader", "net.fabricmc.fabric-loader-intermediary"):
                    result["kind"] = "fabric"
                    if ver:
                        result["loader_version"] = ver
                elif uid == "org.quiltmc.quilt-loader":
                    result["kind"] = "quilt"
                    if ver:
                        result["loader_version"] = ver
                elif uid in ("net.minecraftforge", "net.minecraftforge.forge"):
                    result["kind"] = "forge"
                    if ver:
                        result["loader_version"] = ver.split("-")[-1] if "-" in ver else ver
                elif uid in ("net.neoforged", "net.neoforged.neoforge"):
                    result["kind"] = "neoforge"
                    if ver:
                        result["loader_version"] = ver
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    # CurseForge / Overwolf minecraftinstance.json
    for name in ("minecraftinstance.json", "minecraftinstance.json.bak"):
        cf = source / name
        if not cf.is_file():
            continue
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
            if not result["name"]:
                result["name"] = str(data.get("name") or data.get("baseModLoader", {}).get("name") or "")
            base = data.get("baseModLoader") or {}
            mc = str(base.get("minecraftVersion") or data.get("gameVersion") or "")
            if mc:
                result["mc"] = mc
            forge = str(base.get("forgeVersion") or base.get("name") or "")
            if "fabric" in forge.lower() or "fabric" in str(base.get("name", "")).lower():
                result["kind"] = "fabric"
                result["loader_version"] = str(base.get("forgeVersion") or "")
            elif "quilt" in forge.lower():
                result["kind"] = "quilt"
            elif "neoforge" in forge.lower() or "neo" in forge.lower():
                result["kind"] = "neoforge"
                result["loader_version"] = str(base.get("forgeVersion") or "")
            elif forge:
                result["kind"] = "forge"
                result["loader_version"] = str(base.get("forgeVersion") or "")
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        break

    # Modrinth index (внутри .mrpack или распакованной папки)
    for idx_name in ("modrinth.index.json", "modrinth.index"):
        idx = source / idx_name
        if not idx.is_file():
            # иногда лежит в корне архива рядом с overrides/
            continue
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            deps = data.get("dependencies") or {}
            if deps.get("minecraft"):
                result["mc"] = str(deps["minecraft"])
            if deps.get("fabric-loader"):
                result["kind"] = "fabric"
                result["loader_version"] = str(deps["fabric-loader"])
            elif deps.get("quilt-loader"):
                result["kind"] = "quilt"
                result["loader_version"] = str(deps["quilt-loader"])
            elif deps.get("forge"):
                result["kind"] = "forge"
                result["loader_version"] = str(deps["forge"])
            elif deps.get("neoforge"):
                result["kind"] = "neoforge"
                result["loader_version"] = str(deps["neoforge"])
            if not result["name"]:
                result["name"] = str(data.get("name") or "")
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        break

    # Эвристика по mods/*.jar (fabric.mod.json / mods.toml)
    mods_dir = source / "mods"
    if not mods_dir.is_dir():
        # MultiMC кладёт игру в minecraft/
        mods_dir = source / "minecraft" / "mods"
    if mods_dir.is_dir() and (not result["kind"] or not result["mc"]):
        try:
            jars = list(mods_dir.glob("*.jar"))[:40]
        except OSError:
            jars = []
        for jar in jars:
            try:
                with zipfile.ZipFile(jar) as z:
                    names = set(z.namelist())
                    if "fabric.mod.json" in names and not result["kind"]:
                        result["kind"] = "fabric"
                        try:
                            meta = json.loads(z.read("fabric.mod.json").decode("utf-8", errors="ignore"))
                            depends = meta.get("depends") or {}
                            mc_dep = depends.get("minecraft") or ""
                            if isinstance(mc_dep, list):
                                mc_dep = mc_dep[0] if mc_dep else ""
                            guess = _parse_mc_version_guess(str(mc_dep))
                            if guess and not result["mc"]:
                                result["mc"] = guess
                        except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
                            pass
                    if "quilt.mod.json" in names and not result["kind"]:
                        result["kind"] = "quilt"
                    for toml_name in ("META-INF/mods.toml", "META-INF/neoforge.mods.toml"):
                        if toml_name in names:
                            try:
                                raw = z.read(toml_name).decode("utf-8", errors="ignore")
                            except KeyError:
                                continue
                            if "neoforge" in raw.lower() and not result["kind"]:
                                result["kind"] = "neoforge"
                            elif not result["kind"]:
                                result["kind"] = "forge"
                            if not result["mc"]:
                                m = re.search(
                                    r'minecraftVersionRange\s*=\s*["\']([^"\']+)', raw)
                                if m:
                                    guess = _parse_mc_version_guess(m.group(1))
                                    if guess:
                                        result["mc"] = guess
            except (zipfile.BadZipFile, OSError):
                continue
            if result["kind"] and result["mc"]:
                break

    # version.json в корне или в minecraft/
    for vpath in (source / "version.json", source / "minecraft" / "version.json"):
        if vpath.is_file() and not result["mc"]:
            try:
                data = json.loads(vpath.read_text(encoding="utf-8"))
                vid = str(data.get("id") or data.get("inheritsFrom") or "")
                guess = _parse_mc_version_guess(vid) or vid
                if guess:
                    result["mc"] = guess
                if not result["kind"]:
                    result["kind"] = loader_kind_from_name(vid)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass

    if not result["name"]:
        result["name"] = source.name

    # Ограничим длину имени как в UI
    result["name"] = (result["name"] or t("instance_imported_name")).strip()[:40]
    return result


def install_mrpack_files(index_path, dest_dir, progress_cb=None):
    """Скачать файлы из modrinth.index.json в папку установки."""
    data = json.loads(Path(index_path).read_text(encoding="utf-8"))
    files = data.get("files") or []
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    total = max(1, len(files))
    for i, entry in enumerate(files):
        rel = str(entry.get("path") or "").replace("\\", "/").lstrip("/")
        if not rel or ".." in rel.split("/"):
            continue
        downloads = entry.get("downloads") or []
        if not downloads:
            continue
        url = downloads[0]
        hashes = entry.get("hashes") or {}
        sha1 = hashes.get("sha1")
        target = dest / rel
        try:
            download(url, target, sha1=sha1, size=entry.get("fileSize"),
                     verify_existing=True)
        except Exception:
            # пропускаем отдельные битые ссылки, остальное всё равно ставим
            pass
        if progress_cb:
            progress_cb(i + 1, total)


def copy_instance_files(src_dir, dest_dir, progress_cb=None):
    """Скопировать содержимое сборки в папку установки.

    MultiMC/Prism: копируем подпапку minecraft/, если она есть.
    .mrpack: скачиваем файлы из index + копируем overrides/.
    """
    src = Path(src_dir)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    index_file = src / "modrinth.index.json"
    if not index_file.is_file():
        index_file = src / "modrinth.index"
    is_mrpack = index_file.is_file()

    if is_mrpack:
        install_mrpack_files(index_file, dest, progress_cb=progress_cb)
        overrides = src / "overrides"
        if overrides.is_dir():
            for item in overrides.iterdir():
                if item.name.startswith("._"):
                    continue
                target = dest / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
        return

    # MultiMC: данные игры в minecraft/
    if (src / "minecraft").is_dir() and (
            (src / "instance.cfg").is_file() or (src / "mmc-pack.json").is_file()):
        src = src / "minecraft"

    skip_names = {".cache", ".fabric", ".quilt", "natives", "versions", "libraries",
                  "assets", "runtime", "logs", "crash-reports"}
    entries = [p for p in src.iterdir() if p.name not in skip_names and not p.name.startswith("._")]
    total = max(1, len(entries))
    for i, item in enumerate(entries):
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
        if progress_cb:
            progress_cb(i + 1, total)


def version_files_exist(version_id, seen=None):
    """True only when the version metadata and required client JARs are present.

    Loader profiles inherit the base Minecraft version, so check the parent
    recursively rather than requiring a duplicate JAR in the loader directory.
    """
    if not version_id or not isinstance(version_id, str):
        return False
    seen = set() if seen is None else seen
    if version_id in seen:
        return False
    seen.add(version_id)
    folder = GAME_DIR / "versions" / version_id
    json_path = folder / f"{version_id}.json"
    if not json_path.is_file():
        return False
    try:
        data = read_json(json_path)
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    parent = data.get("inheritsFrom")
    if parent and not version_files_exist(parent, seen):
        return False
    # Loader versions commonly inherit the base game's client JAR.
    jar_id = data.get("jar") or (parent if parent else version_id)
    jar_path = GAME_DIR / "versions" / jar_id / f"{jar_id}.jar"
    return jar_path.is_file()


def instance_files_exist(inst):
    """Whether this configured installation has the actual files needed to run."""
    if not isinstance(inst, dict) or not inst.get("mc"):
        return False
    kind = inst.get("kind")
    if kind:
        # Locate the matching loader profile rather than assuming its folder name;
        # names differ between Fabric, Quilt, Forge and NeoForge.
        for item in scan_installed_versions():
            if item.get("mc") == inst.get("mc") and item.get("kind") == kind:
                if version_files_exist(item.get("version_id") or item.get("id")):
                    return True
        # scan_installed_versions returns parsed loader records; resolve directly
        # from the version directory as a fallback for nonstandard profile names.
        vdir = GAME_DIR / "versions"
        if vdir.is_dir():
            for meta in vdir.glob("*/*.json"):
                try:
                    data = read_json(meta)
                except (OSError, ValueError, TypeError):
                    continue
                name = meta.parent.name
                if data.get("inheritsFrom") == inst.get("mc") and loader_kind_from_name(name) == kind:
                    if version_files_exist(name):
                        return True
        return False
    return version_files_exist(inst.get("mc"))


def loader_kind_from_name(name):
    if name.startswith("fabric-loader-"):
        return "fabric"
    if name.startswith("quilt-loader-"):
        return "quilt"
    if "-forge-" in name:
        return "forge"
    if name.startswith("neoforge-"):
        return "neoforge"
    return None


def scan_installed_versions():
    """Версии, уже скачанные в папку игры: чистые и с загрузчиком."""
    found = []
    vdir = GAME_DIR / "versions"
    if not vdir.is_dir():
        return found
    for d in sorted(vdir.iterdir(), key=lambda x: x.name, reverse=True):
        f = d / f"{d.name}.json"
        if not f.is_file():
            continue
        try:
            data = read_json(f)
        except (OSError, ValueError):
            continue
        mc = data.get("inheritsFrom")
        if not mc:
            found.append({"mc": d.name, "kind": None, "loader_version": "", "version_id": d.name})
            continue
        name = d.name
        if name.startswith("fabric-loader-"):
            kind, lv = "fabric", name[len("fabric-loader-"):-(len(mc) + 1)]
        elif name.startswith("quilt-loader-"):
            kind, lv = "quilt", name[len("quilt-loader-"):-(len(mc) + 1)]
        elif "-forge-" in name:
            kind, lv = "forge", name.split("-forge-", 1)[1]
        elif name.startswith("neoforge-"):
            kind, lv = "neoforge", name[len("neoforge-"):]
        else:
            continue
        found.append({"mc": mc, "kind": kind, "loader_version": lv, "version_id": name})
    return found


def migrate_instances(cfg):
    """Если установок ещё нет (старый конфиг) — создаёт их из уже скачанных версий.
    Папки игры при этом остаются прежними, поэтому сохранения и моды не теряются."""
    if not isinstance(cfg.get("instances"), list):
        instances = []
        for item in scan_installed_versions():
            kind, mc = item["kind"], item["mc"]
            name = mc if not kind else f"{mc} — {LOADER_LABELS[kind]} {item['loader_version']}"
            instances.append({
                "id": new_instance_id(), "name": name, "mc": mc, "kind": kind,
                "loader_version": item["loader_version"], "loader_slug": "",
                "ram_gb": int(cfg.get("ram_gb", 4) or 4),
                "dir": "." if kind is None else f"instances/{kind}-{mc}",
            })
        # Базовая vanilla-версия нужна загрузчикам как зависимость, но не должна
        # отображаться отдельной установкой, если для той же версии найден загрузчик.
        modded_mc = {i["mc"] for i in instances if i.get("kind")}
        instances = [i for i in instances if i.get("kind") or i["mc"] not in modded_mc]
        cfg["instances"] = instances
        # Активной делаем ту установку, что была выбрана раньше (версия + загрузчик)
        wanted_mc = cfg.get("version")
        wanted_kind = LABEL_TO_KIND.get(cfg.get("loader_kind"))
        match = (next((i for i in instances if i["mc"] == wanted_mc and i["kind"] == wanted_kind), None)
                 or next((i for i in instances if i["mc"] == wanted_mc), None))
        cfg["active_instance"] = (match or (instances[0] if instances else {})).get("id", "")
    # Чистим некорректные записи и следим, чтобы активная установка существовала
    cfg["instances"] = [i for i in cfg["instances"] if isinstance(i, dict) and i.get("id") and i.get("mc")]
    if not any(i["id"] == cfg.get("active_instance") for i in cfg["instances"]):
        cfg["active_instance"] = cfg["instances"][0]["id"] if cfg["instances"] else ""
    return cfg


def probe_loader_available(kind, mc):
    """Быстрая проверка: есть ли хоть одна сборка загрузчика для mc.
    Для Fabric/Quilt — лёгкий запрос списка; для Forge — только JSON meta (без Maven);
    результат кэшируется. Полный список версий подгружается при выборе загрузчика."""
    cache_key = (kind, mc)
    if cache_key in LOADER_AVAILABILITY_CACHE:
        return LOADER_AVAILABILITY_CACHE[cache_key]
    if cache_key in LOADER_VERSIONS_CACHE:
        ok = bool(LOADER_VERSIONS_CACHE[cache_key])
        LOADER_AVAILABILITY_CACHE[cache_key] = ok
        return ok

    try:
        if kind in ("fabric", "quilt"):
            # Полный список небольшой и сразу кладём в кэш версий
            items = fetch_loader_versions(kind, mc)
            ok = bool(items)
        elif kind == "forge":
            t = mc_tuple(mc)
            if t is None:
                ok = False
            else:
                if _FORGE_META_CACHE["data"] is None:
                    _FORGE_META_CACHE["data"] = http_get_json(FORGE_META_URL)
                forge_meta = _FORGE_META_CACHE["data"] or {}
                versions = forge_meta.get(mc, [])
                ok = isinstance(versions, list) and any(
                    isinstance(v, str) and (v.startswith(f"{mc}-") or v == mc)
                    for v in versions
                )
                # если в JSON пусто — не качаем Maven на этапе проверки (медленно);
                # при выборе Forge fetch_loader_versions подтянет Maven
        elif kind == "neoforge":
            t = mc_tuple(mc)
            if t is None or t < (1, 20, 1):
                ok = False
            else:
                # Достаточно одного metadata.xml (кэш http_get_text, если есть)
                if mc == "1.20.1":
                    text = http_get_text(f"{NEOFORGE_MAVEN}/forge/maven-metadata.xml")
                    prefix = "1.20.1-"
                else:
                    text = http_get_text(f"{NEOFORGE_MAVEN}/neoforge/maven-metadata.xml")
                    prefix = f"{t[1]}.{t[2]}." if t[0] == 1 else mc + "."
                ok = any(v.startswith(prefix) for v in re.findall(r"<version>([^<]+)</version>", text))
        else:
            ok = False
    except Exception as exc:  # noqa: BLE001
        is_404 = getattr(exc, "code", None) == 404
        if is_404:
            ok = False
        else:
            try:
                ok = bool(installed_loader_versions(kind, mc))
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                raise

    if not ok:
        try:
            ok = bool(installed_loader_versions(kind, mc))
        except Exception:  # noqa: BLE001
            pass
    LOADER_AVAILABILITY_CACHE[cache_key] = bool(ok)
    return bool(ok)


def check_loader_types(mc):
    """Какие загрузчики модов доступны для версии игры mc.
    Возвращает (список_названий, список_предупреждений)."""
    available, errors = [], []
    checks = [("Fabric", "fabric"), ("Quilt", "quilt"), ("Forge", "forge"), ("NeoForge", "neoforge")]
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = {pool.submit(probe_loader_available, kind, mc): (label, kind) for label, kind in checks}
        for future in as_completed(futures):
            label, kind = futures[future]
            try:
                is_available = bool(future.result())
            except Exception as exc:  # noqa: BLE001
                is_available = False
                errors.append(f"{label}: {exc}")
            if is_available:
                available.append(label)
    return [name for name in LOADER_ORDER if name in available], errors


def loader_for_instance(inst):
    """Данные загрузчика установки в виде, который понимает ensure_loader (или None)."""
    kind = inst.get("kind")
    if not kind:
        return None
    if not inst.get("loader_version"):
        raise LauncherError(t("loader_version_missing"))
    return {"kind": kind, "version": inst["loader_version"], "slug": inst.get("loader_slug") or None}


# ----------------------------------------------------------------------------
# Главная логика: скачать, установить и запустить
# ----------------------------------------------------------------------------

def install_and_launch(entry, loader, nick, ram_mb, log, progress, status, launch_game=True, selected_launch_id=None, auth_account=None, auth_client_id="", auth_client_secret="", game_dir=None):
    mc = entry["id"]
    kind = loader["kind"] if loader else None
    GAME_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Описание чистой версии игры
    status(t("version_meta_download", mc=mc))
    json_path = GAME_DIR / "versions" / mc / f"{mc}.json"
    if entry.get("url"):
        download(entry["url"], json_path, sha1=entry.get("sha1"))
    if not json_path.exists():
        raise LauncherError(t("version_meta_missing"))
    vanilla = load_version(mc)

    # 2. Загрузчик модов (если выбран)
    version_id = mc
    if loader:
        version_id = selected_launch_id or ensure_loader(kind, mc, loader, vanilla, log, progress, status)
    vjson = load_version(version_id)

    # 3. Файлы игры и Java
    prep = prepare_game(vjson, log, progress, status)
    java = get_java(vjson, log, progress, status)

    if not launch_game:
        status(t("install_done"))
        progress(0, 1)
        return

    # 4. Запуск
    game_dir = Path(game_dir) if game_dir else instance_dir(kind, mc)
    game_dir.mkdir(parents=True, exist_ok=True)
    if kind:
        (game_dir / "mods").mkdir(exist_ok=True)  # сюда кладутся моды
    authlib_path = ensure_authlib_injector(log, status) if auth_account else None
    command = build_command(vjson, prep, java, game_dir, nick, ram_mb, auth_account=auth_account, authlib_path=authlib_path)

    status(t("game_started"))
    progress(0, 1)
    log(t("game_launching", version=version_id))
    code = stream_process(command, game_dir, log)
    log(t("game_exit_code", code=code))
    if code != 0:
        raise LauncherError(t("game_exit_error", code=code))



# ----------------------------------------------------------------------------
# Дополнительный шрифт интерфейса (Google Fonts, SIL Open Font License 1.1)
# ----------------------------------------------------------------------------

def prepare_pixel_font():
    """Download Press Start 2P on first launch; use the system font if offline."""
    if os.name != "nt":
        return None
    font_dir = APP_DIR / "assets" / "fonts"
    font_file = font_dir / "PressStart2P-Regular.ttf"
    license_file = font_dir / "OFL.txt"
    font_dir.mkdir(parents=True, exist_ok=True)
    sources = {
        font_file: "https://raw.githubusercontent.com/google/fonts/main/ofl/pressstart2p/PressStart2P-Regular.ttf",
        license_file: "https://raw.githubusercontent.com/google/fonts/main/ofl/pressstart2p/OFL.txt",
    }
    for target, url in sources.items():
        if not target.exists() or target.stat().st_size < 1000:
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=4) as response:
                    data = response.read(300_000)
                if target == font_file and not data.startswith(b"\x00\x01\x00\x00"):
                    continue
                if target == license_file and b"SIL OPEN FONT LICENSE" not in data.upper():
                    continue
                target.write_bytes(data)
            except Exception:
                # Offline startup must never be blocked by a missing decorative font.
                pass
    if font_file.exists() and font_file.stat().st_size > 1000:
        try:
            import ctypes
            FR_PRIVATE = 0x10
            if ctypes.windll.gdi32.AddFontResourceExW(str(font_file), FR_PRIVATE, 0):
                ctypes.windll.user32.SendMessageW(0xFFFF, 0x001D, 0, 0)  # WM_FONTCHANGE
                return "Press Start 2P"
        except Exception:
            pass
    return None

# ----------------------------------------------------------------------------
# Окно лаунчера
# ----------------------------------------------------------------------------

# Единая палитра Minecraft: трава / земля / ночной лес
THEME = {
    "bg": "#0a1520",
    "header": "#0c1418",
    "panel": "#121c22",
    "field": "#1a282f",
    "field_hover": "#24343c",
    "border": "#2a3d46",
    "fg": "#e8f0f2",
    "muted": "#8a9aa3",
    "green": "#6fcf4a",
    "green_bright": "#8be05c",
    "green_mid": "#4a9e32",
    "green_dark": "#2d5a24",
    "green_deep": "#1e3d1a",
    "dirt": "#8b6538",
    "dirt_dark": "#5c4326",
    "log_bg": "#080e12",
    "log_fg": "#a8c4c9",
    # «Стекло» поверх фона (не настоящая альфа, но тёмные тона)
    "glass": "#0d1520",
    "glass_chip": "#1a2830",
}

# ── Единый стандарт UI-контролов ──────────────────────────────────────────
# Высота зелёной кнопки, обычной кнопки и выпадающего списка — одинаковая.
UI_CTRL_H = 46          # высота кнопок / combobox
UI_BTN_MIN = 46         # минимальный размер кнопки (ш×в)
UI_CTRL_PAD_H = 16      # горизонтальные отступы внутри контрола
UI_CTRL_PAD_V = 12      # вертикальные — дают ~46px вместе со шрифтом
SIDEBAR_WIDTH = 275     # фиксированная ширина боковой панели (пиксели Tk)

# Имена ttk-стилей (переменные, чтобы не размазывать строки по коду)
STYLE_BTN = "TButton"
STYLE_BTN_ACCENT = "Accent.TButton"
STYLE_ENTRY = "TEntry"
STYLE_COMBO = "TCombobox"

# Базовые kwargs для tk.Button (тулбар / чипы) — обычная кнопка
TK_BTN = {
    "bg": THEME["glass_chip"],
    "fg": THEME["fg"],
    "activebackground": THEME["field_hover"],
    "activeforeground": THEME["green_bright"],
    "disabledforeground": "#5a6a5c",
    "relief": "flat",
    "bd": 0,
    "highlightthickness": 0,
    "cursor": "hand2",
    "compound": "center",
    "anchor": "center",
    "justify": "center",
    "padx": 0,
    "pady": 0,
}

# Зелёная (акцентная) tk.Button — «Играть» и подобные
TK_BTN_ACCENT = {
    **TK_BTN,
    "bg": THEME["green_mid"],
    "fg": "#ffffff",
    "activebackground": THEME["green"],
    "activeforeground": "#ffffff",
    "disabledforeground": "#7a9a70",
}

# Выпадающее меню (кнопка-меню профиля / установки)
TK_BTN_MENU = {
    **TK_BTN,
    "compound": "left",
    "padx": 12,
    "pady": 0,
}


def ui_button(parent, text="", command=None, accent=False, **kw):
    """Обычная или зелёная ttk-кнопка. Минимальный размер UI_BTN_MIN×UI_BTN_MIN,
    высота ряда контролов — UI_CTRL_H."""
    style = STYLE_BTN_ACCENT if accent else STYLE_BTN
    opts = {"style": style}
    if text != "":
        opts["text"] = text
    if command is not None:
        opts["command"] = command
    opts.update(kw)
    return ttk.Button(parent, **opts)


def ui_entry(parent, **kw):
    """Поле ввода (ttk.Entry) единого стиля."""
    opts = {"style": STYLE_ENTRY}
    opts.update(kw)
    return ttk.Entry(parent, **opts)


def ui_combobox(parent, **kw):
    """Выпадающий список (ttk.Combobox) единой высоты с кнопками."""
    opts = {"style": STYLE_COMBO}
    opts.update(kw)
    return ttk.Combobox(parent, **opts)


def ui_tk_button(parent, accent=False, menu=False, **kw):
    """tk.Button по шаблону TK_BTN / TK_BTN_ACCENT / TK_BTN_MENU."""
    base = dict(TK_BTN_ACCENT if accent else (TK_BTN_MENU if menu else TK_BTN))
    base.update(kw)
    return tk.Button(parent, **base)


def ui_fixed_button(parent, side="left", pack_padx=0, accent=False, menu=False, height=None, **btn_kw):
    """Кнопка фиксированной высоты UI_CTRL_H; ширина не меньше UI_BTN_MIN.

    Подбор ширины выполняется отложенно и без update_idletasks внутри Configure,
    чтобы не запускать вложенный цикл геометрических событий Tk.
    """
    h = height if height is not None else UI_CTRL_H
    base = dict(TK_BTN_ACCENT if accent else (TK_BTN_MENU if menu else TK_BTN))
    base.update(btn_kw)
    base.setdefault("anchor", "center")
    base.setdefault("justify", "center")
    base["pady"] = 0
    wrap = tk.Frame(parent, bg=base.get("bg", THEME["glass"]), height=h)
    wrap.pack(side=side, padx=pack_padx)
    wrap.pack_propagate(False)
    btn = tk.Button(wrap, **base)
    btn.pack(fill="both", expand=True)

    fit_job = {"id": None}
    def _fit(_e=None):
        if fit_job["id"] is not None:
            return
        def apply_fit():
            fit_job["id"] = None
            try:
                if not wrap.winfo_exists() or not btn.winfo_exists():
                    return
                w = max(btn.winfo_reqwidth(), UI_BTN_MIN)
                if wrap.winfo_width() != w or wrap.winfo_height() != h:
                    wrap.configure(width=w, height=h)
            except tk.TclError:
                pass
        try:
            fit_job["id"] = parent.after_idle(apply_fit)
        except tk.TclError:
            pass

    btn.bind("<Configure>", _fit, add="+")
    _fit()
    return btn


class NavButton:
    """Пункт бокового меню на Canvas: иконка и подпись ставятся в явные координаты
    на одной горизонтальной оси (середина строки), без автокомпоновки Tk-кнопки.
    Поддерживает .configure(bg=, fg=, text=) и .pack(), как обычная кнопка."""

    def __init__(self, parent, text, image, font, fg, bg, hover_bg, command,
                 height=60, pad_left=12, gap=10, width=None):
        self._bg = bg
        self._hover_bg = hover_bg
        self._command = command
        kw = dict(height=height, bg=bg, bd=0, highlightthickness=0, cursor="hand2")
        if width is not None:
            kw["width"] = width
        self.canvas = tk.Canvas(parent, **kw)
        cy = height // 2
        iw = 0
        self._img_id = None
        if image is not None:
            iw = image.width()
            ih = image.height()
            # низ иконки = высота кнопки/2 + высота иконки/2 → иконка по центру
            self._img_id = self.canvas.create_image(
                pad_left, cy + ih // 2, image=image, anchor="sw")
            self.canvas.image = image
        self._txt_id = self.canvas.create_text(
            pad_left + iw + gap, cy, text=text, anchor="w", fill=fg, font=font)
        self.canvas.bind("<Button-1>", lambda e: self._command() if self._command else None)
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)

    def pack(self, **kw):
        self.canvas.pack(**kw)

    def _on_enter(self, _e=None):
        if self._bg != THEME["green_deep"]:
            self.canvas.configure(bg=self._hover_bg)

    def _on_leave(self, _e=None):
        self.canvas.configure(bg=self._bg)

    def configure(self, **kw):
        if "bg" in kw:
            self._bg = kw["bg"]
            self.canvas.configure(bg=self._bg)
        if "fg" in kw:
            self.canvas.itemconfigure(self._txt_id, fill=kw["fg"])
        if "text" in kw:
            self.canvas.itemconfigure(self._txt_id, text=str(kw["text"]).strip())

    config = configure


def format_count(n):
    """1234567 → '1,2 млн' / '12 тыс.' / '42'."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return t("downloads_millions", n=f"{n / 1_000_000:.1f}".replace(".", ","))
    if n >= 1_000:
        return t("downloads_thousands", n=f"{n / 1_000:.1f}".replace(".", ","))
    return f"{n:,}".replace(",", " ")


def strip_md_inline(text):
    """Убирает простую разметку для однострочных подписей."""
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_~`]{1,3}", "", text)
    return text.strip()


def render_markdown(widget, md, base_font_family="Segoe UI"):
    """Рендерит GitHub-подобный Markdown в tk.Text с тегами (заголовки, списки, код, ссылки)."""
    widget.configure(state="normal")
    widget.delete("1.0", "end")

    fam = base_font_family
    widget.tag_configure("h1", font=(fam, 16, "bold"), foreground=THEME["green_bright"], spacing1=12, spacing3=6)
    widget.tag_configure("h2", font=(fam, 13, "bold"), foreground=THEME["green"], spacing1=10, spacing3=4)
    widget.tag_configure("h3", font=(fam, 11, "bold"), foreground=THEME["fg"], spacing1=8, spacing3=3)
    widget.tag_configure("body", font=(fam, 10), foreground=THEME["log_fg"], spacing1=1, spacing3=1)
    widget.tag_configure("bold", font=(fam, 10, "bold"), foreground=THEME["fg"])
    widget.tag_configure("italic", font=(fam, 10, "italic"), foreground=THEME["muted"])
    widget.tag_configure("code", font=("Consolas", 9), foreground="#c8e6a0", background="#0e1610")
    widget.tag_configure("codeblock", font=("Consolas", 9), foreground="#c8e6a0", background="#0a100c",
                         lmargin1=12, lmargin2=12, spacing1=4, spacing3=4)
    widget.tag_configure("quote", font=(fam, 10, "italic"), foreground=THEME["muted"],
                         lmargin1=16, lmargin2=16, background="#121a14")
    widget.tag_configure("bullet", font=(fam, 10), foreground=THEME["log_fg"], lmargin1=8, lmargin2=22)
    widget.tag_configure("hr", foreground=THEME["border"])
    widget.tag_configure("link", font=(fam, 10, "underline"), foreground=THEME["green_bright"])
    widget.tag_configure("meta", font=(fam, 9), foreground=THEME["muted"])
    widget.tag_configure("title", font=(fam, 15, "bold"), foreground=THEME["fg"], spacing3=4)
    widget.tag_configure("desc", font=(fam, 10), foreground=THEME["muted"], spacing3=8)

    if not md or not str(md).strip():
        widget.insert("end", "Описание отсутствует.", "body")
        widget.configure(state="disabled")
        return

    lines = str(md).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    in_code = False
    code_buf = []

    def flush_code():
        nonlocal code_buf
        if code_buf:
            widget.insert("end", "\n".join(code_buf) + "\n", "codeblock")
            code_buf = []

    def insert_inline(text, base_tag="body"):
        """Разбирает **bold**, *italic*, `code`, [text](url) внутри строки."""
        pattern = re.compile(
            r"(\*\*\*(.+?)\*\*\*|\*\*(.+?)\*\*|\*(.+?)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))"
        )
        pos = 0
        for m in pattern.finditer(text):
            if m.start() > pos:
                widget.insert("end", text[pos:m.start()], base_tag)
            if m.group(2):  # ***bold italic***
                widget.insert("end", m.group(2), "bold")
            elif m.group(3):  # **bold**
                widget.insert("end", m.group(3), "bold")
            elif m.group(4):  # *italic*
                widget.insert("end", m.group(4), "italic")
            elif m.group(5):  # `code`
                widget.insert("end", m.group(5), "code")
            elif m.group(6) is not None:  # [text](url)
                widget.insert("end", m.group(6), "link")
            pos = m.end()
        if pos < len(text):
            widget.insert("end", text[pos:], base_tag)

    while i < len(lines):
        line = lines[i]
        # fenced code block
        if line.strip().startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        stripped = line.strip()
        if not stripped:
            widget.insert("end", "\n")
            i += 1
            continue

        if stripped in ("---", "***", "___") or re.fullmatch(r"-{3,}", stripped):
            widget.insert("end", "─" * 40 + "\n", "hr")
            i += 1
            continue

        if stripped.startswith("# "):
            insert_inline(stripped[2:], "h1")
            widget.insert("end", "\n")
        elif stripped.startswith("## "):
            insert_inline(stripped[3:], "h2")
            widget.insert("end", "\n")
        elif stripped.startswith("### "):
            insert_inline(stripped[4:], "h3")
            widget.insert("end", "\n")
        elif stripped.startswith("> "):
            insert_inline(stripped[2:], "quote")
            widget.insert("end", "\n")
        elif re.match(r"^[-*+]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            text = re.sub(r"^([-*+]|\d+\.)\s+", "•  ", stripped)
            insert_inline(text, "bullet")
            widget.insert("end", "\n")
        else:
            # убрать HTML-теги (Modrinth иногда их допускает)
            clean = re.sub(r"<br\s*/?>", "\n", stripped, flags=re.I)
            clean = re.sub(r"<[^>]+>", "", clean)
            insert_inline(clean, "body")
            widget.insert("end", "\n")
        i += 1

    flush_code()
    widget.configure(state="disabled")
    widget.see("1.0")


def apply_windows_titlebar_theme(win):
    """Шапка окна Windows (DWM) = цвет фона лаунчера + зелёная обводка."""
    if os.name != "nt":
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        if not hwnd:
            hwnd = win.winfo_id()
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        dark = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
            try:
                dwm(hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark))
            except Exception:
                pass

        def colorref(rgb):
            value = rgb.lstrip("#")
            r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
            return ctypes.c_int(r | (g << 8) | (b << 16))

        # caption = фон окна, чтобы шапка не была светлой
        caption = colorref(THEME["bg"])
        text_color = colorref(THEME["fg"])
        border = colorref(THEME["green_dark"])
        for attr, value in ((35, caption), (36, text_color), (34, border)):
            try:
                dwm(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
            except Exception:
                pass
    except Exception:
        pass


def theme_toplevel(win):
    """Единый вид дочерних окон: тёмный фон + тёмная шапка Windows."""
    try:
        win.configure(bg=THEME["bg"])
    except tk.TclError:
        pass
    apply_windows_titlebar_theme(win)
    # DWM иногда применяет атрибуты только после показа окна
    try:
        win.after(30, lambda: apply_windows_titlebar_theme(win))
        win.after(120, lambda: apply_windows_titlebar_theme(win))
        win.after(300, lambda: apply_windows_titlebar_theme(win))
    except tk.TclError:
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config_and_paths()
        set_language(self.cfg.get("language", "auto"))
        self.versions = {}
        self.latest_release = None
        self.q = queue.Queue()
        self.busy = False
        self.ely_login_running = False
        self.instance_dialog_refresh = None  # обновление открытого окна установки
        try:
            self.log_file = open(LOG_FILE, "w", encoding="utf-8")
        except OSError:
            self.log_file = None

        root.title(t("app_title"))
        try:
            root.iconname("AncelLauncher")
        except Exception:
            pass
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        initial_w = min(1280, max(980, screen_w - 80))
        initial_h = min(780, max(600, screen_h - 100))
        root.geometry(f"{initial_w}x{initial_h}")
        root.minsize(900, 560)
        root.resizable(True, True)
        self._apply_visual_theme()
        root.after(120, lambda: apply_windows_titlebar_theme(root))

        migrate_profiles(self.cfg)
        migrate_instances(self.cfg)
        save_config(self.cfg)

        # ── Макет по концепту: сайдбар | контент / низ ──
        main = self.main = tk.Frame(root, bg=THEME["bg"], highlightthickness=0)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=0, minsize=SIDEBAR_WIDTH)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        self._icon_cache = {}
        self._hero_photo = None
        self._hero_size = (0, 0)
        self._load_ui_assets()
        self._current_page = "home"
        self._nav_buttons = {}
        self._pages = {}

        fam = getattr(self, "_ui_font_family", "Segoe UI")
        # Размеры бокового меню (иконка 40px, внутри рисунок 36px)
        NAV_ICON = 32       # размер иконки в меню
        NAV_PAD_L = 12      # отступ иконки слева
        NAV_GAP = 10        # расстояние иконка → текст
        NAV_PAD_R = 8       # запас справа от текста
        NAV_WRAP_X = 8      # поля вокруг блока меню
        # Ширина сайдбара фиксирована: SIDEBAR_WIDTH пикселей (логически в Tk).
        SIDEBAR_W = SIDEBAR_WIDTH
        self._sidebar_w = SIDEBAR_W
        self._bottom_btn_h = UI_CTRL_H
        BTN_H = self.UI_BTN_H
        NAV_INNER_W = SIDEBAR_W - 2 * 14  # одинаковые боковые отступы пунктов меню

        # ----- Сайдбар -----
        sidebar = tk.Frame(main, bg=THEME["bg"], width=SIDEBAR_W, highlightthickness=0)
        sidebar.grid(row=0, column=0, rowspan=3, sticky="nsew")
        sidebar.grid_propagate(False)
        # Фиксируем колонку: всегда ровно SIDEBAR_WIDTH Tk-пикселей.
        main.grid_columnconfigure(0, minsize=SIDEBAR_W, weight=0)
        self._sidebar = sidebar
        sidebar_divider = tk.Frame(sidebar, bg=THEME.get("border", "#263746"), width=1, bd=0, highlightthickness=0)
        sidebar_divider.pack(side="right", fill="y")
        self._sidebar_divider = sidebar_divider

        nav_wrap = tk.Frame(sidebar, bg=THEME["bg"])
        nav_wrap.pack(fill="x", padx=14, pady=(14, 14))

        self._nav_images = []  # держим ссылки на PhotoImage

        def _make_nav(key, label, icon_name, icon_size=NAV_ICON):
            img = self._load_icon(icon_name, icon_size)
            if img is not None:
                self._nav_images.append(img)
            btn = NavButton(
                nav_wrap, text=label, image=img,
                font=(fam, 11, "bold"), fg=THEME["fg"], bg=THEME["bg"],
                hover_bg=THEME["field_hover"],
                command=lambda k=key: self.show_page(k),
                height=icon_size + 12,
                pad_left=NAV_PAD_L, gap=NAV_GAP,
                width=NAV_INNER_W,
            )
            btn.pack(fill="x", pady=2)
            self._nav_buttons[key] = btn
            return btn

        _make_nav("home", t("nav_home"), "home")
        _make_nav("instances", t("nav_instances"), "instances")
        _make_nav("profiles", t("nav_profiles"), "profiles")
        _make_nav("settings", t("nav_settings"), "settings")

        # ----- Область контента -----
        content = tk.Frame(main, bg=THEME["bg"], highlightthickness=0)
        content.grid(row=0, column=1, sticky="nsew")
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self._content = content

        pages_host = tk.Frame(content, bg=THEME["bg"], highlightthickness=0)
        pages_host.grid(row=0, column=0, sticky="nsew")
        pages_host.rowconfigure(0, weight=1)
        pages_host.columnconfigure(0, weight=1)
        self._pages_host = pages_host

        # Домашняя страница: фон
        home = tk.Frame(pages_host, bg=THEME["bg"], highlightthickness=0)
        home.grid(row=0, column=0, sticky="nsew")
        self.hero = tk.Canvas(home, highlightthickness=0, bg="#0a1520", bd=0)
        self.hero.pack(fill="both", expand=True)
        self.hero.bind("<Configure>", self._paint_hero)
        self._pages["home"] = home

        # Страницы (собираются лениво / сразу)
        self._pages["instances"] = self._build_instances_page(pages_host)
        self._pages["profiles"] = self._build_profiles_page(pages_host)
        self._pages["settings"] = self._build_settings_page(pages_host)
        for key, fr in self._pages.items():
            if key != "home":
                fr.grid(row=0, column=0, sticky="nsew")
                fr.grid_remove()

        # ----- Нижняя панель справа от сайдбара -----
        bottom_bar = tk.Frame(main, bg=THEME["glass"], highlightthickness=0)
        self._bottom_bar = bottom_bar

        bottom_inner = tk.Frame(bottom_bar, bg=THEME["glass"])
        bottom_inner.pack(fill="x", padx=17, pady=14)
        bottom_inner.columnconfigure(0, weight=1)

        # Профиль встроен в нижнюю часть сайдбара и занимает доступную ширину.
        profile_group = tk.Frame(sidebar, bg=THEME["bg"], highlightthickness=0)
        # Равные внутренние отступы со всех сторон бокового меню.
        profile_group.pack(side="bottom", fill="x", padx=14, pady=14)
        self.profile_var = tk.StringVar()
        self._profile_popup_items = []
        self.profile_menu_button = ui_fixed_button(
            profile_group, side="left", menu=True,
            textvariable=self.profile_var, font=(fam, 10, "bold"),
            command=self._open_profile_popup, anchor="w", padx=12,
        )
        profile_button_wrap = self.profile_menu_button.master
        # Отключаем авто-подгонку ширины ui_fixed_button: здесь ширина задаётся сайдбаром.
        self.profile_menu_button.unbind("<Configure>")
        profile_button_wrap.configure(bg=THEME["bg"])
        profile_button_wrap.pack_configure(fill="x", expand=True, padx=0)
        profile_button_wrap.pack_propagate(False)

        # Ширину кнопки задаёт геометрический менеджер: она занимает всю
        # доступную ширину внутри profile_group, без ручных Configure-пересчётов.

        # Установка + Играть + инструменты; небольшой отступ от края сайдбара.
        right_bottom = tk.Frame(bottom_inner, bg=THEME["glass"])
        right_bottom.grid(row=0, column=0, sticky="ew", padx=(0, 0))
        right_bottom.columnconfigure(1, weight=1)

        instance_group = tk.Frame(right_bottom, bg=THEME["glass_chip"], highlightthickness=0)
        instance_group.grid(row=0, column=0, sticky="w", padx=(0, 0))
        self.instance_var = tk.StringVar()
        self._instance_popup_items = []
        self.instance_menu_button = ui_fixed_button(
            instance_group, side="left", menu=True,
            textvariable=self.instance_var, font=(fam, 10, "bold"),
            command=self._open_instance_popup,
        )

        play_wrap = tk.Frame(right_bottom, bg=THEME["glass"])
        # Центрируем кнопку относительно всей нижней панели, а не свободного места
        # между выбором установки и инструментами.
        play_wrap.place(relx=0.5, rely=0.5, anchor="center")
        self.play_btn = ui_fixed_button(
            play_wrap, side="top", accent=True,
            text=t("play"), command=self.on_play, state="disabled",
            font=(fam, 12, "bold"), padx=36,
        )

        right_tools = tk.Frame(right_bottom, bg=THEME["glass"])
        right_tools.grid(row=0, column=2, sticky="e", padx=(0, 0))
        self.console_btn = ui_fixed_button(
            right_tools, side="left", height=UI_CTRL_H,
            text="Консоль ▲", font=(fam, 10, "bold"),
            padx=14,  # одинаковый внутренний отступ слева и справа от текста
            command=self.toggle_console,
        )

        # Заглушка для совместимости (кнопки «изменить установку» больше нет внизу)
        self.instance_edit_btn = self.instance_menu_button

        # Статус — отдельная строка сетки над нижней панелью (не перекрывает контент)
        status_row = tk.Frame(main, bg=THEME["glass"], highlightthickness=0)
        status_row.grid(row=1, column=1, sticky="ew")
        self._status_row = status_row
        status_inner = tk.Frame(status_row, bg=THEME["glass"])
        status_inner.pack(fill="x", padx=14, pady=(4, 2))
        status_inner.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value=t("status_loading_versions"))
        tk.Label(
            status_inner, textvariable=self.status_var, bg=THEME["glass"], fg=THEME["muted"],
            font=(fam, 9), anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.bar = ttk.Progressbar(status_inner, mode="determinate", length=160)
        self.bar.grid(row=0, column=1, sticky="e")
        self.bar.grid_remove()
        self._progress_visible = False

        # Нижняя панель уже в row=1 — переносим на row=2
        bottom_bar.grid(row=2, column=1, sticky="ew")
        main.grid_columnconfigure(0, minsize=self._sidebar_w, weight=0)
        sidebar.configure(width=self._sidebar_w)
        sidebar.grid_propagate(False)
        # Ещё раз после полной компоновки — гарантия фиксированной ширины 275.
        def _force_sidebar_w():
            try:
                sidebar.configure(width=self._sidebar_w)
                sidebar.grid_propagate(False)
                main.grid_columnconfigure(0, minsize=self._sidebar_w, weight=0)
            except tk.TclError:
                pass
        root.after(0, _force_sidebar_w)
        root.after(100, _force_sidebar_w)

        # Консоль — между контентом и статусом (row не фиксирован, place поверх контента
        # только при показе, с отступом от низа = status+bottom)
        self.log_frame = tk.Frame(
            main, bg=THEME["log_bg"], highlightthickness=1,
            highlightbackground=THEME["border"],
        )
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            self.log_frame, height=10, state="disabled", wrap="word",
            bg=THEME["log_bg"], fg=THEME["log_fg"],
            insertbackground=THEME["green"],
            font=("Consolas", 9), relief="flat", borderwidth=0,
            padx=10, pady=8,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self.log_frame, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.config(yscrollcommand=scroll.set)

        def _layout_bars(_event=None):
            try:
                bh = max(bottom_bar.winfo_height(), bottom_bar.winfo_reqheight(), 1)
                sh = max(status_row.winfo_height(), status_row.winfo_reqheight(), 1)
                if getattr(self, "console_visible", False):
                    self.log_frame.place(
                        relx=0, rely=1.0, relwidth=1, height=220,
                        anchor="sw", y=-(bh + sh))
                else:
                    self.log_frame.place_forget()
            except tk.TclError:
                pass

        self._layout_bars = _layout_bars
        bottom_bar.bind("<Configure>", lambda e: self._layout_bars())
        status_row.bind("<Configure>", lambda e: self._layout_bars())
        self.set_console(bool(self.cfg["show_console"]))
        self.root.after(50, self._layout_bars)

        self.refresh_profile_ui()
        self.refresh_instance_ui()
        self.show_page("home")

        root.report_callback_exception = self.on_ui_exception
        self.poll_queue()
        threading.Thread(target=self.load_versions, daemon=True).start()


    # ======================================================================
    # Навигация и страницы главного окна
    # ======================================================================

    def show_page(self, name):
        """Показать страницу: home | instances | profiles | settings."""
        if name not in self._pages:
            name = "home"
        # Повторный клик по той же вкладке — ничего не делаем
        if getattr(self, "_current_page", None) == name and getattr(self, "_page_ready", False):
            return
        if getattr(self, "_page_switching", False):
            return
        self._page_switching = True
        try:
            self._current_page = name
            for key, fr in self._pages.items():
                try:
                    if key == name:
                        fr.grid(row=0, column=0, sticky="nsew")
                    else:
                        fr.grid_remove()
                except tk.TclError:
                    pass
            for key, btn in self._nav_buttons.items():
                active = (key == name)
                try:
                    btn.configure(
                        bg=THEME["green_deep"] if active else THEME["bg"],
                        fg=THEME["green_bright"] if active else THEME["fg"],
                    )
                except tk.TclError:
                    pass
            # Обновление списков — после переключения, один раз
            if name == "instances":
                self.root.after_idle(self._refresh_instances_page)
            elif name == "profiles":
                self.root.after_idle(self._refresh_profiles_page)
            elif name == "settings":
                self.root.after_idle(self._refresh_settings_page)
            elif name == "home":
                try:
                    self.root.after_idle(lambda: self._paint_hero())
                except tk.TclError:
                    pass
            self._page_ready = True
        finally:
            self._page_switching = False


    def _maybe_refresh_lists(self):
        """Обновить список на активной вкладке без циклов."""
        page = getattr(self, "_current_page", "")
        try:
            if page == "instances":
                self.root.after_idle(self._refresh_instances_page)
            elif page == "profiles":
                self.root.after_idle(self._refresh_profiles_page)
        except tk.TclError:
            pass

    def _build_instances_page(self, parent):
        """Страница установок: список + создать / импорт / изменить."""
        fam = getattr(self, "_ui_font_family", "Segoe UI")
        page = tk.Frame(parent, bg=THEME["bg"], highlightthickness=0)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)

        head = tk.Frame(page, bg=THEME["bg"])
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        tk.Label(
            head, text=t("nav_instances"), bg=THEME["bg"], fg=THEME["green_bright"],
            font=(fam, 16, "bold"), anchor="w",
        ).pack(side="left")
        btns = tk.Frame(head, bg=THEME["bg"])
        btns.pack(side="right")
        ui_button(btns, text=t("create_instance"), command=self.create_instance, accent=True).pack(
            side="left", padx=(0, 8))
        ui_button(btns, text=t("import_instance"), command=self.import_instance).pack(side="left")

        list_wrap = tk.Frame(
            page, bg=THEME["panel"],
            highlightthickness=1, highlightbackground=THEME["green_dark"],
        )
        list_wrap.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 8))
        list_wrap.columnconfigure(0, weight=1)
        list_wrap.rowconfigure(0, weight=1)

        style = ttk.Style(self.root)
        try:
            style.layout("Instances.Treeview", [
                ("Instances.Treeview.treearea", {"sticky": "nswe"}),
            ])
        except tk.TclError:
            pass
        style.configure(
            "Instances.Treeview",
            background=THEME["panel"], fieldbackground=THEME["panel"],
            foreground=THEME["fg"], rowheight=44, borderwidth=0,
            relief="flat", font=(fam, 10),
        )
        style.map(
            "Instances.Treeview",
            background=[("selected", THEME["green_dark"])],
            foreground=[("selected", "#ffffff")],
        )
        tree = ttk.Treeview(list_wrap, show="tree", selectmode="browse", style="Instances.Treeview")
        tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        self._instances_tree = tree
        self._instances_page_images = []

        foot = tk.Frame(page, bg=THEME["bg"])
        foot.grid(row=2, column=0, sticky="ew", padx=18, pady=(8, 16))
        self._inst_edit_btn = ui_button(
            foot, text=t("instance_edit_action"), command=self._page_edit_instance, state="disabled")
        self._inst_edit_btn.pack(side="left")
        self._inst_delete_btn = ui_button(
            foot, text=t("delete"), command=self._page_delete_instance, state="disabled")
        self._inst_delete_btn.pack(side="left", padx=(8, 0))
        self._inst_folder_btn = ui_button(
            foot, text="Папка игры", command=self._page_open_instance_folder, state="disabled")
        self._inst_folder_btn.pack(side="left", padx=(8, 0))

        def on_double(_e=None):
            if tree.selection():
                self._page_edit_instance()

        tree.bind("<<TreeviewSelect>>", self._on_instances_tree_select)
        tree.bind("<Double-1>", on_double)
        return page

    def _on_instances_tree_select(self, _e=None):
        if getattr(self, "_instances_refreshing", False):
            return
        tree = getattr(self, "_instances_tree", None)
        if tree is None:
            return
        sel = tree.selection()
        self._update_instance_action_buttons(bool(sel))
        if not sel:
            return
        iid = sel[0]
        if iid == self.cfg.get("active_instance"):
            return
        self.select_instance(iid)

    def _update_instance_action_buttons(self, has_selection):
        state = "normal" if has_selection else "disabled"
        for attr in ("_inst_edit_btn", "_inst_delete_btn", "_inst_folder_btn"):
            btn = getattr(self, attr, None)
            if btn is not None:
                try:
                    btn.configure(state=state)
                except tk.TclError:
                    pass

    def _refresh_instances_page(self):
        tree = getattr(self, "_instances_tree", None)
        if tree is None:
            return
        if getattr(self, "_instances_refreshing", False):
            return
        self._instances_refreshing = True
        try:
            # Отвязываем обработчик, чтобы selection_set не вызывал select_instance снова
            tree.unbind("<<TreeviewSelect>>")
            try:
                tree.delete(*tree.get_children())
            except tk.TclError:
                return
            self._instances_page_images = []
            active = self.cfg.get("active_instance")
            for inst in self.cfg.get("instances", []):
                if not isinstance(inst, dict) or not inst.get("id"):
                    continue
                iid = inst["id"]
                name = inst.get("name") or t("instance_label_fallback")
                summary = instance_summary(inst)
                mark = ""
                kind = inst.get("kind") or "vanilla"
                icon_name = kind if kind in ("fabric", "quilt", "forge", "neoforge") else "vanilla"
                image = self._ui_icon(icon_name)
                kwargs = {}
                if image is not None:
                    self._instances_page_images.append(image)
                    kwargs["image"] = image
                tree.insert("", "end", iid=iid, text=f"  {name}  ·  {summary}{mark}", **kwargs)
            if active and tree.exists(active):
                tree.selection_set(active)
                tree.focus(active)
                tree.see(active)
            self._update_instance_action_buttons(bool(tree.selection()))
            tree.bind("<<TreeviewSelect>>", self._on_instances_tree_select)
        finally:
            self._instances_refreshing = False

    def _page_edit_instance(self):
        tree = getattr(self, "_instances_tree", None)
        iid = None
        if tree is not None:
            sel = tree.selection()
            if sel:
                iid = sel[0]
        inst = None
        if iid:
            inst = next((i for i in self.cfg.get("instances", []) if i.get("id") == iid), None)
        if inst is None:
            inst = self.current_instance()
        if inst is None:
            messagebox.showinfo(t("instance"), t("instance_none_msg"), parent=self.root)
            return
        self.open_instance_dialog(inst)

    def _page_instance_content(self):
        inst = self.current_instance()
        if inst is None:
            tree = getattr(self, "_instances_tree", None)
            if tree is not None and tree.selection():
                iid = tree.selection()[0]
                inst = next((i for i in self.cfg.get("instances", []) if i.get("id") == iid), None)
        if inst is None:
            messagebox.showinfo(t("content"), t("content_need_instance"), parent=self.root)
            return
        self.open_content_manager(inst)

    def _page_open_instance_folder(self):
        tree = getattr(self, "_instances_tree", None)
        if tree is None or not tree.selection():
            return
        iid = tree.selection()[0]
        inst = next((i for i in self.cfg.get("instances", [])
                     if isinstance(i, dict) and i.get("id") == iid), None)
        if inst is not None:
            self.open_game_folder(inst)

    def _page_delete_instance(self):
        tree = getattr(self, "_instances_tree", None)
        iid = tree.selection()[0] if tree is not None and tree.selection() else None
        inst = None
        if iid:
            inst = next((i for i in self.cfg.get("instances", []) if i.get("id") == iid), None)
        if inst is None:
            inst = self.current_instance()
        if inst is None:
            return
        self.delete_instance(inst)
        self._refresh_instances_page()

    def _build_profiles_page(self, parent):
        fam = getattr(self, "_ui_font_family", "Segoe UI")
        page = tk.Frame(parent, bg=THEME["bg"], highlightthickness=0)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)

        head = tk.Frame(page, bg=THEME["bg"])
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        tk.Label(
            head, text=t("nav_profiles"), bg=THEME["bg"], fg=THEME["green_bright"],
            font=(fam, 16, "bold"), anchor="w",
        ).pack(side="left")
        btns = tk.Frame(head, bg=THEME["bg"])
        btns.pack(side="right")
        ui_button(btns, text=t("add_local_profile_btn"), command=self.add_local_profile).pack(
            side="left", padx=(0, 8))
        ui_button(btns, text=t("add_ely_account_btn"), command=self.add_ely_account, accent=True).pack(
            side="left")

        list_wrap = tk.Frame(
            page, bg=THEME["panel"],
            highlightthickness=1, highlightbackground=THEME["green_dark"],
        )
        list_wrap.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 8))
        list_wrap.columnconfigure(0, weight=1)
        list_wrap.rowconfigure(0, weight=1)

        style = ttk.Style(self.root)
        try:
            style.layout("ProfilesPage.Treeview", [
                ("ProfilesPage.Treeview.treearea", {"sticky": "nswe"}),
            ])
        except tk.TclError:
            pass
        style.configure(
            "ProfilesPage.Treeview",
            background=THEME["panel"], fieldbackground=THEME["panel"],
            foreground=THEME["fg"], rowheight=36, borderwidth=0,
            relief="flat", font=(fam, 10),
        )
        style.map(
            "ProfilesPage.Treeview",
            background=[("selected", THEME["green_dark"])],
            foreground=[("selected", "#ffffff")],
        )
        tree = ttk.Treeview(
            list_wrap, show="tree", selectmode="browse", style="ProfilesPage.Treeview")
        tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        self._profiles_tree = tree
        self._profiles_page_images = []

        foot = tk.Frame(page, bg=THEME["bg"])
        foot.grid(row=2, column=0, sticky="ew", padx=18, pady=(8, 16))
        ui_button(foot, text=t("profile_configure"), command=self._page_configure_profile).pack(
            side="left")
        ui_button(foot, text=t("delete"), command=self._page_delete_profile).pack(
            side="left", padx=(8, 0))

        def on_double(_e=None):
            self._page_configure_profile()

        tree.bind("<<TreeviewSelect>>", self._on_profiles_tree_select)
        tree.bind("<Double-1>", on_double)
        return page

    def _on_profiles_tree_select(self, _e=None):
        if getattr(self, "_profiles_refreshing", False):
            return
        tree = getattr(self, "_profiles_tree", None)
        if tree is None:
            return
        sel = tree.selection()
        if not sel:
            return
        key = sel[0]
        if key == self.cfg.get("active_profile"):
            return
        self.select_profile(key)

    def _refresh_profiles_page(self):
        tree = getattr(self, "_profiles_tree", None)
        if tree is None:
            return
        if getattr(self, "_profiles_refreshing", False):
            return
        self._profiles_refreshing = True
        try:
            tree.unbind("<<TreeviewSelect>>")
            try:
                tree.delete(*tree.get_children())
            except tk.TclError:
                return
            self._profiles_page_images = []
            active = self.cfg.get("active_profile")
            for p in profile_list(self.cfg):
                icon = self._profile_head_image(p, size=32)
                kwargs = {}
                if icon is not None:
                    self._profiles_page_images.append(icon)
                    kwargs["image"] = icon
                kind = "Ely.by" if p["type"] == "ely" else t("profile_kind_local")
                tree.insert(
                    "", "end", iid=p["key"],
                    text=f"  {p['name']}  ·  {kind}", **kwargs)
            if active and tree.exists(active):
                tree.selection_set(active)
                tree.focus(active)
                tree.see(active)
            tree.bind("<<TreeviewSelect>>", self._on_profiles_tree_select)
        finally:
            self._profiles_refreshing = False

    def _page_configure_profile(self):
        tree = getattr(self, "_profiles_tree", None)
        key = tree.selection()[0] if tree is not None and tree.selection() else None
        if not key:
            cur = active_profile(self.cfg)
            key = cur["key"] if cur else None
        if not key:
            return
        self.select_profile(key)
        p = next((item for item in profile_list(self.cfg) if item["key"] == key), None)
        if not p:
            return
        if p["type"] == "ely":
            self.open_ely_profile()
        else:
            self.open_local_profile_settings()
        self._refresh_profiles_page()

    def _page_delete_profile(self):
        tree = getattr(self, "_profiles_tree", None)
        key = tree.selection()[0] if tree is not None and tree.selection() else None
        if not key:
            return
        p = next((item for item in profile_list(self.cfg) if item["key"] == key), None)
        if not p:
            return
        if not messagebox.askyesno(
                t("profile_delete_title"),
                t("profile_delete_confirm", name=p["name"]),
                parent=self.root):
            return
        skin = remove_profile(self.cfg, key)
        if skin:
            try:
                Path(skin).unlink(missing_ok=True)
            except OSError:
                pass
        save_config(self.cfg)
        self.refresh_profile_ui()
        self._refresh_profiles_page()

    def _build_settings_page(self, parent):
        fam = getattr(self, "_ui_font_family", "Segoe UI")
        page = tk.Frame(parent, bg=THEME["bg"], highlightthickness=0)
        page.columnconfigure(0, weight=1)

        inner = tk.Frame(page, bg=THEME["bg"])
        inner.grid(row=0, column=0, sticky="nsew", padx=17, pady=20)
        inner.columnconfigure(0, weight=1)
        self._settings_inner = inner

        tk.Label(
            inner, text=t("nav_settings"), bg=THEME["bg"], fg=THEME["green_bright"],
            font=(fam, 16, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 18))

        # Папка игры
        tk.Label(
            inner, text=t("settings_game_dir"), bg=THEME["bg"], fg=THEME["fg"],
            font=(fam, 10, "bold"), anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(0, 6))

        self._settings_game_var = tk.StringVar()
        path_row = tk.Frame(inner, bg=THEME["bg"])
        path_row.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        path_row.columnconfigure(0, weight=1)
        ui_entry(path_row, textvariable=self._settings_game_var).grid(
            row=0, column=0, sticky="ew", padx=(0, 8))
        ui_button(path_row, text=t("settings_browse"), command=self._settings_browse_game).grid(
            row=0, column=1)

        tk.Label(
            inner, text=t("settings_game_dir_hint"), bg=THEME["bg"], fg=THEME["muted"],
            font=(fam, 9), wraplength=640, justify="left", anchor="w",
        ).grid(row=3, column=0, sticky="ew", pady=(0, 20))

        # Язык
        tk.Label(
            inner, text=t("settings_language"), bg=THEME["bg"], fg=THEME["fg"],
            font=(fam, 10, "bold"), anchor="w",
        ).grid(row=4, column=0, sticky="ew", pady=(0, 6))

        self._settings_lang_labels = {
            "auto": t("settings_lang_auto"),
            "ru": t("settings_lang_ru"),
            "en": t("settings_lang_en"),
        }
        self._settings_lang_display = tk.StringVar()
        self._settings_lang_code = {"value": "auto"}
        lang_box = ui_combobox(
            inner, textvariable=self._settings_lang_display, state="readonly",
            values=list(self._settings_lang_labels.values()), width=36,
        )
        lang_box.grid(row=5, column=0, sticky="w", pady=(0, 4))

        def on_lang(_e=None):
            chosen = self._settings_lang_display.get()
            for code, label in self._settings_lang_labels.items():
                if label == chosen:
                    self._settings_lang_code["value"] = code
                    break

        lang_box.bind("<<ComboboxSelected>>", on_lang)
        self._settings_lang_box = lang_box

        tk.Label(
            inner, text=t("settings_restart_hint"), bg=THEME["bg"], fg=THEME["muted"],
            font=(fam, 9), wraplength=640, justify="left", anchor="w",
        ).grid(row=6, column=0, sticky="ew", pady=(4, 20))

        tk.Label(
            inner, text=t("about_version", version=LAUNCHER_VERSION),
            bg=THEME["bg"], fg=THEME["muted"], font=(fam, 9), anchor="w",
        ).grid(row=7, column=0, sticky="ew", pady=(0, 12))

        ui_button(inner, text=t("settings_save"), command=self._settings_save, accent=True).grid(
            row=8, column=0, sticky="w", pady=(0, 8))
        return page


    def _refresh_settings_page(self):
        # обновить подписи языков (после смены языка)
        self._settings_lang_labels = {
            "auto": t("settings_lang_auto"),
            "ru": t("settings_lang_ru"),
            "en": t("settings_lang_en"),
        }
        try:
            self._settings_lang_box.configure(values=list(self._settings_lang_labels.values()))
        except (tk.TclError, AttributeError):
            pass
        current_game = str(self.cfg.get("game_dir") or "").strip() or str(default_game_dir())
        self._settings_game_var.set(current_game)
        current = (self.cfg.get("language") or "auto").strip().lower()
        if current not in self._settings_lang_labels:
            current = "auto"
        self._settings_lang_code["value"] = current
        self._settings_lang_display.set(self._settings_lang_labels[current])

    def _settings_browse_game(self):
        chosen = filedialog.askdirectory(
            parent=self.root,
            title=t("settings_game_dir_browse"),
            mustexist=True,
            initialdir=self._settings_game_var.get() or str(default_game_dir()),
        )
        if chosen:
            self._settings_game_var.set(chosen)

    def _settings_save(self):
        self.cfg["language"] = self._settings_lang_code["value"]
        chosen_dir = self._settings_game_var.get().strip()
        default = str(default_game_dir())
        if not chosen_dir or Path(chosen_dir).resolve() == Path(default).resolve():
            self.cfg["game_dir"] = ""
        else:
            self.cfg["game_dir"] = chosen_dir
        save_config(self.cfg)
        apply_game_dir(self.cfg)
        set_language(self.cfg["language"])
        try:
            self.root.title(t("app_title"))
        except tk.TclError:
            pass
        # обновить подписи навигации
        labels = {
            "home": t("nav_home"),
            "instances": t("nav_instances"),
            "profiles": t("nav_profiles"),
            "settings": t("nav_settings"),
        }
        for key, btn in self._nav_buttons.items():
            try:
                btn.configure(text=f"  {labels.get(key, key)}")
            except tk.TclError:
                pass
        self._refresh_settings_page()
        messagebox.showinfo(t("settings_title"), t("settings_saved"), parent=self.root)

    def _load_ui_assets(self):
        """Фон и иконки из папки assets рядом с лаунчером."""
        assets = RESOURCE_DIR / "assets"
        self._hero_path = None
        for candidate in (
            assets / "hero_bg_1280.png",
            assets / "hero_bg.png",
            assets / "hero_bg_1280.jpg",
        ):
            if candidate.is_file():
                self._hero_path = candidate
                break
        self._icons_dir = assets / "icons"
        self._set_window_icon(self.root)

    def _set_window_icon(self, win):
        """Иконка окна (заголовок + панель задач). Windows: .ico + PNG."""
        try:
            base = Path(getattr(self, "_icons_dir", RESOURCE_DIR / "assets" / "icons"))
        except Exception:
            base = RESOURCE_DIR / "assets" / "icons"
        if not hasattr(self, "_window_icon_photos"):
            self._window_icon_photos = []

        # PNG для title bar (iconphoto) — надёжнее на Tk
        photos = []
        for name in ("app_32.png", "app_16.png", "app_48.png", "app_64.png", "app.png"):
            p = base / name
            if not p.is_file():
                continue
            try:
                photos.append(tk.PhotoImage(file=str(p)))
            except tk.TclError:
                pass
        if photos:
            self._window_icon_photos.extend(photos)
            try:
                win.iconphoto(True, *photos)
            except tk.TclError:
                try:
                    win.iconphoto(True, photos[0])
                except tk.TclError:
                    pass

        # .ico для панели задач / Alt-Tab на Windows (нужен абсолютный путь)
        ico = base / "app.ico"
        if os.name == "nt" and ico.is_file():
            ico_s = str(ico.resolve())

            def _apply_ico(_e=None):
                try:
                    win.iconbitmap(default=ico_s)
                except tk.TclError:
                    try:
                        win.iconbitmap(ico_s)
                    except tk.TclError:
                        pass

            _apply_ico()
            # повторно после отрисовки окна — иначе Windows иногда оставляет дефолт
            try:
                win.after(200, _apply_ico)
                win.after(800, _apply_ico)
            except tk.TclError:
                pass

    # Единый стандарт иконок и кнопок тулбара
    # UI_BTN_H      — высота всех кнопок тулбара (верх/низ), px
    # UI_ICON_TOOL  — размер глифа в квадратной кнопке «только иконка»
    # UI_ICON_INLINE— размер глифа рядом с текстом (меню профиля/установки)
    UI_BTN_H = UI_CTRL_H  # 46
    UI_ICON_TOOL = 28
    UI_TOOL_BTN = 46        # размер кнопок «папка» / «консоль»
    UI_ICON_TOOL_BIG = 32   # размер иконок в кнопках
    UI_ICON_INLINE = 28

    def _load_icon(self, name, preferred_size=None):
        """Загрузить PNG-иконку ближайшего размера. Кэш: (name, size)."""
        size = preferred_size or self.UI_ICON_INLINE
        cache_key = (name, size)
        if not hasattr(self, "_icon_cache"):
            self._icon_cache = {}
        if cache_key in self._icon_cache:
            return self._icon_cache[cache_key]
        base = Path(getattr(self, "_icons_dir", Path()))
        # Порядок предпочтений: точный размер → близкие → без суффикса
        candidates = [
            base / f"{name}_{size}.png",
            base / f"{name}.png",
        ]
        for s in (size, 20, 24, 28, 32, 16, 48):
            candidates.append(base / f"{name}_{s}.png")
        seen = set()
        img = None
        for p in candidates:
            key = str(p)
            if key in seen or not p.is_file():
                continue
            seen.add(key)
            try:
                img = tk.PhotoImage(file=str(p))
                break
            except tk.TclError:
                img = None
        self._icon_cache[cache_key] = img
        return img

    def _ui_icon(self, name):
        """Иконка для меню/списков. Загрузчики — крупнее и чётче."""
        if name in ("vanilla", "fabric", "quilt", "forge", "neoforge"):
            return self._load_icon(name, 32)
        return self._load_icon(name, self.UI_ICON_INLINE)

    def _pil_to_photo(self, pil_img):
        import io
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        return tk.PhotoImage(data=buf.getvalue())

    def _profile_head_image(self, profile, size=32):
        """Голова: локальный — ч/б Steve; Ely.by — скин с ely.by."""
        if not hasattr(self, "_head_cache"):
            self._head_cache = {}
        ptype = profile.get("type") if profile else "local"
        name = profile.get("name") if profile else ""
        key = (profile.get("key") if profile else None, size, ptype, name)
        if key in self._head_cache:
            return self._head_cache[key]
        img = None
        try:
            if ptype == "ely" and name:
                skin_file = resolve_profile_skin_file(self.cfg, profile)
                if skin_file is not None:
                    head = extract_skin_head(skin_file, size=size, grayscale=False)
                    if head is not None:
                        img = self._pil_to_photo(head)
                # фоновая подгрузка, если кэша ещё нет
                if img is None:
                    def _fetch(n=name, s=size, k=key):
                        path = download_ely_skin(n, force=True)
                        if path is None:
                            return
                        head = extract_skin_head(path, size=s, grayscale=False)
                        if head is None:
                            return
                        def _apply():
                            try:
                                photo = self._pil_to_photo(head)
                                self._head_cache[k] = photo
                                self.refresh_profile_ui()
                            except Exception:
                                pass
                        try:
                            self.root.after(0, _apply)
                        except tk.TclError:
                            pass
                    threading.Thread(target=_fetch, daemon=True).start()
            # локальный или fallback — ч/б Steve
            if img is None:
                img = self._load_icon("steve_head", size)
                if img is None:
                    # сгенерировать из assets/steve если нет файла
                    img = self._load_icon("local", size)
        except Exception:
            img = self._load_icon("steve_head", size)
        self._head_cache[key] = img
        return img

    def _invalidate_head_cache(self):
        self._head_cache = {}

    def _set_label_icon(self, label, name):
        img = self._ui_icon(name)
        if img is not None:
            label.configure(image=img, text="", compound="center", anchor="center")
            label.image = img
        else:
            label.configure(image="", text="")

    def _set_menubutton_icon(self, button, name):
        """Иконка слева от текста; контент по центру по вертикали.
        Размер как у головы Стива (32px), чтобы обе кнопки нижней панели совпадали."""
        if name in ("vanilla", "fabric", "quilt", "forge", "neoforge",
                    "local", "ely", "profiles", "steve_head"):
            size = 32
        else:
            size = self.UI_ICON_INLINE
        img = self._load_icon(name, size)
        if img is not None and name in ("vanilla", "fabric", "quilt", "forge", "neoforge"):
            # Нормализуем иконки сборок к 32px независимо от исходного PNG.
            try:
                w, h = img.width(), img.height()
                if max(w, h) > 32:
                    factor = max(1, (max(w, h) + 31) // 32)
                    img = img.subsample(factor, factor)
            except (tk.TclError, AttributeError):
                pass
        if img is not None:
            button.configure(image=img, compound="left", anchor="center")
            button.image = img
        else:
            try:
                button.configure(image="", compound="left", anchor="center")
            except tk.TclError:
                pass

    def _set_tool_icon(self, button, name, fallback_text=""):
        """Кнопка только с иконкой: квадрат side×side, глиф по центру."""
        side = self.UI_TOOL_BTN
        glyph = self.UI_ICON_TOOL_BIG
        img = self._load_icon(name, glyph)
        if img is not None:
            button.configure(
                image=img, text="", compound="center", anchor="center",
                justify="center", padx=0, pady=0, width=side, height=side,
            )
            button.image = img
            return
        if fallback_text:
            try:
                # Без PNG: текстовый глиф, центрируем (квадрат задаётся снаружи)
                button.configure(
                    text=fallback_text, image="", compound="center",
                    anchor="center", justify="center", padx=0, pady=0,
                )
            except tk.TclError:
                pass

    def _paint_hero(self, event=None):

        """Фон: изображение Minecraft (или запасной градиент)."""
        c = self.hero
        w = max(c.winfo_width(), 1)
        h = max(c.winfo_height(), 1)
        if w < 8 or h < 8:
            return
        # Не пересобирать картинку при микродвижениях
        if self._hero_photo is not None and abs(w - self._hero_size[0]) < 8 and abs(h - self._hero_size[1]) < 8:
            return
        c.delete("all")
        painted = False
        if self._hero_path and self._hero_path.is_file():
            try:
                from PIL import Image
                src = Image.open(self._hero_path).convert("RGB")
                # cover: заполнить canvas с обрезкой
                sw, sh = src.size
                scale = max(w / sw, h / sh)
                nw, nh = int(sw * scale), int(sh * scale)
                resized = src.resize((nw, nh), Image.LANCZOS)
                left = max(0, (nw - w) // 2)
                top = max(0, (nh - h) // 2)
                cropped = resized.crop((left, top, left + w, top + h))
                # лёгкое затемнение снизу/сверху для читаемости панелей
                from PIL import ImageEnhance
                cropped = ImageEnhance.Brightness(cropped).enhance(0.85)
                tmp = APP_DIR / "assets" / "_hero_runtime.png"
                tmp.parent.mkdir(parents=True, exist_ok=True)
                cropped.save(tmp, format="PNG")
                self._hero_photo = tk.PhotoImage(file=str(tmp))
                self._hero_size = (w, h)
                c.create_image(0, 0, anchor="nw", image=self._hero_photo)
                painted = True
            except Exception:
                painted = False
        if not painted:
            # Запасной сине-зелёный градиент в духе леса
            steps = 32
            for i in range(steps):
                t = i / max(steps - 1, 1)
                r = int(10 + (20 - 10) * t)
                g = int(25 + (40 - 25) * (1 - t))
                b = int(35 + (55 - 35) * (1 - abs(t - 0.3)))
                color = f"#{r:02x}{g:02x}{b:02x}"
                y0 = int(h * i / steps)
                y1 = int(h * (i + 1) / steps) + 1
                c.create_rectangle(0, y0, w, y1, fill=color, outline="")
            self._hero_size = (w, h)
        # Тонкие полоски у стыка с панелями
        c.create_rectangle(0, h - 2, w, h, fill="#0a1210", outline="")

    def _apply_visual_theme(self):
        """Тёмный Minecraft-интерфейс с единым зелёным акцентом."""
        t = THEME
        self.root.configure(bg=t["bg"])
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        try:
            families = {name.lower(): name for name in tkfont.families(self.root)}
        except Exception:
            families = {}
        pixel_family = globals().get("_PIXEL_FONT_FAMILY")
        if pixel_family and pixel_family.lower() not in families:
            try:
                families = {name.lower(): name for name in tkfont.families(self.root)}
            except Exception:
                pass
        pixel_family = next(
            (families[n.lower()] for n in (
                pixel_family, "Press Start 2P", "Minecraft", "Minecraftia", "Consolas"
            ) if n and n.lower() in families),
            None,
        )
        body_family = next(
            (families[n.lower()] for n in ("Segoe UI", "Arial", "TkDefaultFont")
             if n.lower() in families),
            "TkDefaultFont",
        )
        ui_font = (body_family, 10)
        ui_bold = (body_family, 10, "bold")
        hero_font = (pixel_family, 15, "bold") if pixel_family else (body_family, 20, "bold")
        self._ui_font_family = body_family

        self.root.option_add("*Font", ui_font)
        self.root.option_add("*Background", t["bg"])
        self.root.option_add("*Foreground", t["fg"])
        self.root.option_add("*insertBackground", t["green"])
        self.root.option_add("*selectBackground", t["green_dark"])
        self.root.option_add("*selectForeground", "#ffffff")
        # Выпадающий список Combobox без белой системной рамки
        self.root.option_add("*TCombobox*Listbox.background", t["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", t["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", t["green_dark"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)
        self.root.option_add("*TCombobox*Listbox.relief", "flat")
        self.root.option_add("*TCombobox*Listbox.highlightThickness", 0)

        style.configure(".", font=ui_font, background=t["bg"], foreground=t["fg"], borderwidth=0)
        style.configure("App.TFrame", background=t["bg"])
        style.configure("TFrame", background=t["bg"])
        style.configure("Header.TFrame", background=t["header"], relief="flat", borderwidth=0)
        style.configure("Card.TFrame", background=t["panel"])
        style.configure("Action.TFrame", background=t["header"], relief="flat", borderwidth=0)

        style.configure(
            "Card.TLabelframe", background=t["panel"], bordercolor=t["border"],
            relief="solid", borderwidth=1, padding=16,
        )
        style.configure(
            "Card.TLabelframe.Label", background=t["panel"],
            foreground=t["green"], font=ui_bold,
        )
        style.configure(
            "TLabelframe", background=t["panel"], bordercolor=t["border"],
            relief="solid", borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label", background=t["panel"],
            foreground=t["green"], font=ui_bold,
        )

        style.configure("TLabel", background=t["bg"], foreground=t["fg"], padding=(0, 2))
        style.configure(
            "Muted.TLabel", background=t["panel"], foreground=t["muted"],
            font=(body_family, 9),
        )
        style.configure(
            "StatusLine.TLabel", background=t["bg"], foreground=t["muted"],
            font=(body_family, 9),
        )
        style.configure(
            "Hero.TLabel", background=t["header"], foreground=t["green_bright"],
            font=hero_font, padding=(0, 2),
        )
        style.configure(
            "Subtitle.TLabel", background=t["header"], foreground=t["muted"],
            font=(body_family, 9),
        )
        style.configure(
            "StatusBadge.TLabel", background=t["green_deep"], foreground=t["green_bright"],
            font=ui_bold, padding=(14, 9),
        )
        style.configure(
            "Title.TLabel", background=t["bg"], foreground=t["green"],
            font=hero_font, padding=(0, 4),
        )

        style.configure(
            "TButton", background=t["panel"], foreground=t["fg"],
            padding=(UI_CTRL_PAD_H, UI_CTRL_PAD_V), font=ui_bold, borderwidth=0, relief="flat",
            focusthickness=0, focuscolor=t["panel"],
            lightcolor=t["panel"], darkcolor=t["panel"], bordercolor=t["panel"],
        )
        style.map(
            "TButton",
            background=[
                ("active", t["field_hover"]),
                ("pressed", t["green_dark"]),
                ("disabled", "#121812"),
            ],
            foreground=[
                ("disabled", "#5a6a5c"),
                ("active", t["green_bright"]),
            ],
            bordercolor=[("active", t["field_hover"]), ("!active", t["panel"])],
            lightcolor=[("active", t["field_hover"]), ("!active", t["panel"])],
            darkcolor=[("active", t["field_hover"]), ("!active", t["panel"])],
        )
        style.configure(
            "Accent.TButton", background=t["green_mid"], foreground="#ffffff",
            padding=(UI_CTRL_PAD_H + 2, UI_CTRL_PAD_V), font=ui_bold, relief="flat",
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", t["green"]),
                ("pressed", t["green_dark"]),
                ("disabled", t["green_deep"]),
            ],
            foreground=[("disabled", "#7a9a70")],
        )

        style.configure(
            "TEntry", fieldbackground=t["field"], foreground=t["fg"],
            insertcolor=t["green"], padding=(UI_CTRL_PAD_H - 4, UI_CTRL_PAD_V),
            bordercolor=t["border"], lightcolor=t["field"], darkcolor=t["field"],
            relief="flat", borderwidth=1,
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", t["green_dark"]), ("!focus", t["border"])],
            lightcolor=[("focus", t["green_dark"]), ("!focus", t["field"])],
            darkcolor=[("focus", t["green_dark"]), ("!focus", t["field"])],
        )
        style.configure(
            "TCombobox", fieldbackground=t["field"], background=t["field"],
            foreground=t["fg"], arrowcolor=t["green"],
            padding=(UI_CTRL_PAD_H - 4, UI_CTRL_PAD_V),
            bordercolor=t["border"], lightcolor=t["field"], darkcolor=t["field"],
            borderwidth=1, relief="flat",
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", t["field"]), ("disabled", t["panel"])],
            foreground=[("readonly", t["fg"]), ("disabled", t["muted"])],
            selectbackground=[("readonly", t["green_dark"])],
            selectforeground=[("readonly", "#ffffff")],
            background=[("readonly", t["field"]), ("disabled", t["panel"])],
            bordercolor=[
                ("focus", t["green_dark"]), ("!focus", t["border"]),
                ("readonly", t["border"]),
            ],
            lightcolor=[
                ("focus", t["green_dark"]), ("!focus", t["field"]),
                ("readonly", t["field"]),
            ],
            darkcolor=[
                ("focus", t["green_dark"]), ("!focus", t["field"]),
                ("readonly", t["field"]),
            ],
            arrowcolor=[("disabled", t["muted"]), ("!disabled", t["green"])],
        )
        style.configure(
            "TMenubutton", background=t["field"], foreground=t["fg"],
            padding=(14, 11), font=ui_bold, relief="flat", borderwidth=0,
        )
        style.map(
            "TMenubutton",
            background=[("active", t["field_hover"])],
            foreground=[("active", t["green_bright"])],
        )

        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=t["panel"], background=t["green"],
            bordercolor=t["panel"], lightcolor=t["green_bright"], darkcolor=t["green_mid"],
        )
        style.configure(
            "TScrollbar",
            background=t["field"], troughcolor=t["panel"],
            arrowcolor=t["muted"], bordercolor=t["panel"],
            darkcolor=t["field"], lightcolor=t["field"],
            gripcount=0, width=10,
        )
        style.map(
            "TScrollbar",
            background=[("active", t["green_mid"]), ("pressed", t["green"])],
            arrowcolor=[("active", t["green_bright"])],
        )
        style.configure(
            "Treeview", background=t["panel"], fieldbackground=t["panel"],
            foreground=t["fg"], rowheight=34, borderwidth=0, font=ui_font,
        )
        style.configure(
            "Treeview.Heading", background=t["field"], foreground=t["green"],
            font=ui_bold, padding=8,
        )
        style.map(
            "Treeview",
            background=[("selected", t["green_dark"])],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "TCheckbutton", background=t["panel"], foreground=t["fg"],
            focusthickness=0,
        )
        style.map(
            "TCheckbutton",
            background=[("active", t["panel"])],
            foreground=[("active", t["green_bright"])],
        )
        style.configure(
            "TSpinbox", fieldbackground=t["field"], foreground=t["fg"],
            insertcolor=t["green"], arrowcolor=t["green"],
            background=t["field"],
            bordercolor=t["border"], lightcolor=t["field"], darkcolor=t["field"],
            relief="flat", borderwidth=1,
        )
        style.map(
            "TSpinbox",
            bordercolor=[("focus", t["green_dark"]), ("!focus", t["border"])],
            lightcolor=[("focus", t["green_dark"]), ("!focus", t["field"])],
            darkcolor=[("focus", t["green_dark"]), ("!focus", t["field"])],
            arrowcolor=[("disabled", t["muted"])],
        )
        style.configure("Bottom.TFrame", background=t["header"], relief="flat")
        style.configure("Chip.TFrame", background=t["field"], relief="flat")
        # Выпадающий список и шестерёнка — один блок, одна высота
        style.configure(
            "Chip.TMenubutton", background=t["field"], foreground=t["fg"],
            padding=(10, 9), font=ui_bold, relief="flat",
        )
        style.map(
            "Chip.TMenubutton",
            background=[("active", t["field_hover"])],
            foreground=[("active", t["green_bright"])],
        )
        style.configure(
            "Chip.TButton", background=t["field"], foreground=t["fg"],
            padding=(10, 9), font=ui_bold, relief="flat",
        )
        style.map(
            "Chip.TButton",
            background=[("active", t["field_hover"]), ("disabled", t["field"])],
            foreground=[("active", t["green_bright"]), ("disabled", "#5a6a5c")],
        )
        style.configure(
            "Profile.TMenubutton", background=t["field"], foreground=t["fg"],
            padding=(12, 8), font=ui_bold, relief="flat",
        )
        style.map(
            "Profile.TMenubutton",
            background=[("active", t["field_hover"])],
            foreground=[("active", t["green_bright"])],
        )
        style.configure(
            "Instance.TMenubutton", background=t["field"], foreground=t["fg"],
            padding=(18, 10), font=ui_bold, relief="flat",
        )
        style.map(
            "Instance.TMenubutton",
            background=[("active", t["field_hover"])],
            foreground=[("active", t["green_bright"])],
        )
        style.configure(
            "Brand.TLabel", background=t["header"], foreground=t["green_bright"],
            font=(body_family, 13, "bold"),
        )
        style.configure(
            "HeroCenter.TLabel", background=t["bg"], foreground=t["fg"],
            font=(body_family, 22, "bold"),
        )
        style.configure(
            "SubtitleCenter.TLabel", background=t["bg"], foreground=t["muted"],
            font=(body_family, 11),
        )
        # Accent-кнопка: тот же вертикальный padding и шрифт, что у TButton → одинаковая высота
        style.configure(
            "Accent.TButton", background=t["green_mid"], foreground="#ffffff",
            padding=(UI_CTRL_PAD_H + 2, UI_CTRL_PAD_V), font=ui_bold, relief="flat",
        )

    # ======================================================================
    # Профили
    # ======================================================================

    def refresh_profile_ui(self):
        """В главном окне показываются только добавленные профили."""
        self._invalidate_head_cache()
        profiles = profile_list(self.cfg)
        self._profile_popup_items = []
        self._profile_menu_images = []
        for p in profiles:
            icon = self._profile_head_image(p, size=32)
            if icon is not None:
                self._profile_menu_images.append(icon)
            self._profile_popup_items.append({
                "label": profile_label(p),
                "image": icon,
                "command": lambda key=p["key"]: self.select_profile(key),
            })
        current = active_profile(self.cfg)
        if current is None:
            self.profile_var.set("  Профиль  ▾")
            img = self._load_icon("steve_head", 32) or self._load_icon("local", 32)
            if img is not None:
                try:
                    self.profile_menu_button.configure(image=img, compound="left")
                    self.profile_menu_button.image = img
                except tk.TclError:
                    pass
        else:
            self.profile_var.set(f"  {current['name']}  ▾")
            img = self._profile_head_image(current, size=32)
            if img is not None:
                try:
                    self.profile_menu_button.configure(image=img, compound="left")
                    self.profile_menu_button.image = img
                except tk.TclError:
                    pass
        self.update_buttons()
        self._maybe_refresh_lists()

    def _style_menu(self, menu):
        """Нативный Menu — запасной вариант (обводка Windows часто остаётся)."""
        try:
            menu.configure(
                background=THEME["field"], foreground=THEME["fg"],
                activebackground=THEME["green_dark"], activeforeground="#ffffff",
                borderwidth=0, relief="flat", bd=0, activeborderwidth=0,
            )
        except tk.TclError:
            pass

    def _make_thin_scrollbar(self, parent, target):
        """Тонкий скроллбар без системной белой полосы (Canvas)."""
        bar = tk.Canvas(
            parent, width=8, bg=THEME["panel"], highlightthickness=0, bd=0,
            cursor="hand2",
        )
        # busy — защита от re-entrancy: grid/grid_remove меняет размер Treeview,
        # тот снова вызывает yscrollcommand → без флага получается бесконечный цикл
        # и раздувание памяти.
        state = {"first": 0.0, "last": 1.0, "drag": None, "busy": False, "visible": False}

        def redraw(*_):
            if state["busy"]:
                return
            try:
                bar.delete("all")
            except tk.TclError:
                return
            h = max(bar.winfo_height(), 1)
            first, last = state["first"], state["last"]
            span = max(last - first, 0.05)
            if span >= 0.999:
                return  # всё видно — скролл не рисуем
            thumb_h = max(int(h * span), 24)
            y0 = int((h - thumb_h) * first / max(1.0 - span, 0.001))
            y1 = y0 + thumb_h
            pad = 1
            bar.create_rectangle(
                pad, y0, 8 - pad, y1,
                fill=THEME["green_mid"], outline="", tags=("thumb",),
            )

        def _apply_visibility():
            """Показать/скрыть полосу без повторного входа в set_scroll."""
            if state["busy"]:
                return
            need = (state["last"] - state["first"]) < 0.999
            if need == state["visible"]:
                redraw()
                return
            state["busy"] = True
            try:
                if need:
                    bar.grid(row=0, column=1, sticky="ns", padx=(0, 2), pady=4)
                    state["visible"] = True
                else:
                    bar.grid_remove()
                    state["visible"] = False
            except tk.TclError:
                pass
            finally:
                state["busy"] = False
            redraw()

        def set_scroll(*args):
            # yscrollcommand API: set(first, last)
            if state["busy"]:
                return
            try:
                first = float(args[0])
                last = float(args[1])
            except (TypeError, ValueError, IndexError):
                return
            # Игнорируем «шум», если позиция не изменилась
            if abs(first - state["first"]) < 1e-6 and abs(last - state["last"]) < 1e-6:
                return
            state["first"] = first
            state["last"] = last
            # grid/grid_remove откладываем: иначе Treeview мгновенно
            # пересчитывает скролл и снова зовёт set_scroll.
            try:
                bar.after_idle(_apply_visibility)
            except tk.TclError:
                _apply_visibility()

        def on_click(event):
            h = max(bar.winfo_height(), 1)
            first, last = state["first"], state["last"]
            span = max(last - first, 0.05)
            thumb_h = max(int(h * span), 24)
            y0 = int((h - thumb_h) * first / max(1.0 - span, 0.001))
            if y0 <= event.y <= y0 + thumb_h:
                state["drag"] = event.y - y0
            else:
                # клик по треку — прыжок
                ratio = max(0.0, min(1.0, event.y / h))
                try:
                    target.yview_moveto(ratio * max(1.0 - span, 0.0))
                except tk.TclError:
                    pass

        def on_drag(event):
            if state["drag"] is None:
                return
            h = max(bar.winfo_height(), 1)
            first, last = state["first"], state["last"]
            span = max(last - first, 0.05)
            thumb_h = max(int(h * span), 24)
            y0 = event.y - state["drag"]
            max_y = max(h - thumb_h, 1)
            ratio = max(0.0, min(1.0, y0 / max_y)) * max(1.0 - span, 0.0)
            try:
                target.yview_moveto(ratio)
            except tk.TclError:
                pass

        def on_release(_event=None):
            state["drag"] = None

        def on_wheel(event):
            # Windows / macOS
            delta = 0
            if getattr(event, "delta", 0):
                delta = -1 if event.delta > 0 else 1
            elif getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            if delta:
                try:
                    target.yview_scroll(delta, "units")
                except tk.TclError:
                    pass
                return "break"

        bar.bind("<Button-1>", on_click)
        bar.bind("<B1-Motion>", on_drag)
        bar.bind("<ButtonRelease-1>", on_release)
        bar.bind("<Configure>", redraw)
        target.configure(yscrollcommand=set_scroll)
        target.bind("<MouseWheel>", on_wheel)
        target.bind("<Button-4>", on_wheel)
        target.bind("<Button-5>", on_wheel)
        return bar

    def _dismiss_popup(self, event=None):
        """Закрывает кастомное меню."""
        self._popup_open = False
        pop = getattr(self, "_active_popup", None)
        if pop is not None:
            try:
                pop.destroy()
            except tk.TclError:
                pass
            self._active_popup = None

    def _popup_outside_click(self, event):
        """Клик мимо меню — закрыть."""
        if not getattr(self, "_popup_open", False):
            return
        pop = getattr(self, "_active_popup", None)
        if pop is None:
            return
        try:
            w = event.widget
            while w is not None:
                if w == pop:
                    return
                w = getattr(w, "master", None)
        except tk.TclError:
            pass
        try:
            px, py = pop.winfo_rootx(), pop.winfo_rooty()
            pw, ph = pop.winfo_width(), pop.winfo_height()
            if px <= event.x_root <= px + pw and py <= event.y_root <= py + ph:
                return
        except tk.TclError:
            pass
        self._dismiss_popup()

    def _show_popup(self, x, y, items, above=False, parent=None):
        """Кастомное меню без системной обводки (Toplevel + overrideredirect)."""
        self._dismiss_popup()
        if not items:
            return
        fam = getattr(self, "_ui_font_family", "Segoe UI")
        pop = tk.Toplevel(parent or self.root)
        pop.withdraw()
        pop.overrideredirect(True)
        pop.configure(bg=THEME["field"], highlightthickness=0, bd=0)
        try:
            pop.attributes("-topmost", True)
        except tk.TclError:
            pass
        self._active_popup = pop
        self._popup_images = []
        # пока False — игнорируем клик, которым открыли меню
        self._popup_open = False

        body = tk.Frame(pop, bg=THEME["field"], highlightthickness=0, bd=0)
        body.pack(fill="both", expand=True)

        def paint_row(row, active):
            bg = THEME["green_dark"] if active else THEME["field"]
            fg = "#ffffff" if active else THEME["fg"]
            row.configure(bg=bg)
            for c in row.winfo_children():
                try:
                    c.configure(bg=bg, fg=fg)
                except tk.TclError:
                    try:
                        c.configure(bg=bg)
                    except tk.TclError:
                        pass

        def make_row(item):
            if item.get("separator"):
                tk.Frame(body, bg=THEME["border"], height=1).pack(fill="x", padx=8, pady=4)
                return

            def on_click(e=None, cmd=item.get("command")):
                self._dismiss_popup()
                if cmd:
                    self.root.after(10, cmd)
                return "break"

            row = tk.Frame(body, bg=THEME["field"], cursor="hand2")
            row.pack(fill="x")
            img = item.get("image")
            if img is not None:
                self._popup_images.append(img)
                tk.Label(row, image=img, bg=THEME["field"], bd=0).pack(
                    side="left", padx=(10, 4), pady=7)
            tk.Label(
                row, text=item.get("label") or "", bg=THEME["field"], fg=THEME["fg"],
                font=(fam, 10), anchor="w",
            ).pack(side="left", fill="x", expand=True,
                   padx=(10 if img is None else 0, 16), pady=7)

            row.bind("<Enter>", lambda e, r=row: paint_row(r, True))
            row.bind("<Leave>", lambda e, r=row: paint_row(r, False))
            row.bind("<Button-1>", on_click)
            for c in row.winfo_children():
                c.bind("<Enter>", lambda e, r=row: paint_row(r, True))
                c.bind("<Leave>", lambda e, r=row: paint_row(r, False))
                c.bind("<Button-1>", on_click)

        for it in items:
            make_row(it)

        pop.update_idletasks()
        w = max(pop.winfo_reqwidth(), 160)
        h = pop.winfo_reqheight()
        if above:
            y = y - h
        sw, sh = pop.winfo_screenwidth(), pop.winfo_screenheight()
        x = max(0, min(x, sw - w - 4))
        y = max(0, min(y, sh - h - 4))
        pop.geometry(f"{w}x{h}+{x}+{y}")
        pop.deiconify()
        pop.lift()
        pop.bind("<Escape>", lambda e: self._dismiss_popup())
        # Без grab_set — он блокировал окно. Закрытие: клик снаружи / Esc / пункт меню.
        if not getattr(self, "_popup_outside_bound", False):
            self.root.bind_all("<Button-1>", self._popup_outside_click, add="+")
            self.root.bind_all("<Escape>", lambda e: self._dismiss_popup(), add="+")
            self._popup_outside_bound = True
        pop.after(150, lambda: setattr(self, "_popup_open", True) if getattr(self, "_active_popup", None) is pop else None)

    def _open_profile_popup(self):
        btn = self.profile_menu_button
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height() + 2
        self._show_popup(x, y, self._profile_popup_items, above=False)

    def _open_instance_popup(self):
        btn = self.instance_menu_button
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() - 2
        self._show_popup(x, y, self._instance_popup_items, above=True)

    def open_about(self):
        """Окно «О лаунчере»."""
        win = tk.Toplevel(self.root)
        win.title(t("about_title"))
        win.transient(self.root)
        win.resizable(False, False)
        theme_toplevel(win)
        win.grab_set()

        fam = getattr(self, "_ui_font_family", "Segoe UI")
        frm = tk.Frame(win, bg=THEME["bg"], padx=20, pady=18)
        frm.pack(fill="both", expand=True)

        head = tk.Frame(frm, bg=THEME["bg"])
        head.pack(fill="x", pady=(0, 12))

        icon_wrap = tk.Frame(
            head, width=72, height=72, bg=THEME["field"],
            highlightthickness=1, highlightbackground=THEME["green_dark"],
        )
        icon_wrap.pack(side="left", padx=(0, 14))
        icon_wrap.pack_propagate(False)
        icon_lbl = tk.Label(icon_wrap, bg=THEME["field"], bd=0)
        icon_lbl.place(relx=0.5, rely=0.5, anchor="center")
        photo = None
        for name in ("app_64.png", "app_48.png", "app.png", "app_32.png"):
            p = Path(getattr(self, "_icons_dir", Path())) / name
            if p.is_file():
                try:
                    photo = tk.PhotoImage(file=str(p))
                    break
                except tk.TclError:
                    pass
        if photo is not None:
            self._about_icon_photo = photo
            icon_lbl.configure(image=photo)

        titles = tk.Frame(head, bg=THEME["bg"])
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(
            titles, text=t("app_title"), bg=THEME["bg"], fg=THEME["green_bright"],
            font=(fam, 16, "bold"), anchor="w",
        ).pack(anchor="w")
        tk.Label(
            titles, text=t("about_version", version=LAUNCHER_VERSION),
            bg=THEME["bg"], fg=THEME["muted"],
            font=(fam, 10), anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        tk.Label(
            frm, text=t("about_body"), bg=THEME["bg"], fg=THEME["fg"],
            font=(fam, 10), justify="left", wraplength=380, anchor="w",
        ).pack(fill="x", pady=(0, 14))

        ui_button(frm, text=t("about_close"), command=win.destroy).pack(anchor="e")
        win.bind("<Escape>", lambda _e: win.destroy())

    def open_settings(self):
        """Настройки встроены в главное окно."""
        self.show_page("settings")


    def open_profile_manager(self):
        """Профили встроены в главное окно."""
        self.show_page("profiles")


    def select_profile(self, key):
        if key not in [p["key"] for p in profile_list(self.cfg)]:
            return
        self.cfg["active_profile"] = key
        migrate_profiles(self.cfg)  # синхронизирует старые ключи (nickname и т. п.)
        save_config(self.cfg)
        self.refresh_profile_ui()

    def on_profile_action(self):
        current = active_profile(self.cfg)
        if current is None:
            return
        if current["type"] == "ely":
            self.open_ely_profile()
        else:
            self.open_local_profile_settings()

    def open_ely_profile(self):
        try:
            opened = webbrowser.open(ELY_PROFILE_URL)
        except Exception:  # noqa: BLE001
            opened = False
        if not opened:
            self.status_var.set(t("browser_fail", url=ELY_PROFILE_URL))

    def _profile_name_dialog(self, title, initial=""):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        theme_toplevel(dialog)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=t("player_name_label")).pack(anchor="w", pady=(0, 8))
        value = tk.StringVar(value=initial)
        entry = ui_entry(frame, textvariable=value, width=34)
        entry.pack(fill="x")
        entry.focus_set()
        result = {"value": None}

        def accept(_event=None):
            name = value.get().strip()
            if not NICK_RE.fullmatch(name):
                messagebox.showwarning(t("invalid_name_title"), t("invalid_name_msg"),
                                       parent=dialog)
                return
            result["value"] = name
            dialog.destroy()

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(12, 0))
        ui_button(controls, text=t("create"), command=accept).pack(side="left")
        ui_button(controls, text=t("cancel"), command=dialog.destroy).pack(side="right")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.wait_window()
        return result["value"]

    def add_local_profile(self):
        name = self._profile_name_dialog(t("new_local_profile"))
        if not name:
            return
        profiles = self.cfg.setdefault("player_profiles", [])
        if name in profiles:
            messagebox.showwarning(t("profile_exists_title"), t("profile_exists_msg"),
                                   parent=self.root)
            return
        profiles.append(name)
        self.select_profile(profile_key("local", name))

    def open_local_profile_settings(self):
        """Настройка локального профиля: никнейм и скин."""
        p = active_profile(self.cfg)
        if not p or p["type"] != "local":
            return
        old_name = p["name"]
        win = tk.Toplevel(self.root)
        win.title(t("profile_setup_title"))
        win.transient(self.root)
        win.resizable(False, False)
        theme_toplevel(win)
        win.grab_set()
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=t("nickname_label")).pack(anchor="w")
        name_var = tk.StringVar(value=old_name)
        entry = ui_entry(frame, textvariable=name_var, width=40)
        entry.pack(fill="x", pady=(4, 12))
        entry.focus_set()

        ttk.Separator(frame).pack(fill="x", pady=(0, 10))
        ttk.Label(frame, text=t("skin"), font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text=t("skin_hint"),
            wraplength=430,
        ).pack(anchor="w", pady=(4, 8))

        skins = self.cfg.setdefault("profile_skins", {})

        def skin_text():
            current = skins.get(old_name)
            return t("skin_current", path=current) if current else t("skin_none")

        skin_status = tk.StringVar(value=skin_text())
        ttk.Label(frame, textvariable=skin_status, wraplength=430).pack(anchor="w", pady=(0, 8))

        def choose_skin():
            selected = filedialog.askopenfilename(
                parent=win, title=t("skin_choose_title"),
                filetypes=[(t("png_image"), "*.png")])
            if not selected:
                return
            try:
                width, height = png_size(selected)
                if (width, height) not in ((64, 64), (64, 32)):
                    messagebox.showwarning(
                        t("skin_size_title"),
                        t("skin_size_bad", width=width, height=height), parent=win)
                    return
                target = skin_path_for(old_name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(selected, target)
                skins[old_name] = str(target)
                save_config(self.cfg)
                skin_status.set(skin_text())
                self.refresh_profile_ui()
            except (OSError, ValueError, struct.error) as exc:
                messagebox.showerror(t("skin_load_fail"), str(exc), parent=win)

        def reset_skin():
            old = skins.pop(old_name, None)
            if old:
                try:
                    Path(old).unlink(missing_ok=True)
                except OSError:
                    pass
            save_config(self.cfg)
            skin_status.set(skin_text())
            self.refresh_profile_ui()

        skin_buttons = ttk.Frame(frame)
        skin_buttons.pack(fill="x")
        ui_button(skin_buttons, text=t("skin_choose_png"), command=choose_skin).pack(side="left")
        ui_button(skin_buttons, text=t("skin_reset"), command=reset_skin).pack(side="left", padx=8)

        ttk.Label(
            frame,
            text=t("skin_note"),
            wraplength=430,
        ).pack(anchor="w", pady=(12, 0))

        def save():
            new_name = name_var.get().strip()
            if not NICK_RE.fullmatch(new_name):
                messagebox.showwarning(t("invalid_nick_title"), t("invalid_name_msg"),
                                       parent=win)
                return
            if new_name != old_name:
                profiles = self.cfg["player_profiles"]
                if new_name in profiles:
                    messagebox.showwarning(t("profile_exists_title"), t("profile_exists_msg"), parent=win)
                    return
                profiles[profiles.index(old_name)] = new_name
                old_skin = skins.pop(old_name, None)
                if old_skin:  # скин переезжает вместе с профилем
                    target = skin_path_for(new_name)
                    try:
                        if Path(old_skin).exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(old_skin, target)
                            skins[new_name] = str(target)
                    except OSError as exc:
                        skins[new_name] = old_skin
                        messagebox.showwarning(t("skin"), t("skin_rename_fail", error=exc), parent=win)
                self.cfg["active_profile"] = profile_key("local", new_name)
                migrate_profiles(self.cfg)
            save_config(self.cfg)
            self.refresh_profile_ui()
            win.destroy()

        ttk.Separator(frame).pack(fill="x", pady=12)
        bottom = ttk.Frame(frame)
        bottom.pack(fill="x")
        ui_button(bottom, text=t("save"), command=save).pack(side="left")

        def delete_local_profile():
            if not messagebox.askyesno(
                    t("profile_delete_title"), t("profile_delete_local", name=old_name),
                    parent=win):
                return
            skin = remove_profile(self.cfg, profile_key("local", old_name))
            if skin:
                try:
                    Path(skin).unlink(missing_ok=True)
                except OSError:
                    pass
            save_config(self.cfg)
            self.refresh_profile_ui()
            win.destroy()

        ui_button(bottom, text=t("profile_delete_btn"), command=delete_local_profile).pack(side="left", padx=(8, 0))
        ui_button(bottom, text=t("cancel"), command=win.destroy).pack(side="right")

    def delete_profile(self):
        current = active_profile(self.cfg)
        if current is None or current["type"] != "ely":
            return
        what = t("ely_account_word")
        if not messagebox.askyesno(
                t("profile_delete_title"), t("profile_delete_ely", name=current["name"]), parent=self.root):
            return
        account = dict(self.cfg.get("ely_accounts", {}).get(current["name"]) or {})
        if account.get("access_token"):
            threading.Thread(target=ely_invalidate, args=(account,), daemon=True).start()
        skin = remove_profile(self.cfg, current["key"])
        if skin:
            try:
                Path(skin).unlink(missing_ok=True)
            except OSError:
                pass
        save_config(self.cfg)
        self.refresh_profile_ui()

    # --- Ely.by ---------------------------------------------------------

    def add_ely_account(self, prefill_login=""):
        """Вход в Ely.by по нику/e-mail и паролю (как в Legacy Launcher)."""
        if self.ely_login_running:
            self.status_var.set(t("ely_login_busy"))
            return
        win = tk.Toplevel(self.root)
        win.title(t("ely_login_title"))
        win.transient(self.root)
        win.resizable(False, False)
        theme_toplevel(win)
        win.grab_set()
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=t("ely_login_user")).pack(anchor="w")
        login_var = tk.StringVar(value=prefill_login)
        login_entry = ui_entry(frame, textvariable=login_var, width=40)
        login_entry.pack(fill="x", pady=(4, 10))

        ttk.Label(frame, text=t("ely_login_password")).pack(anchor="w")
        pass_var = tk.StringVar()
        pass_entry = ui_entry(frame, textvariable=pass_var, show="•", width=40)
        pass_entry.pack(fill="x", pady=(4, 10))

        totp_box = ttk.Frame(frame)
        ttk.Label(totp_box, text=t("ely_login_totp")).pack(anchor="w")
        totp_var = tk.StringVar()
        totp_entry = ui_entry(totp_box, textvariable=totp_var, width=40)
        totp_entry.pack(fill="x", pady=(4, 10))

        ttk.Label(
            frame, wraplength=360,
            text=t("ely_login_password_note")
        ).pack(anchor="w", pady=(0, 8))
        msg_var = tk.StringVar()
        ttk.Label(frame, textvariable=msg_var, wraplength=360, foreground="#d9534f").pack(anchor="w")

        state = {"busy": False, "needs_2fa": False}
        box = {}

        def poll():
            if "done" not in box:
                win.after(100, poll)
                return
            state["busy"] = False
            self.ely_login_running = False
            login_btn.configure(state="normal")
            done = box.pop("done")
            if isinstance(done, dict):
                pass_var.set("")
                win.destroy()
                self._apply_ely_account(done)
                return
            if getattr(done, "needs_2fa", False):
                if not state["needs_2fa"]:
                    state["needs_2fa"] = True
                    totp_box.pack(fill="x", before=login_btn_row)
                    msg_var.set(t("ely_2fa_required"))
                else:
                    msg_var.set(t("ely_2fa_bad"))
                totp_entry.focus_set()
            else:
                msg_var.set(str(done))

        def submit(_event=None):
            if state["busy"]:
                return
            login, password, totp = login_var.get().strip(), pass_var.get(), totp_var.get().strip()
            if not login or not password:
                msg_var.set(t("ely_need_credentials"))
                return
            if state["needs_2fa"] and not totp:
                msg_var.set(t("ely_need_totp"))
                return
            msg_var.set("")
            state["busy"] = True
            self.ely_login_running = True
            login_btn.configure(state="disabled")
            self.status_var.set(t("ely_logging_in"))

            def work():
                try:
                    box["done"] = ely_authenticate(login, password, totp if state["needs_2fa"] else "")
                except Exception as exc:  # noqa: BLE001
                    box["done"] = exc if isinstance(exc, LauncherError) else LauncherError(str(exc))

            threading.Thread(target=work, daemon=True).start()
            poll()

        login_btn_row = ttk.Frame(frame)
        login_btn_row.pack(fill="x", pady=(12, 0))
        login_btn = ui_button(login_btn_row, text=t("ely_login_btn"), command=submit)
        login_btn.pack(side="left")
        ui_button(login_btn_row, text=t("cancel"), command=win.destroy).pack(side="right")
        win.bind("<Return>", submit)
        win.bind("<Escape>", lambda _e: win.destroy())
        (pass_entry if prefill_login else login_entry).focus_set()
        win.wait_window()

    def _apply_ely_account(self, account):
        username = account["username"]
        self.cfg.setdefault("ely_accounts", {})[username] = account
        self.cfg["active_profile"] = profile_key("ely", username)
        migrate_profiles(self.cfg)
        save_config(self.cfg)
        self.refresh_profile_ui()
        manager_refresh = getattr(self, "profile_manager_refresh", None)
        if callable(manager_refresh):
            manager_refresh()
        self.status_var.set(t("ely_connected", username=username))
        messagebox.showinfo("Ely.by", t("ely_added", username=username), parent=self.root)

    # ======================================================================
    # Каталог модов Modrinth
    # ======================================================================


    def open_content_manager(self, inst_override=None, parent=None):
        """Установленный контент: моды / шейдеры / ресурспаки."""
        inst = inst_override or self.current_instance()
        if not inst:
            messagebox.showinfo(t("content"), t("content_need_instance"), parent=self.root)
            return

        win = tk.Toplevel(parent or self.root)
        win.title(t("content_title", name=inst.get("name") or t("instance_label_fallback")))
        win.geometry("720x520")
        win.minsize(560, 400)
        win.transient(parent or self.root)
        theme_toplevel(win)

        fam = getattr(self, "_ui_font_family", "Segoe UI")
        type_labels = {"mod": t("content_type_mod"), "shader": t("content_type_shader"), "resourcepack": t("content_type_resourcepack")}
        type_folders = {"mod": "mods", "shader": "shaderpacks", "resourcepack": "resourcepacks"}
        type_var = tk.StringVar(value=t("content_type_mod"))

        def type_key():
            inv = {v: k for k, v in type_labels.items()}
            return inv.get(type_var.get(), "mod")

        def folder():
            return instance_path(inst) / type_folders[type_key()]

        def index_path():
            return instance_path(inst) / ".mylauncher_content.json"

        def load_index():
            p = index_path()
            if p.is_file():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    return {}
            return {}

        def save_index(data):
            try:
                index_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass

        def find_meta(idx, base, name):
            """Найти метаданные по ключу или имени файла (Modrinth-имя сохраняется при установке)."""
            folder_name = type_folders[type_key()]
            for k in (f"{folder_name}/{base}", f"{folder_name}/{name}", f"{folder_name}/{base}.disabled"):
                if k in idx:
                    return idx[k]
            for meta in idx.values():
                if not isinstance(meta, dict):
                    continue
                fn = (meta.get("filename") or "").lower()
                if fn == base.lower() or fn == name.lower():
                    return meta
            return {}

        def format_source(meta):
            src = (meta.get("source") or "").strip().lower()
            if src == "modrinth":
                return "Modrinth"
            if src in ("файл", "file", "local"):
                return t("content_source_file")
            if src:
                return meta.get("source")
            return t("content_source_file")

        def scan_entries():
            """Список записей из папки + индекс (имя как на Modrinth, если ставили оттуда)."""
            idx = load_index()
            d = folder()
            entries = []
            if not d.is_dir():
                return entries
            for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
                if not p.is_file():
                    continue
                name = p.name
                enabled = True
                base = name
                if name.endswith(".disabled"):
                    enabled = False
                    base = name[: -len(".disabled")]
                if not base.lower().endswith((".jar", ".zip")):
                    continue
                key = f"{type_folders[type_key()]}/{base}"
                meta = find_meta(idx, base, name)
                display_name = (meta.get("name") or "").strip() or Path(base).stem
                entries.append({
                    "path": p,
                    "filename": name,
                    "base": base,
                    "enabled": enabled,
                    "name": display_name,
                    "version": meta.get("version") or "—",
                    "source": format_source(meta),
                    "project_id": meta.get("project_id") or "",
                    "depends_on": meta.get("depends_on") or [],
                    "icon_url": meta.get("icon_url") or "",
                    "type": type_key(),
                    "key": key,
                })
            return entries

        rootf = tk.Frame(win, bg=THEME["bg"])
        rootf.pack(fill="both", expand=True, padx=14, pady=12)
        rootf.columnconfigure(0, weight=1)
        rootf.rowconfigure(1, weight=1)

        top = tk.Frame(rootf, bg=THEME["bg"])
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ui_combobox(top, textvariable=type_var, state="readonly", width=14,
                     values=list(type_labels.values())).pack(side="left")
        ui_button(
            top, text=t("content_from_modrinth"),
            command=lambda: self.open_modrinth_catalog(inst, initial_type=type_key(), parent=win),
            accent=True).pack(side="left", padx=(10, 6))
        ui_button(top, text=t("content_from_file"), command=lambda: install_from_file()).pack(side="left")

        list_wrap = tk.Frame(
            rootf, bg=THEME["panel"],
            highlightthickness=1, highlightbackground=THEME["green_dark"],
        )
        list_wrap.grid(row=1, column=0, sticky="nsew")
        list_wrap.columnconfigure(0, weight=1)
        list_wrap.rowconfigure(0, weight=1)

        list_canvas = tk.Canvas(list_wrap, bg=THEME["panel"], highlightthickness=0, bd=0)
        list_canvas.grid(row=0, column=0, sticky="nsew")
        list_scroll = self._make_thin_scrollbar(list_wrap, list_canvas)
        list_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 2), pady=4)
        list_scroll.grid_remove()
        list_inner = tk.Frame(list_canvas, bg=THEME["panel"])
        list_window = list_canvas.create_window((0, 0), window=list_inner, anchor="nw")

        def _on_list_cfg(_e=None):
            list_canvas.configure(scrollregion=list_canvas.bbox("all"))
            list_canvas.itemconfigure(list_window, width=list_canvas.winfo_width())

        list_inner.bind("<Configure>", _on_list_cfg)
        list_canvas.bind("<Configure>", lambda e: list_canvas.itemconfigure(list_window, width=e.width))

        def _wheel(event):
            delta = 0
            if getattr(event, "delta", 0):
                delta = -1 if event.delta > 0 else 1
            elif getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            if delta:
                list_canvas.yview_scroll(delta, "units")
                return "break"

        list_canvas.bind("<MouseWheel>", _wheel)
        list_canvas.bind("<Button-4>", _wheel)
        list_canvas.bind("<Button-5>", _wheel)
        list_inner.bind("<MouseWheel>", _wheel)
        list_inner.bind("<Button-4>", _wheel)
        list_inner.bind("<Button-5>", _wheel)

        entries_map = {}
        row_widgets = []
        icon_cache = {}  # key -> PhotoImage (только из главного потока)
        status_var = tk.StringVar(value="")

        def cache_icon_png(url, size=40):
            """Скачать/конвертировать иконку в PNG на диск. Безопасно из любого потока.
            Возвращает Path или None."""
            if not url:
                return None
            try:
                cache_dir = GAME_DIR / "cache" / "modrinth_icons"
                cache_dir.mkdir(parents=True, exist_ok=True)
                h = hashlib.sha1(f"{url}|{size}".encode()).hexdigest()[:22]
                png_path = cache_dir / (h + ".png")
                if png_path.is_file() and png_path.stat().st_size > 0:
                    return png_path
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
                if not data:
                    return None
                try:
                    from PIL import Image
                    import io as _io
                    im = Image.open(_io.BytesIO(data)).convert("RGBA")
                    try:
                        resample = Image.Resampling.LANCZOS
                    except AttributeError:
                        resample = Image.LANCZOS
                    im.thumbnail((size, size), resample)
                    im.save(png_path, format="PNG")
                    return png_path
                except Exception:
                    if data[:8] == b"\x89PNG\r\n\x1a\n":
                        png_path.write_bytes(data)
                        return png_path
                    return None
            except Exception:
                return None

        def photo_from_png(png_path, size=40):
            """Создать PhotoImage — только из главного потока Tk."""
            key = f"{png_path}|{size}"
            if key in icon_cache:
                return icon_cache[key]
            try:
                img = tk.PhotoImage(file=str(png_path))
                # ужать, если файл больше запрошенного (сырой PNG без PIL)
                guard = 0
                while (img.width() > size + 4 or img.height() > size + 4) and guard < 4:
                    img = img.subsample(2, 2)
                    guard += 1
                icon_cache[key] = img
                return img
            except Exception:
                icon_cache[key] = None
                return None

        def load_row_icon(url, size=40):
            """Главный поток: путь из кэша → PhotoImage. Для уже скачанных файлов."""
            if not url:
                return None
            mem_key = f"{url}|{size}"
            if mem_key in icon_cache:
                return icon_cache[mem_key]
            path = cache_icon_png(url, size)
            if not path:
                icon_cache[mem_key] = None
                return None
            img = photo_from_png(path, size)
            icon_cache[mem_key] = img
            return img

        project_icon_cache = {}  # project_id/slug -> icon_url

        def fetch_modrinth_json(url):
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))

        def resolve_icon_url(entry):
            """Реальный icon_url с API Modrinth (угаданный CDN /icon.png часто 404)."""
            url = (entry.get("icon_url") or "").strip()
            # отбрасываем старый нерабочий шаблон
            if url.endswith("/icon.png") and "cdn.modrinth.com/data/" in url:
                url = ""
            if url:
                return url
            keys = []
            pid = (entry.get("project_id") or "").strip()
            if pid:
                keys.append(pid)
            # slug из имени файла: sodium-fabric-0.5.8.jar → пробуем целиком и по частям
            base = Path(entry.get("base") or entry.get("filename") or "").stem
            if base:
                keys.append(base)
                # sodium-fabric-0.5.8 → sodium-fabric, sodium
                parts = base.replace("_", "-").split("-")
                if len(parts) >= 2:
                    keys.append(parts[0])
                    keys.append("-".join(parts[:2]))
            name = (entry.get("name") or "").strip()
            for key in keys:
                if not key:
                    continue
                if key in project_icon_cache:
                    if project_icon_cache[key]:
                        return project_icon_cache[key]
                    continue
                try:
                    data = fetch_modrinth_json(f"{MODRINTH_API}/project/{urllib.parse.quote(key)}")
                    icon = (data.get("icon_url") or "").strip()
                    project_icon_cache[key] = icon or None
                    if data.get("id"):
                        project_icon_cache[data["id"]] = icon or None
                    if data.get("slug"):
                        project_icon_cache[data["slug"]] = icon or None
                    if icon:
                        # сохранить в индекс для следующих открытий
                        try:
                            idx = load_index()
                            k = entry.get("key")
                            if k and k in idx:
                                idx[k]["icon_url"] = icon
                                if data.get("id") and not idx[k].get("project_id"):
                                    idx[k]["project_id"] = data["id"]
                                if data.get("title") and (
                                    not idx[k].get("name")
                                    or idx[k].get("name") == Path(entry.get("base") or "").stem
                                ):
                                    idx[k]["name"] = data["title"]
                                save_index(idx)
                        except Exception:
                            pass
                        return icon
                except Exception:
                    project_icon_cache[key] = None
            # поиск по названию — последний шанс
            if name and len(name) >= 3:
                qkey = "q:" + name.lower()
                if qkey not in project_icon_cache:
                    try:
                        params = urllib.parse.urlencode({
                            "query": name,
                            "limit": 5,
                            "index": "relevance",
                        })
                        data = fetch_modrinth_json(f"{MODRINTH_API}/search?{params}")
                        icon = None
                        for hit in data.get("hits") or []:
                            title = (hit.get("title") or "").strip().lower()
                            if title == name.lower() or name.lower() in title or title in name.lower():
                                icon = (hit.get("icon_url") or "").strip()
                                if hit.get("project_id"):
                                    project_icon_cache[hit["project_id"]] = icon or None
                                break
                        if not icon and data.get("hits"):
                            icon = (data["hits"][0].get("icon_url") or "").strip()
                        project_icon_cache[qkey] = icon or None
                        if icon:
                            try:
                                idx = load_index()
                                k = entry.get("key")
                                if k and k in idx:
                                    idx[k]["icon_url"] = icon
                                    save_index(idx)
                            except Exception:
                                pass
                            return icon
                    except Exception:
                        project_icon_cache[qkey] = None
                elif project_icon_cache.get(qkey):
                    return project_icon_cache[qkey]
            return None

        def clear_rows():
            for w in row_widgets:
                try:
                    w.destroy()
                except tk.TclError:
                    pass
            row_widgets.clear()
            entries_map.clear()

        def on_check(entry, var):
            want = bool(var.get())
            if want == entry["enabled"]:
                return
            try:
                toggle_enable(entry, want)
            except Exception as exc:
                messagebox.showerror(t("error"), str(exc), parent=win)
                var.set(1 if entry["enabled"] else 0)

        def add_entry_row(entry):
            row = tk.Frame(list_inner, bg=THEME["panel"], cursor="hand2")
            row.pack(fill="x", padx=2, pady=1)
            row_widgets.append(row)
            entries_map[entry["filename"]] = entry
            row._entry = entry

            var = tk.IntVar(value=1 if entry["enabled"] else 0)
            row._var = var
            cb = tk.Checkbutton(
                row, variable=var, bg=THEME["panel"], activebackground=THEME["panel"],
                selectcolor=THEME["field"], fg=THEME["fg"],
                highlightthickness=0, bd=0,
                command=lambda e=entry, v=var: on_check(e, v),
            )
            cb.pack(side="left", padx=(6, 2))

            icon_wrap = tk.Frame(row, bg=THEME["panel"], width=40, height=40)
            icon_wrap.pack(side="left", padx=(2, 8), pady=6)
            icon_wrap.pack_propagate(False)
            icon_lbl = tk.Label(icon_wrap, bg=THEME["panel"], bd=0)
            icon_lbl.place(relx=0.5, rely=0.5, anchor="center")

            text_col = tk.Frame(row, bg=THEME["panel"])
            text_col.pack(side="left", fill="x", expand=True, pady=4)

            title_fg = THEME["fg"] if entry["enabled"] else THEME["muted"]
            name = entry["name"]
            if not entry["enabled"]:
                name = name + t("content_disabled_suffix")
            tk.Label(
                text_col, text=name, bg=THEME["panel"], fg=title_fg,
                font=(fam, 10, "bold"), anchor="w",
            ).pack(fill="x")
            sub = f"{entry['version']}  ·  {entry['source']}"
            tk.Label(
                text_col, text=sub, bg=THEME["panel"], fg=THEME["muted"],
                font=(fam, 9), anchor="w",
            ).pack(fill="x")

            def on_rc(event, e=entry):
                items = []
                if e["enabled"]:
                    items.append({"label": t("content_disable"), "command": lambda: toggle_enable(e, False)})
                else:
                    items.append({"label": t("content_enable"), "command": lambda: toggle_enable(e, True)})
                items.append({"label": t("content_delete"), "command": lambda: delete_entry(e)})
                x, y = event.x_root, event.y_root
                win.after(10, lambda: self._show_popup(x, y, items, parent=win))
                return "break"

            for w in (row, text_col, icon_wrap, icon_lbl):
                w.bind("<Button-3>", on_rc)
                w.bind("<ButtonRelease-3>", on_rc)
                w.bind("<Control-Button-1>", on_rc)
                w.bind("<MouseWheel>", _wheel)
                w.bind("<Button-4>", _wheel)
                w.bind("<Button-5>", _wheel)
            for c in text_col.winfo_children():
                c.bind("<Button-3>", on_rc)
                c.bind("<ButtonRelease-3>", on_rc)
                c.bind("<MouseWheel>", _wheel)

            def fetch_icon(e=entry, lbl=icon_lbl):
                # сеть и диск — в фоне; PhotoImage — только в главном потоке
                url = resolve_icon_url(e)
                if not url:
                    return
                png_path = cache_icon_png(url, 40)
                if not png_path:
                    return

                def apply(path=png_path, lab=lbl, u=url):
                    try:
                        img = photo_from_png(path, 40)
                        if img is None:
                            return
                        icon_cache[f"{u}|40"] = img
                        lab.configure(image=img)
                        lab.image = img
                    except tk.TclError:
                        pass

                win.after(0, apply)

            threading.Thread(target=fetch_icon, daemon=True).start()

        def refresh():
            clear_rows()
            entries = scan_entries()
            for e in entries:
                add_entry_row(e)
            n_on = sum(1 for e in entries if e["enabled"])
            status_var.set(t("content_status", total=len(entries), enabled=n_on))
            list_canvas.yview_moveto(0)
            _on_list_cfg()

        def install_from_file():
            paths = filedialog.askopenfilenames(
                parent=win,
                title=t("content_choose_files"),
                filetypes=[
                    (t("files_mods_archives"), "*.jar;*.zip"),
                    ("JAR", "*.jar"),
                    ("ZIP", "*.zip"),
                    (t("all_files"), "*.*"),
                ],
            )
            if not paths:
                return
            dest = folder()
            dest.mkdir(parents=True, exist_ok=True)
            idx = load_index()
            for src in paths:
                src = Path(src)
                target = dest / src.name
                try:
                    shutil.copy2(src, target)
                    key = f"{type_folders[type_key()]}/{src.name}"
                    idx[key] = {
                        "name": src.stem,
                        "version": "—",
                        "source": "file",
                        "project_id": "",
                        "depends_on": [],
                        "enabled": True,
                        "type": type_key(),
                        "filename": src.name,
                    }
                except OSError as exc:
                    messagebox.showerror(t("error"), t("file_error", name=src.name, error=exc), parent=win)
            save_index(idx)
            refresh()

        def dependents_of(project_id, entries):
            if not project_id:
                return []
            return [e for e in entries if project_id in (e.get("depends_on") or [])]

        def toggle_enable(entry, enable):
            p = entry["path"]
            dest = folder()
            if enable and not entry["enabled"]:
                if p.name.endswith(".disabled"):
                    newp = dest / entry["base"]
                    p.rename(newp)
                    entry["path"] = newp
                    entry["filename"] = newp.name
            elif not enable and entry["enabled"]:
                newp = dest / (entry["base"] + ".disabled")
                p.rename(newp)
                entry["path"] = newp
                entry["filename"] = newp.name
            entry["enabled"] = enable
            idx = load_index()
            key = entry["key"]
            if key in idx:
                idx[key]["enabled"] = enable
                save_index(idx)
            refresh()

        def delete_entry(entry):
            entries = list(entries_map.values())
            deps = dependents_of(entry.get("project_id"), entries)
            deps = [d for d in deps if d["enabled"] and d["filename"] != entry["filename"]]
            if deps and entry.get("project_id"):
                dlg = tk.Toplevel(win)
                dlg.title(t("dep_dialog_title"))
                dlg.transient(win)
                dlg.grab_set()
                theme_toplevel(dlg)
                frm = tk.Frame(dlg, bg=THEME["bg"], padx=14, pady=12)
                frm.pack(fill="both", expand=True)
                names = ", ".join(d["name"] for d in deps[:8])
                if len(deps) > 8:
                    names += "…"
                tk.Label(
                    frm,
                    text=t("dep_dialog_body", name=entry["name"], names=names),
                    bg=THEME["bg"], fg=THEME["fg"], justify="left", wraplength=400,
                ).pack(anchor="w")
                choice = {"v": None}

                def pick(v):
                    choice["v"] = v
                    dlg.destroy()

                bf = tk.Frame(frm, bg=THEME["bg"])
                bf.pack(fill="x", pady=(12, 0))
                ui_button(bf, text=t("dep_disable_all"), command=lambda: pick("all")).pack(fill="x", pady=2)
                ui_button(bf, text=t("dep_disable_one"), command=lambda: pick("one")).pack(fill="x", pady=2)
                ui_button(bf, text=t("dep_delete_one"), command=lambda: pick("delete")).pack(fill="x", pady=2)
                ui_button(bf, text=t("cancel"), command=lambda: pick(None)).pack(fill="x", pady=2)
                dlg.wait_window()
                if choice["v"] is None:
                    return
                if choice["v"] == "all":
                    for e in [entry] + deps:
                        try:
                            toggle_enable(e, False)
                        except OSError as exc:
                            messagebox.showerror(t("error"), str(exc), parent=win)
                    return
                if choice["v"] == "one":
                    try:
                        toggle_enable(entry, False)
                    except OSError as exc:
                        messagebox.showerror(t("error"), str(exc), parent=win)
                    return
            else:
                if not messagebox.askyesno(
                        t("content_delete_title"),
                        t("content_delete_confirm", name=entry["name"], filename=entry["filename"]),
                        parent=win):
                    return
            try:
                entry["path"].unlink(missing_ok=True)
                alt = folder() / (entry["base"] + ".disabled")
                if alt.exists():
                    alt.unlink(missing_ok=True)
                idx = load_index()
                idx.pop(entry["key"], None)
                save_index(idx)
            except OSError as exc:
                messagebox.showerror(t("error"), str(exc), parent=win)
            refresh()

        status_bar = tk.Frame(rootf, bg=THEME["bg"])
        status_bar.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        tk.Label(
            status_bar, textvariable=status_var, bg=THEME["bg"], fg=THEME["muted"],
            font=(fam, 9), anchor="w",
        ).pack(side="left")

        type_var.trace_add("write", lambda *_: win.after_idle(refresh))
        self._content_manager_refresh = refresh

        def on_close():
            if getattr(self, "_content_manager_refresh", None) is refresh:
                self._content_manager_refresh = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        refresh()


    def open_modrinth_catalog(self, inst_override=None, initial_type="mod", parent=None):
        """Каталог Modrinth: список с иконками, мультивыбор, зависимости, метка установленных."""
        inst = inst_override or self.current_instance()
        if not inst:
            messagebox.showinfo(t("content"), t("content_need_instance"), parent=self.root)
            return

        win = tk.Toplevel(parent or self.root)
        win.title(t("modrinth_window_title"))
        win.geometry("1100x720")
        win.minsize(900, 560)
        win.transient(parent or self.root)
        theme_toplevel(win)

        fam = getattr(self, "_ui_font_family", "Segoe UI")
        loader = inst.get("kind")  # fabric / forge / … или None
        type_labels = {"mod": t("content_type_mod"), "shader": t("content_type_shader"), "resourcepack": t("content_type_resourcepack")}
        type_folders = {"mod": "mods", "shader": "shaderpacks", "resourcepack": "resourcepacks"}
        type_ext = {"mod": (".jar",), "shader": (".zip",), "resourcepack": (".zip",)}

        rootf = tk.Frame(win, bg=THEME["bg"])
        rootf.pack(fill="both", expand=True, padx=14, pady=12)
        rootf.columnconfigure(0, weight=2)
        rootf.columnconfigure(1, weight=3)
        rootf.rowconfigure(1, weight=1)

        # ── Верх: тип + поиск ──
        top = tk.Frame(rootf, bg=THEME["bg"])
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        type_var = tk.StringVar(value=type_labels.get(initial_type, t("content_type_mod")))
        type_box = ui_combobox(
            top, textvariable=type_var, state="readonly", width=14,
            values=list(type_labels.values()),
        )
        type_box.pack(side="left")
        type_key_by_label = {v: k for k, v in type_labels.items()}
        query = tk.StringVar()
        entry = ui_entry(top, textvariable=query, width=36)
        entry.pack(side="left", padx=(10, 8), fill="x", expand=True)
        ui_button(top, text="🔍", width=3, command=lambda: do_search(reset=True)).pack(side="left")

        def current_type():
            return type_key_by_label.get(type_var.get(), "mod")

        def content_dir():
            return instance_path(inst) / type_folders[current_type()]

        def scan_installed():
            """Имена уже лежащих в папке файлов (lowercase)."""
            d = content_dir()
            names = set()
            if d.is_dir():
                for p in d.iterdir():
                    if p.is_file():
                        names.add(p.name.lower())
            return names

        installed_files = {"set": scan_installed()}

        # ── Левый список (Canvas) ──
        left = tk.Frame(rootf, bg=THEME["panel"], highlightthickness=1,
                        highlightbackground=THEME["green_dark"])
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        list_canvas = tk.Canvas(left, bg=THEME["panel"], highlightthickness=0, bd=0)
        list_canvas.grid(row=0, column=0, sticky="nsew")
        list_scroll = self._make_thin_scrollbar(left, list_canvas)
        list_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 2), pady=4)
        list_scroll.grid_remove()

        # Пагинация
        pager = tk.Frame(left, bg=THEME["panel"])
        pager.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        pager.columnconfigure(1, weight=1)
        page_label = tk.StringVar(value=t("modrinth_page", page=1, pages=1))
        btn_prev = ui_button(pager, text="◀", width=3, command=lambda: go_page(-1))
        btn_prev.grid(row=0, column=0, padx=(0, 4))
        tk.Label(
            pager, textvariable=page_label, bg=THEME["panel"], fg=THEME["fg"],
            font=(fam, 9), anchor="center",
        ).grid(row=0, column=1, sticky="ew")
        btn_next = ui_button(pager, text="▶", width=3, command=lambda: go_page(1))
        btn_next.grid(row=0, column=2, padx=(4, 0))
        list_inner = tk.Frame(list_canvas, bg=THEME["panel"])
        list_window = list_canvas.create_window((0, 0), window=list_inner, anchor="nw")

        def _on_list_configure(_e=None):
            list_canvas.configure(scrollregion=list_canvas.bbox("all"))
            list_canvas.itemconfigure(list_window, width=list_canvas.winfo_width())

        list_inner.bind("<Configure>", _on_list_configure)
        list_canvas.bind("<Configure>", lambda e: list_canvas.itemconfigure(list_window, width=e.width))

        def _wheel(event):
            delta = 0
            if getattr(event, "delta", 0):
                delta = -1 if event.delta > 0 else 1
            elif getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            if delta:
                list_canvas.yview_scroll(delta, "units")
                return "break"

        list_canvas.bind("<MouseWheel>", _wheel)
        list_canvas.bind("<Button-4>", _wheel)
        list_canvas.bind("<Button-5>", _wheel)
        list_inner.bind("<MouseWheel>", _wheel)
        list_inner.bind("<Button-4>", _wheel)
        list_inner.bind("<Button-5>", _wheel)

        # состояние списка
        projects = []  # dicts from search
        selected_idx = {"i": -1}
        checked = set()  # project_id for multi-install
        checked_meta = {}  # project_id -> {title, project_id, slug} — не зависит от текущей страницы
        icon_cache = {}  # url -> PhotoImage
        row_widgets = []
        PAGE_SIZE = 20
        page_state = {"page": 0, "total": 0, "pages": 1, "query": None, "ptype": None}

        # ── Правая панель ──
        right = tk.Frame(rootf, bg=THEME["panel"], highlightthickness=1,
                         highlightbackground=THEME["green_dark"])
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        detail_head = tk.Frame(right, bg=THEME["panel"])
        detail_head.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        detail_head.columnconfigure(1, weight=1)
        # width/height у Label в символах, не в пикселях — фиксируем рамку 64×64
        icon_wrap = tk.Frame(
            detail_head, width=64, height=64, bg=THEME["field"],
            highlightthickness=1, highlightbackground=THEME["border"],
        )
        icon_wrap.grid(row=0, column=0, rowspan=2, padx=(0, 12), sticky="nw")
        icon_wrap.grid_propagate(False)
        d_icon = tk.Label(icon_wrap, bg=THEME["field"], bd=0)
        d_icon.place(relx=0.5, rely=0.5, anchor="center")
        d_title = tk.StringVar(value=t("modrinth_select_left"))
        d_author = tk.StringVar(value="")
        tk.Label(
            detail_head, textvariable=d_title, bg=THEME["panel"], fg=THEME["fg"],
            font=(fam, 13, "bold"), anchor="w", justify="left",
        ).grid(row=0, column=1, sticky="ew")
        tk.Label(
            detail_head, textvariable=d_author, bg=THEME["panel"], fg=THEME["muted"],
            font=(fam, 10), anchor="w", justify="left",
        ).grid(row=1, column=1, sticky="ew")

        desc = tk.Text(
            right, wrap="word", state="disabled",
            bg=THEME["panel"], fg=THEME["log_fg"],
            insertbackground=THEME["green"], font=(fam, 10),
            relief="flat", borderwidth=0, padx=12, pady=4,
            highlightthickness=0,
        )
        desc.grid(row=1, column=0, sticky="nsew")

        ver_row = tk.Frame(right, bg=THEME["panel"])
        ver_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(4, 8))
        ver_row.columnconfigure(1, weight=1)
        tk.Label(ver_row, text=t("modrinth_version_label"), bg=THEME["panel"], fg=THEME["muted"],
                 font=(fam, 9)).grid(row=0, column=0, sticky="w", padx=(0, 8))
        version_var = tk.StringVar()
        version_box = ui_combobox(ver_row, textvariable=version_var, state="readonly")
        version_box.grid(row=0, column=1, sticky="ew")
        versions_for_project = []
        selected_project = [None]
        detail_icon_ref = {"img": None}

        # ── Низ: Установить / Modrinth ──
        bottom = tk.Frame(rootf, bg=THEME["bg"])
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(1, weight=1)
        status = tk.StringVar(value="")
        tk.Label(bottom, textvariable=status, bg=THEME["bg"], fg=THEME["muted"],
                 font=(fam, 9), anchor="w").grid(row=0, column=1, sticky="w", padx=10)
        install_btn = ui_button(bottom, text=t("modrinth_install"),
                                 command=lambda: install_checked(), accent=True)
        install_btn.grid(row=0, column=0, sticky="w")
        ui_button(bottom, text="Modrinth ↗", command=lambda: open_modrinth_page()).grid(
            row=0, column=2, sticky="e")

        # ── API helpers ──
        def api_json(url):
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))

        def cache_modrinth_icon(url, size=40):
            """Скачать/конвертировать иконку в PNG. Безопасно из любого потока → Path|None."""
            if not url:
                return None
            try:
                cache_dir = GAME_DIR / "cache" / "modrinth_icons"
                cache_dir.mkdir(parents=True, exist_ok=True)
                key = hashlib.sha1(f"{url}|{size}".encode()).hexdigest()[:22]
                png_path = cache_dir / (key + ".png")
                if png_path.is_file() and png_path.stat().st_size > 0:
                    return png_path
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = resp.read()
                if not data:
                    return None
                try:
                    from PIL import Image
                    import io as _io
                    im = Image.open(_io.BytesIO(data)).convert("RGBA")
                    try:
                        resample = Image.Resampling.LANCZOS
                    except AttributeError:
                        resample = Image.LANCZOS
                    im.thumbnail((size, size), resample)
                    im.save(png_path, format="PNG")
                    return png_path
                except Exception:
                    if data[:8] == b"\x89PNG\r\n\x1a\n":
                        png_path.write_bytes(data)
                        return png_path
                    return None
            except Exception:
                return None

        def photo_from_path(png_path, size=40):
            """PhotoImage только из главного потока."""
            cache_key = f"{png_path}|{size}"
            if cache_key in icon_cache:
                return icon_cache[cache_key]
            try:
                img = tk.PhotoImage(file=str(png_path))
                guard = 0
                while (img.width() > size + 4 or img.height() > size + 4) and guard < 4:
                    img = img.subsample(2, 2)
                    guard += 1
                icon_cache[cache_key] = img
                return img
            except Exception:
                icon_cache[cache_key] = None
                return None

        def load_icon(url, size=40):
            """Главный поток: кэш → PhotoImage."""
            if not url:
                return None
            mem_key = f"{url}|{size}"
            if mem_key in icon_cache:
                return icon_cache[mem_key]
            path = cache_modrinth_icon(url, size)
            if not path:
                icon_cache[mem_key] = None
                return None
            img = photo_from_path(path, size)
            icon_cache[mem_key] = img
            return img

        def is_installed_project(proj, version_list=None):
            """True, если любой файл версий уже есть в папке."""
            names = installed_files["set"]
            # по slug в имени файла
            slug = (proj.get("slug") or "").lower()
            if slug and any(slug in n for n in names):
                return True
            if version_list:
                for v in version_list:
                    for f in v.get("files") or []:
                        fn = (f.get("filename") or "").lower()
                        if fn and fn in names:
                            return True
            return False

        def one_line(text, n=70):
            t = strip_md_inline(text or "").replace("\n", " ").strip()
            if len(t) > n:
                t = t[: n - 1] + "…"
            return t

        def clear_list():
            for w in row_widgets:
                try:
                    w.destroy()
                except tk.TclError:
                    pass
            row_widgets.clear()

        def select_row(idx):
            selected_idx["i"] = idx
            for i, w in enumerate(row_widgets):
                bg = THEME["green_dark"] if i == idx else THEME["panel"]
                try:
                    w.configure(bg=bg)
                    for c in w.winfo_children():
                        c.configure(bg=bg)
                except tk.TclError:
                    pass
            if 0 <= idx < len(projects):
                show_detail(projects[idx])

        def toggle_check(pid, proj=None):
            if pid in checked:
                checked.discard(pid)
                checked_meta.pop(pid, None)
            else:
                checked.add(pid)
                src = proj or next(
                    (p for p in projects if (p.get("project_id") or p.get("slug")) == pid),
                    None,
                )
                if src:
                    checked_meta[pid] = {
                        "project_id": src.get("project_id") or pid,
                        "slug": src.get("slug") or "",
                        "title": src.get("title") or pid,
                    }
                else:
                    checked_meta[pid] = {"project_id": pid, "slug": "", "title": pid}
            rebuild_checks()

        def rebuild_checks():
            for i, w in enumerate(row_widgets):
                if i >= len(projects):
                    break
                pid = projects[i].get("project_id") or projects[i].get("slug")
                var = getattr(w, "_check_var", None)
                if var is not None:
                    var.set(1 if pid in checked else 0)

        def add_row(idx, proj, installed):
            pid = proj.get("project_id") or proj.get("slug") or str(idx)
            row = tk.Frame(list_inner, bg=THEME["panel"], cursor="hand2")
            row.pack(fill="x", padx=2, pady=1)
            row_widgets.append(row)

            var = tk.IntVar(value=1 if pid in checked else 0)
            row._check_var = var
            cb = tk.Checkbutton(
                row, variable=var, bg=THEME["panel"], activebackground=THEME["panel"],
                selectcolor=THEME["field"], fg=THEME["fg"],
                highlightthickness=0, bd=0,
                command=lambda p=pid, pr=proj: toggle_check(p, pr),
            )
            cb.pack(side="left", padx=(6, 2))

            icon_wrap = tk.Frame(row, bg=THEME["panel"], width=40, height=40)
            icon_wrap.pack(side="left", padx=(2, 8), pady=6)
            icon_wrap.pack_propagate(False)
            icon_lbl = tk.Label(icon_wrap, bg=THEME["panel"], bd=0)
            icon_lbl.place(relx=0.5, rely=0.5, anchor="center")

            text_col = tk.Frame(row, bg=THEME["panel"])
            text_col.pack(side="left", fill="x", expand=True, pady=4)
            title = (proj.get("title") or proj.get("slug") or "Без названия")
            if installed:
                title = "✓  " + title
            tk.Label(
                text_col, text=title, bg=THEME["panel"],
                fg=THEME["green_bright"] if installed else THEME["fg"],
                font=(fam, 10, "bold"), anchor="w",
            ).pack(fill="x")
            tk.Label(
                text_col, text=one_line(proj.get("description")),
                bg=THEME["panel"], fg=THEME["muted"], font=(fam, 9), anchor="w",
            ).pack(fill="x")

            def on_click(_e=None, i=idx):
                select_row(i)

            for w in (row, text_col, icon_wrap, icon_lbl):
                w.bind("<Button-1>", on_click)
                w.bind("<MouseWheel>", _wheel)
                w.bind("<Button-4>", _wheel)
                w.bind("<Button-5>", _wheel)
            for c in text_col.winfo_children():
                c.bind("<Button-1>", on_click)
                c.bind("<MouseWheel>", _wheel)

            # иконка асинхронно: сеть в фоне, PhotoImage в главном потоке
            url = proj.get("icon_url") or ""
            if not url:
                pid = proj.get("project_id") or ""
                if pid:
                    url = f"https://cdn.modrinth.com/data/{pid}/icon.png"
            if url:
                def fetch(u=url, lbl=icon_lbl):
                    png_path = cache_modrinth_icon(u, 40)
                    if not png_path:
                        return
                    def apply(path=png_path, lab=lbl, u2=u):
                        try:
                            img = photo_from_path(path, 40)
                            if img is None:
                                return
                            icon_cache[f"{u2}|40"] = img
                            lab.configure(image=img)
                            lab.image = img
                        except tk.TclError:
                            pass
                    win.after(0, apply)
                threading.Thread(target=fetch, daemon=True).start()

        def update_pager_ui():
            pages = max(1, page_state["pages"])
            page = page_state["page"] + 1
            page_label.set(t("modrinth_page", page=page, pages=pages))
            try:
                btn_prev.config(state="normal" if page_state["page"] > 0 else "disabled")
                btn_next.config(state="normal" if page_state["page"] + 1 < pages else "disabled")
            except tk.TclError:
                pass

        def go_page(delta):
            pages = max(1, page_state["pages"])
            new_page = page_state["page"] + delta
            if new_page < 0 or new_page >= pages:
                return
            page_state["page"] = new_page
            do_search(reset=False)

        def do_search(reset=True):
            status.set("Загрузка…")
            q = query.get().strip()
            ptype = current_type()
            if reset:
                page_state["page"] = 0
            # новый запрос/тип — с первой страницы
            if page_state["query"] != q or page_state["ptype"] != ptype:
                page_state["page"] = 0
                page_state["query"] = q
                page_state["ptype"] = ptype

            facets = [[f"project_type:{ptype}"]]
            if loader and ptype == "mod":
                facets.append([f"categories:{loader}"])
            mc = inst.get("mc")
            if mc:
                facets.append([f"versions:{mc}"])
            offset = page_state["page"] * PAGE_SIZE
            params = {
                "query": q,
                "limit": PAGE_SIZE,
                "offset": offset,
                "index": "downloads" if not q else "relevance",
                "facets": json.dumps(facets),
            }
            token = (q, ptype, page_state["page"])

            def work():
                try:
                    data = api_json(MODRINTH_API + "/search?" + urllib.parse.urlencode(params))
                    hits = data.get("hits") or []
                    total = int(data.get("total_hits") or len(hits))
                    win.after(0, lambda: apply_search(hits, total, token))
                except Exception as exc:
                    err = str(exc)
                    win.after(0, lambda: status.set("Ошибка поиска: " + err))

            threading.Thread(target=work, daemon=True).start()

        def apply_search(hits, total=None, token=None):
            # устаревший ответ другой страницы
            if token is not None:
                q, ptype, page = token
                if q != query.get().strip() or ptype != current_type() or page != page_state["page"]:
                    return
            installed_files["set"] = scan_installed()
            projects.clear()
            projects.extend(hits)
            clear_list()
            # галочки сохраняются между страницами и поисками
            for i, proj in enumerate(hits):
                add_row(i, proj, is_installed_project(proj))
            total = int(total or len(hits))
            page_state["total"] = total
            page_state["pages"] = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            update_pager_ui()
            if hits:
                start = page_state["page"] * PAGE_SIZE + 1
                end = page_state["page"] * PAGE_SIZE + len(hits)
                status.set(
                    t("modrinth_range", start=start, end=end, total=total,
                       page=page_state["page"] + 1, pages=page_state["pages"],
                       checked=len(checked))
                )
            else:
                status.set("Ничего не найдено")
            selected_idx["i"] = -1
            selected_project[0] = None
            d_title.set("Выберите проект слева")
            d_author.set("")
            desc.configure(state="normal")
            desc.delete("1.0", "end")
            desc.configure(state="disabled")
            version_box.configure(values=[])
            version_var.set("")
            versions_for_project.clear()
            list_canvas.yview_moveto(0)

        def show_detail(proj):
            selected_project[0] = proj
            d_title.set(proj.get("title") or proj.get("slug") or "")
            # author из search hit
            author = proj.get("author") or ""
            d_author.set(author if author else "")
            status.set("Загрузка описания…")
            pid = proj.get("project_id") or proj.get("slug")
            ptype = current_type()
            params = []
            if inst.get("mc"):
                params.append("game_versions=" + urllib.parse.quote(json.dumps([inst["mc"]])))
            if loader and ptype == "mod":
                params.append("loaders=" + urllib.parse.quote(json.dumps([loader])))
            q = ("?" + "&".join(params)) if params else ""

            def work():
                try:
                    detail = api_json(f"{MODRINTH_API}/project/{pid}")
                    versions = api_json(f"{MODRINTH_API}/project/{pid}/version{q}")
                    if not isinstance(versions, list):
                        versions = []
                    win.after(0, lambda: apply_detail(detail, versions, proj))
                except Exception as exc:
                    err = str(exc)
                    win.after(0, lambda: status.set("Ошибка: " + err))

            threading.Thread(target=work, daemon=True).start()

            # icon: сеть в фоне, PhotoImage в главном потоке
            url = proj.get("icon_url")
            if url:
                def fi(u=url):
                    png_path = cache_modrinth_icon(u, 56)
                    if not png_path:
                        return
                    def seti(path=png_path, u2=u):
                        try:
                            img = photo_from_path(path, 56)
                            if img is None:
                                return
                            icon_cache[f"{u2}|56"] = img
                            detail_icon_ref["img"] = img
                            d_icon.configure(image=img)
                        except tk.TclError:
                            pass
                    win.after(0, seti)
                threading.Thread(target=fi, daemon=True).start()

        def apply_detail(detail, versions, proj):
            authors = detail.get("organization") or ""
            team = detail.get("team")  # id only
            if detail.get("authors"):
                authors = ", ".join(a.get("name") or a.get("username") or "" for a in detail["authors"] if isinstance(a, dict))
            # search hit author fallback
            if not authors:
                authors = proj.get("author") or ""
            d_author.set(authors)
            d_title.set(detail.get("title") or proj.get("title") or "")
            body = detail.get("body") or detail.get("description") or proj.get("description") or ""
            desc.configure(state="normal")
            desc.delete("1.0", "end")
            try:
                render_markdown(desc, body, fam)
            except Exception:
                desc.insert("1.0", strip_md_inline(body))
            desc.configure(state="disabled")
            versions_for_project[:] = versions
            labels = []
            for v in versions:
                name = v.get("name") or v.get("version_number") or "Версия"
                ver = v.get("version_number") or ""
                vtype = v.get("version_type") or "release"
                tag = {"release": t("ver_release"), "beta": t("ver_beta"), "alpha": t("ver_alpha")}.get(vtype, vtype)
                labels.append(f"{name}  ·  {ver}  ·  {tag}" if ver and ver not in name else f"{name}  ·  {tag}")
            version_box.configure(values=labels)
            if labels:
                version_box.current(0)
                status.set(t("versions_count", n=len(labels)))
            else:
                version_var.set("")
                status.set("Нет совместимых версий")
            # отметить установленные
            if is_installed_project(proj, versions):
                status.set((status.get() or "") + t("already_installed"))

        def open_modrinth_page():
            proj = selected_project[0]
            if not proj:
                messagebox.showinfo("Modrinth", t("modrinth_pick_project"), parent=win)
                return
            slug = proj.get("slug") or proj.get("project_id", "")
            kind_path = {"mod": "mod", "shader": "shader", "resourcepack": "resourcepack"}.get(
                current_type(), "mod")
            webbrowser.open("https://modrinth.com/" + kind_path + "/" + urllib.parse.quote(slug, safe=""))

        def pick_file(version):
            files = version.get("files") or []
            return next((f for f in files if f.get("primary")), files[0] if files else None)

        def download_file(fileinfo, dest_dir, ptype=None):
            filename = Path(fileinfo.get("filename", "file")).name
            allowed = type_ext.get(ptype or "mod") or type_ext["mod"]
            if not filename.lower().endswith(allowed):
                raise LauncherError(t("modrinth_unsupported_type", filename=filename))
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / filename
            req = urllib.request.Request(fileinfo["url"], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=90) as response, open(target, "wb") as out:
                shutil.copyfileobj(response, out)
            expected = (fileinfo.get("hashes") or {}).get("sha1")
            if expected and hashlib.sha1(target.read_bytes()).hexdigest().lower() != expected.lower():
                target.unlink(missing_ok=True)
                raise LauncherError(t("modrinth_checksum", filename=filename))
            installed_files["set"].add(filename.lower())
            return filename

        def resolve_version_for_project(project_id, ptype="mod"):
            """Совместимая версия проекта под MC + loader (полный объект с dependencies)."""
            params = []
            if inst.get("mc"):
                params.append("game_versions=" + urllib.parse.quote(json.dumps([inst["mc"]])))
            if loader and ptype == "mod":
                params.append("loaders=" + urllib.parse.quote(json.dumps([loader])))
            q = ("?" + "&".join(params)) if params else ""
            versions = api_json(f"{MODRINTH_API}/project/{project_id}/version{q}")
            if not isinstance(versions, list) or not versions:
                versions = api_json(f"{MODRINTH_API}/project/{project_id}/version")
            if not isinstance(versions, list) or not versions:
                raise LauncherError(t("modrinth_no_dep_version", project_id=project_id))
            mc = inst.get("mc")
            for v in versions:
                gvs = v.get("game_versions") or []
                lds = v.get("loaders") or []
                if mc and gvs and mc not in gvs:
                    continue
                if loader and ptype == "mod" and lds and loader not in lds:
                    continue
                return ensure_full_version(v)
            return ensure_full_version(versions[0])

        def ensure_full_version(version):
            """Modrinth иногда отдаёт урезанный объект — догружаем по id (с dependencies)."""
            if not version:
                return version
            # уже есть список зависимостей (в т.ч. пустой после догрузки)
            if version.get("_full"):
                return version
            vid = version.get("id")
            if not vid:
                return version
            try:
                full = api_json(f"{MODRINTH_API}/version/{vid}")
                if isinstance(full, dict):
                    full["_full"] = True
                    return full
            except Exception:
                pass
            version["_full"] = True
            return version

        def project_title(project_id, fallback=""):
            try:
                d = api_json(f"{MODRINTH_API}/project/{project_id}")
                return d.get("title") or d.get("slug") or fallback or project_id
            except Exception:
                return fallback or project_id

        def collect_install_plan(roots, ptype="mod"):
            """roots: list of (title, version_dict). План с required-зависимостями."""
            plan = []
            seen_proj = set()
            seen_ver = set()

            def add_version(title, version, is_dep=False):
                version = ensure_full_version(version)
                if not version:
                    return
                vid = version.get("id") or ""
                pid = version.get("project_id") or ""
                if vid and vid in seen_ver:
                    return
                if pid and pid in seen_proj:
                    return
                if vid:
                    seen_ver.add(vid)
                if pid:
                    seen_proj.add(pid)

                fileinfo = pick_file(version)
                fn = ((fileinfo or {}).get("filename") or "").lower()
                already = bool(fn and fn in installed_files["set"])

                deps_raw = version.get("dependencies") or []
                dep_ids = []
                for dep in deps_raw:
                    if (dep.get("dependency_type") or "").lower() != "required":
                        continue
                    if dep.get("project_id"):
                        dep_ids.append(dep["project_id"])

                if fileinfo is not None:
                    plan.append({
                        "title": title,
                        "version": version,
                        "fileinfo": fileinfo,
                        "project_id": pid,
                        "version_id": vid,
                        "is_dep": is_dep,
                        "already": already,
                        "depends_on": dep_ids,
                        "icon_url": None,
                    })
                elif not deps_raw:
                    plan.append({
                        "title": title,
                        "version": version,
                        "fileinfo": None,
                        "project_id": pid,
                        "version_id": vid,
                        "is_dep": is_dep,
                        "already": False,
                        "depends_on": [],
                        "error": t("no_download_file"),
                        "icon_url": None,
                    })

                # Required-зависимости — всегда, для модов
                if ptype != "mod":
                    return
                for dep in deps_raw:
                    dtype = (dep.get("dependency_type") or "").lower()
                    if dtype != "required":
                        continue
                    dep_pid = dep.get("project_id") or ""
                    dep_vid = dep.get("version_id") or ""
                    if not dep_pid and not dep_vid:
                        continue
                    if dep_pid and dep_pid in seen_proj:
                        continue
                    try:
                        if dep_vid:
                            dep_ver = ensure_full_version(api_json(f"{MODRINTH_API}/version/{dep_vid}"))
                        else:
                            dep_ver = resolve_version_for_project(dep_pid, ptype)
                        dep_title = project_title(
                            (dep_ver or {}).get("project_id") or dep_pid,
                            t("dependency"),
                        )
                        add_version(dep_title, dep_ver, is_dep=True)
                    except Exception as exc:
                        plan.append({
                            "title": t("dependency_named", id=dep_pid or dep_vid),
                            "version": None,
                            "fileinfo": None,
                            "project_id": dep_pid,
                            "version_id": dep_vid,
                            "is_dep": True,
                            "already": False,
                            "depends_on": [],
                            "error": str(exc),
                            "icon_url": None,
                        })

            for title, ver in roots:
                if ver is None:
                    continue
                add_version(title, ver, is_dep=False)

            # Зависимости первыми в списке скачивания
            plan.sort(key=lambda it: (0 if it.get("is_dep") else 1, it.get("title") or ""))
            return plan

        def confirm_plan_dialog(plan):
            """Список + Продолжить / Отменить. Кнопки всегда видны внизу."""
            dlg = tk.Toplevel(win)
            dlg.title(t("confirm_install_title"))
            dlg.transient(win)
            dlg.grab_set()
            theme_toplevel(dlg)
            dlg.minsize(480, 360)
            dlg.geometry("520x400")

            # grid: заголовок / список (растягивается) / кнопки (фиксированы внизу)
            frm = tk.Frame(dlg, bg=THEME["bg"], padx=14, pady=12)
            frm.pack(fill="both", expand=True)
            frm.rowconfigure(1, weight=1)
            frm.columnconfigure(0, weight=1)

            n_new = sum(1 for it in plan if not it.get("already") and not it.get("error"))
            n_dep = sum(1 for it in plan if it.get("is_dep") and not it.get("already"))
            tk.Label(
                frm,
                text=t("modrinth_to_install", n_new=n_new, n_dep=n_dep),
                bg=THEME["bg"], fg=THEME["fg"], font=(fam, 11, "bold"),
            ).grid(row=0, column=0, sticky="w", pady=(0, 8))

            box = tk.Frame(
                frm, bg=THEME["panel"],
                highlightthickness=1, highlightbackground=THEME["green_dark"],
            )
            box.grid(row=1, column=0, sticky="nsew")
            box.rowconfigure(0, weight=1)
            box.columnconfigure(0, weight=1)
            txt = tk.Text(
                box, bg=THEME["panel"], fg=THEME["fg"], font=(fam, 10),
                relief="flat", highlightthickness=0, wrap="word",
            )
            txt.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
            sb = ttk.Scrollbar(box, orient="vertical", command=txt.yview)
            sb.grid(row=0, column=1, sticky="ns")
            txt.configure(yscrollcommand=sb.set)

            for item in plan:
                if item.get("error"):
                    line = t("install_error_line", title=item["title"], error=item["error"]) + "\n"
                elif item.get("already"):
                    line = t("install_skip_line", title=item["title"]) + "\n"
                elif item.get("is_dep"):
                    line = t("install_dep_line", title=item["title"]) + "\n"
                else:
                    line = f"• {item['title']}\n"
                ver = item.get("version") or {}
                vn = ver.get("version_number") or ""
                if vn and not item.get("error"):
                    line = line.rstrip("\n") + f"  ·  {vn}\n"
                txt.insert("end", line)
            txt.configure(state="disabled")

            result = {"ok": False}

            def ok():
                result["ok"] = True
                dlg.destroy()

            def cancel():
                dlg.destroy()

            btns = tk.Frame(frm, bg=THEME["bg"])
            btns.grid(row=2, column=0, sticky="ew", pady=(10, 0))
            ui_button(btns, text=t("continue"), command=ok, accent=True).pack(
                side="left")
            ui_button(btns, text=t("abort"), command=cancel).pack(side="right")
            dlg.protocol("WM_DELETE_WINDOW", cancel)
            dlg.wait_window()
            return result["ok"]

        def content_index_path():
            return instance_path(inst) / ".mylauncher_content.json"

        def load_content_index():
            p = content_index_path()
            if p.is_file():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    return {}
            return {}

        def save_content_index(data):
            p = content_index_path()
            try:
                p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass

        def record_install(item, filename, ptype="mod"):
            idx = load_content_index()
            folder = type_folders.get(ptype) or type_folders["mod"]
            key = f"{folder}/{filename}"
            ver = item.get("version") or {}
            display = (item.get("title") or "").strip() or Path(filename).stem
            pid = item.get("project_id") or ""
            icon = (item.get("icon_url") or "").strip()
            # не подставляем фейковый CDN /icon.png — URL возьмём с API при показе
            idx[key] = {
                "name": display,
                "version": ver.get("version_number") or "",
                "source": "modrinth",
                "project_id": pid,
                "version_id": item.get("version_id") or "",
                "depends_on": item.get("depends_on") or [],
                "icon_url": icon,
                "enabled": True,
                "type": ptype,
                "filename": filename,
            }
            save_content_index(idx)

        def install_checked():
            """План с зависимостями → подтверждение → скачивание."""
            dest = content_dir()
            ptype = current_type()
            # снимок для потоков (не трогаем Tk из worker)
            ptype_snap = {"v": ptype}

            roots_spec = []  # (title, pid, ver_or_None)

            if checked:
                # Все отмеченные, в т.ч. с других страниц / прошлых поисков
                for pid in list(checked):
                    meta = checked_meta.get(pid) or {}
                    proj = next(
                        (p for p in projects if (p.get("project_id") or p.get("slug")) == pid),
                        None,
                    )
                    src = proj or meta
                    title = (src.get("title") if isinstance(src, dict) else None) or pid
                    full_pid = (src.get("project_id") if isinstance(src, dict) else None) or pid
                    roots_spec.append((title, full_pid, None))
            elif selected_project[0] is not None:
                idx = version_box.current()
                ver = versions_for_project[idx] if 0 <= idx < len(versions_for_project) else None
                proj = selected_project[0]
                roots_spec.append((
                    proj.get("title") or proj.get("slug"),
                    proj.get("project_id") or proj.get("slug"),
                    ver,
                ))
            else:
                messagebox.showinfo(
                    "Установка",
                    t("pick_projects_hint"),
                    parent=win)
                return

            if not roots_spec:
                return

            status.set("Разрешение зависимостей…")
            install_btn.config(state="disabled")

            def work_plan():
                try:
                    resolved_roots = []
                    for title, pid, ver in roots_spec:
                        if ver is None:
                            ver = resolve_version_for_project(pid, ptype)
                        else:
                            ver = ensure_full_version(ver)
                        resolved_roots.append((title, ver))
                    plan = collect_install_plan(resolved_roots, ptype)
                    if not plan:
                        win.after(0, lambda: (
                            install_btn.config(state="normal"),
                            status.set("Нечего устанавливать"),
                        ))
                        return

                    def after_plan():
                        install_btn.config(state="normal")
                        if not confirm_plan_dialog(plan):
                            status.set("Установка отменена")
                            return
                        status.set("Скачивание…")
                        install_btn.config(state="disabled")

                        def work_dl():
                            log = []
                            try:
                                for item in plan:
                                    if item.get("error"):
                                        log.append(f"✗ {item['title']}: {item['error']}")
                                        continue
                                    if item.get("already"):
                                        log.append(t("already_have", title=item["title"]))
                                        continue
                                    if not item.get("fileinfo"):
                                        log.append(t("no_file_line", title=item["title"]))
                                        continue
                                    try:
                                        fn = download_file(item["fileinfo"], dest, ptype)
                                        record_install(item, fn, ptype)
                                        tag = "зависимость" if item.get("is_dep") else "основной"
                                        log.append(f"✓ {item['title']} ({tag}): {fn}")
                                    except Exception as exc:
                                        log.append(f"✗ {item['title']}: {exc}")
                                msg = "\n".join(log) if log else t("done_simple")

                                def done():
                                    install_btn.config(state="normal")
                                    installed_files["set"] = scan_installed()
                                    status.set("Установка завершена")
                                    clear_list()
                                    for i, p in enumerate(projects):
                                        add_row(i, p, is_installed_project(p))
                                    # обновить окно «Контент», если открыто
                                    cb = getattr(self, "_content_manager_refresh", None)
                                    if callable(cb):
                                        try:
                                            cb()
                                        except Exception:
                                            pass
                                    messagebox.showinfo(t("modrinth_install_done_title"), msg, parent=win)

                                win.after(0, done)
                            except Exception as exc:
                                err = str(exc)
                                win.after(0, lambda: (
                                    install_btn.config(state="normal"),
                                    status.set("Ошибка: " + err),
                                    messagebox.showerror(t("error"), err, parent=win),
                                ))

                        threading.Thread(target=work_dl, daemon=True).start()

                    win.after(0, after_plan)
                except Exception as exc:
                    err = str(exc)
                    win.after(0, lambda: (
                        install_btn.config(state="normal"),
                        status.set("Ошибка: " + err),
                        messagebox.showerror(t("error"), err, parent=win),
                    ))

            threading.Thread(target=work_plan, daemon=True).start()

        type_box.bind("<<ComboboxSelected>>", lambda _e: do_search(reset=True))
        entry.bind("<Return>", lambda _e: do_search(reset=True))
        do_search(reset=True)

    # ======================================================================
    # Установки (instances)
    # ======================================================================

    def current_instance(self):
        return current_instance(self.cfg)

    def refresh_instance_ui(self):
        configured = self.cfg.get("instances", [])
        # Показываем все установки из конфига (файлы могут ещё качаться)
        instances = [inst for inst in configured if isinstance(inst, dict) and inst.get("id") and inst.get("mc")]
        # Не показываем несколько записей одной и той же сборки (например,
        # автоматически импортированную "1.21.1 — Fabric 0.19.5" и созданную
        # пользователем "Fabric 1.21.1"). Приоритет у активной установки.
        active = self.cfg.get("active_instance")
        unique_instances = []
        seen_builds = set()
        for inst in sorted(instances, key=lambda x: x.get("id") != active):
            build_key = (inst.get("mc"), inst.get("kind"),
                         (inst.get("loader_version") or "").strip().lower())
            if build_key in seen_builds:
                continue
            seen_builds.add(build_key)
            unique_instances.append(inst)
        instances = unique_instances
        self._instance_popup_items = []
        self._instance_menu_images = []
        for inst in instances:
            icon_path = (inst.get("icon_path") or "").strip()
            image = None
            try:
                if icon_path and Path(icon_path).is_file():
                    image = tk.PhotoImage(file=icon_path)
                    # 32px — как голова Стива в кнопке профиля
                    factor = max(1, (max(image.width(), image.height()) + 31) // 32)
                    if factor > 1:
                        image = image.subsample(factor, factor)
            except (tk.TclError, OSError):
                image = None
            if image is None:
                kind = inst.get("kind") or "vanilla"
                icon_name = kind if kind in ("fabric", "quilt", "forge", "neoforge") else "vanilla"
                image = self._ui_icon(icon_name)
            if image is not None:
                self._instance_menu_images.append(image)
            name = inst.get("name") or t("instance_label_fallback")
            self._instance_popup_items.append({
                "label": name,
                "image": image,
                "command": lambda iid=inst["id"]: self.select_instance(iid),
            })
        current = self.current_instance()
        if current:
            name = current.get("name") or "Установка"
            self.instance_var.set(f"  {name}  ▾")
            kind = current.get("kind") or "vanilla"
            icon_name = kind if kind in ("fabric", "quilt", "forge", "neoforge") else "vanilla"
            self._set_menubutton_icon(self.instance_menu_button, icon_name)
        else:
            self.instance_var.set(t("no_instance"))
            self._set_menubutton_icon(self.instance_menu_button, "vanilla")
        # Выпадающий список установок неактивен, пока нет ни одной
        try:
            self.instance_menu_button.config(
                state="normal" if instances else "disabled")
        except tk.TclError:
            pass
        self.update_buttons()
        try:
            self.root.after_idle(self.update_buttons)
        except tk.TclError:
            pass
        self._maybe_refresh_lists()

    def select_instance(self, iid):
        if not any(i["id"] == iid for i in self.cfg.get("instances", [])):
            return
        if self.cfg.get("active_instance") == iid:
            return
        self.cfg["active_instance"] = iid
        save_config(self.cfg)
        self.refresh_instance_ui()

    def update_buttons(self):
        has_instance = self.current_instance() is not None
        has_profile = active_profile(self.cfg) is not None
        edit_state = "normal" if (has_instance and not self.busy) else "disabled"
        self.play_btn.config(
            state="normal" if (has_instance and has_profile and not self.busy) else "disabled")
        try:
            if self.instance_edit_btn is not self.instance_menu_button:
                self.instance_edit_btn.config(state=edit_state)
        except tk.TclError:
            pass
        # Кнопка папки находится на странице «Экземпляры» и активна
        # только при явном выборе строки в списке.
        instances_tree = getattr(self, "_instances_tree", None)
        folder_button = getattr(self, "_inst_folder_btn", None)
        if folder_button is not None:
            has_selection = bool(instances_tree is not None and instances_tree.selection())
            folder_button.config(state="normal" if has_selection else "disabled")
        if hasattr(self, "instance_menu_button"):
            has_any = bool(self.cfg.get("instances"))
            self.instance_menu_button.config(
                state="disabled" if (self.busy or not has_any) else "normal")

    def create_instance(self):
        self.open_instance_dialog(None)

    def import_instance(self):
        """Импорт готовой сборки из папки или архива (.zip / .mrpack)."""
        if self.busy:
            messagebox.showinfo(
                t("wait"),
                t("wait_busy"),
                parent=self.root,
            )
            return

        # Небольшое окно выбора: папка или архив
        choice = {"mode": None}
        dlg = tk.Toplevel(self.root)
        dlg.title(t("import_title"))
        dlg.transient(self.root)
        dlg.resizable(False, False)
        theme_toplevel(dlg)
        dlg.grab_set()
        body = ttk.Frame(dlg, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=t("import_choose"), wraplength=320).pack(anchor="w", pady=(0, 12))
        btns = ttk.Frame(body)
        btns.pack(fill="x")

        def pick(mode):
            choice["mode"] = mode
            dlg.destroy()

        ui_button(btns, text=t("import_folder"), command=lambda: pick("folder")).pack(
            side="left", padx=(0, 8))
        ui_button(btns, text=t("import_archive"), command=lambda: pick("archive")).pack(
            side="left", padx=(0, 8))
        ui_button(btns, text=t("cancel"), command=dlg.destroy).pack(side="right")
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        self.root.wait_window(dlg)

        if not choice["mode"]:
            return

        source_path = None
        is_archive = choice["mode"] == "archive"
        if is_archive:
            path = filedialog.askopenfilename(
                parent=self.root,
                title=t("import_choose"),
                filetypes=[
                    (t("packs_filter"), "*.zip *.mrpack"),
                    ("ZIP", "*.zip"),
                    ("Modrinth pack", "*.mrpack"),
                    (t("all_files"), "*.*"),
                ],
            )
            if not path:
                return
            source_path = Path(path)
        else:
            folder = filedialog.askdirectory(
                parent=self.root,
                title=t("import_choose"),
                mustexist=True,
            )
            if not folder:
                return
            source_path = Path(folder)

        if not source_path.exists():
            return

        temp_dir = None

        def work():
            nonlocal temp_dir
            try:
                extract_root = source_path
                if is_archive:
                    if not zipfile.is_zipfile(source_path):
                        raise LauncherError(t("import_not_zip"))
                    temp_dir = APP_DIR / "cache" / "import_tmp" / uuid.uuid4().hex[:10]
                    if temp_dir.exists():
                        shutil.rmtree(temp_dir, ignore_errors=True)
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(source_path) as zf:
                        zf.extractall(temp_dir)
                    # Если в архиве одна корневая папка — зайти в неё
                    children = [p for p in temp_dir.iterdir() if not p.name.startswith(".")]
                    if len(children) == 1 and children[0].is_dir():
                        extract_root = children[0]
                    else:
                        extract_root = temp_dir

                meta = detect_instance_metadata(extract_root)
                name = meta.get("name") or source_path.stem or t("instance_imported_name")
                # Уникальное имя среди существующих установок
                existing = {i["name"].lower() for i in self.cfg.get("instances", [])}
                base_name, n, final_name = name[:40], 2, name[:40]
                while final_name.lower() in existing:
                    suffix = f" {n}"
                    final_name = (base_name[: max(1, 40 - len(suffix))] + suffix)
                    n += 1

                rel_dir = unique_instance_dir(self.cfg, final_name)
                dest = GAME_DIR / rel_dir

                def on_progress(cur, total):
                    self.root.after(0, lambda c=cur, tot=total: self.status_var.set(
                        f"{t('import_progress')} {c}/{tot}"))

                self.root.after(0, lambda: self.status_var.set(t("import_progress")))
                copy_instance_files(extract_root, dest, progress_cb=on_progress)

                # Проверка, что что-то скопировалось
                if not any(dest.iterdir()):
                    shutil.rmtree(dest, ignore_errors=True)
                    raise LauncherError(t("import_empty"))

                mc = (meta.get("mc") or "").strip()
                kind = meta.get("kind")
                loader_version = (meta.get("loader_version") or "").strip()
                # Без версии MC установка не попадёт в список — подставим заглушку,
                # пользователь уточнит в диалоге.
                detected_mc = bool(mc)
                if not mc:
                    mc = "1.20.1"

                inst = {
                    "id": new_instance_id(),
                    "name": final_name,
                    "mc": mc,
                    "kind": kind,
                    "loader_version": loader_version,
                    "loader_slug": "",
                    "ram_gb": int(self.cfg.get("ram_gb", 4) or 4),
                    "icon_path": "",
                    "dir": rel_dir,
                }
                self.cfg.setdefault("instances", []).append(inst)
                self.cfg["active_instance"] = inst["id"]
                save_config(self.cfg)

                self.root.after(0, lambda: self._on_import_done(inst, detected_mc))
            except Exception as exc:
                err = str(exc) or repr(exc)
                self.root.after(0, lambda e=err: messagebox.showerror(
                    t("import_title"), t("import_fail", error=e), parent=self.root))
            finally:
                if temp_dir is not None:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                self.root.after(0, lambda: self.status_var.set(""))

        threading.Thread(target=work, daemon=True).start()

    def _on_import_done(self, inst, detected_mc):
        self.refresh_instance_ui()
        loader = LOADER_LABELS.get(inst.get("kind"), "Vanilla") if inst.get("kind") else "Vanilla"
        if inst.get("loader_version"):
            loader = f"{loader} {inst['loader_version']}"
        msg = t(
            "import_done",
            name=inst.get("name", ""),
            mc=inst.get("mc") or "—",
            loader=loader,
        )
        if not detected_mc:
            msg += "\n\n" + t("import_need_mc")
        messagebox.showinfo(t("import_title"), msg, parent=self.root)
        # Открываем редактор, чтобы пользователь мог поправить версию/загрузчик
        self.open_instance_dialog(inst)

    def edit_selected_instance(self):
        inst = self.current_instance()
        if inst is not None:
            self.open_instance_dialog(inst)

    def _default_instance_name(self):
        names = {i["name"].lower() for i in self.cfg.get("instances", [])}
        base, n, name = t("instance_default_name"), 2, t("instance_default_name")
        while name.lower() in names:
            name = f"{base} {n}"
            n += 1
        return name

    def open_instance_dialog(self, inst=None):
        """Окно создания (inst=None) или изменения установки:
        название, версия Minecraft, загрузчик модов (по желанию), память."""
        if self.busy:
            messagebox.showinfo(t("wait"), t("wait_busy"), parent=self.root)
            return
        if self.instance_dialog_refresh is not None:  # окно уже открыто
            return
        editing = inst is not None

        win = tk.Toplevel(self.root)
        win.title(t("instance_edit") if editing else t("instance_new"))
        win.transient(self.root)
        win.resizable(False, False)
        theme_toplevel(win)
        win.grab_set()
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        name_var = tk.StringVar(value=inst["name"] if editing else self._default_instance_name())
        mc_var = tk.StringVar(value="")
        show_all_var = tk.BooleanVar(value=bool(self.cfg.get("show_all")))
        kind_var = tk.StringVar(value=no_loader_label())
        lv_var = tk.StringVar(value="")
        ram_var = tk.IntVar(value=int(inst.get("ram_gb", 4)) if editing else int(self.cfg.get("ram_gb", 4) or 4))
        status_var = tk.StringVar(value="")
        dq = queue.Queue()
        state = {"types_token": 0, "ver_token": 0, "items": [], "alive": True, "first": True}

        icon_var = tk.StringVar(value=(inst.get("icon_path", "") if editing else ""))
        preview_photo = {"img": None}

        # ── Верх: превью иконки | название + «Выбрать иконку» ──
        head = ttk.Frame(frame)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        head.columnconfigure(1, weight=1)

        preview = tk.Label(
            head, width=64, height=64, bg=THEME["field"],
            highlightthickness=1, highlightbackground=THEME["green_dark"],
            bd=0, compound="center",
        )
        preview.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 12))

        def refresh_preview(*_):
            """Превью: пользовательская PNG или иконка загрузчика/ваниль."""
            custom = (icon_var.get() or "").strip()
            img = None
            if custom and Path(custom).is_file():
                try:
                    img = tk.PhotoImage(file=custom)
                    # Уменьшить пользовательскую иконку до максимума 40 px, не меняя пропорции.
                    factor = max(1, (max(img.width(), img.height()) + 39) // 40)
                    if factor > 1:
                        img = img.subsample(factor, factor)
                except tk.TclError:
                    img = None
            if img is None:
                kind = LABEL_TO_KIND.get(kind_var.get()) or "vanilla"
                name = kind if kind in ("fabric", "quilt", "forge", "neoforge") else "vanilla"
                base = self._load_icon(name, 40)
                if base is not None:
                    img = base
            preview_photo["img"] = img
            if img is not None:
                preview.configure(image=img, text="", width=64, height=64)
            else:
                preview.configure(image="", text="?", width=8, height=3)

        def choose_instance_icon():
            path = filedialog.askopenfilename(
                parent=win, title=t("instance_choose_icon_title"),
                filetypes=[(t("images_png"), "*.png"), (t("all_files"), "*.*")])
            if path:
                icon_var.set(path)
                refresh_preview()

        ui_entry(head, textvariable=name_var, width=40).grid(
            row=0, column=1, sticky="ew", pady=(0, 6))
        ui_button(head, text=t("instance_choose_icon"), command=choose_instance_icon).grid(
            row=1, column=1, sticky="w")

        # ── Поля ──
        ttk.Label(frame, text=t("instance_mc_label")).grid(row=1, column=0, sticky="w", pady=5)
        mc_box = ui_combobox(frame, textvariable=mc_var, state="readonly")
        mc_box.grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Checkbutton(
            frame, text=t("instance_show_all"),
            variable=show_all_var, command=lambda: fill_versions(),
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 6))

        ttk.Label(frame, text=t("instance_loader_label")).grid(row=3, column=0, sticky="w", pady=5)
        kind_box = ui_combobox(frame, textvariable=kind_var, state="readonly", values=[no_loader_label()])
        kind_box.grid(row=3, column=1, sticky="ew", pady=5)

        ttk.Label(frame, text=t("instance_loader_ver_label")).grid(row=4, column=0, sticky="w", pady=5)
        lv_box = ui_combobox(frame, textvariable=lv_var, state="disabled")
        lv_box.grid(row=4, column=1, sticky="ew", pady=5)

        ttk.Label(frame, text=t("instance_ram_label")).grid(row=5, column=0, sticky="w", pady=5)
        ttk.Spinbox(frame, from_=1, to=32, textvariable=ram_var, width=7).grid(
            row=5, column=1, sticky="w", pady=5)

        ttk.Label(frame, textvariable=status_var, wraplength=420, style="Muted.TLabel").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(8, 4))

        icon_var.trace_add("write", lambda *_: refresh_preview())
        refresh_preview()

        # --- загрузка списков (в фоне) ---

        def fill_versions():
            show_all = show_all_var.get()
            ids = [vid for vid, e in self.versions.items() if show_all or e.get("type") == "release"]
            if editing and inst["mc"] not in ids:
                ids.insert(0, inst["mc"])  # своя версия всегда остаётся в списке
            mc_box["values"] = ids
            if mc_var.get() not in ids:
                # Новая установка по умолчанию — последний релиз; при изменении — своя версия
                if editing and inst["mc"] in ids:
                    mc_var.set(inst["mc"])
                elif self.latest_release in ids:
                    mc_var.set(self.latest_release)
                elif ids:
                    mc_var.set(ids[0])
                else:
                    mc_var.set("")
            if not ids:
                status_var.set(t("versions_not_loaded"))
            check_types()

        def check_types(*_):
            state["types_token"] += 1
            token = state["types_token"]
            mc = mc_var.get()
            # Пока идёт поиск — загрузчик недоступен
            kind_box.config(state="disabled", values=[no_loader_label()])
            lv_box.config(state="disabled", values=[])
            lv_var.set("")
            if not mc:
                kind_var.set(no_loader_label())
                kind_box.config(state="readonly")
                on_kind_change()
                return
            prev = kind_var.get()
            if prev not in (no_loader_label(),) and not prev.startswith(t("searching_loaders")[:4]):
                state["prev_kind"] = prev
            kind_var.set(t("searching_loaders"))
            status_var.set(t("checking_loaders", mc=mc))

            def work():
                try:
                    available, errors = check_loader_types(mc)
                except Exception as exc:  # noqa: BLE001
                    available, errors = [], [str(exc)]
                dq.put(("types", token, mc, available, errors))

            threading.Thread(target=work, daemon=True).start()

        def apply_types(token, mc, available, errors):
            if token != state["types_token"] or mc != mc_var.get():
                return
            values = [no_loader_label()] + available
            kind_box.config(state="readonly", values=values)
            wanted = state.get("prev_kind") or kind_var.get()
            if state["first"] and editing and inst.get("kind"):
                wanted = LOADER_LABELS.get(inst["kind"], no_loader_label())
            state["first"] = False
            if wanted not in values or wanted.startswith(t("searching_loaders")[:5]):
                wanted = no_loader_label()
            kind_var.set(wanted)
            status_var.set((t("loader_check_partial", errors="; ".join(errors))) if errors else "")
            on_kind_change()

        def on_kind_change(*_):
            refresh_preview()
            state["ver_token"] += 1
            token = state["ver_token"]
            label = kind_var.get()
            kind = LABEL_TO_KIND.get(label)
            mc = mc_var.get()
            lv_var.set("")
            lv_box.config(values=[], state="disabled")
            state["items"] = []
            if not kind or not mc:
                return
            status_var.set(t("loading_loader_versions", label=label))

            def work():
                error, items = None, []
                try:
                    items = fetch_loader_versions(kind, mc)
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                if not items:
                    items = installed_loader_versions(kind, mc)  # вдруг уже установлено
                dq.put(("loaders", token, kind, mc, items, error))

            threading.Thread(target=work, daemon=True).start()

        def apply_loaders(token, kind, mc, items, error):
            if token != state["ver_token"]:
                return
            state["items"] = items
            if not items:
                status_var.set(error or t("no_loader_for_mc", mc=mc, loader=LOADER_LABELS.get(kind, kind)))
                return
            labels = [i["label"] for i in items]
            lv_box.config(values=labels, state="readonly")
            wanted = None
            if editing and inst.get("kind") == kind and inst["mc"] == mc:
                wanted = inst.get("loader_version")
            index = next((n for n, i in enumerate(items) if i["version"] == wanted), None)
            if index is None:
                index = next((n for n, i in enumerate(items) if i.get("default")), 0)
            lv_var.set(labels[index])
            status_var.set("")

        def pump():
            if not state["alive"]:
                return
            try:
                while True:
                    msg = dq.get_nowait()
                    if msg[0] == "types":
                        apply_types(*msg[1:])
                    elif msg[0] == "loaders":
                        apply_loaders(*msg[1:])
            except queue.Empty:
                pass
            win.after(100, pump)

        def close():
            state["alive"] = False
            self.instance_dialog_refresh = None
            win.destroy()

        # --- сохранение и удаление ---

        def save():
            name = name_var.get().strip()
            if not name or len(name) > 40:
                messagebox.showwarning(t("name"), t("instance_name_required"), parent=win)
                return
            if any(i is not inst and i["name"].lower() == name.lower() for i in self.cfg["instances"]):
                messagebox.showwarning(t("name"), t("instance_name_exists"), parent=win)
                return
            mc = mc_var.get()
            if not mc:
                messagebox.showwarning(t("version"), t("instance_mc_required"), parent=win)
                return
            kind = LABEL_TO_KIND.get(kind_var.get())
            loader_version, slug = "", ""
            if kind:
                index = lv_box.current()
                if index < 0 or index >= len(state["items"]):
                    messagebox.showwarning(t("loader"), t("instance_loader_required"), parent=win)
                    return
                item = state["items"][index]
                loader_version, slug = item["version"], item.get("slug", "")
            try:
                ram_gb = max(1, min(32, int(ram_var.get())))
            except (tk.TclError, ValueError):
                ram_gb = 4

            if editing:
                old_mc = inst.get("mc") or ""
                old_kind = inst.get("kind")
                old_lv = (inst.get("loader_version") or "").strip()
                mc_changed = old_mc != mc
                kind_changed = old_kind != kind
                lv_changed = bool(kind) and old_lv.lower() != loader_version.strip().lower()
                if mc_changed or kind_changed or lv_changed:
                    lines = [
                        t("instance_change_base_warn"),
                        "",
                    ]
                    if mc_changed:
                        lines.append(t("instance_change_base_mc", old=old_mc or "—", new=mc))
                    if kind_changed:
                        old_l = LOADER_LABELS.get(old_kind, no_loader_label()) if old_kind else no_loader_label()
                        new_l = LOADER_LABELS.get(kind, no_loader_label()) if kind else no_loader_label()
                        lines.append(t("instance_change_base_loader", old=old_l, new=new_l))
                    if lv_changed and not kind_changed:
                        lines.append(
                            t("instance_change_base_lv", old=old_lv or "—", new=loader_version or "—")
                        )
                    lines.append("")
                    lines.append(t("instance_change_base_hint"))
                    if not messagebox.askyesno(
                            t("instance_change_base_title"),
                            "\n".join(lines),
                            parent=win):
                        return

                target = inst
                target.update(name=name, mc=mc, kind=kind, loader_version=loader_version,
                              loader_slug=slug, ram_gb=ram_gb, icon_path=icon_var.get().strip())
            else:
                target = {"id": new_instance_id(), "name": name, "mc": mc, "kind": kind,
                          "loader_version": loader_version, "loader_slug": slug, "ram_gb": ram_gb,
                          "icon_path": icon_var.get().strip(), "dir": unique_instance_dir(self.cfg, name)}
                # Старый импорт из versions/ мог автоматически добавить vanilla как отдельную
                # установку. При создании модифицированной установки убираем только такую
                # стандартную запись (имя совпадает с номером версии), не пользовательские.
                if kind:
                    # Удаляем автоматически импортированную vanilla-запись и
                    # повторные записи этой же сборки с тем же загрузчиком.
                    self.cfg["instances"] = [
                        i for i in self.cfg["instances"]
                        if not (
                            (i.get("kind") is None and i.get("mc") == mc
                             and i.get("name") == mc)
                            or (i.get("kind") == kind and i.get("mc") == mc
                                and (i.get("loader_version") or "").strip().lower()
                                    == loader_version.strip().lower())
                        )
                    ]
                self.cfg["instances"].append(target)
            self.cfg["active_instance"] = target["id"]
            self.cfg["show_all"] = bool(show_all_var.get())
            self.cfg["version"] = mc
            self.cfg["ram_gb"] = ram_gb
            save_config(self.cfg)
            self.refresh_instance_ui()
            close()
            # После создания/изменения — скачиваем файлы игры (без запуска)
            self.start_worker(target, launch_game=False)

        def delete():
            close()
            self.delete_instance(inst)

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        buttons.columnconfigure(0, weight=1)
        ui_button(
            buttons, text=t("save") if editing else t("create"),
            command=save,
            accent=True).grid(row=0, column=0, sticky="ew")
        col = 1
        if editing:
            ui_button(buttons, text=t("content"), command=lambda: self.open_content_manager(
                {"id": inst["id"], "name": name_var.get().strip() or inst["name"], "mc": mc_var.get() or inst["mc"],
                 "kind": LABEL_TO_KIND.get(kind_var.get()), "loader_version": lv_var.get(), "dir": inst.get("dir", ".")},
                parent=win)).grid(row=0, column=col, padx=(8, 0))
            ui_button(buttons, text=t("delete"), command=delete).grid(
                row=0, column=col + 1, padx=(8, 0))
            col += 2
        ui_button(buttons, text=t("cancel"), command=close).grid(
            row=0, column=col, padx=(8, 0))

        mc_box.bind("<<ComboboxSelected>>", check_types)
        kind_box.bind("<<ComboboxSelected>>", on_kind_change)
        win.protocol("WM_DELETE_WINDOW", close)
        self.instance_dialog_refresh = fill_versions
        fill_versions()
        pump()

    def delete_instance(self, inst):
        if not messagebox.askyesno(t("instance_delete_title"), t("instance_delete_confirm", name=inst["name"]),
                                   parent=self.root):
            return
        try:
            path = instance_path(inst)
        except LauncherError:
            path = None
        shared = any(i is not inst and i.get("dir") == inst.get("dir") for i in self.cfg["instances"])
        instances_root = (GAME_DIR / "instances").resolve()
        # Папку удаляем только если она внутри minecraft/instances и больше никому не нужна
        removable = (path is not None and path.exists() and not shared
                     and instances_root in path.parents)
        if removable and messagebox.askyesno(
                t("instance_delete_folder_title"),
                t("instance_delete_folder_confirm", path=path),
                default="no", parent=self.root):
            shutil.rmtree(path, ignore_errors=True)
        # Удаляем запись и сразу выбираем последнюю оставшуюся установку,
        # файлы которой действительно присутствуют на диске. Иначе active_instance
        # указывает на удалённый ID и интерфейс ошибочно показывает пустой список.
        self.cfg["instances"] = [
            i for i in self.cfg.get("instances", [])
            if i.get("id") != inst.get("id")
        ]
        migrate_instances(self.cfg)
        remaining = [i for i in self.cfg.get("instances", []) if instance_files_exist(i)]
        self.cfg["active_instance"] = remaining[-1]["id"] if remaining else ""
        save_config(self.cfg)
        self.refresh_instance_ui()

    def open_game_folder(self, inst=None):
        inst = inst or self.current_instance()
        if inst is None:
            return
        try:
            folder = instance_path(inst)
            folder.mkdir(parents=True, exist_ok=True)
            if inst.get("kind"):
                (folder / "mods").mkdir(exist_ok=True)
            if OS_NAME == "windows":
                os.startfile(folder)  # noqa: S606
            elif OS_NAME == "osx":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except (OSError, LauncherError) as e:
            messagebox.showerror(t("error"), t("open_folder_fail", error=e), parent=self.root)

    # ======================================================================
    # Консоль, журнал и обработка сообщений из фоновых потоков
    # ======================================================================

    def _show_progress(self, visible):
        """Показывает полоску прогресса только во время загрузки."""
        if not hasattr(self, "bar"):
            return
        if visible and not getattr(self, "_progress_visible", False):
            self.bar.grid()
            self._progress_visible = True
        elif not visible and getattr(self, "_progress_visible", False):
            try:
                self.bar.stop()
            except tk.TclError:
                pass
            self.bar.grid_remove()
            self._progress_visible = False

    def set_console(self, visible):
        self.console_visible = visible
        if hasattr(self, "console_btn"):
            try:
                self.console_btn.configure(text="Консоль ▼" if visible else "Консоль ▲")
            except tk.TclError:
                pass
        if hasattr(self, "_layout_bars"):
            self._layout_bars()

    def toggle_console(self):
        self.set_console(not self.console_visible)
        self.cfg["show_console"] = self.console_visible
        save_config(self.cfg)

    def append_log(self, text):
        if self.log_file:
            try:
                self.log_file.write(text + "\n")
                self.log_file.flush()
            except OSError:
                pass
        self.log_text.config(state="normal")
        self.log_text.insert("end", text + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 5000:
            self.log_text.delete("1.0", "1000.0")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def on_ui_exception(self, exc, val, tb):
        self.append_log(t("ui_error", error="".join(traceback.format_exception(exc, val, tb))))

    def poll_queue(self):
        """Забирает сообщения от фоновых потоков и обновляет окно."""
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self.append_log(msg[1])
                elif kind == "status":
                    self.status_var.set(msg[1])
                elif kind == "progress":
                    done, total = msg[1], msg[2]
                    # Показываем полоску только при реальной загрузке
                    if total == 0:
                        self._show_progress(True)
                        self.bar.config(mode="indeterminate")
                        self.bar.start(12)
                    elif total > 1 and done < total:
                        self._show_progress(True)
                        self.bar.stop()
                        self.bar.config(mode="determinate", maximum=total, value=done)
                    else:
                        # Завершение (0/1 или done==total) — прячем
                        self.bar.stop()
                        self.bar.config(mode="determinate", maximum=max(total, 1), value=done)
                        self._show_progress(False)
                elif kind == "versions":
                    self.versions, self.latest_release = msg[1], msg[2]
                    if self.instance_dialog_refresh is not None:
                        self.instance_dialog_refresh()
                elif kind == "busy":
                    self.busy = bool(msg[1])
                    self.update_buttons()
                    if not self.busy:
                        self._show_progress(False)
                elif kind == "installed":
                    self.refresh_instance_ui()
                elif kind == "ely_refreshed":
                    account = msg[1]
                    if account.get("username") in self.cfg.get("ely_accounts", {}):
                        self.cfg["ely_accounts"][account["username"]] = account
                        save_config(self.cfg)
                elif kind == "ely_relogin":
                    self.add_ely_account(prefill_login=msg[1])
                elif kind == "error":
                    self.set_console(True)  # при ошибке сразу показываем консоль
                    messagebox.showerror(t("error"), msg[1])
        except queue.Empty:
            pass
        self.root.after(100, self.poll_queue)

    def load_versions(self):
        try:
            manifest = http_get_json(MANIFEST_URL)
            versions = {v["id"]: v for v in manifest["versions"]}
            self.q.put(("versions", versions, manifest["latest"]["release"]))
            hint = (t("hint_select_play") if self.cfg.get("instances")
                    else t("hint_create_first"))
            self.q.put(("status", hint))
        except Exception as e:  # noqa: BLE001
            local = {}
            versions_dir = GAME_DIR / "versions"
            if versions_dir.is_dir():
                for d in sorted(versions_dir.iterdir()):
                    f = d / f"{d.name}.json"
                    if f.exists():
                        try:
                            if "inheritsFrom" in read_json(f):
                                continue  # версии загрузчиков выбираются отдельно
                        except (OSError, ValueError):
                            continue
                        local[d.name] = {"id": d.name, "type": "release"}
            self.q.put(("log", t("versions_fetch_fail", error=e)))
            self.q.put(("versions", local, None))
            self.q.put(("status", t("no_mojang")))

    # ======================================================================
    # Запуск
    # ======================================================================

    def on_play(self):
        inst = self.current_instance()
        if inst is None:
            messagebox.showwarning(t("instance_none_title"), t("instance_none_msg"), parent=self.root)
            return
        self.start_worker(inst, launch_game=True)

    def start_worker(self, inst, launch_game=True):
        if self.busy:
            return
        profile = active_profile(self.cfg)
        if profile is None:
            messagebox.showwarning(t("no_profile_title"), t("no_profile_msg"),
                                   parent=self.root)
            return
        nick = profile["name"]
        auth_account = None
        if profile["type"] == "local":
            if not NICK_RE.fullmatch(nick):
                messagebox.showwarning(t("invalid_nick_title"), t("invalid_nick_msg"), parent=self.root)
                return
        else:
            auth_account = dict(self.cfg.get("ely_accounts", {}).get(nick) or {})
            if not auth_account:
                messagebox.showwarning("Ely.by", t("ely_account_missing"), parent=self.root)
                return
        try:
            loader = loader_for_instance(inst)
            game_dir = instance_path(inst)
        except LauncherError as exc:
            messagebox.showwarning(t("instance"), str(exc), parent=self.root)
            return
        entry = self.versions.get(inst["mc"]) or {"id": inst["mc"], "type": "release"}
        ram_mb = max(1, min(32, int(inst.get("ram_gb", 4) or 4))) * 1024
        self.cfg.update(version=inst["mc"], active_instance=inst["id"])
        save_config(self.cfg)
        threading.Thread(
            target=self.worker,
            args=(entry, loader, nick, ram_mb, launch_game, auth_account, game_dir), daemon=True,
        ).start()

    def worker(self, entry, loader, nick, ram_mb, launch_game=True, auth_account=None, game_dir=None):
        put = self.q.put
        put(("busy", True))
        try:
            if launch_game and auth_account:
                # Сессию Ely.by проверяем заранее, в фоне, чтобы окно не зависало
                put(("status", t("ely_session_check")))
                auth_account, changed = ely_ensure_session(auth_account)
                if changed:
                    put(("ely_refreshed", dict(auth_account)))
            install_and_launch(
                entry, loader, nick, ram_mb,
                log=lambda text: put(("log", text)),
                progress=lambda done, total: put(("progress", done, total)),
                status=lambda text: put(("status", text)),
                launch_game=launch_game,
                auth_account=auth_account if launch_game else None,
                game_dir=game_dir,
            )
            put(("installed", not launch_game))
            put(("status", t("ready_play") if not launch_game else t("ready_again")))
        except LauncherError as e:
            put(("progress", 0, 1))
            put(("log", t("log_error", error=e)))
            put(("status", t("status_error")))
            if getattr(e, "relogin", False) and auth_account:
                put(("ely_relogin", auth_account.get("username", nick)))
            else:
                put(("error", str(e)))
        except Exception as e:  # noqa: BLE001
            put(("progress", 0, 1))
            put(("log", traceback.format_exc()))
            put(("status", t("status_error")))
            put(("error", t("unexpected_error", error=e)))
        finally:
            put(("busy", False))


def hide_own_console():
    """Если Windows открыла для лаунчера собственное чёрное окно консоли
    (запуск двойным щелчком через python.exe) — прячем его.
    Если лаунчер запущен из вашего PowerShell, то окно не трогаем."""
    if OS_NAME != "windows":
        return
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        hwnd = kernel.GetConsoleWindow()
        if not hwnd:
            return
        pids = (ctypes.c_uint * 4)()
        if kernel.GetConsoleProcessList(pids, 4) <= 1:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:  # noqa: BLE001
        pass


def main():
    hide_own_console()
    if OS_NAME == "windows":
        try:  # чтобы текст в окне не был размытым на экранах с масштабом
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    global _PIXEL_FONT_FAMILY
    _PIXEL_FONT_FAMILY = prepare_pixel_font()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
