#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AncelLauncher — простой лаунчер Minecraft: Java Edition (версия 1.0_beta).

Возможности:
  * скачивание и запуск версий игры (офлайн / Ely.by);
  * автоматическая Java;
  * загрузчики: Fabric, Quilt, Forge, NeoForge;
  * установки (сборки) и менеджер контента через Modrinth;
  * локальные профили и аккаунты Ely.by.

Запуск: через AncelLauncher.pyw (Qt); этот файл — движок, его грузит core/backend.py
Данные (.exe): папка data/ рядом с программой (конфиг, лог, skins, minecraft).
В режиме .pyw данные лежат рядом со скриптом, как раньше.
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



# ----------------------------------------------------------------------------
# Настройки и константы
# ----------------------------------------------------------------------------

def _install_base():
    """Папка, где лежит .exe / .pyw (не путать с данными)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _runtime_base():
    """Каталог с данными пользователя.

    • .pyw (разработка) — рядом со скриптом (как раньше).
    • .exe — подпапка data/ рядом с exe, чтобы рядом с программой
      не появлялись разрозненные файлы (конфиг, лог, skins, assets…).
      Если рядом с exe уже есть старый launcher_config.json — используем
      саму папку exe (обратная совместимость).
    """
    install = _install_base()
    if not getattr(sys, "frozen", False):
        return install
    # Уже портативный расклад «всё рядом с exe» из прошлых версий
    if (install / "launcher_config.json").is_file():
        return install
    data = install / "data"
    try:
        data.mkdir(parents=True, exist_ok=True)
    except OSError:
        return install
    return data


def _resource_base():
    """Каталог ассетов: в разработке — рядом со скриптом; в exe — _MEIPASS."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


INSTALL_DIR = _install_base()
APP_DIR = _runtime_base()
RESOURCE_DIR = _resource_base()
CONFIG_FILE = APP_DIR / "launcher_config.json"
LOG_FILE = APP_DIR / "launcher.log"
SKINS_DIR = APP_DIR / "skins"
CACHE_DIR = APP_DIR / "cache"
LOCALE_DIR = RESOURCE_DIR / "locale"

# Папка игры (версии, libraries, instances…). По умолчанию — minecraft/ внутри data/.
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
LAUNCHER_VERSION = "1.0_beta"
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

def http_get_text(url, timeout=12):
    """HTTP GET для API и метаданных с ограниченным ожиданием ответа.

    Короткий тайм-аут особенно важен при проверке доступности загрузчиков:
    недоступный Maven/API не должен надолго задерживать обновление списка.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
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


# Реальная сетевая активность download(): нужна интерфейсу, чтобы отличать
# «качаем файлы» от «просто проверяем, что всё уже скачано».
NET_ACTIVITY = {"active": 0, "last": 0.0}
_NET_LOCK = threading.Lock()


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

    with _NET_LOCK:
        NET_ACTIVITY["active"] += 1
    try:
        _download_from_network(url, path, sha1)
    finally:
        with _NET_LOCK:
            NET_ACTIVITY["active"] -= 1
            NET_ACTIVITY["last"] = time.monotonic()


def _download_from_network(url, path, sha1=None):
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
    """Запускает процесс и выводит его сообщения в журнал.
    Возвращает (код_выхода, длительность_сек)."""
    t0 = time.time()
    proc = subprocess.Popen(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
    )
    for line in proc.stdout:
        log(line.rstrip())
    code = proc.wait()
    return code, max(0.0, time.time() - t0)


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
    code, _duration = stream_process(
        [str(java), "-jar", str(installer), "--installClient", str(GAME_DIR)],
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



ely_auth = ely_authenticate  # alias for backend compatibility
def ely_ensure_session(account):
    """Продлевает токен и подтягивает актуальный ник/UUID с Ely.by.

    Всегда вызывает /auth/refresh (а не только validate), чтобы после смены
    ника на сайте лаунчер увидел новое имя. Возвращает (account, changed).
    """
    token = account.get("access_token")
    client_token = account.get("client_token")
    if not token or not client_token:
        raise ElyAuthError(t("ely_err_legacy"), relogin=True)
    try:
        result = ely_post("/auth/refresh", {"accessToken": token, "clientToken": client_token})
    except ElyAuthError as exc:
        raise ElyAuthError(t("ely_err_session_end"), relogin=True) from exc
    fresh = ely_account_from_response(result, client_token)
    updated = dict(account)
    updated.update(access_token=fresh["access_token"], client_token=fresh["client_token"],
                   uuid=fresh["uuid"], username=fresh["username"])
    changed = (
        updated.get("username") != account.get("username")
        or updated.get("uuid") != account.get("uuid")
        or updated.get("access_token") != account.get("access_token")
    )
    return updated, changed


def apply_ely_account_update(cfg, old_name, account):
    """Записывает аккаунт Ely.by в конфиг; при смене ника переносит ключ и кэш скина."""
    new_name = account.get("username") or old_name
    ely = cfg.setdefault("ely_accounts", {})
    if old_name and old_name != new_name and old_name in ely:
        ely.pop(old_name, None)
        old_skin = ely_skin_cache_path(old_name)
        new_skin = ely_skin_cache_path(new_name)
        if old_skin.is_file():
            try:
                new_skin.parent.mkdir(parents=True, exist_ok=True)
                if new_skin.is_file():
                    old_skin.unlink(missing_ok=True)
                else:
                    shutil.move(str(old_skin), str(new_skin))
            except OSError:
                pass
        if cfg.get("active_profile") == profile_key("ely", old_name):
            cfg["active_profile"] = profile_key("ely", new_name)
    ely[new_name] = account
    migrate_profiles(cfg)
    return new_name


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


def extract_skin_head(skin_file, size=32, grayscale=False, rounded=True):
    """Вырезать лицо из скина Minecraft (как в Prism): head + hat overlay.

    Стандартный скин 64×64 (или 64×32): лицо (8,8)–(16,16), шляпа (40,8)–(48,16).
    Возвращает PIL.Image RGBA или None. По умолчанию со скруглёнными краями.
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
    # NEAREST сохраняет пиксельный стиль Minecraft без размытия
    face = face.resize((size, size), PILImage.Resampling.NEAREST)
    if rounded:
        face = round_pil_image(face, radius=max(4, size // 5))
    return face


def round_pil_image(pil_img, radius=8):
    """Скруглить углы PIL.Image (RGBA), сохранив прозрачность снаружи."""
    try:
        from PIL import Image as PILImage
        from PIL import ImageDraw
    except ImportError:
        return pil_img
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    w, h = pil_img.size
    radius = max(1, min(radius, min(w, h) // 2))
    # Маска со скруглённым прямоугольником
    mask = PILImage.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    out = pil_img.copy()
    out.putalpha(mask)
    return out


def ely_skin_cache_path(username):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", username or "player")
    return SKINS_DIR / "ely_cache" / f"{safe}.png"


def download_ely_skin(username, force=False, max_age=3600):
    """Скачать скин с skinsystem.ely.by в локальный кэш. None если нет.

    max_age — если кэш старше (сек), скачиваем заново (даже без force).
    """
    if not username:
        return None
    dest = ely_skin_cache_path(username)
    if dest.is_file() and dest.stat().st_size > 100 and not force:
        age = time.time() - dest.stat().st_mtime
        if age < max_age:
            return dest
    url = f"https://skinsystem.ely.by/skins/{urllib.parse.quote(username)}.png"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = resp.read()
        if not data or len(data) < 100:
            return dest if dest.is_file() and dest.stat().st_size > 100 else None
        # простая проверка PNG
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return dest if dest.is_file() and dest.stat().st_size > 100 else None
        dest.write_bytes(data)
        return dest
    except Exception:
        return dest if dest.is_file() and dest.stat().st_size > 100 else None


def resolve_profile_skin_file(cfg, profile, force=False):
    """Путь к PNG-скину для иконки головы.

    Локальный профиль → None (рисуем ч/б Steve).
    Ely.by → кэш skinsystem.ely.by (с периодическим обновлением).
    """
    if not profile:
        return None
    if profile.get("type") != "ely":
        return None
    name = profile.get("name") or ""
    return download_ely_skin(name, force=force)


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


def format_playtime(seconds):
    """Человекочитаемое время игры для списка экземпляров."""
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds < 60:
        return t("playtime_lt_minute")
    minutes = seconds // 60
    if minutes < 60:
        return t("playtime_minutes", n=minutes)
    hours = minutes // 60
    mins = minutes % 60
    if hours < 48:
        return t("playtime_hours", h=hours, m=mins)
    days = hours // 24
    hours = hours % 24
    return t("playtime_days", d=days, h=hours)


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
        # progress_cb(done, total, stage) — stage: "download" | "install"
        def _dl_prog(done, total):
            if progress_cb:
                try:
                    progress_cb(done, total, "download")
                except TypeError:
                    progress_cb(done, total)

        install_mrpack_files(index_file, dest, progress_cb=_dl_prog)
        overrides = src / "overrides"
        if overrides.is_dir():
            items = [p for p in overrides.iterdir() if not p.name.startswith("._")]
            total_ov = max(1, len(items))
            for i, item in enumerate(items):
                target = dest / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
                if progress_cb:
                    try:
                        progress_cb(i + 1, total_ov, "install")
                    except TypeError:
                        progress_cb(i + 1, total_ov)
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
            try:
                progress_cb(i + 1, total, "install")
            except TypeError:
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
        return 0.0

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
    code, duration = stream_process(command, game_dir, log)
    log(t("game_exit_code", code=code))
    if code != 0:
        err = LauncherError(t("game_exit_error", code=code))
        err.play_duration = duration
        raise err
    return duration



# ----------------------------------------------------------------------------
# Дополнительный шрифт интерфейса (Google Fonts, SIL Open Font License 1.1)
# ----------------------------------------------------------------------------

