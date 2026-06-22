from flask import Flask, render_template, jsonify, request
import sqlite3
import os
import calendar
from datetime import datetime, date

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "/data/pyoutage.db")
PLUG_IP = os.environ.get("PLUG_IP", "192.168.0.111")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# API: Live status
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT status, timestamp FROM ping_log ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception:
        return jsonify({"status": "unknown", "since": None, "ts": None})

    if not row:
        return jsonify({"status": "unknown", "since": None, "ts": None})

    return jsonify({
        "status": "ON" if row["status"] == 1 else "OFF",
        "since":  datetime.fromtimestamp(row["timestamp"]).isoformat(),
        "ts":     row["timestamp"]
    })


# ─────────────────────────────────────────────────────────────────────────────
# API: Day view
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/day")
def api_day():
    """
    Returns ping-by-ping data for one day, plus derived segments for timeline.
    Query param: date=YYYY-MM-DD  (default: today)
    """
    date_str = request.args.get("date", date.today().isoformat())
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date. Use YYYY-MM-DD"}), 400

    start_ts = int(d.replace(hour=0,  minute=0,  second=0).timestamp())
    end_ts   = int(d.replace(hour=23, minute=59, second=59).timestamp())

    conn = get_db()
    rows = conn.execute(
        "SELECT timestamp, status FROM ping_log "
        "WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp",
        (start_ts, end_ts)
    ).fetchall()
    conn.close()

    if not rows:
        return jsonify({
            "date": date_str, "pings": [], "segments": [],
            "uptime_pct": None, "day_start_ts": start_ts, "day_end_ts": end_ts
        })

    pings = [{"ts": r["timestamp"], "status": r["status"]} for r in rows]

    total    = len(pings)
    on_count = sum(1 for p in pings if p["status"] == 1)
    uptime   = round((on_count / total) * 100, 2) if total else 0

    # Build ON/OFF segments for the timeline bar
    segments = []
    seg_start  = pings[0]["ts"]
    seg_status = pings[0]["status"]
    for p in pings[1:]:
        if p["status"] != seg_status:
            segments.append({"start": seg_start, "end": p["ts"], "status": seg_status})
            seg_start  = p["ts"]
            seg_status = p["status"]
    segments.append({"start": seg_start, "end": pings[-1]["ts"], "status": seg_status})

    return jsonify({
        "date":         date_str,
        "pings":        pings,
        "segments":     segments,
        "uptime_pct":   uptime,
        "day_start_ts": start_ts,
        "day_end_ts":   end_ts
    })


# ─────────────────────────────────────────────────────────────────────────────
# API: Month view
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/month")
def api_month():
    """
    Returns per-day uptime % for a given month.
    Query params: year=YYYY, month=MM  (defaults: current month)
    """
    today = date.today()
    try:
        year  = int(request.args.get("year",  today.year))
        month = int(request.args.get("month", today.month))
    except ValueError:
        return jsonify({"error": "Invalid year or month"}), 400

    last_day_num = calendar.monthrange(year, month)[1]
    start_ts = int(datetime(year, month, 1).timestamp())
    end_ts   = int(datetime(year, month, last_day_num, 23, 59, 59).timestamp())

    conn = get_db()
    rows = conn.execute("""
        SELECT
            DATE(timestamp, 'unixepoch', 'localtime') AS day,
            SUM(status)  AS on_count,
            COUNT(*)     AS total
        FROM ping_log
        WHERE timestamp BETWEEN ? AND ?
        GROUP BY day
        ORDER BY day
    """, (start_ts, end_ts)).fetchall()
    conn.close()

    data = {
        r["day"]: round((r["on_count"] / r["total"]) * 100, 2)
        for r in rows if r["total"]
    }

    days = []
    for n in range(1, last_day_num + 1):
        key = f"{year}-{month:02d}-{n:02d}"
        days.append({"date": key, "uptime_pct": data.get(key)})

    valid    = [d["uptime_pct"] for d in days if d["uptime_pct"] is not None]
    avg      = round(sum(valid) / len(valid), 2) if valid else None

    return jsonify({
        "year": year, "month": month,
        "days": days,
        "avg_uptime_pct": avg
    })


# ─────────────────────────────────────────────────────────────────────────────
# API: Year view
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/year")
def api_year():
    """
    Returns per-month uptime % for a given year.
    Query param: year=YYYY  (default: current year)
    """
    try:
        year = int(request.args.get("year", date.today().year))
    except ValueError:
        return jsonify({"error": "Invalid year"}), 400

    start_ts = int(datetime(year, 1,  1,  0,  0,  0).timestamp())
    end_ts   = int(datetime(year, 12, 31, 23, 59, 59).timestamp())

    conn = get_db()
    rows = conn.execute("""
        SELECT
            STRFTIME('%Y-%m', timestamp, 'unixepoch', 'localtime') AS month,
            SUM(status) AS on_count,
            COUNT(*)    AS total
        FROM ping_log
        WHERE timestamp BETWEEN ? AND ?
        GROUP BY month
        ORDER BY month
    """, (start_ts, end_ts)).fetchall()
    conn.close()

    data = {
        r["month"]: round((r["on_count"] / r["total"]) * 100, 2)
        for r in rows if r["total"]
    }

    months = []
    for m in range(1, 13):
        key = f"{year}-{m:02d}"
        months.append({
            "month":      key,
            "month_name": calendar.month_abbr[m],
            "uptime_pct": data.get(key)
        })

    valid = [m["uptime_pct"] for m in months if m["uptime_pct"] is not None]
    avg   = round(sum(valid) / len(valid), 2) if valid else None

    return jsonify({
        "year": year,
        "months": months,
        "avg_uptime_pct": avg
    })


# ─────────────────────────────────────────────────────────────────────────────
# API: Available date range (for pickers)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/available")
def api_available():
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT MIN(timestamp) AS first, MAX(timestamp) AS last FROM ping_log"
        ).fetchone()
        conn.close()
    except Exception:
        return jsonify({"first_date": None, "last_date": None})

    if not row or not row["first"]:
        return jsonify({"first_date": None, "last_date": None})

    return jsonify({
        "first_date": datetime.fromtimestamp(row["first"]).date().isoformat(),
        "last_date":  datetime.fromtimestamp(row["last"]).date().isoformat()
    })


# ─────────────────────────────────────────────────────────────────────────────
# Main page
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", plug_ip=PLUG_IP)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
