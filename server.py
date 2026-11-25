#!/usr/bin/env python3
"""Claude Code token leaderboard.

One process, standard library only:
  POST /v1/metrics        OTLP/HTTP JSON receiver (Claude Code telemetry), auth by x-lb-token
  GET|POST /v1/metrics/backfill   history from local transcripts (window, then upload)
  POST /v1/metrics/9router        hourly totals read from a 9router usage log (replaces that machine's rows)
  GET  /                  leaderboard page
  GET  /api/leaderboard   leaderboard data (?period=today|7d|30d|month|all&source=all|claude|9router)
  GET  /api/participant   one participant's detail (?id=&period=&source=): series, models, token mix
  GET  /install.sh        installer for macOS / Linux
  GET  /install.ps1       installer for Windows PowerShell
  GET  /api/me            who is viewing (email from Cloudflare Access) + linked participant
  POST /api/me/link       link the viewer's email to a participant (proves it with the lb_ token)
  POST|DELETE /api/me/avatar   upload / remove the viewer's own photo
  GET  /avatars/<id>      participant photo
  GET  /admin             admin panel (only for LB_ADMIN_EMAILS)
  GET|POST /api/admin/... participants: list, add, edit, new token, remove history/photo, delete
  GET  /healthz
"""
import gzip
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sys
import threading
import time
import zlib
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from accessauth import AccessVerifier
from lbcommon import BASE, connect, delete_employee, hash_token, install_commands, load_config, new_token

CFG = load_config()
TZ = ZoneInfo(CFG["LB_TZ"])
DB = connect(CFG)
DB_LOCK = threading.Lock()
MAX_BODY = 4 * 1024 * 1024

# Metrics we keep, with a per-data-point sanity cap (anything above is rejected as garbage).
TRACKED = {
    "claude_code.token.usage": 50_000_000,
    "claude_code.cost.usage": 5_000,
    "claude_code.session.count": 1_000,
    "claude_code.active_time.total": 7 * 86400,
}
TEMPORALITY_CUMULATIVE = {2, "2", "AGGREGATION_TEMPORALITY_CUMULATIVE"}
PERIODS = ("today", "7d", "30d", "month", "all")
STATIC = os.path.join(BASE, "static")
AVATARS = os.path.join(os.path.dirname(CFG["LB_DB"]), "avatars")
AVATAR_MAX = 600 * 1024
AVATAR_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
AVATAR_CTYPE = {v: k for k, v in AVATAR_TYPES.items()}
ACCESS = AccessVerifier(CFG["LB_CF_TEAM_DOMAIN"], CFG["LB_CF_VIEWER_AUD"])
LANG = "id" if CFG["LB_LANG"].lower().startswith("id") else "en"   # the default; a visitor can switch
MARKER = re.compile(r"\u27e6(.*?)\u2016(.*?)\u27e7", re.S)   # ⟦English‖Indonesian⟧ in templates
_REQ = threading.local()   # the language of the request this thread is answering


def lang():
    return getattr(_REQ, "lang", LANG)


def T(en, id_):
    return id_ if lang() == "id" else en


def localize(text):
    current = lang()
    return MARKER.sub(lambda m: m.group(2) if current == "id" else m.group(1), text)


def load_secret():
    """Random key for signing cookies, created on first start next to the database."""
    path = os.path.join(os.path.dirname(CFG["LB_DB"]), "secret.key")
    try:
        with open(path, "rb") as fh:
            key = fh.read().strip()
        if len(key) >= 32:
            return key
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


SECRET = load_secret()


def sign(value):
    return "%s.%s" % (value, hmac.new(SECRET, value.encode(), hashlib.sha256).hexdigest()[:40])


def unsign(signed):
    value, _, mac = (signed or "").rpartition(".")
    if value and hmac.compare_digest(mac, hmac.new(SECRET, value.encode(), hashlib.sha256).hexdigest()[:40]):
        return value
    return None


def log(msg):
    sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    sys.stderr.flush()


# ---------------------------------------------------------------- OTLP ingest

def attr_map(attrs):
    out = {}
    for a in attrs or []:
        v = a.get("value") or {}
        for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if k in v:
                out[a.get("key", "")] = v[k]
                break
    return out


def point_value(dp):
    try:
        if "asDouble" in dp:
            return float(dp["asDouble"])
        if "asInt" in dp:
            return float(int(dp["asInt"]))
    except (TypeError, ValueError):
        return None
    return None


def point_time(dp, now):
    try:
        ts = int(dp.get("timeUnixNano") or 0) / 1e9
    except (TypeError, ValueError):
        ts = 0
    # Clock skew guard: future -> now, missing/very old -> now.
    if ts <= 0 or ts > now or ts < now - 30 * 86400:
        ts = now
    return int(ts)


def ingest(employee_id, payload):
    """Add one OTLP ExportMetricsServiceRequest (JSON) to the hourly buckets."""
    now = time.time()
    adds = {}  # (hour, metric, type, model) -> value
    rejected = 0
    first_token_ts = None
    with DB_LOCK:
        DB.execute("BEGIN IMMEDIATE")
        try:
            for rm in payload.get("resourceMetrics") or []:
                res_attrs = attr_map((rm.get("resource") or {}).get("attributes"))
                for sm in rm.get("scopeMetrics") or []:
                    for m in sm.get("metrics") or []:
                        name = m.get("name")
                        if name not in TRACKED:
                            continue
                        data = m.get("sum") or m.get("gauge")
                        if not isinstance(data, dict):
                            continue
                        cumulative = data.get("aggregationTemporality") in TEMPORALITY_CUMULATIVE
                        for dp in data.get("dataPoints") or []:
                            val = point_value(dp)
                            if val is None:
                                continue
                            attrs = attr_map(dp.get("attributes"))
                            if cumulative:
                                val = cumulative_delta(employee_id, name, res_attrs, attrs, dp, val, now)
                            if val < 0 or val > TRACKED[name]:
                                rejected += 1
                                continue
                            if val == 0:
                                continue
                            ts = point_time(dp, now)
                            key = (ts - ts % 3600, name, str(attrs.get("type", "")), str(attrs.get("model", "")))
                            adds[key] = adds.get(key, 0.0) + val
                            if name == "claude_code.token.usage":
                                try:
                                    start = int(int(dp.get("startTimeUnixNano") or 0) / 1e9)
                                except (TypeError, ValueError):
                                    start = 0
                                first = start if 0 < start <= ts else ts
                                first_token_ts = first if first_token_ts is None else min(first_token_ts, first)
            for (hour, metric, typ, model), val in adds.items():
                DB.execute(
                    "INSERT INTO usage_hourly(employee_id, hour, metric, type, model, value) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(employee_id, hour, metric, type, model) DO UPDATE SET value = value + excluded.value",
                    (employee_id, hour, metric, typ, model, val),
                )
            DB.execute("UPDATE employees SET last_seen = ? WHERE id = ?", (int(now), employee_id))
            if first_token_ts is not None:
                DB.execute(
                    "UPDATE employees SET first_otel_at = ? WHERE id = ? AND first_otel_at IS NULL",
                    (int(first_token_ts), employee_id),
                )
            DB.execute("COMMIT")
        except Exception:
            DB.execute("ROLLBACK")
            raise
    return len(adds), rejected


def cumulative_delta(employee_id, name, res_attrs, attrs, dp, val, now):
    """Turn a cumulative counter value into the increase since the last export."""
    series = json.dumps(
        [employee_id, name, sorted(res_attrs.items()), sorted(attrs.items())], default=str, separators=(",", ":")
    )
    try:
        start_ns = int(dp.get("startTimeUnixNano") or 0)
    except (TypeError, ValueError):
        start_ns = 0
    row = DB.execute("SELECT start_ns, value FROM cumulative_state WHERE series = ?", (series,)).fetchone()
    if row and row["start_ns"] == start_ns:
        delta = val - row["value"]
        if delta < 0:  # counter reset
            delta = val
    else:
        delta = val
    DB.execute(
        "INSERT INTO cumulative_state(series, start_ns, value, updated) VALUES (?,?,?,?) "
        "ON CONFLICT(series) DO UPDATE SET start_ns = excluded.start_ns, value = excluded.value, updated = excluded.updated",
        (series, start_ns, val, int(now)),
    )
    return delta


# ---------------------------------------------------------------- transcript backfill

BACKFILL_MAX_DAYS = 90
BACKFILL_MAX_ROWS = 20000
BACKFILL_FIELDS = {  # payload field -> (OTel token type, per-hour sanity cap)
    "input": ("input", 500_000_000),
    "output": ("output", 50_000_000),
    "cache_read": ("cacheRead", 5_000_000_000),
    "cache_creation": ("cacheCreation", 500_000_000),
}


def backfill_window(employee_id):
    """Backfill may only cover [now - 90 days, first OTel data) so nothing is counted twice."""
    now = int(time.time())
    row = DB.execute("SELECT first_otel_at FROM employees WHERE id = ?", (employee_id,)).fetchone()
    cutoff = row["first_otel_at"] if row and row["first_otel_at"] else now
    return now - BACKFILL_MAX_DAYS * 86400, min(cutoff, now)


def valid_machine(m):
    return isinstance(m, str) and 1 <= len(m) <= 64 and all(c.isalnum() or c in "-_" for c in m)


def as_count(v, cap):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0 or v > cap:
        raise ValueError("bad number")
    return float(v)


ROUTER_PREFIX = "9router-"
ROUTER_FIELDS = dict(BACKFILL_FIELDS, cost=("", 5_000), requests=("", 1_000_000))


def store_backfill(employee_id, payload):
    machine = payload.get("machine")
    if not valid_machine(machine) or machine.startswith(ROUTER_PREFIX):
        raise ValueError("bad machine id")
    rows = payload.get("rows") or []
    sessions = payload.get("sessions") or []
    if not isinstance(rows, list) or not isinstance(sessions, list) or len(rows) + len(sessions) > BACKFILL_MAX_ROWS:
        raise ValueError("bad rows")
    with DB_LOCK:
        start, cutoff = backfill_window(employee_id)
        out = {}
        skipped = 0
        for r in rows:
            if not isinstance(r, dict):
                raise ValueError("bad row")
            hour, model = r.get("hour"), r.get("model")
            if not isinstance(hour, int) or hour % 3600 or not isinstance(model, str) or not 0 < len(model) <= 80:
                raise ValueError("bad row")
            if hour < start - 3600 or hour >= cutoff:
                skipped += 1
                continue
            for field, (typ, cap) in BACKFILL_FIELDS.items():
                v = as_count(r.get(field, 0), cap)
                if v:
                    k = (hour, "claude_code.token.usage", typ, model)
                    out[k] = out.get(k, 0.0) + v
        for s in sessions:
            if not isinstance(s, dict) or not isinstance(s.get("hour"), int) or s["hour"] % 3600:
                raise ValueError("bad session row")
            if s["hour"] < start - 3600 or s["hour"] >= cutoff:
                continue
            v = as_count(s.get("count", 0), 10_000)
            if v:
                k = (s["hour"], "claude_code.session.count", "", "")
                out[k] = out.get(k, 0.0) + v
        try:
            messages = int(payload.get("messages") or 0)
        except (TypeError, ValueError):
            messages = 0
        DB.execute("BEGIN IMMEDIATE")
        try:
            DB.execute("DELETE FROM usage_backfill WHERE employee_id = ? AND machine = ?", (employee_id, machine))
            DB.executemany(
                "INSERT INTO usage_backfill(employee_id, machine, hour, metric, type, model, value) VALUES (?,?,?,?,?,?,?)",
                [(employee_id, machine, h, m, t, mo, v) for (h, m, t, mo), v in out.items()],
            )
            DB.execute(
                "INSERT INTO backfill_runs(employee_id, machine, at, cutoff, messages) VALUES (?,?,?,?,?) "
                "ON CONFLICT(employee_id, machine) DO UPDATE SET at = excluded.at, cutoff = excluded.cutoff, messages = excluded.messages",
                (employee_id, machine, int(time.time()), cutoff, messages),
            )
            DB.execute("COMMIT")
        except Exception:
            DB.execute("ROLLBACK")
            raise
    output = sum(v for (h, m, t, mo), v in out.items() if t == "output")
    return {"ok": True, "buckets": len(out), "skipped": skipped, "output": int(output), "cutoff": cutoff}


def store_router(employee_id, payload):
    """Hourly totals from a 9router usage log. Each sync sends every hour from `from` on and
    replaces that machine's rows for those hours, so a repeat never counts twice and hours the
    log no longer holds (if 9router ever trims it) stay as they were."""
    machine = payload.get("machine")
    if not valid_machine(machine) or not machine.startswith(ROUTER_PREFIX):
        raise ValueError("bad machine id")
    rows = payload.get("rows") or []
    if not isinstance(rows, list) or len(rows) > BACKFILL_MAX_ROWS:
        raise ValueError("bad rows")
    now = int(time.time())
    start = now - BACKFILL_MAX_DAYS * 86400
    frm = payload.get("from")
    if not isinstance(frm, int) or frm % 3600 or frm > now:
        raise ValueError("bad from")
    out = {}
    requests = 0
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError("bad row")
        hour, model = r.get("hour"), r.get("model")
        if not isinstance(hour, int) or hour % 3600 or not isinstance(model, str) or not 0 < len(model) <= 80:
            raise ValueError("bad row")
        if hour < max(frm, start - 3600) or hour > now:
            continue
        for field, (typ, cap) in ROUTER_FIELDS.items():
            v = as_count(r.get(field, 0), cap)
            if not v:
                continue
            if field == "cost":
                k = (hour, "claude_code.cost.usage", "", model)
            elif field == "requests":
                k = (hour, "router.request.count", "", "")
                requests += v
            else:
                k = (hour, "claude_code.token.usage", typ, model)
            out[k] = out.get(k, 0.0) + v
    last = payload.get("last_request")
    last = int(last) if isinstance(last, (int, float)) and start <= last <= now + 300 else None
    with DB_LOCK:
        DB.execute("BEGIN IMMEDIATE")
        try:
            DB.execute("DELETE FROM usage_backfill WHERE employee_id = ? AND machine = ? AND hour >= ?", (employee_id, machine, frm))
            DB.executemany(
                "INSERT INTO usage_backfill(employee_id, machine, hour, metric, type, model, value) VALUES (?,?,?,?,?,?,?)",
                [(employee_id, machine, h, m, t, mo, v) for (h, m, t, mo), v in out.items()],
            )
            total = DB.execute(
                "SELECT COALESCE(SUM(value), 0) FROM usage_backfill WHERE employee_id = ? AND machine = ? AND metric = 'router.request.count'",
                (employee_id, machine)).fetchone()[0]
            DB.execute(
                "INSERT INTO backfill_runs(employee_id, machine, at, cutoff, messages) VALUES (?,?,?,?,?) "
                "ON CONFLICT(employee_id, machine) DO UPDATE SET at = excluded.at, cutoff = excluded.cutoff, messages = excluded.messages",
                (employee_id, machine, now, now, int(total)),
            )
            if last:
                DB.execute("UPDATE employees SET last_seen = MAX(COALESCE(last_seen, 0), ?) WHERE id = ?", (min(last, now), employee_id))
            DB.execute("COMMIT")
        except Exception:
            DB.execute("ROLLBACK")
            raise
    output = sum(v for (h, m, t, mo), v in out.items() if t == "output")
    return {"ok": True, "buckets": len(out), "requests": int(requests), "output": int(output)}


def find_employee(token):
    if not token:
        return None
    row = DB.execute(
        "SELECT id, active FROM employees WHERE token_hash = ?", (hash_token(token),)
    ).fetchone()
    if not row or not row["active"]:
        return None
    return row["id"]


# ---------------------------------------------------------------- leaderboard

def period_start(period):
    now = datetime.now(TZ)
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "7d":
        start = now - timedelta(days=7)
    elif period == "30d":
        start = now - timedelta(days=30)
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return 0
    ts = int(start.timestamp())
    return ts - ts % 3600


ALL_USAGE = (
    "SELECT employee_id, hour, metric, type, model, value FROM usage_hourly "
    "UNION ALL SELECT employee_id, hour, metric, type, model, value FROM usage_backfill"
)


def avatar_url(row):
    if row["avatar_ext"] and row["avatar_ver"]:
        return "/avatars/%d?v=%d" % (row["id"], row["avatar_ver"])
    return None


_LB_CACHE = {}
_LB_CACHE_LOCK = threading.Lock()


SOURCES = ("all", "claude", "9router")
TOTAL_KEYS = ("output", "input", "cache_read", "cache_creation", "cost", "sessions", "requests")


def rank_rows(rows):
    rank, prev = 0, None
    for i, r in enumerate(rows, 1):
        if r["output"] != prev:
            rank, prev = i, r["output"]
        r["rank"] = rank
    return rows


def leaderboard(period, me_id=None, source="all"):
    """Every open dashboard polls once a second, so the result is shared for up to a second.
    source narrows the board to people on Claude Code or on 9router; ranks follow the filter."""
    now = time.time()
    with _LB_CACHE_LOCK:
        hit = _LB_CACHE.get(period)
        if hit is None or now - hit[0] >= 1.0:
            hit = (now, _leaderboard(period))
            _LB_CACHE[period] = hit
    data = hit[1]
    rows = []
    for r in data["rows"]:
        if source == "claude" and r["router"] or source == "9router" and not r["router"]:
            continue
        r = dict(r)
        r["me"] = r["id"] == me_id
        rows.append(r)
    if source != "all":
        rank_rows(rows)
        totals = {k: sum(x[k] for x in rows) for k in TOTAL_KEYS}
        totals["cost"] = round(totals["cost"], 4)
        return dict(data, rows=rows, totals=totals, source=source)
    return dict(data, rows=rows, source=source)


def participant(employee_id, period, me_id=None, source="all"):
    """Detail card: the leaderboard row plus output over time and the model split.
    Rank and share follow the board's source filter when the person is part of it."""
    board = leaderboard(period, me_id, source)
    row = next((r for r in board["rows"] if r["id"] == employee_id), None)
    if row is None and source != "all":
        board = leaderboard(period, me_id)
        row = next((r for r in board["rows"] if r["id"] == employee_id), None)
    if row is None:
        return None
    since = board["since"]
    with DB_LOCK:
        emp = DB.execute("SELECT created_at FROM employees WHERE id = ?", (employee_id,)).fetchone()
        hours = DB.execute(
            "SELECT hour, SUM(value) AS v FROM (" + ALL_USAGE + ") WHERE employee_id = ? "
            "AND metric = 'claude_code.token.usage' AND type = 'output' AND hour >= ? GROUP BY hour ORDER BY hour",
            (employee_id, since),
        ).fetchall()
        models = DB.execute(
            "SELECT model, SUM(value) AS v FROM (" + ALL_USAGE + ") WHERE employee_id = ? "
            "AND metric = 'claude_code.token.usage' AND type = 'output' AND hour >= ? GROUP BY model ORDER BY v DESC",
            (employee_id, since),
        ).fetchall()

    now = datetime.now(TZ)
    if period == "today":
        unit = "hour"
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        keys = [int((start + timedelta(hours=i)).timestamp()) for i in range(24)]
        def key(h):
            return h - h % 3600
    else:
        first = datetime.fromtimestamp(since if since else (hours[0]["hour"] if hours else now.timestamp()), TZ).date()
        days = (now.date() - first).days + 1
        unit = "week" if days > 120 else "day"
        if unit == "week":
            first = first - timedelta(days=first.weekday())
            step, n = 7, (now.date() - first).days // 7 + 1
        else:
            step, n = 1, days
        keys = [int(datetime.combine(first + timedelta(days=i * step), datetime.min.time(), TZ).timestamp()) for i in range(n)]
        def key(h):
            d = datetime.fromtimestamp(h, TZ).date()
            if unit == "week":
                d = d - timedelta(days=d.weekday())
            return int(datetime.combine(d, datetime.min.time(), TZ).timestamp())
    buckets = dict.fromkeys(keys, 0)
    by_hour_of_day = [0] * 24
    for h in hours:
        k = key(h["hour"])
        if k in buckets:
            buckets[k] += h["v"]
        by_hour_of_day[datetime.fromtimestamp(h["hour"], TZ).hour] += h["v"]
    total_models = sum(m["v"] for m in models) or 1
    out = dict(row)
    out.pop("id", None)
    out.update({
        "id": employee_id,
        "period": period,
        "since": since,
        "tz": board["tz"],
        "generated_at": board["generated_at"],
        "of": sum(1 for r in board["rows"] if r["output"] > 0),
        "share": (row["output"] / board["totals"]["output"]) if board["totals"]["output"] else 0,
        "created_at": emp["created_at"] if emp else None,
        "unit": unit,
        "series": [[k, int(buckets[k])] for k in keys],
        "active_days": len({datetime.fromtimestamp(h["hour"], TZ).date() for h in hours if h["v"] > 0}),
        "peak_hour": max(range(24), key=lambda i: by_hour_of_day[i]) if any(by_hour_of_day) else None,
        "models": [{"model": m["model"], "output": int(m["v"]), "share": m["v"] / total_models} for m in models if m["v"] > 0],
    })
    return out


def _leaderboard(period):
    since = period_start(period)
    with DB_LOCK:
        rows = DB.execute(
            """
            SELECT e.id, e.name, e.team, e.last_seen, e.avatar_ext, e.avatar_ver,
              COALESCE(SUM(CASE WHEN u.metric='claude_code.token.usage' AND u.type='output' THEN u.value END),0) AS output,
              COALESCE(SUM(CASE WHEN u.metric='claude_code.token.usage' AND u.type='input' THEN u.value END),0) AS input,
              COALESCE(SUM(CASE WHEN u.metric='claude_code.token.usage' AND u.type='cacheRead' THEN u.value END),0) AS cache_read,
              COALESCE(SUM(CASE WHEN u.metric='claude_code.token.usage' AND u.type='cacheCreation' THEN u.value END),0) AS cache_creation,
              COALESCE(SUM(CASE WHEN u.metric='claude_code.cost.usage' THEN u.value END),0) AS cost,
              COALESCE(SUM(CASE WHEN u.metric='claude_code.session.count' THEN u.value END),0) AS sessions,
              COALESCE(SUM(CASE WHEN u.metric='claude_code.active_time.total' THEN u.value END),0) AS active_seconds,
              COALESCE(SUM(CASE WHEN u.metric='router.request.count' THEN u.value END),0) AS requests,
              EXISTS(SELECT 1 FROM backfill_runs r WHERE r.employee_id = e.id AND r.machine LIKE '9router-%') AS router
            FROM employees e
            LEFT JOIN ({all_usage}) u ON u.employee_id = e.id AND u.hour >= ?
            WHERE e.active = 1
            GROUP BY e.id
            ORDER BY output DESC, e.name COLLATE NOCASE ASC
            """.format(all_usage=ALL_USAGE),
            (since,),
        ).fetchall()
        models = DB.execute(
            "SELECT employee_id, model, SUM(value) AS v FROM (" + ALL_USAGE + ") "
            "WHERE metric='claude_code.token.usage' AND type='output' AND hour >= ? "
            "GROUP BY employee_id, model",
            (since,),
        ).fetchall()
    top_model = {}
    for m in models:
        cur = top_model.get(m["employee_id"])
        if cur is None or m["v"] > cur[1]:
            top_model[m["employee_id"]] = (m["model"], m["v"])
    out = []
    for r in rows:
        out.append({
            "name": r["name"],
            "team": r["team"],
            "output": int(r["output"]),
            "input": int(r["input"]),
            "cache_read": int(r["cache_read"]),
            "cache_creation": int(r["cache_creation"]),
            "cost": round(r["cost"], 4),
            "sessions": int(r["sessions"]),
            "active_seconds": int(r["active_seconds"]),
            "requests": int(r["requests"]),
            "router": bool(r["router"]),
            "top_model": (top_model.get(r["id"]) or ("", 0))[0],
            "last_seen": r["last_seen"],
            "avatar": avatar_url(r),
            "id": r["id"],
        })
    rank_rows(out)
    totals = {k: sum(x[k] for x in out) for k in TOTAL_KEYS}
    return {
        "period": period,
        "since": since,
        "generated_at": int(time.time()),
        "tz": CFG["LB_TZ"],
        "totals": totals,
        "has_router": any(x["router"] for x in out),
        "rows": out,
    }


# ---------------------------------------------------------------- viewer profile

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def employee_by_email(email):
    if not email:
        return None
    return DB.execute(
        "SELECT id, name, team, avatar_ext, avatar_ver FROM employees WHERE email = ? COLLATE NOCASE AND active = 1",
        (email,),
    ).fetchone()


def employee_by_id(employee_id):
    return DB.execute(
        "SELECT id, name, team, avatar_ext, avatar_ver FROM employees WHERE id = ? AND active = 1", (employee_id,),
    ).fetchone()


def me_payload(email, row, admin=False):
    return {
        "email": email,
        "admin": bool(admin),
        "participant": {"name": row["name"], "team": row["team"], "avatar": avatar_url(row)} if row else None,
    }


def sniff_image(data):
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def save_avatar(employee_id, data, ext):
    os.makedirs(AVATARS, mode=0o700, exist_ok=True)
    tmp = os.path.join(AVATARS, ".%d.%s.tmp" % (employee_id, secrets.token_hex(4)))
    with open(tmp, "wb") as fh:
        fh.write(data)
    for old in AVATAR_CTYPE:
        if old != ext:
            try:
                os.remove(os.path.join(AVATARS, "%d.%s" % (employee_id, old)))
            except FileNotFoundError:
                pass
    os.replace(tmp, os.path.join(AVATARS, "%d.%s" % (employee_id, ext)))


def remove_avatar(employee_id):
    for ext in AVATAR_CTYPE:
        try:
            os.remove(os.path.join(AVATARS, "%d.%s" % (employee_id, ext)))
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- admin

ADMINS = {e.strip().lower() for e in CFG["LB_ADMIN_EMAILS"].split(",") if e.strip()}
ADMIN_KEY = CFG["LB_ADMIN_KEY"]
ADMIN_COOKIE = hmac.new(SECRET, ("admin:" + ADMIN_KEY).encode(), hashlib.sha256).hexdigest() if ADMIN_KEY else None
NAME_MAX, TEAM_MAX = 60, 40


def admin_list():
    with DB_LOCK:
        rows = DB.execute("SELECT * FROM employees ORDER BY name COLLATE NOCASE").fetchall()
        runs = DB.execute(
            "SELECT r.employee_id, r.machine, r.at, r.messages, "
            "(SELECT COALESCE(SUM(value), 0) FROM usage_backfill b WHERE b.employee_id = r.employee_id AND b.machine = r.machine "
            " AND b.metric = 'claude_code.token.usage' AND b.type = 'output') AS output "
            "FROM backfill_runs r ORDER BY r.at"
        ).fetchall()
        totals = {r["employee_id"]: r["v"] for r in DB.execute(
            "SELECT employee_id, SUM(value) AS v FROM (" + ALL_USAGE + ") "
            "WHERE metric = 'claude_code.token.usage' AND type = 'output' GROUP BY employee_id")}
    machines = {}
    for r in runs:
        machines.setdefault(r["employee_id"], []).append(
            {"machine": r["machine"], "at": r["at"], "messages": r["messages"], "output": int(r["output"])})
    teams = sorted({r["team"] for r in rows if r["team"]}, key=str.lower)
    return {
        "teams": teams,
        "participants": [{
            "id": r["id"], "name": r["name"], "team": r["team"], "email": r["email"], "active": bool(r["active"]),
            "created_at": r["created_at"], "last_seen": r["last_seen"], "first_otel_at": r["first_otel_at"],
            "avatar": avatar_url(r), "output": int(totals.get(r["id"], 0)), "machines": machines.get(r["id"], []),
            "router": any(m["machine"].startswith(ROUTER_PREFIX) for m in machines.get(r["id"], [])),
        } for r in rows],
    }


class AdminError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def _clean_fields(body, partial):
    out = {}
    if "name" in body or not partial:
        name = " ".join(str(body.get("name") or "").split())
        if not name or len(name) > NAME_MAX:
            raise AdminError(400, T("Name must be 1-%d characters.", "Nama harus 1-%d karakter.") % NAME_MAX)
        out["name"] = name
    if "team" in body:
        team = " ".join(str(body.get("team") or "").split())
        if len(team) > TEAM_MAX:
            raise AdminError(400, T("Team name can be at most %d characters.", "Nama tim maksimal %d karakter.") % TEAM_MAX)
        out["team"] = team
    if "email" in body:
        email = str(body.get("email") or "").strip().lower() or None
        if email and not EMAIL_RE.match(email):
            raise AdminError(400, T("That email address is not valid.", "Format email tidak valid."))
        out["email"] = email
    if "active" in body:
        out["active"] = 1 if body.get("active") else 0
    return out


def admin_action(actor, method, parts, body, base_url):
    """parts: path after /api/admin/ split by '/'. Returns a JSON-able dict."""
    if parts == ["participants"] and method == "POST":
        f = _clean_fields(body, partial=False)
        token = new_token()
        with DB_LOCK:
            if DB.execute("SELECT 1 FROM employees WHERE name = ?", (f["name"],)).fetchone():
                raise AdminError(409, T("%s already exists.", "Nama %s sudah ada.") % f["name"])
            if f.get("email") and DB.execute("SELECT 1 FROM employees WHERE email = ? COLLATE NOCASE", (f["email"],)).fetchone():
                raise AdminError(409, T("%s is already used by another participant.", "Email %s sudah dipakai peserta lain.") % f["email"])
            cur = DB.execute(
                "INSERT INTO employees(name, team, token_hash, created_at, email) VALUES (?,?,?,?,?)",
                (f["name"], f.get("team", ""), hash_token(token), int(time.time()), f.get("email")),
            )
            new_id = cur.lastrowid
        log("admin %s: added %s (%s)" % (actor, f["name"], new_id))
        return {"id": new_id, "name": f["name"], "token": token, "commands": install_commands(CFG, token, base_url)}

    if len(parts) < 3 or parts[0] != "participants" or not parts[1].isdigit() or method != "POST":
        raise AdminError(404, "not found")
    pid, action = int(parts[1]), parts[2]
    with DB_LOCK:
        row = DB.execute("SELECT * FROM employees WHERE id = ?", (pid,)).fetchone()
    if not row:
        raise AdminError(404, T("Participant not found.", "Peserta tidak ditemukan."))

    if action == "update" and len(parts) == 3:
        f = _clean_fields(body, partial=True)
        if not f:
            return {"ok": True}
        with DB_LOCK:
            if "name" in f and DB.execute("SELECT 1 FROM employees WHERE name = ? AND id != ?", (f["name"], pid)).fetchone():
                raise AdminError(409, T("The name %s is already taken.", "Nama %s sudah dipakai.") % f["name"])
            if f.get("email") and DB.execute(
                    "SELECT 1 FROM employees WHERE email = ? COLLATE NOCASE AND id != ?", (f["email"], pid)).fetchone():
                raise AdminError(409, T("%s is already linked to another participant.", "Email %s sudah terhubung ke peserta lain.") % f["email"])
            sets = ", ".join("%s = ?" % k for k in f)
            DB.execute("UPDATE employees SET %s WHERE id = ?" % sets, list(f.values()) + [pid])
        log("admin %s: updated %s (%s) %s" % (actor, row["name"], pid, sorted(f)))
        return {"ok": True}

    if action == "rotate" and len(parts) == 3:
        token = new_token()
        with DB_LOCK:
            DB.execute("UPDATE employees SET token_hash = ? WHERE id = ?", (hash_token(token), pid))
        log("admin %s: new token for %s (%s)" % (actor, row["name"], pid))
        return {"id": pid, "name": row["name"], "token": token, "commands": install_commands(CFG, token, base_url)}

    if action == "avatar-delete" and len(parts) == 3:
        with DB_LOCK:
            DB.execute("UPDATE employees SET avatar_ext = NULL, avatar_ver = NULL WHERE id = ?", (pid,))
            remove_avatar(pid)
        log("admin %s: removed photo of %s (%s)" % (actor, row["name"], pid))
        return {"ok": True}

    if action == "machine-delete" and len(parts) == 4 and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", parts[3]):
        machine = parts[3]
        with DB_LOCK:
            DB.execute("BEGIN IMMEDIATE")
            try:
                n = DB.execute("DELETE FROM usage_backfill WHERE employee_id = ? AND machine = ?", (pid, machine)).rowcount
                DB.execute("DELETE FROM backfill_runs WHERE employee_id = ? AND machine = ?", (pid, machine))
                DB.execute("COMMIT")
            except Exception:
                DB.execute("ROLLBACK")
                raise
        log("admin %s: removed history of machine %s for %s (%s), %d rows" % (actor, machine, row["name"], pid, n))
        return {"ok": True}

    if action == "delete" and len(parts) == 3:
        if str(body.get("confirm") or "").strip().lower() != row["name"].lower():
            raise AdminError(400, T("Type the participant's exact name to confirm.", "Ketik nama peserta persis untuk konfirmasi."))
        with DB_LOCK:
            DB.execute("BEGIN IMMEDIATE")
            try:
                delete_employee(DB, pid, AVATARS)
                DB.execute("COMMIT")
            except Exception:
                DB.execute("ROLLBACK")
                raise
        log("admin %s: deleted %s (%s)" % (actor, row["name"], pid))
        return {"ok": True}

    raise AdminError(404, "not found")


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "claude-leaderboard/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the log quiet; errors are logged explicitly
        pass

    # -- helpers
    def send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, code, obj, headers=None):
        self.send(code, json.dumps(obj, separators=(",", ":")), "application/json", headers)

    def public_url(self):
        if CFG["LB_PUBLIC_URL"]:
            return CFG["LB_PUBLIC_URL"]
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "localhost"
        proto = self.headers.get("X-Forwarded-Proto")
        if not proto:
            proto = "https" if "https" in (self.headers.get("CF-Visitor") or "") else "http"
        return "%s://%s" % (proto, host)

    def viewer_email(self):
        """Email of the person viewing the page, verified from the Cloudflare Access JWT."""
        if ACCESS.enabled:
            token = self.headers.get("Cf-Access-Jwt-Assertion") or ""
            if not token:
                for part in (self.headers.get("Cookie") or "").split(";"):
                    name, _, val = part.strip().partition("=")
                    if name == "CF_Authorization":
                        token = val
            return ACCESS.email(token)
        return CFG["LB_DEV_EMAIL"].lower() or None

    def same_origin_write(self):
        """Writes need our custom header (forces a CORS preflight cross-site) and a matching Origin."""
        if self.headers.get("X-LB-Request") != "1":
            return False
        origin = self.headers.get("Origin")
        if origin and origin.rstrip("/") != self.public_url().rstrip("/"):
            return False
        return True

    def cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            key, _, val = part.strip().partition("=")
            if key == name:
                return val
        return None

    def cookie_attrs(self, max_age, same_site="Lax"):
        secure = "; Secure" if self.public_url().startswith("https://") else ""
        return "Path=/; Max-Age=%d; HttpOnly; SameSite=%s%s" % (max_age, same_site, secure)

    def viewer(self):
        """(login email or None, participant row or None) for whoever is looking at the page.

        With Cloudflare Access the participant comes from the verified email. Without it, a viewer
        proves who they are once with their lb_ token and gets a signed cookie."""
        email = self.viewer_email()
        with DB_LOCK:
            row = employee_by_email(email) if email else None
            if row is None and not ACCESS.enabled:
                value = unsign(self.cookie("lb_me"))
                if value and value.isdigit():
                    row = employee_by_id(int(value))
        return email, row

    def is_admin(self):
        email = self.viewer_email()
        if email and email in ADMINS:
            return email
        if ADMIN_COOKIE and hmac.compare_digest(self.cookie("lb_admin") or "", ADMIN_COOKIE):
            return "admin-key"
        return None

    def read_body(self, limit):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length < 0 or length > limit:
            self.close_connection = True
            return None
        return self.rfile.read(length) if length else b""

    def view_allowed(self):
        key = CFG["LB_VIEW_KEY"]
        if not key:
            return True
        return hmac.compare_digest(self.cookie("lb_view") or "", key)

    # -- routes
    def do_HEAD(self):
        self.do_GET()

    def set_lang(self):
        """The visitor's choice from the language button (lb_lang cookie), else LB_LANG.
        Installers and telemetry come without the cookie, so they keep the server default."""
        choice = self.cookie("lb_lang")
        _REQ.lang = choice if choice in ("en", "id") else LANG

    def do_GET(self):
        self.set_lang()
        url = urlparse(self.path)
        path = url.path
        qs = parse_qs(url.query)
        try:
            if path == "/healthz":
                return self.send_json(200, {"ok": True})
            if path == "/v1/metrics/backfill":
                employee_id = find_employee(self.headers.get("x-lb-token") or "")
                if employee_id is None:
                    return self.send_json(401, {"error": "invalid token"})
                with DB_LOCK:
                    start, cutoff = backfill_window(employee_id)
                return self.send_json(200, {"start": start, "cutoff": cutoff}, {"Cache-Control": "no-store"})
            if path == "/api/me":
                email, row = self.viewer()
                return self.send_json(200, me_payload(email, row, self.is_admin()), {"Cache-Control": "no-store"})
            if path in ("/admin", "/admin/"):
                if self.is_admin():
                    return self.send_template("admin.html", "text/html; charset=utf-8")
                if ADMIN_KEY:
                    return self.send_template("admin-login.html", "text/html; charset=utf-8")
                return self.send(403, T("This page is for leaderboard admins.\n", "Halaman ini khusus admin leaderboard.\n"),
                                 "text/plain; charset=utf-8", {"Cache-Control": "no-store"})
            if path == "/api/admin/participants":
                actor = self.is_admin()
                if not actor:
                    return self.send_json(403, {"error": T("Admins only.", "Khusus admin.")})
                data = admin_list()
                data["signed_in_with_key"] = actor == "admin-key"
                return self.send_json(200, data, {"Cache-Control": "no-store"})
            m = re.fullmatch(r"/avatars/(\d+)", path)
            if m and not self.view_allowed():
                return self.send(401, b"", "text/plain")
            if m:
                with DB_LOCK:
                    row = DB.execute("SELECT avatar_ext FROM employees WHERE id = ? AND active = 1", (int(m.group(1)),)).fetchone()
                fpath = row and row["avatar_ext"] and os.path.join(AVATARS, "%s.%s" % (m.group(1), row["avatar_ext"]))
                if not fpath or not os.path.exists(fpath):
                    return self.send(404, "not found\n", "text/plain")
                with open(fpath, "rb") as fh:
                    data = fh.read()
                return self.send(200, data, AVATAR_CTYPE[row["avatar_ext"]], {
                    "Cache-Control": "private, max-age=31536000, immutable",
                    "Content-Security-Policy": "default-src 'none'; sandbox",
                })
            if path == "/install.sh":
                return self.send_template("install.sh", "text/x-shellscript; charset=utf-8")
            if path == "/install.ps1":
                return self.send_template("install.ps1", "text/plain; charset=utf-8")
            if path in ("/", "/index.html", "/join", "/api/leaderboard", "/api/participant"):
                if CFG["LB_VIEW_KEY"] and "key" in qs and path in ("/", "/index.html", "/join"):
                    if hmac.compare_digest(qs["key"][0], CFG["LB_VIEW_KEY"]):
                        return self.send(302, b"", "text/plain", {
                            "Location": path,
                            "Set-Cookie": "lb_view=%s; %s" % (CFG["LB_VIEW_KEY"], self.cookie_attrs(31536000)),
                        })
                if not self.view_allowed():
                    return self.send(401, T("You need the leaderboard access link.\n", "Butuh link akses leaderboard.\n"),
                                     "text/plain; charset=utf-8")
                if path == "/api/participant":
                    period = (qs.get("period") or ["7d"])[0]
                    if period not in PERIODS:
                        period = "7d"
                    try:
                        pid = int((qs.get("id") or [""])[0])
                    except ValueError:
                        return self.send_json(400, {"error": "id"})
                    source = (qs.get("source") or ["all"])[0]
                    if source not in SOURCES:
                        source = "all"
                    _, me = self.viewer()
                    data = participant(pid, period, me["id"] if me else None, source)
                    if data is None:
                        return self.send_json(404, {"error": "not found"})
                    return self.send_json(200, data, {"Cache-Control": "no-store"})
                if path == "/api/leaderboard":
                    period = (qs.get("period") or ["7d"])[0]
                    if period not in PERIODS:
                        period = "7d"
                    source = (qs.get("source") or ["all"])[0]
                    if source not in SOURCES:
                        source = "all"
                    _, me = self.viewer()
                    return self.send_json(200, leaderboard(period, me["id"] if me else None, source), {"Cache-Control": "no-store"})
                page = "join.html" if path == "/join" else "index.html"
                return self.send_template(page, "text/html; charset=utf-8")
            return self.send(404, "not found\n", "text/plain")
        except Exception as exc:  # pragma: no cover
            log("GET %s failed: %r" % (path, exc))
            return self.send(500, "error\n", "text/plain")

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path != "/api/me/avatar":
            return self.send(404, "not found\n", "text/plain")
        if not self.same_origin_write():
            return self.send_json(403, {"error": "forbidden"})
        email, me = self.viewer()
        if not me:
            return self.send_json(403, {"error": T("Link your name first.", "Hubungkan nama kamu dulu.")})
        with DB_LOCK:
            DB.execute("UPDATE employees SET avatar_ext = NULL, avatar_ver = NULL WHERE id = ?", (me["id"],))
            remove_avatar(me["id"])
            me = employee_by_id(me["id"])
        return self.send_json(200, me_payload(email, me, self.is_admin()))

    def admin_post(self, path):
        if not self.same_origin_write():
            return self.send_json(403, {"error": "forbidden"})
        raw = self.read_body(16384)
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.send_json(400, {"error": T("Invalid data.", "Data tidak valid.")})
        if path == "/api/admin/login":
            if not ADMIN_KEY:
                return self.send_json(404, {"error": "not found"})
            if not hmac.compare_digest(str(body.get("key") or "").encode(), ADMIN_KEY.encode()):
                time.sleep(1)  # slow down guessing
                log("admin login failed from %s" % (self.headers.get("CF-Connecting-IP") or self.client_address[0]))
                return self.send_json(403, {"error": T("Wrong admin key.", "Kunci admin salah.")})
            log("admin login with key from %s" % (self.headers.get("CF-Connecting-IP") or self.client_address[0]))
            return self.send_json(200, {"ok": True}, {
                "Set-Cookie": "lb_admin=%s; %s" % (ADMIN_COOKIE, self.cookie_attrs(30 * 86400, "Strict"))})
        if path == "/api/admin/logout":
            return self.send_json(200, {"ok": True}, {"Set-Cookie": "lb_admin=; %s" % self.cookie_attrs(0, "Strict")})
        actor = self.is_admin()
        if not actor:
            return self.send_json(403, {"error": T("Admins only.", "Khusus admin.")})
        parts = path[len("/api/admin/"):].strip("/").split("/")
        try:
            return self.send_json(200, admin_action(actor, "POST", parts, body, self.public_url()), {"Cache-Control": "no-store"})
        except AdminError as exc:
            return self.send_json(exc.code, {"error": str(exc)})
        except Exception as exc:
            log("admin POST %s failed: %r" % (path, exc))
            return self.send_json(500, {"error": T("Could not save. Try again.", "Gagal menyimpan, coba lagi.")})

    def api_post(self, path):
        if not self.same_origin_write():
            return self.send_json(403, {"error": "forbidden"})
        email = self.viewer_email()
        if ACCESS.enabled and not email:
            return self.send_json(401, {"error": T("Sign in through Cloudflare Access first.", "Login lewat Cloudflare Access dulu.")})
        if path == "/api/me/link":
            body = self.read_body(4096)
            try:
                token = str(json.loads(body or b"{}").get("token") or "").strip()
            except (ValueError, AttributeError):
                token = ""
            if email and not EMAIL_RE.match(email):
                return self.send_json(400, {"error": T("Invalid login email.", "Email login tidak valid.")})
            with DB_LOCK:
                row = DB.execute("SELECT id, name, email, active FROM employees WHERE token_hash = ?", (hash_token(token),)).fetchone() if token else None
            if not row or not row["active"]:
                time.sleep(0.5)
                return self.send_json(400, {"error": T("Unknown token. Copy the lb_... part of your install command.",
                                                       "Token tidak dikenal. Salin bagian lb_... dari perintah install kamu.")})
            if not email:
                # No Access login: remember who this browser belongs to in a signed cookie.
                log("linked a browser to employee %s" % row["id"])
                with DB_LOCK:
                    me = employee_by_id(row["id"])
                return self.send_json(200, me_payload(None, me, self.is_admin()), {
                    "Set-Cookie": "lb_me=%s; %s" % (sign(str(row["id"])), self.cookie_attrs(31536000))})
            if row["email"] and row["email"].lower() != email:
                return self.send_json(409, {"error": T("%s is already linked to another email. Ask an admin to move it.",
                                                       "Nama %s sudah terhubung ke email lain. Minta admin untuk memindahkannya.") % row["name"]})
            with DB_LOCK:
                DB.execute("BEGIN IMMEDIATE")
                DB.execute("UPDATE employees SET email = NULL WHERE email = ? COLLATE NOCASE AND id != ?", (email, row["id"]))
                DB.execute("UPDATE employees SET email = ? WHERE id = ?", (email, row["id"]))
                DB.execute("COMMIT")
                me = employee_by_id(row["id"])
            log("linked %s to employee %s" % (email, row["id"]))
            return self.send_json(200, me_payload(email, me, self.is_admin()))
        if path == "/api/me/avatar":
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            body = self.read_body(AVATAR_MAX)
            if body is None:
                return self.send_json(413, {"error": T("Photo too large (max. 600 KB after resizing).", "Foto terlalu besar (maks. 600 KB setelah dikecilkan).")})
            ext = sniff_image(body)
            if ctype not in AVATAR_TYPES or ext != AVATAR_TYPES[ctype]:
                return self.send_json(415, {"error": T("Use a JPG, PNG or WebP photo.", "Pakai foto JPG, PNG, atau WebP.")})
            email, me = self.viewer()
            if not me:
                return self.send_json(403, {"error": T("Link your name first.", "Hubungkan nama kamu dulu.")})
            with DB_LOCK:
                save_avatar(me["id"], body, ext)
                DB.execute("UPDATE employees SET avatar_ext = ?, avatar_ver = ? WHERE id = ?", (ext, int(time.time() * 1000), me["id"]))
                me = employee_by_id(me["id"])
            return self.send_json(200, me_payload(email, me, self.is_admin()))
        return self.send(404, "not found\n", "text/plain")

    def do_POST(self):
        self.set_lang()
        path = urlparse(self.path).path
        if path.startswith("/api/admin/"):
            return self.admin_post(path)
        if path in ("/api/me/link", "/api/me/avatar"):
            try:
                return self.api_post(path)
            except Exception as exc:
                log("POST %s failed: %r" % (path, exc))
                return self.send_json(500, {"error": T("Could not save. Try again.", "Gagal menyimpan, coba lagi.")})
        if path not in ("/v1/metrics", "/v1/metrics/backfill", "/v1/metrics/9router"):
            return self.send(404, "not found\n", "text/plain")
        token = self.headers.get("x-lb-token") or ""
        auth = self.headers.get("Authorization") or ""
        if not token and auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            return self.send_json(413, {"error": "body too large"})
        body = self.rfile.read(length) if length else b""
        employee_id = find_employee(token)
        if employee_id is None:
            return self.send_json(401, {"error": "invalid token"})
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self.send_json(415, {"error": "use OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=http/json"})
        enc = (self.headers.get("Content-Encoding") or "").lower()
        try:
            if enc == "gzip":
                body = gzip.decompress(body)
            elif enc == "deflate":
                body = zlib.decompress(body)
            if len(body) > MAX_BODY * 4:
                return self.send_json(413, {"error": "body too large"})
            payload = json.loads(body or b"{}")
        except (OSError, ValueError, zlib.error):
            return self.send_json(400, {"error": "bad json"})
        if path == "/v1/metrics/9router":
            try:
                result = store_router(employee_id, payload if isinstance(payload, dict) else {})
            except ValueError as exc:
                return self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                log("9router sync failed for employee %s: %r" % (employee_id, exc))
                return self.send_json(500, {"error": "9router sync failed"})
            return self.send_json(200, result)
        if path == "/v1/metrics/backfill":
            try:
                result = store_backfill(employee_id, payload if isinstance(payload, dict) else {})
            except ValueError as exc:
                return self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                log("backfill failed for employee %s: %r" % (employee_id, exc))
                return self.send_json(500, {"error": "backfill failed"})
            log("employee %s: backfill %s" % (employee_id, result))
            return self.send_json(200, result)
        try:
            _, rejected = ingest(employee_id, payload if isinstance(payload, dict) else {})
        except Exception as exc:
            log("ingest failed for employee %s: %r" % (employee_id, exc))
            return self.send_json(500, {"error": "ingest failed"})
        if rejected:
            log("employee %s: rejected %d out-of-range data points" % (employee_id, rejected))
        return self.send_json(200, {})

    def send_template(self, name, ctype):
        with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
            text = fh.read()
        title = CFG["LB_TITLE"] if name.startswith("install.") else html.escape(CFG["LB_TITLE"])
        text = text.replace("__LB_URL__", self.public_url()).replace("__LB_TITLE__", title).replace("__LB_LANG__", lang())
        text = localize(text)
        if name.startswith("install."):
            # Installers sit behind the Access service-token app, so whoever can download them
            # already holds these values; they get written into the OTel headers.
            text = text.replace("__LB_CF_ID__", CFG["LB_CF_CLIENT_ID"]).replace("__LB_CF_SECRET__", CFG["LB_CF_CLIENT_SECRET"])
        headers = {"Cache-Control": "no-store" if name.startswith("install.") else "no-cache", "Vary": "Cookie"}
        if ctype.startswith("text/html"):
            headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        return self.send(200, text, ctype, headers)


def main():
    server = ThreadingHTTPServer((CFG["LB_HOST"], int(CFG["LB_PORT"])), Handler)
    server.daemon_threads = True
    log("listening on %s:%s db=%s" % (CFG["LB_HOST"], CFG["LB_PORT"], CFG["LB_DB"]))
    server.serve_forever()


if __name__ == "__main__":
    main()
