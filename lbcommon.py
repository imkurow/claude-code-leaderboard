"""Shared config + database helpers for the Claude Code token leaderboard."""
import hashlib
import os
import secrets
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    """Read config.env (KEY=VALUE lines) next to this file; real env vars win."""
    cfg = {}
    path = os.path.join(BASE, "config.env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                cfg[key.strip()] = val.strip().strip('"').strip("'")
    for key in list(cfg) + [
        "LB_HOST", "LB_PORT", "LB_DB", "LB_TZ", "LB_PUBLIC_URL", "LB_VIEW_KEY", "LB_TITLE",
        "LB_CF_CLIENT_ID", "LB_CF_CLIENT_SECRET", "LB_CF_TEAM_DOMAIN", "LB_CF_VIEWER_AUD", "LB_DEV_EMAIL",
        "LB_ADMIN_EMAILS", "LB_ADMIN_KEY", "LB_LANG",
    ]:
        if key in os.environ:
            cfg[key] = os.environ[key]
    cfg.setdefault("LB_HOST", "127.0.0.1")
    cfg.setdefault("LB_PORT", "8790")
    cfg.setdefault("LB_DB", os.path.join(BASE, "data", "leaderboard.db"))
    cfg.setdefault("LB_TZ", "UTC")
    cfg.setdefault("LB_PUBLIC_URL", "")
    cfg.setdefault("LB_VIEW_KEY", "")
    cfg.setdefault("LB_TITLE", "Claude Code Leaderboard")
    # Interface language: en or id.
    cfg.setdefault("LB_LANG", "en")
    # Cloudflare Access service token that Claude Code sends with every export (empty = no Access).
    cfg.setdefault("LB_CF_CLIENT_ID", "")
    cfg.setdefault("LB_CF_CLIENT_SECRET", "")
    # Access login for viewers: team domain (xxx.cloudflareaccess.com) + the viewer app's AUD tag.
    # With both set, the viewer's email comes from a verified Cf-Access-Jwt-Assertion.
    cfg.setdefault("LB_CF_TEAM_DOMAIN", "")
    cfg.setdefault("LB_CF_VIEWER_AUD", "")
    # Local development only: pretend to be this email (ignored when LB_CF_TEAM_DOMAIN is set).
    cfg.setdefault("LB_DEV_EMAIL", "")
    # Who may open /admin (comma-separated login emails, checked against the Access JWT).
    cfg.setdefault("LB_ADMIN_EMAILS", "")
    # Or a passphrase for /admin, for setups without Cloudflare Access.
    cfg.setdefault("LB_ADMIN_KEY", "")
    cfg["LB_PUBLIC_URL"] = cfg["LB_PUBLIC_URL"].rstrip("/")
    return cfg


SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    team        TEXT NOT NULL DEFAULT '',
    token_hash  TEXT NOT NULL UNIQUE,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    last_seen   INTEGER
);
-- Usage summed into hourly buckets (hour = unix epoch at the start of the hour).
CREATE TABLE IF NOT EXISTS usage_hourly (
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    hour        INTEGER NOT NULL,
    metric      TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    value       REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (employee_id, hour, metric, type, model)
);
CREATE INDEX IF NOT EXISTS usage_hourly_hour ON usage_hourly(hour);
-- History read from local Claude Code transcripts (before OTel was installed), per machine.
-- A re-run replaces that machine's rows, so it is safe to repeat.
CREATE TABLE IF NOT EXISTS usage_backfill (
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    machine     TEXT NOT NULL,
    hour        INTEGER NOT NULL,
    metric      TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    value       REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (employee_id, machine, hour, metric, type, model)
);
CREATE TABLE IF NOT EXISTS backfill_runs (
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    machine     TEXT NOT NULL,
    at          INTEGER NOT NULL,
    cutoff      INTEGER NOT NULL,
    messages    INTEGER NOT NULL,
    PRIMARY KEY (employee_id, machine)
);
-- Badges, kept from the first time they are reached (earned_at: when, as far as the data tells;
-- detected_at: when the server noticed, for the live "unlocked" notices).
CREATE TABLE IF NOT EXISTS achievements (
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    badge       TEXT NOT NULL,
    earned_at   INTEGER NOT NULL,
    detected_at INTEGER NOT NULL,
    PRIMARY KEY (employee_id, badge)
);
-- Last seen value per series, only used if a client sends cumulative temporality.
CREATE TABLE IF NOT EXISTS cumulative_state (
    series   TEXT PRIMARY KEY,
    start_ns INTEGER NOT NULL,
    value    REAL NOT NULL,
    updated  INTEGER NOT NULL
);
"""


def connect(cfg):
    path = cfg["LB_DB"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(employees)")}
    if "first_otel_at" not in cols:
        # Time of the first real OTel token data; transcript backfill only counts what came before it.
        # For people who were already sending, the first hour bucket is the best we know.
        conn.execute("ALTER TABLE employees ADD COLUMN first_otel_at INTEGER")
        conn.execute(
            "UPDATE employees SET first_otel_at = (SELECT MIN(hour) FROM usage_hourly u "
            "WHERE u.employee_id = employees.id AND u.metric = 'claude_code.token.usage')"
        )
    if "email" not in cols:
        conn.execute("ALTER TABLE employees ADD COLUMN email TEXT")
    if "avatar_ext" not in cols:
        conn.execute("ALTER TABLE employees ADD COLUMN avatar_ext TEXT")
        conn.execute("ALTER TABLE employees ADD COLUMN avatar_ver INTEGER")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS employees_email ON employees(email COLLATE NOCASE) WHERE email IS NOT NULL")
    return conn


def T(cfg, en, id_):
    """Pick the message in the configured language."""
    return id_ if cfg.get("LB_LANG", "en").lower().startswith("id") else en


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token():
    return "lb_" + secrets.token_urlsafe(24)


def install_commands(cfg, token, url=None):
    """The install one-liners for a token (they carry the Access service-token headers):
    sh (macOS/Linux), ps (Windows), router / router_ps for a 9router machine, jrouter / jrouter_ps for JRouter."""
    url = (url or cfg["LB_PUBLIC_URL"] or "https://<DOMAIN-LEADERBOARD>").rstrip("/")
    cid, csec = cfg["LB_CF_CLIENT_ID"], cfg["LB_CF_CLIENT_SECRET"]
    sh_hdr = ps_hdr = ""
    if cid and csec:
        sh_hdr = "-H 'CF-Access-Client-Id: %s' -H 'CF-Access-Client-Secret: %s' " % (cid, csec)
        ps_hdr = " -Headers @{'CF-Access-Client-Id'='%s';'CF-Access-Client-Secret'='%s'}" % (cid, csec)
    return {
        "sh": "curl -fsSL %s%s/install.sh | bash -s -- %s" % (sh_hdr, url, token),
        "router": "curl -fsSL %s%s/install.sh | bash -s -- %s --9router" % (sh_hdr, url, token),
        "router_ps": "$env:LB_TOKEN='%s'; $env:LB_9ROUTER='1'; irm %s/install.ps1%s | iex" % (token, url, ps_hdr),
        "jrouter": "curl -fsSL %s%s/install.sh | bash -s -- %s --jrouter" % (sh_hdr, url, token),
        "jrouter_ps": "$env:LB_TOKEN='%s'; $env:LB_JROUTER='1'; irm %s/install.ps1%s | iex" % (token, url, ps_hdr),
        "ps": "$env:LB_TOKEN='%s'; irm %s/install.ps1%s | iex" % (token, url, ps_hdr),
    }


def delete_employee(conn, employee_id, avatars_dir):
    """Remove a participant with all their usage, history and photo."""
    conn.execute("DELETE FROM usage_hourly WHERE employee_id = ?", (employee_id,))
    conn.execute("DELETE FROM usage_backfill WHERE employee_id = ?", (employee_id,))
    conn.execute("DELETE FROM backfill_runs WHERE employee_id = ?", (employee_id,))
    conn.execute("DELETE FROM achievements WHERE employee_id = ?", (employee_id,))
    conn.execute("DELETE FROM cumulative_state WHERE series LIKE ?", ("[%d,%%" % employee_id,))
    conn.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
    for ext in ("jpg", "png", "webp"):
        path = os.path.join(avatars_dir, "%d.%s" % (employee_id, ext))
        if os.path.exists(path):
            os.remove(path)
