import os
import sqlite3
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

DB_PATH       = Path("/home/david/freerange-hmi/transfers.db")
PI4_1_HOST    = "david@192.168.3.41"
REMOTE_BASE   = "/home/david/freerange"
PORT          = 8420

CAMERA_DIRS = {
    "insta360-x2": ["DCIM/Camera01", "DCIM"],
    "pi-zero":     ["recordings"],
    "generic":     [""],
}

VIDEO_EXTS  = {".mp4", ".insv", ".mov", ".h264", ".mkv"}
PHOTO_EXTS  = {".jpg", ".jpeg", ".insp", ".png", ".dng"}
IGNORE_EXTS = {".lrv", ".thm"}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

transfer_log: list[str] = []
transfer_running = False


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transfers (
                id         INTEGER PRIMARY KEY,
                filename   TEXT NOT NULL,
                size       INTEGER,
                camera     TEXT,
                remote_path TEXT,
                transferred_at TEXT NOT NULL
            )
        """)
        conn.commit()


def mark_transferred(filename: str, size: int, camera: str, remote_path: str):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO transfers (filename, size, camera, remote_path, transferred_at)
            VALUES (?, ?, ?, ?, ?)
        """, (filename, size, camera, remote_path, datetime.now().isoformat()))
        conn.commit()


def already_transferred(filename: str, size: int) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM transfers WHERE filename = ? AND size = ?",
            (filename, size)
        ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def scan_source(source_path: str, camera: str) -> list[dict]:
    base = Path(source_path)
    if not base.exists():
        return []

    search_dirs = CAMERA_DIRS.get(camera, [""])
    files = []
    seen = set()

    for sub in search_dirs:
        search_root = base / sub if sub else base
        if not search_root.exists():
            continue
        for f in search_root.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() in IGNORE_EXTS:
                continue
            if f.name.upper().startswith("LRV_"):
                continue
            if f.suffix.lower() not in VIDEO_EXTS and f.suffix.lower() not in PHOTO_EXTS:
                continue
            if f.name in seen:
                continue
            seen.add(f.name)
            size = f.stat().st_size
            files.append({
                "name":        f.name,
                "path":        str(f),
                "size":        size,
                "size_mb":     round(size / 1024 / 1024, 1),
                "type":        "video" if f.suffix.lower() in VIDEO_EXTS else "photo",
                "transferred": already_transferred(f.name, size),
            })

    files.sort(key=lambda x: x["name"])
    return files


# ---------------------------------------------------------------------------
# Transfer
# ---------------------------------------------------------------------------

def do_transfer(files: list[dict], camera: str):
    global transfer_running
    transfer_running = True
    transfer_log.clear()

    remote_dir = f"{REMOTE_BASE}/{camera}/{datetime.now().strftime('%Y-%m-%d')}"
    transfer_log.append(f"Target: {PI4_1_HOST}:{remote_dir}")

    # Ensure remote dir exists
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", PI4_1_HOST, f"mkdir -p {remote_dir}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        transfer_log.append(f"ERROR creating remote dir: {result.stderr.strip()}")
        transfer_running = False
        return

    for f in files:
        if f.get("transferred"):
            transfer_log.append(f"SKIP {f['name']} (already transferred)")
            continue

        transfer_log.append(f"Sending {f['name']} ({f['size_mb']} MB)...")
        result = subprocess.run(
            ["rsync", "-av", "--progress",
             "-e", "ssh -o StrictHostKeyChecking=no",
             f["path"], f"{PI4_1_HOST}:{remote_dir}/"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            mark_transferred(f["name"], f["size"], camera, f"{remote_dir}/{f['name']}")
            transfer_log.append(f"OK {f['name']}")
        else:
            transfer_log.append(f"FAIL {f['name']}: {result.stderr.strip()}")

    transfer_log.append("Done.")
    transfer_running = False


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/scan")
def api_scan(source: str = Query(""), camera: str = Query("insta360-x2")):
    if not source:
        return JSONResponse({"error": "no source path"}, status_code=400)
    files = scan_source(source, camera)
    new_count   = sum(1 for f in files if not f["transferred"])
    total_mb    = sum(f["size_mb"] for f in files if not f["transferred"])
    return {"files": files, "new": new_count, "total_mb": round(total_mb, 1)}


@app.post("/api/transfer")
async def api_transfer(payload: dict):
    global transfer_running
    if transfer_running:
        return JSONResponse({"error": "transfer already running"}, status_code=409)
    files  = payload.get("files", [])
    camera = payload.get("camera", "insta360-x2")
    threading.Thread(target=do_transfer, args=(files, camera), daemon=True).start()
    return {"status": "started"}


@app.get("/api/log")
def api_log():
    return {"log": list(transfer_log), "running": transfer_running}


@app.get("/api/history")
def api_history(limit: int = 50):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM transfers ORDER BY transferred_at DESC LIMIT ?
        """, (limit,)).fetchall()
    return {"transfers": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Freerange</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #f5f5f5; color: #1a1a1a; font-family: system-ui, sans-serif; font-size: 0.9rem; }

  header {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding: 14px 20px; background: #fff; border-bottom: 1px solid #e0e0e0;
    position: sticky; top: 0; z-index: 10;
  }
  header h1 { font-size: 1rem; font-weight: 600; color: #111; }

  select, input[type=text] {
    padding: 7px 12px; border-radius: 5px; border: 1px solid #d0d0d0;
    background: #fff; color: #1a1a1a; font-size: 0.85rem; outline: none;
  }
  select:focus, input[type=text]:focus { border-color: #888; }

  #source-input { flex: 1; min-width: 200px; max-width: 360px; }

  button {
    padding: 7px 14px; border-radius: 5px; border: 1px solid #d0d0d0;
    background: #fff; color: #444; font-size: 0.85rem; cursor: pointer;
  }
  button:hover { background: #f0f0f0; color: #111; }
  button.primary { background: #2a7a3a; border-color: #1e6030; color: #fff; }
  button.primary:hover { background: #236832; }
  button:disabled { opacity: 0.4; cursor: not-allowed; }

  #stats { font-size: 0.75rem; color: #888; margin-left: auto; white-space: nowrap; }

  main { padding: 16px 20px; max-width: 1000px; }

  #file-list { margin-top: 16px; }

  .controls { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; }
  .controls span { font-size: 0.75rem; color: #888; }

  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 6px;
    overflow: hidden; border: 1px solid #e8e8e8; }
  th { text-align: left; padding: 8px 12px; font-size: 0.75rem; color: #888;
    border-bottom: 1px solid #e8e8e8; font-weight: 500; background: #fafafa; }
  td { padding: 8px 12px; border-bottom: 1px solid #f0f0f0; font-size: 0.82rem; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #fafafa; }
  tr.done td { opacity: 0.4; }

  .badge { display: inline-block; padding: 2px 7px; border-radius: 3px;
    font-size: 0.7rem; font-weight: 600; }
  .badge.video { background: #dbeafe; color: #1d4ed8; }
  .badge.photo { background: #ede9fe; color: #6d28d9; }
  .badge.done  { background: #dcfce7; color: #166534; }
  .badge.new   { background: #fef9c3; color: #854d0e; }

  #log-panel {
    margin-top: 20px; background: #1a1a1a; border: 1px solid #e0e0e0; border-radius: 6px;
    padding: 12px; font-family: monospace; font-size: 0.78rem; color: #aaa;
    max-height: 200px; overflow-y: auto; display: none;
  }
  #log-panel.active { display: block; }
  #log-panel .ok   { color: #4ade80; }
  #log-panel .fail { color: #f87171; }
  #log-panel .info { color: #aaa; }

  #history-section { margin-top: 28px; }
  #history-section h2 { font-size: 0.85rem; color: #888; margin-bottom: 10px; font-weight: 500; }

  #empty { text-align: center; color: #bbb; padding: 48px 0; }
</style>
</head>
<body>

<header>
  <h1>Freerange</h1>
  <select id="camera-select" onchange="onCameraChange()">
    <option value="insta360-x2">Insta360 X2</option>
    <option value="pi-zero">Pi Zero</option>
    <option value="generic">Generic</option>
  </select>
  <input id="source-input" type="text" placeholder="SD card path, e.g. /mnt/h" value="/mnt/h">
  <button onclick="scan()">Scan</button>
  <button id="transfer-btn" class="primary" onclick="transfer()" disabled>Transfer New</button>
  <span id="stats"></span>
</header>

<main>
  <div id="file-list" style="display:none">
    <div class="controls">
      <button onclick="selectAll(true)" style="font-size:0.75rem;padding:4px 8px">Select all</button>
      <button onclick="selectAll(false)" style="font-size:0.75rem;padding:4px 8px">Deselect all</button>
      <span id="sel-count"></span>
    </div>
    <table>
      <thead>
        <tr>
          <th style="width:32px"></th>
          <th>Filename</th>
          <th>Type</th>
          <th>Size</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody id="file-tbody"></tbody>
    </table>
  </div>

  <div id="empty">Insert SD card and click Scan</div>

  <div id="log-panel"></div>

  <div id="history-section" style="display:none">
    <h2>Transfer History</h2>
    <table>
      <thead>
        <tr><th>Filename</th><th>Camera</th><th>Size</th><th>Destination</th><th>Date</th></tr>
      </thead>
      <tbody id="history-tbody"></tbody>
    </table>
  </div>
</main>

<script>
let scannedFiles = [];
let logTimer = null;

function onCameraChange() {
  scannedFiles = [];
  document.getElementById("file-list").style.display = "none";
  document.getElementById("empty").textContent = "Insert SD card and click Scan";
  document.getElementById("empty").style.display = "block";
  document.getElementById("transfer-btn").disabled = true;
  document.getElementById("stats").textContent = "";
}

async function scan() {
  const source = document.getElementById("source-input").value.trim();
  const camera = document.getElementById("camera-select").value;
  document.getElementById("stats").textContent = "Scanning…";
  document.getElementById("empty").style.display = "none";

  const res = await fetch(`/api/scan?source=${encodeURIComponent(source)}&camera=${encodeURIComponent(camera)}`);
  const data = await res.json();

  if (data.error) {
    document.getElementById("stats").textContent = data.error;
    return;
  }

  scannedFiles = data.files;
  renderFiles();
  document.getElementById("stats").textContent =
    `${data.new} new · ${data.total_mb} MB`;
  document.getElementById("transfer-btn").disabled = data.new === 0;
  loadHistory();
}

function renderFiles() {
  const tbody = document.getElementById("file-tbody");
  if (scannedFiles.length === 0) {
    document.getElementById("empty").textContent = "No media files found on SD card.";
    document.getElementById("empty").style.display = "block";
    document.getElementById("file-list").style.display = "none";
    return;
  }

  document.getElementById("empty").style.display = "none";
  document.getElementById("file-list").style.display = "block";

  tbody.innerHTML = scannedFiles.map((f, i) => `
    <tr class="${f.transferred ? 'done' : ''}" id="row-${i}">
      <td><input type="checkbox" data-idx="${i}" ${!f.transferred ? 'checked' : ''} ${f.transferred ? 'disabled' : ''}></td>
      <td style="font-family:monospace">${f.name}</td>
      <td><span class="badge ${f.type}">${f.type}</span></td>
      <td>${f.size_mb} MB</td>
      <td><span class="badge ${f.transferred ? 'done' : 'new'}">${f.transferred ? 'done' : 'new'}</span></td>
    </tr>
  `).join("");

  updateSelCount();
  tbody.querySelectorAll("input[type=checkbox]").forEach(cb =>
    cb.addEventListener("change", updateSelCount)
  );
}

function updateSelCount() {
  const checked = document.querySelectorAll("#file-tbody input:checked").length;
  document.getElementById("sel-count").textContent = `${checked} selected`;
}

function selectAll(state) {
  document.querySelectorAll("#file-tbody input[type=checkbox]:not(:disabled)").forEach(cb => cb.checked = state);
  updateSelCount();
}

function getSelected() {
  const selected = [];
  document.querySelectorAll("#file-tbody input[type=checkbox]:checked").forEach(cb => {
    selected.push(scannedFiles[parseInt(cb.dataset.idx)]);
  });
  return selected;
}

async function transfer() {
  const files = getSelected();
  if (!files.length) return;

  const camera = document.getElementById("camera-select").value;
  document.getElementById("transfer-btn").disabled = true;

  const res = await fetch("/api/transfer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files, camera })
  });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }

  const panel = document.getElementById("log-panel");
  panel.className = "active";
  panel.innerHTML = "";
  pollLog();
}

function pollLog() {
  logTimer = setInterval(async () => {
    const res  = await fetch("/api/log");
    const data = await res.json();
    const panel = document.getElementById("log-panel");
    panel.innerHTML = data.log.map(line => {
      const cls = line.startsWith("OK") ? "ok" : line.startsWith("FAIL") ? "fail" : "info";
      return `<div class="${cls}">${line}</div>`;
    }).join("");
    panel.scrollTop = panel.scrollHeight;

    if (!data.running) {
      clearInterval(logTimer);
      document.getElementById("transfer-btn").disabled = false;
      scan();
    }
  }, 1000);
}

async function loadHistory() {
  const res  = await fetch("/api/history");
  const data = await res.json();
  if (!data.transfers.length) return;

  document.getElementById("history-section").style.display = "block";
  document.getElementById("history-tbody").innerHTML = data.transfers.map(t => `
    <tr>
      <td style="font-family:monospace">${t.filename}</td>
      <td>${t.camera}</td>
      <td>${(t.size / 1024 / 1024).toFixed(1)} MB</td>
      <td style="font-family:monospace;font-size:0.7rem">${t.remote_path}</td>
      <td>${t.transferred_at.slice(0, 16).replace("T", " ")}</td>
    </tr>
  `).join("");
}

loadHistory();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
