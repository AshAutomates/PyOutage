# 🐍 PyOutage — Power Cut Tracker

Track exactly when your power goes off and comes back on. Self-hosted, Docker-based, runs on any always-on computer (inverter-powered).

---

## How it works

```
Smart Plug (e.g. 192.168.0.111)          Your Computer (inverter-powered)
  connected to MAIN power only   ←ping every 3s─   Docker running PyOutage
  goes offline when power cuts                        logs events to SQLite
                                                      serves dashboard on :5000
```

- Poller pings your smart plug every **3 seconds**
- Requires **2 consecutive failures** before logging as power OFF (avoids false alarms)
- On status change → writes to DB + runs hook scripts
- Every **1 hour** → writes a heartbeat (proves power was stable)
- Dashboard reads event-based data — calculates uptime by duration, not row count

---

## Project structure

```
pyoutage/
├── config.json                          ← default config (plug IP, intervals)
├── docker-compose.yml
├── on_power_off/                        ← drop .py scripts here to run on power cut
│   └── example_log_hook.py.disabled
├── on_power_on/                         ← drop .py scripts here to run on restore
│   └── example_log_hook.py.disabled
├── poller/
│   ├── Dockerfile
│   ├── config.json
│   └── poller.py
└── web/
    ├── Dockerfile
    ├── app.py
    ├── config.json
    └── templates/
        └── index.html
```

---

## Setup

### Prerequisites
- Docker + Docker Compose installed
- Smart plug connected to **main power only** (not inverter) with a known IP address
- This computer connected to inverter (always on)

### 1. Set your plug IP

**Option A — Edit config before first run (recommended):**
```json
// config.json
{
  "plug_ip": "192.168.0.111",   ← change this to your plug's IP
  "ping_interval": 3,
  "ping_timeout": 1,
  "confirm_failures": 2,
  "heartbeat_seconds": 3600
}
```
Also update the same value in `poller/config.json` and `web/config.json`.

**Option B — Change from dashboard after first run:**
Open dashboard → ⚙️ Settings tab → change Plug IP → Save.
Poller reloads automatically within one ping cycle (~3 seconds). No restart needed.

### 2. Start

```bash
cd pyoutage
docker compose up -d --build
```

### 3. Open dashboard

```
http://localhost:5000
```

From any device on your local network:
```
http://<your-computer-ip>:5000
```

Find your IP:
```bash
# Linux/Mac
hostname -I | awk '{print $1}'

# Windows (PowerShell)
ipconfig
```

---

## Changing the plug IP in future

### If your smart plug gets a new IP address:

**Method 1 — Dashboard (easiest, no restart needed):**
1. Open dashboard
2. Click ⚙️ Settings tab
3. Enter new IP in "Plug IP Address"
4. Click 💾 Save Settings
5. Done — poller reloads within 3 seconds

**Method 2 — Edit config.json manually:**
1. Edit `config.json` in your repo
2. Push to GitHub
3. On Docker machine: `git pull && docker compose down && docker compose up -d --build`

---

## Dashboard features

| Tab | What you see |
|---|---|
| **Day** | 24h timeline (green=ON, red=OFF) + hourly breakdown + outage log table |
| **Month** | Per-day uptime % bar chart + best/worst day |
| **Year** | Per-month uptime % bar chart + best/worst month |
| **Settings** | Change plug IP and polling config from the browser |
| **Header** | Live power status with pulsing indicator, updates every 5s |

**Color coding:** 🟢 ≥90% · 🟡 50–89% · 🔴 <50%

---

## Hook system — integrate other Python scripts

PyOutage can run your own Python scripts automatically when power goes off or comes back on.

### How to add a hook

1. Create a `.py` file in `hooks/on_power_off/` or `hooks/on_power_on/`
2. That's it — PyOutage runs all `.py` files in those folders on each event

Files ending in `.disabled` are ignored (use this to store example scripts).

### Environment variables available in hooks

```python
import os

# Available in both on_power_off and on_power_on hooks:
event     = os.environ.get("PYOUTAGE_EVENT")           # "POWER_OFF" or "POWER_ON"
timestamp = os.environ.get("PYOUTAGE_TIMESTAMP")       # "2026-06-23 14:30:00"
unix_ts   = os.environ.get("PYOUTAGE_UNIX_TS")         # "1750000000"
plug_ip   = os.environ.get("PYOUTAGE_PLUG_IP")         # "192.168.0.111"

# Only available in on_power_on hooks:
outage_secs     = os.environ.get("PYOUTAGE_OUTAGE_SECS")      # "825"
outage_duration = os.environ.get("PYOUTAGE_OUTAGE_DURATION")  # "13m 45s"
```

### Example hook ideas

**Send Telegram notification:**
```python
# hooks/on_power_off/telegram_alert.py
import os, requests
BOT_TOKEN = "your_bot_token"
CHAT_ID   = "your_chat_id"
ts = os.environ.get("PYOUTAGE_TIMESTAMP")
requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
  json={"chat_id": CHAT_ID, "text": f"❌ Power cut at {ts}!"})
```

**Log to Google Sheet, send WhatsApp, trigger a backup, turn on a smart device via API** — all possible with a simple `.py` file dropped in the hooks folder.

---

## Deploy workflow (after making changes)

```bash
# On editing machine
git add .
git commit -m "your message"
git push

# On Docker machine
git pull
docker compose down
docker compose up -d --build   # --build required when code files changed
```

**When do you need --build?**

| Changed | Command |
|---|---|
| `docker-compose.yml` only | `docker compose down && docker compose up -d` |
| Any `.py` or `.html` file | `docker compose down && docker compose up -d --build` |
| Plug IP via Settings tab | Nothing — auto-reloads |

---

## Useful commands

```bash
# Start
docker compose up -d --build

# Stop
docker compose down

# Live poller logs
docker logs -f pyoutage-poller

# Live web logs
docker logs -f pyoutage-web

# Status of containers
docker compose ps

# Backup database
docker run --rm \
  -v pyoutage-data:/data \
  -v $(pwd):/backup \
  alpine cp /data/pyoutage.db /backup/pyoutage-backup.db
```

---

## Database schema

```sql
-- Sparse log: only status changes + hourly heartbeats (~35-50 rows/day)
ping_log (id, timestamp, status, event_type)
-- event_type: ON | OFF | HEARTBEAT | POLLER_START

-- One row per outage event
power_events (id, outage_start, outage_end, duration_seconds, restored)
```

Uptime % is calculated from **duration**, not row count — accurate even with sparse logging.

---

## Windows Firewall (if other devices can't reach dashboard)

```powershell
# Run as Administrator
netsh advfirewall firewall add rule name="PyOutage" dir=in action=allow protocol=TCP localport=5000
```
