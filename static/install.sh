#!/usr/bin/env bash
# __LB_TITLE__ - Claude Code telemetry setup (macOS / Linux)
#
#   ⟦Install‖Pasang⟧ : ⟦get the full command (with CF-Access-* headers) from the admin‖perintah lengkap (dengan header CF-Access-*) didapat dari admin⟧
#   ⟦Remove‖Hapus⟧  : bash -s -- --uninstall  (⟦use the same download command‖pakai perintah download yang sama⟧)
#   9router : bash -s -- <TOKEN> --9router   (⟦on the Linux machine that runs 9router; remove with‖di mesin Linux yang menjalankan 9router; hapus dengan⟧ --uninstall --9router)
#
# ⟦What changes: the "env" block in ~/.claude/settings.json (a backup is made first).‖Yang diubah: blok "env" di ~/.claude/settings.json (backup dibuat dulu).⟧
# ⟦What Claude Code sends: metric numbers (token counts per type & model, cost estimate,
# session count, active time). Prompt contents, code, and files are NOT sent.‖Yang dikirim Claude Code: angka metrik (jumlah token per tipe & model, estimasi biaya,
# jumlah sesi, waktu aktif). Isi prompt, kode, dan file TIDAK ikut terkirim.⟧
# ⟦The installer also uploads token history from local transcripts (up to 90 days, from before
# telemetry was enabled), as hourly totals per model only. Safe to run again.‖Installer juga mengirim riwayat token dari transkrip lokal (maks. 90 hari, sebelum
# telemetry aktif), hanya berupa total per jam & model. Aman dijalankan ulang.⟧
set -euo pipefail

LB_ENDPOINT="__LB_URL__/v1/metrics"
CF_ID="__LB_CF_ID__"
CF_SECRET="__LB_CF_SECRET__"
case "$CF_ID" in __*) CF_ID="" ;; esac
case "$CF_SECRET" in __*) CF_SECRET="" ;; esac
SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
MODE=install
ROUTER=0
TOKEN=""
for arg in "$@"; do
  case "$arg" in
    --uninstall) MODE=uninstall ;;
    --9router) ROUTER=1 ;;
    -*) echo "⟦Unknown option‖Opsi tidak dikenal⟧: $arg" >&2; exit 2 ;;
    *) TOKEN="$arg" ;;
  esac
done

if [ "$MODE" = install ]; then
  if [ -z "$TOKEN" ]; then
    echo "⟦Use the install command from the admin (it ends with‖Pakai perintah install dari admin (diakhiri⟧: bash -s -- <TOKEN>)" >&2
    exit 2
  fi
  case "$TOKEN" in *[!A-Za-z0-9_-]*) echo "⟦Invalid token format.‖Format token tidak valid.⟧" >&2; exit 2 ;; esac
  LB_HEADERS="x-lb-token=$TOKEN"
  cf_args=()
  if [ -n "$CF_ID" ]; then
    LB_HEADERS="$LB_HEADERS,CF-Access-Client-Id=$CF_ID,CF-Access-Client-Secret=$CF_SECRET"
    cf_args=(-H "CF-Access-Client-Id: $CF_ID" -H "CF-Access-Client-Secret: $CF_SECRET")
  fi
  printf '⟦Checking token with the server... ‖Cek token ke server... ⟧'
  code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$LB_ENDPOINT" ${cf_args[@]+"${cf_args[@]}"} \
    -H 'Content-Type: application/json' -H "x-lb-token: $TOKEN" \
    --data '{"resourceMetrics":[]}' || true)
  case "$code" in
    200) echo "OK" ;;
    401) echo "⟦rejected. The token is wrong or has been revoked; ask the admin for a new one.‖ditolak. Token salah atau sudah dicabut, minta token baru ke admin.⟧" >&2; exit 1 ;;
    302|403) echo "⟦rejected by Cloudflare Access. Ask the admin for the latest install command.‖ditolak Cloudflare Access. Minta perintah install terbaru ke admin.⟧" >&2; exit 1 ;;
    *) echo "⟦failed‖gagal⟧ (HTTP $code). ⟦Could not reach the leaderboard server.‖Server leaderboard tidak bisa dihubungi.⟧" >&2; exit 1 ;;
  esac
fi

# ---------------------------------------------------------------------------
# 9router: instead of Claude Code telemetry, read 9router's own usage log on this machine
# every minute (cron) and send hourly totals per model. Prompts and responses are not read.
# ---------------------------------------------------------------------------
if [ "$ROUTER" = 1 ]; then
  R_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/claude-leaderboard"
  R_SCRIPT="$R_DIR/9router-sync.py"
  R_TAG="# claude-leaderboard 9router sync"
  if [ "$MODE" = uninstall ]; then
    if command -v crontab >/dev/null 2>&1; then
      (crontab -l 2>/dev/null | grep -vF "$R_TAG" || true) | crontab - || true
    fi
    rm -f "$R_SCRIPT" "$R_DIR/9router.env" "$R_DIR/9router.state" "$R_DIR/9router.lock" "$R_DIR/9router-sync.log"
    echo "⟦9router sync removed. Data already on the leaderboard stays.‖Sinkron 9router dihapus. Data yang sudah ada di leaderboard tetap.⟧"
    exit 0
  fi
  command -v python3 >/dev/null 2>&1 || { echo "⟦python3 is needed for the 9router sync.‖Sinkron 9router butuh python3.⟧" >&2; exit 1; }
  R_DB="${LB_9R_DB:-${DATA_DIR:-$HOME/.9router}/db/data.sqlite}"
  if [ ! -f "$R_DB" ]; then
    echo "⟦9router's database was not found at‖Database 9router tidak ditemukan di⟧ $R_DB" >&2
    echo "⟦Run this as the user that runs 9router, or give its path:‖Jalankan sebagai user yang menjalankan 9router, atau sebutkan lokasinya:⟧ ... | LB_9R_DB=/path/to/data.sqlite bash -s -- <TOKEN> --9router" >&2
    exit 1
  fi
  mkdir -p "$R_DIR"
  chmod 700 "$R_DIR"
  umask 077
  cat > "$R_DIR/9router.env" <<EOF
LB_URL=__LB_URL__
LB_TOKEN=$TOKEN
LB_CF_ID=$CF_ID
LB_CF_SECRET=$CF_SECRET
LB_9R_DB=$R_DB
LB_9R_KEYS=${LB_9R_KEYS:-}
EOF
  cat > "$R_SCRIPT" <<'PY'
#!/usr/bin/env python3
"""__LB_TITLE__: send 9router usage to the leaderboard.

Reads 9router's usage log (usageHistory in its SQLite database) read-only and sends hourly
totals per model: tokens, cost and request count. Prompts and responses are never read or sent.
Settings live in 9router.env beside this file. cron runs it every minute; it only sends when
the totals changed, and at least every 30 minutes.
"""
import datetime as dt
import fcntl
import getpass
import hashlib
import json
import os
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DAYS = 90
RESEND = 1800


def settings():
    c = {}
    with open(os.path.join(HERE, "9router.env"), encoding="utf-8") as fh:
        for line in fh:
            k, sep, v = line.rstrip("\n").partition("=")
            if sep:
                c[k.strip()] = v.strip()
    return c


def to_ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v / 1000.0 if v > 1e11 else float(v)
    s = str(v).strip()
    if s.isdigit():
        n = int(s)
        return n / 1000.0 if n > 1e11 else float(n)
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()


def num(v):
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return f if f > 0 and f == f else 0.0


def collect(cfg):
    path = cfg.get("LB_9R_DB") or os.path.expanduser("~/.9router/db/data.sqlite")
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    cols = {r["name"] for r in con.execute("PRAGMA table_info(usageHistory)")}
    need = {"timestamp", "model", "promptTokens", "completionTokens"}
    if not need <= cols:
        raise SystemExit("9router: usageHistory has an unknown layout (%s); this 9router version is not supported yet."
                         % ", ".join(sorted(cols)) if cols else "9router: no usageHistory table in %s" % path)
    pick = [c for c in ("timestamp", "provider", "model", "promptTokens", "completionTokens", "cost", "status", "tokens", "apiKey") if c in cols]
    since = time.time() - DAYS * 86400
    sample = con.execute("SELECT timestamp FROM usageHistory ORDER BY rowid DESC LIMIT 1").fetchone()
    raw = sample[0] if sample is not None else None
    if isinstance(raw, str) and not raw.isdigit():
        bound = dt.datetime.fromtimestamp(since, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")  # ISO text sorts by time
    elif raw is not None and float(raw) > 1e11:
        bound = since * 1000  # milliseconds
    else:
        bound = since
    keys = {k.strip() for k in (cfg.get("LB_9R_KEYS") or "").split(",") if k.strip()}
    allowed = None
    if keys and "apiKey" in cols:
        allowed = set(keys)
        try:
            for r in con.execute("SELECT * FROM apiKeys"):
                r = dict(r)
                if keys & {str(r.get("name") or ""), str(r.get("id") or ""), str(r.get("key") or "")}:
                    allowed |= {str(r.get("key") or ""), str(r.get("id") or "")}
        except sqlite3.Error:
            pass
    agg, first, last, n = {}, None, None, 0
    for r in con.execute("SELECT %s FROM usageHistory WHERE timestamp >= ?" % ", ".join(pick), (bound,)):
        r = dict(r)
        if allowed is not None and str(r.get("apiKey") or "") not in allowed:
            continue
        ts = to_ts(r.get("timestamp"))
        if ts is None or ts < since:
            continue
        try:
            t = json.loads(r.get("tokens") or "{}")
        except ValueError:
            t = {}
        t = t if isinstance(t, dict) else {}
        prompt, out = num(r.get("promptTokens")), num(r.get("completionTokens"))
        cached = num(t.get("cached_tokens") or t.get("cache_read_input_tokens"))
        created = num(t.get("cache_creation_input_tokens"))
        status = str(r.get("status") or "ok").lower()
        if not (prompt or out) and status not in ("ok", "success", "200"):
            continue
        # OpenAI-style prompt_tokens include the cached part; Anthropic-style input_tokens do not.
        inp = prompt - cached - created if prompt >= cached + created else prompt
        model = str(r.get("model") or r.get("provider") or "unknown")[:80]
        hour = int(ts) - int(ts) % 3600
        a = agg.setdefault((hour, model), {"hour": hour, "model": model, "input": 0, "output": 0,
                                           "cache_read": 0, "cache_creation": 0, "cost": 0.0, "requests": 0})
        a["input"] += int(inp)
        a["output"] += int(out)
        a["cache_read"] += int(cached)
        a["cache_creation"] += int(created)
        a["cost"] = round(a["cost"] + num(r.get("cost")), 6)
        a["requests"] += 1
        first = ts if first is None else min(first, ts)
        last = ts if last is None else max(last, ts)
        n += 1
    con.close()
    now = int(time.time())
    machine = "9router-" + hashlib.sha256(("%s|%s|%s" % (socket.gethostname(), getpass.getuser(), path)).encode()).hexdigest()[:16]
    return {
        "machine": machine,
        "from": (int(first) - int(first) % 3600) if first is not None else now - now % 3600,
        "last_request": int(last) if last is not None else None,
        "rows": sorted(agg.values(), key=lambda a: (a["hour"], a["model"])),
    }, n


def send(cfg, payload):
    req = urllib.request.Request(cfg["LB_URL"].rstrip("/") + "/v1/metrics/9router", data=json.dumps(payload).encode(),
                                 method="POST", headers={"Content-Type": "application/json", "x-lb-token": cfg["LB_TOKEN"],
                                                         "User-Agent": "claude-leaderboard-9router-sync"})
    if cfg.get("LB_CF_ID"):
        req.add_header("CF-Access-Client-Id", cfg["LB_CF_ID"])
        req.add_header("CF-Access-Client-Secret", cfg.get("LB_CF_SECRET", ""))
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def main():
    force = "--force" in sys.argv
    lock = open(os.path.join(HERE, "9router.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    cfg = settings()
    payload, n = collect(cfg)
    state_path = os.path.join(HERE, "9router.state")
    digest = hashlib.sha256(json.dumps(payload["rows"], sort_keys=True).encode()).hexdigest()
    try:
        with open(state_path) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    if not force and state.get("digest") == digest and time.time() - state.get("at", 0) < RESEND:
        return 0
    try:
        res = send(cfg, payload)
    except urllib.error.HTTPError as e:
        print("%s: the leaderboard answered HTTP %d: %s" % (time.strftime("%F %T"), e.code, e.read()[:200].decode("utf-8", "replace")))
        return 1
    except (urllib.error.URLError, OSError) as e:
        print("%s: could not reach the leaderboard: %s" % (time.strftime("%F %T"), e))
        return 1
    with open(state_path, "w") as fh:
        json.dump({"digest": digest, "at": time.time()}, fh)
    if force or sys.stdout.isatty():
        print("OK, %d requests, %s output tokens." % (n, format(res.get("output", 0), ",")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY
  chmod 700 "$R_SCRIPT"
  printf '⟦Sending 9router usage (up to 90 days)... ‖Kirim pemakaian 9router (maks. 90 hari)... ⟧'
  python3 "$R_SCRIPT" --force || { echo "⟦failed, see the message above.‖gagal, lihat pesan di atas.⟧" >&2; exit 1; }
  if command -v crontab >/dev/null 2>&1; then
    line="* * * * * /usr/bin/env python3 \"$R_SCRIPT\" > \"$R_DIR/9router-sync.log\" 2>&1 $R_TAG"
    { crontab -l 2>/dev/null | grep -vF "$R_TAG" || true; echo "$line"; } | crontab -
    echo "⟦Done. 9router usage now reaches‖Selesai. Pemakaian 9router sekarang masuk ke⟧ __LB_URL__ ⟦every minute.‖tiap menit.⟧"
  else
    echo "⟦crontab is not available here. Run this every minute with your own scheduler:‖crontab tidak tersedia. Jalankan ini tiap menit lewat scheduler lain:⟧ python3 $R_SCRIPT" >&2
  fi
  exit 0
fi

mkdir -p "$(dirname "$SETTINGS")"
if [ -f "$SETTINGS" ]; then
  backup="$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
  cp "$SETTINGS" "$backup"
  echo "Backup: $backup"
fi
export LB_MODE="$MODE" LB_HEADERS="${LB_HEADERS:-}" LB_ENDPOINT LB_SETTINGS="$SETTINGS"

if [ "$(uname -s)" = Darwin ]; then
  # JavaScript for Automation ships with every macOS, so no python/node needed.
  osascript -l JavaScript - <<'JXA' >/dev/null
ObjC.import('Foundation');
const ENV = $.NSProcessInfo.processInfo.environment;
const g = k => ObjC.unwrap(ENV.objectForKey(k)) || '';
const path = g('LB_SETTINGS');
let obj = {};
if ($.NSFileManager.defaultManager.fileExistsAtPath(path)) {
  const txt = ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null)) || '';
  if (txt.trim()) obj = JSON.parse(txt);
}
if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) throw new Error('settings.json ⟦is not a JSON object‖bukan object JSON⟧');
const env = (obj.env && typeof obj.env === 'object') ? obj.env : {};
const KEYS = ['OTEL_EXPORTER_OTLP_METRICS_PROTOCOL', 'OTEL_EXPORTER_OTLP_METRICS_ENDPOINT',
              'OTEL_EXPORTER_OTLP_METRICS_HEADERS', 'OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE',
              'OTEL_METRIC_EXPORT_INTERVAL'];
const exporters = String(env.OTEL_METRICS_EXPORTER || '').split(',').map(s => s.trim()).filter(s => s && s !== 'none');
if (g('LB_MODE') === 'install') {
  env.CLAUDE_CODE_ENABLE_TELEMETRY = '1';
  if (!exporters.includes('otlp')) exporters.push('otlp');
  env.OTEL_METRICS_EXPORTER = exporters.join(',');
  env.OTEL_EXPORTER_OTLP_METRICS_PROTOCOL = 'http/json';
  env.OTEL_EXPORTER_OTLP_METRICS_ENDPOINT = g('LB_ENDPOINT');
  env.OTEL_EXPORTER_OTLP_METRICS_HEADERS = g('LB_HEADERS');
  env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE = 'delta';
  env.OTEL_METRIC_EXPORT_INTERVAL = '5000';
} else {
  KEYS.forEach(k => delete env[k]);
  if (!env.OTEL_EXPORTER_OTLP_ENDPOINT) {
    const rest = exporters.filter(e => e !== 'otlp');
    if (rest.length) env.OTEL_METRICS_EXPORTER = rest.join(','); else delete env.OTEL_METRICS_EXPORTER;
  }
  if (!Object.keys(env).some(k => k.startsWith('OTEL_'))) delete env.CLAUDE_CODE_ENABLE_TELEMETRY;
}
obj.env = env;
if (!Object.keys(env).length) delete obj.env;
const out = $(JSON.stringify(obj, null, 2) + '\n');
if (!out.writeToFileAtomicallyEncodingError(path, true, $.NSUTF8StringEncoding, null)) throw new Error('⟦failed to write‖gagal menulis⟧ ' + path);
JXA
elif command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY'
import json, os
path = os.environ["LB_SETTINGS"]
obj = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as fh:
        txt = fh.read()
    if txt.strip():
        obj = json.loads(txt)
if not isinstance(obj, dict):
    raise SystemExit("settings.json ⟦is not a JSON object‖bukan object JSON⟧")
env = obj.get("env") if isinstance(obj.get("env"), dict) else {}
keys = ["OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS", "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
        "OTEL_METRIC_EXPORT_INTERVAL"]
exporters = [s.strip() for s in str(env.get("OTEL_METRICS_EXPORTER", "")).split(",") if s.strip() and s.strip() != "none"]
if os.environ["LB_MODE"] == "install":
    env["CLAUDE_CODE_ENABLE_TELEMETRY"] = "1"
    if "otlp" not in exporters:
        exporters.append("otlp")
    env["OTEL_METRICS_EXPORTER"] = ",".join(exporters)
    env["OTEL_EXPORTER_OTLP_METRICS_PROTOCOL"] = "http/json"
    env["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"] = os.environ["LB_ENDPOINT"]
    env["OTEL_EXPORTER_OTLP_METRICS_HEADERS"] = os.environ["LB_HEADERS"]
    env["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"] = "delta"
    env["OTEL_METRIC_EXPORT_INTERVAL"] = "5000"
else:
    for k in keys:
        env.pop(k, None)
    if not env.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        rest = [e for e in exporters if e != "otlp"]
        if rest:
            env["OTEL_METRICS_EXPORTER"] = ",".join(rest)
        else:
            env.pop("OTEL_METRICS_EXPORTER", None)
    if not any(k.startswith("OTEL_") for k in env):
        env.pop("CLAUDE_CODE_ENABLE_TELEMETRY", None)
obj["env"] = env
if not env:
    obj.pop("env")
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(obj, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
os.replace(tmp, path)
PY
else
  echo "⟦python3 is needed to edit‖Butuh python3 untuk mengubah⟧ $SETTINGS. ⟦Add these manually to the \"env\" block:‖Tambahkan manual ke blok \"env\":⟧" >&2
  cat >&2 <<EOF
  "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
  "OTEL_METRICS_EXPORTER": "otlp",
  "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": "http/json",
  "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "$LB_ENDPOINT",
  "OTEL_EXPORTER_OTLP_METRICS_HEADERS": "$LB_HEADERS",
  "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "delta"
EOF
  exit 1
fi

if [ "$MODE" = uninstall ]; then
  echo "⟦Leaderboard telemetry removed from‖Telemetry leaderboard dihapus dari⟧ $SETTINGS."
  exit 0
fi

# ---------------------------------------------------------------------------
# Backfill: history from before telemetry was enabled, read from Claude Code's local
# transcripts (~/.claude/projects/**/*.jsonl). Only hourly token totals per model and
# hourly session counts are sent. Conversation content never leaves the laptop.
# Safe to repeat: earlier results for this laptop are replaced, not added to.
# ---------------------------------------------------------------------------
backfill() {
  local win start cutoff machine out summary resp
  printf '⟦Importing usage history from local transcripts... ‖Ambil riwayat pemakaian dari transkrip lokal... ⟧'
  win=$(curl -fsS ${cf_args[@]+"${cf_args[@]}"} -H "x-lb-token: $TOKEN" "$LB_ENDPOINT/backfill" 2>/dev/null) \
    || { echo "⟦skipped (could not reach the server).‖dilewati (server tidak bisa dihubungi).⟧"; return 0; }
  start=$(printf '%s' "$win" | sed -n 's/.*"start":\([0-9]*\).*/\1/p')
  cutoff=$(printf '%s' "$win" | sed -n 's/.*"cutoff":\([0-9]*\).*/\1/p')
  [ -n "$start" ] && [ -n "$cutoff" ] || { echo "⟦skipped.‖dilewati.⟧"; return 0; }
  if command -v shasum >/dev/null 2>&1; then
    machine=$(printf '%s|%s' "$(hostname)" "$(id -un)" | shasum -a 256 | cut -c1-16)
  else
    machine=$(printf '%s|%s' "$(hostname)" "$(id -un)" | sha256sum | cut -c1-16)
  fi
  out=$(mktemp "${TMPDIR:-/tmp}/lb-backfill.XXXXXX")
  export LB_START="$start" LB_CUTOFF="$cutoff" LB_MACHINE="$machine" LB_OUT="$out"
  export LB_ROOTS="$(dirname "$SETTINGS")/projects:$HOME/.config/claude/projects"

  if [ "$(uname -s)" = Darwin ]; then
    osascript -l JavaScript - > "$out.sum" 2>/dev/null <<'JXA' || true
ObjC.import('Foundation');
const ENV = $.NSProcessInfo.processInfo.environment;
const g = k => ObjC.unwrap(ENV.objectForKey(k)) || '';
const start = Number(g('LB_START')), cutoff = Number(g('LB_CUTOFF'));
const fm = $.NSFileManager.defaultManager;
const seen = {}, agg = {}, sess = {};
let files = 0, msgs = 0, output = 0;
const doneRoots = {};
g('LB_ROOTS').split(':').filter(Boolean).forEach(root => {
  const real = ObjC.unwrap($(root).stringByResolvingSymlinksInPath);
  if (doneRoots[real]) return;
  doneRoots[real] = true;
  if (!fm.fileExistsAtPath(root)) return;
  const en = fm.enumeratorAtPath(root);
  if (!en || en.isNil()) return;
  for (;;) {
    const rel = en.nextObject;
    if (!rel || rel.isNil()) break;
    const r = ObjC.unwrap(rel);
    if (!r.endsWith('.jsonl')) continue;
    const path = root + '/' + r;
    const attrs = fm.attributesOfItemAtPathError(path, null);
    if (attrs && !attrs.isNil()) {
      const md = attrs.objectForKey('NSFileModificationDate');
      if (md && !md.isNil() && md.timeIntervalSince1970 < start) continue;
    }
    const s = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!s || s.isNil()) continue;
    files++;
    const lines = ObjC.unwrap(s).split('\n');
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.indexOf('"usage"') < 0) continue;
      let o;
      try { o = JSON.parse(line); } catch (e) { continue; }
      if (!o || o.type !== 'assistant' || !o.message || !o.message.usage) continue;
      const m = o.message, u = m.usage, model = m.model;
      if (!model || model === '<synthetic>') continue;
      const ts = Date.parse(o.timestamp) / 1000;
      if (!(ts >= start && ts < cutoff)) continue;
      if (m.id) {
        const id = m.id + ':' + (o.requestId || '');
        if (seen[id]) continue;
        seen[id] = 1;
      }
      const hour = Math.floor(ts / 3600) * 3600, key = hour + '|' + model;
      const a = agg[key] || (agg[key] = { hour: hour, model: model, input: 0, output: 0, cache_read: 0, cache_creation: 0 });
      a.input += u.input_tokens || 0;
      a.output += u.output_tokens || 0;
      a.cache_read += u.cache_read_input_tokens || 0;
      a.cache_creation += u.cache_creation_input_tokens || 0;
      output += u.output_tokens || 0;
      msgs++;
      if (o.sessionId && !o.isSidechain) {
        const f = sess[o.sessionId];
        if (f === undefined || ts < f) sess[o.sessionId] = ts;
      }
    }
  }
});
const perHour = {};
Object.keys(sess).forEach(k => { const h = Math.floor(sess[k] / 3600) * 3600; perHour[h] = (perHour[h] || 0) + 1; });
const payload = {
  machine: g('LB_MACHINE'), messages: msgs, files: files,
  rows: Object.keys(agg).map(k => agg[k]),
  sessions: Object.keys(perHour).map(h => ({ hour: Number(h), count: perHour[h] })),
};
$(JSON.stringify(payload)).writeToFileAtomicallyEncodingError(g('LB_OUT'), true, $.NSUTF8StringEncoding, null);
msgs + ' ' + files + ' ' + output;
JXA
  elif command -v python3 >/dev/null 2>&1; then
    python3 - > "$out.sum" 2>/dev/null <<'PY' || true
import json, os
from datetime import datetime
start, cutoff = int(os.environ["LB_START"]), int(os.environ["LB_CUTOFF"])

def parse_ts(t):
    try:
        t = t.replace("Z", "+00:00")
        if "." in t:  # Python < 3.11 wants exactly 3 or 6 fractional digits
            head, rest = t.split(".", 1)
            n = 0
            while n < len(rest) and rest[n].isdigit():
                n += 1
            frac, tz = rest[:n], rest[n:]
            t = "%s.%s%s" % (head, (frac + "000000")[:6], tz)
        return datetime.fromisoformat(t).timestamp()
    except Exception:
        return None

seen, agg, sess, done = set(), {}, {}, set()
files = msgs = output = 0
for root in os.environ["LB_ROOTS"].split(":"):
    real = os.path.realpath(root)
    if not root or real in done or not os.path.isdir(root):
        continue
    done.add(real)
    for dirpath, _, names in os.walk(root):
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getmtime(path) < start:
                    continue
                fh = open(path, encoding="utf-8", errors="replace")
            except OSError:
                continue
            files += 1
            with fh:
                for line in fh:
                    if '"usage"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    m = o.get("message") if isinstance(o, dict) and o.get("type") == "assistant" else None
                    u = m.get("usage") if isinstance(m, dict) else None
                    model = m.get("model") if isinstance(m, dict) else None
                    if not isinstance(u, dict) or not model or model == "<synthetic>":
                        continue
                    ts = parse_ts(str(o.get("timestamp") or ""))
                    if ts is None or not (start <= ts < cutoff):
                        continue
                    if m.get("id"):
                        key = (m["id"], o.get("requestId") or "")
                        if key in seen:
                            continue
                        seen.add(key)
                    hour = int(ts) - int(ts) % 3600
                    a = agg.setdefault((hour, model), {"hour": hour, "model": model, "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0})
                    a["input"] += int(u.get("input_tokens") or 0)
                    a["output"] += int(u.get("output_tokens") or 0)
                    a["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
                    a["cache_creation"] += int(u.get("cache_creation_input_tokens") or 0)
                    output += int(u.get("output_tokens") or 0)
                    msgs += 1
                    sid = o.get("sessionId")
                    if sid and not o.get("isSidechain"):
                        sess[sid] = min(ts, sess.get(sid, ts))
per_hour = {}
for ts in sess.values():
    h = int(ts) - int(ts) % 3600
    per_hour[h] = per_hour.get(h, 0) + 1
payload = {"machine": os.environ["LB_MACHINE"], "messages": msgs, "files": files,
           "rows": list(agg.values()), "sessions": [{"hour": h, "count": c} for h, c in per_hour.items()]}
with open(os.environ["LB_OUT"], "w", encoding="utf-8") as fh:
    json.dump(payload, fh)
print(msgs, files, output)
PY
  fi

  summary=$(cat "$out.sum" 2>/dev/null || true)
  rm -f "$out.sum"
  if [ -z "$summary" ] || [ ! -s "$out" ]; then
    rm -f "$out"; echo "⟦skipped (could not read the transcripts).‖dilewati (tidak bisa membaca transkrip).⟧"; return 0
  fi
  set -- $summary
  if [ "$1" = 0 ]; then
    rm -f "$out"; echo "⟦no history from before telemetry was enabled.‖tidak ada riwayat sebelum telemetry aktif.⟧"; return 0
  fi
  resp=$(curl -fsS -X POST ${cf_args[@]+"${cf_args[@]}"} -H "x-lb-token: $TOKEN" \
    -H 'Content-Type: application/json' --data-binary @"$out" "$LB_ENDPOINT/backfill" 2>/dev/null) || resp=""
  rm -f "$out"
  if [ -n "$resp" ]; then
    echo "OK, $1 ⟦responses from‖respons dari⟧ $2 ⟦files‖file⟧ ($3 ⟦output tokens‖output token⟧) ⟦added to the leaderboard.‖masuk leaderboard.⟧"
  else
    echo "⟦failed to send; try running this install command again later.‖gagal mengirim, coba jalankan perintah install ini lagi nanti.⟧"
  fi
}
backfill || true

echo "⟦Done. Restart Claude Code; new data shows up at‖Selesai. Tutup lalu buka lagi Claude Code; data baru muncul di⟧ __LB_URL__ ⟦within ~1 minute of use.‖dalam ~1 menit setelah dipakai.⟧"
