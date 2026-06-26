from flask import Flask, render_template, jsonify, request
import sqlite3
import os
import json
import calendar
from datetime import datetime, date
from pathlib import Path

app = Flask(__name__)

DB_PATH     = os.environ.get("DB_PATH",     "/data/pyoutage.db")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/data/config.json")
DEFAULT_CFG = "/app/config.json"


# ── Config helpers ────────────────────────────────────────────────────────────

def load_config():
    for path in [CONFIG_PATH, DEFAULT_CFG]:
        if Path(path).exists():
            with open(path) as f:
                cfg = json.load(f)
                # backward compatibility — rename plug_ip to lookout_ip
                if "plug_ip" in cfg and "lookout_ip" not in cfg:
                    cfg["lookout_ip"] = cfg.pop("plug_ip")
                return cfg
    return {
        "lookout_ip": "192.168.0.111",
        "ping_interval": 3,
        "ping_timeout": 1,
        "confirm_failures": 2,
        "heartbeat_seconds": 3600
    }

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


# ── DB helper ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def has_any_data(conn, start_ts, end_ts):
    row = conn.execute(
        "SELECT COUNT(*) as c FROM ping_log WHERE timestamp BETWEEN ? AND ?",
        (start_ts, end_ts)
    ).fetchone()
    return row["c"] > 0

def day_bounds(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (
        int(d.replace(hour=0,  minute=0,  second=0).timestamp()),
        int(d.replace(hour=23, minute=59, second=59).timestamp())
    )

def get_outages_in_range(conn, start_ts, end_ts):
    return conn.execute("""
        SELECT outage_start, outage_end, duration_seconds, restored
        FROM power_events
        WHERE outage_start <= ? AND (outage_end >= ? OR outage_end IS NULL)
        ORDER BY outage_start
    """, (end_ts, start_ts)).fetchall()

def outage_seconds_in_window(outages, start_ts, end_ts):
    now = int(datetime.now().timestamp())
    end_ts = min(end_ts, now)   # never count future time
    if end_ts <= start_ts:
        return 0
    total = 0
    for o in outages:
        s = max(o["outage_start"], start_ts)
        e = min(o["outage_end"] if o["outage_end"] else now, end_ts)
        if e > s:
            total += e - s
    return total

def compute_uptime(off_secs, start_ts, end_ts):
    now = int(datetime.now().timestamp())
    end_ts = min(end_ts, now)   # cap to now — no future time
    window = end_ts - start_ts
    if window <= 0:
        return None             # window hasn't started yet
    return round(((window - min(off_secs, window)) / window) * 100, 2)

def build_day_segments(outages, day_start, day_end):
    now     = int(datetime.now().timestamp())
    day_end = min(day_end, now)   # don't draw future time
    if day_end <= day_start:
        return []
    if not outages:
        return [{"start": day_start, "end": day_end, "status": 1}]
    segments = []
    cursor = day_start
    for o in outages:
        if o["start"] > cursor:
            segments.append({"start": cursor, "end": o["start"], "status": 1})
        segments.append({"start": o["start"], "end": min(o["end"], day_end), "status": 0})
        cursor = min(o["end"], day_end)
        if cursor >= day_end:
            break
    if cursor < day_end:
        segments.append({"start": cursor, "end": day_end, "status": 1})
    return segments

def fmt_dur(secs):
    if not secs:
        return "—"
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h: return f"{h}h {m}m"
    if m: return f"{m}m {s}s"
    return f"{s}s"


# ── API: Settings ─────────────────────────────────────────────────────────────

@app.route("/api/config", methods=["GET"])
def api_config_get():
    return jsonify(load_config())

@app.route("/api/config", methods=["POST"])
def api_config_post():
    try:
        new_cfg = request.get_json()
        # Validate
        required = ["lookout_ip", "ping_interval", "ping_timeout", "confirm_failures", "heartbeat_seconds"]
        for k in required:
            if k not in new_cfg:
                return jsonify({"error": f"Missing field: {k}"}), 400

        new_cfg["ping_interval"]     = int(new_cfg["ping_interval"])
        new_cfg["ping_timeout"]      = int(new_cfg["ping_timeout"])
        new_cfg["confirm_failures"]  = int(new_cfg["confirm_failures"])
        new_cfg["heartbeat_seconds"] = int(new_cfg["heartbeat_seconds"])

        save_config(new_cfg)
        return jsonify({"ok": True, "config": new_cfg})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── API: Live status ──────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    try:
        conn = get_db()

        # Check for ongoing outage first
        ongoing = conn.execute(
            "SELECT outage_start FROM power_events WHERE restored=0 ORDER BY outage_start DESC LIMIT 1"
        ).fetchone()

        if ongoing:
            # Power is OFF — since the outage started
            conn.close()
            return jsonify({
                "status":         "OFF",
                "since":          datetime.fromtimestamp(ongoing["outage_start"]).isoformat(),
                "ts":             ongoing["outage_start"],
                "ongoing_outage": True
            })

        # Power is ON — since the last restore event (or poller start if no outages)
        last_restore = conn.execute(
            "SELECT outage_end FROM power_events WHERE restored=1 ORDER BY outage_end DESC LIMIT 1"
        ).fetchone()

        if last_restore:
            since_ts = last_restore["outage_end"]
        else:
            # No outages ever — since the first ping log entry
            first = conn.execute(
                "SELECT timestamp FROM ping_log ORDER BY timestamp ASC LIMIT 1"
            ).fetchone()
            since_ts = first["timestamp"] if first else int(datetime.now().timestamp())

        conn.close()
        return jsonify({
            "status":         "ON",
            "since":          datetime.fromtimestamp(since_ts).isoformat(),
            "ts":             since_ts,
            "ongoing_outage": False
        })

    except Exception:
        return jsonify({"status": "unknown", "since": None})


# ── API: Day ──────────────────────────────────────────────────────────────────

@app.route("/api/day")
def api_day():
    date_str = request.args.get("date", date.today().isoformat())
    try:
        start_ts, end_ts = day_bounds(date_str)
    except ValueError:
        return jsonify({"error": "Invalid date"}), 400

    conn = get_db()

    if not has_any_data(conn, start_ts, end_ts):
        conn.close()
        return jsonify({"date": date_str, "has_data": False, "outages": [], "uptime_pct": None, "day_start_ts": start_ts, "day_end_ts": end_ts})

    outages_raw = get_outages_in_range(conn, start_ts, end_ts)
    conn.close()

    off_secs = outage_seconds_in_window(outages_raw, start_ts, end_ts)
    uptime   = compute_uptime(off_secs, start_ts, end_ts)
    window   = end_ts - start_ts

    outage_list = []
    for o in outages_raw:
        s = max(o["outage_start"], start_ts)
        e = min(o["outage_end"] if o["outage_end"] else int(datetime.now().timestamp()), end_ts)
        outage_list.append({"start": s, "end": e, "duration": e - s, "ongoing": not o["restored"]})

    segments = build_day_segments(outage_list, start_ts, end_ts)

    hourly = []
    for h in range(24):
        hs  = start_ts + h * 3600
        he  = hs + 3600
        off = outage_seconds_in_window(outages_raw, hs, he)
        hourly.append({"hour": h, "uptime_pct": compute_uptime(off, hs, he)})

    return jsonify({
        "date":         date_str,
        "has_data":     True,
        "outages":      outage_list,
        "segments":     segments,
        "uptime_pct":   uptime,
        "off_seconds":  off_secs,
        "on_seconds":   window - off_secs,
        "hourly":       hourly,
        "day_start_ts": start_ts,
        "day_end_ts":   end_ts
    })


# ── API: Month ────────────────────────────────────────────────────────────────

@app.route("/api/month")
def api_month():
    today = date.today()
    try:
        year  = int(request.args.get("year",  today.year))
        month = int(request.args.get("month", today.month))
    except ValueError:
        return jsonify({"error": "Invalid year or month"}), 400

    last_day_num = calendar.monthrange(year, month)[1]
    month_start  = int(datetime(year, month, 1).timestamp())
    month_end    = int(datetime(year, month, last_day_num, 23, 59, 59).timestamp())

    conn = get_db()
    outages = get_outages_in_range(conn, month_start, month_end)

    days = []
    for n in range(1, last_day_num + 1):
        ds = int(datetime(year, month, n, 0,  0,  0).timestamp())
        de = int(datetime(year, month, n, 23, 59, 59).timestamp())
        if not has_any_data(conn, ds, de):
            days.append({"date": f"{year}-{month:02d}-{n:02d}", "uptime_pct": None})
            continue
        off = outage_seconds_in_window(outages, ds, de)
        days.append({"date": f"{year}-{month:02d}-{n:02d}", "uptime_pct": compute_uptime(off, ds, de)})

    conn.close()
    valid = [d["uptime_pct"] for d in days if d["uptime_pct"] is not None]
    return jsonify({
        "year": year, "month": month,
        "days": days,
        "avg_uptime_pct": round(sum(valid)/len(valid), 2) if valid else None
    })


# ── API: Year ─────────────────────────────────────────────────────────────────

@app.route("/api/year")
def api_year():
    try:
        year = int(request.args.get("year", date.today().year))
    except ValueError:
        return jsonify({"error": "Invalid year"}), 400

    year_start = int(datetime(year, 1,  1,  0,  0,  0).timestamp())
    year_end   = int(datetime(year, 12, 31, 23, 59, 59).timestamp())

    conn = get_db()
    outages = get_outages_in_range(conn, year_start, year_end)

    months = []
    for m in range(1, 13):
        last_day = calendar.monthrange(year, m)[1]
        ms = int(datetime(year, m, 1).timestamp())
        me = int(datetime(year, m, last_day, 23, 59, 59).timestamp())
        if not has_any_data(conn, ms, me):
            months.append({"month": f"{year}-{m:02d}", "month_name": calendar.month_abbr[m], "uptime_pct": None})
            continue
        off = outage_seconds_in_window(outages, ms, me)
        months.append({"month": f"{year}-{m:02d}", "month_name": calendar.month_abbr[m], "uptime_pct": compute_uptime(off, ms, me)})

    conn.close()
    valid = [m["uptime_pct"] for m in months if m["uptime_pct"] is not None]
    return jsonify({
        "year": year, "months": months,
        "avg_uptime_pct": round(sum(valid)/len(valid), 2) if valid else None
    })


# ── API: Recent events ────────────────────────────────────────────────────────

@app.route("/api/events")
def api_events():
    limit = int(request.args.get("limit", 20))
    conn = get_db()
    rows = conn.execute("""
        SELECT outage_start, outage_end, duration_seconds, restored
        FROM power_events ORDER BY outage_start DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    events = []
    for r in rows:
        events.append({
            "outage_start":     r["outage_start"],
            "outage_end":       r["outage_end"],
            "duration_seconds": r["duration_seconds"],
            "restored":         bool(r["restored"]),
            "start_fmt":        datetime.fromtimestamp(r["outage_start"]).strftime("%d %b %Y %H:%M:%S"),
            "end_fmt":          datetime.fromtimestamp(r["outage_end"]).strftime("%d %b %Y %H:%M:%S") if r["outage_end"] else "Ongoing",
            "duration_fmt":     fmt_dur(r["duration_seconds"])
        })
    return jsonify({"events": events})


# ── Main page ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    cfg = load_config()
    return render_template("index.html", lookout_ip=cfg["lookout_ip"])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
