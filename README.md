# Gallery Server

Short-lived local web server to browse photos and videos from your phone
(or any device in the LAN). Serves a chosen folder recursively with
thumbnails, full files, and download. Protected by a token in the URL.

## Features

- **Recursive media listing** — picks up images and videos in all subfolders
- **Thumbnails** — auto-generated with Pillow, cached on disk
- **Full preview** — click any item to view the original (images) or play the video
- **Download** — every file has a direct download link
- **Token in URL** — `?token=…` required, 403 without it
- **Range requests** — video seeking works (206 Partial Content)
- **QR code** — scan with your phone to open the gallery instantly
- **Local + LAN URLs** — click to copy
- **System tray** — minimize and keep running in background
- **YAML config** — folder, host, port, token saved between runs
- **English / Russian UI**

## Requirements

- Python 3.10+
- Tkinter (bundled with most Python installs)

## Install

- Linux / macOS
```bash
git clone https://github.com/sameaslooks/gallery-server.git
cd gallery-server
python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
chmod +x run.sh
./run.sh
```

- Windows
```bash
git clone https://github.com/sameaslooks/gallery-server.git
cd gallery-server
python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
run.bat
```

## Usage

1. Pick a **folder** to serve.
2. Set **host** (`0.0.0.0` for LAN access) and **port** (default `8765`).
3. Token is auto-generated — regenerate if needed.
4. Press **START**.
5. Scan the **QR** with your phone, or open the **LAN URL** in a browser.
6. Press **STOP** (or close the app) to shut the server down.

## How it works

- Serves `http://<host>:<port>/` with a single-page gallery.
- All API and file endpoints require `?token=<your-token>`.
- Thumbnails are cached under `<folder>/.gallery_cache/`.
- Video streaming uses HTTP Range — seeking works in browsers.

## Security

- **Token is required** for every request; without it the server returns 403.
- **Local-only by default**: bind to `0.0.0.0` only when you need LAN access.
- **Short-lived**: server runs only while the app is open.
- **No TLS**: intended for trusted LANs. Don't expose to the internet.

## Config

`gallery-server-config.yaml` is saved next to the script:

```yaml
folder: D:\Photos
host: 0.0.0.0
port: 8765
token: eK3xQ9pLm2vN8bRt
language: en
```

## License

GPLv3 — see [LICENSE](LICENSE).