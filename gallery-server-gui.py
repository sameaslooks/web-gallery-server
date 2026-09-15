"""
Gallery Server — short-lived local web server to browse photos and videos
from your phone (or any device in the LAN).

Serves a chosen folder recursively: thumbnails, full files, download.
Protected by a token in the URL — 403 without it. Runs while the app is open.

Video thumbnails and on-the-fly transcoding use ffmpeg if it is available
in PATH. Without ffmpeg, images still get thumbnails and video files are
served as-is (browser playback depends on the codec).

Author: sameaslooks
License: GPLv3
"""

import hashlib
import io
import json
import locale
import mimetypes
import os
import queue
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import (
    Tk, Frame, Label, Entry, Text, StringVar, IntVar,
    END, BOTH, LEFT, RIGHT, X, Y,
    filedialog, messagebox, Scrollbar,
)
from tkinter import ttk

import yaml

try:
    from PIL import Image, ImageOps, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import qrcode
    HAS_QR = True
except ImportError:
    HAS_QR = False

try:
    import pystray
    from pystray import MenuItem as Item, Menu
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_FILE = "gallery-server-config.yaml"
CACHE_DIR_NAME = ".gallery_cache"
FAVORITES_FILE = "favorites.json"

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif",
    ".heic", ".heif", ".avif", ".jfif",
}
VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".flv",
    ".mpg", ".mpeg", ".3gp", ".ts", ".mts", ".m2ts",
}

THUMB_SIZE = (320, 320)
THUMB_QUALITY = 75

DEFAULT_PORT = 8765
DEFAULT_HOST = "0.0.0.0"

# ffmpeg detection (optional)
try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = shutil.which("ffmpeg")

# Browser-friendly codecs (played as-is)
BROWSER_VIDEO_EXTS = {".mp4", ".m4v", ".webm", ".mov", ".ogv"}
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Duplicate detection: hash first N bytes + size + name (fast, not full-file)
DUP_HASH_BYTES = 1024 * 1024  # 1 MB


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------

TRANSLATIONS = {
    "en": {
        "title": "Gallery Server",
        "folder_group": "Folder to serve",
        "folder_label": "Folder:",
        "browse": "...",
        "server_group": "Server",
        "host_label": "Host:",
        "port_label": "Port:",
        "token_label": "Token:",
        "regen_token": "Regenerate",
        "start": "▶ START",
        "stop": "■ STOP",
        "url_group": "URLs (click to copy)",
        "url_local": "Local:",
        "url_lan": "LAN:",
        "qr_group": "QR — scan with your phone",
        "log_group": "Log",
        "ready": "Ready",
        "running": "Running",
        "stopped": "Stopped",
        "copy_hint": "(click to copy)",
        "lang": "Language:",
        "tray_show": "Show window",
        "tray_hide": "Hide to tray",
        "tray_stop": "Stop server",
        "tray_quit": "Quit",
        "no_folder": "Choose a folder first",
        "folder_missing": "Folder does not exist: {path}",
        "port_busy": "Port {port} is busy: {err}",
        "log_start": "[*] Starting server...",
        "log_listen": "[+] Listening on http://{host}:{port}",
        "log_stop": "[*] Server stopped",
        "log_files": "[*] Found {n} media files",
        "log_no_files": "[!] No media files in the folder",
        "log_token": "[*] Token: {token}",
        "log_copied": "[+] Copied to clipboard: {text}",
        "log_qr_missing": "[!] qrcode not installed — QR disabled",
        "log_pil_missing": "[!] Pillow not installed — thumbnails disabled",
        "log_ffmpeg_ok": "[*] ffmpeg: {path}",
        "log_ffmpeg_missing": "[!] ffmpeg not found — video thumbnails and "
                              "transcoding disabled (install ffmpeg and add to PATH)",
        "log_tray_off": "[*] Tray disabled",
        "log_tray_on": "[*] Tray enabled",
    },
    "ru": {
        "title": "Gallery Server",
        "folder_group": "Папка для раздачи",
        "folder_label": "Папка:",
        "browse": "...",
        "server_group": "Сервер",
        "host_label": "Хост:",
        "port_label": "Порт:",
        "token_label": "Токен:",
        "regen_token": "Сгенерировать",
        "start": "▶ СТАРТ",
        "stop": "■ СТОП",
        "url_group": "URL (клик — копировать)",
        "url_local": "Локальный:",
        "url_lan": "LAN:",
        "qr_group": "QR — сканируй телефоном",
        "log_group": "Лог",
        "ready": "Готов",
        "running": "Работает",
        "stopped": "Остановлен",
        "copy_hint": "(клик — копировать)",
        "lang": "Язык:",
        "tray_show": "Показать окно",
        "tray_hide": "Скрыть в трей",
        "tray_stop": "Остановить сервер",
        "tray_quit": "Выход",
        "no_folder": "Сначала выбери папку",
        "folder_missing": "Папка не существует: {path}",
        "port_busy": "Порт {port} занят: {err}",
        "log_start": "[*] Запуск сервера...",
        "log_listen": "[+] Слушаю на http://{host}:{port}",
        "log_stop": "[*] Сервер остановлен",
        "log_files": "[*] Найдено медиафайлов: {n}",
        "log_no_files": "[!] В папке нет медиафайлов",
        "log_token": "[*] Токен: {token}",
        "log_copied": "[+] Скопировано: {text}",
        "log_qr_missing": "[!] qrcode не установлен — QR отключён",
        "log_pil_missing": "[!] Pillow не установлен — превью отключены",
        "log_ffmpeg_ok": "[*] ffmpeg: {path}",
        "log_ffmpeg_missing": "[!] ffmpeg не найден — превью видео и "
                              "транскодинг отключены (поставь ffmpeg и добавь в PATH)",
        "log_tray_off": "[*] Трей отключён",
        "log_tray_on": "[*] Трей включён",
    },
}

CURRENT_LANG = "en"


def detect_language():
    try:
        sys_lang = (locale.getdefaultlocale()[0] or "en").lower()
    except Exception:
        sys_lang = "en"
    return "ru" if sys_lang.startswith("ru") else "en"


def tr(key, **kw):
    lang = CURRENT_LANG if CURRENT_LANG in TRANSLATIONS else "en"
    s = TRANSLATIONS[lang].get(key) or TRANSLATIONS["en"].get(key, key)
    return s.format(**kw) if kw else s


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Favorites (server-side, shared across all clients)
# ---------------------------------------------------------------------------

class Favorites:
    """Persistent set of favorite paths (relative to root). Thread-safe."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._set = set()
        self._load()

    def _load(self):
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._set = set(str(x) for x in data)
        except Exception:
            self._set = set()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(sorted(self._set), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)
        except Exception:
            pass

    def list(self):
        with self._lock:
            return set(self._set)

    def toggle(self, rel: str) -> bool:
        """Returns True if now favorite."""
        with self._lock:
            if rel in self._set:
                self._set.discard(rel)
                state = False
            else:
                self._set.add(rel)
                state = True
            self._save()
            return state


# ---------------------------------------------------------------------------
# Thumbnails (images + video frames)
# ---------------------------------------------------------------------------

class ThumbCache:
    """Disk + memory cache for thumbnails (images and video frames)."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem = {}
        self._lock = threading.Lock()

    def _key(self, path: Path) -> str:
        st = path.stat()
        seed = f"{path}|{st.st_mtime}|{st.st_size}"
        return hashlib.sha1(seed.encode()).hexdigest()

    def _cache_file(self, key: str) -> Path:
        return self.cache_dir / f"{key}.jpg"

    def _from_memory(self, key):
        with self._lock:
            return self._mem.get(key)

    def _to_memory(self, key, data):
        with self._lock:
            self._mem[key] = data

    def _image_thumb(self, path: Path) -> bytes | None:
        if not HAS_PIL:
            return None
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail(THUMB_SIZE, Image.LANCZOS)
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
                return buf.getvalue()
        except Exception:
            return None

    def _video_thumb(self, path: Path) -> bytes | None:
        if not FFMPEG:
            return None
        try:
            w, h = THUMB_SIZE
            vf = f"scale=w={w}:h={h}:force_original_aspect_ratio=decrease"
            for seek in ("1", "0"):
                cmd = [
                    FFMPEG,
                    "-hide_banner", "-loglevel", "error",
                    "-ss", seek,
                    "-i", str(path),
                    "-frames:v", "1",
                    "-vf", vf,
                    "-f", "image2",
                    "-vcodec", "mjpeg",
                    "-q:v", "4",
                    "pipe:1",
                ]
                try:
                    res = subprocess.run(
                        cmd, capture_output=True, timeout=15, check=False,
                        creationflags=_CREATE_NO_WINDOW,
                    )
                except Exception:
                    continue
                if res.returncode == 0 and res.stdout:
                    return res.stdout
            return None
        except Exception:
            return None

    def get(self, path: Path) -> bytes | None:
        if not path.is_file():
            return None
        ext = path.suffix.lower()

        if ext in IMAGE_EXTS:
            kind = "image"
        elif ext in VIDEO_EXTS:
            kind = "video"
        else:
            return None

        try:
            key = self._key(path)
        except Exception:
            return None

        cached = self._from_memory(key)
        if cached is not None:
            return cached

        cache_file = self._cache_file(key)
        if cache_file.exists():
            try:
                data = cache_file.read_bytes()
                self._to_memory(key, data)
                return data
            except Exception:
                pass

        if kind == "image":
            data = self._image_thumb(path)
        else:
            data = self._video_thumb(path)

        if data:
            try:
                cache_file.write_bytes(data)
            except Exception:
                pass
            self._to_memory(key, data)
        return data


# ---------------------------------------------------------------------------
# File listing + cache + duplicates
# ---------------------------------------------------------------------------

class MediaCache:
    """Caches list_media() result for a root, invalidated by root mtime
    and a lightweight periodic check."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()
        self._files = None
        self._root_mtime = None
        self._built_at = 0.0
        self._ttl = 5.0  # seconds

    def invalidate(self):
        with self._lock:
            self._files = None
            self._root_mtime = None

    def get(self):
        with self._lock:
            now = time.time()
            if (self._files is not None
                    and (now - self._built_at) < self._ttl):
                return self._files

        files = list_media(self.root)

        with self._lock:
            self._files = files
            self._built_at = time.time()
            try:
                self._root_mtime = self.root.stat().st_mtime
            except Exception:
                self._root_mtime = None
        return files


def list_media(root: Path):
    """Recursively list image/video files under root."""
    out = []
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if CACHE_DIR_NAME in p.parts:
            continue
        ext = p.suffix.lower()
        if ext in IMAGE_EXTS:
            kind = "image"
        elif ext in VIDEO_EXTS:
            kind = "video"
        else:
            continue
        try:
            st = p.stat()
        except Exception:
            continue
        out.append({
            "path": p.relative_to(root).as_posix(),
            "name": p.name,
            "kind": kind,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
        })
    out.sort(key=lambda x: (x["mtime"], x["path"]), reverse=True)
    return out


def compute_duplicates(files):
    """Group files by (size, hash of first DUP_HASH_BYTES bytes).

    Returns list of groups, each a list of file dicts (only groups with
    more than one entry)."""
    by_key = {}
    for f in files:
        try:
            full = None
            # We need the actual path; f only has relative path — caller
            # should pass absolute. To keep API simple, we accept absolute
            # path in a synthetic key.
            pass
        except Exception:
            continue
    return []


def compute_duplicates_for_root(root: Path, files):
    """Group files by (size, hash of first N bytes). Fast, no full-file read
    unless size collides."""
    by_key = {}
    for f in files:
        rel = f["path"]
        size = f["size"]
        if size <= 0:
            continue
        try:
            abs_path = root / rel
            with open(abs_path, "rb") as fh:
                head = fh.read(DUP_HASH_BYTES)
            h = hashlib.sha1(head).hexdigest()
        except Exception:
            continue
        key = (size, h)
        by_key.setdefault(key, []).append(f)

    groups = []
    for key, items in by_key.items():
        if len(items) > 1:
            groups.append(items)
    groups.sort(key=lambda g: (-len(g), -g[0]["size"]))
    return groups


# ---------------------------------------------------------------------------
# HTML frontend (single page)
# ---------------------------------------------------------------------------

HTML_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Gallery</title>
<style>
  :root {
    --bg: #111; --fg: #eee; --muted: #888;
    --card: #1c1c1c; --accent: #4aa3ff; --border: rgba(128,128,128,0.2);
    --fav: #ffcc33;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg: #f5f5f5; --fg: #222; --muted: #666;
            --card: #fff; --accent: #0066cc; --border: rgba(0,0,0,0.1); }
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont,
         "Segoe UI", Roboto, sans-serif; background: var(--bg);
         color: var(--fg); padding: 8px; padding-bottom: 40px; }
  header { position: sticky; top: 0; z-index: 10; background: var(--bg);
           padding: 8px 4px 8px; display: flex; flex-wrap: wrap;
           align-items: center; gap: 6px;
           border-bottom: 1px solid var(--border); }
  header h1 { font-size: 16px; margin: 0; flex: 0 0 auto; }
  header .meta { font-size: 12px; color: var(--muted); flex: 1 1 auto;
                 min-width: 80px; }
  header input, header select, header button {
           background: var(--card); color: var(--fg);
           border: 1px solid var(--border); border-radius: 6px;
           padding: 6px 8px; font-size: 13px; }
  header input[type=search] { min-width: 140px; flex: 1 1 160px; }
  .toolbar2 { display: flex; flex-wrap: wrap; align-items: center;
              gap: 6px; width: 100%; }
  .toolbar2 label { font-size: 12px; color: var(--muted); }
  .toolbar2 input[type=date], .toolbar2 input[type=number] {
              padding: 4px 6px; font-size: 12px; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .chip { background: var(--card); border: 1px solid var(--border);
          border-radius: 999px; padding: 2px 8px; font-size: 11px;
          color: var(--muted); cursor: pointer; }
  .chip.on { color: var(--fg); border-color: var(--accent); }
  .crumbs { display: flex; flex-wrap: wrap; align-items: center;
            gap: 4px; font-size: 12px; color: var(--muted);
            padding: 4px 2px; }
  .crumbs a { color: var(--accent); cursor: pointer;
              text-decoration: none; }
  .crumbs a:hover { text-decoration: underline; }
  .crumbs .sep { opacity: 0.5; }
  .grid { display: grid; margin-top: 8px;
          grid-template-columns: repeat(auto-fill, minmax(var(--cell, 130px), 1fr));
          gap: 8px; }
  .cell { position: relative; background: var(--card); border-radius: 8px;
          overflow: hidden; aspect-ratio: 1 / 1; cursor: pointer;
          display: flex; align-items: center; justify-content: center; }
  .cell img { width: 100%; height: 100%; object-fit: cover;
              display: block; background: #000; }
  .cell .placeholder { font-size: 40px; color: var(--muted); }
  .cell .badge { position: absolute; top: 4px; left: 4px;
                 background: rgba(0,0,0,0.65); color: #fff;
                 border-radius: 4px; padding: 2px 6px; font-size: 10px;
                 text-transform: uppercase; letter-spacing: 0.5px; }
  .cell .name { position: absolute; left: 0; right: 0; bottom: 0;
                padding: 4px 6px; font-size: 11px; color: #fff;
                background: linear-gradient(to top, rgba(0,0,0,0.85), transparent);
                white-space: nowrap; overflow: hidden;
                text-overflow: ellipsis; pointer-events: none; }
  .cell .name .sub { display: block; font-size: 10px; color: #bbb;
                     white-space: nowrap; overflow: hidden;
                     text-overflow: ellipsis; }
  .cell .dl { position: absolute; right: 4px; top: 4px;
              background: rgba(0,0,0,0.6); color: #fff;
              border-radius: 6px; padding: 4px 6px; font-size: 12px;
              text-decoration: none; }
  .cell .dl:hover { background: var(--accent); }
  .cell .folder-btn { position: absolute; right: 4px; top: 30px;
              background: rgba(0,0,0,0.6); color: #fff;
              border-radius: 6px; padding: 4px 6px; font-size: 12px;
              text-decoration: none; border: 0; cursor: pointer; }
  .cell .folder-btn:hover { background: var(--accent); }
  .cell .fav { position: absolute; right: 4px; bottom: 4px;
               background: rgba(0,0,0,0.6); color: #fff;
               border-radius: 6px; padding: 2px 7px; font-size: 14px;
               line-height: 1; user-select: none; cursor: pointer;
               border: 0; }
  .cell .fav.on { color: var(--fav); }
  .folder { position: relative; background: var(--card); border-radius: 8px;
            aspect-ratio: 1 / 1; cursor: pointer; display: flex;
            flex-direction: column; align-items: center;
            justify-content: center; gap: 6px; padding: 8px;
            text-align: center; }
  .folder .icon { font-size: 44px; }
  .folder .fname { font-size: 12px; color: var(--fg); word-break: break-all;
                   max-height: 2.6em; overflow: hidden; }
  .folder .fcount { font-size: 11px; color: var(--muted); }
  .empty { padding: 40px 8px; text-align: center; color: var(--muted); }
  .viewer { position: fixed; inset: 0; background: rgba(0,0,0,0.97);
            display: none; align-items: center; justify-content: center;
            z-index: 100; }
  .viewer.open { display: flex; flex-direction: column; }
  .viewer .bar { position: absolute; top: 0; left: 0; right: 0;
                 padding: 10px; display: flex; gap: 8px; align-items: center;
                 background: linear-gradient(to bottom, rgba(0,0,0,0.85), transparent);
                 color: #fff; z-index: 2; }
  .viewer .bar .title { flex: 1; font-size: 13px;
                        white-space: nowrap; overflow: hidden;
                        text-overflow: ellipsis; }
  .viewer .bar .sub { display: block; color: #aaa; font-size: 11px;
                      white-space: nowrap; overflow: hidden;
                      text-overflow: ellipsis; }
  .viewer .bar .path { display: block; color: #7ab8ff; font-size: 11px;
                       white-space: nowrap; overflow: hidden;
                       text-overflow: ellipsis; margin-top: 2px;
                       cursor: pointer; }
  .viewer .bar .path:hover { text-decoration: underline; }
  .viewer .bar a, .viewer .bar button { background: rgba(255,255,255,0.15);
                                        color: #fff; border: 0;
                                        border-radius: 6px; padding: 6px 10px;
                                        font-size: 13px; text-decoration: none;
                                        cursor: pointer; }
  .viewer .bar .fav-btn.on { color: var(--fav); }
  .viewer .content { max-width: 100%; max-height: 100%;
                     display: flex; align-items: center;
                     justify-content: center; overflow: hidden;
                     width: 100%; height: 100%; }
  .viewer img, .viewer video {
      max-width: 100vw; max-height: 100vh; display: block;
      transform-origin: center center;
      transition: transform 0.15s ease-out;
      touch-action: none;
  }
  .viewer .nav { position: absolute; top: 0; bottom: 0; width: 20%;
                 display: flex; align-items: center; justify-content: center;
                 color: rgba(255,255,255,0.35); font-size: 44px;
                 cursor: pointer; user-select: none; z-index: 1; }
  .viewer .nav:hover { color: #fff; }
  .viewer .nav.prev { left: 0; }
  .viewer .nav.next { right: 0; }
  .viewer .nav.hidden { display: none; }
  .spinner { width: 40px; height: 40px; border: 3px solid rgba(255,255,255,0.2);
             border-top-color: #fff; border-radius: 50%;
             animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .progress { position: absolute; bottom: 0; left: 0; right: 0;
              height: 3px; background: rgba(255,255,255,0.15); z-index: 3; }
  .progress .bar { height: 100%; width: 0%; background: var(--accent);
                   transition: width 0.2s; }
  .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
           background: rgba(0,0,0,0.85); color: #fff; padding: 8px 14px;
           border-radius: 20px; font-size: 13px; z-index: 200;
           opacity: 0; pointer-events: none; transition: opacity 0.2s; }
  .toast.show { opacity: 1; }
  .dups-modal { position: fixed; inset: 0; background: rgba(0,0,0,0.7);
                display: none; align-items: center; justify-content: center;
                z-index: 150; }
  .dups-modal.open { display: flex; }
  .dups-modal .box { background: var(--bg); border: 1px solid var(--border);
                     border-radius: 10px; max-width: 92vw; max-height: 88vh;
                     width: 720px; display: flex; flex-direction: column; }
  .dups-modal .head { padding: 12px; border-bottom: 1px solid var(--border);
                      display: flex; gap: 8px; align-items: center; }
  .dups-modal .head h2 { margin: 0; font-size: 15px; flex: 1; }
  .dups-modal .body { overflow: auto; padding: 8px; }
  .dup-group { margin-bottom: 12px; border: 1px solid var(--border);
               border-radius: 8px; padding: 8px; }
  .dup-group .dup-meta { font-size: 12px; color: var(--muted);
                         margin-bottom: 6px; }
  .dup-group .dup-items { display: flex; flex-wrap: wrap; gap: 8px; }
  .dup-item { width: 130px; background: var(--card);
              border-radius: 6px; overflow: hidden; cursor: pointer;
              position: relative; }
  .dup-item img { width: 100%; height: 100px; object-fit: cover;
                  display: block; background: #000; }
  .dup-item .dn { font-size: 10px; padding: 4px; word-break: break-all;
                  max-height: 3em; overflow: hidden; }
  .dup-item .del { position: absolute; top: 4px; right: 4px;
                   background: rgba(200,30,30,0.9); color: #fff; border: 0;
                   border-radius: 4px; padding: 2px 6px; font-size: 12px;
                   cursor: pointer; }
</style>
</head>
<body>
<header>
  <h1 id="title">Gallery</h1>
  <span class="meta" id="meta"></span>
  <button id="refresh" title="Refresh">⟳</button>
  <div class="toolbar2">
    <input type="search" id="q" placeholder="Search…">
    <select id="sort">
      <option value="mtime_desc">Date ↓</option>
      <option value="mtime_asc">Date ↑</option>
      <option value="name_asc">Name A→Z</option>
      <option value="name_desc">Name Z→A</option>
      <option value="size_desc">Size ↓</option>
      <option value="size_asc">Size ↑</option>
      <option value="kind">Kind</option>
    </select>
    <select id="filter">
      <option value="all">All</option>
      <option value="image">Photos</option>
      <option value="video">Videos</option>
    </select>
    <select id="view">
      <option value="flat">Flat</option>
      <option value="tree">Folders</option>
    </select>
    <span class="chips">
      <span class="chip" id="chip-fav">★ Favs</span>
      <span class="chip" id="chip-dups">⧉ Dups</span>
    </span>
    <label>Min&nbsp;MB <input type="number" id="minmb" min="0" step="1"
                              style="width:70px"></label>
    <label>Max&nbsp;MB <input type="number" id="maxmb" min="0" step="1"
                              style="width:70px"></label>
    <label>From <input type="date" id="dfrom"></label>
    <label>To <input type="date" id="dto"></label>
    <button id="clear-filters" title="Clear filters">✕</button>
  </div>
</header>

<div class="crumbs" id="crumbs" style="display:none"></div>
<div class="grid" id="grid"></div>
<div class="empty" id="empty" style="display:none"></div>

<div class="viewer" id="viewer">
  <div class="nav prev hidden" id="v-prev">‹</div>
  <div class="nav next hidden" id="v-next">›</div>
  <div class="bar">
    <span class="title" id="v-title"></span>
    <button class="fav-btn" id="v-fav" title="Favorite">★</button>
    <button id="v-goto-folder" title="Go to containing folder">📂</button>
    <a id="v-download" download>⤓</a>
    <button id="v-close">✕</button>
  </div>
  <div class="content" id="v-content"></div>
  <div class="progress" id="v-progress" style="display:none"><div class="bar"></div></div>
</div>

<div class="dups-modal" id="dups-modal">
  <div class="box">
    <div class="head">
      <h2>Duplicate files</h2>
      <button id="dups-refresh">⟳ Scan</button>
      <button id="dups-close">✕</button>
    </div>
    <div class="body" id="dups-body"></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const TOKEN = __TOKEN__;
const qs = new URLSearchParams(location.search);
const qtoken = qs.get('token') || TOKEN;
const url = (p, extra) => {
  const sep = p.includes('?') ? '&' : '?';
  return p + sep + 'token=' + encodeURIComponent(qtoken) +
         (extra ? '&' + extra : '');
};

const grid = document.getElementById('grid');
const meta = document.getElementById('meta');
const empty = document.getElementById('empty');
const sortSel = document.getElementById('sort');
const filterSel = document.getElementById('filter');
const viewSel = document.getElementById('view');
const qInput = document.getElementById('q');
const minMb = document.getElementById('minmb');
const maxMb = document.getElementById('maxmb');
const dFrom = document.getElementById('dfrom');
const dTo = document.getElementById('dto');
const chipFav = document.getElementById('chip-fav');
const chipDups = document.getElementById('chip-dups');
const crumbs = document.getElementById('crumbs');
const clearBtn = document.getElementById('clear-filters');

const viewer = document.getElementById('viewer');
const vTitle = document.getElementById('v-title');
const vContent = document.getElementById('v-content');
const vDl = document.getElementById('v-download');
const vFav = document.getElementById('v-fav');
const vGotoFolder = document.getElementById('v-goto-folder');
const vPrev = document.getElementById('v-prev');
const vNext = document.getElementById('v-next');
const vProgress = document.getElementById('v-progress');
const vProgressBar = vProgress.firstElementChild;

const toast = document.getElementById('toast');
function showToast(msg) {
  toast.textContent = msg;
  toast.classList.add('show');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toast.classList.remove('show'), 1500);
}

let ALL = [];
let FAVS = new Set();
let VISIBLE = [];
let CURRENT_FOLDER = '';
let VIEWER_INDEX = -1;
let VIEWER_ZOOM = 1;
let VIEWER_IMG = null;
let VIEWER_HIDE_UI = false;

const LS = {
  get(k, d) { try { const v = localStorage.getItem('gs_' + k);
                    return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem('gs_' + k, v); } catch {} },
  getBool(k, d) { return this.get(k, d ? '1' : '0') === '1'; },
};

function fmtSize(n) {
  const u = ['B','KB','MB','GB','TB'];
  let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : n) + ' ' + u[i];
}

function fmtDate(ts) {
  try { return new Date(ts * 1000).toLocaleString(); } catch { return ''; }
}

function dirOf(p) {
  const i = p.lastIndexOf('/');
  return i === -1 ? '' : p.slice(0, i);
}

function applyFilters(arr) {
  const f = filterSel.value;
  const q = qInput.value.trim().toLowerCase();
  const favOnly = chipFav.classList.contains('on');
  const minB = minMb.value ? parseFloat(minMb.value) * 1024 * 1024 : 0;
  const maxB = maxMb.value ? parseFloat(maxMb.value) * 1024 * 1024 : Infinity;
  const dFromT = dFrom.value ? new Date(dFrom.value + 'T00:00:00').getTime() / 1000 : 0;
  const dToT = dTo.value ? new Date(dTo.value + 'T23:59:59').getTime() / 1000 : Infinity;

  return arr.filter(x => {
    if (f !== 'all' && x.kind !== f) return false;
    if (favOnly && !FAVS.has(x.path)) return false;
    if (q && !x.name.toLowerCase().includes(q) && !x.path.toLowerCase().includes(q)) return false;
    if (x.size < minB || x.size > maxB) return false;
    if (x.mtime < dFromT || x.mtime > dToT) return false;
    return true;
  });
}

function sortFiles(arr) {
  const v = sortSel.value;
  const [key, dir] = v.split('_');
  const mul = dir === 'asc' ? 1 : -1;
  const out = arr.slice();
  if (key === 'name') out.sort((a, b) => a.name.localeCompare(b.name) * mul);
  else if (key === 'size') out.sort((a, b) => (a.size - b.size) * mul);
  else if (key === 'mtime') out.sort((a, b) => (a.mtime - b.mtime) * mul);
  else if (key === 'kind') out.sort((a, b) =>
    (a.kind.localeCompare(b.kind)) || (b.mtime - a.mtime));
  return out;
}

function currentFolderItems() {
  const prefix = CURRENT_FOLDER ? CURRENT_FOLDER + '/' : '';
  return ALL.filter(x => {
    if (!x.path.startsWith(prefix)) return false;
    const rest = x.path.slice(prefix.length);
    return rest.indexOf('/') === -1;
  });
}

function subfolders() {
  const prefix = CURRENT_FOLDER ? CURRENT_FOLDER + '/' : '';
  const set = new Set();
  for (const x of ALL) {
    if (!x.path.startsWith(prefix)) continue;
    const rest = x.path.slice(prefix.length);
    const slash = rest.indexOf('/');
    if (slash !== -1) set.add(rest.slice(0, slash));
  }
  return Array.from(set).sort((a, b) => a.localeCompare(b));
}

function renderCrumbs() {
  if (viewSel.value !== 'tree') { crumbs.style.display = 'none'; return; }
  crumbs.style.display = 'flex';
  crumbs.innerHTML = '';
  const mkLink = (label, path) => {
    const a = document.createElement('a');
    a.textContent = label;
    a.addEventListener('click', () => { CURRENT_FOLDER = path; render(); });
    return a;
  };
  crumbs.appendChild(mkLink('🏠 root', ''));
  if (CURRENT_FOLDER) {
    const parts = CURRENT_FOLDER.split('/');
    let acc = '';
    for (const p of parts) {
      acc = acc ? acc + '/' + p : p;
      const sep = document.createElement('span');
      sep.className = 'sep';
      sep.textContent = '/';
      crumbs.appendChild(sep);
      crumbs.appendChild(mkLink(p, acc));
    }
  }
}

function render() {
  renderCrumbs();

  let list;
  if (viewSel.value === 'tree') {
    list = currentFolderItems();
  } else {
    list = ALL;
  }
  list = sortFiles(applyFilters(list));
  VISIBLE = list;

  meta.textContent = list.length + ' / ' + ALL.length;

  grid.innerHTML = '';
  empty.style.display = 'none';
  empty.textContent = '';

  if (viewSel.value === 'tree') {
    const subs = subfolders();
    for (const s of subs) {
      grid.appendChild(makeFolderCell(s));
    }
  }

  if (!list.length && (viewSel.value !== 'tree' || !grid.children.length)) {
    empty.style.display = 'block';
    empty.textContent = 'No files';
    return;
  }

  for (const f of list) grid.appendChild(makeCell(f));
}

function makeFolderCell(name) {
  const c = document.createElement('div');
  c.className = 'folder';
  const icon = document.createElement('div');
  icon.className = 'icon';
  icon.textContent = '📁';
  const nm = document.createElement('div');
  nm.className = 'fname';
  nm.textContent = name;
  const prefix = CURRENT_FOLDER ? CURRENT_FOLDER + '/' + name + '/' : name + '/';
  const cnt = ALL.filter(x => x.path.startsWith(prefix)).length;
  const ccount = document.createElement('div');
  ccount.className = 'fcount';
  ccount.textContent = cnt + ' files';
  c.append(icon, nm, ccount);
  c.addEventListener('click', () => {
    CURRENT_FOLDER = CURRENT_FOLDER ? CURRENT_FOLDER + '/' + name : name;
    render();
  });
  return c;
}

function makeCell(f) {
  const c = document.createElement('div');
  c.className = 'cell';

  const img = document.createElement('img');
  img.loading = 'lazy';
  img.src = url('/thumb/' + encodeURIComponent(f.path));
  img.onerror = () => {
    img.remove();
    c.insertBefore(placeholder(f.kind === 'video' ? '🎞' : '🖼'), c.firstChild);
  };
  c.appendChild(img);

  if (f.kind === 'video') {
    const badge = document.createElement('div');
    badge.className = 'badge';
    badge.textContent = 'video';
    c.appendChild(badge);
  }

  // Name + path below
  const name = document.createElement('div');
  name.className = 'name';
  const nameMain = document.createElement('span');
  nameMain.textContent = f.name;
  name.appendChild(nameMain);
  const dir = dirOf(f.path);
  if (dir) {
    const sub = document.createElement('span');
    sub.className = 'sub';
    sub.textContent = '📂 ' + dir;
    name.appendChild(sub);
  }
  c.appendChild(name);

  const dl = document.createElement('a');
  dl.className = 'dl';
  dl.textContent = '⤓';
  dl.href = url('/file/' + encodeURIComponent(f.path));
  dl.setAttribute('download', f.name);
  dl.addEventListener('click', e => e.stopPropagation());
  c.appendChild(dl);

  // Go to containing folder (only useful when there's a dir)
  if (dir) {
    const fb = document.createElement('button');
    fb.className = 'folder-btn';
    fb.textContent = '📂';
    fb.title = 'Show in folder: ' + dir;
    fb.addEventListener('click', e => {
      e.stopPropagation();
      gotoFolder(dir);
    });
    c.appendChild(fb);
  }

  const fav = document.createElement('button');
  fav.className = 'fav' + (FAVS.has(f.path) ? ' on' : '');
  fav.textContent = '★';
  fav.title = 'Favorite';
  fav.addEventListener('click', async e => {
    e.stopPropagation();
    await toggleFav(f.path);
  });
  c.appendChild(fav);

  c.addEventListener('click', () => openViewerByPath(f.path));
  return c;
}

function placeholder(emoji) {
  const p = document.createElement('div');
  p.className = 'placeholder';
  p.textContent = emoji;
  return p;
}

// Switch to tree view and navigate to a folder
function gotoFolder(dir) {
  viewSel.value = 'tree';
  LS.set('view', 'tree');
  CURRENT_FOLDER = dir || '';
  // if the folder doesn't exist in tree (e.g. filtered), it still works —
  // empty is fine, crumbs show the path
  render();
}

async function toggleFav(path) {
  try {
    const r = await fetch(url('/api/fav'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path}),
    });
    if (!r.ok) return;
    const data = await r.json();
    if (data.favorite) FAVS.add(path); else FAVS.delete(path);
    if (VIEWER_INDEX >= 0 && VISIBLE[VIEWER_INDEX] &&
        VISIBLE[VIEWER_INDEX].path === path) {
      vFav.classList.toggle('on', FAVS.has(path));
    }
    render();
  } catch {}
}

// ---------- viewer ----------

function openViewerByPath(path) {
  const idx = VISIBLE.findIndex(f => f.path === path);
  if (idx < 0) return;
  openViewerAt(idx);
}

function openViewerAt(idx) {
  if (idx < 0 || idx >= VISIBLE.length) return;
  VIEWER_INDEX = idx;
  const f = VISIBLE[idx];

  vTitle.innerHTML = '';
  const t1 = document.createElement('div');
  t1.textContent = f.name;
  const t2 = document.createElement('div');
  t2.className = 'sub';
  t2.textContent = fmtSize(f.size) + ' · ' + fmtDate(f.mtime);
  vTitle.append(t1, t2);

  const dir = dirOf(f.path);
  if (dir) {
    const p = document.createElement('div');
    p.className = 'path';
    p.textContent = '📂 ' + dir + '  (click to open folder)';
    p.title = 'Go to folder';
    p.addEventListener('click', e => {
      e.stopPropagation();
      closeViewer();
      gotoFolder(dir);
    });
    vTitle.appendChild(p);
  }

  vFav.classList.toggle('on', FAVS.has(f.path));
  vGotoFolder.style.display = dir ? '' : 'none';

  vDl.href = url('/file/' + encodeURIComponent(f.path));
  vDl.setAttribute('download', f.name);
  vContent.innerHTML = '<div class="spinner"></div>';
  vProgress.style.display = 'none';
  vProgressBar.style.width = '0%';
  VIEWER_ZOOM = 1;
  VIEWER_IMG = null;
  VIEWER_HIDE_UI = false;
  viewer.classList.remove('hide-ui');

  vPrev.classList.toggle('hidden', idx <= 0);
  vNext.classList.toggle('hidden', idx >= VISIBLE.length - 1);

  if (f.kind === 'image') {
    const img = document.createElement('img');
    img.onload = () => {
      vContent.innerHTML = '';
      vContent.appendChild(img);
      VIEWER_IMG = img;
      loadExif(f.path, t2);
    };
    img.onerror = () => {
      vContent.innerHTML = '<div style="color:#fff">Failed to load</div>';
    };
    img.src = url('/file/' + encodeURIComponent(f.path));
  } else {
    const v = document.createElement('video');
    v.controls = true;
    v.autoplay = true;
    v.playsInline = true;
    v.preload = 'auto';
    v.src = url('/play/' + encodeURIComponent(f.path));

    v.addEventListener('timeupdate', () => {
      if (v.duration && isFinite(v.duration)) {
        vProgress.style.display = 'block';
        vProgressBar.style.width =
          Math.min(100, (v.currentTime / v.duration) * 100) + '%';
      }
    });
    v.addEventListener('waiting', () => {
      vProgress.style.display = 'block';
    });
    v.onloadeddata = () => {
      vContent.innerHTML = '';
      vContent.appendChild(v);
      v.play().catch(() => {});
    };
    v.onerror = () => {
      vContent.innerHTML =
        '<div style="color:#fff;padding:20px;text-align:center">' +
        'Cannot play in browser.<br><br>' +
        '<a href="' + vDl.href + '" download style="color:#4aa3ff">' +
        'Download file</a></div>';
    };
    vContent.appendChild(v);
  }

  viewer.classList.add('open');
}

async function loadExif(path, subEl) {
  try {
    const r = await fetch(url('/api/exif', 'p=' + encodeURIComponent(path)));
    if (!r.ok) return;
    const d = await r.json();
    const parts = [];
    if (d.date) parts.push('📷 ' + d.date);
    if (d.make || d.model) parts.push([d.make, d.model].filter(Boolean).join(' '));
    if (d.width && d.height) parts.push(d.width + '×' + d.height);
    if (d.iso) parts.push('ISO ' + d.iso);
    if (d.fnum) parts.push('f/' + d.fnum);
    if (d.exposure) parts.push(d.exposure + 's');
    if (d.focal) parts.push(d.focal + 'mm');
    if (parts.length) subEl.textContent += ' · ' + parts.join(' · ');
  } catch {}
}

function closeViewer() {
  viewer.classList.remove('open');
  viewer.classList.remove('hide-ui');
  vContent.innerHTML = '';
  vProgress.style.display = 'none';
  VIEWER_INDEX = -1;
  VIEWER_IMG = null;
  VIEWER_ZOOM = 1;
}

function nextItem(delta) {
  if (VIEWER_INDEX < 0) return;
  const ni = VIEWER_INDEX + delta;
  if (ni < 0 || ni >= VISIBLE.length) return;
  openViewerAt(ni);
}

document.getElementById('v-close').addEventListener('click', closeViewer);
viewer.addEventListener('click', e => {
  if (e.target === viewer) closeViewer();
});
vPrev.addEventListener('click', e => { e.stopPropagation(); nextItem(-1); });
vNext.addEventListener('click', e => { e.stopPropagation(); nextItem(1); });
vFav.addEventListener('click', async e => {
  e.stopPropagation();
  if (VIEWER_INDEX < 0) return;
  const f = VISIBLE[VIEWER_INDEX];
  await toggleFav(f.path);
});
vGotoFolder.addEventListener('click', e => {
  e.stopPropagation();
  if (VIEWER_INDEX < 0) return;
  const f = VISIBLE[VIEWER_INDEX];
  const dir = dirOf(f.path);
  closeViewer();
  gotoFolder(dir);
});

// keyboard
document.addEventListener('keydown', e => {
  if (!viewer.classList.contains('open')) return;
  if (e.key === 'Escape') { closeViewer(); }
  else if (e.key === 'ArrowLeft') { nextItem(-1); }
  else if (e.key === 'ArrowRight') { nextItem(1); }
  else if (e.key === ' ') {
    const v = vContent.querySelector('video');
    if (v) { e.preventDefault(); if (v.paused) v.play(); else v.pause(); }
  }
  else if (e.key === '+' || e.key === '=') { setZoom(VIEWER_ZOOM * 1.25); }
  else if (e.key === '-') { setZoom(VIEWER_ZOOM / 1.25); }
  else if (e.key === '0') { setZoom(1); }
  else if (e.key.toLowerCase() === 'h') { toggleViewerUI(); }
});

function toggleViewerUI() {
  VIEWER_HIDE_UI = !VIEWER_HIDE_UI;
  viewer.classList.toggle('hide-ui', VIEWER_HIDE_UI);
  document.querySelector('.viewer .bar').style.opacity =
    VIEWER_HIDE_UI ? '0' : '1';
  document.querySelector('.viewer .bar').style.pointerEvents =
    VIEWER_HIDE_UI ? 'none' : '';
}

function setZoom(z) {
  if (!VIEWER_IMG) return;
  VIEWER_ZOOM = Math.max(1, Math.min(8, z));
  VIEWER_IMG.style.transform = 'scale(' + VIEWER_ZOOM + ')';
}
if (viewer) {
  viewer.addEventListener('wheel', e => {
    if (!VIEWER_IMG) return;
    if (!e.ctrlKey && !e.metaKey && Math.abs(e.deltaY) < 5) return;
    e.preventDefault();
    setZoom(VIEWER_ZOOM * (e.deltaY < 0 ? 1.15 : 1 / 1.15));
  }, {passive: false});

  viewer.addEventListener('dblclick', e => {
    if (!VIEWER_IMG) return;
    setZoom(VIEWER_ZOOM > 1 ? 1 : 2.5);
  });

  let tStart = null, tPinch = null;
  viewer.addEventListener('touchstart', e => {
    if (e.touches.length === 2 && VIEWER_IMG) {
      tPinch = { d: touchDist(e.touches), z: VIEWER_ZOOM };
      tStart = null;
    } else if (e.touches.length === 1) {
      tStart = {
        x: e.touches[0].clientX,
        y: e.touches[0].clientY,
        t: Date.now(),
      };
    }
  }, {passive: true});

  viewer.addEventListener('touchmove', e => {
    if (tPinch && e.touches.length === 2 && VIEWER_IMG) {
      e.preventDefault();
      const d = touchDist(e.touches);
      setZoom(tPinch.z * (d / tPinch.d));
    }
  }, {passive: false});

  viewer.addEventListener('touchend', e => {
    if (tPinch && e.touches.length < 2) { tPinch = null; return; }
    if (!tStart) return;
    const dx = (e.changedTouches[0].clientX - tStart.x);
    const dy = (e.changedTouches[0].clientY - tStart.y);
    const dt = Date.now() - tStart.t;
    tStart = null;
    if (VIEWER_ZOOM > 1.01) return;
    if (dt < 500 && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
      nextItem(dx < 0 ? 1 : -1);
    }
  }, {passive: true});

  function touchDist(ts) {
    const dx = ts[0].clientX - ts[1].clientX;
    const dy = ts[0].clientY - ts[1].clientY;
    return Math.sqrt(dx*dx + dy*dy);
  }
}

// ---------- toolbar ----------
document.getElementById('refresh').addEventListener('click', load);
sortSel.addEventListener('change', () => { LS.set('sort', sortSel.value); render(); });
filterSel.addEventListener('change', () => { LS.set('filter', filterSel.value); render(); });
viewSel.addEventListener('change', () => {
  LS.set('view', viewSel.value);
  CURRENT_FOLDER = '';
  render();
});
qInput.addEventListener('input', debounce(render, 150));
minMb.addEventListener('input', debounce(render, 250));
maxMb.addEventListener('input', debounce(render, 250));
dFrom.addEventListener('change', render);
dTo.addEventListener('change', render);
chipFav.addEventListener('click', () => {
  chipFav.classList.toggle('on');
  LS.set('favonly', chipFav.classList.contains('on') ? '1' : '0');
  render();
});
chipDups.addEventListener('click', openDups);
clearBtn.addEventListener('click', () => {
  qInput.value = ''; minMb.value = ''; maxMb.value = '';
  dFrom.value = ''; dTo.value = '';
  chipFav.classList.remove('on');
  LS.set('favonly', '0');
  render();
});

function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ---------- duplicates ----------
const dupsModal = document.getElementById('dups-modal');
const dupsBody = document.getElementById('dups-body');
document.getElementById('dups-close').addEventListener('click', () =>
  dupsModal.classList.remove('open'));
document.getElementById('dups-refresh').addEventListener('click', scanDups);
dupsModal.addEventListener('click', e => {
  if (e.target === dupsModal) dupsModal.classList.remove('open');
});

async function openDups() {
  dupsModal.classList.add('open');
  await scanDups();
}

async function scanDups() {
  dupsBody.innerHTML = '<div style="padding:20px;text-align:center;color:#888">' +
                       'Scanning… (hashing first 1 MB of each file)</div>';
  try {
    const r = await fetch(url('/api/dups'));
    if (!r.ok) { dupsBody.textContent = 'error ' + r.status; return; }
    const data = await r.json();
    renderDups(data.groups || []);
  } catch (e) {
    dupsBody.textContent = 'error: ' + e;
  }
}

function renderDups(groups) {
  dupsBody.innerHTML = '';
  if (!groups.length) {
    dupsBody.innerHTML = '<div style="padding:20px;text-align:center;color:#888">' +
                         'No duplicates found.</div>';
    return;
  }
  const total = groups.reduce((s, g) => s + g.length, 0);
  const head = document.createElement('div');
  head.style.cssText = 'padding:6px 4px;color:#888;font-size:12px';
  head.textContent = groups.length + ' groups, ' + total + ' files';
  dupsBody.appendChild(head);

  groups.forEach((g, gi) => {
    const wrap = document.createElement('div');
    wrap.className = 'dup-group';
    const m = document.createElement('div');
    m.className = 'dup-meta';
    m.textContent = 'Group ' + (gi+1) + ' — ' + g.length + ' files, ' +
                    fmtSize(g[0].size) + ' each';
    wrap.appendChild(m);
    const items = document.createElement('div');
    items.className = 'dup-items';
    for (const f of g) {
      const it = document.createElement('div');
      it.className = 'dup-item';
      const im = document.createElement('img');
      im.loading = 'lazy';
      im.src = url('/thumb/' + encodeURIComponent(f.path));
      im.onerror = () => { im.replaceWith(placeholder(f.kind==='video'?'🎞':'🖼')); };
      const nm = document.createElement('div');
      nm.className = 'dn';
      nm.textContent = f.path;
      nm.title = f.path;
      const del = document.createElement('button');
      del.className = 'del';
      del.textContent = '🗑';
      del.title = 'Delete file';
      del.addEventListener('click', async e => {
        e.stopPropagation();
        if (!confirm('Delete file?\n' + f.path)) return;
        try {
          const r = await fetch(url('/api/delete'), {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: f.path}),
          });
          if (!r.ok) { showToast('Delete failed'); return; }
          showToast('Deleted');
          it.remove();
          ALL = ALL.filter(x => x.path !== f.path);
          FAVS.delete(f.path);
        } catch { showToast('Delete failed'); }
      });
      it.append(im, nm, del);
      it.addEventListener('click', () => {
        dupsModal.classList.remove('open');
        openViewerByPath(f.path);
      });
      items.appendChild(it);
    }
    wrap.appendChild(items);
    dupsBody.appendChild(wrap);
  });
}

// ---------- data ----------
async function load() {
  meta.textContent = 'loading…';
  try {
    const r = await fetch(url('/api/files'));
    if (!r.ok) { meta.textContent = 'error ' + r.status; return; }
    const data = await r.json();
    ALL = data.files;
    FAVS = new Set(data.favorites || []);
    render();
  } catch (e) {
    meta.textContent = 'error: ' + e;
  }
}

// restore settings
if (LS.get('sort')) sortSel.value = LS.get('sort');
if (LS.get('filter')) filterSel.value = LS.get('filter');
if (LS.get('view')) viewSel.value = LS.get('view');
if (LS.getBool('favonly', false)) chipFav.classList.add('on');

load();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# EXIF extraction
# ---------------------------------------------------------------------------

def read_exif(path: Path) -> dict:
    """Return a small dict with common EXIF fields (best-effort)."""
    out = {}
    if not HAS_PIL:
        return out
    if path.suffix.lower() not in IMAGE_EXTS:
        return out
    try:
        with Image.open(path) as im:
            out["width"], out["height"] = im.size
            exif = None
            try:
                exif = im.getexif()
            except Exception:
                exif = None
            if exif:
                def g(tag, default=None):
                    return exif.get(tag, default)

                # Basic tags
                dt = g(306) or g(36867)  # DateTime / DateTimeOriginal
                if dt:
                    out["date"] = str(dt)
                make = g(271)
                model = g(272)
                if make: out["make"] = str(make).strip()
                if model: out["model"] = str(model).strip()

                # EXIF IFD
                try:
                    ifd = exif.get_ifd(0x8769)
                except Exception:
                    ifd = {}
                if ifd:
                    iso = ifd.get(0x8827)
                    if iso: out["iso"] = iso
                    fnum = ifd.get(0x829D)
                    if fnum:
                        try: out["fnum"] = float(fnum)
                        except Exception: pass
                    exp = ifd.get(0x829A)
                    if exp:
                        try:
                            e = float(exp)
                            if e > 0:
                                out["exposure"] = f"{e:g}"
                        except Exception:
                            pass
                    focal = ifd.get(0x920A)
                    if focal:
                        try: out["focal"] = f"{float(focal):g}"
                        except Exception: pass
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class GalleryHandler(BaseHTTPRequestHandler):
    server_version = "GalleryServer/1.2"

    def log_message(self, fmt, *args):
        pass

    # ---------- helpers ----------

    def _send_json(self, obj, status=200, extra_headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _send_bytes(self, data: bytes, content_type: str, status=200,
                    extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _send_text(self, text: str, status=200,
                   content_type="text/plain; charset=utf-8"):
        self._send_bytes(text.encode("utf-8"), content_type, status)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except Exception:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _check_token(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        t = (params.get("token") or [""])[0]
        if t != self.server.token:
            self._send_json(
                {"code": 403, "message": "Forbidden. token required."},
                status=403,
            )
            return False
        return True

    def _safe_path(self, rel: str) -> Path | None:
        try:
            rel = urllib.parse.unquote(rel)
        except Exception:
            return None
        rel = rel.replace("\\", "/").lstrip("/")
        candidate = (self.server.root / rel).resolve()
        root = self.server.root.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    # ---------- routes ----------

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._serve_index(); return
        if path == "/api/files":
            self._serve_files(); return
        if path == "/api/exif":
            self._serve_exif(); return
        if path == "/api/dups":
            self._serve_dups(); return
        if path.startswith("/thumb/"):
            self._serve_thumb(path[len("/thumb/"):]); return
        if path.startswith("/file/"):
            self._serve_file(path[len("/file/"):]); return
        if path.startswith("/play/"):
            self._serve_play(path[len("/play/"):]); return
        self._send_text("not found", status=404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/fav":
            self._api_fav(); return
        if path == "/api/delete":
            self._api_delete(); return
        self._send_text("not found", status=404)

    def do_HEAD(self):
        self.do_GET()

    # ---------- handlers ----------

    def _serve_index(self):
        if not self._check_token():
            return
        page = HTML_PAGE.replace("__TOKEN__", json.dumps(self.server.token))
        self._send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_files(self):
        if not self._check_token():
            return
        files = self.server.media.get()
        total = sum(f["size"] for f in files)
        favs = sorted(self.server.favorites.list())
        self._send_json({
            "files": files,
            "total": total,
            "favorites": favs,
        })

    def _serve_exif(self):
        if not self._check_token():
            return
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        rel = (params.get("p") or [""])[0]
        if not rel:
            self._send_json({}, status=400); return
        p = self._safe_path(rel)
        if p is None or not p.is_file():
            self._send_json({}, status=404); return
        self._send_json(read_exif(p))

    def _serve_dups(self):
        if not self._check_token():
            return
        files = self.server.media.get()
        groups = compute_duplicates_for_root(self.server.root, files)
        self._send_json({"groups": groups})

    def _serve_thumb(self, rel: str):
        if not self._check_token():
            return
        p = self._safe_path(rel)
        if p is None or not p.is_file():
            self._send_text("not found", status=404); return
        data = self.server.thumbs.get(p)
        if data is None:
            self._send_text("no thumbnail", status=404); return
        self._send_bytes(
            data, "image/jpeg",
            extra_headers={"Cache-Control": "public, max-age=86400"},
        )

    def _serve_file(self, rel: str):
        if not self._check_token():
            return
        p = self._safe_path(rel)
        if p is None or not p.is_file():
            self._send_text("not found", status=404); return
        ctype, _ = mimetypes.guess_type(str(p))
        if not ctype:
            ctype = "application/octet-stream"
        try:
            st = p.stat()
        except Exception:
            self._send_text("not found", status=404); return
        size = st.st_size
        range_header = self.headers.get("Range")

        if range_header:
            try:
                rng = range_header.strip().replace("bytes=", "")
                start_s, end_s = rng.split("-", 1)
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else size - 1
                end = min(end, size - 1)
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(length))
                self.send_header("Content-Range",
                                 f"bytes {start}-{end}/{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                with open(p, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except Exception:
                pass
            return

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        try:
            with open(p, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception:
            pass

    def _serve_play(self, rel: str):
        if not self._check_token():
            return
        p = self._safe_path(rel)
        if p is None or not p.is_file():
            self._send_text("not found", status=404); return

        ext = p.suffix.lower()
        direct_ok = ext in BROWSER_VIDEO_EXTS

        if direct_ok or not FFMPEG:
            return self._serve_file(rel)

        try:
            cmd = [
                FFMPEG,
                "-hide_banner", "-loglevel", "error",
                "-i", str(p),
                "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                "-f", "mp4",
                "pipe:1",
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                bufsize=0,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as e:
            self._send_text(f"transcode failed: {e}", status=500); return

        try:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            while True:
                try:
                    chunk = proc.stdout.read(65536)
                except Exception:
                    break
                if not chunk:
                    break
                try:
                    self.wfile.write(f"{len(chunk):X}\r\n".encode())
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                except Exception:
                    break
            try:
                self.wfile.write(b"0\r\n\r\n")
            except Exception:
                pass
        except Exception:
            pass
        finally:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try: proc.wait(timeout=1)
                    except Exception: pass
                    if proc.poll() is None:
                        proc.kill()
                        try: proc.wait(timeout=1)
                        except Exception: pass
            except Exception:
                pass
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass

    # ---------- POST APIs ----------

    def _api_fav(self):
        if not self._check_token():
            return
        try:
            body = self._read_body()
            data = json.loads(body.decode("utf-8") or "{}")
        except Exception:
            self._send_json({"error": "bad json"}, status=400); return
        rel = (data.get("path") or "").strip()
        if not rel:
            self._send_json({"error": "no path"}, status=400); return
        # safety
        p = self._safe_path(rel)
        if p is None:
            self._send_json({"error": "bad path"}, status=400); return
        state = self.server.favorites.toggle(rel)
        self._send_json({"path": rel, "favorite": state})

    def _api_delete(self):
        if not self._check_token():
            return
        try:
            body = self._read_body()
            data = json.loads(body.decode("utf-8") or "{}")
        except Exception:
            self._send_json({"error": "bad json"}, status=400); return
        rel = (data.get("path") or "").strip()
        if not rel:
            self._send_json({"error": "no path"}, status=400); return
        p = self._safe_path(rel)
        if p is None or not p.is_file():
            self._send_json({"error": "not found"}, status=404); return
        try:
            p.unlink()
        except Exception as e:
            self._send_json({"error": str(e)}, status=500); return
        # invalidate caches
        try:
            self.server.favorites.toggle  # noop
        except Exception:
            pass
        self.server.media.invalidate()
        self._send_json({"deleted": rel})


class GalleryServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, root: Path, token: str,
                 thumbs: ThumbCache, media: MediaCache,
                 favorites: Favorites, logger):
        super().__init__(addr, handler)
        self.root = root
        self.token = token
        self.thumbs = thumbs
        self.media = media
        self.favorites = favorites
        self.logger = logger


# ---------------------------------------------------------------------------
# Server thread
# ---------------------------------------------------------------------------

class ServerThread(threading.Thread):
    def __init__(self, host, port, root, token, log):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.root = root
        self.token = token
        self.log = log
        self.server = None
        self._ready = threading.Event()
        self._error = None

    def run(self):
        try:
            cache_dir = self.root / CACHE_DIR_NAME
            thumbs = ThumbCache(cache_dir)
            media = MediaCache(self.root)
            favorites = Favorites(cache_dir / FAVORITES_FILE)
            self.server = GalleryServer(
                (self.host, self.port), GalleryHandler,
                self.root, self.token, thumbs, media, favorites, self.log,
            )
        except OSError as e:
            self._error = e
            self._ready.set(); return
        except Exception as e:
            self._error = e
            self._ready.set(); return
        self._ready.set()
        try:
            self.server.serve_forever(poll_interval=0.5)
        except Exception:
            pass

    def wait_ready(self, timeout=3.0):
        self._ready.wait(timeout=timeout)
        return self._error

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
            except Exception:
                pass
            try:
                self.server.server_close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root: Tk):
        global CURRENT_LANG
        self.root = root
        root.title(tr("title"))
        root.geometry("760x820")

        self.cfg = load_config()
        CURRENT_LANG = self.cfg.get("language") or detect_language()

        self.log_queue = queue.Queue()
        self.server_thread = None
        self.tray_icon = None
        self._qr_photo = None

        self.folder_var = StringVar(value=self.cfg.get("folder", ""))
        self.host_var = StringVar(value=self.cfg.get("host", DEFAULT_HOST))
        self.port_var = IntVar(value=int(self.cfg.get("port", DEFAULT_PORT)))
        self.token_var = StringVar(
            value=self.cfg.get("token", "") or secrets.token_urlsafe(12)
        )
        self.local_url_var = StringVar(value="")
        self.lan_url_var = StringVar(value="")
        self.status_var = StringVar(value=tr("ready"))
        self.lang_var = StringVar(value=CURRENT_LANG)

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if HAS_TRAY:
            try:
                self._setup_tray()
            except Exception:
                pass

        self.root.after(100, self._drain_log)
        self._log(tr("log_pil_missing") if not HAS_PIL else "[*] Pillow OK")
        if not HAS_QR:
            self._log(tr("log_qr_missing"))
        if FFMPEG:
            self._log(tr("log_ffmpeg_ok", path=FFMPEG))
        else:
            self._log(tr("log_ffmpeg_missing"))

    # ---------- UI ----------

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        f_folder = ttk.LabelFrame(self.root, text=tr("folder_group"))
        f_folder.pack(fill=X, **pad)
        row = Frame(f_folder); row.pack(fill=X, padx=4, pady=4)
        Label(row, text=tr("folder_label")).pack(side=LEFT)
        Entry(row, textvariable=self.folder_var).pack(
            side=LEFT, fill=X, expand=True, padx=4)
        ttk.Button(row, text=tr("browse"), width=3,
                   command=self.pick_folder).pack(side=LEFT)

        f_srv = ttk.LabelFrame(self.root, text=tr("server_group"))
        f_srv.pack(fill=X, **pad)

        row = Frame(f_srv); row.pack(fill=X, padx=4, pady=2)
        Label(row, text=tr("host_label")).pack(side=LEFT)
        Entry(row, textvariable=self.host_var, width=14).pack(side=LEFT, padx=4)
        Label(row, text=tr("port_label")).pack(side=LEFT, padx=(12, 0))
        Entry(row, textvariable=self.port_var, width=7).pack(side=LEFT, padx=4)

        row = Frame(f_srv); row.pack(fill=X, padx=4, pady=2)
        Label(row, text=tr("token_label")).pack(side=LEFT)
        Entry(row, textvariable=self.token_var).pack(
            side=LEFT, fill=X, expand=True, padx=4)
        ttk.Button(row, text=tr("regen_token"),
                   command=self.regen_token).pack(side=LEFT)

        f_run = Frame(self.root)
        f_run.pack(fill=X, **pad)
        self.btn_start = ttk.Button(f_run, text=tr("start"), command=self.start)
        self.btn_start.pack(side=LEFT, padx=2)
        self.btn_stop = ttk.Button(f_run, text=tr("stop"),
                                   command=self.stop, state="disabled")
        self.btn_stop.pack(side=LEFT, padx=2)
        Label(f_run, textvariable=self.status_var, fg="gray").pack(
            side=LEFT, padx=12)

        f_url = ttk.LabelFrame(self.root, text=tr("url_group"))
        f_url.pack(fill=X, **pad)

        row = Frame(f_url); row.pack(fill=X, padx=4, pady=2)
        Label(row, text=tr("url_local"), width=12, anchor="w").pack(side=LEFT)
        e1 = Entry(row, textvariable=self.local_url_var, state="readonly")
        e1.pack(side=LEFT, fill=X, expand=True, padx=4)
        e1.bind("<Button-1>", lambda ev: self._copy(self.local_url_var.get()))

        row = Frame(f_url); row.pack(fill=X, padx=4, pady=2)
        Label(row, text=tr("url_lan"), width=12, anchor="w").pack(side=LEFT)
        e2 = Entry(row, textvariable=self.lan_url_var, state="readonly")
        e2.pack(side=LEFT, fill=X, expand=True, padx=4)
        e2.bind("<Button-1>", lambda ev: self._copy(self.lan_url_var.get()))

        Label(f_url, text=tr("copy_hint"), fg="gray").pack(
            anchor="w", padx=8, pady=(0, 4))

        f_qr = ttk.LabelFrame(self.root, text=tr("qr_group"))
        f_qr.pack(fill=X, **pad)
        self.qr_label = Label(f_qr, text="", bg="#111")
        self.qr_label.pack(padx=8, pady=8)

        f_log = ttk.LabelFrame(self.root, text=tr("log_group"))
        f_log.pack(fill=BOTH, expand=True, **pad)
        self.log = Text(f_log, height=10, wrap="none")
        self.log.pack(side=LEFT, fill=BOTH, expand=True, padx=4, pady=4)
        sb = Scrollbar(f_log, command=self.log.yview)
        sb.pack(side=LEFT, fill=Y)
        self.log.config(yscrollcommand=sb.set)

        row = Frame(self.root)
        row.pack(fill=X, padx=8, pady=4)
        Label(row, text=tr("lang")).pack(side=LEFT)
        combo = ttk.Combobox(row, textvariable=self.lang_var,
                             values=["en", "ru"], width=4, state="readonly")
        combo.pack(side=LEFT, padx=4)
        combo.bind("<<ComboboxSelected>>", self._on_lang_change)

    # ---------- helpers ----------

    def pick_folder(self):
        d = filedialog.askdirectory(mustexist=True)
        if d:
            self.folder_var.set(d)

    def regen_token(self):
        self.token_var.set(secrets.token_urlsafe(12))

    def _on_lang_change(self, event=None):
        global CURRENT_LANG
        CURRENT_LANG = self.lang_var.get()
        messagebox.showinfo(
            "Language",
            "Restart the app to apply." if CURRENT_LANG == "en"
            else "Перезапустите приложение."
        )

    def _log(self, msg):
        self.log_queue.put(msg)

    def _drain_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log.insert(END, msg + "\n")
                self.log.see(END)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log)

    def _copy(self, text):
        if not text:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._log(tr("log_copied", text=text))
        except Exception:
            pass

    def _detect_lan_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.2)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _make_qr(self, data: str):
        if not HAS_QR or not HAS_PIL:
            self.qr_label.config(image="", text="(qrcode not installed)")
            return
        try:
            img = qrcode.make(data)
            img = img.convert("RGB").resize((220, 220))
            photo = ImageTk.PhotoImage(img)
            self._qr_photo = photo
            self.qr_label.config(image=photo, text="")
        except Exception as e:
            self.qr_label.config(text=f"(qr error: {e})")

    # ---------- lifecycle ----------

    def start(self):
        if self.server_thread and self.server_thread.is_alive():
            return
        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showwarning(tr("title"), tr("no_folder"))
            return
        root = Path(folder)
        if not root.exists() or not root.is_dir():
            messagebox.showwarning(tr("title"),
                                   tr("folder_missing", path=folder))
            return

        host = self.host_var.get().strip() or DEFAULT_HOST
        try:
            port = int(self.port_var.get())
        except Exception:
            port = DEFAULT_PORT
        token = self.token_var.get().strip() or secrets.token_urlsafe(12)
        self.token_var.set(token)

        self._log(tr("log_start"))
        self._log(tr("log_token", token=token))

        self.server_thread = ServerThread(host, port, root, token, self._log)
        self.server_thread.start()
        err = self.server_thread.wait_ready(timeout=3.0)
        if err is not None:
            self._log(tr("port_busy", port=port, err=err))
            self.server_thread = None
            self.status_var.set(tr("stopped"))
            return

        lan_ip = self._detect_lan_ip() if host in ("0.0.0.0", "") else host
        local = f"http://127.0.0.1:{port}/?token={token}"
        lan = f"http://{lan_ip}:{port}/?token={token}"
        self.local_url_var.set(local)
        self.lan_url_var.set(lan)
        self._make_qr(lan)

        self._log(tr("log_listen", host=host, port=port))
        n = len(list_media(root))
        if n:
            self._log(tr("log_files", n=n))
        else:
            self._log(tr("log_no_files"))

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.status_var.set(tr("running"))

        cfg = {
            "folder": folder,
            "host": host,
            "port": port,
            "token": token,
            "language": self.lang_var.get(),
        }
        save_config(cfg)

    def stop(self):
        if self.server_thread:
            self._log("[*] stopping...")
            self.server_thread.stop()
            try:
                self.server_thread.join(timeout=3)
            except Exception:
                pass
            self.server_thread = None
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.status_var.set(tr("stopped"))
        self.local_url_var.set("")
        self.lan_url_var.set("")
        self.qr_label.config(image="", text="")
        self._qr_photo = None
        self._log(tr("log_stop"))

    # ---------- tray ----------

    def _tray_image(self):
        img = Image.new("RGB", (64, 64), (30, 30, 30))
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        d.rectangle([8, 8, 56, 56], outline=(60, 160, 255), width=4)
        d.ellipse([22, 22, 42, 42], outline=(60, 160, 255), width=4)
        d.ellipse([30, 30, 34, 34], fill=(60, 160, 255))
        return img

    def _setup_tray(self):
        menu = Menu(
            Item(tr("tray_show"), self._tray_show, default=True),
            Item(tr("tray_hide"), self._tray_hide),
            Item(tr("tray_stop"), self._tray_stop),
            Item(tr("tray_quit"), self._tray_quit),
        )
        self.tray_icon = pystray.Icon(
            "gallery_server", self._tray_image(), tr("title"), menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _tray_show(self, icon=None, item=None):
        self.root.after(0, self.root.deiconify)

    def _tray_hide(self, icon=None, item=None):
        self.root.after(0, self.root.withdraw)

    def _tray_stop(self, icon=None, item=None):
        self.root.after(0, self.stop)

    def _tray_quit(self, icon=None, item=None):
        self.root.after(0, self._real_quit)

    def _on_close(self):
        self._real_quit()

    def _real_quit(self):
        try:
            self.stop()
        except Exception:
            pass
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            self.root.after(100, self.root.destroy)
        except Exception:
            self.root.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    root = Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    print("[*] Starting Gallery Server...")
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            input("Press Enter to close...")
        except Exception:
            pass