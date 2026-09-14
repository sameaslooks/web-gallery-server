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
    FFMPEG = shutil.which("ffmpeg")  # fallback на PATH

# Browser-friendly codecs (played as-is)
BROWSER_VIDEO_EXTS = {".mp4", ".m4v", ".webm", ".mov", ".ogv"}
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

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
        import hashlib
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
        """Extract a frame at 1s via ffmpeg (if available)."""
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
# File listing
# ---------------------------------------------------------------------------

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
    # Default server-side order: date desc. Client re-sorts anyway.
    out.sort(key=lambda x: (x["mtime"], x["path"]), reverse=True)
    return out


# ---------------------------------------------------------------------------
# HTML frontend (single page)
# ---------------------------------------------------------------------------

HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Gallery</title>
<style>
  :root {
    --bg: #111; --fg: #eee; --muted: #888;
    --card: #1c1c1c; --accent: #4aa3ff; --border: rgba(128,128,128,0.2);
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
           padding: 8px 4px 12px; display: flex; flex-wrap: wrap;
           align-items: center; gap: 8px;
           border-bottom: 1px solid var(--border); }
  header h1 { font-size: 16px; margin: 0; flex: 1 1 auto; }
  header .meta { font-size: 12px; color: var(--muted); }
  header select, header button {
           background: var(--card); color: var(--fg);
           border: 1px solid var(--border); border-radius: 6px;
           padding: 6px 8px; font-size: 13px; }
  .grid { display: grid; margin-top: 8px;
          grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
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
                background: linear-gradient(to top, rgba(0,0,0,0.8), transparent);
                white-space: nowrap; overflow: hidden;
                text-overflow: ellipsis; pointer-events: none; }
  .cell .dl { position: absolute; right: 4px; top: 4px;
              background: rgba(0,0,0,0.6); color: #fff;
              border-radius: 6px; padding: 4px 6px; font-size: 12px;
              text-decoration: none; }
  .cell .dl:hover { background: var(--accent); }
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
  .viewer .bar a, .viewer .bar button { background: rgba(255,255,255,0.15);
                                        color: #fff; border: 0;
                                        border-radius: 6px; padding: 6px 10px;
                                        font-size: 13px; text-decoration: none;
                                        cursor: pointer; }
  .viewer .content { max-width: 100%; max-height: 100%;
                     display: flex; align-items: center;
                     justify-content: center; }
  .viewer img, .viewer video { max-width: 100vw; max-height: 100vh;
                               display: block; }
  .spinner { width: 40px; height: 40px; border: 3px solid rgba(255,255,255,0.2);
             border-top-color: #fff; border-radius: 50%;
             animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<header>
  <h1 id="title">Gallery</h1>
  <span class="meta" id="meta"></span>
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
  <button id="refresh">⟳</button>
</header>
<div class="grid" id="grid"></div>
<div class="empty" id="empty" style="display:none"></div>
<div class="viewer" id="viewer">
  <div class="bar">
    <span class="title" id="v-title"></span>
    <a id="v-download" download>⤓</a>
    <button id="v-close">✕</button>
  </div>
  <div class="content" id="v-content"></div>
</div>
<script>
const TOKEN = __TOKEN__;
const qs = new URLSearchParams(location.search);
const qtoken = qs.get('token') || TOKEN;
const url = p => p + (p.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(qtoken);

const grid = document.getElementById('grid');
const meta = document.getElementById('meta');
const empty = document.getElementById('empty');
const sortSel = document.getElementById('sort');
const filterSel = document.getElementById('filter');
const viewer = document.getElementById('viewer');
const vTitle = document.getElementById('v-title');
const vContent = document.getElementById('v-content');
const vDl = document.getElementById('v-download');

let ALL = [];

function fmtSize(n) {
  const u = ['B','KB','MB','GB','TB'];
  let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : n) + u[i];
}

function sortFiles(arr) {
  const v = sortSel.value;
  const [key, dir] = v.split('_');
  const mul = dir === 'asc' ? 1 : -1;
  const out = arr.slice();
  if (key === 'name') {
    out.sort((a, b) => a.name.localeCompare(b.name) * mul);
  } else if (key === 'size') {
    out.sort((a, b) => (a.size - b.size) * mul);
  } else if (key === 'mtime') {
    out.sort((a, b) => (a.mtime - b.mtime) * mul);
  } else if (key === 'kind') {
    out.sort((a, b) =>
      (a.kind.localeCompare(b.kind)) || (b.mtime - a.mtime));
  }
  return out;
}

function applyFilter(arr) {
  const f = filterSel.value;
  if (f === 'all') return arr;
  return arr.filter(x => x.kind === f);
}

function render() {
  const visible = sortFiles(applyFilter(ALL));
  meta.textContent = visible.length + ' / ' + ALL.length + ' files';
  grid.innerHTML = '';
  empty.style.display = visible.length ? 'none' : 'block';
  empty.textContent = visible.length ? '' : 'No files';
  for (const f of visible) grid.appendChild(makeCell(f));
}

function makeCell(f) {
  const c = document.createElement('div');
  c.className = 'cell';

  const img = document.createElement('img');
  img.loading = 'lazy';
  img.src = url('/thumb/' + encodeURIComponent(f.path));
  img.onerror = () => {
    img.remove();
    c.appendChild(placeholder(f.kind === 'video' ? '🎞' : '🖼'));
  };
  c.appendChild(img);

  if (f.kind === 'video') {
    const badge = document.createElement('div');
    badge.className = 'badge';
    badge.textContent = 'video';
    c.appendChild(badge);
  }

  const name = document.createElement('div');
  name.className = 'name';
  name.textContent = f.name;
  c.appendChild(name);

  const dl = document.createElement('a');
  dl.className = 'dl';
  dl.textContent = '⤓';
  dl.href = url('/file/' + encodeURIComponent(f.path));
  dl.setAttribute('download', f.name);
  dl.addEventListener('click', e => e.stopPropagation());
  c.appendChild(dl);

  c.addEventListener('click', () => openViewer(f));
  return c;
}

function placeholder(emoji) {
  const p = document.createElement('div');
  p.className = 'placeholder';
  p.textContent = emoji;
  return p;
}

function openViewer(f) {
  vTitle.textContent = f.name + ' · ' + fmtSize(f.size);
  vDl.href = url('/file/' + encodeURIComponent(f.path));
  vDl.setAttribute('download', f.name);
  vContent.innerHTML = '<div class="spinner"></div>';

  if (f.kind === 'image') {
    const img = document.createElement('img');
    img.onload = () => { vContent.innerHTML = ''; vContent.appendChild(img); };
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
    v.onloadeddata = () => {
      vContent.innerHTML = '';
      v.style.visibility = 'visible';
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
    v.style.visibility = 'hidden';
    vContent.appendChild(v);
  }
  viewer.classList.add('open');
}

document.getElementById('v-close').addEventListener('click', () => {
  viewer.classList.remove('open');
  vContent.innerHTML = '';
});
viewer.addEventListener('click', e => {
  if (e.target === viewer) document.getElementById('v-close').click();
});
document.getElementById('refresh').addEventListener('click', load);
sortSel.addEventListener('change', () => {
  localStorage.setItem('gs_sort', sortSel.value);
  render();
});
filterSel.addEventListener('change', () => {
  localStorage.setItem('gs_filter', filterSel.value);
  render();
});

async function load() {
  meta.textContent = 'loading...';
  try {
    const r = await fetch(url('/api/files'));
    if (!r.ok) { meta.textContent = 'error ' + r.status; return; }
    const data = await r.json();
    ALL = data.files;
    render();
  } catch (e) {
    meta.textContent = 'error: ' + e;
  }
}

if (localStorage.getItem('gs_sort')) sortSel.value = localStorage.getItem('gs_sort');
if (localStorage.getItem('gs_filter')) filterSel.value = localStorage.getItem('gs_filter');

load();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class GalleryHandler(BaseHTTPRequestHandler):
    server_version = "GalleryServer/1.1"

    def log_message(self, fmt, *args):
        pass  # silence default stderr logging

    # ---------- helpers ----------

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
            self._serve_index()
            return
        if path == "/api/files":
            self._serve_files()
            return
        if path.startswith("/thumb/"):
            self._serve_thumb(path[len("/thumb/"):])
            return
        if path.startswith("/file/"):
            self._serve_file(path[len("/file/"):])
            return
        if path.startswith("/play/"):
            self._serve_play(path[len("/play/"):])
            return
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
        files = list_media(self.server.root)
        total = sum(f["size"] for f in files)
        self._send_json({"files": files, "total": total})

    def _serve_thumb(self, rel: str):
        if not self._check_token():
            return
        p = self._safe_path(rel)
        if p is None or not p.is_file():
            self._send_text("not found", status=404)
            return
        data = self.server.thumbs.get(p)
        if data is None:
            self._send_text("no thumbnail", status=404)
            return
        self._send_bytes(
            data, "image/jpeg",
            extra_headers={"Cache-Control": "public, max-age=86400"},
        )

    def _serve_file(self, rel: str):
        if not self._check_token():
            return
        p = self._safe_path(rel)
        if p is None or not p.is_file():
            self._send_text("not found", status=404)
            return
        ctype, _ = mimetypes.guess_type(str(p))
        if not ctype:
            ctype = "application/octet-stream"
        try:
            st = p.stat()
        except Exception:
            self._send_text("not found", status=404)
            return
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
        """Stream video for browser playback.

        Direct stream for browser-friendly formats; on-the-fly transcode
        via ffmpeg for the rest (mkv, avi, hevc, etc). Falls back to
        plain file serve if ffmpeg is missing.

        ffmpeg is force-killed when the client disconnects or on any error.
        """
        if not self._check_token():
            return
        p = self._safe_path(rel)
        if p is None or not p.is_file():
            self._send_text("not found", status=404)
            return

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
            self._send_text(f"transcode failed: {e}", status=500)
            return

        try:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            while True:
                # Read with a small timeout so we can detect a dead client
                # and kill the ffmpeg process.
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
                    # Client went away — break and kill ffmpeg below.
                    break
            try:
                self.wfile.write(b"0\r\n\r\n")
            except Exception:
                pass
        except Exception:
            pass
        finally:
            # Hard kill: terminate, then kill, then wait.
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except Exception:
                        pass
                    if proc.poll() is None:
                        proc.kill()
                        try:
                            proc.wait(timeout=1)
                        except Exception:
                            pass
            except Exception:
                pass
            # Close pipes
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass


class GalleryServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, root: Path, token: str,
                 thumbs: ThumbCache, logger):
        super().__init__(addr, handler)
        self.root = root
        self.token = token
        self.thumbs = thumbs
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
            thumbs = ThumbCache(self.root / CACHE_DIR_NAME)
            self.server = GalleryServer(
                (self.host, self.port), GalleryHandler,
                self.root, self.token, thumbs, self.log,
            )
        except OSError as e:
            self._error = e
            self._ready.set()
            return
        except Exception as e:
            self._error = e
            self._ready.set()
            return
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

        # Folder
        f_folder = ttk.LabelFrame(self.root, text=tr("folder_group"))
        f_folder.pack(fill=X, **pad)
        row = Frame(f_folder); row.pack(fill=X, padx=4, pady=4)
        Label(row, text=tr("folder_label")).pack(side=LEFT)
        Entry(row, textvariable=self.folder_var).pack(
            side=LEFT, fill=X, expand=True, padx=4)
        ttk.Button(row, text=tr("browse"), width=3,
                   command=self.pick_folder).pack(side=LEFT)

        # Server
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

        # Buttons
        f_run = Frame(self.root)
        f_run.pack(fill=X, **pad)
        self.btn_start = ttk.Button(f_run, text=tr("start"), command=self.start)
        self.btn_start.pack(side=LEFT, padx=2)
        self.btn_stop = ttk.Button(f_run, text=tr("stop"),
                                   command=self.stop, state="disabled")
        self.btn_stop.pack(side=LEFT, padx=2)
        Label(f_run, textvariable=self.status_var, fg="gray").pack(
            side=LEFT, padx=12)

        # URLs
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

        # QR
        f_qr = ttk.LabelFrame(self.root, text=tr("qr_group"))
        f_qr.pack(fill=X, **pad)
        self.qr_label = Label(f_qr, text="", bg="#111")
        self.qr_label.pack(padx=8, pady=8)

        # Log
        f_log = ttk.LabelFrame(self.root, text=tr("log_group"))
        f_log.pack(fill=BOTH, expand=True, **pad)
        self.log = Text(f_log, height=10, wrap="none")
        self.log.pack(side=LEFT, fill=BOTH, expand=True, padx=4, pady=4)
        sb = Scrollbar(f_log, command=self.log.yview)
        sb.pack(side=LEFT, fill=Y)
        self.log.config(yscrollcommand=sb.set)

        # Language
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