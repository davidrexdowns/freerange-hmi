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

## Transfer protocols

| Leg | Protocol | Detail |
|---|---|---|
| SD card → WSL | None (local filesystem) | Windows exposes the drive to WSL via `drvfs`, a virtual filesystem driver built into WSL2. Files are read locally — no network involved. |
| WSL → pi4-1 | SSH + rsync over SSH | SSH (port 22) opens the connection and creates the remote directory. rsync handles the file list and transfer over the same encrypted SSH tunnel. |

No data touches the internet at any point. The only network hop is the encrypted SSH connection between your local machine and your local server.

## WSL — persistent SD card mount

By default WSL doesn't automount drives that appear after startup. To make `/mnt/h` persist across sessions, create a systemd mount unit:

```bash
sudo mkdir -p /mnt/h

sudo tee /etc/systemd/system/mnt-h.mount > /dev/null << 'EOF'
[Unit]
Description=Mount Windows H: drive
After=network.target

[Mount]
What=H:
Where=/mnt/h
Type=drvfs
Options=metadata,uid=1000,gid=1000

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable mnt-h.mount
sudo systemctl start mnt-h.mount
```

Requires `systemd=true` in `/etc/wsl.conf`. The SD card must be assigned H: in Windows before WSL starts.

## Usage

1. Insert SD card — ensure it is assigned H: in Windows
2. Open `http://localhost:8420`
3. Select camera type and set source path to `/mnt/h`
4. Click **Scan**
5. Click **Transfer New**
