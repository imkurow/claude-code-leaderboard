#!/usr/bin/env node
/*
  Claude Code Leaderboard: send 9router, JRouter, Hermes or OpenRouter usage to the leaderboard
  (Windows, macOS, Linux).

  --kind=hermes reads Hermes Agent's state.db (session_model_usage) in HERMES_HOME (~/.hermes,
  %LOCALAPPDATA%\hermes on Windows). --kind=openrouter asks OpenRouter's activity API with the
  person's management key, which stays on this machine; OpenRouter reports whole UTC days only,
  so its usage shows up the day after. Everything below about the usage log is 9router / JRouter.

  JRouter is a fork of 9router with the same usage log (usageHistory in <data dir>/db/data.sqlite);
  only the data folder differs (~/.9router or ~/.jrouter, %APPDATA%\9router or %APPDATA%\jrouter).
  This reads that log read-only and sends hourly totals per model: tokens, cost and the request
  count. Prompts and responses are never read or sent. Settings live in <kind>.json beside this
  file (written by the installer). The installer schedules it every minute (cron, launchd or Task
  Scheduler); it only sends when the totals changed, and at least every 30 minutes.

    node router-sync.js --kind=9router            normal run (quiet; the last result goes to <kind>-sync.log)
    node router-sync.js --kind=jrouter --force    send now and print the result
    node router-sync.js --kind=jrouter --dry-run  read and summarise, send nothing

  It needs Node.js 18 or newer (both routers run on Node) and reads SQLite with node:sqlite
  (Node 22.5+) or the better-sqlite3 copy the router keeps in its runtime folder.
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
const KIND = ((process.argv.find(a => a.startsWith('--kind=')) || '--kind=9router').slice(7) || '9router').toLowerCase();
const NAMES = { '9router': '9router', jrouter: 'JRouter', hermes: 'Hermes', openrouter: 'OpenRouter', opencode: 'OpenCode' };
const NAME = NAMES[KIND] || KIND;
const FORCE = process.argv.includes('--force');
const DRY = process.argv.includes('--dry-run');
const LOUD = FORCE || DRY || process.stdout.isTTY;

function report(msg, failed) {
  try { fs.writeFileSync(path.join(HERE, KIND + '-sync.log'), new Date().toISOString() + ' ' + msg + '\n'); } catch (e) { /* none */ }
  if (LOUD || failed) (failed ? process.stderr : process.stdout).write(msg + '\n');
}

function settings() {
  if (!NAMES[KIND]) throw new Error('unknown --kind ' + KIND + ' (' + Object.keys(NAMES).join(', ') + ')');
  return JSON.parse(fs.readFileSync(path.join(HERE, KIND + '.json'), 'utf8'));
}

function defaultDataDir() {
  if (KIND === 'opencode') return path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share'), 'opencode');
  if (KIND === 'hermes') {
    if (process.env.HERMES_HOME) return process.env.HERMES_HOME;
    return process.platform === 'win32'
      ? path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'hermes')
      : path.join(os.homedir(), '.hermes');
  }
  if (process.env.DATA_DIR) return process.env.DATA_DIR;
  return process.platform === 'win32'
    ? path.join(process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'), KIND)
    : path.join(os.homedir(), '.' + KIND);
}

// SQLite: node:sqlite when this Node has it, else the better-sqlite3 that the router keeps itself.
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
  // the router's own runtime copy; JRouter keeps it under 9router's folder on Windows
  const appdata = process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming');
  const tries = [path.join(dataDir, 'runtime', 'node_modules', 'better-sqlite3'), path.join(dataDir, 'runtime', 'better-sqlite3'),
    path.join(appdata, '9router', 'runtime', 'node_modules', 'better-sqlite3'), path.join(os.homedir(), '.9router', 'runtime', 'node_modules', 'better-sqlite3'),
    'better-sqlite3'];
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

// <kind>-<16 hex>: the computer, its user and the file, or an explicit seed (OpenRouter's key)
function machineId(file, seed) {
  let user = '';
  try { user = os.userInfo().username; } catch (e) { user = process.env.USER || process.env.USERNAME || ''; }
  return KIND + '-' + crypto.createHash('sha256').update(seed || (os.hostname() + '|' + user + '|' + file)).digest('hex').slice(0, 16);
}

function bucket(agg, ts, model, inp, out, cacheRead, cacheWrite, cost, requests) {
  const hour = Math.floor(ts) - (Math.floor(ts) % 3600);
  model = String(model || 'unknown').slice(0, 80);
  const k = hour + '|' + model;
  let a = agg.get(k);
  if (!a) agg.set(k, a = { hour, model, input: 0, output: 0, cache_read: 0, cache_creation: 0, cost: 0, requests: 0 });
  a.input += Math.round(inp); a.output += Math.round(out);
  a.cache_read += Math.round(cacheRead); a.cache_creation += Math.round(cacheWrite);
  a.cost = Math.round((a.cost + cost) * 1e6) / 1e6;
  a.requests += Math.round(requests);
}

function finish(agg, first, last, machine, count) {
  const now = Math.floor(Date.now() / 1000);
  const rows = [...agg.values()].sort((a, b) => a.hour - b.hour || (a.model < b.model ? -1 : 1));
  return {
    payload: {
      machine,
      from: first != null ? Math.floor(first) - (Math.floor(first) % 3600) : now - (now % 3600),
      last_request: last != null ? Math.floor(last) : null,
      rows,
    },
    count,
  };
}

// Hermes Agent: per session and model, counters that grow while the session runs. They are
// counted in the hour the session last used that model.
function collectHermes(cfg) {
  const dataDir = cfg.dataDir || defaultDataDir();
  const file = cfg.db || path.join(dataDir, 'state.db');
  if (!fs.existsSync(file)) throw new Error('Hermes state.db not found at ' + file);
  const db = openDb(file, dataDir);
  try {
    const cols = new Set(db.all('PRAGMA table_info(session_model_usage)').map(r => r.name));
    for (const c of ['model', 'input_tokens', 'output_tokens', 'last_seen']) {
      if (!cols.has(c)) throw new Error(cols.size ? 'session_model_usage has an unknown layout; this Hermes version is not supported yet' : 'no session_model_usage table in ' + file);
    }
    const has = c => cols.has(c) ? c : '0';
    const since = Date.now() / 1000 - DAYS * 86400;
    const agg = new Map();
    let first = null, last = null, n = 0;
    const sql = 'SELECT model, ' + has('api_call_count') + ' AS calls, input_tokens, output_tokens, ' + has('cache_read_tokens') + ' AS cr, ' +
      has('cache_write_tokens') + ' AS cw, ' + has('estimated_cost_usd') + ' AS est, ' + has('actual_cost_usd') + ' AS act, last_seen FROM session_model_usage WHERE last_seen >= ?';
    for (const r of db.all(sql, since)) {
      const ts = toTs(r.last_seen);
      if (ts == null || ts < since) continue;
      const cost = num(r.act) || num(r.est);
      bucket(agg, ts, r.model, num(r.input_tokens), num(r.output_tokens), num(r.cr), num(r.cw), cost, num(r.calls) || 1);
      first = first == null ? ts : Math.min(first, ts);
      last = last == null ? ts : Math.max(last, ts);
      n += num(r.calls) || 1;
    }
    return finish(agg, first, last, machineId(file), n);
  } finally {
    db.close();
  }
}

// OpenCode: session_v2 keeps each session's token and cost totals (counters that grow while it
// runs), counted in the hour the session was last updated. Its database is opencode.db in OpenCode's
// data folder (`opencode debug paths db` prints it; OPENCODE_DB overrides it).
function collectOpenCode(cfg) {
  const dataDir = cfg.dataDir || defaultDataDir();
  const file = cfg.db || process.env.OPENCODE_DB || path.join(dataDir, 'opencode.db');
  if (!fs.existsSync(file)) throw new Error('OpenCode database not found at ' + file);
  const db = openDb(file, dataDir);
  try {
    const cols = new Set(db.all('PRAGMA table_info(session_v2)').map(r => r.name));
    for (const c of ['tokens_input', 'tokens_output', 'time_updated']) {
      if (!cols.has(c)) throw new Error(cols.size ? 'session_v2 has an unknown layout; this OpenCode version is not supported yet' : 'no session_v2 table in ' + file + ' (OpenCode before 2026 is not supported)');
    }
    const has = c => cols.has(c) ? c : '0';
    const msgs = new Set(db.all('PRAGMA table_info(session_message)').map(r => r.name)).has('type');
    const since = Date.now() / 1000 - DAYS * 86400;
    const agg = new Map();
    let first = null, last = null, n = 0;
    const sql = 'SELECT s.tokens_input AS i, s.tokens_output AS o, ' + has('tokens_reasoning') + ' AS r, ' + has('tokens_cache_read') + ' AS cr, ' +
      has('tokens_cache_write') + ' AS cw, ' + has('cost') + ' AS cost, ' + (cols.has('model') ? 's.model' : "''") + ' AS model, s.time_updated AS t' +
      (msgs ? ", (SELECT COUNT(*) FROM session_message m WHERE m.session_id = s.id AND m.type = 'assistant') AS calls" : ', 1 AS calls') +
      ' FROM session_v2 s WHERE s.time_updated >= ?';
    for (const r of db.all(sql, since * 1000)) {
      const ts = toTs(r.t);
      if (ts == null || ts < since || !(num(r.i) || num(r.o))) continue;
      let model = 'unknown';
      try { const m = JSON.parse(r.model || '{}'); model = m.id || m.modelID || model; } catch (e) { model = String(r.model || model); }
      bucket(agg, ts, model, num(r.i), num(r.o) + num(r.r), num(r.cr), num(r.cw), num(r.cost), num(r.calls) || 1);
      first = first == null ? ts : Math.min(first, ts);
      last = last == null ? ts : Math.max(last, ts);
      n += num(r.calls) || 1;
    }
    return finish(agg, first, last, machineId(file), n);
  } finally {
    db.close();
  }
}

// OpenRouter: daily totals per model for the last 30 completed UTC days (activity API, management key).
async function collectOpenRouter(cfg) {
  if (!cfg.apiKey) throw new Error('no OpenRouter management key in ' + KIND + '.json');
  const res = await fetch('https://openrouter.ai/api/v1/activity', {
    headers: { Authorization: 'Bearer ' + cfg.apiKey, 'User-Agent': 'claude-leaderboard-openrouter-sync' }, signal: AbortSignal.timeout(30000),
  });
  const text = await res.text();
  if (res.status === 401 || res.status === 403) throw new Error('OpenRouter refused the key (HTTP ' + res.status + '). The activity API needs a management key.');
  if (!res.ok) throw new Error('OpenRouter answered HTTP ' + res.status + ': ' + text.slice(0, 200));
  const data = (JSON.parse(text || '{}').data) || [];
  const agg = new Map();
  let first = null, last = null, n = 0;
  for (const r of data) {
    const ts = Date.parse(String(r.date || '').slice(0, 10) + 'T00:00:00Z') / 1000;
    if (!ts) continue;
    const prompt = num(r.prompt_tokens), cached = num(r.cached_tokens);
    bucket(agg, ts, r.model || r.model_permaslug, Math.max(0, prompt - cached), num(r.completion_tokens), Math.min(cached, prompt || cached), 0,
      num(r.usage), num(r.requests));
    first = first == null ? ts : Math.min(first, ts);
    last = last == null ? ts + 86399 : Math.max(last, ts + 86399);
    n += num(r.requests);
  }
  // the same account counts once, whichever computer runs this
  return finish(agg, first, last, machineId(null, crypto.createHash('sha256').update(cfg.apiKey).digest('hex')), n);
}

function collect(cfg) {
  if (KIND === 'hermes') return collectHermes(cfg);
  if (KIND === 'opencode') return collectOpenCode(cfg);
  if (KIND === 'openrouter') return collectOpenRouter(cfg);
  const dataDir = cfg.dataDir || defaultDataDir();
  const file = cfg.db || path.join(dataDir, 'db', 'data.sqlite');
  if (!fs.existsSync(file)) throw new Error(NAME + ' database not found at ' + file);
  const db = openDb(file, dataDir);
  try {
    const cols = new Set(db.all('PRAGMA table_info(usageHistory)').map(r => r.name));
    for (const c of ['timestamp', 'model', 'promptTokens', 'completionTokens']) {
      if (!cols.has(c)) {
        throw new Error(cols.size ? 'usageHistory has an unknown layout (' + [...cols].sort().join(', ') + '); this ' + NAME + ' version is not supported yet'
          : 'no usageHistory table in ' + file);
      }
    }
    const pick = ['timestamp', 'provider', 'model', 'promptTokens', 'completionTokens', 'cost', 'status', 'tokens', 'apiKey'].filter(c => cols.has(c));
    const since = Date.now() / 1000 - DAYS * 86400;
    const sample = db.all('SELECT timestamp FROM usageHistory ORDER BY rowid DESC LIMIT 1')[0];
    const raw = sample ? sample.timestamp : null;
    if (DRY) {
      // where the numbers come from, so an empty result can be told apart from a wrong folder
      const total = (db.all('SELECT COUNT(*) AS n FROM usageHistory')[0] || {}).n || 0;
      report('reading ' + file + ': ' + total + ' requests in the log, newest ' + (raw == null ? 'none' : String(raw)) +
        (cfg.keys ? ', only keys ' + cfg.keys : ''));
    }
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
    const machine = KIND + '-' + crypto.createHash('sha256').update(os.hostname() + '|' + user + '|' + file).digest('hex').slice(0, 16);
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
  const headers = { 'Content-Type': 'application/json', 'x-lb-token': cfg.token, 'User-Agent': 'claude-leaderboard-' + KIND + '-sync' };
  if (cfg.cfId) { headers['CF-Access-Client-Id'] = cfg.cfId; headers['CF-Access-Client-Secret'] = cfg.cfSecret || ''; }
  const res = await fetch(String(cfg.url).replace(/\/+$/, '') + '/v1/metrics/' + KIND, {
    method: 'POST', headers, body: JSON.stringify(payload), redirect: 'manual', signal: AbortSignal.timeout(30000),
  });
  const text = await res.text();
  if (!res.ok) throw new Error('the leaderboard answered HTTP ' + res.status + ': ' + text.slice(0, 200));
  return JSON.parse(text || '{}');
}

function lock() {
  const file = path.join(HERE, KIND + '.lock');
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
  const { payload, count } = await collect(cfg);
  const output = payload.rows.reduce((s, r) => s + r.output, 0);
  if (DRY) {
    report('dry run: ' + count + ' requests, ' + output.toLocaleString('en-US') + ' output tokens in ' + payload.rows.length + ' hourly rows (machine ' + payload.machine + '). Nothing sent.');
    return 0;
  }
  if (!lock()) return 0;
  const statePath = path.join(HERE, KIND + '.state');
  const digest = crypto.createHash('sha256').update(JSON.stringify(payload.rows)).digest('hex');
  let state = {};
  try { state = JSON.parse(fs.readFileSync(statePath, 'utf8')); } catch (e) { state = {}; }
  if (!FORCE && state.digest === digest && Date.now() / 1000 - (state.at || 0) < RESEND) return 0;
  const res = await send(cfg, payload);
  fs.writeFileSync(statePath, JSON.stringify({ digest, at: Date.now() / 1000 }));
  report('OK, ' + count + ' requests, ' + Number(res.output || 0).toLocaleString('en-US') + ' output tokens.');
  return 0;
}

main().then(code => process.exit(code), err => { report(NAME + ' sync failed: ' + (err && err.message || err), true); process.exit(1); });
