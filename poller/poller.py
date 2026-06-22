import sqlite3
import time
import subprocess
import os
import logging
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
PLUG_IP       = os.environ.get("PLUG_IP", "192.168.0.111")
PING_INTERVAL = int(os.environ.get("PING_INTERVAL", "5"))   # seconds
DB_PATH       = os.environ.get("DB_PATH", "/data/pyoutage.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PyOutage] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)


# ── DB init ───────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ping_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,   -- Unix epoch (seconds)
            status    INTEGER NOT NULL    -- 1 = power ON, 0 = power OFF
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON ping_log(timestamp)")
    conn.commit()
    conn.close()
    log.info(f"Database ready at {DB_PATH}")


# ── Ping ──────────────────────────────────────────────────────────────────────
def ping(ip: str) -> bool:
    """Returns True if the host responds to a single ICMP ping."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3
        )
        return result.returncode == 0
    except Exception:
        return False


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    init_db()
    log.info(f"Poller started — target: {PLUG_IP}  interval: {PING_INTERVAL}s")

    last_status = None

    while True:
        ts     = int(time.time())
        is_up  = ping(PLUG_IP)
        status = 1 if is_up else 0

        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO ping_log (timestamp, status) VALUES (?, ?)",
            (ts, status)
        )
        conn.commit()
        conn.close()

        # Log to console only on state change
        if status != last_status:
            if last_status is not None:          # skip the very first reading
                label = "✅ Power ON" if status else "❌ Power OFF"
                log.info(f"Status changed → {label}")
            last_status = status

        time.sleep(PING_INTERVAL)


if __name__ == "__main__":
    main()
