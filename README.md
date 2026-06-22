# 🐍 PyOutage — Power Cut Tracker

Track exactly when your power goes off and comes back, with a live dashboard showing daily, monthly, and yearly uptime stats.

---

## How it works

```
Smart Plug (192.168.0.111)          Your Computer (inverter-powered)
  connected to MAIN power   ←ping every 5s─   Docker running PyOutage
  goes offline when cut                          logs status to SQLite
                                                 serves dashboard on :5000
```

- **Poller** pings your smart plug every 5 seconds
- If ping fails → power is OFF → logs `0`
- If ping succeeds → power is ON → logs `1`
- **Dashboard** reads the logs and shows timeline + uptime %

---

## Project structure

```
pyoutage/
├── docker-compose.yml
├── poller/
│   ├── Dockerfile
│   └── poller.py          # pings plug, writes to SQLite
└── web/
    ├── Dockerfile
    ├── app.py             # Flask API + page
    └── templates/
        └── index.html     # Dashboard UI
```

---

## Setup

### 1. Prerequisites

- Docker + Docker Compose installed on your inverter-powered computer
- Smart plug connected to **main power** (not inverter) at `192.168.0.111`

### 2. Configure

Edit `docker-compose.yml` and set your smart plug IP if different:

```yaml
environment:
  PLUG_IP: "192.168.0.111"    # ← change this if needed
  PING_INTERVAL: "5"          # ← seconds between pings (5 recommended)
```

### 3. Build and start

```bash
cd pyoutage
docker compose up -d --build
```

### 4. Open dashboard

```
http://localhost:5000
```

Or from any device on your LAN:
```
http://<your-computer-ip>:5000
```

To find your computer's IP:
```bash
hostname -I | awk '{print $1}'
```

---

## Dashboard features

| Tab   | What you see |
|-------|-------------|
| **Day**   | 24-hour timeline bar (green=ON, red=OFF) + hourly breakdown + outage count |
| **Month** | Per-day uptime % bar chart + best/worst day |
| **Year**  | Per-month uptime % bar chart + best/worst month |
| **Live**  | Current power status in the header, updates every 5s |

---

## Commands

```bash
# Start
docker compose up -d

# Stop
docker compose down

# View poller logs (live)
docker logs -f pyoutage-poller

# View web logs
docker logs -f pyoutage-web

# Restart everything
docker compose restart

# Check status
docker compose ps
```

---

## Notes

- The SQLite database is stored in a Docker volume (`pyoutage-data`) and **persists across restarts**
- The poller uses `network_mode: host` so it can reach your LAN IP directly
- Smart plug takes ~4s to boot after power returns — a 5s ping interval catches it on the next cycle
- Color coding on charts: 🟢 ≥90% · 🟡 50–89% · 🔴 <50%

---

## Backup the database

```bash
docker run --rm \
  -v pyoutage-data:/data \
  -v $(pwd):/backup \
  alpine cp /data/pyoutage.db /backup/pyoutage-backup.db
```
