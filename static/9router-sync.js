#!/usr/bin/env node
/*
  Claude Code Leaderboard: send 9router usage to the leaderboard (Windows, macOS and Linux).

  Reads 9router's usage log (usageHistory in <9router data dir>/db/data.sqlite) read-only and
  sends hourly totals per model: tokens, cost and the request count. Prompts and responses are
  never read or sent. Settings live in 9router.json beside this file (written by the installer).
  The installer schedules it every minute (cron, launchd or Task Scheduler); it only sends when
  the totals changed, and at least every 30 minutes.

    node 9router-sync.js            normal run (quiet; the last result goes to 9router-sync.log)
    node 9router-sync.js --force    send now and print the result
    node 9router-sync.js --dry-run  read and summarise, send nothing

  It needs Node.js 18 or newer (9router itself runs on Node) and reads SQLite with node:sqlite
  (Node 22.5+) or the better-sqlite3 copy 9router keeps in its runtime folder.
*/
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

const HERE = __dirname;
const DAYS = 90;
const RESEND = 1800;
const FORCE = process.argv.includes('--force');
const DRY = process.argv.includes('--dry-run');
const LOUD = FORCE || DRY || process.stdout.isTTY;

function report(msg, failed) {
  try { fs.writeFileSync(path.join(HERE, '9router-sync.log'), new Date().toISOString() + ' ' + msg + '\n'); } catch (e) { /* none */ }
  if (LOUD || failed) (failed ? process.stderr : process.stdout).write(msg + '\n');
}

function settings() {
  return JSON.parse(fs.readFileSync(path.join(HERE, '9router.json'), 'utf8'));
}

function defaultDataDir() {
  if (process.env.DATA_DIR) return process.env.DATA_DIR;
  return process.platform === 'win32'
    ? path.join(process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'), '9router')
    : path.join(os.homedir(), '.9router');
}

// SQLite: node:sqlite when this Node has it, else the better-sqlite3 that 9router keeps itself.
function openDb(file, dataDir) {
  let sqlite = null;
  try { sqlite = require('node:sqlite'); } catch (e) { /* older Node */ }
  if (!sqlite) {
    const [maj, min] = process.versions.node.split('.').map(Number);
    if ((maj === 22 && min >= 5) || maj === 23) {
      if (!process.execArgv.includes('--experimental-sqlite')) {
        const r = spawnSync(process.execPath, ['--experimental-sqlite', '--no-warnings', __filename, ...process.argv.slice(2)], { stdio: 'inherit' });
        process.exit(r.status == null ? 1 : r.status);
      }
    }
  }
  if (sqlite && sqlite.DatabaseSync) {
    let db;
    try { db = new sqlite.DatabaseSync(file, { readOnly: true }); } catch (e) { db = new sqlite.DatabaseSync(file); }
    return { all: (sql, ...a) => db.prepare(sql).all(...a), close: () => db.close() };
  }
  const tries = [path.join(dataDir, 'runtime', 'node_modules', 'better-sqlite3'), path.join(dataDir, 'runtime', 'better-sqlite3'), 'better-sqlite3'];
  for (const t of tries) {
    try {
      const B = require(t);
      const db = new B(file, { readonly: true, fileMustExist: true });
      return { all: (sql, ...a) => db.prepare(sql).all(...a), close: () => db.close() };
    } catch (e) { /* next */ }
  }
  throw new Error('no SQLite reader: use Node.js 22.5 or newer, or keep 9router\'s runtime folder (' + path.join(dataDir, 'runtime') + ')');
}

function toTs(v) {
  if (v == null) return null;
  if (typeof v === 'number') return v > 1e11 ? v / 1000 : v;
  const s = String(v).trim();
  if (/^\d+$/.test(s)) { const n = Number(s); return n > 1e11 ? n / 1000 : n; }
  const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');
  return Number.isNaN(t) ? null : t / 1000;
}
const num = v => { const f = Number(v || 0); return f > 0 && Number.isFinite(f) ? f : 0; };

function collect(cfg) {
  const dataDir = cfg.dataDir || defaultDataDir();
  const file = cfg.db || path.join(dataDir, 'db', 'data.sqlite');
  if (!fs.existsSync(file)) throw new Error('9router database not found at ' + file);
  const db = openDb(file, dataDir);
  try {
    const cols = new Set(db.all('PRAGMA table_info(usageHistory)').map(r => r.name));
    for (const c of ['timestamp', 'model', 'promptTokens', 'completionTokens']) {
      if (!cols.has(c)) {
        throw new Error(cols.size ? 'usageHistory has an unknown layout (' + [...cols].sort().join(', ') + '); this 9router version is not supported yet'
          : 'no usageHistory table in ' + file);
      }
    }
    const pick = ['timestamp', 'provider', 'model', 'promptTokens', 'completionTokens', 'cost', 'status', 'tokens', 'apiKey'].filter(c => cols.has(c));
    const since = Date.now() / 1000 - DAYS * 86400;
    const sample = db.all('SELECT timestamp FROM usageHistory ORDER BY rowid DESC LIMIT 1')[0];
    const raw = sample ? sample.timestamp : null;
    let bound = since;
    if (typeof raw === 'string' && !/^\d+$/.test(raw)) bound = new Date(since * 1000).toISOString().slice(0, 19);   // ISO text sorts by time
    else if (raw != null && Number(raw) > 1e11) bound = since * 1000;                                                // milliseconds
    const keys = new Set(String(cfg.keys || '').split(',').map(s => s.trim()).filter(Boolean));
    let allowed = null;
    if (keys.size && cols.has('apiKey')) {
      allowed = new Set(keys);
      try {
        for (const r of db.all('SELECT * FROM apiKeys')) {
          if ([r.name, r.id, r.key].some(v => keys.has(String(v || '')))) { allowed.add(String(r.key || '')); allowed.add(String(r.id || '')); }
        }
      } catch (e) { /* no apiKeys table */ }
    }
    const agg = new Map();
    let first = null, last = null, n = 0;
    for (const r of db.all('SELECT ' + pick.join(', ') + ' FROM usageHistory WHERE timestamp >= ?', bound)) {
      if (allowed && !allowed.has(String(r.apiKey || ''))) continue;
      const ts = toTs(r.timestamp);
      if (ts == null || ts < since) continue;
      let t = {};
      try { t = JSON.parse(r.tokens || '{}') || {}; } catch (e) { t = {}; }
      const prompt = num(r.promptTokens), out = num(r.completionTokens);
      const cached = num(t.cached_tokens || t.cache_read_input_tokens), created = num(t.cache_creation_input_tokens);
      const status = String(r.status || 'ok').toLowerCase();
      if (!(prompt || out) && !['ok', 'success', '200'].includes(status)) continue;
      // OpenAI-style prompt_tokens include the cached part; Anthropic-style input_tokens do not
      const inp = prompt >= cached + created ? prompt - cached - created : prompt;
      const model = String(r.model || r.provider || 'unknown').slice(0, 80);
      const hour = Math.floor(ts) - (Math.floor(ts) % 3600);
      const k = hour + '|' + model;
      let a = agg.get(k);
      if (!a) agg.set(k, a = { hour, model, input: 0, output: 0, cache_read: 0, cache_creation: 0, cost: 0, requests: 0 });
      a.input += Math.round(inp); a.output += Math.round(out);
      a.cache_read += Math.round(cached); a.cache_creation += Math.round(created);
      a.cost = Math.round((a.cost + num(r.cost)) * 1e6) / 1e6;
      a.requests += 1;
      first = first == null ? ts : Math.min(first, ts);
      last = last == null ? ts : Math.max(last, ts);
      n++;
    }
    const now = Math.floor(Date.now() / 1000);
    let user = '';
    try { user = os.userInfo().username; } catch (e) { user = process.env.USER || process.env.USERNAME || ''; }
    const machine = '9router-' + crypto.createHash('sha256').update(os.hostname() + '|' + user + '|' + file).digest('hex').slice(0, 16);
    const rows = [...agg.values()].sort((a, b) => a.hour - b.hour || (a.model < b.model ? -1 : 1));
    return {
      payload: {
        machine,
        from: first != null ? Math.floor(first) - (Math.floor(first) % 3600) : now - (now % 3600),
        last_request: last != null ? Math.floor(last) : null,
        rows,
      },
      count: n,
    };
  } finally {
    db.close();
  }
}

async function send(cfg, payload) {
  const headers = { 'Content-Type': 'application/json', 'x-lb-token': cfg.token, 'User-Agent': 'claude-leaderboard-9router-sync' };
  if (cfg.cfId) { headers['CF-Access-Client-Id'] = cfg.cfId; headers['CF-Access-Client-Secret'] = cfg.cfSecret || ''; }
  const res = await fetch(String(cfg.url).replace(/\/+$/, '') + '/v1/metrics/9router', {
    method: 'POST', headers, body: JSON.stringify(payload), redirect: 'manual', signal: AbortSignal.timeout(30000),
  });
  const text = await res.text();
  if (!res.ok) throw new Error('the leaderboard answered HTTP ' + res.status + ': ' + text.slice(0, 200));
  return JSON.parse(text || '{}');
}

function lock() {
  const file = path.join(HERE, '9router.lock');
  for (let i = 0; i < 2; i++) {
    try {
      const fd = fs.openSync(file, 'wx');
      fs.writeSync(fd, String(process.pid));
      fs.closeSync(fd);
      process.on('exit', () => { try { fs.unlinkSync(file); } catch (e) { /* gone */ } });
      return true;
    } catch (e) {
      if (e.code !== 'EEXIST') throw e;
      try { if (Date.now() - fs.statSync(file).mtimeMs > 10 * 60 * 1000) { fs.unlinkSync(file); continue; } } catch (e2) { continue; }
      return false;   // another run is busy
    }
  }
  return false;
}

async function main() {
  if (typeof fetch !== 'function') throw new Error('Node.js 18 or newer is needed (this is ' + process.version + ')');
  const cfg = settings();
  const { payload, count } = collect(cfg);
  const output = payload.rows.reduce((s, r) => s + r.output, 0);
  if (DRY) {
    report('dry run: ' + count + ' requests, ' + output.toLocaleString('en-US') + ' output tokens in ' + payload.rows.length + ' hourly rows (machine ' + payload.machine + '). Nothing sent.');
    return 0;
  }
  if (!lock()) return 0;
  const statePath = path.join(HERE, '9router.state');
  const digest = crypto.createHash('sha256').update(JSON.stringify(payload.rows)).digest('hex');
  let state = {};
  try { state = JSON.parse(fs.readFileSync(statePath, 'utf8')); } catch (e) { state = {}; }
  if (!FORCE && state.digest === digest && Date.now() / 1000 - (state.at || 0) < RESEND) return 0;
  const res = await send(cfg, payload);
  fs.writeFileSync(statePath, JSON.stringify({ digest, at: Date.now() / 1000 }));
  report('OK, ' + count + ' requests, ' + Number(res.output || 0).toLocaleString('en-US') + ' output tokens.');
  return 0;
}

main().then(code => process.exit(code), err => { report('9router sync failed: ' + (err && err.message || err), true); process.exit(1); });
