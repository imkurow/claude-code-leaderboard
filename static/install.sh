#!/usr/bin/env bash
# __LB_TITLE__ - Claude Code telemetry setup (macOS / Linux)
#
#   ⟦Install‖Pasang⟧ : ⟦get the full command (with CF-Access-* headers) from the admin‖perintah lengkap (dengan header CF-Access-*) didapat dari admin⟧
#   ⟦Remove‖Hapus⟧  : bash -s -- --uninstall  (⟦use the same download command‖pakai perintah download yang sama⟧)
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
TOKEN=""
for arg in "$@"; do
  case "$arg" in
    --uninstall) MODE=uninstall ;;
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
