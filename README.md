# freerange-hmi

A minimal web app for offloading camera SD cards to a local server without any cloud services or proprietary software.

Insert the card, scan, transfer. Files land on your server organised by camera and date. Already-transferred files are tracked in SQLite so re-inserting the same card skips duplicates automatically.

## How it works

- Reads directly from the SD card mount point (e.g. `/mnt/h` on WSL)
- Transfers via rsync over SSH to a target server
- Stores transfer history in a local SQLite database
- Runs as a systemd service, accessible at `http://localhost:8420`

## File layout on the server

```
/home/david/freerange/
├── insta360-x2/
│   └── 2026-08-04/
│       ├── VID_20260804_112005_00_022.insv
│       └── ...
├── pi-zero/
└── ...
```

## Supported cameras

| Camera | Source path on card |
|---|---|
| Insta360 X2 | `DCIM/Camera01/` |
| Pi Zero | `recordings/` |
| Generic | root of card |

Insta360 X2 LRV preview files (`LRV_*.insv`) are filtered out automatically.

## Setup

**Prerequisites:** Python 3, FastAPI, uvicorn, rsync, SSH key auth to target server.

```bash
pip install fastapi uvicorn
```

Create the destination directory on your server:

```bash
ssh user@server "mkdir -p /home/david/freerange"
```

Run the app:

```bash
python3 main.py
```

Or install as a systemd service:

```ini
[Unit]
Description=Freerange Camera File Manager
After=network.target

[Service]
Type=simple
User=david
WorkingDirectory=/home/david/freerange-hmi
ExecStart=/path/to/python3 main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## Configuration

Edit the top of `main.py`:

```python
PI4_1_HOST  = "david@192.168.3.41"   # SSH target
REMOTE_BASE = "/home/david/freerange" # Base path on server
PORT        = 8420
```

## Usage

1. Insert SD card — on WSL, mount it: `sudo mkdir -p /mnt/h && sudo mount -t drvfs H: /mnt/h`
2. Open `http://localhost:8420`
3. Select camera type and set source path
4. Click **Scan**
5. Click **Transfer New**
