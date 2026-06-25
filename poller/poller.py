import sqlite3
import time
import subprocess
import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
DB_PATH      = os.environ.get("DB_PATH",      "/data/pyoutage.db")
CONFIG_PATH  = os.environ.get("CONFIG_PATH",  "/data/config.json")
HOOKS_DIR    = os.environ.get("HOOKS_DIR",    "/hooks")
DEFAULT_CFG  = "/app/config.json"   # bundled default inside container

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PyOutage] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────
def load_config() -> dict:
    """Load config from /data/config.json (user) or fall back to bundled default."""
    for path in [CONFIG_PATH, DEFAULT_CFG]:
        if Path(path).exists():
            with open(path) as f:
                cfg = json.load(f)
                log.info(f"Config loaded from {path}")
                return cfg
    # hardcoded fallback
    return {
        "lookout_ip":           "192.168.0.111",
        "ping_interval":     3,
        "ping_timeout":      1,
        "confirm_failures":  2,
        "heartbeat_seconds": 3600
    }


# ── DB ────────────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ping_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  INTEGER NOT NULL,
            status     INTEGER NOT NULL,
            event_type TEXT    NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pl_ts ON ping_log(timestamp)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS power_events (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            outage_start     INTEGER NOT NULL,
            outage_end       INTEGER,
            duration_seconds INTEGER,
            restored         INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pe_start ON power_events(outage_start)")

    conn.commit()
    conn.close()
    log.info(f"Database ready at {DB_PATH}")


# ── Ping ──────────────────────────────────────────────────────────────────────
def ping(ip: str, timeout: int) -> bool:
    try:
        r = subprocess.run(
            ["ping", "-c", "1", f"-W{timeout}", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 1
        )
        return r.returncode == 0
    except Exception:
        return False


# ── DB helpers ────────────────────────────────────────────────────────────────
def write_ping_log(conn, ts, status, event_type):
    conn.execute(
        "INSERT INTO ping_log (timestamp, status, event_type) VALUES (?,?,?)",
        (ts, status, event_type)
    )

def open_outage(conn, ts):
    conn.execute(
        "INSERT INTO power_events (outage_start, outage_end, duration_seconds, restored) VALUES (?,NULL,NULL,0)",
        (ts,)
    )
    log.info(f"❌ Power OFF detected at {fmt_ts(ts)}")

def close_outage(conn, ts) -> int:
    """Close open outage, return duration in seconds."""
    row = conn.execute(
        "SELECT id, outage_start FROM power_events WHERE restored=0 ORDER BY outage_start DESC LIMIT 1"
    ).fetchone()
    if row:
        duration = ts - row[1]
        conn.execute(
            "UPDATE power_events SET outage_end=?, duration_seconds=?, restored=1 WHERE id=?",
            (ts, duration, row[0])
        )
        log.info(f"✅ Power ON restored at {fmt_ts(ts)} — outage lasted {fmt_dur(duration)}")
        return duration
    return 0


# ── Hooks ─────────────────────────────────────────────────────────────────────
def run_hooks(folder: str, env: dict):
    """Run all .py files in hooks/<folder>/ with given env vars."""
    hooks_path = Path(HOOKS_DIR) / folder
    if not hooks_path.exists():
        return

    scripts = sorted(hooks_path.glob("*.py"))
    if not scripts:
        return

    log.info(f"Running {len(scripts)} hook(s) from {hooks_path}")
    full_env = {**os.environ, **{k: str(v) for k, v in env.items()}}

    for script in scripts:
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                env=full_env,
                timeout=30,
                capture_output=True,
                text=True
            )
            if result.stdout:
                log.info(f"  [{script.name}] {result.stdout.strip()}")
            if result.returncode != 0:
                log.warning(f"  [{script.name}] exited with code {result.returncode}: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            log.warning(f"  [{script.name}] timed out after 30s")
        except Exception as e:
            log.warning(f"  [{script.name}] error: {e}")


# ── Formatters ────────────────────────────────────────────────────────────────
def fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

def fmt_dur(secs):
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h: return f"{h}h {m}m"
    if m: return f"{m}m {s}s"
    return f"{s}s"


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    init_db()
    cfg = load_config()

    LOOKOUT_IP           = cfg["lookout_ip"]
    PING_INTERVAL     = cfg["ping_interval"]
    PING_TIMEOUT      = cfg["ping_timeout"]
    CONFIRM_FAILURES  = cfg["confirm_failures"]
    HEARTBEAT_SECS    = cfg["heartbeat_seconds"]

    log.info(f"Poller started")
    log.info(f"  Lookout Device IP       : {LOOKOUT_IP}")
    log.info(f"  Ping interval   : {PING_INTERVAL}s")
    log.info(f"  Ping timeout    : {PING_TIMEOUT}s")
    log.info(f"  Confirm failures: {CONFIRM_FAILURES} consecutive")
    log.info(f"  Heartbeat every : {HEARTBEAT_SECS}s")

    # Record startup
    ts = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    initial = 1 if ping(LOOKOUT_IP, PING_TIMEOUT) else 0
    write_ping_log(conn, ts, initial, "POLLER_START")
    conn.commit()
    conn.close()

    last_status      = initial
    last_heartbeat   = ts
    fail_streak      = 0   # consecutive ping failures counter
    last_cfg_mtime   = 0   # to detect config file changes

    while True:
        # Reload config if file changed
        cfg_file = Path(CONFIG_PATH)
        if cfg_file.exists():
            mtime = cfg_file.stat().st_mtime
            if mtime != last_cfg_mtime:
                cfg              = load_config()
                LOOKOUT_IP          = cfg["lookout_ip"]
                PING_INTERVAL    = cfg["ping_interval"]
                PING_TIMEOUT     = cfg["ping_timeout"]
                CONFIRM_FAILURES = cfg["confirm_failures"]
                HEARTBEAT_SECS   = cfg["heartbeat_seconds"]
                last_cfg_mtime   = mtime
                log.info(f"Config reloaded — new Lookout Device IP: {LOOKOUT_IP}")

        ts    = int(time.time())
        is_up = ping(LOOKOUT_IP, PING_TIMEOUT)

        # ── Failure confirmation logic ──────────────────────────────────────
        # Only mark as OFF after N consecutive failures
        if not is_up:
            fail_streak += 1
        else:
            fail_streak = 0

        # Determine confirmed status
        if fail_streak >= CONFIRM_FAILURES:
            confirmed_status = 0
        elif fail_streak == 0:
            confirmed_status = 1
        else:
            # Still accumulating failures — don't change status yet
            time.sleep(PING_INTERVAL)
            continue

        conn = sqlite3.connect(DB_PATH)

        if confirmed_status != last_status:
            # ── Status changed ──────────────────────────────────────────────
            event_type = "ON" if confirmed_status else "OFF"
            write_ping_log(conn, ts, confirmed_status, event_type)

            base_env = {
                "PYOUTAGE_EVENT":     f"POWER_{event_type}",
                "PYOUTAGE_TIMESTAMP": fmt_ts(ts),
                "PYOUTAGE_UNIX_TS":   ts,
                "PYOUTAGE_LOOKOUT_IP":   LOOKOUT_IP,
            }

            if confirmed_status == 0:
                open_outage(conn, ts)
                conn.commit()
                conn.close()
                run_hooks("on_power_off", base_env)
            else:
                duration = close_outage(conn, ts)
                conn.commit()
                conn.close()
                run_hooks("on_power_on", {
                    **base_env,
                    "PYOUTAGE_OUTAGE_SECS":     duration,
                    "PYOUTAGE_OUTAGE_DURATION": fmt_dur(duration),
                })

            last_status    = confirmed_status
            last_heartbeat = ts
            fail_streak    = 0

        else:
            # ── No change — check heartbeat ─────────────────────────────────
            if ts - last_heartbeat >= HEARTBEAT_SECS:
                write_ping_log(conn, ts, confirmed_status, "HEARTBEAT")
                last_heartbeat = ts
                log.info(f"💓 Heartbeat — Power {'ON' if confirmed_status else 'OFF'}")

            conn.commit()
            conn.close()

        time.sleep(PING_INTERVAL)


if __name__ == "__main__":
    main()
