#!/usr/bin/env python3
"""Manage leaderboard participants from the command line (the web admin at /admin does the same).

  ./manage.py add "Name" [--team "Team"]           create a participant, print token + install command
  ./manage.py list                                 list participants
  ./manage.py rotate "Name"                        new token (old one stops working)
  ./manage.py revoke "Name" | activate "Name"      hide / show on the board (revoked tokens are rejected)
  ./manage.py rename "Old Name" "New Name"
  ./manage.py team "Name" "Team"
  ./manage.py email "Name" name@example.com        link the login email (for photo upload); "-" to unlink
  ./manage.py delete "Name" --yes                  remove participant and all their usage
  ./manage.py demo                                 fill an empty database with made-up people to try it out
"""
import argparse
import os
import sys
import time
from datetime import datetime

from lbcommon import T, connect, delete_employee, hash_token, install_commands, load_config, new_token

CFG = load_config()


def t(en, id_):
    return T(CFG, en, id_)


def print_instructions(name, token):
    cmds = install_commands(CFG, token)
    print()
    print(t("Participant: %s", "Peserta : %s") % name)
    print(t("Token      : %s   (keep it, it cannot be shown again)", "Token   : %s   (simpan, tidak bisa dilihat lagi)") % token)
    print()
    print(t("Send this to %s in a private chat (not a group):", "Kirim perintah ini ke %s lewat chat pribadi (jangan di grup):") % name)
    print()
    print("macOS / Linux:")
    print("  " + cmds["sh"])
    print()
    print("Windows (PowerShell):")
    print("  " + cmds["ps"])
    print()
    print(t("9router (run on the Linux machine where 9router runs, as the same user):",
            "9router (jalankan di mesin Linux tempat 9router berjalan, dengan user yang sama):"))
    print("  " + cmds["router"])
    print()


def get(conn, name):
    row = conn.execute("SELECT * FROM employees WHERE name = ?", (name,)).fetchone()
    if not row:
        sys.exit(t("No participant named %r", "Tidak ada peserta bernama %r") % name)
    return row


def fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


DEMO_PEOPLE = [
    ("Ada Lovelace", "Platform", 1.6), ("Grace Hopper", "Platform", 1.25), ("Alan Turing", "Research", 1.05),
    ("Katherine Johnson", "Data", 0.8), ("Linus Torvalds", "Infra", 0.62), ("Margaret Hamilton", "Mobile", 0.45),
    ("Dennis Ritchie", "Infra", 0.3), ("Barbara Liskov", "Research", 0.0),
]
DEMO_MODELS = [("claude-sonnet-5-5", 0.62, 15.0), ("claude-opus-5-5", 0.28, 75.0), ("claude-haiku-5-5", 0.10, 5.0)]


def demo(conn):
    """Made-up participants with 45 days of plausible usage, so a fresh install has something to show."""
    import random
    if conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]:
        sys.exit(t("The database already has participants; demo data only goes into an empty one.",
                   "Database sudah berisi peserta; data demo hanya untuk database kosong."))
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(CFG["LB_TZ"])
    rnd = random.Random(7)
    now = int(time.time()) // 3600 * 3600
    rows = []
    for name, team, weight in DEMO_PEOPLE:
        cur = conn.execute(
            "INSERT INTO employees(name, team, token_hash, created_at, last_seen, first_otel_at) VALUES (?,?,?,?,?,?)",
            (name, team, hash_token(new_token()), now - 45 * 86400, now - rnd.randint(60, 7200) if weight else None,
             now - 45 * 86400))
        eid = cur.lastrowid
        if not weight:
            continue
        for h in range(now - 45 * 86400, now + 3600, 3600):
            local = datetime.fromtimestamp(h, tz)
            workday = local.weekday() < 5
            if not (9 <= local.hour <= 19) or rnd.random() > (0.55 if workday else 0.08):
                continue
            output = int(rnd.lognormvariate(10.1, 0.7) * weight)
            for model, share, price in DEMO_MODELS:
                out = int(output * share * rnd.uniform(0.6, 1.4))
                if out <= 0:
                    continue
                inp = int(out * rnd.uniform(0.02, 0.06))
                cread = int(out * rnd.uniform(60, 140))
                cwrite = int(out * rnd.uniform(2, 5))
                for typ, val in (("output", out), ("input", inp), ("cacheRead", cread), ("cacheCreation", cwrite)):
                    rows.append((eid, h, "claude_code.token.usage", typ, model, val))
                rows.append((eid, h, "claude_code.cost.usage", "", model, round((out * price + inp * price / 5 + cread * price / 50 + cwrite * price / 4) / 1e6, 4)))
            if rnd.random() < 0.35:
                rows.append((eid, h, "claude_code.session.count", "", "", 1))
            rows.append((eid, h, "claude_code.active_time.total", "", "", rnd.randint(300, 2400)))
    conn.executemany("INSERT OR REPLACE INTO usage_hourly(employee_id, hour, metric, type, model, value) VALUES (?,?,?,?,?,?)", rows)
    print(t("Added %d demo participants. Remove them later with ./manage.py delete \"Name\" --yes.",
            "Menambahkan %d peserta demo. Hapus nanti dengan ./manage.py delete \"Nama\" --yes.") % len(DEMO_PEOPLE))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("name"); a.add_argument("--team", default="")
    sub.add_parser("list")
    for c in ("rotate", "revoke", "activate"):
        sub.add_parser(c).add_argument("name")
    r = sub.add_parser("rename"); r.add_argument("old"); r.add_argument("new")
    tp = sub.add_parser("team"); tp.add_argument("name"); tp.add_argument("team")
    em = sub.add_parser("email"); em.add_argument("name"); em.add_argument("email")
    d = sub.add_parser("delete"); d.add_argument("name"); d.add_argument("--yes", action="store_true")
    sub.add_parser("demo")
    args = p.parse_args()

    conn = connect(CFG)
    if args.cmd == "add":
        name = args.name.strip()
        if not name or len(name) > 60:
            sys.exit(t("Name must be 1-60 characters", "Nama harus 1-60 karakter"))
        if conn.execute("SELECT 1 FROM employees WHERE name = ?", (name,)).fetchone():
            sys.exit(t("%r already exists (use rotate for a new token)", "Nama %r sudah ada (pakai rotate kalau butuh token baru)") % name)
        token = new_token()
        conn.execute(
            "INSERT INTO employees(name, team, token_hash, created_at) VALUES (?,?,?,?)",
            (name, args.team.strip(), hash_token(token), int(time.time())),
        )
        print_instructions(name, token)
    elif args.cmd == "list":
        rows = conn.execute("SELECT * FROM employees ORDER BY name COLLATE NOCASE").fetchall()
        if not rows:
            print(t("No participants yet. Add one with: ./manage.py add \"Name\"", "Belum ada peserta. Tambah dengan: ./manage.py add \"Nama\""))
        runs = {}
        for r in conn.execute("SELECT employee_id, COUNT(*) AS n, MAX(at) AS at FROM backfill_runs GROUP BY employee_id"):
            runs[r["employee_id"]] = r
        for row in rows:
            r = runs.get(row["id"])
            bf = t("%d computer(s), %s", "%d laptop, %s") % (r["n"], fmt_ts(r["at"])) if r else "-"
            print(t("%-22s %-14s %-8s last data: %-16s  history: %-24s  email: %s%s",
                    "%-22s %-14s %-8s terakhir kirim: %-16s  backfill: %-22s  email: %s%s") % (
                row["name"], row["team"] or "-", t("active", "aktif") if row["active"] else t("inactive", "nonaktif"),
                fmt_ts(row["last_seen"]), bf, row["email"] or "-", t("  (has photo)", "  (ada foto)") if row["avatar_ext"] else ""))
    elif args.cmd == "rotate":
        row = get(conn, args.name)
        token = new_token()
        conn.execute("UPDATE employees SET token_hash = ? WHERE id = ?", (hash_token(token), row["id"]))
        print(t("The old token of %s no longer works. Run the installer again with the new token:",
                "Token lama %s sudah tidak berlaku. Jalankan ulang installer dengan token baru:") % row["name"])
        print_instructions(row["name"], token)
    elif args.cmd in ("revoke", "activate"):
        row = get(conn, args.name)
        conn.execute("UPDATE employees SET active = ? WHERE id = ?", (1 if args.cmd == "activate" else 0, row["id"]))
        print(t("%s is now %s", "%s sekarang %s") % (row["name"], t("active", "aktif") if args.cmd == "activate" else t("inactive", "nonaktif")))
    elif args.cmd == "rename":
        row = get(conn, args.old)
        conn.execute("UPDATE employees SET name = ? WHERE id = ?", (args.new.strip(), row["id"]))
        print(t("Renamed: %s -> %s", "Diganti: %s -> %s") % (row["name"], args.new.strip()))
    elif args.cmd == "team":
        row = get(conn, args.name)
        conn.execute("UPDATE employees SET team = ? WHERE id = ?", (args.team.strip(), row["id"]))
        print(t("%s -> team %s", "%s -> tim %s") % (row["name"], args.team.strip()))
    elif args.cmd == "email":
        row = get(conn, args.name)
        addr = None if args.email == "-" else args.email.strip().lower()
        if addr and ("@" not in addr or " " in addr):
            sys.exit(t("Invalid email", "Email tidak valid"))
        if addr:
            conn.execute("UPDATE employees SET email = NULL WHERE email = ? COLLATE NOCASE AND id != ?", (addr, row["id"]))
        conn.execute("UPDATE employees SET email = ? WHERE id = ?", (addr, row["id"]))
        print("%s -> %s" % (row["name"], addr or t("(no email)", "(tanpa email)")))
    elif args.cmd == "demo":
        demo(conn)
    elif args.cmd == "delete":
        row = get(conn, args.name)
        if not args.yes:
            sys.exit(t("This deletes %s and all their data. Repeat with --yes", "Ini menghapus %s beserta semua datanya. Ulangi dengan --yes") % row["name"])
        delete_employee(conn, row["id"], os.path.join(os.path.dirname(CFG["LB_DB"]), "avatars"))
        print(t("Deleted: %s", "Dihapus: %s") % row["name"])


if __name__ == "__main__":
    main()
